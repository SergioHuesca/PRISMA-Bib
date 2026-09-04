"""Integration tests for ``src/prismabib/sources/crossref.py`` (ADR 0020).

Mock only at the HTTP boundary (``respx``), the same discipline
``tests/integration/sources/test_unpaywall.py`` already established.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from prismabib.config import Settings
from prismabib.errors import EntitlementError, UpstreamError, ValidationError
from prismabib.sources.cache import HttpCache
from prismabib.sources.crossref import CrossrefTdmClient
from prismabib.sources.ratelimit import RateLimiter

_DOI = "10.1007/s00000-026-0001-0"
_LOOKUP_ENDPOINT = CrossrefTdmClient.LOOKUP_ENDPOINT_TEMPLATE.format(doi=_DOI)
_TDM_URL = "https://link.springer.com/content/pdf/10.1007/s00000-026-0001-0.pdf"
_FAST_RATE_LIMITER_KWARGS = {"rate": 1000.0}


def _settings(*, email: str | None = "reviewer@example.org") -> Settings:
    return Settings(  # pragma: allowlist secret
        _env_file=None,
        scopus_api_key="test-scopus-key",  # pragma: allowlist secret
        unpaywall_email=email,
    )


def _client(**kwargs: object) -> CrossrefTdmClient:
    settings = kwargs.pop("settings", None) or _settings()
    return CrossrefTdmClient(
        settings, rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS), **kwargs
    )


def _work_response() -> dict[str, object]:
    return {
        "message": {
            "link": [
                {
                    "URL": _TDM_URL,
                    "intended-application": "text-mining",
                    "content-type": "application/pdf",
                }
            ]
        }
    }


@pytest.mark.integration
def test_client__no_settings_given__constructs_without_a_credential() -> None:
    """Unlike Unpaywall/ScienceDirect, Crossref needs no credential at all (ADR 0020)."""
    CrossrefTdmClient(_settings(email=None))


@pytest.mark.integration
def test_lookup__200__returns_parsed_response() -> None:
    with respx.mock:
        route = respx.get(_LOOKUP_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_work_response())
        )
        client = _client()

        response = client.lookup(_DOI)

    assert route.call_count == 1
    assert response == _work_response()


@pytest.mark.integration
def test_lookup__email_configured__sent_as_mailto() -> None:
    with respx.mock:
        route = respx.get(_LOOKUP_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_work_response())
        )
        client = _client(settings=_settings(email="reviewer@example.org"))

        client.lookup(_DOI)

    assert route.calls.last.request.url.params["mailto"] == "reviewer@example.org"


@pytest.mark.integration
def test_lookup__no_email_configured__works_anonymously() -> None:
    """The public pool: no ``mailto`` parameter is sent at all."""
    with respx.mock:
        route = respx.get(_LOOKUP_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_work_response())
        )
        client = _client(settings=_settings(email=None))

        client.lookup(_DOI)

    assert "mailto" not in route.calls.last.request.url.params


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
    assert route.call_count == 1


@pytest.mark.integration
def test_lookup__not_json__raises_validation_error() -> None:
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
            side_effect=[httpx.Response(429), httpx.Response(200, json=_work_response())]
        )
        client = _client()

        response = client.lookup(_DOI)

    assert route.call_count == 2
    assert response == _work_response()


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

    assert route.call_count == CrossrefTdmClient.MAX_ATTEMPTS


@pytest.mark.integration
def test_fetch_bytes__200__returns_content_and_actual_content_type() -> None:
    with respx.mock:
        respx.get(_TDM_URL).mock(
            return_value=httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=b"%PDF-1.4\n%%EOF"
            )
        )
        client = _client()

        content, content_type = client.fetch_bytes(_TDM_URL)

    assert content == b"%PDF-1.4\n%%EOF"
    assert content_type == "application/pdf"


@pytest.mark.integration
@pytest.mark.acceptance("S06-AC2")
def test_fetch_bytes__403__raises_entitlement_error_without_retrying() -> None:
    """The BLOCKING regression this pins: a TDM host's 403 is an entitlement question.

    Unlike an Unpaywall OA location (no entitlement concept, so a 403 there
    is a plain ``UpstreamError``), a Crossref TDM link points at a
    publisher's own licensed text-mining endpoint -- the same shape as
    ScienceDirect's 403 -- so ``CrossrefTdmResolver`` needs
    ``EntitlementError`` specifically to record ``entitled=False`` rather
    than "not found" (ADR 0019, restated by ADR 0020).
    """
    with respx.mock:
        route = respx.get(_TDM_URL).mock(return_value=httpx.Response(403))
        client = _client()

        with pytest.raises(EntitlementError):
            client.fetch_bytes(_TDM_URL)

    assert route.call_count == 1


@pytest.mark.integration
def test_fetch_bytes__5xx_exhausts_retries__raises_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with respx.mock:
        route = respx.get(_TDM_URL).mock(return_value=httpx.Response(503))
        client = _client()

        with pytest.raises(UpstreamError):
            client.fetch_bytes(_TDM_URL)

    assert route.call_count == CrossrefTdmClient.MAX_ATTEMPTS


@pytest.mark.integration
def test_fetch_bytes__404__raises_upstream_error_without_retry() -> None:
    with respx.mock:
        route = respx.get(_TDM_URL).mock(return_value=httpx.Response(404))
        client = _client()

        with pytest.raises(UpstreamError):
            client.fetch_bytes(_TDM_URL)

    assert route.call_count == 1


@pytest.mark.integration
@respx.mock
def test_fetch_bytes__every_request__identifies_the_client_in_the_user_agent() -> None:
    route = respx.get(_TDM_URL).mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.4\n%%EOF", headers={"content-type": "application/pdf"}
        )
    )

    with _client() as client:
        client.fetch_bytes(_TDM_URL)

    user_agent = route.calls.last.request.headers["user-agent"]
    assert user_agent.startswith("prismabib/")
    assert "github.com" in user_agent


@pytest.mark.integration
@respx.mock
def test_fetch_bytes__tdm_host_redirects__follows_and_returns_the_pdf() -> None:
    redirect_url = "https://link.springer.com/content/pdf/10.1007/actual.pdf"
    respx.get(_TDM_URL).mock(return_value=httpx.Response(302, headers={"location": redirect_url}))
    respx.get(redirect_url).mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.4\n%%EOF", headers={"content-type": "application/pdf"}
        )
    )

    with _client() as client:
        content, content_type = client.fetch_bytes(_TDM_URL)

    assert content.startswith(b"%PDF-")
    assert content_type == "application/pdf"


@pytest.mark.integration
@respx.mock
def test_lookup__api_redirects_elsewhere__is_not_followed() -> None:
    respx.get(_LOOKUP_ENDPOINT).mock(
        return_value=httpx.Response(302, headers={"location": "https://elsewhere.example.org/x"})
    )

    with _client() as client, pytest.raises(UpstreamError):
        client.lookup(_DOI)
