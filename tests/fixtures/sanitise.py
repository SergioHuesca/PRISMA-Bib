"""Sanitiser for recorded Scopus responses (BUILD_PLAN §3.7.5, lines 533-537).

Cassettes under ``tests/fixtures/cassettes/`` are never the raw bytes a real
API call returned. They are the output of :func:`sanitise_page` (for a
Scopus Search response body) run once, by hand, against a real recording --
see ``tests/fixtures/README.md`` for the exact procedure. This module is
what performs that transformation, and is deliberately import-only: it is
never given network access and never reads ``.env`` itself, so it cannot
accidentally embed a real secret no matter how it is invoked.

What is stripped, what is regenerated, and what is left alone (BUILD_PLAN
line 534, transcribed exactly):

- **Stripped**: the API key and institution token, wherever they appear --
  the ``X-ELS-APIKey``/``X-ELS-Insttoken`` request headers
  (:func:`sanitise_headers`) and the ``apiKey``/``insttoken`` query-string
  parameters of a request URL (:func:`sanitise_query_string`).
- **Regenerated** (BUILD_PLAN's exact list: titles, abstracts, author names,
  affiliations):
    - ``dc:title`` and ``dc:description`` are passed through
      :func:`_transliterate`, which walks the original string
      character-by-character and substitutes a random character of the
      *same class* (upper-case letter, lower-case letter, digit) for every
      letter/digit, leaving whitespace and punctuation untouched. This is
      what "comparable length and character class" (line 534) means taken
      literally: the output is exactly as long as the input and has an
      identical case/digit/punctuation skeleton, so a contract test
      asserting "there is a title-shaped string here" keeps working, while
      the actual prose -- the licensed part -- is gone.
    - ``dc:creator`` and every element of an ``author`` list (or a lone
      ``author`` mapping) get a synthetic name drawn from
      :data:`_GIVEN_NAMES`/:data:`_SURNAMES`, deterministically, from the
      seeded :class:`random.Random` passed in.
    - Every element of an ``affiliation`` list (or a lone ``affiliation``
      mapping) gets a synthetic institution/city/country from
      :data:`_AFFIL_NAMES`/:data:`_CITIES`/:data:`_COUNTRIES`.
- **Left alone, deliberately**: everything else -- record identifiers
  (``dc:identifier``, ``eid``, ``prism:doi``, ``pii``, ``afid``, ``authid``,
  ``author-url``, ``affiliation-url``), dates, counts, subtype codes,
  ``authkeywords``, funding fields, and the whole ``link``/``cursor``
  pagination envelope. BUILD_PLAN line 534 names exactly four things to
  regenerate; inventing a fifth (e.g. scrambling ``eid``) would be scope the
  spec did not ask for, and would risk breaking the very cross-references
  (``afid`` on an author matching ``afid`` on an affiliation) a contract
  test might reasonably rely on. **Structure is what survives**: field
  presence and absence per entry, scalar-vs-list shape, and the pagination
  envelope are copied through unchanged -- see the module docstring's
  companion note in ``tests/fixtures/README.md`` about the specific 22/25
  ``authkeywords`` split preserved in ``complete-page-0000.json``.
"""

from __future__ import annotations

import copy
import random
import string
from collections.abc import Iterable, Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "***REDACTED***"

#: Header names (matched case-insensitively) whose value is always fully
#: replaced by :data:`REDACTED`, never merely substring-scrubbed -- an API
#: key is the entire header value, so there is nothing left to preserve.
_SECRET_HEADER_NAMES = {"x-els-apikey", "x-els-insttoken", "authorization"}

#: Query-string parameter names (matched case-insensitively) treated the
#: same way as the header names above.
_SECRET_QUERY_PARAM_NAMES = {"apikey", "insttoken"}

_GIVEN_NAMES = (
    "Alex", "Jordan", "Morgan", "Taylor", "Casey", "Riley", "Sam", "Jamie",
    "Drew", "Avery", "Priya", "Wei", "Fatima", "Hiro", "Lena", "Mateo",
    "Ingrid", "Kwame", "Noor", "Soo-Ah",
)  # fmt: skip

_SURNAMES = (
    "Nguyen", "Kowalski", "Fernandez", "Okafor", "Ivanov", "Tanaka", "Silva",
    "Larsen", "Haddad", "Park", "Dubois", "Costa", "Meyer", "Kim", "Rossi",
    "Andersen", "Osei", "Petrov", "Yamada", "Cohen",
)  # fmt: skip

_AFFIL_NAMES = (
    "Northfield Institute of Technology",
    "Meridian State University",
    "Lakeside University of Engineering",
    "Harborview Polytechnic",
    "Ashgrove National Laboratory",
    "Redcliff Institute of Science",
    "Summerdale University",
    "Ironwood College of Engineering",
)

_CITIES = (
    "Rivertown", "Lakeside", "Fairview", "Norwood", "Elmsworth", "Brightwood",
    "Millbrook", "Stonebridge",
)  # fmt: skip

_COUNTRIES = (
    "Norway", "Chile", "Vietnam", "Poland", "Kenya", "Portugal", "Malaysia",
    "Uruguay", "Estonia", "Ghana",
)  # fmt: skip


def _redact_string(value: str, secrets: Iterable[str]) -> str:
    """Replace every occurrence of any ``secrets`` member in ``value``.

    Args:
        value: The string to scrub.
        secrets: The secret values to redact, if present.

    Returns:
        ``value`` with each non-empty secret substring replaced by
        :data:`REDACTED`.
    """
    result = value
    for secret in secrets:
        if secret:
            result = result.replace(secret, REDACTED)
    return result


def sanitise_headers(headers: Mapping[str, str], secrets: Iterable[str] = ()) -> dict[str, str]:
    """Redact API keys/tokens from a header mapping.

    Args:
        headers: Request (or, defensively, response) headers as recorded.
        secrets: Additional secret values to scrub from any header value,
            beyond the always-redacted names in :data:`_SECRET_HEADER_NAMES`.

    Returns:
        A new mapping: every header named in :data:`_SECRET_HEADER_NAMES`
        (case-insensitively) has its value fully replaced by
        :data:`REDACTED`; every other header has any occurrence of a
        ``secrets`` value scrubbed from it in place.
    """
    secrets = tuple(secrets)
    sanitised: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in _SECRET_HEADER_NAMES:
            sanitised[key] = REDACTED
        else:
            sanitised[key] = _redact_string(value, secrets)
    return sanitised


def sanitise_query_string(url: str, secrets: Iterable[str] = ()) -> str:
    """Redact API keys/tokens from a URL's query string.

    Args:
        url: A full or relative URL, with or without a query string.
        secrets: Additional secret values to scrub from any parameter value.

    Returns:
        ``url`` with every ``apiKey``/``insttoken`` query parameter
        (case-insensitively) replaced by :data:`REDACTED`, and any
        occurrence of a ``secrets`` value scrubbed from every other
        parameter's value.
    """
    secrets = tuple(secrets)
    split = urlsplit(url)
    pairs = parse_qsl(split.query, keep_blank_values=True)
    redacted_pairs = [
        (
            key,
            REDACTED
            if key.lower() in _SECRET_QUERY_PARAM_NAMES
            else _redact_string(value, secrets),
        )
        for key, value in pairs
    ]
    new_query = urlencode(redacted_pairs)
    return urlunsplit((split.scheme, split.netloc, split.path, new_query, split.fragment))


def _transliterate(text: str, rng: random.Random) -> str:
    """Regenerate ``text`` with the same length and per-character class.

    Args:
        text: The original (licensed) prose -- a title or abstract.
        rng: A seeded :class:`random.Random`, for determinism.

    Returns:
        A string of identical length: every upper-case letter becomes a
        (different) random upper-case letter, every lower-case letter a
        random lower-case letter, every digit a random digit, and every
        other character (space, punctuation, non-ASCII marks) is copied
        through unchanged. See the module docstring for why this satisfies
        BUILD_PLAN line 534's "comparable length and character class".
    """
    output: list[str] = []
    for char in text:
        if char.isupper():
            output.append(rng.choice(string.ascii_uppercase))
        elif char.islower():
            output.append(rng.choice(string.ascii_lowercase))
        elif char.isdigit():
            output.append(rng.choice(string.digits))
        else:
            output.append(char)
    return "".join(output)


def _synthetic_person(rng: random.Random) -> tuple[str, str]:
    """Draw one synthetic ``(given_name, surname)`` pair."""
    return rng.choice(_GIVEN_NAMES), rng.choice(_SURNAMES)


def _sanitise_author(author: Mapping[str, Any], rng: random.Random) -> dict[str, Any]:
    """Replace one ``author`` entry's name fields with a synthetic identity.

    Args:
        author: One element of a Scopus entry's ``author`` list (or the
            entry's lone scalar ``author`` mapping).
        rng: A seeded :class:`random.Random`, for determinism.

    Returns:
        A shallow-modified copy of ``author``: only the keys already
        present are touched (so a record missing ``orcid``, for instance,
        still has no ``orcid`` afterwards -- field presence/absence is
        preserved), and every key not named below (``authid``, ``afid``,
        ``author-url``, ``@seq``, ``@_fa``, ...) is copied through
        unchanged.
    """
    result = dict(author)
    given, surname = _synthetic_person(rng)
    if "given-name" in result:
        result["given-name"] = given
    if "surname" in result:
        result["surname"] = surname
    if "initials" in result:
        result["initials"] = f"{given[0]}."
    if "authname" in result:
        result["authname"] = f"{surname} {given[0]}."
    return result


def _sanitise_affiliation(affiliation: Mapping[str, Any], rng: random.Random) -> dict[str, Any]:
    """Replace one ``affiliation`` entry's institution fields with synthetic values.

    Args:
        affiliation: One element of a Scopus entry's ``affiliation`` list
            (or the entry's lone scalar ``affiliation`` mapping).
        rng: A seeded :class:`random.Random`, for determinism.

    Returns:
        A shallow-modified copy of ``affiliation``, following the same
        "only touch keys already present" rule as :func:`_sanitise_author`.
    """
    result = dict(affiliation)
    if "affilname" in result:
        result["affilname"] = rng.choice(_AFFIL_NAMES)
    if "affiliation-city" in result:
        result["affiliation-city"] = rng.choice(_CITIES)
    if "affiliation-country" in result:
        result["affiliation-country"] = rng.choice(_COUNTRIES)
    return result


def _sanitise_entry(entry: Mapping[str, Any], rng: random.Random) -> dict[str, Any]:
    """Sanitise one ``search-results.entry`` element in place-of-a-copy.

    Args:
        entry: One Scopus Search API entry, exactly as recorded.
        rng: A seeded :class:`random.Random`, for determinism.

    Returns:
        A deep copy of ``entry`` with ``dc:title``/``dc:description``
        transliterated, ``dc:creator``/``author``/``affiliation``
        replaced with synthetic identities, and every other key --
        including whether each of those keys is present at all --
        untouched.
    """
    result: dict[str, Any] = copy.deepcopy(dict(entry))

    title = result.get("dc:title")
    if isinstance(title, str):
        result["dc:title"] = _transliterate(title, rng)

    description = result.get("dc:description")
    if isinstance(description, str):
        result["dc:description"] = _transliterate(description, rng)

    if isinstance(result.get("dc:creator"), str):
        _given, surname = _synthetic_person(rng)
        result["dc:creator"] = f"{surname} {_given[0]}."

    authors = result.get("author")
    if isinstance(authors, list):
        result["author"] = [
            _sanitise_author(item, rng) if isinstance(item, Mapping) else item for item in authors
        ]
    elif isinstance(authors, Mapping):
        result["author"] = _sanitise_author(authors, rng)

    affiliations = result.get("affiliation")
    if isinstance(affiliations, list):
        result["affiliation"] = [
            _sanitise_affiliation(item, rng) if isinstance(item, Mapping) else item
            for item in affiliations
        ]
    elif isinstance(affiliations, Mapping):
        result["affiliation"] = _sanitise_affiliation(affiliations, rng)

    return result


def sanitise_page(page: Mapping[str, Any], *, seed: int = 0) -> dict[str, Any]:
    """Sanitise one recorded Scopus Search API response page.

    Args:
        page: The parsed (``json.load``-ed) response body, i.e. a
            ``{"search-results": {...}}`` mapping.
        seed: The seed for the internal :class:`random.Random` driving
            every substitution -- fixed by default so re-running this
            function against the same input is byte-identical, which is
            what lets a reviewer diff a cassette regeneration meaningfully.

    Returns:
        A deep copy of ``page`` with every ``search-results.entry`` element
        run through :func:`_sanitise_entry`. Every field a contract test
        might assert the presence *or absence* of --
        ``opensearch:totalResults``, ``cursor``, ``link``, and the set of
        keys each entry does or does not carry -- is copied through
        unchanged; only the four content categories BUILD_PLAN line 534
        names are ever rewritten.
    """
    rng = random.Random(seed)
    result: dict[str, Any] = copy.deepcopy(dict(page))
    search_results = result.get("search-results")
    if isinstance(search_results, Mapping):
        entries = search_results.get("entry")
        if isinstance(entries, list):
            search_results["entry"] = [
                _sanitise_entry(item, rng) if isinstance(item, Mapping) else item
                for item in entries
            ]
    return result


__all__ = [
    "REDACTED",
    "sanitise_headers",
    "sanitise_page",
    "sanitise_query_string",
]
