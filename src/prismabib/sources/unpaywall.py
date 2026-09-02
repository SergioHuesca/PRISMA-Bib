"""The Unpaywall client (BUILD_PLAN Stage 6, ADR 0019).

Unpaywall (https://unpaywall.org/products/api) answers "does an open-access
copy of this DOI exist, and where" -- ``GET
https://api.unpaywall.org/v2/{doi}?email={email}``. Unlike Scopus and
ScienceDirect, it takes no API key: the ``email`` query parameter is
Unpaywall's own terms-of-use requirement (a real, reachable contact address,
not a credential), which is why :class:`~prismabib.config.Settings` carries
``unpaywall_email`` as a plain ``str`` rather than a ``SecretStr`` -- see
that module's docstring.

Two calls, not one: :meth:`UnpaywallClient.lookup` asks Unpaywall itself for
the best OA location, and :meth:`UnpaywallClient.fetch_bytes` then downloads
the PDF from wherever that location actually is -- an institutional
repository, a preprint server, the publisher's own site -- which is a
different host on every call and carries no Unpaywall-specific shape at all.
Both go through the same injectable
:class:`~prismabib.sources.ratelimit.RateLimiter` and
:class:`~prismabib.sources.cache.HttpCache`, and the same ``tenacity``
backoff policy as :mod:`prismabib.sources.scopus`, reused rather than
reimplemented.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, Self

import httpx
import structlog
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from prismabib.config import Settings
from prismabib.errors import ConfigError, RateLimitError, UpstreamError, ValidationError
from prismabib.sources.cache import HttpCache
from prismabib.sources.ratelimit import RateLimiter

logger = structlog.get_logger(__name__)

JsonDict = dict[str, Any]


class UnpaywallClient:
    """A client for the Unpaywall API and for downloading the OA copies it locates.

    Args:
        settings: The environment configuration providing
            ``UNPAYWALL_EMAIL``. Defaults to ``Settings()`` when omitted.
        http_client: An injectable ``httpx.Client``, primarily for tests.
        rate_limiter: An injectable
            :class:`~prismabib.sources.ratelimit.RateLimiter`.
        cache: An injectable :class:`~prismabib.sources.cache.HttpCache`.
        timeout: The ``httpx`` request timeout in seconds, used only when
            this client constructs its own ``http_client``.

    Raises:
        ConfigError: If ``settings`` is omitted and the environment cannot
            be loaded, or if ``UNPAYWALL_EMAIL`` is not set.
    """

    LOOKUP_ENDPOINT_TEMPLATE = "https://api.unpaywall.org/v2/{doi}"
    MAX_ATTEMPTS = 5

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        http_client: httpx.Client | None = None,
        rate_limiter: RateLimiter | None = None,
        cache: HttpCache | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._settings = settings if settings is not None else Settings()
        if not self._settings.unpaywall_email:
            raise ConfigError(
                "UNPAYWALL_EMAIL is not set. Unpaywall's API requires a real, reachable "
                "email address as a query parameter (not a credential -- see its terms "
                "of use at https://unpaywall.org/products/api). Set UNPAYWALL_EMAIL in "
                "your .env."
            )
        self._http = http_client if http_client is not None else httpx.Client(timeout=timeout)
        self._owns_http = http_client is None
        self._rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter()
        self._cache = cache

    def __enter__(self) -> Self:
        """Enter the client as a context manager; returns ``self``."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Exit the client as a context manager, closing an owned HTTP client."""
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client, if this instance owns one."""
        if self._owns_http:
            self._http.close()

    def lookup(self, doi: str) -> JsonDict | None:
        """Ask Unpaywall for the best open-access location of one DOI.

        Args:
            doi: The bare DOI (``10.xxxx/...``), not URL-wrapped.

        Returns:
            The parsed Unpaywall response, or ``None`` if Unpaywall has no
            record for this DOI (HTTP 404) -- not an entitlement question,
            simply "we do not know this DOI", so this resolver step
            contributes ``entitled=NULL`` rather than any refusal.

        Raises:
            RateLimitError: On HTTP 429 exhausting the retry budget.
            UpstreamError: On HTTP 5xx exhausting the retry budget, or any
                other unexpected non-2xx status.
            ValidationError: If a 200 response body is not a JSON object.
        """
        url = self.LOOKUP_ENDPOINT_TEMPLATE.format(doi=doi)
        params = {"email": self._settings.unpaywall_email or ""}
        cached = self._cache.load(url, params) if self._cache is not None else None
        if cached is not None:
            logger.info("unpaywall.lookup", endpoint=url, cache="hit")
            return None if cached == b"" else _parse_json(cached)

        response = self._request_with_retry(url, params)
        if response.status_code == 404:
            logger.info("unpaywall.lookup.not_found", endpoint=url)
            if self._cache is not None:
                self._cache.store(url, params, b"")
            return None

        body = response.content
        if self._cache is not None:
            self._cache.store(url, params, body)
        logger.info("unpaywall.lookup", endpoint=url, cache="miss")
        return _parse_json(body)

    def fetch_bytes(self, url: str) -> bytes:
        """Download the bytes at an open-access location URL.

        Args:
            url: A URL taken from an Unpaywall ``best_oa_location`` (e.g.
                ``url_for_pdf``). Not necessarily Unpaywall's own host --
                this is wherever the OA copy actually lives.

        Returns:
            The raw response body.

        Raises:
            RateLimitError: On HTTP 429 exhausting the retry budget.
            UpstreamError: On HTTP 5xx exhausting the retry budget, on HTTP
                404 (the location Unpaywall pointed to no longer resolves),
                or any other unexpected non-2xx status.
        """
        response = self._request_with_retry(url, {}, allow_404=False)
        return response.content

    def _request_with_retry(
        self, url: str, params: Mapping[str, str], *, allow_404: bool = True
    ) -> httpx.Response:
        """Perform one logical request, retrying transient failures with backoff."""
        retryer = Retrying(
            stop=stop_after_attempt(self.MAX_ATTEMPTS),
            wait=wait_random_exponential(multiplier=1, max=30),
            retry=retry_if_exception_type((RateLimitError, UpstreamError)),
            reraise=True,
        )
        response: httpx.Response = retryer(self._do_request, url, params, allow_404)
        return response

    def _do_request(self, url: str, params: Mapping[str, str], allow_404: bool) -> httpx.Response:
        """Perform exactly one HTTP GET, honouring the rate limiter and raising on error status."""
        self._rate_limiter.acquire()
        response = self._http.get(url, params=dict(params))
        self._rate_limiter.observe_headers(response.headers)
        self._raise_for_status(response, allow_404=allow_404)
        return response

    def _raise_for_status(self, response: httpx.Response, *, allow_404: bool) -> None:
        """Translate a non-2xx Unpaywall (or OA-location) response into the §3.3 taxonomy."""
        status = response.status_code
        if 200 <= status < 300:
            return
        if status == 404 and allow_404:
            return

        endpoint = str(response.request.url)

        if status == 429:
            logger.warning("unpaywall.request.rate_limited", endpoint=endpoint, status=status)
            raise RateLimitError(f"Unpaywall rate-limited the request (HTTP 429) for {endpoint}.")

        if 500 <= status < 600:
            logger.warning("unpaywall.request.upstream_error", endpoint=endpoint, status=status)
            raise UpstreamError(
                f"Unpaywall (or the OA host) returned HTTP {status} for {endpoint}."
            )

        logger.warning("unpaywall.request.unexpected_status", endpoint=endpoint, status=status)
        raise UpstreamError(
            f"Unpaywall (or the OA host) returned unexpected HTTP {status} for {endpoint}."
        )


def _parse_json(body: bytes) -> JsonDict:
    """Parse a raw Unpaywall response body, raising a prismabib error on failure.

    Args:
        body: The raw response bytes.

    Returns:
        The parsed JSON object.

    Raises:
        ValidationError: If ``body`` is not valid JSON, or parses to
            something other than a JSON object.
    """
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Unpaywall response is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValidationError(
            f"Unpaywall response is valid JSON but not a JSON object (got {type(parsed).__name__})"
        )
    return parsed


def best_oa_pdf_url(response: JsonDict) -> str | None:
    """Read the best PDF URL out of a parsed Unpaywall response, if any.

    Args:
        response: The parsed response from :meth:`UnpaywallClient.lookup`.

    Returns:
        ``best_oa_location.url_for_pdf`` when present and non-empty, else
        ``best_oa_location.url`` under the same condition (some OA
        locations, e.g. an HTML landing page, carry only a generic ``url``),
        else ``None`` when Unpaywall reports no OA location at all
        (``best_oa_location`` is ``null`` when ``is_oa`` is ``false``).
    """
    location = response.get("best_oa_location")
    if not isinstance(location, Mapping):
        return None
    for key in ("url_for_pdf", "url"):
        value = location.get(key)
        if isinstance(value, str) and value:
            return value
    return None


__all__ = ["UnpaywallClient", "best_oa_pdf_url"]
