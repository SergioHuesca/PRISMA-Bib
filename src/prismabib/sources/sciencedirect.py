"""The ScienceDirect Article Retrieval client (BUILD_PLAN Stage 6, ADR 0019).

Fetches Elsevier's ``FULL``-view Article Retrieval XML by DOI --
``GET https://api.elsevier.com/content/article/doi/{doi}``. This is
**entitled Elsevier content only**: the ScienceDirect API serves nothing
outside Elsevier's own catalogue, which is precisely the coverage hazard
Stage 6 exists to guard against (see :mod:`prismabib.fulltext.resolve`'s
module docstring). This module's only job is one HTTP call and one error
taxonomy; the anti-bias policy -- what a 403 means for the resolver chain --
lives one layer up, in :class:`~prismabib.fulltext.resolve.ScienceDirectResolver`.

Deliberately mirrors :mod:`prismabib.sources.scopus` rather than inventing a
second client shape: the same injectable
:class:`~prismabib.sources.ratelimit.RateLimiter` and
:class:`~prismabib.sources.cache.HttpCache`, the same ``tenacity`` backoff
policy (retry 429/5xx, never 401/403), and the same
:func:`~prismabib.sources.scopus.register_secret_for_redaction` call so the
Elsevier key never reaches a log line -- reused directly rather than
reimplemented, since it is already tested at
``tests/unit/sources/test_ratelimit.py`` and
``tests/integration/sources/test_scopus.py``.
"""

from __future__ import annotations

from typing import Self

import httpx
import structlog
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from prismabib.config import Settings
from prismabib.errors import (
    AuthError,
    ConfigError,
    EntitlementError,
    RateLimitError,
    SourceError,
    UpstreamError,
)
from prismabib.sources.cache import HttpCache
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.scopus import register_secret_for_redaction

logger = structlog.get_logger(__name__)


class ArticleNotFoundError(SourceError):
    """ScienceDirect has no article at the requested DOI (HTTP 404).

    Not an entitlement question (:class:`~prismabib.errors.EntitlementError`
    is that): a 404 here means the DOI is not in Elsevier's ScienceDirect
    catalogue at all -- most commonly because the paper was never published
    by Elsevier -- which is exactly what
    :class:`~prismabib.fulltext.resolve.ScienceDirectResolver` needs to tell
    apart from "we are not subscribed to this paper" (ADR 0019's three-valued
    ``entitled`` column).
    """


class ScienceDirectClient:
    """A client for Elsevier's Article Retrieval API, addressed by DOI.

    Args:
        settings: The environment configuration providing
            ``ELSEVIER_SD_API_KEY``. Defaults to ``Settings()`` when
            omitted.
        http_client: An injectable ``httpx.Client``, primarily for tests.
            A client is constructed and owned (and later closed) internally
            when omitted.
        rate_limiter: An injectable
            :class:`~prismabib.sources.ratelimit.RateLimiter`.
        cache: An injectable :class:`~prismabib.sources.cache.HttpCache`.
            ``None`` (the default) disables caching entirely.
        timeout: The ``httpx`` request timeout in seconds, used only when
            this client constructs its own ``http_client``.

    Raises:
        ConfigError: If ``settings`` is omitted and the environment cannot
            be loaded (raised by :class:`~prismabib.config.Settings`
            itself), or if ``ELSEVIER_SD_API_KEY`` is not set -- unlike
            Scopus's key, this one is optional on :class:`Settings` (most
            commands never need it), so the requirement is enforced here,
            at the one place that does.
    """

    #: Article Retrieval, addressed by DOI. Elsevier's other addressing mode
    #: (Scopus EID / PUI) is not used here: a DOI is what
    #: :class:`~prismabib.fulltext.resolve.ScienceDirectResolver` already
    #: has on every ``records.doi``-bearing row, and it is also what
    #: :mod:`prismabib.publishers` keys its registrant-prefix table on, so
    #: the two never need to agree via a second identifier translation.
    ARTICLE_ENDPOINT_TEMPLATE = "https://api.elsevier.com/content/article/doi/{doi}"
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
        if self._settings.elsevier_sd_api_key is None:
            raise ConfigError(
                "ELSEVIER_SD_API_KEY is not set. ScienceDirect Article Retrieval is a "
                "different Elsevier entitlement from Scopus Search -- set "
                "ELSEVIER_SD_API_KEY in your .env (it may be the same key value as "
                "SCOPUS_API_KEY, with different entitlements attached to it)."
            )
        self._http = http_client if http_client is not None else httpx.Client(timeout=timeout)
        self._owns_http = http_client is None
        self._rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter()
        self._cache = cache

        register_secret_for_redaction(self._settings.elsevier_sd_api_key.get_secret_value())

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

    def article_retrieval_xml(self, doi: str) -> bytes:
        """Fetch the FULL-view Article Retrieval XML for one article, by DOI.

        Args:
            doi: The bare DOI (``10.xxxx/...``), not URL-wrapped.

        Returns:
            The raw XML response body, exactly as Elsevier returned it --
            parsing is :mod:`prismabib.fulltext.extract`'s job, not this
            client's.

        Raises:
            AuthError: On HTTP 401 (bad key); never retried.
            EntitlementError: On HTTP 403 -- the key is valid but not
                entitled to this article. Never retried, never silently
                degraded: the resolver chain's caller decides what a
                refusal means (ADR 0019 hard rule 1), this client only
                reports it.
            ArticleNotFoundError: On HTTP 404 -- this DOI is not in
                ScienceDirect's catalogue. Never retried: retrying cannot
                make an absent article appear.
            RateLimitError: On HTTP 429 exhausting the retry budget.
            UpstreamError: On HTTP 5xx exhausting the retry budget, or any
                other unexpected non-2xx status.
        """
        url = self.ARTICLE_ENDPOINT_TEMPLATE.format(doi=doi)
        response = self._request_with_retry(url)
        return response.content

    def _request_with_retry(self, url: str) -> httpx.Response:
        """Perform one logical request, retrying transient failures with backoff.

        Exponential backoff with jitter, up to :data:`MAX_ATTEMPTS` total
        attempts, only for :class:`~prismabib.errors.RateLimitError` and
        :class:`~prismabib.errors.UpstreamError` -- mirrors
        :meth:`prismabib.sources.scopus.ScopusClient._request_with_retry`.
        """
        retryer = Retrying(
            stop=stop_after_attempt(self.MAX_ATTEMPTS),
            wait=wait_random_exponential(multiplier=1, max=30),
            retry=retry_if_exception_type((RateLimitError, UpstreamError)),
            reraise=True,
        )
        response: httpx.Response = retryer(self._do_request, url)
        return response

    def _do_request(self, url: str) -> httpx.Response:
        """Perform exactly one HTTP GET, honouring the cache, rate limiter, and error taxonomy."""
        cached = self._cache.load(url, {"view": "FULL"}) if self._cache is not None else None
        if cached is not None:
            logger.info("sciencedirect.request", endpoint=url, cache="hit")
            return httpx.Response(200, content=cached, request=httpx.Request("GET", url))

        self._rate_limiter.acquire()
        response = self._http.get(url, params={"view": "FULL"}, headers=self._headers())
        self._rate_limiter.observe_headers(response.headers)
        self._raise_for_status(response)
        if self._cache is not None:
            self._cache.store(url, {"view": "FULL"}, response.content)
        logger.info(
            "sciencedirect.request",
            endpoint=url,
            cache="miss",
            remaining_quota=response.headers.get("X-RateLimit-Remaining"),
        )
        return response

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Translate a non-2xx ScienceDirect response into the §3.3 error taxonomy."""
        status = response.status_code
        if 200 <= status < 300:
            return

        endpoint = str(response.request.url)

        if status == 401:
            logger.warning("sciencedirect.request.auth_error", endpoint=endpoint, status=status)
            raise AuthError(f"ScienceDirect rejected the API key (HTTP 401) for {endpoint}.")

        if status == 403:
            logger.warning(
                "sciencedirect.request.entitlement_error", endpoint=endpoint, status=status
            )
            raise EntitlementError(
                f"ScienceDirect denied access (HTTP 403) to {endpoint}: this article is not "
                "entitled under your key.\n\n"
                "This is an access-rights problem, not a bug: Article Retrieval FULL text "
                "is licensed per publication and per institution, so a key that can search "
                "Scopus can still be refused the article's text.\n\n"
                "prismabib records this as an entitlement gap and moves to the next "
                "full-text resolver rather than treating it as an absent paper (ADR 0019); "
                "it will never silently retry at a lesser view."
            )

        if status == 404:
            logger.info("sciencedirect.request.not_found", endpoint=endpoint, status=status)
            raise ArticleNotFoundError(
                f"ScienceDirect has no article at {endpoint} (HTTP 404): this DOI is not in "
                "Elsevier's ScienceDirect catalogue."
            )

        if status == 429:
            logger.warning("sciencedirect.request.rate_limited", endpoint=endpoint, status=status)
            raise RateLimitError(
                f"ScienceDirect rate-limited the request (HTTP 429) for {endpoint}."
            )

        if 500 <= status < 600:
            logger.warning("sciencedirect.request.upstream_error", endpoint=endpoint, status=status)
            raise UpstreamError(f"ScienceDirect returned HTTP {status} for {endpoint}.")

        logger.warning("sciencedirect.request.unexpected_status", endpoint=endpoint, status=status)
        raise UpstreamError(f"ScienceDirect returned an unexpected HTTP {status} for {endpoint}.")

    def _headers(self) -> dict[str, str]:
        """Build the Elsevier authentication headers for every request.

        Raises:
            ConfigError: Never, in practice -- ``__init__`` already refuses
                to construct this client without a key. Re-checked here
                only so mypy can narrow ``elsevier_sd_api_key`` from
                ``SecretStr | None`` to ``SecretStr`` without an ``assert``.
        """
        api_key = self._settings.elsevier_sd_api_key
        if api_key is None:
            raise ConfigError("ELSEVIER_SD_API_KEY is not set.")
        return {
            "X-ELS-APIKey": api_key.get_secret_value(),
            "Accept": "text/xml",
        }


__all__ = ["ArticleNotFoundError", "ScienceDirectClient"]
