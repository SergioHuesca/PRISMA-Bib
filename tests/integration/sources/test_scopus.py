"""Integration tests for ``src/prismabib/sources/scopus.py`` (BUILD_PLAN Stage 2 Tests table, lines 815-821).

Mock only at the HTTP boundary (``respx``, §3.7.3 rule 1). Every test builds
its own :class:`~prismabib.sources.scopus.ScopusClient` with an injected
:class:`~prismabib.sources.ratelimit.RateLimiter` running far faster than
the production default (6 req/s) -- not to change behaviour under test, but
so a handful of requests never has to wait out even the default limiter's
sub-second burst window, keeping these comfortably under the < 2 s
integration budget (BUILD_PLAN §3.7.2).

``test_search__429_then_200__retries_and_succeeds`` and
``test_search__5xx_exhausts_retries__raises_upstream_error`` additionally
patch ``time.sleep`` (via the standard ``monkeypatch`` fixture) to a no-op.
This is not a ``prismabib.*`` patch -- ``ScopusClient`` builds its
``tenacity.Retrying`` inline and exposes no seam to inject a fake sleep
function into the retry backoff, unlike ``RateLimiter``'s own
constructor-level ``clock``/``sleep`` parameters (see
``tests/unit/sources/test_ratelimit.py``). Patching the stdlib
``time.sleep`` itself is squarely "the system clock" (§3.7.3 rule 1's other
permitted double, alongside ``respx``), just applied to the one clock
primitive ``time-machine`` does not cover: it does not touch prismabib code,
does not change what is retried or how many times, and turns an
otherwise-real multi-second exponential backoff into a fast, deterministic
test -- exactly the same trade time-machine makes for wall-clock reads.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from prismabib.config import Settings
from prismabib.errors import AuthError, EntitlementError, UpstreamError
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.scopus import ScopusClient

_SEARCH_URL = ScopusClient.SEARCH_ENDPOINT

_FAST_RATE_LIMITER_KWARGS = {"rate": 1000.0}


def _settings() -> Settings:
    # A literal dummy, never a real credential. detect-secrets' keyword heuristic
    # cannot distinguish it from a live key, and this repository is public, so the
    # suppression is scoped to this single line rather than widened to the file.
    return Settings(  # pragma: allowlist secret
        _env_file=None,
        scopus_api_key="test-api-key",  # pragma: allowlist secret
    )


def _client() -> ScopusClient:
    return ScopusClient(_settings(), rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS))


def _page(*, entry_ids: list[str], current: str, next_cursor: str | None) -> dict:
    return {
        "search-results": {
            "opensearch:totalResults": "1575",
            "entry": [{"dc:identifier": f"SCOPUS_ID:{eid}"} for eid in entry_ids],
            "cursor": {"@current": current, "@next": next_cursor or ""},
        }
    }


@pytest.mark.integration
def test_search__three_pages__yields_all_records_once() -> None:
    page0 = _page(entry_ids=["1", "2"], current="*", next_cursor="CUR1")
    page1 = _page(entry_ids=["3", "4"], current="CUR1", next_cursor="CUR2")
    page2 = _page(entry_ids=["5", "6"], current="CUR2", next_cursor=None)

    with respx.mock:
        respx.get(_SEARCH_URL).mock(
            side_effect=[
                httpx.Response(200, json=page0),
                httpx.Response(200, json=page1),
                httpx.Response(200, json=page2),
            ]
        )
        client = _client()

        pages = list(client.search('TITLE-ABS-KEY("x")'))

    record_ids = [
        entry["dc:identifier"] for page in pages for entry in page["search-results"]["entry"]
    ]

    assert sorted(record_ids) == sorted(f"SCOPUS_ID:{n}" for n in ("1", "2", "3", "4", "5", "6"))
    assert len(record_ids) == len(set(record_ids))


@pytest.mark.integration
def test_search__cursor_unchanged_between_pages__terminates() -> None:
    stale_page = _page(entry_ids=["1"], current="*", next_cursor="*")

    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=stale_page))
        client = _client()

        pages = list(client.search('TITLE-ABS-KEY("x")'))

    assert len(pages) == 1
    assert route.call_count == 1


@pytest.mark.integration
def test_search__429_then_200__retries_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    success_page = _page(entry_ids=["1"], current="*", next_cursor=None)

    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(
            side_effect=[httpx.Response(429), httpx.Response(200, json=success_page)]
        )
        client = _client()

        pages = list(client.search('TITLE-ABS-KEY("x")'))

    assert route.call_count == 2
    assert pages == [success_page]


@pytest.mark.integration
def test_search__401__raises_auth_error_without_retry() -> None:
    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(return_value=httpx.Response(401))
        client = _client()

        with pytest.raises(AuthError):
            list(client.search('TITLE-ABS-KEY("x")'))

    assert route.call_count == 1


@pytest.mark.integration
@pytest.mark.acceptance("S02-AC4")
def test_search__403_on_complete_view__raises_entitlement_error() -> None:
    """S02-AC4, the most important test in this stage.

    Never falls back to STANDARD: the assertion below does not stop at "an
    ``EntitlementError`` was raised" -- it inspects every request ``respx``
    actually recorded and requires each one to have carried
    ``view=COMPLETE``. A client that silently retried under
    ``view=STANDARD`` after the first 403 would still raise (STANDARD would
    also eventually error or succeed with a degraded payload upstream), so
    only inspecting the exception is not sufficient to catch that
    regression -- inspecting the issued requests is.
    """
    error_body = {
        "service-error": {
            "status": {
                "statusCode": "AUTHORIZATION_ERROR",
                "statusText": "The requestor is not entitled to access this resource under view=COMPLETE",
            }
        }
    }

    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(return_value=httpx.Response(403, json=error_body))
        client = _client()

        with pytest.raises(EntitlementError, match="view=COMPLETE"):
            list(client.search('TITLE-ABS-KEY("x")', view="COMPLETE"))

    issued_views = [call.request.url.params.get("view") for call in route.calls]
    assert issued_views == ["COMPLETE"]


@pytest.mark.integration
def test_search__5xx_exhausts_retries__raises_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(return_value=httpx.Response(503))
        client = _client()

        with pytest.raises(UpstreamError):
            list(client.search('TITLE-ABS-KEY("x")'))

    assert route.call_count == ScopusClient.MAX_ATTEMPTS
