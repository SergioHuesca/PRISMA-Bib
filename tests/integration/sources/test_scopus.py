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

import json
import time
from pathlib import Path

import httpx
import pytest
import respx

from prismabib.config import Settings
from prismabib.errors import (
    AuthError,
    EntitlementError,
    QuotaExceededError,
    UpstreamError,
    ValidationError,
)
from prismabib.sources.cache import HttpCache
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.scopus import RecordNotFoundError, ScopusClient

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


@pytest.mark.integration
@pytest.mark.parametrize(
    "required_phrase",
    [
        pytest.param("institutional token", id="names-the-usual-remedy"),
        pytest.param("SCOPUS_INSTTOKEN", id="names-the-env-var-to-set"),
        pytest.param("library", id="names-who-to-ask"),
    ],
)
def test_scopus__entitlement_error__tells_the_researcher_what_to_do(
    required_phrase: str,
) -> None:
    """A 403 is the most common way a new researcher's first session ends.

    The message used to spend two-thirds of its length justifying *why*
    prismabib refuses to degrade to ``view=STANDARD``, citing a BUILD_PLAN
    section the user does not have on disk -- correct, and useless to
    somebody who just wants to know whether they can use the tool at all.
    Explaining the refusal is not the same as telling them what to do about
    it, and only one of those unblocks anybody.

    These assertions exist so that guidance cannot be silently refactored
    away: the design rationale may be reworded freely, but the remedy has
    to survive.
    """
    error_body = {
        "service-error": {
            "status": {
                "statusCode": "AUTHORIZATION_ERROR",
                "statusText": "The requestor is not entitled to access this resource",
            }
        }
    }

    with respx.mock:
        respx.get(_SEARCH_URL).mock(return_value=httpx.Response(403, json=error_body))
        client = _client()

        with pytest.raises(EntitlementError) as excinfo:
            list(client.search('TITLE-ABS-KEY("x")', view="COMPLETE"))

    assert required_phrase in str(excinfo.value)


@pytest.mark.integration
def test_scopus__auth_error__names_the_insttoken_mismatch_trap() -> None:
    """The single most common newcomer mistake deserves to be named.

    Pasting the API key into both ``SCOPUS_API_KEY`` and
    ``SCOPUS_INSTTOKEN`` yields a 401 whose Scopus-side text is
    "Institution Token is not associated with API Key". The operator of
    this very repository hit it. A message that says only "check your
    credentials" leaves them checking the one field that was right.
    """
    with respx.mock:
        respx.get(_SEARCH_URL).mock(return_value=httpx.Response(401, json={}))
        client = _client()

        with pytest.raises(AuthError) as excinfo:
            list(client.search('TITLE-ABS-KEY("x")', view="COMPLETE"))

    message = str(excinfo.value)
    assert "Institution Token is not associated with API Key" in message
    assert "dev.elsevier.com" in message


@pytest.mark.integration
def test_scopus__abstract_404__raises_record_not_found_after_exactly_one_request() -> None:
    """A withdrawn record must cost one call, not the whole retry budget.

    The call count is the load-bearing assertion, and it is the only one that
    distinguishes the fix from the defect: raising ``RecordNotFoundError``
    after five attempts would satisfy the exception check while still burning
    the budget. Scopus withdraws and merges records, so across an 1,800-record
    enrichment this is close to certain, and retrying cannot conjure a record
    that no longer exists.
    """
    url = f"{ScopusClient.ABSTRACT_URL_PREFIX}scopus_id/2-s2.0-85000000001"

    with respx.mock:
        route = respx.get(url__startswith=ScopusClient.ABSTRACT_URL_PREFIX).mock(
            return_value=httpx.Response(404, json={"service-error": {}})
        )
        client = _client()

        with pytest.raises(RecordNotFoundError, match="withdraws and merges"):
            client.abstract("scopus:2-s2.0-85000000001")

    assert route.call_count == 1


@pytest.mark.integration
def test_scopus__search_404__stays_a_retried_upstream_error_naming_real_causes() -> None:
    """A 404 on the search endpoint is not a withdrawn record and must not claim to be.

    No identifier appears in that URL's path, so the causes are a proxy, a
    rewritten base URL, or a gateway fault -- transient things worth retrying.
    Reusing the record-specific message here would send an operator hunting for
    a paper that was never named.
    """
    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(return_value=httpx.Response(404, json={}))
        client = _client()

        with pytest.raises(UpstreamError, match="addresses no individual record"):
            list(client.search('TITLE-ABS-KEY("x")', view="COMPLETE"))

    assert route.call_count == ScopusClient.MAX_ATTEMPTS


_ABSTRACT_URL = ScopusClient.ABSTRACT_ENDPOINT_TEMPLATE.format(scopus_id="2-s2.0-1")


def _cached_client(cache_dir: Path) -> ScopusClient:
    return ScopusClient(
        _settings(),
        rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS),
        cache=HttpCache(cache_dir),
    )


def _cache_files(cache_dir: Path) -> list[Path]:
    return [p for p in cache_dir.rglob("*") if p.is_file()]


@pytest.mark.integration
@pytest.mark.parametrize(
    "bad",
    [
        httpx.Response(200, content=b"<html>captive portal</html>"),
        httpx.Response(200, json={"service-error": {"status": {"statusCode": "GENERIC_ERROR"}}}),
    ],
    ids=["html", "service-error"],
)
def test_abstract__bad_200_then_good__nothing_cached_and_retry_hits_the_network(
    tmp_path: Path, bad: httpx.Response
) -> None:
    good = {"abstracts-retrieval-response": {"coredata": {"dc:title": "t"}}}
    client = _cached_client(tmp_path / "cache")
    with respx.mock:
        route = respx.get(_ABSTRACT_URL).mock(side_effect=[bad, httpx.Response(200, json=good)])
        with pytest.raises(ValidationError):
            client.abstract("scopus:2-s2.0-1")
        assert not _cache_files(tmp_path / "cache")

        assert client.abstract("scopus:2-s2.0-1") == good
        assert route.call_count == 2
        assert client.abstract("scopus:2-s2.0-1") == good
    assert route.call_count == 2


@pytest.mark.integration
def test_search__bad_200_then_good__nothing_cached_and_retry_hits_the_network(
    tmp_path: Path,
) -> None:
    good = _page(entry_ids=["1"], current="*", next_cursor=None)
    client = _cached_client(tmp_path / "cache")
    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(
            side_effect=[
                httpx.Response(200, content=b"<html>captive portal</html>"),
                httpx.Response(200, json={"service-error": {"status": {}}}),
                httpx.Response(200, json=good),
            ]
        )
        for _ in range(2):
            with pytest.raises(ValidationError):
                list(client.search('TITLE-ABS-KEY("x")'))
            assert not _cache_files(tmp_path / "cache")

        assert list(client.search('TITLE-ABS-KEY("x")')) == [good]
        assert list(client.search('TITLE-ABS-KEY("x")')) == [good]
    assert route.call_count == 3


_POISON = json.dumps({"service-error": {"status": {"statusCode": "GENERIC_ERROR"}}}).encode()
_META_URL = ScopusClient.ABSTRACT_ENDPOINT_TEMPLATE.format(scopus_id="2-s2.0-1")


@pytest.mark.integration
@pytest.mark.parametrize("view", ["FULL", "META"])
def test_abstract__poisoned_cache_entry__raises_validation_error(tmp_path: Path, view: str) -> None:
    HttpCache(tmp_path / "cache").store(_ABSTRACT_URL, {"view": view}, _POISON)
    client = _cached_client(tmp_path / "cache")
    with respx.mock:
        route = respx.get(_ABSTRACT_URL).mock(return_value=httpx.Response(500))
        with pytest.raises(ValidationError):
            client.abstract("scopus:2-s2.0-1") if view == "FULL" else client.abstract_meta(
                "scopus:2-s2.0-1"
            )
    assert route.call_count == 0


@pytest.mark.integration
def test_abstract_meta__bad_200_then_good__nothing_cached_and_retry_hits_the_network(
    tmp_path: Path,
) -> None:
    good = {"abstracts-retrieval-response": {"coredata": {"dc:identifier": "SCOPUS_ID:1"}}}
    client = _cached_client(tmp_path / "cache")
    with respx.mock:
        route = respx.get(_META_URL).mock(
            side_effect=[
                httpx.Response(200, content=_POISON),
                httpx.Response(200, json=good),
            ]
        )
        with pytest.raises(ValidationError):
            client.abstract_meta("scopus:2-s2.0-1")
        assert not _cache_files(tmp_path / "cache")

        client.abstract_meta("scopus:2-s2.0-1")
        assert route.call_count == 2
        client.abstract_meta("scopus:2-s2.0-1")
    assert route.call_count == 2


@pytest.mark.integration
def test_search_standard_by_dois__bad_200_then_good__nothing_cached_and_retry_hits_the_network(
    tmp_path: Path,
) -> None:
    good = _page(entry_ids=["1"], current="*", next_cursor=None)
    good["search-results"]["opensearch:totalResults"] = "0"
    good["search-results"]["entry"] = []
    client = _cached_client(tmp_path / "cache")
    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(
            side_effect=[httpx.Response(200, content=_POISON), httpx.Response(200, json=good)]
        )
        with pytest.raises(ValidationError):
            client.search_standard_by_dois(["10.1/a"])
        assert not _cache_files(tmp_path / "cache")

        client.search_standard_by_dois(["10.1/a"])
        assert route.call_count == 2
        client.search_standard_by_dois(["10.1/a"])
    assert route.call_count == 2


@pytest.mark.integration
def test_search__200_with_zero_remaining_and_far_reset__next_request_raises_quota_error() -> None:
    slept: list[float] = []
    limiter = RateLimiter(rate=1000.0, sleep=slept.append)
    client = ScopusClient(_settings(), rate_limiter=limiter)
    reset = str(int(time.time()) + 3 * 86400)
    page = _page(entry_ids=["1"], current="*", next_cursor=None)

    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(
            return_value=httpx.Response(
                200,
                json=page,
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset},
            )
        )
        assert len(list(client.search("q"))) == 1
        with pytest.raises(QuotaExceededError):
            list(client.search("q2"))

    assert route.call_count == 1
    assert slept == []
