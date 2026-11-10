"""Size checks: how big a search is before any Record is captured (GUI spec #312, ticket #314).

Each function makes one request and writes nothing. When the service does not report an
allowance, the result carries :data:`NOT_REPORTED` instead of a guess.

Errors are the library's own: :class:`~prismabib.errors.AuthError` for a rejected key,
:class:`~prismabib.errors.UpstreamError` for a network failure or a bad status,
:class:`~prismabib.errors.ConfigError` for a missing key.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Literal

import httpx

from prismabib.capture.openalex_search import cost_bound_usd
from prismabib.config import Settings, require_openalex_key, require_scopus_key
from prismabib.errors import UpstreamError, ValidationError
from prismabib.sources.openalex import SEARCH_PAGE_SIZE, OpenAlexClient
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.scopus import ScopusClient

NOT_REPORTED: Final = "not reported"
NotReported = Literal["not reported"]


@dataclass(frozen=True)
class ScopusSizeCheck:
    """What a Scopus query would return and cost.

    Attributes:
        record_count: ``opensearch:totalResults``.
        quota_cost: Requests the capture would spend (one per page of 25).
        weekly_remaining: ``X-RateLimit-Remaining`` as the service sent it, or not reported.
    """

    record_count: int
    quota_cost: int
    weekly_remaining: int | NotReported


@dataclass(frozen=True)
class OpenAlexSizeCheck:
    """What an OpenAlex search would return and cost.

    Attributes:
        record_count: ``meta.count``.
        pages: The page budget the capture needs (100 Records a page).
        cost_bound_usd: The most those pages cost at list price.
        daily_remaining: ``x-ratelimit-remaining`` as the service sent it (its own units), or
            not reported.
        resets_at: When the daily allowance resets (UTC), from ``x-ratelimit-reset`` (seconds
            from now), or not reported.
    """

    record_count: int
    pages: int
    cost_bound_usd: float
    daily_remaining: int | NotReported
    resets_at: datetime | NotReported


def _as_int(value: str | None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]  # None lands in the except
    except (TypeError, ValueError):
        return None


def check_scopus_size(
    query: str,
    *,
    settings: Settings | None = None,
    transport: httpx.BaseTransport | None = None,
    rate_limiter: RateLimiter | None = None,
) -> ScopusSizeCheck:
    """Read how many Records a Scopus query matches, with one request.

    Args:
        query: The Scopus query string.
        settings: Environment settings (the key); defaults to ``Settings()``.
        transport: An ``httpx`` transport, for tests.
        rate_limiter: An injectable limiter, for tests.

    Raises:
        ConfigError: If the key is not set.
        AuthError: On HTTP 401.
        UpstreamError: On a network failure or a 5xx past the retries.
        ValidationError: If the body is not a Scopus search response.
    """
    resolved = settings if settings is not None else Settings()
    require_scopus_key(resolved)
    http = httpx.Client(transport=transport, timeout=30.0)
    with ScopusClient(resolved, http_client=http, rate_limiter=rate_limiter) as client:
        try:
            total, remaining = client.probe_size(query)
        finally:
            http.close()
    allowance = _as_int(remaining)
    return ScopusSizeCheck(
        record_count=total,
        quota_cost=math.ceil(total / ScopusClient.PAGE_SIZE),
        weekly_remaining=NOT_REPORTED if allowance is None else allowance,
    )


def check_openalex_size(
    query: str,
    *,
    settings: Settings | None = None,
    transport: httpx.BaseTransport | None = None,
    rate_limiter: RateLimiter | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> OpenAlexSizeCheck:
    """Read how many Records an OpenAlex search matches, with one request (one page).

    Args:
        query: The OpenAlex ``search`` string, sent unchanged.
        settings: Environment settings (the key); defaults to ``Settings()``.
        transport: An ``httpx`` transport, for tests.
        rate_limiter: An injectable limiter, for tests.
        sleep: How retries wait; replaced in tests.
        now: The clock the reset instant is computed from; replaced in tests.

    Raises:
        ConfigError: If the key is not set.
        AuthError: On HTTP 401.
        UpstreamError: On a network failure, a 5xx past the retries, or any other bad status.
        ValidationError: If the body carries no ``meta.count``.
    """
    resolved = settings if settings is not None else Settings()
    require_openalex_key(resolved)
    http = httpx.Client(transport=transport, timeout=30.0)
    with OpenAlexClient(resolved, http_client=http, rate_limiter=rate_limiter, sleep=sleep) as c:
        try:
            page = c.fetch_search_page(query, "*", select="id")
        finally:
            http.close()
    if page.status != 200:
        raise UpstreamError(f"OpenAlex returned HTTP {page.status}.")
    try:
        parsed: Any = json.loads(page.body)
    except ValueError as exc:
        raise ValidationError("OpenAlex returned a body that is not JSON.") from exc
    meta = parsed.get("meta") if isinstance(parsed, dict) else None
    count = meta.get("count") if isinstance(meta, dict) else None
    if not isinstance(count, int) or isinstance(count, bool):
        raise ValidationError("OpenAlex returned no meta.count.")
    headers = {k.lower(): v for k, v in page.headers.items()}
    remaining = _as_int(headers.get("x-ratelimit-remaining"))
    reset = _as_int(headers.get("x-ratelimit-reset"))
    pages = math.ceil(count / SEARCH_PAGE_SIZE)
    return OpenAlexSizeCheck(
        record_count=count,
        pages=pages,
        cost_bound_usd=cost_bound_usd(pages),
        daily_remaining=NOT_REPORTED if remaining is None else remaining,
        resets_at=NOT_REPORTED if reset is None else now() + timedelta(seconds=reset),
    )
