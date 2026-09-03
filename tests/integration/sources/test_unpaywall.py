"""Integration tests for ``src/prismabib/sources/unpaywall.py`` (ADR 0019).

Mock only at the HTTP boundary (``respx``), the same discipline
``tests/integration/sources/test_scopus.py`` already established.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from prismabib.config import Settings
from prismabib.errors import ConfigError, UpstreamError
from prismabib.sources.cache import HttpCache
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.unpaywall import UnpaywallClient

_DOI = "10.1109/tpami.2026.100001"
_LOOKUP_ENDPOINT = UnpaywallClient.LOOKUP_ENDPOINT_TEMPLATE.format(doi=_DOI)
_OA_PDF_URL = "https://oa-host.example.org/paper.pdf"
_FAST_RATE_LIMITER_KWARGS = {"rate": 1000.0}


def _settings(*, email: str | None = "reviewer@example.org") -> Settings:
    return Settings(  # pragma: allowlist secret
        _env_file=None,
        scopus_api_key="test-scopus-key",  # pragma: allowlist secret
        unpaywall_email=email,
    )


def _client(**kwargs: object) -> UnpaywallClient:
    settings = kwargs.pop("settings", None) or _settings()
    return UnpaywallClient(
        settings, rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS), **kwargs
    )


def _oa_response() -> dict[str, object]:
    return {"best_oa_location": {"url_for_pdf": _OA_PDF_URL, "url": _OA_PDF_URL}}


@pytest.mark.integration
def test_client__missing_email__raises_config_error() -> None:
    with pytest.raises(ConfigError, match="UNPAYWALL_EMAIL"):
        UnpaywallClient(_settings(email=None))


@pytest.mark.integration
def test_lookup__200__returns_parsed_response() -> None:
    with respx.mock:
        route = respx.get(_LOOKUP_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_oa_response())
        )
        client = _client()

        response = client.lookup(_DOI)

    assert route.call_count == 1
    assert response == _oa_response()


@pytest.mark.integration
def test_lookup__404__returns_none_and_caches_the_negative_result(tmp_path: Path) -> None:
    cache = HttpCache(tmp_path / "cache")

    with respx.mock:
        route = respx.get(_LOOKUP_ENDPOINT).mock(return_value=httpx.Response(404))
        client = _client(cache=cache)

        first = client.lookup(_DOI)
        second = client.lookup(_DOI)

    assert first is None
    assert second is None
    # The second call is served from the negative cache entry, not the network.
    assert route.call_count == 1


@pytest.mark.integration
def test_lookup__cache_hit__does_not_call_the_network(tmp_path: Path) -> None:
    import json

    cache = HttpCache(tmp_path / "cache")
    cache.store(
        _LOOKUP_ENDPOINT,
        {"email": "reviewer@example.org"},
        json.dumps(_oa_response()).encode("utf-8"),
    )

    with respx.mock:
        route = respx.get(_LOOKUP_ENDPOINT).mock(return_value=httpx.Response(500))
        client = _client(cache=cache)

        response = client.lookup(_DOI)

    assert route.call_count == 0
    assert response == _oa_response()


@pytest.mark.integration
def test_lookup__not_json__raises_validation_error() -> None:
    from prismabib.errors import ValidationError

    with respx.mock:
        respx.get(_LOOKUP_ENDPOINT).mock(return_value=httpx.Response(200, content=b"not json"))
        client = _client()

        with pytest.raises(ValidationError):
            client.lookup(_DOI)


@pytest.mark.integration
def test_lookup__429_then_200__retries_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with respx.mock:
        route = respx.get(_LOOKUP_ENDPOINT).mock(
            side_effect=[httpx.Response(429), httpx.Response(200, json=_oa_response())]
        )
        client = _client()

        response = client.lookup(_DOI)

    assert route.call_count == 2
    assert response == _oa_response()


@pytest.mark.integration
def test_lookup__5xx_exhausts_retries__raises_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with respx.mock:
        route = respx.get(_LOOKUP_ENDPOINT).mock(return_value=httpx.Response(503))
        client = _client()

        with pytest.raises(UpstreamError):
            client.lookup(_DOI)

    assert route.call_count == UnpaywallClient.MAX_ATTEMPTS


@pytest.mark.integration
def test_fetch_bytes__200__returns_content_and_content_type() -> None:
    with respx.mock:
        respx.get(_OA_PDF_URL).mock(
            return_value=httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4\n%%EOF"
            )
        )
        client = _client()

        content, content_type = client.fetch_bytes(_OA_PDF_URL)

    assert content == b"%PDF-1.4\n%%EOF"
    assert content_type == "application/pdf"


@pytest.mark.integration
@pytest.mark.acceptance("S06-AC2")
def test_fetch_bytes__403__raises_immediately_without_retrying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The BLOCKING regression this pins.

    Before this fix, every unexpected non-5xx status (including this 403 --
    a Cloudflare-fronted OA host refusing a scripted download is a real
    shape this takes) was raised as the same ``UpstreamError`` type the
    retry predicate matched on, so it was retried
    ``UnpaywallClient.MAX_ATTEMPTS`` times with exponential backoff before
    being raised anyway. ``monkeypatch``ing ``time.sleep`` to a no-op would
    hide that regression from a call-count assertion alone if a retry loop
    reintroduced it silently -- so this test does NOT patch ``time.sleep``:
    if the fix regresses, this test would also become slow (multiple real
    ``wait_random_exponential`` sleeps), which is deliberate, not an
    oversight.
    """
    with respx.mock:
        route = respx.get(_OA_PDF_URL).mock(return_value=httpx.Response(403))
        client = _client()

        with pytest.raises(UpstreamError):
            client.fetch_bytes(_OA_PDF_URL)

    assert route.call_count == 1


@pytest.mark.integration
def test_fetch_bytes__5xx_exhausts_retries__raises_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with respx.mock:
        route = respx.get(_OA_PDF_URL).mock(return_value=httpx.Response(503))
        client = _client()

        with pytest.raises(UpstreamError):
            client.fetch_bytes(_OA_PDF_URL)

    assert route.call_count == UnpaywallClient.MAX_ATTEMPTS


@pytest.mark.integration
def test_fetch_bytes__404__raises_upstream_error_without_retry() -> None:
    """``fetch_bytes`` never tolerates a 404 the way ``lookup`` does (``allow_404=False``)."""
    with respx.mock:
        route = respx.get(_OA_PDF_URL).mock(return_value=httpx.Response(404))
        client = _client()

        with pytest.raises(UpstreamError):
            client.fetch_bytes(_OA_PDF_URL)

    assert route.call_count == 1


@pytest.mark.integration
@respx.mock
def test_fetch_bytes__oa_host_redirects__follows_and_returns_the_pdf() -> None:
    """A redirect is an ordinary open-access download, not a failure.

    `httpx` defaults `follow_redirects` to False, unlike `requests`. This is
    the only client that fetches from *arbitrary* hosts -- whatever Unpaywall
    names as the OA location -- and repositories redirect as a matter of
    course: DSpace to a bitstream, a DOI to a publisher, http to https.

    Measured on a real 35-record corpus before the fix: 10 records failed
    mid-chain and **6 of them were 301/302**, surfaced to the operator as
    "an upstream outage, a network timeout". Six recoverable papers lost to a
    client default.
    """
    redirect_url = "https://oa-host.example.org/bitstream/1234/paper.pdf"
    respx.get(_OA_PDF_URL).mock(
        return_value=httpx.Response(302, headers={"location": redirect_url})
    )
    respx.get(redirect_url).mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.4\n%%EOF", headers={"content-type": "application/pdf"}
        )
    )

    with _client() as client:
        content, content_type = client.fetch_bytes(_OA_PDF_URL)

    assert content.startswith(b"%PDF-")
    assert content_type == "application/pdf"


@pytest.mark.integration
@respx.mock
def test_fetch_bytes__every_request__identifies_the_client_in_the_user_agent() -> None:
    """Requests carry a descriptive User-Agent, not `python-httpx/x.y.z`.

    Identifying an unauthenticated client is ordinary good manners, and several
    open-access hosts refuse the default outright -- the same 35-record run drew
    three 403s and a 418 from OA repositories.

    The header carries no email: Unpaywall receives one as a query parameter
    because its terms require it, but the OA hosts this client then downloads
    from are third parties that never asked.
    """
    route = respx.get(_OA_PDF_URL).mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.4\n%%EOF", headers={"content-type": "application/pdf"}
        )
    )

    with _client() as client:
        client.fetch_bytes(_OA_PDF_URL)

    user_agent = route.calls.last.request.headers["user-agent"]
    assert user_agent.startswith("prismabib/")
    assert "github.com" in user_agent
    assert "@" not in user_agent
