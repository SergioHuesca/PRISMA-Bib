"""The Scopus Search / Abstract Retrieval client (BUILD_PLAN §Stage 2, lines 758-800).

Implements every row of the "Scopus specifics" table (lines 758-768):

- ``GET https://api.elsevier.com/content/search/scopus``, always with
  ``view=COMPLETE`` -- never falls back to ``STANDARD`` on a 403; see
  :meth:`ScopusClient.search` and BUILD_PLAN §5 risk 1.
- Cursor pagination (``cursor=*``, then the response's own next cursor)
  from the very first page, not ``start``/``count``, so results are never
  capped at 5,000.
- ``count=25``.
- A header-aware, hard-capped :class:`~prismabib.sources.ratelimit.RateLimiter`
  consulted before every request.
- ``tenacity``-driven exponential backoff with jitter on transient 429s and
  5xx, capped at 5 attempts; 401 and 403 are raised immediately and never
  retried.
- Quota logging after every call.

It also owns the one piece of defence-in-depth BUILD_PLAN §3.4 (line 396)
asks for: redacting the API key **at the structlog processor level**. See
:func:`register_secret_for_redaction`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator, Mapping, MutableMapping
from typing import Any, Self
from urllib.parse import parse_qs, urlsplit

import httpx
import structlog
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from prismabib.config import Settings
from prismabib.errors import (
    AuthError,
    EntitlementError,
    QuotaExceededError,
    RateLimitError,
    SourceError,
    UpstreamError,
    ValidationError,
)
from prismabib.sources.cache import HttpCache
from prismabib.sources.ratelimit import RateLimiter

logger = structlog.get_logger(__name__)

JsonDict = dict[str, Any]

# A 429 whose X-RateLimit-Reset is further out than this is treated as the
# weekly quota (QuotaExceededError, terminal for this run) rather than a
# transient per-second throttle (RateLimitError, retried by tenacity).
# Scopus does not document a header that names *which* quota tripped, so
# this threshold is a deliberate interpretation -- see the module report.
_QUOTA_RESET_THRESHOLD_SECONDS = 3600.0

_REDACTION_MARKER = "***REDACTED***"
_REDACTED_SECRETS: set[str] = set()
_REDACTION_PROCESSOR_INSTALLED = False


def _redact_value(value: Any, secrets: frozenset[str]) -> Any:
    """Recursively replace any occurrence of a registered secret in ``value``.

    Args:
        value: A structlog event-dict value: a string, a nested mapping/
            sequence built from strings, or any other (left untouched) type.
        secrets: The set of secret values to scrub.

    Returns:
        ``value`` with every registered secret substring replaced by
        :data:`_REDACTION_MARKER`, recursing into mappings, lists, and
        tuples; anything else is returned unchanged.
    """
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(secret, _REDACTION_MARKER)
        return value
    if isinstance(value, Mapping):
        return {key: _redact_value(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_value(item, secrets) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item, secrets) for item in value)
    return value


def _redact_secrets_processor(
    _wrapped_logger: Any, _method_name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    """structlog processor: scrub every registered secret from a log event.

    Args:
        _wrapped_logger: Unused; required by structlog's processor
            signature.
        _method_name: Unused; required by structlog's processor signature.
        event_dict: The event as assembled so far.

    Returns:
        ``event_dict`` with registered secret values redacted throughout.
        A no-op copy when no secret has been registered yet.
    """
    if not _REDACTED_SECRETS:
        return event_dict
    secrets = frozenset(_REDACTED_SECRETS)
    return {key: _redact_value(item, secrets) for key, item in event_dict.items()}


def register_secret_for_redaction(secret: str | None) -> None:
    """Ensure ``secret`` is scrubbed from every future structlog log line.

    BUILD_PLAN §3.4 (line 396) requires redaction "at the logger processor
    level, not at call sites": a call site is free to log a full URL,
    header dict, or params mapping without individually remembering to
    scrub it, because a single processor -- spliced in once, ahead of
    every other configured processor -- rewrites every event before it
    reaches a renderer or sink.

    Idempotent and process-global: safe to call once per
    :class:`ScopusClient` constructed (which is exactly what its
    constructor does). The installed processor is a no-op until at least
    one secret has been registered, so importing this module never mutates
    logging behaviour on its own.

    Args:
        secret: The value to redact. ``None`` or an empty string is a
            no-op -- an empty string would otherwise match, and mangle,
            every log line.
    """
    global _REDACTION_PROCESSOR_INSTALLED
    if not secret:
        return
    _REDACTED_SECRETS.add(secret)
    if not _REDACTION_PROCESSOR_INSTALLED:
        current_processors = list(structlog.get_config()["processors"])
        structlog.configure(processors=[_redact_secrets_processor, *current_processors])
        _REDACTION_PROCESSOR_INSTALLED = True


def _parse_json(body: bytes) -> JsonDict:
    """Parse a raw Scopus response body, raising a prismabib error on failure.

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
        raise ValidationError(f"Scopus response is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValidationError(
            f"Scopus response is valid JSON but not a JSON object (got {type(parsed).__name__})"
        )
    return parsed


def _result_count(page: Mapping[str, Any]) -> int | None:
    """Best-effort count of the ``entry`` records in a search page, for logging only.

    Args:
        page: A parsed Scopus search response.

    Returns:
        The number of entries, or ``None`` if the page does not have the
        expected shape -- this must never raise, since it exists purely for
        an observability log line; real shape validation happens in
        :func:`extract_total_results` and :func:`extract_next_cursor`.
    """
    results = page.get("search-results")
    if not isinstance(results, Mapping):
        return None
    entries = results.get("entry")
    return len(entries) if isinstance(entries, list) else None


def extract_total_results(page: Mapping[str, Any]) -> int:
    """Read ``opensearch:totalResults`` from a Scopus search page.

    This is the **only** sanctioned source of the PRISMA "records
    identified" count (BUILD_PLAN S02-AC5) -- never a row count derived
    from iterating pages, which would silently disagree with the server's
    own count if a page were ever dropped or duplicated.

    Args:
        page: A parsed Scopus search response (one page).

    Returns:
        The total number of results the query matched, per the server.

    Raises:
        ValidationError: If ``page`` lacks a ``search-results`` object, or
            ``opensearch:totalResults`` is missing or not an integer
            string.
    """
    results = page.get("search-results")
    if not isinstance(results, Mapping):
        raise ValidationError("Scopus search response is missing the 'search-results' object")
    raw_total = results.get("opensearch:totalResults")
    if raw_total is None:
        raise ValidationError("Scopus search response is missing 'opensearch:totalResults'")
    try:
        return int(raw_total)
    except (TypeError, ValueError) as exc:
        raise ValidationError(
            f"Scopus search response has a non-integer opensearch:totalResults: {raw_total!r}"
        ) from exc


def extract_next_cursor(page: Mapping[str, Any]) -> str | None:
    """Read the next-page cursor from a Scopus search page.

    Prefers ``search-results.cursor["@next"]`` (the documented cursor
    pagination field, BUILD_PLAN line 764); falls back to the ``cursor``
    query parameter of the ``"next"`` entry in ``search-results.link``, in
    case a given response carries a link but no ``cursor`` object.

    Args:
        page: A parsed Scopus search response (one page).

    Returns:
        The next cursor value, or ``None`` if there is no further page.

    Raises:
        ValidationError: If ``page`` lacks a ``search-results`` object.
    """
    results = page.get("search-results")
    if not isinstance(results, Mapping):
        raise ValidationError("Scopus search response is missing the 'search-results' object")

    cursor_obj = results.get("cursor")
    if isinstance(cursor_obj, Mapping):
        next_value = cursor_obj.get("@next")
        if isinstance(next_value, str) and next_value:
            return next_value

    links = results.get("link")
    if isinstance(links, list):
        for link in links:
            if isinstance(link, Mapping) and link.get("@ref") == "next":
                href = link.get("@href")
                if isinstance(href, str):
                    query = parse_qs(urlsplit(href).query)
                    values = query.get("cursor")
                    if values:
                        return values[0]
    return None


def _entitlement_name(response: httpx.Response, view: str | None) -> str:
    """Best-effort description of the entitlement missing from a 403 response.

    Args:
        response: The 403 response.
        view: The ``view`` that was requested.

    Returns:
        The server's own ``service-error.status.statusText`` when present;
        otherwise a fallback naming the requested ``view``.
    """
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, Mapping):
        service_error = body.get("service-error")
        if isinstance(service_error, Mapping):
            status = service_error.get("status")
            if isinstance(status, Mapping):
                text = status.get("statusText")
                if isinstance(text, str) and text:
                    return text
    return f"view={view}" if view else "unknown entitlement"


def _is_quota_exhausted(headers: Mapping[str, str]) -> bool:
    """Distinguish a terminal weekly-quota 429 from a transient rate-limit 429.

    Args:
        headers: The response headers of a 429 response.

    Returns:
        ``True`` if ``X-RateLimit-Remaining`` is zero (or absent-but-zero)
        and ``X-RateLimit-Reset`` is more than
        :data:`_QUOTA_RESET_THRESHOLD_SECONDS` away, on the interpretation
        that a reset that far out cannot be a per-second/per-minute
        throttle and must be the weekly quota; ``False`` otherwise
        (including when the headers are missing or unparseable, so an
        ambiguous 429 defaults to the retryable, less destructive branch).
    """
    remaining_raw = headers.get("X-RateLimit-Remaining")
    reset_raw = headers.get("X-RateLimit-Reset")
    if remaining_raw is None or reset_raw is None:
        return False
    try:
        remaining = int(remaining_raw)
        reset_at = float(reset_raw)
    except ValueError:
        return False
    if remaining > 0:
        return False
    return (reset_at - time.time()) > _QUOTA_RESET_THRESHOLD_SECONDS


class RecordNotFoundError(SourceError):
    """Scopus has no record at the requested identifier (HTTP 404).

    Defined here rather than in :mod:`prismabib.errors`, following
    :class:`~prismabib.capture.writer.SealedRunError`: §3.3 froze that module's
    taxonomy, and this is a condition only this source can report.

    It exists to be **non-retryable**. Every unexpected status previously became
    an :class:`~prismabib.errors.UpstreamError`, which sits in the retry set, so
    a 404 consumed the whole retry budget and then aborted the caller. Over an
    1,800-record enrichment that is close to certain: Scopus withdraws and
    merges records, so an identifier captured in an earlier run can stop
    resolving later. Retrying cannot fix a record that does not exist, and
    aborting a long run because one of them vanished loses the operator hours of
    quota-bound progress for a fact about the index.

    **Raised only for the Abstract Retrieval record endpoint.** A search request
    carries its identifiers in the ``query`` parameter, not in the path, so a
    404 there cannot mean "this record was withdrawn" -- it means the URL itself
    did not resolve (a proxy or gateway in front of Elsevier, a rewritten base
    URL, an outage returning the wrong status). Naming that a
    ``RecordNotFoundError`` would report a cause that does not exist on that
    path, and would silently take ``search()`` out of the retry set, which is
    behaviour this class was never meant to change. A 404 from any other
    endpoint therefore stays an :class:`~prismabib.errors.UpstreamError`,
    retried exactly as before, with a message that names the causes that
    actually apply there.
    """


class ScopusClient:
    """A client for the Scopus Search and Abstract Retrieval APIs.

    Args:
        settings: The environment configuration providing
            ``SCOPUS_API_KEY`` (required) and ``SCOPUS_INSTTOKEN``
            (optional). Defaults to ``Settings()`` (reads the process
            environment / ``.env``) when omitted.
        http_client: An injectable ``httpx.Client``, primarily for tests
            (``respx`` mounts onto a client's transport). A client is
            constructed and owned (and later closed) internally when
            omitted.
        rate_limiter: An injectable
            :class:`~prismabib.sources.ratelimit.RateLimiter`. Defaults to
            one at the BUILD_PLAN line 766 default of 6 requests/second.
        cache: An injectable :class:`~prismabib.sources.cache.HttpCache`.
            ``None`` (the default) disables caching entirely -- every call
            reaches the network.
        timeout: The ``httpx`` request timeout in seconds, used only when
            this client constructs its own ``http_client``.

    Raises:
        ConfigError: If ``settings`` is omitted and the environment is
            missing ``SCOPUS_API_KEY`` (raised by :class:`Settings` itself).
    """

    SEARCH_ENDPOINT = "https://api.elsevier.com/content/search/scopus"
    #: Abstract Retrieval, addressed by **EID**.
    #:
    #: Not ``/scopus_id/{id}``, which is what this was and which never worked
    #: against real Scopus: that path expects the bare numeric Scopus id, while
    #: a prismabib record id is ``scopus:2-s2.0-<digits>`` (BUILD_PLAN §3.2) --
    #: an EID. Sending ``2-s2.0-<digits>`` to ``/scopus_id/`` returns **404**,
    #: indistinguishable from a withdrawn record, so the enrichment appeared to
    #: find nothing for every record of a live corpus.
    #:
    #: Measured on 2026-09-01 against a real key, same record, same call:
    #: ``/scopus_id/2-s2.0-105044913252`` -> 404,
    #: ``/scopus_id/105044913252`` -> 200,
    #: ``/eid/2-s2.0-105044913252`` -> 200.
    #:
    #: The EID path is used rather than stripping the prefix because it takes
    #: exactly what the store holds: one namespace removal, no reformatting of
    #: an identifier into a shape it was not stored in.
    ABSTRACT_ENDPOINT_TEMPLATE = "https://api.elsevier.com/content/abstract/eid/{scopus_id}"

    #: The fixed part of :data:`ABSTRACT_ENDPOINT_TEMPLATE`, i.e. every URL that
    #: addresses **one record by identifier**. Derived from the template rather
    #: than written out again, so the two cannot drift apart; it is what
    #: :meth:`_raise_for_status` tests to decide whether a 404 can mean "that
    #: record is gone" (see :class:`RecordNotFoundError`).
    ABSTRACT_URL_PREFIX = ABSTRACT_ENDPOINT_TEMPLATE.partition("{scopus_id}")[0]
    PAGE_SIZE = 25
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
        self._http = http_client if http_client is not None else httpx.Client(timeout=timeout)
        self._owns_http = http_client is None
        self._rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter()
        self._cache = cache

        register_secret_for_redaction(self._settings.scopus_api_key.get_secret_value())
        if self._settings.scopus_insttoken is not None:
            register_secret_for_redaction(self._settings.scopus_insttoken.get_secret_value())

    def __enter__(self) -> Self:
        """Enter the client as a context manager; returns ``self``."""
        return self

    def __exit__(self, *exc_info: object) -> None:
        """Exit the client as a context manager, closing an owned HTTP client."""
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client, if this instance owns one.

        A no-op when ``http_client`` was injected at construction -- the
        caller that injected it owns its lifecycle.
        """
        if self._owns_http:
            self._http.close()

    def search(
        self, query: str, *, view: str = "COMPLETE", start_cursor: str = "*"
    ) -> Iterator[JsonDict]:
        """Iterate every page of a Scopus Search API query, via cursor pagination.

        Starts at ``cursor=*`` and follows each page's own next cursor
        (BUILD_PLAN line 764), never ``start``/``count`` (which caps at
        5,000 results). Stops when a page reports no next cursor, or when
        the reported next cursor is unchanged from the one just used --
        the latter guards against an infinite loop on a stale/unchanged
        cursor (BUILD_PLAN test `test_search__cursor_unchanged_between_pages`).

        Args:
            query: The Scopus Boolean query string, e.g. as built by
                :mod:`prismabib.query`.
            view: The Scopus response view. **Must** stay ``"COMPLETE"``
                for any real acquisition run -- ``STANDARD`` omits
                ``authkeywords`` and full affiliation data (BUILD_PLAN §5
                risk 1). This client never substitutes a different view on
                its own; a 403 always raises rather than degrading.
            start_cursor: The cursor to begin from. Defaults to ``"*"`` (the
                start of the result set). A resumed capture passes the
                ``@next`` cursor of the last page it durably wrote, so it
                continues from there instead of replaying pages Layer 0
                already holds — BUILD_PLAN line 768 ("resumes without
                re-fetching") and §5 risk 2 ("never re-fetch what Layer 0
                already holds"). Relying on the HTTP cache instead is not
                equivalent: that cache is gitignored and disposable, so on a
                cold cache the replay costs real weekly quota.

        Yields:
            Each page's parsed Scopus search response, in order, exactly
            once.

        Raises:
            AuthError: On HTTP 401 (bad key); never retried.
            EntitlementError: On HTTP 403 for the requested ``view``;
                never retried, never degrades to a lesser view.
            QuotaExceededError: On HTTP 429 where the response headers
                indicate the weekly quota, not a transient throttle, is
                exhausted.
            RateLimitError: On HTTP 429 exhausting the retry budget.
            UpstreamError: On HTTP 5xx exhausting the retry budget, or any
                other unexpected non-2xx status -- including HTTP 404, which
                on this endpoint means the URL did not resolve rather than
                that any record is missing, and is retried as it always was.
            ValidationError: If a page's body is not a well-formed Scopus
                search response.
        """
        cursor = start_cursor
        while True:
            params = {
                "query": query,
                "view": view,
                "count": str(self.PAGE_SIZE),
                "cursor": cursor,
            }
            page = self._get(self.SEARCH_ENDPOINT, params, view=view)
            yield page

            next_cursor = extract_next_cursor(page)
            if next_cursor is None or next_cursor == cursor:
                break
            cursor = next_cursor

    def abstract(self, record_id: str) -> JsonDict:
        """Fetch the full Abstract Retrieval record for one Scopus record.

        Args:
            record_id: The canonical prismabib record id
                (``scopus:2-s2.0-XXXXXXXXXXX``, BUILD_PLAN §3.2) or a bare
                Scopus id (``2-s2.0-XXXXXXXXXXX``) -- the ``scopus:``
                namespace prefix, if present, is stripped before calling
                the API.

        Returns:
            The parsed Abstract Retrieval response.

        Raises:
            AuthError: On HTTP 401; never retried.
            EntitlementError: On HTTP 403; never retried.
            RecordNotFoundError: On HTTP 404 -- Scopus has no record at that
                identifier. Never retried: retrying cannot resurrect a
                withdrawn or merged record, and burning the retry budget on
                one would abort a long enrichment run.
            QuotaExceededError: On HTTP 429 indicating weekly quota
                exhaustion.
            RateLimitError: On HTTP 429 exhausting the retry budget.
            UpstreamError: On HTTP 5xx exhausting the retry budget, or any
                other unexpected non-2xx status.
            ValidationError: If the response body is not well-formed JSON, or
                is HTTP 200 without an ``abstracts-retrieval-response``
                object.
        """
        scopus_id = record_id.removeprefix("scopus:")
        url = self.ABSTRACT_ENDPOINT_TEMPLATE.format(scopus_id=scopus_id)
        response = self._get(url, {"view": "FULL"}, view="FULL")
        # Validate the envelope, exactly as the search path validates a page.
        # Scopus can answer HTTP 200 with a `service-error` body, and without
        # this the caller cannot tell that from a record it genuinely has
        # nothing to say about: `capture_abstracts` would seal a Layer 0
        # manifest reporting the record fetched, and the payload line written
        # for it carries no `coredata`, so it cannot even be keyed back to a
        # record afterwards. Layer 0 is immutable, so that false success is
        # permanent (BUILD_PLAN §1.4).
        if not isinstance(response.get("abstracts-retrieval-response"), Mapping):
            raise ValidationError(
                f"Scopus returned HTTP 200 for {url} without an "
                "'abstracts-retrieval-response' object, so the response describes no "
                f"record. Top-level keys were {sorted(response)!r}."
            )
        return response

    def _get(self, url: str, params: dict[str, str], *, view: str | None) -> JsonDict:
        """Fetch and parse one JSON object, transparently using the cache when present."""
        cached = self._cache.load(url, params) if self._cache is not None else None
        if cached is not None:
            page = _parse_json(cached)
            logger.info(
                "scopus.request",
                endpoint=url,
                cursor=params.get("cursor"),
                result_count=_result_count(page),
                remaining_quota=None,
                cache="hit",
            )
            return page

        response = self._request_with_retry(url, params, view=view)
        body = response.content
        if self._cache is not None:
            self._cache.store(url, params, body)
        page = _parse_json(body)
        logger.info(
            "scopus.request",
            endpoint=url,
            cursor=params.get("cursor"),
            result_count=_result_count(page),
            remaining_quota=response.headers.get("X-RateLimit-Remaining"),
            cache="miss",
        )
        return page

    def _request_with_retry(
        self, url: str, params: dict[str, str], *, view: str | None
    ) -> httpx.Response:
        """Perform one logical request, retrying transient failures with backoff.

        Exponential backoff with jitter, up to :data:`MAX_ATTEMPTS` total
        attempts, only for :class:`RateLimitError` and
        :class:`UpstreamError` -- :class:`AuthError` and
        :class:`EntitlementError` propagate on the first attempt, since
        retrying a bad key or a missing entitlement only wastes quota
        (BUILD_PLAN line 767).
        """
        retryer = Retrying(
            stop=stop_after_attempt(self.MAX_ATTEMPTS),
            wait=wait_random_exponential(multiplier=1, max=30),
            retry=retry_if_exception_type((RateLimitError, UpstreamError)),
            reraise=True,
        )
        response: httpx.Response = retryer(self._do_request, url, params, view)
        return response

    def _do_request(self, url: str, params: dict[str, str], view: str | None) -> httpx.Response:
        """Perform exactly one HTTP GET, honouring the rate limiter and raising on error status."""
        self._rate_limiter.acquire()
        response = self._http.get(url, params=params, headers=self._headers())
        self._rate_limiter.observe_headers(response.headers)
        self._raise_for_status(response, view=view)
        return response

    def _raise_for_status(self, response: httpx.Response, *, view: str | None) -> None:
        """Translate a non-2xx Scopus response into the BUILD_PLAN §3.3 error taxonomy."""
        status = response.status_code
        if 200 <= status < 300:
            return

        endpoint = str(response.request.url)

        if status == 401:
            logger.warning("scopus.request.auth_error", endpoint=endpoint, status=status)
            raise AuthError(
                f"Scopus rejected the API key (HTTP 401) for {endpoint}.\n"
                "\n"
                "What to check, in the order that resolves this most often:\n"
                "  1. SCOPUS_API_KEY is set in your .env and is the key itself, not a\n"
                "     quoted string or a copied URL. Get one at https://dev.elsevier.com.\n"
                "  2. If you set SCOPUS_INSTTOKEN, it must be the institutional token\n"
                "     issued *for that same key*. Scopus reports the mismatch as\n"
                "     'Institution Token is not associated with API Key' -- the most\n"
                "     common cause is pasting the API key into both fields.\n"
                "  3. If you have no institutional token, leave SCOPUS_INSTTOKEN empty\n"
                "     rather than blank-but-present with a placeholder value."
            )

        if status == 403:
            entitlement = _entitlement_name(response, view)
            logger.warning(
                "scopus.request.entitlement_error",
                endpoint=endpoint,
                status=status,
                entitlement=entitlement,
                view=view,
            )
            raise EntitlementError(
                f"Scopus denied access (HTTP 403) to {endpoint} under view={view!r}: "
                f"missing entitlement {entitlement!r}.\n"
                "\n"
                "This is an access-rights problem, not a bug in your query or your key.\n"
                f"view={view!r} carries author keywords and full affiliation data, and a\n"
                "personal or free Scopus API key does not include it -- it is granted to\n"
                "subscribing institutions.\n"
                "\n"
                "What to do:\n"
                "  1. Run from your institution's network, which is often sufficient.\n"
                "  2. Off campus, ask your university library or research-support office\n"
                "     for a Scopus *institutional token* and set SCOPUS_INSTTOKEN in your\n"
                "     .env alongside SCOPUS_API_KEY. Requesting it is a normal, routine\n"
                "     request; librarians handle it regularly.\n"
                "  3. If your institution has no Scopus subscription, prismabib cannot\n"
                "     run against Scopus for you today. See docs/getting-started.md for\n"
                "     the access requirements before investing further time.\n"
                "\n"
                "prismabib will not silently retry at a lesser view. STANDARD omits the\n"
                "keyword and affiliation fields, so a corpus built from it would produce\n"
                "a keyword network and a geography analysis that look complete and are\n"
                "quietly biased -- a wrong number is worse than a refusal here."
            )

        if status == 429:
            if _is_quota_exhausted(response.headers):
                logger.warning("scopus.request.quota_exceeded", endpoint=endpoint, status=status)
                raise QuotaExceededError(
                    f"Scopus reports the weekly quota exhausted (HTTP 429) for {endpoint}; "
                    "this will not recover within a single run's retry budget."
                )
            logger.warning("scopus.request.rate_limited", endpoint=endpoint, status=status)
            raise RateLimitError(
                f"Scopus rate-limited the request (HTTP 429, transient) for {endpoint}."
            )

        if 500 <= status < 600:
            logger.warning("scopus.request.upstream_error", endpoint=endpoint, status=status)
            raise UpstreamError(f"Scopus returned HTTP {status} for {endpoint}.")

        if status == 404:
            if endpoint.startswith(self.ABSTRACT_URL_PREFIX):
                logger.info("scopus.request.not_found", endpoint=endpoint, status=status)
                raise RecordNotFoundError(
                    f"Scopus has no record at {endpoint} (HTTP 404). Scopus withdraws and "
                    "merges records, so an identifier captured in an earlier run can stop "
                    "resolving later; this is a fact about the index, not a transient fault."
                )
            # No identifier appears in this URL's path, so a 404 here cannot be
            # a withdrawn record. It is an UpstreamError -- and therefore still
            # retried -- exactly as it was before RecordNotFoundError existed.
            logger.warning("scopus.request.endpoint_not_found", endpoint=endpoint, status=status)
            raise UpstreamError(
                f"Scopus returned HTTP 404 for {endpoint}, an endpoint that addresses no "
                "individual record, so this is not a withdrawn or merged record.\n"
                "\n"
                "What produces it, in the order worth checking:\n"
                "  1. A proxy, VPN or captive portal answering on Elsevier's behalf --\n"
                "     institutional networks are the common case.\n"
                "  2. A rewritten or outdated api.elsevier.com base URL.\n"
                "  3. An Elsevier-side gateway fault reporting 404 for a route that\n"
                "     normally exists; this one does clear on its own."
            )

        logger.warning("scopus.request.unexpected_status", endpoint=endpoint, status=status)
        raise UpstreamError(f"Scopus returned an unexpected HTTP {status} for {endpoint}.")

    def _headers(self) -> dict[str, str]:
        """Build the Elsevier authentication headers for every request."""
        headers = {
            "X-ELS-APIKey": self._settings.scopus_api_key.get_secret_value(),
            "Accept": "application/json",
        }
        if self._settings.scopus_insttoken is not None:
            headers["X-ELS-Insttoken"] = self._settings.scopus_insttoken.get_secret_value()
        return headers
