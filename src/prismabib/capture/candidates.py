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

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from prismabib.errors import LogError, ValidationError
from prismabib.models import normalise_doi as _models_normalise_doi
from prismabib.prisma.log import AppendOnlyLog

CURRENT_CANDIDATE_SCHEMA_VERSION = 1

_DOI_LIKE = re.compile(r"(10\.|doi:|https?://)", re.IGNORECASE)
_YEAR = re.compile(r"[0-9]+")


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
        else:
            rows.append(CandidateRow(title=title, year=int(year), doi=normalise_doi(doi)))
    if errors:
        raise ValidationError("; ".join(errors))
    return rows


def candidate_id(title: str, year: int, doi: str | None) -> str:
    """Deterministic id from normalised title, year and DOI."""
    key = "\x1f".join([normalise_title(title), str(year), normalise_doi(doi) or ""])
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
        if self.candidate_id != candidate_id(self.title, self.year, self.doi):
            raise ValueError("candidate_id does not match title, year and doi")
        return self

    @classmethod
    def from_row(cls, row: CandidateRow, *, reviewer: str, pasted_at: datetime) -> CandidateEvent:
        """Build an event, deriving ``candidate_id`` from the row."""
        doi = normalise_doi(row.doi)
        return cls(
            candidate_id=candidate_id(row.title, row.year, doi),
            title=row.title,
            year=row.year,
            doi=doi,
            pasted_at=pasted_at,
            reviewer=reviewer,
        )


class CandidateLog:
    """Append-only candidates file, built on the shared :class:`AppendOnlyLog`."""

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

    @property
    def path(self) -> Path:
        """The JSONL path."""
        return self._store.path

    def load(self) -> list[CandidateEvent]:
        """Every candidate event, oldest first."""
        return self._store.load()

    def add(self, event: CandidateEvent) -> bool:
        """Append ``event`` unless the same candidate is already logged.

        Returns:
            ``True`` if appended, ``False`` for a re-paste of an existing row
            (nothing is written; the first event stands).
        """
        if any(e.candidate_id == event.candidate_id for e in self._store.load()):
            return False
        try:
            self._store.write_event(event)
        except LogError:
            # A concurrent writer may have added it between check and write.
            if any(e.candidate_id == event.candidate_id for e in self._store.load()):
                return False
            raise
        return True


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


def near_matches(
    candidate: CandidateEvent | CandidateRow,
    records: Iterable[tuple[str, int | None]],
) -> list[tuple[str, int | None]]:
    """Records for the review list: close to the candidate but not exact matches.

    Two rules, no scoring: same normalised title with a different year, or
    the same year with one normalised title a whole-word prefix of the other
    (a truncated subtitle). Exact matches are excluded.
    """
    want = normalise_title(candidate.title)
    found: list[tuple[str, int | None]] = []
    for title, year in records:
        have = normalise_title(title)
        if not have or not want or title_match(candidate, title, year):
            continue
        same_title_other_year = have == want
        prefix = year == candidate.year and (
            have.startswith(want + " ") or want.startswith(have + " ")
        )
        if same_title_other_year or prefix:
            found.append((title, year))
    return found


__all__ = [
    "CandidateEvent",
    "CandidateLog",
    "CandidateRow",
    "candidate_id",
    "near_matches",
    "normalise_doi",
    "normalise_title",
    "parse_pasted_rows",
    "title_match",
]
