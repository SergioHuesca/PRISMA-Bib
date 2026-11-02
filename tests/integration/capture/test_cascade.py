"""Integration tests for ``src/prismabib/capture/cascade.py`` (ADR 0041 Decision 6).

Real filesystem (``tmp_path``), no network: a fake ``httpx.MockTransport`` plays Scopus
and counts requests. Bodies are synthetic; no licensed Scopus payload is committed.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest

from prismabib.capture.cascade import _Progress, _seal, capture_scopus_cascade
from prismabib.capture.layout import NON_RUN_DIRNAMES, SealedRunError, guard_writable, is_sealed
from prismabib.capture.manifest import CascadeRunManifest
from prismabib.capture.openalex import ApiKeyInBodyError
from prismabib.capture.writer import payload_sha256
from prismabib.config import Settings
from prismabib.errors import ValidationError
from prismabib.project import Project
from prismabib.sources.ratelimit import RateLimiter

_KEY = "sentinel-scopus-key-123"
Handler = Callable[[httpx.Request], httpx.Response]
_AFF = [{"affilname": "Uni", "affiliation-city": "Lima", "affiliation-country": "Peru"}]


@pytest.fixture(autouse=True)
def _isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    yield


class Fake:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def _search(entries: list[dict[str, Any]]) -> httpx.Response:
    body = {"search-results": {"opensearch:totalResults": str(len(entries)), "entry": entries}}
    return httpx.Response(200, content=json.dumps(body).encode())


def _entry(sid: str, doi: str, aff: list[dict[str, str]] | None) -> dict[str, Any]:
    entry: dict[str, Any] = {"dc:identifier": f"SCOPUS_ID:{sid}", "prism:doi": doi}
    if aff is not None:
        entry["affiliation"] = aff
    return entry


def _meta(aff: list[dict[str, str]] | None) -> httpx.Response:
    body: dict[str, Any] = {"abstracts-retrieval-response": {"coredata": {}}}
    if aff is not None:
        body["abstracts-retrieval-response"]["affiliation"] = aff
    return httpx.Response(200, content=json.dumps(body).encode())


def _project(tmp_path: Path) -> Project:
    return Project.init("demo", title="Demo", root=tmp_path / "projects")


def _run(project: Project, fake: Fake, **kwargs: Any) -> CascadeRunManifest:
    kwargs.setdefault("fallback_opt_in", True)
    kwargs.setdefault("budget_requests", 10)
    return capture_scopus_cascade(
        project,
        settings=Settings(scopus_api_key=_KEY),
        transport=fake.transport,
        rate_limiter=RateLimiter(rate=1_000_000.0),
        **kwargs,
    )


def _runs(project: Project) -> list[Path]:
    root = project.raw_dir / "scopus-cascade"
    return sorted(root.iterdir()) if root.is_dir() else []


def test_non_run_dirnames__contains_scopus_cascade() -> None:
    assert "scopus-cascade" in NON_RUN_DIRNAMES


@pytest.mark.integration
def test_capture__standard_and_meta__outcomes_files_and_seal(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/abstract/eid/" in request.url.path:
            return httpx.Response(404, content=b"{}") if "999" in request.url.path else _meta(_AFF)
        # 10.1/c has no entry.
        return _search([_entry("1", "10.1/a", _AFF), _entry("2", "10.1/b", None)])

    project, fake = _project(tmp_path), Fake(handler)
    m = _run(project, fake, dois=["10.1/A", "10.1/b", "10.1/c"], scopus_ids=["5", "999"])

    (run_dir,) = _runs(project)
    assert is_sealed(run_dir) and not (run_dir / "progress.json").exists()
    assert m.run_id == run_dir.name and m.stop_reason == "complete" and m.fallback_opt_in
    assert m.budget_requests == 10 and m.requests_made == 3 and m.api_key_used
    assert [r.file for r in m.requests] == [f"response-000{i}.jsonl" for i in range(3)]
    assert [r.view for r in m.requests] == ["STANDARD", "META", "META"]
    out = {o.identifier: (o.outcome, o.affiliations) for r in m.requests for o in r.outcomes}
    assert out == {
        "10.1/a": ("value", 1),
        "10.1/b": ("absent", 0),
        "10.1/c": ("not_found", 0),
        "5": ("value", 1),
        "999": ("not_found", 0),
    }
    assert m.payload_files == [r.file for r in m.requests]
    assert m.payload_sha256 == payload_sha256(run_dir, m.payload_files)
    assert CascadeRunManifest.model_validate_json((run_dir / "manifest.json").read_text())
    with pytest.raises(SealedRunError):
        guard_writable(run_dir)


@pytest.mark.integration
def test_capture__401_in_opt_in_run__refused_and_run_continues(tmp_path: Path) -> None:
    fake = Fake(lambda r: httpx.Response(401, content=b"{}"))
    m = _run(_project(tmp_path), fake, dois=["10.1/a"], scopus_ids=["5"])
    assert [o.outcome for r in m.requests for o in r.outcomes] == ["refused", "refused"]
    assert [r.http_status for r in m.requests] == [401, 401]


@pytest.mark.integration
def test_capture__no_opt_in__refuses_without_request_or_run(tmp_path: Path) -> None:
    project, fake = _project(tmp_path), Fake(lambda r: _search([]))
    with pytest.raises(ValidationError):
        _run(project, fake, dois=["10.1/a"], fallback_opt_in=False)
    assert fake.requests == [] and _runs(project) == []


@pytest.mark.integration
def test_capture__budget_reached__stops_budget_and_seals(tmp_path: Path) -> None:
    project, fake = _project(tmp_path), Fake(lambda r: _meta(_AFF))
    m = _run(project, fake, scopus_ids=["1", "2", "3"], budget_requests=2)
    assert m.stop_reason == "budget" and m.requests_made == 2 and len(fake.requests) == 2
    assert is_sealed(_runs(project)[0])


@pytest.mark.integration
def test_capture__weekly_quota_429__stops_quota_and_seals(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if len(fake.requests) == 1:
            return _meta(_AFF)
        headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(time.time() + 10**6)}
        return httpx.Response(429, headers=headers, content=b"{}")

    project, fake = _project(tmp_path), Fake(handler)
    m = _run(project, fake, scopus_ids=["1", "2", "3"])
    assert m.stop_reason == "quota" and len(m.requests) == 1
    assert is_sealed(_runs(project)[0])


@pytest.mark.integration
def test_capture__key_in_body__not_written_and_run_unsealed(tmp_path: Path) -> None:
    project = _project(tmp_path)
    fake = Fake(lambda r: httpx.Response(200, content=f'{{"echo":"{_KEY}"}}'.encode()))
    with pytest.raises(ApiKeyInBodyError):
        _run(project, fake, scopus_ids=["1"])
    (run_dir,) = _runs(project)
    assert not is_sealed(run_dir) and not (run_dir / "response-0000.jsonl").exists()


@pytest.mark.integration
def test_capture__key_in_no_written_file(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _run(project, Fake(lambda r: _meta(_AFF)), scopus_ids=["1"])
    files = [p for p in _runs(project)[0].rglob("*") if p.is_file()]
    assert files
    assert all(_KEY not in p.read_text(encoding="utf-8") for p in files)


@pytest.mark.integration
def test_capture__duplicate_scopus_id_in_one_response__fails_loudly(tmp_path: Path) -> None:
    fake = Fake(lambda r: _search([_entry("1", "10.1/a", _AFF), _entry("1", "10.1/b", _AFF)]))
    with pytest.raises(ValidationError, match="1"):
        _run(_project(tmp_path), fake, dois=["10.1/a", "10.1/b"])


@pytest.mark.integration
def test_seal__fallback_opt_in_false__refused(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    progress = _Progress(fallback_opt_in=False, budget_requests=1, started_at=datetime.now(UTC))
    with pytest.raises(ValidationError):
        _seal(run_dir, progress, api_key_used=True, criteria_version="v1")
    assert not is_sealed(run_dir)
