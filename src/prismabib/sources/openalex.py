"""The OpenAlex works client (ADR 0028, level 4 of the field-level cascade).

Modelled on :mod:`prismabib.sources.crossref`: same injectable
``http_client``/``rate_limiter``/``cache``, same ``tenacity`` policy (429 and
5xx retried, nothing else), same error taxonomy. 401 and 403 raise
:class:`~prismabib.errors.AuthError` / :class:`~prismabib.errors.EntitlementError`,
except in an explicit ``fallback_opt_in`` run, where ADR 0028 (resolved item 1)
records them as a ``refused`` outcome carrying the HTTP status so the cascade
can continue to the next level.

**The API key** (``Settings.openalex_api_key``, optional) travels in the
``Authorization: Bearer`` request header (ADR 0031 Decision 6), never in the URL
or ``params``, so it is never part of a cache key or an httpx log line. It is
registered for structlog redaction, and a process-wide ``logging.Filter`` on the
``httpx`` logger scrubs it (and its percent-encoded form) as defence in depth.

**Nothing here writes to the store.** :func:`topics_to_keywords` returns the
``openalex-topic`` keyword kind as data; persisting it needs a separate ADR
(ADR 0028, resolved item 5).
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Final, Literal, Self
from urllib.parse import quote, quote_plus

import httpx
import structlog
from tenacity import Retrying, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from prismabib import __version__
from prismabib.config import Settings
from prismabib.errors import (
    AuthError,
    EntitlementError,
    RateLimitError,
    UpstreamError,
    ValidationError,
)
from prismabib.sources.cache import HttpCache
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.scopus import register_secret_for_redaction

logger = structlog.get_logger(__name__)

JsonDict = dict[str, Any]

_USER_AGENT: Final = f"prismabib/{__version__} (+https://github.com/SergioHuesca/PRISMA-Bib)"
# type, primary_location, primary_topic: ADR 0031 note of 2026-10-22, ADR 0032 Decision 2.
_SELECT: Final = (
    "id,doi,title,publication_year,abstract_inverted_index,authorships,topics,"
    "type,primary_location,primary_topic"
)
_DOI_PREFIX: Final = re.compile(r"^https?://(dx\.)?doi\.org/", re.IGNORECASE)
KEYWORD_KIND: Final = "openalex-topic"


class _HttpxKeyRedactionFilter(logging.Filter):
    """Scrub registered API keys from httpx's ``HTTP Request: GET <url>`` INFO lines.

    structlog's redaction processor never sees stdlib records, so this is the
    only guard on that logger (ADR 0031 Decision 6). Process-wide: it holds the
    keys of every client built so far.
    """

    def __init__(self) -> None:
        super().__init__()
        self.keys: set[str] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        """Rewrite the record's message with each key replaced by ``***``; keep the record."""
        message = record.getMessage()
        # Longest first: a short key that is a substring of a longer one must not mangle it.
        # An empty key is skipped: replace("", "***") would garble every line.
        for key in sorted(filter(None, self.keys), key=len, reverse=True):
            message = message.replace(key, "***").replace(quote_plus(key, safe=""), "***")
        record.msg, record.args = message, None
        return True


_HTTPX_FILTER: Final = _HttpxKeyRedactionFilter()


def _guard_httpx_log(key: str) -> None:
    """Register ``key`` with the httpx log filter, attaching it to the logger once."""
    _HTTPX_FILTER.keys.add(key)
    httpx_logger = logging.getLogger("httpx")
    if _HTTPX_FILTER not in httpx_logger.filters:
        httpx_logger.addFilter(_HTTPX_FILTER)


class _RetryableUpstreamError(UpstreamError):
    """A 5xx from OpenAlex -- retried, unlike any other unexpected status."""


@dataclass(frozen=True)
class OpenAlexWork:
    """The subset of an OpenAlex work this project reads (the ``select`` list)."""

    openalex_id: str | None
    doi: str | None
    title: str | None
    publication_year: int | None
    abstract_inverted_index: Mapping[str, Sequence[int]] | None = None
    authorships: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    topics: Sequence[Mapping[str, Any]] = field(default_factory=tuple)


@dataclass(frozen=True)
class NotFound:
    """OpenAlex has no work for this DOI (ADR 0028 outcome ``not_found``)."""


@dataclass(frozen=True)
class Refused:
    """OpenAlex answered 401 or 403 (ADR 0028 outcome ``refused``)."""

    http_status: int


@dataclass(frozen=True)
class FieldOutcome:
    """One field's outcome at this level: ``value``, ``absent``, ``not_found`` or ``refused``."""

    outcome: Literal["value", "absent", "not_found", "refused"]
    value: Any = None
    http_status: int | None = None


@dataclass(frozen=True)
class AuthorFields:
    """Authors and institution affiliations, in OpenAlex's authorship order."""

    authors: tuple[str, ...]
    affiliations: tuple[str, ...]


@dataclass(frozen=True)
class TopicKeyword:
    """One OpenAlex topic, as data for the ``openalex-topic`` keyword kind."""

    name: str
    score: float | None
    kind: str = KEYWORD_KIND


def normalise_title(title: str) -> str:
    """Lowercase, delete punctuation, collapse whitespace (ADR 0028 Decision 4)."""
    kept = "".join(c for c in title.lower() if not unicodedata.category(c).startswith("P"))
    return " ".join(kept.split())


def normalise_doi(doi: str) -> str:
    """Bare, lowercase DOI: OpenAlex returns ``https://doi.org/...`` URLs."""
    return _DOI_PREFIX.sub("", doi.strip()).lower()


def rebuild_abstract(inverted_index: Mapping[str, Sequence[int]] | None) -> str | None:
    """Rebuild an abstract from OpenAlex's ``abstract_inverted_index``.

    Returns:
        The words joined by single spaces in position order, or ``None`` when
        the index is missing or empty (the ``absent`` outcome).
    """
    if not inverted_index:
        return None
    placed = sorted((pos, word) for word, positions in inverted_index.items() for pos in positions)
    return " ".join(word for _, word in placed) or None


def authorships_to_fields(work: OpenAlexWork) -> AuthorFields:
    """Authors and de-duplicated institution names from ``authorships[]``."""
    authors: list[str] = []
    affiliations: dict[str, None] = {}
    for authorship in work.authorships:
        author = authorship.get("author")
        name = author.get("display_name") if isinstance(author, Mapping) else None
        if isinstance(name, str) and name:
            authors.append(name)
        for inst in authorship.get("institutions") or ():
            inst_name = inst.get("display_name") if isinstance(inst, Mapping) else None
            if isinstance(inst_name, str) and inst_name:
                affiliations[inst_name] = None
    return AuthorFields(tuple(authors), tuple(affiliations))


def topics_to_keywords(work: OpenAlexWork) -> tuple[TopicKeyword, ...]:
    """The work's topics as ``openalex-topic`` keywords (data only; nothing is stored)."""
    out: list[TopicKeyword] = []
    for topic in work.topics:
        name = topic.get("display_name")
        if isinstance(name, str) and name:
            score = topic.get("score")
            out.append(TopicKeyword(name, float(score) if isinstance(score, int | float) else None))
    return tuple(out)


def field_outcomes(result: OpenAlexWork | NotFound | Refused) -> dict[str, FieldOutcome]:
    """Map a lookup result to the per-field outcomes of ADR 0028 Decision 1."""
    names = ("abstract", "authors", "affiliations", "keywords")
    if isinstance(result, Refused):
        return {n: FieldOutcome("refused", http_status=result.http_status) for n in names}
    if isinstance(result, NotFound):
        return {n: FieldOutcome("not_found") for n in names}
    people = authorships_to_fields(result)
    values: dict[str, Any] = {
        "abstract": rebuild_abstract(result.abstract_inverted_index),
        "authors": people.authors,
        "affiliations": people.affiliations,
        "keywords": topics_to_keywords(result),
    }
    return {n: FieldOutcome("value", v) if v else FieldOutcome("absent") for n, v in values.items()}


def lookup_outcome(
    works: Iterable[OpenAlexWork],
    *,
    doi: str | None = None,
    title: str | None = None,
    year: int | None = None,
) -> tuple[OpenAlexWork | None, list[str]]:
    """The one place a work is matched to an identifier and classified (ADR 0031 Decision 4).

    Match by normalised ``doi``, or else by exact normalised ``title`` with the
    same ``year`` (ADR 0028 Decision 4); a near miss is not a match.

    Returns:
        ``(work, absent_fields)``. ``work`` is ``None`` for ``not_found``; for a
        ``value`` ``absent_fields`` lists, in order, the empty fields among
        ``abstract``, ``authors``, ``affiliations``, ``keywords``.
    """
    wanted = normalise_title(title) if title is not None else None
    found = next(
        (
            w
            for w in works
            if (doi is not None and w.doi == normalise_doi(doi))
            or (
                wanted is not None
                and w.title
                and w.publication_year == year
                and normalise_title(w.title) == wanted
            )
        ),
        None,
    )
    if found is None:
        return None, []
    return found, [n for n, o in field_outcomes(found).items() if o.outcome == "absent"]


def parse_work(raw: Mapping[str, Any]) -> OpenAlexWork:
    year = raw.get("publication_year")
    doi = raw.get("doi")
    return OpenAlexWork(
        openalex_id=raw.get("id"),
        doi=normalise_doi(doi) if isinstance(doi, str) else None,
        title=raw.get("title"),
        publication_year=year if isinstance(year, int) else None,
        abstract_inverted_index=raw.get("abstract_inverted_index") or None,
        authorships=raw.get("authorships") or (),
        topics=raw.get("topics") or (),
    )


def _parse_results(body: bytes, *, single: bool = False) -> list[OpenAlexWork]:
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"OpenAlex response is not valid JSON: {exc}") from exc
    if single:
        parsed = {"results": [parsed]}
    results = parsed.get("results") if isinstance(parsed, dict) else None
    if not isinstance(results, list):
        raise ValidationError("OpenAlex response has no 'results' list")
    return [parse_work(r) for r in results if isinstance(r, dict)]


class OpenAlexClient:
    """A client for the OpenAlex ``/works`` endpoint.

    Args:
        settings: Environment configuration; ``openalex_api_key`` is optional.
            Defaults to ``Settings()``.
        http_client: An injectable ``httpx.Client``, primarily for tests.
        rate_limiter: An injectable :class:`~prismabib.sources.ratelimit.RateLimiter`.
        cache: An injectable :class:`~prismabib.sources.cache.HttpCache`.
        timeout: The request timeout in seconds, for a self-built client.
    """

    ENDPOINT = "https://api.openalex.org/works"
    MAX_ATTEMPTS = 5
    BATCH_SIZE = 50

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
        self._http = (
            http_client
            if http_client is not None
            else httpx.Client(timeout=timeout, headers={"User-Agent": _USER_AGENT})
        )
        self._owns_http = http_client is None
        self._rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter()
        self._cache = cache
        key = self._settings.openalex_api_key
        if key is not None:
            register_secret_for_redaction(key.get_secret_value())
            _guard_httpx_log(key.get_secret_value())

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

    def lookup_dois(
        self, dois: Iterable[str], *, fallback_opt_in: bool = False
    ) -> dict[str, OpenAlexWork | NotFound | Refused]:
        """Look up DOIs, 50 per request (``filter=doi:a|b|...``).

        Args:
            dois: DOIs; case variants of one DOI share one lookup and result.
            fallback_opt_in: True only inside an opt-in fallback run (ADR 0028
                resolved item 1). Then 401/403 come back as ``Refused``;
                otherwise they raise.

        Returns:
            One entry per input DOI (keyed as given): the work, ``NotFound``
            when OpenAlex has none (absent from the batch, or a 404), or
            ``Refused`` on 401/403 when ``fallback_opt_in``.

        Raises:
            AuthError: On HTTP 401 outside an opt-in run.
            EntitlementError: On HTTP 403 outside an opt-in run.
            RateLimitError: On HTTP 429 exhausting the retry budget.
            UpstreamError: On 5xx exhausting the retry budget, or any other
                unexpected status.
            ValidationError: If a 200 body is not the documented shape.
        """
        inputs = list(dict.fromkeys(dois))
        # Work on one canonical (lowercase) form, so case variants share one result.
        canon = list(dict.fromkeys(normalise_doi(d) for d in inputs))
        found: dict[str, OpenAlexWork | NotFound | Refused] = {}
        # `|` and `,` are filter syntax: such a DOI can never sit inside a batch.
        singles = [d for d in canon if "|" in d or "," in d]
        batchable = [d for d in canon if d not in singles]
        for start in range(0, len(batchable), self.BATCH_SIZE):
            batch = batchable[start : start + self.BATCH_SIZE]
            params = {
                "filter": "doi:" + "|".join(batch),
                "per-page": str(self.BATCH_SIZE),
                "select": _SELECT,
            }
            result = self._get_works(params, fallback_opt_in=fallback_opt_in)
            if result is None:
                # A 404 on the whole batch says nothing about any one DOI.
                singles.extend(batch)
            elif isinstance(result, list):
                found.update({d: lookup_outcome(result, doi=d)[0] or NotFound() for d in batch})
            else:
                found.update(dict.fromkeys(batch, result))
        for doi in singles:
            found[doi] = self._lookup_one(doi, fallback_opt_in=fallback_opt_in)
        return {d: found[normalise_doi(d)] for d in inputs}

    def _lookup_one(self, doi: str, *, fallback_opt_in: bool) -> OpenAlexWork | NotFound | Refused:
        """Look one DOI up by URL path; only a 404 here means ``not_found``."""
        url = f"{self.ENDPOINT}/doi:{quote(normalise_doi(doi), safe='')}"
        result = self._get_works({"select": _SELECT}, url=url, fallback_opt_in=fallback_opt_in)
        if result is None:
            return NotFound()
        return result if isinstance(result, Refused) else result[0]

    def search_title(
        self, title: str, year: int, *, fallback_opt_in: bool = False
    ) -> OpenAlexWork | Refused | None:
        """Keyword-search by title; accept only an exact normalised-title, same-year match.

        Returns:
            The matching work; ``None`` if nothing matches exactly (a near
            miss belongs on a human review list, ADR 0028 Decision 4); or
            ``Refused`` on 401/403 when ``fallback_opt_in``.

        Raises:
            AuthError: On HTTP 401 outside an opt-in run.
            EntitlementError: On HTTP 403 outside an opt-in run.
        """
        params = {
            "search": title,
            "filter": f"publication_year:{year}",
            "per-page": "25",
            "select": _SELECT,
        }
        result = self._get_works(params, fallback_opt_in=fallback_opt_in)
        if isinstance(result, Refused):
            return result
        if result is None:
            return None
        return lookup_outcome(result, title=title, year=year)[0]

    def _get_works(
        self, params: Mapping[str, str], *, fallback_opt_in: bool, url: str | None = None
    ) -> list[OpenAlexWork] | Refused | None:
        """GET works (the key is a header, so never in the cache key); ``None`` means HTTP 404.

        With ``url`` (a single-work path) the body is one work object, returned
        as a one-item list.
        """
        url = url or self.ENDPOINT
        single = url != self.ENDPOINT
        cached = self._cache.load(url, params) if self._cache is not None else None
        if cached is not None:
            logger.info("openalex.works", cache="hit")
            return _parse_results(cached, single=single)

        response = self._request_with_retry(url, params)
        if response.status_code == 404:
            return None
        if response.status_code in (401, 403):
            status = response.status_code
            logger.warning("openalex.works.refused", status=status, opt_in=fallback_opt_in)
            if fallback_opt_in:
                return Refused(status)
            if status == 401:
                raise AuthError("OpenAlex rejected the request (HTTP 401): check OPENALEX_API_KEY.")
            raise EntitlementError("OpenAlex refused access (HTTP 403).")
        results = _parse_results(response.content, single=single)
        # Cache only a body that parsed: a cached proxy/HTML 200 would replay forever.
        if self._cache is not None:
            self._cache.store(url, params, response.content)
        logger.info("openalex.works", cache="miss")
        return results

    def _request_with_retry(self, url: str, params: Mapping[str, str]) -> httpx.Response:
        retryer = Retrying(
            stop=stop_after_attempt(self.MAX_ATTEMPTS),
            wait=wait_random_exponential(multiplier=1, max=30),
            retry=retry_if_exception_type((RateLimitError, _RetryableUpstreamError)),
            reraise=True,
        )
        response: httpx.Response = retryer(self._do_request, url, params)
        return response

    def _do_request(self, url: str, params: Mapping[str, str]) -> httpx.Response:
        self._rate_limiter.acquire()
        key = self._settings.openalex_api_key
        headers = {"Authorization": f"Bearer {key.get_secret_value()}"} if key is not None else {}
        response = self._http.get(url, params=dict(params), headers=headers)
        self._rate_limiter.observe_headers(response.headers)
        status = response.status_code
        if status == 429:
            logger.warning("openalex.request.rate_limited", status=status)
            raise RateLimitError("OpenAlex rate-limited the request (HTTP 429).")
        if 500 <= status < 600:
            logger.warning("openalex.request.upstream_error", status=status)
            raise _RetryableUpstreamError(f"OpenAlex returned HTTP {status}.")
        if not (200 <= status < 300 or status in (401, 403, 404)):
            logger.warning("openalex.request.unexpected_status", status=status)
            raise UpstreamError(f"OpenAlex returned unexpected HTTP {status}.")
        return response


__all__ = [
    "AuthorFields",
    "FieldOutcome",
    "NotFound",
    "OpenAlexClient",
    "OpenAlexWork",
    "Refused",
    "TopicKeyword",
    "authorships_to_fields",
    "field_outcomes",
    "lookup_outcome",
    "normalise_doi",
    "normalise_title",
    "parse_work",
    "rebuild_abstract",
    "topics_to_keywords",
]
