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
    - ``authkeywords`` is transliterated term-by-term, preserving the
      pipe-delimited structure and each term's length. Keywords are licensed
      Scopus content like any other prose; the keyword *pipeline* is what the
      fixtures exercise, not the vocabulary.
    - **Person and institution identifiers are remapped, not passed through**:
      ``authid``, ``orcid``, ``afid``, and the ``author-url``/
      ``affiliation-url`` that embed them. This goes beyond BUILD_PLAN line
      534's four named categories, deliberately. A real ORCID or Scopus author
      id sitting next to a *fabricated* name is worse than either alone: it is
      a resolvable pointer to a named living researcher, republished on a
      PUBLIC repository, attached to someone else's identity. Names are noise;
      an ORCID is an identity. The synthetic ORCID is built to be format-valid
      but check-digit-INVALID, so it can never resolve to a real person.
      Mapping is stable per id (see :func:`_map_id`), so the cross-reference an
      ``afid`` on an author shares with an ``afid`` on an affiliation survives,
      and re-running the sanitiser reproduces the cassette byte-for-byte.

- **Left alone, deliberately**: *work* identifiers -- ``dc:identifier``,
  ``eid``, ``prism:doi``, ``pii``, ``article-number``, ``prism:url`` -- plus
  dates, counts, subtype codes, venue names, funding fields, and the whole
  ``link``/``cursor`` pagination envelope. These identify a published paper,
  not a person, and BUILD_PLAN §2.5 settles the principle explicitly when it
  says ``decisions.jsonl`` "contains no licensed content -- only record IDs
  and decisions". **Structure is what survives**: field presence and absence
  per entry, scalar-vs-list shape, and the pagination envelope are copied
  through unchanged -- see ``tests/fixtures/README.md`` on the specific 22/25
  ``authkeywords`` split preserved in ``complete-page-0000.json``.
"""

from __future__ import annotations

import copy
import hashlib
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


_AUTHOR_URL_PREFIX = "https://api.elsevier.com/content/author/author_id/"
_AFFILIATION_URL_PREFIX = "https://api.elsevier.com/content/affiliation/affiliation_id/"

# Stable real-id -> synthetic-id maps, shared across every page sanitised in one
# process. Stability is not cosmetic: Stage 3 groups records by `afid` and Stage 7
# counts distinct institutions, so a per-occurrence random id would silently change
# what these fixtures exercise, and a collapsed id would invent collaborations that
# never happened.
_ID_MAPS: dict[str, dict[str, str]] = {}


def _map_id(real: str, namespace: str) -> str:
    """Map a real Scopus identifier to a stable synthetic one.

    Args:
        real: The identifier as recorded.
        namespace: Which id space this belongs to (``authid``, ``afid``), so two
            different kinds never collide onto the same synthetic value.

    Returns:
        A synthetic identifier of the same length and character class, stable for
        the lifetime of the process.
    """
    space = _ID_MAPS.setdefault(namespace, {})
    if real not in space:
        # Deterministic in the real id, so re-running the sanitiser reproduces the
        # same cassette byte-for-byte -- a cassette that churns on every run makes
        # its diff unreviewable, which §3.7.5 explicitly cares about.
        digest = hashlib.sha256(f"{namespace}:{real}".encode()).hexdigest()
        digits = "".join(str(int(c, 16) % 10) for c in digest)
        space[real] = digits[: len(real)] if real.isdigit() else digest[: len(real)]
    return space[real]


def _map_afid_field(value: Any) -> Any:
    """Map an ``afid`` that Scopus emits as either a scalar or a list.

    The scalar-vs-list inconsistency is itself a quirk the cassettes exist to pin
    (BUILD_PLAN §2.4), so the shape is preserved exactly and only the values move.
    """
    if isinstance(value, str):
        return _map_id(value, "afid")
    if isinstance(value, list):
        return [
            {**item, "$": _map_id(str(item["$"]), "afid")}
            if isinstance(item, Mapping) and "$" in item
            else (_map_id(item, "afid") if isinstance(item, str) else item)
            for item in value
        ]
    return value


def _synthetic_orcid(real: str) -> str:
    """Return a format-valid but deliberately UNREGISTERABLE ORCID.

    An ORCID's final character is a MOD-11-2 check digit. This builds the digits
    deterministically from the real value and then forces a check digit that is
    wrong on purpose, so the result matches the `NNNN-NNNN-NNNN-NNNC` shape any
    parser expects while never resolving to a living researcher. Publishing a
    real ORCID beside a fabricated name -- on a PUBLIC repository -- is the one
    thing in a cassette that identifies an actual person.
    """
    digest = hashlib.sha256(f"orcid:{real}".encode()).hexdigest()
    digits = "".join(str(int(c, 16) % 10) for c in digest)[:15]
    total = 0
    for ch in digits:
        total = (total + int(ch)) * 2
    correct = (12 - total % 11) % 11
    wrong = (correct + 1) % 11
    check = "X" if wrong == 10 else str(wrong)
    return f"{digits[0:4]}-{digits[4:8]}-{digits[8:12]}-{digits[12:15]}{check}"


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

    # Person identifiers MUST be remapped, not passed through. A real `authid` or
    # `orcid` next to a fabricated name is worse than either alone: it is a
    # resolvable pointer to a named living researcher, published on a PUBLIC
    # repository, attached to someone else's name. Names alone are noise; an ORCID
    # is an identity.
    if "authid" in result:
        result["authid"] = _map_id(str(result["authid"]), "authid")
    if "orcid" in result:
        result["orcid"] = _synthetic_orcid(str(result["orcid"]))
    if "author-url" in result and "authid" in result:
        result["author-url"] = f"{_AUTHOR_URL_PREFIX}{result['authid']}"
    if "afid" in result:
        result["afid"] = _map_afid_field(result["afid"])
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

    # A real `afid` resolves to the real institution regardless of the synthetic
    # `affilname` beside it, so it is remapped too. The mapping is stable across
    # the whole cassette set, which matters: Stage 3 groups by `afid` and Stage 7's
    # geography analysis counts distinct institutions, so collapsing or randomising
    # them per-occurrence would silently change what those fixtures exercise.
    if "afid" in result:
        result["afid"] = _map_afid_field(result["afid"])
    if "affiliation-url" in result and "afid" in result:
        result["affiliation-url"] = f"{_AFFILIATION_URL_PREFIX}{result['afid']}"
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

    # Author keywords are licensed Scopus content too, and this repository is
    # PUBLIC, so they are transliterated rather than passed through. The
    # pipe-delimited structure and each term's length are preserved, because the
    # keyword pipeline (Stage 3 normalisation, Stage 7 co-occurrence) is built
    # against that shape -- what the contract tests pin is the shape, not the
    # vocabulary. Whether the field is present at all is still untouched: the
    # 22-of-25 presence split is the very quirk §3.7.5 exists to preserve.
    keywords = result.get("authkeywords")
    if isinstance(keywords, str):
        result["authkeywords"] = " | ".join(
            _transliterate(term.strip(), rng) for term in keywords.split("|")
        )

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
