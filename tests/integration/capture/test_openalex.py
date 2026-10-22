"""Integration tests for ``src/prismabib/capture/openalex.py`` (ADR 0031).

Real filesystem (``tmp_path``), no network: the run is driven through a fake
``httpx.MockTransport`` that counts requests, which is the technique every quota
assertion here rests on (a re-request is invisible in the output, so the counter
is the assertion; BUILD_PLAN §5 risk 2). Nothing is ever written outside
``<project>/raw/openalex/<run_id>/``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
import pytest
from pydantic import SecretStr

from prismabib.capture.layout import SealedRunError, guard_writable, is_sealed
from prismabib.capture.manifest import OpenAlexRunManifest, OpenAlexTitle
from prismabib.capture.openalex import (
    PROGRESS_FILENAME,
    ApiKeyInBodyError,
    _seal,
    capture_openalex,
)
from prismabib.config import Settings
from prismabib.errors import AuthError, EntitlementError, ValidationError
from prismabib.project import Project
from prismabib.sources.ratelimit import RateLimiter
from prismabib.store.load import _sealed_run_dirs

#: An ASCII sentinel (valid in an HTTP header) whose percent-encoded and JSON-escaped
#: forms both differ from the raw one, so each stored form is checked separately.
_KEY = "sentinel+key/a=b\\c"
_KEY_FORMS = (_KEY, quote_plus(_KEY, safe=""), json.dumps(_KEY)[1:-1])
assert len(set(_KEY_FORMS)) == 3

Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Keep ``Settings`` from reading a real ``.env`` or environment key."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    yield


class Fake:
    """A request-counting fake OpenAlex: a handler plus the requests it saw."""

    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def _work(doi: str, *, title: str = "A Title", year: int = 2020, abstract: bool = True) -> dict:
    return {
        "id": f"https://openalex.org/W{abs(hash(doi)) % 10**6}",
        "doi": f"https://doi.org/{doi}",
        "title": title,
        "publication_year": year,
        "abstract_inverted_index": {"hello": [0], "world": [1]} if abstract else None,
        "authorships": [{"author": {"display_name": "Ada"}, "institutions": []}],
        "topics": [{"display_name": "Topic", "score": 0.9}],
    }


def _json(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(payload, separators=(",", ":")).encode())


def _serve(works: dict[str, dict]) -> Handler:
    """A handler answering batches and single lookups from ``works`` by DOI."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/works/doi:"):
            doi = request.url.path.removeprefix("/works/doi:")
            return _json(works[doi]) if doi in works else httpx.Response(404, content=b"{}")
        flt = request.url.params["filter"]
        if flt.startswith("doi:"):
            wanted = flt.removeprefix("doi:").split("|")
            return _json({"results": [works[d] for d in wanted if d in works]})
        return _json({"results": list(works.values())})

    return handler


def _project(tmp_path: Path) -> Project:
    return Project.init("demo", title="Demo", root=tmp_path / "projects")


def _settings(key: str | None = None) -> Settings:
    return Settings(scopus_api_key="x", openalex_api_key=SecretStr(key) if key else None)


def _run(
    project: Project, fake: Fake, *, key: str | None = None, **kwargs: Any
) -> OpenAlexRunManifest | None:
    return capture_openalex(
        project,
        settings=_settings(key),
        transport=fake.transport,
        rate_limiter=RateLimiter(rate=1_000_000.0),
        **kwargs,
    )


def _runs(project: Project) -> list[Path]:
    root = project.raw_dir / "openalex"
    return sorted(p for p in root.iterdir()) if root.is_dir() else []


def _tree_bytes(directory: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(directory)): p.read_bytes() for p in directory.rglob("*") if p.is_file()
    }


# --- the run layout and encoding --------------------------------------------------------------


@pytest.mark.integration
def test_capture_openalex__one_batch__writes_one_jsonl_per_response_and_seals(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    fake = Fake(_serve({"10.1/a": _work("10.1/a")}))
    manifest = _run(project, fake, dois=["10.1/a"])
    assert manifest is not None
    [run_dir] = _runs(project)
    assert run_dir.name == manifest.run_id
    assert sorted(p.name for p in run_dir.iterdir()) == ["manifest.json", "response-0000.jsonl"]
    assert is_sealed(run_dir)
    # The sidecar is bookkeeping and is gone once sealed.
    assert not (run_dir / PROGRESS_FILENAME).exists()
    stored = (run_dir / "response-0000.jsonl").read_bytes()
    assert stored == fake.handler(fake.requests[0]).content + b"\n"
    assert manifest.payload_sha256 == hashlib.sha256(stored).hexdigest()
    assert manifest.requests[0].sha256 == hashlib.sha256(stored).hexdigest()
    assert OpenAlexRunManifest.model_validate_json((run_dir / "manifest.json").read_text())


@pytest.mark.integration
def test_capture_openalex__body_with_line_break__is_stored_compact_with_raw_digest(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    pretty = json.dumps({"results": [_work("10.1/a")]}, indent=2).encode()

    fake = Fake(lambda _r: httpx.Response(200, content=pretty))
    manifest = _run(project, fake, dois=["10.1/a"])
    assert manifest is not None
    [request] = manifest.requests
    stored = (_runs(project)[0] / request.file).read_bytes()
    assert request.encoding == "compact"
    assert request.raw_sha256 == hashlib.sha256(pretty).hexdigest()
    assert stored.count(b"\n") == 1 and stored.endswith(b"\n")
    assert json.loads(stored) == json.loads(pretty)


@pytest.mark.integration
def test_capture_openalex__verbatim_body__keeps_exact_bytes_and_no_raw_digest(
    tmp_path: Path,
) -> None:
    body = b'{"results":[ ],  "meta":{"count":0}}'  # odd spacing must survive
    fake = Fake(lambda _r: httpx.Response(200, content=body))
    manifest = _run(_project(tmp_path), fake, dois=["10.1/a"])
    assert manifest is not None
    assert manifest.requests[0].encoding == "verbatim"
    assert manifest.requests[0].raw_sha256 is None


# --- outcomes ----------------------------------------------------------------------------------


@pytest.mark.integration
def test_capture_openalex__batch__records_value_absent_fields_and_not_found(
    tmp_path: Path,
) -> None:
    fake = Fake(_serve({"10.1/a": _work("10.1/a", abstract=False)}))
    manifest = _run(_project(tmp_path), fake, dois=["10.1/A", "10.1/missing"])
    assert manifest is not None
    [request] = manifest.requests
    assert request.lookup == "doi_batch"
    assert request.identifiers == ["10.1/a", "10.1/missing"]
    assert request.result_count == 1
    found, missing = request.outcomes
    assert (found.outcome, found.absent_fields) == ("value", ["abstract", "affiliations"])
    assert found.openalex_id is not None
    assert (missing.outcome, missing.openalex_id, missing.absent_fields) == ("not_found", None, [])


@pytest.mark.integration
def test_capture_openalex__batch_404__falls_back_to_single_lookups_each_stored(
    tmp_path: Path,
) -> None:
    works = {"10.1/a": _work("10.1/a")}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("filter", "").startswith("doi:"):
            return httpx.Response(404, content=b"{}")
        return _serve(works)(request)

    fake = Fake(handler)
    manifest = _run(_project(tmp_path), fake, dois=["10.1/a", "10.1/b"])
    assert manifest is not None
    kinds = [(r.lookup, r.http_status, [o.outcome for o in r.outcomes]) for r in manifest.requests]
    assert kinds == [
        ("doi_batch", 404, []),
        ("doi_single", 200, ["value"]),
        ("doi_single", 404, ["not_found"]),
    ]
    assert manifest.requests[1].params == {"select": manifest.requests[1].api_view}


@pytest.mark.integration
def test_capture_openalex__title_search__exact_match_is_value_near_miss_is_not_found(
    tmp_path: Path,
) -> None:
    works = {
        "10.1/a": _work("10.1/a", title="Deep Learning, Revisited!", year=2021),
        "10.1/b": _work("10.1/b", title="Deep Learning Revisited Again", year=2021),
    }
    fake = Fake(_serve(works))
    manifest = _run(
        _project(tmp_path),
        fake,
        titles=[("deep learning revisited", 2021), ("Deep Learning Revisit", 2021)],
    )
    assert manifest is not None
    outcomes = [r.outcomes[0].outcome for r in manifest.requests]
    assert [r.lookup for r in manifest.requests] == ["title_search"] * 2
    assert outcomes == ["value", "not_found"]
    assert manifest.requests[0].identifiers == [
        OpenAlexTitle(title="deep learning revisited", year=2021)
    ]


# --- the opt-in record, refusals ---------------------------------------------------------------


@pytest.mark.integration
def test_capture_openalex__opt_in_and_refresh__are_recorded_in_the_manifest(
    tmp_path: Path,
) -> None:
    fake = Fake(_serve({}))
    manifest = _run(_project(tmp_path), fake, dois=["10.1/a"], fallback_opt_in=True, refresh=True)
    assert manifest is not None
    assert (manifest.fallback_opt_in, manifest.refresh) == (True, True)
    assert manifest.requests[0].entitlement_context.fallback_opt_in is True
    plain = _run(_project(tmp_path / "other"), fake, dois=["10.1/a"])
    assert plain is not None and (plain.fallback_opt_in, plain.refresh) == (False, False)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("status", "error"), [(401, AuthError), (403, EntitlementError)], ids=["401", "403"]
)
def test_refusal_without_opt_in__writes_response_raises_and_never_seals(
    tmp_path: Path, status: int, error: type[Exception]
) -> None:
    project = _project(tmp_path)
    fake = Fake(lambda _r: httpx.Response(status, content=b'{"error":"no"}'))
    with pytest.raises(error):
        _run(project, fake, dois=["10.1/a"])
    [run_dir] = _runs(project)
    assert (run_dir / "response-0000.jsonl").read_bytes() == b'{"error":"no"}\n'
    refusal = json.loads((run_dir / PROGRESS_FILENAME).read_text())["refusal"]
    assert refusal == {"index": 0, "status": status}
    assert not is_sealed(run_dir)


@pytest.mark.integration
def test_resume_of_refused_run__starts_new_run_and_never_seals_it(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with pytest.raises(AuthError):
        _run(project, Fake(lambda _r: httpx.Response(401, content=b"{}")), dois=["10.1/a"])
    [dead] = _runs(project)
    before = _tree_bytes(dead)

    fake = Fake(_serve({"10.1/a": _work("10.1/a")}))
    manifest = _run(project, fake, dois=["10.1/a"])
    assert manifest is not None and manifest.run_id != dead.name
    assert len(fake.requests) == 1
    assert _tree_bytes(dead) == before
    assert not is_sealed(dead)


@pytest.mark.integration
def test_capture_openalex__opt_in_refusal__seals_with_refused_outcome_and_status(
    tmp_path: Path,
) -> None:
    fake = Fake(lambda _r: httpx.Response(403, content=b"{}"))
    manifest = _run(_project(tmp_path), fake, dois=["10.1/a"], fallback_opt_in=True)
    assert manifest is not None
    [request] = manifest.requests
    assert request.entitlement_context.refusal_status == 403
    assert [(o.outcome, o.http_status) for o in request.outcomes] == [("refused", 403)]


@pytest.mark.integration
def test_seal__no_opt_in_but_non_200_response__is_refused(tmp_path: Path) -> None:
    project = _project(tmp_path)
    fake = Fake(lambda _r: httpx.Response(403, content=b"{}"))
    _run(project, fake, dois=["10.1/a"], fallback_opt_in=True)
    [sealed] = _runs(project)
    manifest = OpenAlexRunManifest.model_validate_json((sealed / "manifest.json").read_text())

    # Rebuild the open state of that run with the opt-in turned off, and try to seal it.
    from prismabib.capture.openalex import _Progress

    progress = _Progress(
        dois=["10.1/a"],
        titles=[],
        fallback_opt_in=False,
        refresh=False,
        started_at=manifest.started_at,
        requests=manifest.requests,
    )
    unsealed = sealed.parent / "20990101T000000Z-deadbeef"
    unsealed.mkdir()
    with pytest.raises(ValidationError, match="not 200 or 404"):
        _seal(unsealed, progress, api_key_used=False, criteria_version="1.0.0")
    assert not is_sealed(unsealed)


# --- the API key -------------------------------------------------------------------------------


@pytest.mark.integration
def test_capture_openalex__200_401_404_with_a_key__no_stored_file_contains_the_key(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    works = {"10.1/ok": _work("10.1/ok")}

    def handler(request: httpx.Request) -> httpx.Response:
        flt = request.url.params.get("filter", "")
        if "10.1/denied" in flt or "10.1/denied" in request.url.path:
            return httpx.Response(401, content=b'{"error":"bad key"}')
        if "10.1/gone" in flt or "10.1/gone" in request.url.path:
            return httpx.Response(404, content=b"{}")
        return _serve(works)(request)

    manifest = _run(
        project,
        Fake(handler),
        key=_KEY,
        dois=["10.1/ok", "10.1/gone|x", "10.1/denied|y"],
        fallback_opt_in=True,
    )
    assert manifest is not None and manifest.api_key_used is True
    statuses = {r.http_status for r in manifest.requests}
    assert statuses == {200, 401, 404}
    for path, data in _tree_bytes(project.raw_dir).items():
        for form in _KEY_FORMS:
            assert form.encode() not in data, path
    assert all("api_key" not in r.params for r in manifest.requests)


@pytest.mark.integration
def test_capture_openalex__key_in_header_not_params__reaches_no_stored_file(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    inner = _serve({"10.1/a": _work("10.1/a")})
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return inner(request)

    manifest = _run(project, Fake(handler), key=_KEY, dois=["10.1/a"])
    assert manifest is not None
    # Guard against a vacuous test: the key did travel, in the header only.
    assert _KEY in seen[0].headers.get("authorization", "")
    assert "api_key" not in seen[0].url.params
    assert _KEY not in str(seen[0].url)
    assert all(set(r.params) == {"filter", "per-page", "select"} for r in manifest.requests)
    for data in _tree_bytes(project.raw_dir).values():
        for form in _KEY_FORMS:
            assert form.encode() not in data


@pytest.mark.integration
@pytest.mark.parametrize("status", [200, 401], ids=["ok", "refused"])
@pytest.mark.parametrize("form", _KEY_FORMS, ids=["raw", "percent-encoded", "json-escaped"])
def test_capture_openalex__body_carrying_the_key__is_refused_unwritten_and_unsealed(
    tmp_path: Path, form: str, status: int
) -> None:
    project = _project(tmp_path)
    leaked = form
    # Built by hand so each form appears in the body exactly as parametrised.
    body = b'{"results":[],"echo":"' + leaked.encode() + b'"}'
    fake = Fake(lambda _r: httpx.Response(status, content=body))
    with pytest.raises(ApiKeyInBodyError) as caught:
        _run(project, fake, key=_KEY, dois=["10.1/a"])
    assert "response-0000.jsonl" in str(caught.value)
    assert _KEY not in str(caught.value) and leaked not in str(caught.value)
    [run_dir] = _runs(project)
    assert not (run_dir / "response-0000.jsonl").exists()
    assert not is_sealed(run_dir)
    for data in _tree_bytes(project.raw_dir).values():
        assert _KEY.encode() not in data and leaked.encode() not in data


# --- resumption, reuse, immutability -----------------------------------------------------------


def _many(n: int) -> list[str]:
    return [f"10.1/{i:03d}" for i in range(n)]


@pytest.mark.integration
def test_capture_openalex__interrupted_run__resumes_without_rerequesting_written_responses(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    dois = _many(120)  # three batches of 50, 50, 20
    works = {d: _work(d) for d in dois}
    inner = _serve(works)
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 3:
            raise httpx.ConnectError("connection dropped")
        return inner(request)

    with pytest.raises(httpx.ConnectError):
        _run(project, Fake(flaky), dois=dois)
    [run_dir] = _runs(project)
    assert not is_sealed(run_dir)
    assert sorted(p.name for p in run_dir.glob("response-*")) == [
        "response-0000.jsonl",
        "response-0001.jsonl",
    ]
    first_bytes = _tree_bytes(run_dir)

    fake = Fake(inner)
    manifest = _run(project, fake, dois=dois)
    assert manifest is not None
    assert manifest.run_id == run_dir.name  # same run, not a new one
    assert len(fake.requests) == 1  # only the third batch
    assert [r.index for r in manifest.requests] == [0, 1, 2]
    for name in ("response-0000.jsonl", "response-0001.jsonl"):
        assert (run_dir / name).read_bytes() == first_bytes[name]
    assert is_sealed(run_dir) and len(_runs(project)) == 1


@pytest.mark.integration
def test_capture_openalex__retried_unit_makes_fewer_requests__orphan_files_are_deleted(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    works = {"10.1/a": _work("10.1/a"), "10.1/b": _work("10.1/b")}
    inner = _serve(works)
    calls = {"n": 0}

    def crash_in_singles(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(404, content=b"{}")  # batch 404 -> singles
        if calls["n"] == 3:
            raise httpx.ConnectError("dropped")
        return inner(request)

    with pytest.raises(httpx.ConnectError):
        _run(project, Fake(crash_in_singles), dois=["10.1/a", "10.1/b"])
    [run_dir] = _runs(project)
    assert sorted(p.name for p in run_dir.glob("response-*")) == [
        "response-0000.jsonl",
        "response-0001.jsonl",
    ]

    manifest = _run(project, Fake(inner), dois=["10.1/a", "10.1/b"])  # one batch request now
    assert manifest is not None and manifest.run_id == run_dir.name
    assert manifest.payload_files == ["response-0000.jsonl"]
    assert sorted(p.name for p in run_dir.iterdir()) == ["manifest.json", "response-0000.jsonl"]


@pytest.mark.integration
def test_capture_openalex__unsealed_run_with_a_different_plan__is_not_resumed(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    inner = _serve({d: _work(d) for d in _many(60)})

    def fail_second(request: httpx.Request) -> httpx.Response:
        if "10.1/050" in request.url.params.get("filter", ""):
            raise httpx.ConnectError("dropped")
        return inner(request)

    with pytest.raises(httpx.ConnectError):
        _run(project, Fake(fail_second), dois=_many(60))
    manifest = _run(project, Fake(inner), dois=_many(59))
    assert manifest is not None
    assert len(_runs(project)) == 2


@pytest.mark.integration
def test_capture_openalex__rerun_of_held_plan__creates_no_run_and_requests_nothing(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    fake = Fake(_serve({"10.1/a": _work("10.1/a")}))
    first = _run(project, fake, dois=["10.1/a", "10.1/zzz"], titles=[("A Title", 2020)])
    assert first is not None
    sent = len(fake.requests)
    before = _tree_bytes(project.raw_dir)

    assert _run(project, fake, dois=["10.1/A", "10.1/zzz"], titles=[("a  title!", 2020)]) is None
    assert len(fake.requests) == sent  # not_found is held too
    assert _tree_bytes(project.raw_dir) == before


@pytest.mark.integration
def test_capture_openalex__new_identifier__gets_new_run_and_leaves_sealed_run_untouched(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    fake = Fake(_serve({"10.1/a": _work("10.1/a"), "10.1/b": _work("10.1/b")}))
    first = _run(project, fake, dois=["10.1/a"])
    assert first is not None
    [first_dir] = _runs(project)
    before = _tree_bytes(first_dir)

    second = _run(project, fake, dois=["10.1/a", "10.1/b"])
    assert second is not None and second.run_id != first.run_id
    assert second.requests[0].identifiers == ["10.1/b"]  # the held DOI is not asked again
    assert _tree_bytes(first_dir) == before
    with pytest.raises(SealedRunError):
        guard_writable(first_dir)


@pytest.mark.integration
def test_capture_openalex__refusal_outcome__does_not_hold_the_identifier(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _run(
        project,
        Fake(lambda _r: httpx.Response(401, content=b"{}")),
        dois=["10.1/a"],
        fallback_opt_in=True,
    )
    fake = Fake(_serve({"10.1/a": _work("10.1/a")}))
    manifest = _run(project, fake, dois=["10.1/a"], fallback_opt_in=True)
    assert manifest is not None and len(fake.requests) == 1


@pytest.mark.integration
def test_capture_openalex__refresh__requests_held_identifiers_in_a_new_run(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    fake = Fake(_serve({"10.1/a": _work("10.1/a")}))
    first = _run(project, fake, dois=["10.1/a"])
    again = _run(project, fake, dois=["10.1/a"], refresh=True)
    assert first is not None and again is not None
    assert again.run_id != first.run_id and again.refresh is True
    assert len(fake.requests) == 2
    assert again.requests[0].retrieved_at >= first.requests[0].retrieved_at


@pytest.mark.integration
def test_capture_openalex__writes_only_inside_the_openalex_run_directory(tmp_path: Path) -> None:
    project = _project(tmp_path)
    before = set(project.raw_dir.parent.rglob("*"))
    manifest = _run(project, Fake(_serve({})), key=_KEY, dois=["10.1/a"])
    assert manifest is not None
    created = set(project.raw_dir.parent.rglob("*")) - before
    run_dir = project.raw_dir / "openalex" / manifest.run_id
    assert all(p == run_dir.parent or run_dir in (p, *p.parents) for p in created)


@pytest.mark.integration
def test_capture_openalex__scopus_scan__ignores_an_openalex_run(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _run(project, Fake(_serve({})), dois=["10.1/a"])
    assert _sealed_run_dirs(project.raw_dir) == []
