"""One OpenAlex search work, as the values the Layer 1 tables hold (ADR 0042 Decision 5).

Pure derivation, no I/O and no accumulator: :func:`parse_search_work` turns one raw work from a
sealed ``raw/openalex-search`` response into a :class:`SearchWork`, and the loader in
:mod:`prismabib.store.load` decides what to do with it (membership, duplicates, provenance).

Three rules differ from the registration path (ADR 0032), because a search work is already
identified and refusing it would drop it from the count: an unmapped ``type`` is stored raw
(OQ-6), a missing source or unmapped source type is NULL, and a work with no mapped subfield has
no subject code (the loader records ``none_assigned``, OQ-7). A work with no title cannot be
stored and raises :class:`~prismabib.errors.ValidationError`.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final

from prismabib.countries import ALPHA2_TO_ALPHA3
from prismabib.errors import ValidationError
from prismabib.openalex_map import (
    SOURCE_TYPE_TO_VENUE_TYPE,
    TYPE_TO_DOC_TYPE,
    ClassificationRefusal,
    primary_topic_to_subject_area,
)
from prismabib.sources.openalex import (
    authorship_details,
    parse_work,
    rebuild_abstract,
    topics_to_keywords,
)

#: ``malformed_entries.reason`` of a search work that cannot be loaded (no id, or no title).
REASON_MALFORMED_SEARCH_WORK: Final = "malformed_openalex_search_work"

_Author = tuple[str, str, str | None]  # (author_id, surname, given_name)
_Affiliation = tuple[str, str, str | None]  # (afid, name, country_iso3)


@dataclass(frozen=True)
class SearchWork:
    """The values of one loadable search work (every field already in its stored form)."""

    openalex_id: str
    doi: str | None
    title: str
    year: int | None
    doc_type: str | None
    venue_type: str | None
    venue_name: str | None
    abstract: str | None
    authors: tuple[_Author, ...]
    affiliations: tuple[_Affiliation, ...]
    topics: tuple[str, ...]
    cited_by_count: int | None
    subject_code: str | None
    #: Authorship-level country codes with no alpha-3 mapping; skipped, and logged by the loader.
    unmapped_countries: tuple[str, ...] = ()

    @property
    def record_id(self) -> str:
        """``openalex:<id>``, the key of ``openalex_records``."""
        return f"openalex:{self.openalex_id}"


def work_id(raw: Mapping[str, Any]) -> str | None:
    """The work's OpenAlex id as sent, or ``None`` when it is not a non-blank string."""
    value = raw.get("id")
    return value if isinstance(value, str) and value.strip() else None


def _fold(name: str) -> str:
    """NFKC, casefold, one space, stripped: the key of an id-less author or institution."""
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def _split_name(display_name: str) -> tuple[str, str | None]:
    """``(surname, given)``: the last whitespace token, and the tokens before it."""
    tokens = display_name.split()
    return tokens[-1], " ".join(tokens[:-1]) or None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _source(raw: Mapping[str, Any]) -> Mapping[str, Any]:
    location = raw.get("primary_location")
    source = location.get("source") if isinstance(location, Mapping) else None
    return source if isinstance(source, Mapping) else {}


def _subject_code(raw: Mapping[str, Any]) -> str | None:
    topic = raw.get("primary_topic")
    try:
        return primary_topic_to_subject_area(topic if isinstance(topic, Mapping) else None).code
    except ClassificationRefusal:
        return None


def parse_search_work(raw: Mapping[str, Any]) -> SearchWork:
    """Derive the stored values of one search work.

    Raises:
        ValidationError: If the work has no id, or no title once stripped.
    """
    openalex_id = work_id(raw)
    parsed = parse_work(raw)
    if openalex_id is None:
        raise ValidationError("the OpenAlex work has no id")
    if not isinstance(parsed.title, str) or not parsed.title.strip():
        raise ValidationError(f"the OpenAlex work {openalex_id} has no title")

    raw_type = raw.get("type")
    doc_type = (
        (TYPE_TO_DOC_TYPE.get(raw_type) or raw_type)
        if isinstance(raw_type, str) and raw_type
        else None
    )
    source = _source(raw)
    source_type = source.get("type")
    venue_name = source.get("display_name")
    details = authorship_details(parsed)

    authors: list[_Author] = []
    seen_authors: set[str] = set()
    for author in details.authors:
        if not author.name.strip():
            continue
        surname, given = _split_name(author.name)
        author_id = (
            f"openalex-author:{author.author_id}"
            if author.author_id
            else f"openalex-name:{_fold(author.name)}"
        )
        if author_id in seen_authors:
            continue  # the same author listed twice keeps the first position
        seen_authors.add(author_id)
        authors.append((author_id, surname, given))

    affiliations: dict[str, _Affiliation] = {}
    for inst in details.institutions:
        afid = (
            f"openalex-inst:{inst.inst_id}" if inst.inst_id else f"openalex-name:{_fold(inst.name)}"
        )
        country = ALPHA2_TO_ALPHA3.get(inst.country_code.upper()) if inst.country_code else None
        affiliations.setdefault(afid, (afid, inst.name, country))
    # Owner decision 2026-11-04 (ADR 0042 D-2): a country an authorship names that none of its
    # institutions carries still reaches the geography analyses, as a synthetic affiliation.
    # A code outside ALPHA2_TO_ALPHA3 is skipped, not guessed.
    unmapped: list[str] = []
    for code in details.countries:
        if (iso3 := ALPHA2_TO_ALPHA3.get(code)) is not None:
            afid = f"openalex-country:{code}"
            affiliations.setdefault(afid, (afid, code, iso3))
        elif code:
            unmapped.append(code)

    return SearchWork(
        openalex_id=openalex_id,
        doi=parsed.doi or None,
        title=parsed.title,
        year=parsed.publication_year,
        doc_type=doc_type,
        venue_type=SOURCE_TYPE_TO_VENUE_TYPE.get(source_type)
        if isinstance(source_type, str)
        else None,
        venue_name=(venue_name.strip() or None) if isinstance(venue_name, str) else None,
        abstract=rebuild_abstract(parsed.abstract_inverted_index),
        authors=tuple(authors),
        affiliations=tuple(affiliations.values()),
        topics=tuple(t.name for t in topics_to_keywords(parsed)),
        cited_by_count=_count(raw.get("cited_by_count")),
        subject_code=_subject_code(raw),
        unmapped_countries=tuple(unmapped),
    )
