"""Size-check functions (GUI spec #312, ticket #314): stubbed transport, fake key, synthetic bodies."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from pydantic import SecretStr

from prismabib.config import Settings
from prismabib.errors import AuthError, ConfigError, UpstreamError
from prismabib.sizecheck import (
    NOT_REPORTED,
    check_openalex_size,
    check_scopus_size,
)
from prismabib.sources.ratelimit import RateLimiter

_KEY = "fake-key-not-real"  # pragma: allowlist secret
_NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)


def _client_kwargs(handler: Any) -> dict[str, Any]:
    return {
        "transport": httpx.MockTransport(handler),
        "rate_limiter": RateLimiter(rate=1_000_000.0),
        "sleep": lambda _s: None,
    }


def _scopus(handler: Any, *, key: str | None = _KEY) -> Any:
    settings = Settings(scopus_api_key=SecretStr(key) if key else None)
    return check_scopus_size(
        "TITLE(x)",
        settings=settings,
        transport=httpx.MockTransport(handler),
        rate_limiter=RateLimiter(rate=1_000_000.0),
    )


def _openalex(handler: Any, *, key: str | None = _KEY) -> Any:
    settings = Settings(openalex_api_key=SecretStr(key) if key else None)
    return check_openalex_size("x", settings=settings, now=lambda: _NOW, **_client_kwargs(handler))


def _scopus_body(total: Any) -> bytes:
    return json.dumps(
        {"search-results": {"opensearch:totalResults": str(total), "entry": []}}
    ).encode()


def _oa_body(count: int) -> bytes:
    return json.dumps({"meta": {"count": count, "next_cursor": "c1"}, "results": []}).encode()


def test_scopus__reports_count_cost_and_weekly_remaining() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-ELS-APIKey"] == _KEY
        return httpx.Response(
            200, content=_scopus_body(101), headers={"X-RateLimit-Remaining": "19990"}
        )

    result = _scopus(handler)
    assert (result.record_count, result.quota_cost, result.weekly_remaining) == (101, 5, 19990)


def test_scopus__zero_records__costs_nothing() -> None:
    result = _scopus(lambda _r: httpx.Response(200, content=_scopus_body(0)))
    assert (result.record_count, result.quota_cost) == (0, 0)


def test_scopus__allowance_omitted__is_not_reported() -> None:
    result = _scopus(lambda _r: httpx.Response(200, content=_scopus_body(3)))
    assert result.weekly_remaining == NOT_REPORTED


def test_scopus__unparseable_allowance__is_not_reported() -> None:
    result = _scopus(
        lambda _r: httpx.Response(
            200, content=_scopus_body(3), headers={"X-RateLimit-Remaining": "lots"}
        )
    )
    assert result.weekly_remaining == NOT_REPORTED


def test_scopus__rejected_key__raises_auth_error() -> None:
    with pytest.raises(AuthError):
        _scopus(lambda _r: httpx.Response(401))


def test_scopus__network_failure__raises_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(UpstreamError):
        _scopus(handler)


def test_scopus__missing_key__raises_config_error() -> None:
    with pytest.raises(ConfigError):
        _scopus(lambda _r: httpx.Response(200), key=None)


def test_openalex__reports_count_cost_pages_allowance_and_reset() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == f"Bearer {_KEY}"
        return httpx.Response(
            200,
            content=_oa_body(250),
            headers={"x-ratelimit-remaining": "9990", "x-ratelimit-reset": "43200"},
        )

    result = _openalex(handler)
    assert result.record_count == 250
    assert result.pages == 3
    assert result.cost_bound_usd == pytest.approx(0.003)
    assert result.daily_remaining == 9990
    assert result.resets_at == datetime(2026, 11, 11, 0, 0, 0, tzinfo=UTC)
    assert result.resets_at.tzinfo is UTC  # type: ignore[union-attr]


def test_openalex__allowance_omitted__is_not_reported() -> None:
    result = _openalex(lambda _r: httpx.Response(200, content=_oa_body(1)))
    assert result.daily_remaining == NOT_REPORTED
    assert result.resets_at == NOT_REPORTED


def test_openalex__rejected_key__raises_auth_error() -> None:
    with pytest.raises(AuthError):
        _openalex(lambda _r: httpx.Response(401))


def test_openalex__network_failure__raises_upstream_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(UpstreamError):
        _openalex(handler)


def test_openalex__no_count_in_body__raises_validation_error() -> None:
    from prismabib.errors import ValidationError

    with pytest.raises(ValidationError):
        _openalex(lambda _r: httpx.Response(200, content=b'{"results": []}'))


def test_openalex__missing_key__raises_config_error() -> None:
    with pytest.raises(ConfigError):
        _openalex(lambda _r: httpx.Response(200), key=None)
