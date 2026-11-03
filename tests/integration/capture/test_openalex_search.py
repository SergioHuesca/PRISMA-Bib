"""Integration tests for ``capture/openalex_search.py`` (ADR 0042 Decision 4, PR 3).

Real filesystem (``tmp_path``), no network: every run is driven through a fake
``httpx.MockTransport`` whose request counter is the assertion for quota. Response shapes follow
the live probe of 2026-11-03 (``meta.count`` on every page, ``meta.cost_usd`` 0.001, DOIs as
``https://doi.org/`` URLs, ``x-ratelimit-*`` headers, ``Invalid cursor value`` as HTTP 400).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from prismabib.capture.layout import (
    NON_RUN_DIRNAMES,
    OPENALEX_SEARCH_DIRNAME,
    RUN_MANIFEST_FILENAME,
    is_sealed,
)
from prismabib.capture.manifest import OpenAlexSearchRunManifest
from prismabib.capture.openalex import PROGRESS_FILENAME, ApiKeyInBodyError
from prismabib.capture.openalex_search import capture_openalex_search
from prismabib.config import Settings
from prismabib.errors import AuthError, ConfigError, RateLimitError
from prismabib.project import Project
from prismabib.sources.openalex import SEARCH_SELECT, classify_429
from prismabib.sources.ratelimit import RateLimiter

pytestmark = pytest.mark.integration

_KEY = "oa-sentinel-key-not-real"  # pragma: allowlist secret
_QUERY = '"carbon capture" AND policy'
_REMOVED = (
    "sustainable_development_goals_aurora",
    "host_venue",
    "alternate_host_venues",
    "grants",
    "has_ngrams",
)
Handler = Callable[[httpx.Request], httpx.Response]


@pytest.fixture(autouse=True)
def _isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.chdir(tmp_path)
    for name in ("OPENALEX_API_KEY", "SCOPUS_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    yield


class Fake:
    """Serves ``pages`` by cursor (``*``, ``c1``, ...) and records every request."""

    def __init__(self, pages: list[list[dict[str, Any]]], *, count: int | None = None) -> None:
        self.pages = pages
        self.count = sum(len(p) for p in pages) if count is None else count
        self.requests: list[httpx.Request] = []
        self.override: Handler | None = None
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.override is not None:
            return self.override(request)
        cursor = request.url.params["cursor"]
        index = 0 if cursor == "*" else int(cursor.removeprefix("c"))
        meta: dict[str, Any] = {
            "count": self.count,
            "per_page": 100,
            "next_cursor": f"c{index + 1}" if index + 1 < len(self.pages) else None,
            "cost_usd": 0.001,
        }
        return page_response({"meta": meta, "results": self.pages[index]})


def page_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        content=json.dumps(payload, separators=(",", ":")).encode(),
        headers={"x-ratelimit-remaining": "9990", "x-ratelimit-reset": "40000"},
    )


def works(*numbers: int) -> list[dict[str, Any]]:
    return [
        {
            "id": f"https://openalex.org/W{n}",
            "doi": f"https://doi.org/10.1/{n}",
            "type": "article",
            "cited_by_count": n,
            "authorships": [],
        }
        for n in numbers
    ]


def project(tmp_path: Path, mode: str = "openalex") -> Project:
    return Project.init("demo", title="Demo", root=tmp_path / "p", source_mode=mode, actor="t")  # type: ignore[arg-type]


def settings(key: str | None = _KEY) -> Settings:
    return Settings(openalex_api_key=SecretStr(key) if key else None)


def run(proj: Project, fake: Fake, *, budget: int = 10, **kwargs: Any) -> Any:
    kwargs.setdefault("settings", settings())
    return capture_openalex_search(
        proj,
        query=kwargs.pop("query", _QUERY),
        budget=budget,
        transport=fake.transport,
        rate_limiter=RateLimiter(rate=1_000_000.0),
        sleep=lambda _s: None,
        **kwargs,
    )


def runs(proj: Project) -> list[Path]:
    root = proj.raw_dir / OPENALEX_SEARCH_DIRNAME
    return sorted(p for p in root.iterdir()) if root.is_dir() else []


def progress_of(run_dir: Path) -> dict[str, Any]:
    return json.loads((run_dir / PROGRESS_FILENAME).read_text(encoding="utf-8"))


def three_pages() -> Fake:
    return Fake([works(1, 2), works(3, 4), works(5)])


def test_layout__openalex_search__is_a_non_run_dirname() -> None:
    assert OPENALEX_SEARCH_DIRNAME in NON_RUN_DIRNAMES


# --- the mode and the key guard ------------------------------------------------------------


def test_openalex_search__scopus_plus_openalex_mode__refused_before_any_request(
    tmp_path: Path,
) -> None:
    fake = three_pages()
    with pytest.raises(ConfigError, match="openalex_search"):
        run(project(tmp_path, "scopus+openalex"), fake)
    assert fake.requests == []


def test_openalex_search__no_key__refused_before_first_request(tmp_path: Path) -> None:
    fake = three_pages()
    proj = project(tmp_path)
    with pytest.raises(ConfigError, match="OPENALEX_API_KEY"):
        run(proj, fake, settings=settings(None))
    assert fake.requests == []
    assert runs(proj) == []


def test_openalex_search__without_scopus_key__succeeds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismabib.sources import scopus

    def boom(*_a: object, **_k: object) -> None:
        raise AssertionError("the Scopus client must not be built in mode openalex")

    monkeypatch.setattr(scopus, "ScopusClient", boom)
    result = run(project(tmp_path), three_pages())
    assert result.status == "sealed"


# --- paging, the request and the seal -----------------------------------------------------


def test_openalex_search__pages_by_cursor_until_next_cursor_is_null(tmp_path: Path) -> None:
    proj = project(tmp_path)
    fake = three_pages()
    result = run(proj, fake)

    assert result.status == "sealed"
    assert [r.url.params["cursor"] for r in fake.requests] == ["*", "c1", "c2"]
    manifest = OpenAlexSearchRunManifest.model_validate_json(
        (runs(proj)[0] / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert (manifest.total_count, manifest.works_captured, manifest.complete) == (5, 5, True)
    assert [r.cursor_sent for r in manifest.requests] == ["*", "c1", "c2"]
    assert manifest.query == _QUERY
    assert manifest.query_key == _QUERY
    assert is_sealed(runs(proj)[0])
    assert not (runs(proj)[0] / PROGRESS_FILENAME).exists()
    assert result.spent_usd == pytest.approx(0.003)


def test_openalex_search__request_uses_search_per_page_and_a_clean_select(tmp_path: Path) -> None:
    fake = three_pages()
    run(project(tmp_path), fake)
    params = fake.requests[0].url.params
    assert params["search"] == _QUERY
    assert params["per_page"] == "100"
    assert "per-page" not in params
    assert not any(k.endswith(".search") for k in params)
    assert "cited_by_count" in params["select"].split(",")
    assert params["select"] == SEARCH_SELECT
    for removed in _REMOVED:
        assert removed not in params["select"]


def test_openalex_search__key_only_in_header_never_in_params_or_manifest(tmp_path: Path) -> None:
    proj = project(tmp_path)
    fake = three_pages()
    run(proj, fake)
    for request in fake.requests:
        assert request.headers["Authorization"] == f"Bearer {_KEY}"
        assert _KEY not in str(request.url)
    for path in runs(proj)[0].iterdir():
        assert _KEY.encode() not in path.read_bytes()


def test_openalex_search__body_carrying_the_key__not_written(tmp_path: Path) -> None:
    proj = project(tmp_path)
    fake = three_pages()
    fake.override = lambda _r: page_response({"meta": {"count": 1}, "results": [], "echo": _KEY})
    with pytest.raises(ApiKeyInBodyError):
        run(proj, fake)
    assert not list(runs(proj)[0].glob("response-*"))
    assert not is_sealed(runs(proj)[0])


# --- budget stop, resume, reuse -----------------------------------------------------------


def test_openalex_search__budget_stop_leaves_run_unsealed_and_resumes_from_cursor(
    tmp_path: Path,
) -> None:
    proj = project(tmp_path)
    fake = three_pages()
    first = run(proj, fake, budget=2)

    assert first.status == "budget_stop"
    (run_dir,) = runs(proj)
    assert not is_sealed(run_dir)
    state = progress_of(run_dir)
    assert state["next_cursor"] == "c2"
    assert state["discarded"] is False
    assert len(fake.requests) == 2

    second = run(proj, fake, budget=2)
    assert second.status == "sealed"
    assert second.run_id == first.run_id
    assert [r.url.params["cursor"] for r in fake.requests] == ["*", "c1", "c2"]  # none re-asked
    assert len(runs(proj)) == 1


def test_openalex_search__same_query_sealed__creates_no_run_and_no_request(tmp_path: Path) -> None:
    proj = project(tmp_path)
    fake = three_pages()
    run(proj, fake)
    fake.requests.clear()
    again = run(proj, fake, query="  " + _QUERY.replace(" ", "   ") + " ")
    assert again.status == "already_sealed"
    assert fake.requests == []
    assert len(runs(proj)) == 1


def test_openalex_search__refresh_makes_new_run_and_leaves_the_sealed_one_alone(
    tmp_path: Path,
) -> None:
    proj = project(tmp_path)
    fake = three_pages()
    first = runs(proj)[0] if run(proj, fake) else None
    assert first is not None
    before = {p.name: p.read_bytes() for p in first.iterdir()}
    result = run(proj, fake, refresh=True)
    assert result.status == "sealed"
    assert len(runs(proj)) == 2
    assert {p.name: p.read_bytes() for p in first.iterdir()} == before


# --- discard and restart ------------------------------------------------------------------


def test_openalex_search__seal_refused_when_works_captured_differs_from_total_count(
    tmp_path: Path,
) -> None:
    proj = project(tmp_path)
    fake = Fake([works(1, 2), works(3)], count=4)
    result = run(proj, fake)

    assert result.status == "discarded"
    assert "discard and restart" in result.message
    (run_dir,) = runs(proj)
    assert not (run_dir / RUN_MANIFEST_FILENAME).exists()
    assert progress_of(run_dir)["discarded"] is True
    files = {p.name: p.read_bytes() for p in run_dir.glob("response-*")}

    fake2 = Fake([works(1, 2), works(3, 4)])
    again = run(proj, fake2)
    assert again.status == "sealed"
    assert again.run_id != result.run_id
    assert {p.name: p.read_bytes() for p in run_dir.glob("response-*")} == files


def test_openalex_search__no_count_on_first_page__discarded_without_spending_more(
    tmp_path: Path,
) -> None:
    proj = project(tmp_path)
    fake = three_pages()
    fake.override = lambda _r: page_response({"meta": {"next_cursor": "c1"}, "results": works(1)})
    result = run(proj, fake)
    assert result.status == "discarded"
    assert len(fake.requests) == 1


def test_openalex_search__resume_with_changed_meta_count__discard_and_restart(
    tmp_path: Path,
) -> None:
    proj = project(tmp_path)
    fake = three_pages()
    run(proj, fake, budget=1)
    fake.count = 6  # the corpus moved while the run waited
    result = run(proj, fake)

    assert result.status == "discarded"
    assert "discard and restart" in result.message
    (run_dir,) = runs(proj)
    assert progress_of(run_dir)["discarded"] is True
    assert not (run_dir / RUN_MANIFEST_FILENAME).exists()


def test_openalex_search__discarded_run_is_never_resumed(tmp_path: Path) -> None:
    proj = project(tmp_path)
    fake = three_pages()
    run(proj, fake, budget=1)
    fake.count = 6
    discarded = run(proj, fake)
    fake.count = 5
    fake.requests.clear()

    fresh = run(proj, fake)
    assert fresh.status == "sealed"
    assert fresh.run_id != discarded.run_id
    assert fake.requests[0].url.params["cursor"] == "*"  # the old cursor is never used again


def test_openalex_search__expired_cursor__discard_and_restart(tmp_path: Path) -> None:
    proj = project(tmp_path)
    fake = three_pages()
    run(proj, fake, budget=1)
    fake.override = lambda _r: httpx.Response(400, json={"message": "Invalid cursor value"})
    result = run(proj, fake)

    assert result.status == "discarded"
    assert "discard and restart" in result.message
    (run_dir,) = runs(proj)
    assert progress_of(run_dir)["discarded"] is True
    assert not (run_dir / RUN_MANIFEST_FILENAME).exists()


def test_openalex_search__other_400_on_a_cursor__raises_and_stays_resumable(
    tmp_path: Path,
) -> None:
    from prismabib.errors import UpstreamError

    proj = project(tmp_path)
    fake = three_pages()
    run(proj, fake, budget=1)
    fake.override = lambda _r: httpx.Response(400, json={"message": "something else"})
    with pytest.raises(UpstreamError):
        run(proj, fake)
    assert progress_of(runs(proj)[0])["discarded"] is False


# --- 401 and the two flavours of 429 -------------------------------------------------------


def test_openalex_search__401_past_page_one__raises_auth_error_and_stays_resumable(
    tmp_path: Path,
) -> None:
    proj = project(tmp_path)
    fake = three_pages()
    run(proj, fake, budget=1)
    fake.override = lambda _r: httpx.Response(401, json={"error": "key required"})
    with pytest.raises(AuthError):
        run(proj, fake)
    assert progress_of(runs(proj)[0])["discarded"] is False


def test_openalex_search__per_second_429__is_retried_and_the_run_completes(
    tmp_path: Path,
) -> None:
    proj = project(tmp_path)
    fake = three_pages()
    inner = fake._handle
    seen: list[int] = []

    def flaky(request: httpx.Request) -> httpx.Response:
        seen.append(1)
        if len(seen) == 1:
            return httpx.Response(429, json=_PER_SECOND_BODY, headers=_PER_SECOND_HEADERS)
        fake.override = None
        return inner(request)

    fake.override = flaky
    result = run(proj, fake)
    assert result.status == "sealed"


def test_openalex_search__per_second_429_that_never_clears__raises_rate_limit_error(
    tmp_path: Path,
) -> None:
    fake = three_pages()
    fake.override = lambda _r: httpx.Response(
        429, json=_PER_SECOND_BODY, headers=_PER_SECOND_HEADERS
    )
    with pytest.raises(RateLimitError):
        run(project(tmp_path), fake)
    assert len(fake.requests) == 5  # the client's retry budget


_PER_SECOND_BODY = {  # live shape, 2026-11-03: remaining fields read 0 although the day is not spent
    "error": "Rate limit exceeded",
    "message": "Rate limit exceeded: 30 requests per second. Please slow down.",
    "retryAfter": 0.0163,
    "costUsd": 0,
    "dailyRemainingUsd": 0,
}
_PER_SECOND_HEADERS = {
    "retry-after": "1",
    "x-ratelimit-remaining": "0",
    "x-ratelimit-remaining-usd": "0",
}


_DAILY_BODY = {  # live shape, 2026-11-03
    "error": "Rate limit exceeded",
    "message": "Insufficient budget. This request costs $0.001 but you only have $0 remaining. "
    "Resets at midnight UTC. Need more? Add funds at https://openalex.org/pricing",
    "retryAfter": 34265,
    "costUsd": 0.001,
    "dailyRemainingUsd": 0,
    "creditsRequired": 10,
    "creditsRemaining": 0,
}
_DAILY_HEADERS = {
    "retry-after": "34265",
    "x-ratelimit-reset": "34265",
    "x-ratelimit-remaining": "0",
    "x-ratelimit-remaining-usd": "0",
}


@pytest.mark.parametrize(
    ("headers", "body", "expected"),
    [
        (_PER_SECOND_HEADERS, _PER_SECOND_BODY, "per_second"),
        (_DAILY_HEADERS, _DAILY_BODY, "daily"),
        # Both flavours report 0 remaining, so an unknown shape stops rather than retries.
        ({"retry-after": "1"}, {"message": "something new"}, "daily"),
        ({}, {"message": "Anonymous search is temporarily rate-limited"}, "daily"),
    ],
)
def test_classify_429__message_text_not_remaining_fields__per_second_or_daily(
    headers: dict[str, str], body: dict[str, Any], expected: str
) -> None:
    assert classify_429(httpx.Response(429, json=body, headers=headers)) == expected


def test_openalex_search__insufficient_budget_429__stops_with_reset_recorded_and_no_retry(
    tmp_path: Path,
) -> None:
    proj = project(tmp_path)
    fake = three_pages()
    run(proj, fake, budget=1)
    fake.requests.clear()
    fake.override = lambda _r: httpx.Response(429, json=_DAILY_BODY, headers=_DAILY_HEADERS)
    stopped = run(proj, fake)

    assert stopped.status == "daily_budget"
    assert len(fake.requests) == 1  # one request, no retry loop
    assert stopped.resets_in_seconds == 34265
    assert stopped.reason.startswith("Insufficient budget")
    state = progress_of(runs(proj)[0])
    assert state["resets_in_seconds"] == 34265
    assert state["stop_reason"].startswith("Insufficient budget")
    assert state["discarded"] is False
    assert not is_sealed(runs(proj)[0])

    fake.override = None  # the next UTC day
    assert run(proj, fake).status == "sealed"
    assert is_sealed(runs(proj)[0])


def test_openalex_search__cost_usd_from_meta_else_list_price(tmp_path: Path) -> None:
    fake = Fake([works(1)])
    fake.override = lambda _r: page_response(
        {"meta": {"count": 1, "next_cursor": None}, "results": works(1)}
    )
    assert run(project(tmp_path), fake).spent_usd == pytest.approx(0.001)  # list price
