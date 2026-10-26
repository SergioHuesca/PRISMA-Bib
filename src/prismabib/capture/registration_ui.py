"""Pure view models for the registration form (ADR 0035 Decisions 2-4; Panel arrives in a later PR).

Everything a reviewer sees or a test asserts is a plain function over plain data, so the
widgets can be thin. This module imports no Panel and no log: the writers
(``register_candidate``, ``reverse_registration``) are called by the view, never here, and
matching is the writer's own :func:`~prismabib.capture.registrations.matching_works`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final

from prismabib.capture.candidates import CandidateView, near_rule
from prismabib.capture.layout import OPENALEX_DIRNAME, RUN_MANIFEST_FILENAME, is_sealed
from prismabib.capture.manifest import OpenAlexRunManifest
from prismabib.capture.registrations import (
    REVERSAL_REASONS,
    RegistrationRefusal,
    matching_works,
    read_response,
)
from prismabib.prisma.impact import ReversalImpact
from prismabib.sources.openalex import parse_work

RULES: Final[tuple[str, ...]] = ("doi", "title_year", "near_confirmed")
NEAR: Final = "near_confirmed"
RULE_DEFINITIONS: Final[Mapping[str, str]] = {
    "doi": "the work's DOI equals the candidate's DOI",
    "title_year": "the candidate has no DOI; the normalised title and the year are equal",
    "near_confirmed": (
        "same normalised title with a different year, or one title a whole-word prefix of "
        "the other; needs the reviewer's confirmation"
    ),
}
NEAR_RULE_TEXT: Final[Mapping[str, str]] = {
    "same_title_other_year": "same normalised title, different year",
    "title_prefix": "one title is a whole-word prefix of the other, same year",
}


# -- candidates ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateRowView:
    """One row of the candidate table. ``record_id`` is set when it has an active registration."""

    candidate_id: str
    title: str
    year: int
    doi: str | None
    reviewer: str
    pasted_at: str
    record_id: str | None

    @property
    def state(self) -> str:
        """``unregistered`` or ``registered (<record_id>)``."""
        return "unregistered" if self.record_id is None else f"registered ({self.record_id})"

    @property
    def selectable(self) -> bool:
        """A candidate with an active registration cannot be selected for Register."""
        return self.record_id is None


def candidate_rows(
    views: Sequence[CandidateView], registered: Mapping[str, str]
) -> list[CandidateRowView]:
    """Rows from ``CandidateLog.view()``; ``registered`` maps candidate_id to active record_id."""
    return [
        CandidateRowView(
            candidate_id=v.event.candidate_id,
            title=v.event.title,
            year=v.event.year,
            doi=v.doi,
            reviewer=v.event.reviewer,
            pasted_at=v.event.pasted_at.isoformat(),
            record_id=registered.get(v.event.candidate_id),
        )
        for v in views
    ]


# -- payload choice ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResponseOption:
    """A sealed response in which exactly one work satisfies the rule."""

    label: str  # <run>/response-NNNN
    value: str  # openalex/<run>/response-NNNN.jsonl, what the writer takes
    api_view: str
    http_status: int
    work: dict[str, Any]


@dataclass(frozen=True)
class ResponseOptions:
    """The offered responses plus the counts that explain the ones not offered."""

    options: tuple[ResponseOption, ...]
    unmatched: int  # readable responses with no work or several under the rule
    unreadable: int  # responses the writer would refuse to read (not 200, old select, bad digest)


def response_options(raw_dir: Path, candidate: CandidateView, rule: str) -> ResponseOptions:
    """Sealed OpenAlex responses with exactly one work satisfying ``rule`` (Layer 0 read only)."""
    options: list[ResponseOption] = []
    unmatched = unreadable = 0
    root = raw_dir / OPENALEX_DIRNAME
    run_dirs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    for run_dir in run_dirs:
        if not is_sealed(run_dir):
            continue
        try:
            manifest = OpenAlexRunManifest.model_validate_json(
                (run_dir / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
        except ValueError:
            unreadable += 1
            continue
        for request in sorted(manifest.requests, key=lambda r: r.file):
            value = f"{OPENALEX_DIRNAME}/{run_dir.name}/{request.file}"
            try:
                works = read_response(raw_dir, value).works
            except RegistrationRefusal:
                unreadable += 1
                continue
            found = matching_works(candidate, rule, works)
            if len(found) != 1:
                unmatched += 1
                continue
            options.append(
                ResponseOption(
                    label=f"{run_dir.name}/{request.file.removesuffix('.jsonl')}",
                    value=value,
                    api_view=request.api_view,
                    http_status=request.http_status,
                    work=found[0],
                )
            )
    return ResponseOptions(tuple(options), unmatched, unreadable)


def preselected_rule(candidate: CandidateView, works: Sequence[dict[str, Any]]) -> str | None:
    """The rule to preselect: the only one under which exactly one work matches, else ``None``."""
    ok = [r for r in RULES if len(matching_works(candidate, r, works)) == 1]
    return ok[0] if len(ok) == 1 else None


# -- the match table -----------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchTable:
    """Candidate and work side by side, with the rule named (ADR 0035 section 3)."""

    rule: str
    rule_definition: str
    near_rule: str | None  # which near rule fired; only for ``near_confirmed``
    rows: tuple[tuple[str, str, str], ...]  # (field, candidate, work)


def match_table(candidate: CandidateView, work: Mapping[str, Any], rule: str) -> MatchTable:
    """Side-by-side evidence for the one satisfying ``work`` under ``rule``."""
    parsed = parse_work(work)
    title, year = parsed.title or "", parsed.publication_year
    fired = near_rule(candidate.event, title, year) if rule == NEAR else None
    return MatchTable(
        rule=rule,
        rule_definition=RULE_DEFINITIONS[rule],
        near_rule=fired,
        rows=(
            ("title", candidate.event.title, title),
            ("year", str(candidate.event.year), "" if year is None else str(year)),
            ("doi", candidate.doi or "", parsed.doi or ""),
        ),
    )


def register_confirmation_text(table: MatchTable) -> str:
    """The last step before the write: DOI, title, year and rule (ADR 0029 section 1)."""
    work = {name: w for name, _c, w in table.rows}
    text = f"Register {work['doi']}: {work['title']} ({work['year']}) under rule {table.rule}."
    if table.near_rule is not None:
        text += f" Near match: {NEAR_RULE_TEXT[table.near_rule]}."
    return text


# -- near-match confirmation ---------------------------------------------------------------------


@dataclass(frozen=True)
class NearConfirmation:
    """The confirmation checkbox: the only source of ``confirmed_near=True``.

    ``selection`` is ``(candidate_id, payload, rule)``. Any change of selection clears the tick,
    and the box is not offered (so cannot be ticked) for ``doi`` and ``title_year``.
    """

    selection: tuple[str, str, str] | None = None
    checked: bool = False

    @property
    def offered(self) -> bool:
        """Whether the checkbox is shown at all."""
        return self.selection is not None and self.selection[2] == NEAR

    @property
    def confirmed_near(self) -> bool:
        """The value to pass as ``confirmed_near``."""
        return self.checked and self.offered

    def select(self, selection: tuple[str, str, str] | None) -> NearConfirmation:
        """A new state for ``selection``; the tick is cleared unless the selection is unchanged."""
        return self if selection == self.selection else NearConfirmation(selection, False)

    def tick(self, checked: bool) -> NearConfirmation:
        """A new state with the box set; ignored when the box is not offered."""
        return replace(self, checked=checked and self.offered)


# -- reversal ------------------------------------------------------------------------------------


def reason_options() -> tuple[str, ...]:
    """The reversal reasons, the writer's own tuple (never a copy)."""
    return REVERSAL_REASONS


def can_reverse(reason: str, note: str) -> bool:
    """Whether the reverse button may be enabled: a known reason, and a note for ``other``."""
    return reason in REVERSAL_REASONS and (reason != "other" or bool(note.strip()))


def reversal_confirmation_text(impact: ReversalImpact | None) -> str:
    """Warning text for the single reversal confirmation; ``None`` means the impact is unknown."""
    if impact is None:
        return "The impact of this reversal is unknown (see the error). Reverse anyway?"
    included = "is currently included" if impact.currently_included else "is not currently included"
    return (
        f"Reversing {impact.record_id} orphans {impact.decisions_orphaned} current "
        f"decision(s); the paper {included}. The decisions stay in the log. Reverse?"
    )


__all__ = [
    "CandidateRowView",
    "MatchTable",
    "NearConfirmation",
    "ResponseOption",
    "ResponseOptions",
    "can_reverse",
    "candidate_rows",
    "match_table",
    "preselected_rule",
    "reason_options",
    "register_confirmation_text",
    "response_options",
    "reversal_confirmation_text",
]
