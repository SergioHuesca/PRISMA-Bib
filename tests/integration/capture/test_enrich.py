"""Integration tests for ``src/prismabib/capture/enrich.py`` (Abstract Retrieval enrichment).

Real filesystem (``tmp_path``), mocked network (``respx`` at the transport
boundary only) -- the standard integration mix (§3.7.2). Sockets are banned
process-wide by ``tests/conftest.py``, so nothing here can reach Scopus even if
a route were missed.

**The technique every quota assertion here rests on is a call counter.** These
tests exist because this module spends a *weekly* API quota one request per
record: for the real corpus that is ~1,800 calls, and a defect that re-requests
what Layer 0 already holds is not visible in any output -- the manifest, the
payload bytes and the record count are all identical whether the run made 120
requests or 240. So the assertion is on ``route.call_count``, an exact integer,
and never on "the run finished". BUILD_PLAN §5 risk 2 ("never re-fetch what
Layer 0 already holds") is a claim about a number that only a counter can
check.

``_write_batch`` is called directly by one test. That is white-box testing of
an internal function, not mocking one (§3.7.3 rule 1 forbids the latter, not
the former) -- the same allowance
``test_capture__raw_dir__is_never_reopened_for_write`` takes on the search side.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import time
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

from prismabib import __version__ as CLIENT_VERSION
from prismabib.capture import enrich
from prismabib.capture.enrich import (
    BATCH_SIZE,
    CONSECUTIVE_NOT_FOUND_LIMIT,
    PROGRESS_FILENAME,
    capture_abstracts,
)
from prismabib.capture.layout import ABSTRACTS_DIRNAME, CACHE_DIRNAME, SealedRunError
from prismabib.capture.manifest import AbstractUnavailable
from prismabib.capture.writer import _find_resumable_run, capture_search
from prismabib.errors import EntitlementError, QuotaExceededError, ValidationError
from prismabib.project import Project
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.scopus import ScopusClient
from prismabib.store.load import _sealed_run_dirs

#: Derived from the client, never restated.
#:
#: This was a literal `.../scopus_id/`, and that is why 22 tests mocked the
#: wrong endpoint for three releases without noticing: the client sent an EID
#: to a path that expects a bare numeric id, real Scopus answered 404 for every
#: record, and every test here passed because the mock answered whatever the
#: test itself had written down. A restated constant cannot catch a change in
#: the thing it restates.
_ABSTRACT_URL_PREFIX = ScopusClient.ABSTRACT_URL_PREFIX

#: Enough records to cross a payload-file boundary twice over, since batching by
#: position in the sorted record list is the property most of these tests are
#: really about, and it is invisible below 101 records.
_CORPUS_SIZE = 120


@pytest.fixture(autouse=True)
def _fast_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[RateLimiter]]:
    """Replace the per-run rate limiter with one that never sleeps.

    ``capture_abstracts`` constructs a real
    :class:`~prismabib.sources.ratelimit.RateLimiter` at the plan's default of 6
    requests per second. That is correct in production and ruinous here: 120
    requests would take ~19 seconds of wall clock against a 2-second budget for
    an integration test, in a suite that runs under ``-n auto``.

    The limiter itself is not what these tests are about -- it has its own unit
    and property suites, driven through its *injected* clock and sleep, which is
    the seam it was built with. What is replaced here is only the rate, and the
    substitution is visible: the fixture yields the limiters it built, and
    ``test_enrich__each_run__builds_its_own_rate_limiter`` reads that list to
    assert the one behaviour of it this module is responsible for.
    """
    built: list[RateLimiter] = []

    def _fast() -> RateLimiter:
        limiter = RateLimiter(rate=1_000_000.0)
        built.append(limiter)
        return limiter

    monkeypatch.setattr(enrich, "RateLimiter", _fast)
    yield built


def _init_project(tmp_path: Path, slug: str = "demo") -> Project:
    return Project.init(slug, title="Demo Project", root=tmp_path)


def _record_id(index: int) -> str:
    """A canonical record id whose sort order matches ``index``.

    Zero-padded on purpose: ``capture_abstracts`` iterates ``sorted(record_id)``
    lexicographically, so ids that sort differently from their index would make
    every "record N" assertion below mean something other than it says.
    """
    return f"scopus:2-s2.0-85{index:09d}"


def _corpus(size: int = _CORPUS_SIZE) -> list[str]:
    return [_record_id(index) for index in range(size)]


def _scopus_id_of(request: httpx.Request) -> str:
    return str(request.url).rsplit("/", 1)[-1].split("?")[0]


def _abstract_body(scopus_id: str, *, with_subject_areas: bool = True) -> dict[str, Any]:
    """A minimal Abstract Retrieval response for ``scopus_id``.

    Deliberately minimal rather than a copy of the cassette: what these tests
    exercise is the *run* -- counting, batching, sealing, resumption -- and the
    payload only has to be a distinguishable, well-formed response. The
    *shape* of a real response is pinned separately, and against fixtures, by
    ``tests/contract/test_scopus_contract.py``.
    """
    body: dict[str, Any] = {
        "abstracts-retrieval-response": {
            "coredata": {
                "eid": scopus_id,
                "dc:identifier": f"SCOPUS_ID:{scopus_id.removeprefix('2-s2.0-')}",
                "dc:title": f"Title of {scopus_id}",
            }
        }
    }
    if with_subject_areas:
        body["abstracts-retrieval-response"]["subject-areas"] = {
            "subject-area": [
                {"@_fa": "true", "@code": "1702", "@abbrev": "COMP", "$": "Artificial Intelligence"}
            ]
        }
    return body


def _ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=_abstract_body(_scopus_id_of(request)))


def _mock_abstracts(side_effect: Any = _ok_handler) -> respx.Route:
    return respx.get(url__startswith=_ABSTRACT_URL_PREFIX).mock(side_effect=side_effect)


def _run_dirs(project: Project) -> list[Path]:
    root = project.raw_dir / ABSTRACTS_DIRNAME
    return sorted((entry for entry in root.iterdir() if entry.is_dir()), key=lambda p: p.name)


def _sole_run_dir(project: Project) -> Path:
    dirs = _run_dirs(project)
    assert len(dirs) == 1
    return dirs[0]


def _payload_bytes(run_dir: Path, payload_files: Sequence[str]) -> bytes:
    return b"".join((run_dir / name).read_bytes() for name in payload_files)


def _line_count(path: Path) -> int:
    return len([line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])


def _clear_http_cache(project: Project) -> None:
    """Delete ``raw/_cache/``, the way a routine cleanup or a fresh clone would.

    Every quota claim about resumption has to survive this. ``raw/_cache/`` is
    gitignored and documented as disposable, so a test that leaves it warm
    cannot tell "the resume started at the right offset" apart from "the
    re-requested records happened to be free this time" -- and only the first
    of those is true on the machine that matters.
    """
    shutil.rmtree(project.raw_dir / CACHE_DIRNAME, ignore_errors=True)


# ---------------------------------------------------------------------------
# The core quota claims: exactly one request per record, and none on a re-run.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_enrich__cold_cache__issues_exactly_one_request_per_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    records = _corpus()

    with respx.mock:
        route = _mock_abstracts()
        manifest = capture_abstracts(project, record_ids=records)

    run_dir = _sole_run_dir(project)

    assert route.call_count == _CORPUS_SIZE
    assert manifest.records_requested == _CORPUS_SIZE
    assert manifest.records_fetched == _CORPUS_SIZE
    assert manifest.payload_files == ["abstracts-0000.jsonl", "abstracts-0001.jsonl"]
    assert manifest.unavailable == []
    assert manifest.client_version == CLIENT_VERSION
    assert manifest.criteria_version == project.criteria.version
    assert (run_dir / "manifest.json").is_file()

    # Batching is by POSITION in the sorted record list, so file 0 holds
    # exactly BATCH_SIZE records and file 1 the remainder -- never a split
    # that depends on how the run went.
    assert _line_count(run_dir / "abstracts-0000.jsonl") == BATCH_SIZE
    assert _line_count(run_dir / "abstracts-0001.jsonl") == _CORPUS_SIZE - BATCH_SIZE


@pytest.mark.integration
def test_enrich__warm_cache_rerun__issues_zero_requests_and_reproduces_the_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second run over the same records must cost nothing and produce the same bytes.

    Two independent claims in one place because they fail together: the byte
    identity is what makes ``payload_sha256`` a citable identifier of a
    capture (S02-AC2's guarantee, restated for this run kind), and it only
    holds if the re-run genuinely replayed the cached bodies rather than
    re-fetching and re-encoding whatever the server happened to return the
    second time.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    records = _corpus()

    with respx.mock:
        _mock_abstracts()
        first = capture_abstracts(project, record_ids=records)

    with respx.mock:
        route = _mock_abstracts()
        second = capture_abstracts(project, record_ids=records)

    assert route.call_count == 0
    assert first.run_id != second.run_id
    assert first.payload_sha256 == second.payload_sha256


# ---------------------------------------------------------------------------
# Budget, interruption, resumption.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_enrich__budget__stops_after_exactly_that_many_requests_and_leaves_the_run_unsealed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)

    with respx.mock:
        route = _mock_abstracts()
        manifest = capture_abstracts(project, record_ids=_corpus(), budget=50)

    run_dir = _sole_run_dir(project)

    assert route.call_count == 50
    assert not (run_dir / "manifest.json").exists()
    assert (run_dir / PROGRESS_FILENAME).is_file()
    assert manifest.records_requested == _CORPUS_SIZE

    # 50 records were fetched but only 100 make a batch, so nothing is durable
    # yet: a short payload file would not be byte-identical to an
    # uninterrupted run's, which is the property resumption has to preserve.
    progress = json.loads((run_dir / PROGRESS_FILENAME).read_text(encoding="utf-8"))
    assert progress["records_done"] == 0
    assert progress["payload_files"] == []
    assert list(run_dir.glob("abstracts-*.jsonl")) == []


@pytest.mark.integration
def test_enrich__interrupted_then_resumed__total_requests_equal_the_record_count(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resuming must not re-spend quota on records the first attempt already fetched.

    The exact-equality assertion is the whole test. A resumed run that quietly
    replayed from record 0 would still finish, still write the right files, and
    still seal -- and would have spent 170 calls of a weekly quota instead of
    120, with nothing in any artefact to show it.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    records = _corpus()

    with respx.mock:
        first_route = _mock_abstracts()
        capture_abstracts(project, record_ids=records, budget=50)

    with respx.mock:
        second_route = _mock_abstracts()
        manifest = capture_abstracts(project, record_ids=records)

    run_dir = _sole_run_dir(project)

    assert first_route.call_count + second_route.call_count == _CORPUS_SIZE
    assert manifest.run_id == run_dir.name
    assert manifest.records_fetched == _CORPUS_SIZE
    assert (run_dir / "manifest.json").is_file()


@pytest.mark.integration
def test_enrich__resumed_run__writes_bytes_identical_to_an_uninterrupted_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Where a run was interrupted must leave no trace in the payload files.

    Two separate projects, one interrupted and one not, compared byte for
    byte. If batching were by "responses collected so far" rather than by
    position in the sorted record list, these files would differ -- and the
    difference would be a ``payload_sha256`` that depends on when someone
    pressed Ctrl-C, i.e. a capture that cannot be cited.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    records = _corpus()

    interrupted_project = _init_project(tmp_path / "a", slug="interrupted")
    with respx.mock:
        _mock_abstracts()
        capture_abstracts(interrupted_project, record_ids=records, budget=37)
    with respx.mock:
        _mock_abstracts()
        resumed = capture_abstracts(interrupted_project, record_ids=records)

    clean_project = _init_project(tmp_path / "b", slug="clean")
    with respx.mock:
        _mock_abstracts()
        clean = capture_abstracts(clean_project, record_ids=records)

    resumed_dir = _sole_run_dir(interrupted_project)
    clean_dir = _sole_run_dir(clean_project)

    assert resumed.payload_files == clean.payload_files
    assert _payload_bytes(resumed_dir, resumed.payload_files) == _payload_bytes(
        clean_dir, clean.payload_files
    )
    assert resumed.payload_sha256 == clean.payload_sha256


@pytest.mark.integration
def test_enrich__mid_run_transport_failure__preserves_completed_batches_and_stays_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dropped connection must destroy nothing that was already durable."""
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    records = _corpus()
    calls = {"n": 0}

    def _fail_after_105(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] > 105:
            raise httpx.ConnectError("simulated mid-run failure")
        return _ok_handler(request)

    with respx.mock:
        _mock_abstracts(_fail_after_105)
        with pytest.raises(httpx.ConnectError):
            capture_abstracts(project, record_ids=records)

    run_dir = _sole_run_dir(project)
    progress = json.loads((run_dir / PROGRESS_FILENAME).read_text(encoding="utf-8"))

    assert not (run_dir / "manifest.json").exists()
    assert progress["records_done"] == BATCH_SIZE
    assert progress["payload_files"] == ["abstracts-0000.jsonl"]
    assert _line_count(run_dir / "abstracts-0000.jsonl") == BATCH_SIZE


# ---------------------------------------------------------------------------
# Quota and entitlement.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_enrich__weekly_quota_exhausted__raises_without_sealing_and_keeps_durable_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    records = _corpus()
    calls = {"n": 0}

    def _quota_after_105(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] > 105:
            return httpx.Response(
                429,
                headers={
                    "X-RateLimit-Remaining": "0",
                    # Far enough out that it cannot be a per-second throttle,
                    # which is how the client tells the weekly quota apart.
                    "X-RateLimit-Reset": str(int(time.time()) + 7 * 24 * 3600),
                },
            )
        return _ok_handler(request)

    with respx.mock:
        _mock_abstracts(_quota_after_105)
        with pytest.raises(QuotaExceededError):
            capture_abstracts(project, record_ids=records)

    run_dir = _sole_run_dir(project)
    progress = json.loads((run_dir / PROGRESS_FILENAME).read_text(encoding="utf-8"))

    assert not (run_dir / "manifest.json").exists()
    assert progress["records_done"] == BATCH_SIZE
    assert progress["payload_files"] == ["abstracts-0000.jsonl"]
    assert progress["records_fetched"] == BATCH_SIZE
    assert _line_count(run_dir / "abstracts-0000.jsonl") == BATCH_SIZE


@pytest.mark.integration
def test_enrich__403_on_the_first_record__raises_after_exactly_one_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The entitlement probe costs one call, not one per record.

    A key entitled for Search ``view=COMPLETE`` is commonly not entitled for
    Abstract Retrieval, and that failure is a flat 403 on every record. Without
    the first-record rule, discovering it would cost the whole corpus in
    requests -- ~1,800 against a weekly quota -- and every one of them would be
    recorded as an individually embargoed record, producing a sealed,
    plausible-looking run in which nothing was enriched.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)

    with respx.mock:
        route = _mock_abstracts(
            lambda request: httpx.Response(
                403,
                json={"service-error": {"status": {"statusCode": "AUTHENTICATION_ERROR"}}},
            )
        )
        with pytest.raises(EntitlementError) as excinfo:
            capture_abstracts(project, record_ids=_corpus())

    assert route.call_count == 1
    assert not (_sole_run_dir(project) / "manifest.json").exists()

    message = str(excinfo.value)
    assert "Abstract Retrieval" in message
    # It must also say what the failure is NOT, or an operator whose search
    # runs work perfectly goes and re-checks the one entitlement that is fine.
    assert "view=COMPLETE" in message
    assert "DIFFERENT entitlement" in message


@pytest.mark.integration
def test_enrich__403_on_a_later_record__is_recorded_as_unavailable_and_the_run_continues(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A per-record embargo is data, not a failure -- and it must reach the manifest."""
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    records = _corpus()
    embargoed = _record_id(7).removeprefix("scopus:")

    def _embargo_one(request: httpx.Request) -> httpx.Response:
        if _scopus_id_of(request) == embargoed:
            return httpx.Response(403, json={"service-error": {"status": {"statusText": "x"}}})
        return _ok_handler(request)

    with respx.mock:
        route = _mock_abstracts(_embargo_one)
        manifest = capture_abstracts(project, record_ids=records)

    run_dir = _sole_run_dir(project)

    assert route.call_count == _CORPUS_SIZE
    assert (run_dir / "manifest.json").is_file()
    assert manifest.records_fetched == _CORPUS_SIZE - 1
    assert manifest.unavailable == [
        AbstractUnavailable(record_id=_record_id(7), http_status=403, reason="not_entitled")
    ]
    # The embargoed record occupied a position in batch 0, and that batch is
    # still a full 100 positions wide -- it simply holds 99 payload lines.
    assert _line_count(run_dir / "abstracts-0000.jsonl") == BATCH_SIZE - 1


@pytest.mark.integration
def test_enrich__record_with_no_subject_areas__is_fetched_and_recorded_as_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Scopus assigns none" must be distinguishable from "we never asked".

    Both look identical in Layer 1 -- zero rows in ``subject_areas`` -- so the
    distinction can only live in the manifest. It matters because a
    ``criteria.yaml`` subject filter treats a record with no codes as
    unevaluable rather than excluded, and whether that is the right call
    depends entirely on which of the two situations produced the gap.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    records = _corpus()
    bare = _record_id(3).removeprefix("scopus:")

    def _one_without_areas(request: httpx.Request) -> httpx.Response:
        scopus_id = _scopus_id_of(request)
        return httpx.Response(
            200, json=_abstract_body(scopus_id, with_subject_areas=scopus_id != bare)
        )

    with respx.mock:
        _mock_abstracts(_one_without_areas)
        manifest = capture_abstracts(project, record_ids=records)

    run_dir = _sole_run_dir(project)
    unavailable = manifest.unavailable

    assert [item.record_id for item in unavailable] == [_record_id(3)]
    assert unavailable[0].reason == "no_subject_areas"
    assert unavailable[0].http_status == 200
    # The payload is still written: the response is real data, and only the
    # subject areas are absent from it.
    assert manifest.records_fetched == _CORPUS_SIZE
    assert _line_count(run_dir / "abstracts-0000.jsonl") == BATCH_SIZE


# ---------------------------------------------------------------------------
# Layer 0 invariants: sealing, the sidecar, the view, and invisibility.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_enrich__sealed_run__refuses_a_second_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)

    with respx.mock:
        _mock_abstracts()
        capture_abstracts(project, record_ids=_corpus(5))

    run_dir = _sole_run_dir(project)

    with pytest.raises(SealedRunError):
        enrich._write_batch(run_dir, "abstracts-0001.jsonl", [{"x": 1}])

    assert not (run_dir / "abstracts-0001.jsonl").exists()


@pytest.mark.integration
def test_enrich__seal__deletes_progress_json_and_excludes_it_from_the_payload_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``progress.json`` is bookkeeping, not payload.

    If it were hashed, ``payload_sha256`` would change with a resumption that
    fetched nothing new, and two captures of identical data would no longer
    have identical identifiers.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)

    with respx.mock:
        _mock_abstracts()
        manifest = capture_abstracts(project, record_ids=_corpus())

    run_dir = _sole_run_dir(project)
    expected = hashlib.sha256(_payload_bytes(run_dir, manifest.payload_files)).hexdigest()

    assert not (run_dir / PROGRESS_FILENAME).exists()
    assert manifest.payload_sha256 == expected
    assert PROGRESS_FILENAME not in manifest.payload_files


@pytest.mark.integration
def test_enrich__every_request__asks_for_view_full_and_never_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``view=META`` is cheaper and *does* carry subject areas -- which is the trap.

    Degrading would look like it worked: the codes would arrive and the store
    would fill. It is refused for the same reason ``STANDARD`` is refused on
    the search side (§5 risk 1) -- a corpus whose subject areas came from two
    different views is not one filter, and nothing downstream could tell.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)

    with respx.mock:
        route = _mock_abstracts()
        manifest = capture_abstracts(project, record_ids=_corpus(10))

    issued = {call.request.url.params.get("view") for call in route.calls}

    assert issued == {"FULL"}
    assert manifest.view == "FULL"


@pytest.mark.integration
def test_enrich__abstract_runs__are_invisible_to_the_search_run_scans(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``raw/abstracts/`` must never be mistaken for a search run.

    The Layer 1 loader would read an ``AbstractRunManifest`` as a
    ``RunManifest``, fail on the missing ``total_results``/``query``, and --
    if it got past that -- try to read Abstract Retrieval responses as search
    entries and die on a missing ``prism:coverDate``. The capture-side
    resumption scan would try to resume it as a search run. Both exclusions are
    by name (``NON_RUN_DIRNAMES``), so this test fails if either scan is ever
    rewritten to infer "is this a run?" from the directory's contents.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    page = {
        "search-results": {
            "opensearch:totalResults": "1",
            "entry": [{"eid": "2-s2.0-85000000000", "prism:coverDate": "2021-01-01"}],
            "cursor": {"@current": "*", "@next": ""},
        }
    }

    with respx.mock:
        respx.get("https://api.elsevier.com/content/search/scopus").mock(
            return_value=httpx.Response(200, json=page)
        )
        search_manifest = capture_search(project, query='TITLE-ABS-KEY("x")')

    with respx.mock:
        _mock_abstracts()
        capture_abstracts(project, record_ids=_corpus(3))

    sealed = _sealed_run_dirs(project.raw_dir)

    assert (project.raw_dir / ABSTRACTS_DIRNAME).is_dir()
    assert [path.name for path in sealed] == [search_manifest.run_id]
    assert (
        _find_resumable_run(
            project.raw_dir,
            query='TITLE-ABS-KEY("other")',
            view="COMPLETE",
            endpoint="https://api.elsevier.com/content/search/scopus",
        )
        is None
    )


@pytest.mark.integration
def test_enrich__each_run__builds_its_own_rate_limiter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _fast_rate_limiter: list[RateLimiter]
) -> None:
    """Scopus quotas are per-API, so an enrichment run starts with a full bucket.

    A limiter shared with (or carried over from) a search run arrives with that
    API's consumed tokens and its ``X-RateLimit-Reset``, and would throttle --
    or park -- this run against a quota it does not spend.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)

    with respx.mock:
        _mock_abstracts()
        capture_abstracts(project, record_ids=_corpus(3))
        capture_abstracts(project, record_ids=_corpus(4))

    assert len(_fast_rate_limiter) == 2
    assert _fast_rate_limiter[0] is not _fast_rate_limiter[1]


# ---------------------------------------------------------------------------
# Record-set resolution and argument validation.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_enrich__no_record_ids__enriches_every_record_in_the_sealed_search_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default is "enrich the corpus I already captured", deduplicated across runs."""
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)

    def _page(eids: list[str]) -> dict[str, Any]:
        return {
            "search-results": {
                "opensearch:totalResults": str(len(eids)),
                "entry": [
                    {"eid": eid, "prism:coverDate": "2021-01-01", "dc:identifier": eid}
                    for eid in eids
                ],
                "cursor": {"@current": "*", "@next": ""},
            }
        }

    with respx.mock:
        respx.get("https://api.elsevier.com/content/search/scopus").mock(
            side_effect=[
                httpx.Response(200, json=_page(["2-s2.0-1", "2-s2.0-2"])),
                httpx.Response(200, json=_page(["2-s2.0-2", "2-s2.0-3"])),
            ]
        )
        first = capture_search(project, query='TITLE-ABS-KEY("a")')
        second = capture_search(project, query='TITLE-ABS-KEY("b")')

    with respx.mock:
        route = _mock_abstracts()
        manifest = capture_abstracts(project)

    requested = sorted(_scopus_id_of(call.request) for call in route.calls)

    # "2-s2.0-2" appears in both runs and is fetched once: it is one paper, and
    # a second call for it would be a second charge against the same quota.
    assert requested == ["2-s2.0-1", "2-s2.0-2", "2-s2.0-3"]
    assert manifest.records_requested == 3
    assert manifest.source_run_ids == sorted([first.run_id, second.run_id])


@pytest.mark.integration
def test_enrich__no_sealed_runs_and_no_record_ids__refuses_rather_than_sealing_an_empty_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty sealed run is indistinguishable from "asked, and Scopus had nothing"."""
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)

    with pytest.raises(ValidationError):
        capture_abstracts(project)

    assert not (project.raw_dir / ABSTRACTS_DIRNAME).exists()


@pytest.mark.integration
@pytest.mark.parametrize("budget", [pytest.param(0, id="zero"), pytest.param(-1, id="negative")])
def test_enrich__non_positive_budget__is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, budget: int
) -> None:
    """A budget of 0 would create a run directory and fetch nothing, forever."""
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)

    with pytest.raises(ValidationError):
        capture_abstracts(project, record_ids=_corpus(3), budget=budget)

    assert not (project.raw_dir / ABSTRACTS_DIRNAME).exists()


@pytest.mark.integration
def test_enrich__404_on_a_record__is_recorded_once_without_consuming_the_retry_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A withdrawn record must cost one call and not end the run.

    Scopus withdraws and merges records, so an identifier captured in an
    earlier search run can stop resolving later. Before ``RecordNotFoundError``
    every unexpected status became a retryable ``UpstreamError``: a 404 burned
    the whole retry budget and then aborted the caller. Over an 1,800-record
    enrichment that is close to certain, and it would cost the operator hours
    of quota-bound progress for a fact about the index that retrying cannot
    change.

    The call count is the load-bearing assertion. Recording the record as
    unavailable while silently retrying it five times first would satisfy every
    other assertion here.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    records = _corpus()
    withdrawn = _record_id(3).removeprefix("scopus:")

    def _withdraw_one(request: httpx.Request) -> httpx.Response:
        if _scopus_id_of(request) == withdrawn:
            return httpx.Response(404, json={"service-error": {"status": {"statusText": "x"}}})
        return _ok_handler(request)

    with respx.mock:
        route = _mock_abstracts(_withdraw_one)
        manifest = capture_abstracts(project, record_ids=records)

    run_dir = _sole_run_dir(project)

    assert route.call_count == _CORPUS_SIZE
    assert (run_dir / "manifest.json").is_file()
    assert manifest.records_fetched == _CORPUS_SIZE - 1
    assert manifest.unavailable == [
        AbstractUnavailable(record_id=_record_id(3), http_status=404, reason="not_found")
    ]


@pytest.mark.integration
def test_enrich__http_200_without_the_abstract_envelope__raises_and_does_not_seal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 200 carrying an error body must not be sealed as a successful fetch.

    Scopus can answer HTTP 200 with a ``service-error`` payload. Before the
    envelope check, ``capture_abstracts`` wrote that body verbatim as a payload
    line, counted the record as fetched, and **sealed** -- so the manifest
    asserted `records_fetched == records_requested` for a record that was never
    retrieved. Worse, the line written carries no ``coredata``, so it cannot be
    keyed back to a record at all, which defeats the reason payloads are stored
    without a ``{"record_id": ...}`` envelope.

    Layer 0 is immutable by design, so that false success would have been
    permanent: BUILD_PLAN §1.4's plausible wrong number, arriving through the
    artefact introduced to prevent it.

    The seal assertion is the load-bearing one -- raising while still leaving a
    sealed manifest behind would satisfy the exception check alone.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)

    with respx.mock:
        _mock_abstracts(
            lambda request: httpx.Response(
                200, json={"service-error": {"status": {"statusCode": "RESOURCE_NOT_FOUND"}}}
            )
        )
        with pytest.raises(ValidationError, match="abstracts-retrieval-response"):
            capture_abstracts(project, record_ids=_corpus()[:1])

    assert not list((project.raw_dir / ABSTRACTS_DIRNAME).rglob("manifest.json"))


@pytest.mark.integration
def test_enrich__resumed_at_a_batch_boundary_with_a_cold_cache__refetches_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`start = state.records_done` is what saves the quota, and only this pins it.

    The sibling resumption tests use budgets *below* ``BATCH_SIZE``, so no batch
    is ever written and ``records_done`` stays 0 -- every resume in the suite
    was from offset zero. Their saving therefore came from ``raw/_cache/``,
    which is gitignored and documented as disposable, not from ``progress.json``.
    They would pass unchanged against a resume that replayed from record 0.

    This one budgets exactly one batch, then deletes the cache before resuming,
    so the only thing that can prevent a refetch is the persisted offset. That
    matters at the scale this feature exists for: on an 1,800-record corpus a
    silent replay costs the operator a weekly quota and shows up in no artefact.

    The two call counts are asserted separately, not just their sum: a resume
    that refetched the first 100 and skipped the last 20 would sum to 120 too.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    records = _corpus()

    with respx.mock:
        first_route = _mock_abstracts()
        capture_abstracts(project, record_ids=records, budget=BATCH_SIZE)

    _clear_http_cache(project)

    with respx.mock:
        second_route = _mock_abstracts()
        manifest = capture_abstracts(project, record_ids=records)

    assert first_route.call_count == BATCH_SIZE
    assert second_route.call_count == _CORPUS_SIZE - BATCH_SIZE
    assert manifest.records_fetched == _CORPUS_SIZE
    assert (_sole_run_dir(project) / "manifest.json").is_file()


@pytest.mark.integration
def test_capture_abstracts__every_request_404s__stops_instead_of_spending_the_quota(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unentitled key is refused with 404 here, not 403 -- measured, not assumed.

    Found on 2026-09-01 by probing a real key: Scopus answered a Search
    ``view=COMPLETE`` query with 1,662 results and then returned **404** for a
    record that same response had just supplied. Elsevier signals "not entitled
    to this endpoint" that way, so the 403 probe never fires.

    Before this breaker the run treated each 404 as a withdrawn record and
    carried on: it would have spent the whole weekly quota, sealed
    successfully, loaded zero subject areas, and left a manifest asserting that
    every record in a live corpus had been withdrawn from Scopus. Recording a
    falsehood about the corpus is worse than failing.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    records = _corpus()

    def always_404(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "not found"})

    with respx.mock:
        route = _mock_abstracts(side_effect=always_404)
        with pytest.raises(EntitlementError) as excinfo:
            capture_abstracts(project, record_ids=records)

    message = str(excinfo.value)
    assert "Abstract Retrieval entitlement" in message
    assert "SCOPUS_INSTTOKEN" in message
    assert "no manifest now claims your" in message
    # Stopped early: the breaker's limit, not the corpus size.
    assert route.call_count == CONSECUTIVE_NOT_FOUND_LIMIT
    assert route.call_count < _CORPUS_SIZE
    # Nothing sealed, so no run asserts the corpus was withdrawn.
    root = project.raw_dir / ABSTRACTS_DIRNAME
    assert not root.exists() or not any(
        (entry / "manifest.json").exists() for entry in root.iterdir() if entry.is_dir()
    )


@pytest.mark.integration
def test_capture_abstracts__a_few_withdrawn_records__still_completes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: genuinely withdrawn records must not trip the breaker.

    Scopus really does withdraw and merge records -- the first attempt of the
    investigation above hit one -- so a handful of 404s is ordinary and the run
    has to finish, recording them as unavailable. Without this test the breaker
    could be tightened to 1 and everything above would still pass, turning a
    normal corpus into an unrunnable one.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    records = _corpus()
    withdrawn = set(records[:3])

    def mostly_ok(request: httpx.Request) -> httpx.Response:
        scopus_id = _scopus_id_of(request)
        if any(scopus_id in record for record in withdrawn):
            return httpx.Response(404, json={"error": "not found"})
        return httpx.Response(200, json=_abstract_body(scopus_id))

    with respx.mock:
        route = _mock_abstracts(side_effect=mostly_ok)
        manifest = capture_abstracts(project, record_ids=records)

    assert route.call_count == _CORPUS_SIZE
    assert manifest.records_fetched == _CORPUS_SIZE - len(withdrawn)
    assert sorted(u.reason for u in manifest.unavailable) == ["not_found"] * len(withdrawn)


@pytest.mark.integration
def test_abstract__url__addresses_the_eid_endpoint_with_the_stored_identifier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The request URL must match what Scopus actually serves.

    Every other test here mocks whichever endpoint the client happens to call,
    so all of them passed for three releases while the client sent an EID to
    ``/content/abstract/scopus_id/`` -- a path that expects the bare numeric
    Scopus id. Real Scopus answered 404 for every record of a live corpus, and
    the enrichment appeared to find nothing.

    This asserts the URL against Elsevier's contract rather than against our
    own mock: a record id is ``scopus:2-s2.0-<digits>`` (BUILD_PLAN §3.2),
    which is an EID, so it belongs on the ``/eid/`` path with only the
    namespace removed. Measured against a real key on 2026-09-01:
    ``/scopus_id/2-s2.0-...`` 404s while ``/eid/2-s2.0-...`` returns 200.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    seen: list[str] = []

    def record_url(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json=_abstract_body("2-s2.0-85012345678"))

    with respx.mock:
        respx.get(url__startswith="https://api.elsevier.com/content/abstract/").mock(
            side_effect=record_url
        )
        with ScopusClient() as client:
            client.abstract("scopus:2-s2.0-85012345678")

    assert len(seen) == 1
    url = seen[0]
    assert "/content/abstract/eid/2-s2.0-85012345678" in url, url
    assert "/scopus_id/" not in url, "an EID sent to the scopus_id path 404s on real Scopus"
    assert "scopus:" not in url, "the prismabib namespace must not reach the API"
    assert "view=FULL" in url
