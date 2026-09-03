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
from typing import Any, Final, Self

import httpx
import structlog
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from prismabib import __version__
from prismabib.config import FullTextSettings, Settings
from prismabib.errors import ConfigError, RateLimitError, UpstreamError, ValidationError
from prismabib.sources.cache import HttpCache
from prismabib.sources.ratelimit import RateLimiter

logger = structlog.get_logger(__name__)

JsonDict = dict[str, Any]


class _RetryableUpstreamError(UpstreamError):
    """A 5xx from Unpaywall or the OA host it redirected to -- the only outcome retried.

    A distinct subclass, not a status-code check inside the retry predicate,
    because :func:`tenacity.retry_if_exception_type` matches on type alone. Before
    this split, *every* unexpected non-2xx/404 status -- 401, 403, a
    Cloudflare-fronted OA host answering 403 to a scripted download -- was raised as
    plain :class:`~prismabib.errors.UpstreamError`, which was also the retried type:
    a transient-looking 403 was retried five times with exponential backoff and then
    raised anyway, for no benefit and five times the latency. Only a 5xx is
    transient in the sense retrying helps with; a 4xx from the OA host is a
    permanent answer to this specific request.
    """


#: Sent on every request. Identifying the client is ordinary good manners for an
#: unauthenticated API, and several open-access hosts refuse `python-httpx/x.y.z`
#: outright -- the same 35-record run drew three 403s and a 418 ("I'm a teapot",
#: which some hosts use as a bot block) from OA repositories.
#:
#: Deliberately carries no email. Unpaywall already receives one as a query
#: parameter because its terms require it; the OA hosts this client then downloads
#: from are third parties that never asked, and a User-Agent is broadcast to every
#: one of them.
#: `__version__`, not `importlib.metadata.version(...)`: the latter raises
#: `PackageNotFoundError` in a source checkout, and because this module is
#: imported by `fulltext.resolve` and thence by `cli`, that would kill the whole
#: CLI at import rather than degrade one header. `prismabib.__init__` already
#: falls back to "0+unknown" for exactly this case.
_USER_AGENT: Final = f"prismabib/{__version__} (+https://github.com/SergioHuesca/PRISMA-Bib)"


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
        settings: Settings | FullTextSettings | None = None,
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
        self._http = (
            http_client
            if http_client is not None
            else httpx.Client(
                timeout=timeout,
                # `httpx` defaults `follow_redirects` to False, unlike `requests`.
                # Unlike every other client in this package, this one fetches from
                # *arbitrary* hosts -- whatever Unpaywall names as the open-access
                # location -- and repositories redirect as a matter of course
                # (DSpace to a bitstream, a DOI to a publisher, http to https).
                # Without this, a redirect surfaced as "unexpected HTTP 302" and the
                # record was recorded as a mid-chain failure. Measured on a real
                # 35-record run: 6 of 10 failures were 301/302.
                follow_redirects=True,
                # Five covers the cases this exists for (DSpace to a bitstream,
                # http to https, a DOI to a publisher). httpx's default of 20
                # is a wider capability than any of them needs, granted to
                # hosts named by a third-party API.
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

        response = self._request_with_retry(url, params, follow_redirects=False)
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

    def fetch_bytes(self, url: str) -> tuple[bytes, str | None]:
        """Download the bytes at an open-access location URL.

        Args:
            url: A URL taken from an Unpaywall ``best_oa_location`` (e.g.
                ``url_for_pdf``). Not necessarily Unpaywall's own host --
                this is wherever the OA copy actually lives.

        Returns:
            ``(content, content_type)``: the raw response body and its
            ``Content-Type`` header (``None`` if the host did not send one).
            The caller (:class:`~prismabib.fulltext.resolve.OpenAccessResolver`)
            needs the content type, alongside the bytes themselves, to tell an
            actual PDF apart from an HTML landing page that ``best_oa_pdf_url``
            fell back to (:func:`~prismabib.sources.unpaywall.looks_like_pdf`)
            -- a distinction this client is best placed to hand back, since it
            is the only place that ever sees the response headers.

        Raises:
            RateLimitError: On HTTP 429 exhausting the retry budget.
            UpstreamError: On HTTP 5xx exhausting the retry budget (retried
                first), on HTTP 404 (the location Unpaywall pointed to no
                longer resolves), or any other unexpected non-2xx status
                (never retried for a non-5xx status -- see
                :class:`_RetryableUpstreamError`).
        """
        response = self._request_with_retry(url, {}, allow_404=False)
        return response.content, response.headers.get("content-type")

    def _request_with_retry(
        self,
        url: str,
        params: Mapping[str, str],
        *,
        allow_404: bool = True,
        follow_redirects: bool = True,
    ) -> httpx.Response:
        """Perform one logical request, retrying transient failures with backoff."""
        retryer = Retrying(
            stop=stop_after_attempt(self.MAX_ATTEMPTS),
            wait=wait_random_exponential(multiplier=1, max=30),
            # `_RetryableUpstreamError`, not the base `UpstreamError`: a 5xx is worth
            # retrying, a 4xx the "unexpected status" branch below raises as plain
            # `UpstreamError` is a permanent answer to this exact request and must not
            # cost four more attempts and up to ~2 minutes of backoff before surfacing.
            retry=retry_if_exception_type((RateLimitError, _RetryableUpstreamError)),
            reraise=True,
        )
        response: httpx.Response = retryer(
            self._do_request, url, params, allow_404, follow_redirects
        )
        return response

    def _do_request(
        self,
        url: str,
        params: Mapping[str, str],
        allow_404: bool,
        follow_redirects: bool = True,
    ) -> httpx.Response:
        """Perform exactly one HTTP GET, honouring the rate limiter and raising on error status."""
        self._rate_limiter.acquire()
        # `or None`, never a bare `{}`: httpx *replaces* a URL's query string with
        # `params`, so an empty mapping silently truncates it.
        # `?sequence=1&isAllowed=y` is the canonical DSpace bitstream form --
        # exactly the hosts this client downloads from -- and stripping it yields a
        # 404 whose logged URL is the truncated one, so nothing in the output
        # reveals what happened.
        response = self._http.get(
            url, params=dict(params) or None, follow_redirects=follow_redirects
        )
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
            raise _RetryableUpstreamError(
                f"Unpaywall (or the OA host) returned HTTP {status} for {endpoint}."
            )

        # Everything else -- 401, 403 (a Cloudflare-fronted OA host refusing a
        # scripted download is the case that motivated this split), a stray 3xx --
        # is a permanent answer to *this* request and is deliberately the base
        # `UpstreamError`, not `_RetryableUpstreamError`: retrying cannot change a
        # 403 into a 200, so paying the retry budget for it before giving up
        # anyway only adds latency.
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

    .. deprecated::
        Superseded by :func:`oa_pdf_candidates`, which is what
        :class:`~prismabib.fulltext.resolve.OpenAccessResolver` now calls.
        Reading only ``best_oa_location`` and falling back to its landing-page
        ``url`` is precisely what reported nine records as having no full text
        when Unpaywall knew of an open-access copy. Kept for callers outside
        this package; nothing in ``src/`` uses it.

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


#: How many candidate locations one record may be tried at. Unpaywall usually
#: reports one to three; the cap exists so a pathological response cannot turn one
#: record into dozens of downloads.
_MAX_OA_CANDIDATES = 5


def oa_pdf_candidates(response: JsonDict) -> tuple[str, ...]:
    """Every URL worth trying for a PDF, best first.

    Args:
        response: The parsed response from :meth:`UnpaywallClient.lookup`.

    Returns:
        Up to :data:`_MAX_OA_CANDIDATES` URLs, de-duplicated, ordered:
        every location's ``url_for_pdf`` first (a direct PDF link), then
        every location's generic ``url`` (usually a landing page). Empty
        when Unpaywall reports no OA location at all.

    :func:`best_oa_pdf_url` looks only at ``best_oa_location`` and falls
    straight back to its landing-page ``url``. Measured on a real 35-record
    corpus, that produced nine ``not_a_pdf`` misses -- records where Unpaywall
    *did* know of an open-access copy, and the one location asked happened to
    offer only HTML. Unpaywall returns every location it knows in
    ``oa_locations``; a repository mirror frequently carries a direct
    ``url_for_pdf`` where the publisher's own "best" location does not.

    Trying a direct PDF link at *any* location before any landing page is the
    ordering that matters: it is what turns "Unpaywall says this is open
    access" into an actual file, rather than into a miss reported as though no
    open-access copy existed.
    """
    locations: list[Mapping[str, Any]] = []
    best = response.get("best_oa_location")
    if isinstance(best, Mapping):
        locations.append(best)
    others = response.get("oa_locations")
    if isinstance(others, list):
        locations.extend(item for item in others if isinstance(item, Mapping))

    candidates: list[str] = []
    for key in ("url_for_pdf", "url"):
        for location in locations:
            value = location.get(key)
            if isinstance(value, str) and value and value not in candidates:
                candidates.append(value)
    return tuple(candidates[:_MAX_OA_CANDIDATES])


#: A PDF's magic bytes, per the PDF spec (ISO 32000-1 §7.5.2): the header
#: ``%PDF-1.N`` must appear somewhere in the first 1024 bytes of the file (some
#: producers prepend a short binary comment or BOM before it), so this module does
#: not require it at byte offset 0.
_PDF_MAGIC = b"%PDF-"
_PDF_SNIFF_WINDOW = 1024


def looks_like_pdf(content: bytes, content_type: str | None) -> bool:
    """Whether a downloaded body is actually a PDF, not an HTML landing page.

    :func:`best_oa_pdf_url` falls back from ``url_for_pdf`` to the generic
    ``url`` when Unpaywall's best OA location carries no direct PDF link --
    and that ``url`` is routinely a publisher landing page, not a PDF. Before
    this check existed, :class:`~prismabib.fulltext.resolve.OpenAccessResolver`
    wrote whatever bytes came back straight to disk as ``media_type="pdf"``,
    ``entitled=True``: ``pdfplumber`` then failed to extract any section from
    the HTML (an exception this codebase already swallows, by design, as "an
    unparseable file is a fact about that file"), so the record silently got
    zero sections *and* was counted resolved forever -- never retried, and
    counted toward coverage in a report whose entire point is not to overstate
    it.

    Args:
        content: The downloaded response body.
        content_type: The response's ``Content-Type`` header, or ``None`` when
            the host sent none (some OA hosts, and every local manual-drop
            file, have no HTTP response to draw one from at all).

    Returns:
        ``True`` iff :data:`_PDF_MAGIC` appears within the first
        :data:`_PDF_SNIFF_WINDOW` bytes of ``content`` **and** the content
        type, if any, is not an explicitly textual one.

    The magic bytes decide; the content type may only veto, and only when it
    positively claims text. An earlier version required the type to mention
    ``"pdf"`` and rejected everything else before ever looking at the bytes --
    which rejected real PDFs served as ``application/octet-stream``,
    ``binary/octet-stream`` or ``application/force-download``, exactly how
    DSpace and EPrints bitstream endpoints serve them.

    That failure is worse than the landing-page bug this function exists to
    catch. A landing page accepted as a PDF is visible: zero sections extract
    and the text is obviously not there. Obtainable open-access text rejected
    is invisible, and it is *misreported* -- the record falls through the
    chain and is counted "Not found" in the coverage table, understating what
    was available in the one artefact whose job is to not misstate coverage.
    """
    if content_type is not None:
        normalised = content_type.split(";", 1)[0].strip().casefold()
        if normalised.startswith("text/") or "html" in normalised or "xml" in normalised:
            return False
    return _PDF_MAGIC in content[:_PDF_SNIFF_WINDOW]


__all__ = ["UnpaywallClient", "best_oa_pdf_url", "looks_like_pdf", "oa_pdf_candidates"]
