"""Internal domain models (BUILD_PLAN §Stage 1, lines 660-697).

These are prismabib's *internal* representation of a bibliographic record --
deliberately decoupled from Scopus's wire format. Validators normalise a
handful of known Scopus quirks at the boundary (§2.4 line 265):

- ``Record.affiliations`` accepts a single affiliation object as well as a
  list, because Scopus's Search/Abstract Retrieval APIs emit ``affiliation``
  as a bare object when a record has exactly one affiliation and as an array
  otherwise.
- ``Affiliation.afid`` accepts a list of ids as well as a scalar, for the
  same reason applied one level down; the first id is kept as canonical.
- ``Affiliation.country`` is normalised to ISO 3166-1 alpha-3 via
  :mod:`prismabib.countries`. An unmapped string is preserved unchanged
  (never dropped) and logged as a warning -- see that module's docstring and
  BUILD_PLAN §5 risk 8.

The deduplication key of BUILD_PLAN §3.2 (lines 370-374) is also shipped
here, ahead of the Stage 2 acquisition code that will be its first caller.
It is a pure function over :class:`Record` and has no I/O or Scopus-specific
knowledge, so the domain layer -- not ``sources/`` or ``store/`` -- is where
it belongs; putting it here also means Stage 3's tests can import it without
depending on anything acquisition-related.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal

import structlog
from pydantic import BaseModel, Field, field_validator

from prismabib.countries import normalise_country

logger = structlog.get_logger(__name__)

# DOI "URL forms" seen in the wild, longest/most-specific first so a prefix
# match never leaves a shorter sibling prefix behind.
_DOI_URL_PREFIXES: tuple[str, ...] = (
    "https://dx.doi.org/",
    "http://dx.doi.org/",
    "https://doi.org/",
    "http://doi.org/",
    "doi:",
)


class Author(BaseModel):
    """A single contributing author as attached to a :class:`Record`."""

    author_id: str | None
    surname: str
    given_name: str | None
    initials: str | None


class Affiliation(BaseModel):
    """An institutional affiliation attached to a :class:`Record`.

    ``country`` is normalised to ISO 3166-1 alpha-3 on construction; see the
    module docstring for the unmapped-country contract.
    """

    afid: str | None
    name: str
    city: str | None
    country: str | None

    @field_validator("afid", mode="before")
    @classmethod
    def _coerce_afid_scalar(cls, value: Any) -> Any:
        """Collapse Scopus's occasional ``afid`` list into a single scalar.

        Args:
            value: The raw ``afid`` value as received, either a scalar or a
                list of ids.

        Returns:
            ``None`` for an empty list, the sole scalar unchanged, or the
            first element of a list (stringified) as the canonical id.
        """
        if isinstance(value, list):
            return str(value[0]) if value else None
        return value

    @field_validator("country", mode="before")
    @classmethod
    def _normalise_country(cls, value: Any) -> Any:
        """Normalise a free-text country string to ISO 3166-1 alpha-3.

        Args:
            value: The raw ``country`` value as received.

        Returns:
            The value unchanged if it is not a string (validation of the
            actual type is left to Pydantic); otherwise the ISO alpha-3 code
            when the string is recognised, or the original string unchanged
            when it is not -- an unmapped country is never dropped.
        """
        if not isinstance(value, str):
            return value
        normalised, matched = normalise_country(value)
        if not matched:
            logger.warning(
                "affiliation.country.unmapped",
                raw=value,
            )
        return normalised


class Venue(BaseModel):
    """The publication venue (journal, conference, book, ...) of a :class:`Record`."""

    name: str
    issn: str | None
    eissn: str | None
    venue_type: Literal["journal", "conference", "book", "other"]
    abbreviation: str | None


class PayloadRef(BaseModel):
    """A pointer to the Layer 0 raw-capture line that produced a record.

    Layer 0 (the immutable JSONL raw capture, BUILD_PLAN §2.2) does not
    exist until Stage 2, so this is modelled as a plain value object over
    ``(path, line)`` -- it is constructible and JSON-round-trippable with no
    Layer 0 file present, and only :meth:`resolve` performs any I/O.

    ``line`` is a **0-based line index** into the JSONL file at ``path``,
    i.e. the same convention as ``enumerate()`` over the file's lines.
    """

    path: Path
    line: Annotated[int, Field(ge=0)]

    def resolve(self) -> str:
        """Read the originating raw JSON line from ``path``.

        Returns:
            The raw text of line ``self.line`` in ``self.path``, with the
            trailing newline stripped.

        Raises:
            FileNotFoundError: If ``self.path`` does not exist.
            IndexError: If ``self.path`` has fewer than ``self.line + 1``
                lines.
        """
        with self.path.open("r", encoding="utf-8") as handle:
            for index, raw_line in enumerate(handle):
                if index == self.line:
                    return raw_line.rstrip("\n")
        raise IndexError(f"line {self.line} not found in {self.path} (fewer lines than requested)")


class Record(BaseModel):
    """A normalised bibliographic record -- prismabib's unit of analysis."""

    record_id: str
    doi: str | None
    title: str
    abstract: str | None
    year: int
    cover_date: date | None
    doc_type: str
    language: str | None
    venue: Venue
    authors: list[Author]
    affiliations: list[Affiliation]
    author_keywords: list[str]
    index_keywords: list[str]
    subject_areas: list[str]
    open_access: bool | None
    source_payload_ref: PayloadRef

    @field_validator("affiliations", mode="before")
    @classmethod
    def _coerce_affiliations_scalar(cls, value: Any) -> Any:
        """Wrap a single Scopus ``affiliation`` object into a one-item list.

        Args:
            value: The raw ``affiliations`` value as received: either a
                list, or a single mapping / :class:`Affiliation` when Scopus
                emits ``affiliation`` as a bare object (one affiliation).

        Returns:
            ``value`` unchanged if it is already a list (or not a mapping /
            ``Affiliation``); otherwise a single-item list wrapping it.
        """
        if isinstance(value, dict | Affiliation):
            return [value]
        return value


def normalise_doi(doi: str) -> str:
    """Normalise a DOI to prismabib's canonical secondary-key form.

    Per BUILD_PLAN §3.2 (lines 370-374): lowercase, with URL forms (e.g.
    ``https://doi.org/``, ``doi:``) stripped, leaving the bare ``10.xxxx/...``
    form.

    Args:
        doi: A DOI as received from a source, possibly wrapped in a URL or
            ``doi:`` prefix, in any case.

    Returns:
        The bare, lowercase DOI with any recognised URL/scheme prefix
        removed and surrounding whitespace stripped.
    """
    text = doi.strip()
    lowered = text.casefold()
    for prefix in _DOI_URL_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.strip().lower()


def _normalise_text_key(text: str) -> str:
    """Fold free text into a stable comparison key for dedup fallback.

    Args:
        text: Free text such as a record title or an author surname.

    Returns:
        The text casefolded, stripped, and with internal whitespace runs
        collapsed to a single space.
    """
    return " ".join(text.casefold().split())


DedupKey = tuple[Literal["doi"], str] | tuple[Literal["fallback"], str, str, int]
"""The BUILD_PLAN §3.2 deduplication key.

``("doi", normalised_doi)`` when the record carries a DOI; otherwise
``("fallback", normalised_title, first_author_surname, year)``.
"""


def dedup_key(record: Record) -> DedupKey:
    """Compute the BUILD_PLAN §3.2 deduplication key for a record.

    Uses the normalised DOI when present, since it is the highest-confidence
    key across sources. Falls back to ``(normalised_title,
    first_author_surname, year)`` when no DOI is available -- the fuzzy key
    intended for future multi-source deduplication.

    Args:
        record: The record to key.

    Returns:
        A tagged tuple identifying which branch of the key was used; see
        :data:`DedupKey`.

    Raises:
        ValueError: If the record has neither a DOI nor any authors, so no
            key can be computed.
    """
    if record.doi:
        return ("doi", normalise_doi(record.doi))
    if not record.authors:
        raise ValueError(
            f"cannot compute a dedup key for record {record.record_id!r}: "
            "no DOI and no authors to fall back on"
        )
    first_author_surname = _normalise_text_key(record.authors[0].surname)
    normalised_title = _normalise_text_key(record.title)
    return ("fallback", normalised_title, first_author_surname, record.year)
