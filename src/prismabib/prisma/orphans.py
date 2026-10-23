"""Orphaned decisions: current decisions on a ``record_id`` outside ``S_raw`` (ADR 0033 §3).

An orphan is **reported, never deleted, never re-attached, and never an
arithmetic term** -- it is deliberately not a :class:`~prismabib.prisma.flow.FlowCounts`
field, because no identity would use it and a field no equation reads is a
check that cannot fail.

"Current" is the fold rule of BUILD_PLAN Stage 4 (lines 970-972): the latest
event per ``(stage, record_id, reviewer)`` by ``(ts, event_id)``. Earlier
events for the same key are history and are not listed.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Final, Literal

from prismabib.prisma.events import DecisionEvent
from prismabib.prisma.log import DecisionLog, FoldKey
from prismabib.project import Project
from prismabib.store.db import connect

#: Why a record left ``S_raw``. ``reversal`` and ``supersession`` apply only to
#: registrations (ADR 0033 §3); they come from the registration log, which the
#: caller supplies. Anything else is ``"record not in S_raw"`` (e.g. a rebuild that
#: dropped a Scopus id).
OrphanReason = Literal["reversal", "supersession", "record not in S_raw"]

_DEFAULT_REASON: Final[OrphanReason] = "record not in S_raw"


@dataclass(frozen=True)
class OrphanedDecision:
    """One current decision whose record is no longer in ``S_raw``."""

    event_id: str
    record_id: str
    stage: str
    reviewer: str
    decision: str
    reason: OrphanReason


def orphaned_decisions(
    raw: Collection[str],
    fold: Mapping[FoldKey, DecisionEvent],
    *,
    outcomes: Mapping[str, Literal["reversal", "supersession"]] | None = None,
) -> list[OrphanedDecision]:
    """List the current decisions whose ``record_id`` is not in ``raw``.

    Args:
        raw: ``S_raw`` (``records_all``).
        fold: A folded decision log (one current event per key).
        outcomes: ``record_id`` -> how a registration left ``S_raw``. Supplied
            by the registration fold; absent ids are reported as
            ``"record not in S_raw"``.

    Returns:
        The orphans, sorted by ``(record_id, stage, reviewer, event_id)`` so
        the listing is a fixed function of the data.
    """
    known = outcomes or {}
    return sorted(
        (
            OrphanedDecision(
                event_id=event.event_id,
                record_id=event.record_id,
                stage=event.stage.value,
                reviewer=event.reviewer,
                decision=event.decision,
                reason=known.get(event.record_id, _DEFAULT_REASON),
            )
            for event in fold.values()
            if event.record_id not in raw
        ),
        key=lambda o: (o.record_id, o.stage, o.reviewer, o.event_id),
    )


def project_orphaned_decisions(
    project: Project,
    *,
    outcomes: Mapping[str, Literal["reversal", "supersession"]] | None = None,
) -> list[OrphanedDecision]:
    """:func:`orphaned_decisions` over ``project``'s store and decision log.

    One Layer 1 read and one fold. A project with no ``decisions.jsonl`` has
    no orphans (and the file is not created by asking).

    Args:
        project: The project to inspect.
        outcomes: See :func:`orphaned_decisions`.

    Returns:
        The orphans.
    """
    if not project.decisions_path.exists():
        return []
    connection = connect(project, read_only=True)
    try:
        raw = {row[0] for row in connection.execute("SELECT record_id FROM records_all").fetchall()}
    finally:
        connection.close()
    return orphaned_decisions(raw, DecisionLog(project).fold(), outcomes=outcomes)


__all__ = ["OrphanReason", "OrphanedDecision", "orphaned_decisions", "project_orphaned_decisions"]
