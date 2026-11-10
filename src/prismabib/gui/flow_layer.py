"""Flow counts and registering a Record, as the screen sees them.

Plain functions over the library: ``compute_flow_counts`` (what the CLI ``flow`` prints) and the
CLI's own writers, ``CandidateLog.add`` and ``register_candidate``. Registering writes one event to
the registration log and nothing else: it starts no cascade step, and it takes effect in the Flow
counts at the next build, exactly as on the CLI. Nothing here reads a key.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from prismabib.capture.candidates import (
    CandidateEvent,
    CandidateLog,
    CandidateView,
    parse_pasted_rows,
)
from prismabib.capture.registrations import (
    MatchRule,
    RegisterEvent,
    RegistrationRefusal,
    active_registrations,
    load_registration_events,
    register_candidate,
)
from prismabib.errors import ValidationError
from prismabib.gui.layer import Session, write_operation
from prismabib.openalex_map import ClassificationRefusal
from prismabib.prisma.flow import FlowCounts, compute_flow_counts
from prismabib.project import Project
from prismabib.source_mode import command_refusal, require_command

NEEDS_STORE = "Registering needs the built store. Finish the Build step first."


@dataclass(frozen=True)
class FlowRow:
    """One printed line of the CLI's flow report: a label, its count and how it is shown."""

    label: str
    value: int
    subtract: bool = False  # shown as "-n", as the CLI does
    indent: bool = False  # a breakdown of the line above, not a further subtraction


_AUTOMATED: dict[str, str] = {
    "year": "by publication year",
    "subject_area": "by subject area",
    "doc_type": "by document type",
    "venue": "by conference whitelist",
}


def flow_rows(counts: FlowCounts, *, mode: str) -> list[FlowRow]:
    """The report's lines in the CLI's order, in the researcher's words (included studies)."""
    source = "OpenAlex meta.count" if mode == "openalex" else "Scopus total_results"
    rows = [FlowRow(f"Records identified ({source})", counts.identified)]
    if counts.registered:  # its own line, never part of Identified
        rows.append(FlowRow("Registered records (added by a reviewer)", counts.registered))
    rows += [
        FlowRow("Duplicates across searches", counts.duplicates_across_searches, True),
        FlowRow("Other reasons (unreadable capture entries)", counts.removed_other_reasons, True),
        FlowRow("Excluded by automated Criteria", counts.excluded_automated, True),
        *(
            FlowRow(_AUTOMATED[reason], n, indent=True)
            for reason, n in counts.excluded_automated_by_reason.items()
        ),
        FlowRow("Remaining after automated Criteria", counts.after_automated),
        FlowRow("Excluded by language", counts.excluded_language, True),
        FlowRow("Remaining, to title and abstract screening", counts.after_language),
        FlowRow("Excluded at title and abstract", counts.excluded_title_abstract, True),
        FlowRow("Unsure or not yet screened (title and abstract)", counts.unsure_title_abstract),
        FlowRow("Sought for full-text retrieval", counts.retrieved_fulltext),
        FlowRow("Excluded at full text", sum(counts.excluded_fulltext.values()), True),
        *(
            FlowRow(f"Reason {reason}", n, indent=True)
            for reason, n in sorted(counts.excluded_fulltext.items())
        ),
        FlowRow("Unsure or not yet screened (full text)", counts.unsure_fulltext),
        FlowRow("Included studies", counts.included),
    ]
    return rows


def flow_counts(project: Project) -> FlowCounts:
    """The numbers the CLI ``flow`` command prints, recomputed from the store and the log."""
    return compute_flow_counts(project)


def pending_registrations(project: Project, counts: FlowCounts) -> int:
    """Active registrations the built store does not hold yet (they count after the next build)."""
    active = active_registrations(load_registration_events(project.registrations_path))
    return max(len(active) - counts.registered, 0)


def candidate_log(project: Project) -> CandidateLog:
    """The Project's log of candidates (Records the search missed)."""
    return CandidateLog(project.root / "decisions" / "candidates.jsonl")


def candidates(project: Project) -> list[CandidateView]:
    """Every candidate, oldest first."""
    return candidate_log(project).view()


def registration_refusal(project: Project) -> str | None:
    """Why registering is not available here (Source mode, no built store), else ``None``."""
    return command_refusal(project, "register") or (
        None if project.db_path.is_file() else NEEDS_STORE
    )


@write_operation
def add_candidates(session: Session, text: str, *, reviewer: str) -> dict[str, int]:
    """Add pasted ``title, year, doi`` rows as candidates, as ``candidates add`` does.

    Returns:
        How many rows were ``added``, ``enriched``, ``already_present`` or in ``conflict``.

    Raises:
        ProjectLockedError: If the session is read-only.
        ValidationError: With a blank reviewer, no rows, or a row that does not parse (nothing
            is written then).
    """
    project = session.require_writable()
    if not reviewer.strip():
        raise ValidationError("Enter the reviewer name: every registration will carry it.")
    rows = parse_pasted_rows(text)
    if not rows:
        raise ValidationError("Paste at least one line: the title, then the year, then the DOI.")
    log, now = candidate_log(project), datetime.now(UTC)
    return dict(
        Counter(log.add(CandidateEvent.from_row(r, reviewer=reviewer, pasted_at=now)) for r in rows)
    )


@write_operation
def register_record(
    session: Session,
    *,
    candidate_id: str,
    reviewer: str,
    payload_file: str,
    match_rule: str,
    confirmed_near: bool = False,
) -> RegisterEvent:
    """Register a candidate as a Registered record: the CLI ``register`` command's own call.

    ``near_confirmed`` is refused unless ``confirmed_near`` is true.

    Raises:
        ProjectLockedError: If the session is read-only.
        ConfigError: If the Source mode has no registration.
        ValidationError: Before the store is built, or with a blank reviewer.
        PrismabibError: Every refusal of the library; nothing is written then.
    """
    project = session.require_writable()
    require_command(project, "register")
    if not project.db_path.is_file():
        raise ValidationError(NEEDS_STORE)
    if not reviewer.strip():
        raise ValidationError("Enter the reviewer name: every registration will carry it.")
    try:
        return register_candidate(
            project,
            candidate_log(project),
            candidate_id=candidate_id,
            reviewer=reviewer,
            payload_file=payload_file,
            match_rule=cast(MatchRule, match_rule),
            confirmed_near=confirmed_near,
        )
    except (RegistrationRefusal, ClassificationRefusal) as exc:  # written for researchers
        raise ValidationError(f"{str(exc)[:1].upper()}{str(exc)[1:]}.") from exc


__all__ = [
    "NEEDS_STORE",
    "FlowRow",
    "add_candidates",
    "candidate_log",
    "candidates",
    "flow_counts",
    "flow_rows",
    "pending_registrations",
    "register_record",
    "registration_refusal",
]
