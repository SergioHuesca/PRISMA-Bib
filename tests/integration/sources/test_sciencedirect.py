"""Integration tests for ``src/prismabib/sources/sciencedirect.py`` (ADR 0019).

Mock only at the HTTP boundary (``respx``), the same discipline
``tests/integration/sources/test_scopus.py`` already established; see that
module's docstring for why the retry-exhaustion tests below also patch the
stdlib ``time.sleep``.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from prismabib.config import Settings
from prismabib.errors import AuthError, ConfigError, EntitlementError, RateLimitError, UpstreamError
from prismabib.sources.cache import HttpCache
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.sciencedirect import ArticleNotFoundError, ScienceDirectClient

_DOI = "10.1016/j.example.2026.100001"
_ENDPOINT = ScienceDirectClient.ARTICLE_ENDPOINT_TEMPLATE.format(doi=_DOI)
_FAST_RATE_LIMITER_KWARGS = {"rate": 1000.0}
_MODELLED_XML = (
    Path(__file__).parent.parent.parent
    / "fixtures"
    / "cassettes"
    / "sciencedirect-article-full-modelled.xml"
).read_bytes()


def _settings(*, api_key: str | None = "test-sd-key") -> Settings:
    # A literal dummy, never a real credential -- same allowlist discipline as
    # tests/integration/sources/test_scopus.py's `_settings()`.
    return Settings(  # pragma: allowlist secret
        _env_file=None,
        scopus_api_key="test-scopus-key",  # pragma: allowlist secret
        elsevier_sd_api_key=api_key,
    )


def _client(**kwargs: object) -> ScienceDirectClient:
    settings = kwargs.pop("settings", None) or _settings()
    return ScienceDirectClient(
        settings, rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS), **kwargs
    )


@pytest.mark.integration
def test_client__missing_api_key__raises_config_error() -> None:
    with pytest.raises(ConfigError, match="ELSEVIER_SD_API_KEY"):
        ScienceDirectClient(_settings(api_key=None))


@pytest.mark.integration
def test_client__empty_api_key__raises_config_error() -> None:
    """The BLOCKING regression this pins: ``ELSEVIER_SD_API_KEY=`` in ``.env``.

    pydantic-settings parses an empty ``.env`` value into ``SecretStr("")``,
    not ``None`` -- ``.env.example`` ships exactly this shape. Before this
    fix, only ``is None`` was checked, so the client constructed
    successfully, sent an empty ``X-ELS-APIKey`` header, and Elsevier
    answered 401 -- aborting the whole resolver chain (including
    ``ManualDropResolver``, which needs no credential at all) instead of
    degrading gracefully the way ``default_chain`` is written to.
    """
    with pytest.raises(ConfigError, match="ELSEVIER_SD_API_KEY"):
        ScienceDirectClient(_settings(api_key=""))


@pytest.mark.integration
def test_article_retrieval_xml__200__returns_body() -> None:
    with respx.mock:
        route = respx.get(_ENDPOINT).mock(return_value=httpx.Response(200, content=_MODELLED_XML))
        client = _client()

        body = client.article_retrieval_xml(_DOI)

    assert route.call_count == 1
    assert body == _MODELLED_XML


@pytest.mark.integration
def test_article_retrieval_xml__cache_hit__does_not_call_the_network() -> None:
    cache = HttpCache(Path("/tmp") / "prismabib-test-sd-cache")
    cache.store(_ENDPOINT, {"view": "FULL"}, _MODELLED_XML)

    with respx.mock:
        route = respx.get(_ENDPOINT).mock(return_value=httpx.Response(500))
        client = _client(cache=cache)

        body = client.article_retrieval_xml(_DOI)

    assert route.call_count == 0
    assert body == _MODELLED_XML


@pytest.mark.integration
def test_article_retrieval_xml__401__raises_auth_error_without_retry() -> None:
    with respx.mock:
        route = respx.get(_ENDPOINT).mock(return_value=httpx.Response(401))
        client = _client()

        with pytest.raises(AuthError):
            client.article_retrieval_xml(_DOI)

    assert route.call_count == 1


@pytest.mark.integration
@pytest.mark.acceptance("S06-AC2")
def test_article_retrieval_xml__403__raises_entitlement_error_without_retry() -> None:
    with respx.mock:
        route = respx.get(_ENDPOINT).mock(return_value=httpx.Response(403))
        client = _client()

        with pytest.raises(EntitlementError):
            client.article_retrieval_xml(_DOI)

    assert route.call_count == 1


@pytest.mark.integration
def test_article_retrieval_xml__404__raises_article_not_found_without_retry() -> None:
    with respx.mock:
        route = respx.get(_ENDPOINT).mock(return_value=httpx.Response(404))
        client = _client()

        with pytest.raises(ArticleNotFoundError):
            client.article_retrieval_xml(_DOI)

    assert route.call_count == 1


@pytest.mark.integration
def test_article_retrieval_xml__429_then_200__retries_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with respx.mock:
        route = respx.get(_ENDPOINT).mock(
            side_effect=[httpx.Response(429), httpx.Response(200, content=_MODELLED_XML)]
        )
        client = _client()

        body = client.article_retrieval_xml(_DOI)

    assert route.call_count == 2
    assert body == _MODELLED_XML


@pytest.mark.integration
def test_article_retrieval_xml__429_exhausts_retries__raises_rate_limit_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with respx.mock:
        route = respx.get(_ENDPOINT).mock(return_value=httpx.Response(429))
        client = _client()

        with pytest.raises(RateLimitError):
            client.article_retrieval_xml(_DOI)

    assert route.call_count == ScienceDirectClient.MAX_ATTEMPTS


@pytest.mark.integration
def test_article_retrieval_xml__5xx_exhausts_retries__raises_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with respx.mock:
        route = respx.get(_ENDPOINT).mock(return_value=httpx.Response(503))
        client = _client()

        with pytest.raises(UpstreamError):
            client.article_retrieval_xml(_DOI)

    assert route.call_count == ScienceDirectClient.MAX_ATTEMPTS


@pytest.mark.integration
def test_article_retrieval_xml__unexpected_status__raises_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unrecognised status (not 401/403/404/429/5xx) also raises ``UpstreamError``.

    Unlike :mod:`prismabib.sources.unpaywall` (fixed alongside this stage's
    review to *not* retry a non-5xx unexpected status), this client's retry
    predicate is unconditional on ``UpstreamError`` and this branch was not
    part of the reported defect -- so this still retries to exhaustion, and
    the assertion says so rather than assuming it does not.
    """
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    with respx.mock:
        route = respx.get(_ENDPOINT).mock(return_value=httpx.Response(418))
        client = _client()

        with pytest.raises(UpstreamError):
            client.article_retrieval_xml(_DOI)

    assert route.call_count == ScienceDirectClient.MAX_ATTEMPTS
