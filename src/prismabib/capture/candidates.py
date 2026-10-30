"""Candidate intake (ADR 0028 Decision 5): pasted title/year rows as Layer 2 events.

A candidate is a title and year a person found by searching Google Scholar by
hand. It is **not** a record: this module never creates a ``Record`` and has no
registration path (ADR 0028 resolved item 3 needs a Stage 2 amendment first).
Matching is exact on normalised title plus year; a near miss is only listed
for a person to confirm. No fuzzy scoring (ADR 0028 rejected it).
"""

from __future__ import annotations

import csv
import hashlib
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from prismabib.errors import LogError, ValidationError
from prismabib.log import AppendOnlyLog
from prismabib.models import normalise_doi as _models_normalise_doi

CURRENT_CANDIDATE_SCHEMA_VERSION = 1
CURRENT_DOI_ENRICHMENT_SCHEMA_VERSION = 1
CURRENT_DOI_CONFLICT_SCHEMA_VERSION = 1

_DOI_LIKE = re.compile(r"(10\.|doi:|https?://)", re.IGNORECASE)
_YEAR = re.compile(r"[0-9]+")
MIN_YEAR, MAX_YEAR = 1000, 2100  # a typo'd year would silently never match any record


def normalise_title(title: str) -> str:
    """Fold a title to a comparison key: accent-free, lowercase, no punctuation.

    Punctuation, symbols and separators (including Unicode dashes) become a
    space, so ``well-being`` and ``well being`` match; whitespace is then
    collapsed.
    """
    decomposed = unicodedata.normalize("NFKD", title).casefold()
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    spaced = "".join(" " if unicodedata.category(c)[0] in "PSZC" else c for c in stripped)
    return " ".join(spaced.split())


def normalise_doi(doi: str | None) -> str | None:
    """Reuse :func:`prismabib.models.normalise_doi`; blank input becomes ``None``."""
    if doi is None:
        return None
    return _models_normalise_doi(doi) or None


@dataclass(frozen=True)
class CandidateRow:
    """One parsed pasted row."""

    title: str
    year: int
    doi: str | None = None


def _parse_line(line: str) -> tuple[str, str, str | None]:
    """Split one non-blank line into raw (title, year, doi) fields."""
    if "\t" in line:
        fields = [f.strip() for f in line.split("\t")]
        if len(fields) > 3:
            raise ValueError(f"expected 2 or 3 tab-separated columns, got {len(fields)}")
        if len(fields) < 2:
            raise ValueError("expected title and year")
        return fields[0], fields[1], (fields[2] or None) if len(fields) == 3 else None
    fields = next(csv.reader([line]))
    if len(fields) >= 3 and not fields[-1].strip():
        fields.pop()  # trailing empty DOI column, as the tab form allows
    doi = fields.pop().strip() if len(fields) >= 3 and _DOI_LIKE.match(fields[-1].strip()) else None
    if len(fields) < 2:
        raise ValueError("expected title and year")
    # Unquoted titles may contain commas: the year is the last remaining field.
    return ",".join(fields[:-1]).strip(), fields[-1].strip(), doi


def _is_header(line: str) -> bool:
    """True for a ``Title,Year[,DOI]`` header in either separator form."""
    fields = line.split("\t") if "\t" in line else next(csv.reader([line]))
    return [f.strip().casefold() for f in fields[:2]] == ["title", "year"]


def parse_pasted_rows(text: str) -> list[CandidateRow]:
    """Parse pasted rows: ``title<TAB|,>year[<TAB|,>doi]``, optional header.

    Blank lines are skipped (they are not rows). Every other line either
    becomes a row or contributes to a single error naming its line number.

    Raises:
        ValidationError: One message listing every bad line as ``line N: ...``.
    """
    rows: list[CandidateRow] = []
    errors: list[str] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        if not rows and not errors and _is_header(line):
            continue
        try:
            title, year, doi = _parse_line(line)
        except ValueError as exc:
            errors.append(f"line {number}: {exc}")
            continue
        if not title:
            errors.append(f"line {number}: missing title")
        elif not _YEAR.fullmatch(year):
            errors.append(f"line {number}: year {year!r} is not an integer")
        elif not MIN_YEAR <= int(year) <= MAX_YEAR:
            errors.append(f"line {number}: year {year!r} is outside {MIN_YEAR}-{MAX_YEAR}")
        else:
            rows.append(CandidateRow(title=title, year=int(year), doi=normalise_doi(doi)))
    if errors:
        raise ValidationError("; ".join(errors))
    return rows


def candidate_id(title: str, year: int) -> str:
    """Deterministic id from normalised title and year only.

    The DOI is deliberately not part of identity: the same paper pasted with
    and without a DOI is one candidate. The DOI stays on the event.
    """
    key = "\x1f".join([normalise_title(title), str(year)])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


class CandidateEvent(BaseModel):
    """One immutable candidate-paste event (Layer 2)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default_factory=lambda: CURRENT_CANDIDATE_SCHEMA_VERSION)
    candidate_id: str
    title: str
    year: int
    doi: str | None = None
    pasted_at: datetime
    reviewer: str

    @field_validator("schema_version")
    @classmethod
    def _current_version(cls, value: int) -> int:
        if value != CURRENT_CANDIDATE_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {value}")
        return value

    @field_validator("title", "reviewer")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("pasted_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("pasted_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _id_matches_content(self) -> CandidateEvent:
        if self.candidate_id != candidate_id(self.title, self.year):
            raise ValueError("candidate_id does not match title and year")
        return self

    @classmethod
    def from_row(cls, row: CandidateRow, *, reviewer: str, pasted_at: datetime) -> CandidateEvent:
        """Build an event, deriving ``candidate_id`` from the row.

        Intake path: enforces the year bound. Log loading does not, so older
        append-only logs stay readable.
        """
        if not MIN_YEAR <= row.year <= MAX_YEAR:
            raise ValidationError(f"year {row.year} is outside {MIN_YEAR}-{MAX_YEAR}")
        doi = normalise_doi(row.doi)
        return cls(
            candidate_id=candidate_id(row.title, row.year),
            title=row.title,
            year=row.year,
            doi=doi,
            pasted_at=pasted_at,
            reviewer=reviewer,
        )


class DoiEnrichmentEvent(BaseModel):
    """A DOI pasted later for an existing DOI-less candidate (Layer 2).

    Links to the candidate by ``candidate_id``; never part of identity. At most
    one exists per candidate (the first DOI stands), so ``candidate_id`` is
    also this log's unique event id.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default_factory=lambda: CURRENT_DOI_ENRICHMENT_SCHEMA_VERSION)
    candidate_id: str
    doi: str
    pasted_at: datetime
    reviewer: str

    @field_validator("schema_version")
    @classmethod
    def _current_version(cls, value: int) -> int:
        if value != CURRENT_DOI_ENRICHMENT_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {value}")
        return value

    @field_validator("doi")
    @classmethod
    def _normalised_doi(cls, value: str) -> str:
        normalised = normalise_doi(value)
        if normalised is None:
            raise ValueError("doi must not be blank")
        return normalised

    @field_validator("reviewer")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("pasted_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("pasted_at must be timezone-aware")
        return value


class DoiConflictEvent(BaseModel):
    """A pasted DOI that differs from the one stored for the candidate (Layer 2).

    The stored DOI is never replaced; this records the disagreement. Unique per
    (candidate, conflicting DOI), so repeating the same conflict writes nothing.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: int = Field(default_factory=lambda: CURRENT_DOI_CONFLICT_SCHEMA_VERSION)
    candidate_id: str
    doi: str
    stored_doi: str
    pasted_at: datetime
    reviewer: str

    @property
    def event_id(self) -> str:
        """Storage-level uniqueness key: candidate plus conflicting DOI."""
        return f"{self.candidate_id}:{self.doi}"

    @field_validator("schema_version")
    @classmethod
    def _current_version(cls, value: int) -> int:
        if value != CURRENT_DOI_CONFLICT_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {value}")
        return value

    @field_validator("doi", "stored_doi")
    @classmethod
    def _normalised_doi(cls, value: str) -> str:
        normalised = normalise_doi(value)
        if normalised is None:
            raise ValueError("doi must not be blank")
        return normalised

    @field_validator("reviewer")
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("pasted_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("pasted_at must be timezone-aware")
        return value

    @model_validator(mode="after")
    def _actually_differs(self) -> DoiConflictEvent:
        if self.doi == self.stored_doi:
            raise ValueError("a conflict needs a DOI different from the stored one")
        return self


AddResult = Literal["added", "enriched", "already_present", "conflict"]


@dataclass(frozen=True)
class CandidateView:
    """A candidate with its effective DOI, for the review list."""

    event: CandidateEvent
    doi: str | None  # first of: original DOI, earliest enrichment DOI
    conflicting_dois: tuple[str, ...] = ()  # later, different DOIs; never override ``doi``


class CandidateLog:
    """Append-only candidates file plus a sibling DOI-enrichment file.

    Both are built on the shared :class:`AppendOnlyLog`. The enrichment file is
    ``<stem>.doi<suffix>`` next to ``path``.
    """

    def __init__(self, path: Path) -> None:
        self._store: AppendOnlyLog[CandidateEvent] = AppendOnlyLog(
            path,
            model=CandidateEvent,
            current_schema_version=CURRENT_CANDIDATE_SCHEMA_VERSION,
            event_id=lambda event: event.candidate_id,
            noun="candidate",
            event_description="candidate paste",
            recovery_hint="CandidateLog.add",
            writer_name="CandidateLog",
            labour="human candidate",
        )
        self._doi_store: AppendOnlyLog[DoiEnrichmentEvent] = AppendOnlyLog(
            path.with_name(f"{path.stem}.doi{path.suffix}"),
            model=DoiEnrichmentEvent,
            current_schema_version=CURRENT_DOI_ENRICHMENT_SCHEMA_VERSION,
            event_id=lambda event: event.candidate_id,
            noun="doi enrichment",
            event_description="candidate DOI enrichment",
            recovery_hint="CandidateLog.add",
            writer_name="CandidateLog",
            labour="human candidate",
        )
        self._conflict_store: AppendOnlyLog[DoiConflictEvent] = AppendOnlyLog(
            path.with_name(f"{path.stem}.conflicts{path.suffix}"),
            model=DoiConflictEvent,
            current_schema_version=CURRENT_DOI_CONFLICT_SCHEMA_VERSION,
            event_id=lambda event: event.event_id,
            noun="doi conflict",
            event_description="candidate DOI conflict",
            recovery_hint="CandidateLog.add",
            writer_name="CandidateLog",
            labour="human candidate",
        )

    @property
    def conflict_path(self) -> Path:
        """The DOI-conflict JSONL path."""
        return self._conflict_store.path

    @property
    def path(self) -> Path:
        """The candidates JSONL path."""
        return self._store.path

    @property
    def doi_path(self) -> Path:
        """The DOI-enrichment JSONL path."""
        return self._doi_store.path

    def load(self) -> list[CandidateEvent]:
        """Every candidate event, oldest first."""
        return self._store.load()

    def load_enrichments(self) -> list[DoiEnrichmentEvent]:
        """Every DOI-enrichment event, oldest first."""
        return self._doi_store.load()

    def load_conflicts(self) -> list[DoiConflictEvent]:
        """Every DOI-conflict event, oldest first."""
        return self._conflict_store.load()

    def conflicts(self, candidate_id_: str) -> list[DoiConflictEvent]:
        """Conflicting DOIs pasted for one candidate, oldest first."""
        return [e for e in self._conflict_store.load() if e.candidate_id == candidate_id_]

    def view(self) -> list[CandidateView]:
        """Candidates with effective DOI (original, else first enrichment).

        The first DOI always stands; conflicting DOIs are listed on the view.
        """
        enriched: dict[str, str] = {}
        for e in self._doi_store.load():
            enriched.setdefault(e.candidate_id, e.doi)
        conflicting: dict[str, list[str]] = {}
        for ce in self._conflict_store.load():
            conflicting.setdefault(ce.candidate_id, []).append(ce.doi)
        return [
            CandidateView(
                c, c.doi or enriched.get(c.candidate_id), tuple(conflicting.get(c.candidate_id, []))
            )
            for c in self._store.load()
        ]

    def effective_doi(self, candidate_id_: str) -> str | None:
        """Effective DOI of one candidate, or ``None`` if it has none (or is unknown)."""
        return next((v.doi for v in self.view() if v.event.candidate_id == candidate_id_), None)

    def add(self, event: CandidateEvent) -> AddResult:
        """Log a paste, never dropping a new fact.

        Returns:
            ``"added"`` for a new candidate; ``"enriched"`` if the candidate
            existed without a DOI and ``event`` carries one (a
            :class:`DoiEnrichmentEvent` is appended); ``"already_present"``
            when the paste matches the stored DOI or carries none (nothing
            written); ``"conflict"`` when it carries a DOI different from the
            stored one (a :class:`DoiConflictEvent` is appended once per
            distinct conflicting DOI; the stored DOI is never replaced).
        """
        existing = next(
            (v for v in self.view() if v.event.candidate_id == event.candidate_id), None
        )
        if existing is None:
            try:
                self._store.write_event(event)
            except LogError:
                # A concurrent writer may have added it between check and write.
                if any(e.candidate_id == event.candidate_id for e in self._store.load()):
                    return "already_present"
                raise
            return "added"
        if event.doi is None:
            return "already_present"
        if existing.doi is not None:
            if event.doi == existing.doi:
                return "already_present"
            conflict = DoiConflictEvent(
                candidate_id=event.candidate_id,
                doi=event.doi,
                stored_doi=existing.doi,
                pasted_at=event.pasted_at,
                reviewer=event.reviewer,
            )
            try:
                self._conflict_store.write_event(conflict)
            except LogError:
                # The same conflicting DOI is already recorded: write nothing more.
                if conflict.event_id not in {e.event_id for e in self._conflict_store.load()}:
                    raise
            return "conflict"
        try:
            self._doi_store.write_event(
                DoiEnrichmentEvent(
                    candidate_id=event.candidate_id,
                    doi=event.doi,
                    pasted_at=event.pasted_at,
                    reviewer=event.reviewer,
                )
            )
        except LogError:
            if event.candidate_id in {e.candidate_id for e in self._doi_store.load()}:
                return "already_present"
            raise
        return "enriched"


def title_match(
    candidate: CandidateEvent | CandidateRow, record_title: str, record_year: int | None
) -> bool:
    """Exact normalised-title equality and equal years; nothing looser."""
    key = normalise_title(record_title)
    return (
        record_year is not None
        and candidate.year == record_year
        and bool(key)
        and normalise_title(candidate.title) == key
    )


def near_rule(
    candidate: CandidateEvent | CandidateRow, title: str, year: int | None
) -> Literal["same_title_other_year", "title_prefix"] | None:
    """Which near rule (if any) makes ``(title, year)`` a near match for ``candidate``.

    Two rules, no scoring: ``same_title_other_year`` (same normalised title, not an
    exact match) or ``title_prefix`` (same year, one normalised title a whole-word
    prefix of the other, i.e. a truncated subtitle). Exact matches give ``None``.
    """
    want, have = normalise_title(candidate.title), normalise_title(title)
    if not have or not want or title_match(candidate, title, year):
        return None
    if have == want:
        return "same_title_other_year"
    if year == candidate.year and (have.startswith(want + " ") or want.startswith(have + " ")):
        return "title_prefix"
    return None


def near_matches(
    candidate: CandidateEvent | CandidateRow,
    records: Iterable[tuple[str, int | None]],
) -> list[tuple[str, int | None]]:
    """Records for the review list: close to the candidate but not exact matches (`near_rule`)."""
    return [(t, y) for t, y in records if near_rule(candidate, t, y) is not None]


__all__ = [
    "CandidateEvent",
    "CandidateLog",
    "CandidateRow",
    "CandidateView",
    "DoiConflictEvent",
    "DoiEnrichmentEvent",
    "candidate_id",
    "near_matches",
    "near_rule",
    "normalise_doi",
    "normalise_title",
    "parse_pasted_rows",
    "title_match",
]
