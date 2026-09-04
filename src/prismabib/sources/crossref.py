"""The Crossref text-mining (TDM) client (BUILD_PLAN Stage 6, ADR 0020).

Crossref's free, keyless REST API (``GET
https://api.crossref.org/works/{doi}``) exposes publisher-declared
text-mining links under ``message.link[]`` -- each carrying an
``intended-application`` (``"text-mining"`` is the one this module cares
about; Crossref also uses the same field for ``"similarity-checking"`` and
``"unspecified"`` links this module has no business fetching), a
publisher-declared ``content-type`` (never trusted -- see
:func:`~prismabib.sources.unpaywall.looks_like_pdf`), and a ``URL`` that
points directly at the publisher's own host, not Crossref's.

Modelled closely on :mod:`prismabib.sources.unpaywall`, the other no-credential
source in this package: the same constructor shape (injectable
``http_client``/``rate_limiter``/``cache``/``timeout``), the same
:class:`~prismabib.sources.cache.HttpCache`,
:class:`~prismabib.sources.ratelimit.RateLimiter`, ``tenacity`` retry policy,
and User-Agent construction -- reused rather than reimplemented, and
deliberately *not* imported from that module, since ``_USER_AGENT`` there is
private to it.

Two calls, not one, exactly as :class:`~prismabib.sources.unpaywall.UnpaywallClient`
has two: :meth:`CrossrefTdmClient.lookup` asks Crossref for the work's
metadata (including its TDM links), and :meth:`CrossrefTdmClient.fetch_bytes`
then downloads from wherever a chosen TDM link actually points -- a
publisher's own host, never Crossref's.

**Crossref needs no credential.** ``mailto`` is not a key: it is Crossref's
own "polite pool" convention (a real, reachable contact address gets
preferential rate limiting), the same status :class:`Settings.unpaywall_email`
already has for Unpaywall's terms of use. This module reuses that same
setting rather than adding a second one -- ADR 0020 is explicit that no new
config field is added for this resolver -- and works anonymously (the
"public pool", still functional, just less favoured) when it is unset.

**Why a TDM link's 403 is translated to :class:`~prismabib.errors.EntitlementError`,
unlike an Unpaywall OA location's 403.** An Unpaywall OA location is a public
copy with no entitlement concept at all -- a 403 there is a bot filter or a
dead mirror, so :class:`~prismabib.sources.unpaywall.UnpaywallClient.fetch_bytes`
raises the same non-retried, non-entitlement ``UpstreamError`` it raises for a
404. A Crossref TDM link, by contrast, points at the publisher's own
text-mining endpoint (Springer's, Elsevier's, ACM's), which is licensed
per-institution exactly like ScienceDirect's Article Retrieval API -- a 403
there is refused *entitlement*, and
:class:`~prismabib.fulltext.resolve.CrossrefTdmResolver` needs that
distinction to record ``entitled=False`` rather than "not found" (ADR 0019's
three-valued column, ADR 0020's unchanged 403 rule).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Self

import httpx
import structlog
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from prismabib import __version__
from prismabib.config import FullTextSettings, Settings
from prismabib.errors import EntitlementError, RateLimitError, UpstreamError, ValidationError
from prismabib.sources.cache import HttpCache
from prismabib.sources.ratelimit import RateLimiter

logger = structlog.get_logger(__name__)

JsonDict = dict[str, Any]

#: Crossref's own vocabulary for ``message.link[].intended-application``.
#: Only this value names a text-and-data-mining link; Crossref also uses the
#: field for ``"similarity-checking"`` (plagiarism-detection services) and
#: ``"unspecified"``, neither of which this module has any business fetching.
_TDM_INTENDED_APPLICATION: Final = "text-mining"


class _RetryableUpstreamError(UpstreamError):
    """A 5xx from Crossref or a TDM host -- the only outcome retried.

    A distinct subclass, not a status-code check inside the retry predicate,
    matching :class:`prismabib.sources.unpaywall._RetryableUpstreamError`
    exactly and for the identical reason: ``tenacity.retry_if_exception_type``
    matches on type, and a 4xx from a TDM host is a permanent answer to that
    specific request -- retrying it for four more attempts and up to ~2
    minutes of backoff before raising anyway adds only latency.
    """


#: Sent on every request, identifying the client the same way
#: :mod:`prismabib.sources.unpaywall` does for the identical reason: several
#: hosts refuse a bare ``python-httpx/x.y.z`` outright, and TDM links point at
#: arbitrary publisher hosts this module has never talked to before.
#:
#: `__version__`, not `importlib.metadata.version(...)`: the latter raises
#: `PackageNotFoundError` in a source checkout, and this module is imported by
#: `fulltext.resolve` and thence by `cli`, so that would kill the whole CLI at
#: import rather than degrade one header.
_USER_AGENT: Final = f"prismabib/{__version__} (+https://github.com/SergioHuesca/PRISMA-Bib)"


@dataclass(frozen=True)
class TdmLink:
    """One ``text-mining``-intended link from a Crossref work record.

    Attributes:
        url: Where the link points -- a publisher's own host, not Crossref's.
        content_type: Crossref's own *declared* content type for this link
            (e.g. ``"application/pdf"``, ``"text/html"``, ``"unspecified"``).
            Informational only: neither this module nor
            :class:`~prismabib.fulltext.resolve.CrossrefTdmResolver` trusts
            it for acceptance -- Springer's own declared ``application/pdf``
            is usable, but ACM's ``"unspecified"`` says nothing at all, and
            :func:`~prismabib.sources.unpaywall.looks_like_pdf` decides based
            on the bytes actually downloaded and their *own* HTTP response's
            ``Content-Type``, exactly as it already does for Unpaywall.
    """

    url: str
    content_type: str | None


def tdm_links(response: JsonDict) -> tuple[TdmLink, ...]:
    """Every ``text-mining``-intended link in a parsed Crossref work response.

    Args:
        response: The parsed response from :meth:`CrossrefTdmClient.lookup`.

    Returns:
        Every ``message.link[]`` entry whose ``intended-application`` is
        exactly ``"text-mining"``, in the order Crossref returned them.
        Empty when ``message`` or ``message.link`` is missing, malformed, or
        carries no text-mining entry -- Crossref's own measured majority
        case (23 of 29 records on the corpus ADR 0020 measured against).
    """
    message = response.get("message")
    if not isinstance(message, Mapping):
        return ()
    links = message.get("link")
    if not isinstance(links, list):
        return ()

    result: list[TdmLink] = []
    for entry in links:
        if not isinstance(entry, Mapping):
            continue
        if entry.get("intended-application") != _TDM_INTENDED_APPLICATION:
            continue
        url = entry.get("URL")
        if not isinstance(url, str) or not url:
            continue
        content_type = entry.get("content-type")
        result.append(
            TdmLink(url=url, content_type=content_type if isinstance(content_type, str) else None)
        )
    return tuple(result)


class CrossrefTdmClient:
    """A client for Crossref's work-metadata API and the TDM links it names.

    Args:
        settings: The environment configuration. ``unpaywall_email``, if
            set, is sent as Crossref's own ``mailto`` "polite pool" courtesy
            parameter -- see the module docstring for why this reuses that
            field rather than declaring a new one. Defaults to
            ``Settings()`` when omitted.
        http_client: An injectable ``httpx.Client``, primarily for tests.
        rate_limiter: An injectable
            :class:`~prismabib.sources.ratelimit.RateLimiter`.
        cache: An injectable :class:`~prismabib.sources.cache.HttpCache`.
        timeout: The ``httpx`` request timeout in seconds, used only when
            this client constructs its own ``http_client``.

    Unlike :class:`~prismabib.sources.unpaywall.UnpaywallClient` and
    :class:`~prismabib.sources.sciencedirect.ScienceDirectClient`, this
    client never raises :class:`~prismabib.errors.ConfigError`: Crossref
    needs no credential at all, and works anonymously (Crossref's "public
    pool") when no ``mailto`` address is configured.
    """

    LOOKUP_ENDPOINT_TEMPLATE = "https://api.crossref.org/works/{doi}"
    MAX_ATTEMPTS = 5

    def __init__(
        self,
        settings: Settings | FullTextSettings | None = None,
        *,
        http_client: httpx.Client | None = None,
        rate_limiter: RateLimiter | None = None,
        cache: HttpCache | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._settings = settings if settings is not None else Settings()
        self._http = (
            http_client
            if http_client is not None
            else httpx.Client(
                timeout=timeout,
                # Unlike Crossref's own API (never redirects -- see `lookup`'s
                # explicit `follow_redirects=False`), a TDM link's host is an
                # arbitrary publisher, which routinely redirects (http to
                # https, a stable DOI-shaped URL to the actual PDF endpoint).
                # Same discipline and the same cap as
                # `UnpaywallClient`'s client, for the identical reason.
                follow_redirects=True,
                max_redirects=5,
                headers={"User-Agent": _USER_AGENT},
            )
        )
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
        """Ask Crossref for one work's metadata, including its TDM links.

        Args:
            doi: The bare DOI (``10.xxxx/...``), not URL-wrapped.

        Returns:
            The parsed Crossref response (pass to :func:`tdm_links` to read
            its text-mining links), or ``None`` if Crossref has no record for
            this DOI (HTTP 404) -- not an entitlement question, simply
            "Crossref does not know this DOI".

        Raises:
            RateLimitError: On HTTP 429 exhausting the retry budget.
            UpstreamError: On HTTP 5xx exhausting the retry budget, or any
                other unexpected non-2xx status.
            ValidationError: If a 200 response body is not a JSON object.
        """
        url = self.LOOKUP_ENDPOINT_TEMPLATE.format(doi=doi)
        params = self._mailto_params()
        cached = self._cache.load(url, params) if self._cache is not None else None
        if cached is not None:
            logger.info("crossref.lookup", endpoint=url, cache="hit")
            return None if cached == b"" else _parse_json(cached)

        # `follow_redirects=False`: Crossref's own API does not redirect, so a
        # redirect here is untrustworthy, exactly the reasoning
        # `UnpaywallClient.lookup` already applies to its own lookup call.
        response = self._request_with_retry(url, params, follow_redirects=False)
        if response.status_code == 404:
            logger.info("crossref.lookup.not_found", endpoint=url)
            if self._cache is not None:
                self._cache.store(url, params, b"")
            return None

        body = response.content
        if self._cache is not None:
            self._cache.store(url, params, body)
        logger.info("crossref.lookup", endpoint=url, cache="miss")
        return _parse_json(body)

    def fetch_bytes(self, url: str) -> tuple[bytes, str | None]:
        """Download the bytes at one publisher-hosted TDM link.

        Args:
            url: A :class:`TdmLink.url` -- a publisher's own host, never
                Crossref's.

        Returns:
            ``(content, content_type)``: the raw response body and its
            actual ``Content-Type`` header (``None`` if the host sent none).
            The caller (:class:`~prismabib.fulltext.resolve.CrossrefTdmResolver`)
            needs this alongside the bytes to tell a real PDF apart from an
            HTML landing page wearing a TDM label
            (:func:`~prismabib.sources.unpaywall.looks_like_pdf`) --
            Crossref's own declared :attr:`TdmLink.content_type` is never
            trusted for this, since it is the publisher's *claim*, not this
            client's observation.

        Raises:
            EntitlementError: On HTTP 403 -- the publisher's own text-mining
                endpoint refused this request. Propagates untranslated so
                :class:`~prismabib.fulltext.resolve.CrossrefTdmResolver` (and,
                one frame up, :func:`~prismabib.fulltext.resolve.resolve_fulltext`)
                can record ``entitled=False`` and move to the next resolver
                (ADR 0019's unchanged 403 rule, restated by ADR 0020).
            RateLimitError: On HTTP 429 exhausting the retry budget.
            UpstreamError: On HTTP 5xx exhausting the retry budget (retried
                first), on HTTP 404, or any other unexpected non-2xx status.
        """
        response = self._request_with_retry(url, {}, allow_404=False, entitlement_on_403=True)
        return response.content, response.headers.get("content-type")

    def _mailto_params(self) -> dict[str, str]:
        """Build Crossref's ``mailto`` "polite pool" query parameter, if configured.

        Returns:
            ``{"mailto": <address>}`` when ``settings.unpaywall_email`` is
            set, else ``{}`` -- Crossref's public pool works without one.
        """
        email = self._settings.unpaywall_email
        return {"mailto": email} if email else {}

    def _request_with_retry(
        self,
        url: str,
        params: Mapping[str, str],
        *,
        allow_404: bool = True,
        follow_redirects: bool = True,
        entitlement_on_403: bool = False,
    ) -> httpx.Response:
        """Perform one logical request, retrying transient failures with backoff."""
        retryer = Retrying(
            stop=stop_after_attempt(self.MAX_ATTEMPTS),
            wait=wait_random_exponential(multiplier=1, max=30),
            # `_RetryableUpstreamError`, not `EntitlementError`: a 5xx is
            # worth retrying, a 403 (translated to `EntitlementError` when
            # `entitlement_on_403` is set) is a permanent answer to this
            # exact request and must not cost four more attempts.
            retry=retry_if_exception_type((RateLimitError, _RetryableUpstreamError)),
            reraise=True,
        )
        response: httpx.Response = retryer(
            self._do_request, url, params, allow_404, follow_redirects, entitlement_on_403
        )
        return response

    def _do_request(
        self,
        url: str,
        params: Mapping[str, str],
        allow_404: bool,
        follow_redirects: bool,
        entitlement_on_403: bool,
    ) -> httpx.Response:
        """Perform exactly one HTTP GET, honouring the rate limiter and raising on error status."""
        self._rate_limiter.acquire()
        # `or None`, never a bare `{}`: httpx *replaces* a URL's query string
        # with `params`, so an empty mapping silently truncates a TDM URL's own
        # query string -- the same trap `UnpaywallClient._do_request` guards
        # against.
        response = self._http.get(
            url, params=dict(params) or None, follow_redirects=follow_redirects
        )
        self._rate_limiter.observe_headers(response.headers)
        self._raise_for_status(response, allow_404=allow_404, entitlement_on_403=entitlement_on_403)
        return response

    def _raise_for_status(
        self, response: httpx.Response, *, allow_404: bool, entitlement_on_403: bool
    ) -> None:
        """Translate a non-2xx Crossref (or TDM-host) response into the §3.3 taxonomy."""
        status = response.status_code
        if 200 <= status < 300:
            return
        if status == 404 and allow_404:
            return

        endpoint = str(response.request.url)

        if status == 403 and entitlement_on_403:
            logger.warning("crossref.request.entitlement_error", endpoint=endpoint, status=status)
            raise EntitlementError(
                f"the text-mining host denied access (HTTP 403) to {endpoint}: this content "
                "is not entitled under the credentials this request carried (which may be "
                "none -- Crossref TDM links are often gated on a publisher-specific "
                "subscription this client has no way to authenticate against).\n\n"
                "prismabib records this as an entitlement gap and moves to the next "
                "full-text resolver rather than treating it as an absent paper (ADR 0019, "
                "ADR 0020)."
            )

        if status == 429:
            logger.warning("crossref.request.rate_limited", endpoint=endpoint, status=status)
            raise RateLimitError(f"Crossref rate-limited the request (HTTP 429) for {endpoint}.")

        if 500 <= status < 600:
            logger.warning("crossref.request.upstream_error", endpoint=endpoint, status=status)
            raise _RetryableUpstreamError(
                f"Crossref (or the TDM host) returned HTTP {status} for {endpoint}."
            )

        # Everything else -- 401, a plain 403 with `entitlement_on_403=False`
        # (Crossref's own lookup endpoint, which is not an entitlement
        # question), a stray 3xx -- is a permanent answer to *this* request
        # and is deliberately the base `UpstreamError`, not
        # `_RetryableUpstreamError`: retrying cannot change it.
        logger.warning("crossref.request.unexpected_status", endpoint=endpoint, status=status)
        raise UpstreamError(
            f"Crossref (or the TDM host) returned unexpected HTTP {status} for {endpoint}."
        )


def _parse_json(body: bytes) -> JsonDict:
    """Parse a raw Crossref response body, raising a prismabib error on failure.

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
        raise ValidationError(f"Crossref response is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValidationError(
            f"Crossref response is valid JSON but not a JSON object (got {type(parsed).__name__})"
        )
    return parsed


__all__ = ["CrossrefTdmClient", "TdmLink", "tdm_links"]
