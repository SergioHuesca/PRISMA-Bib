"""What reversing a registration would do to the decisions on that record (ADR 0035 Decision 4).

``orphaned_decisions`` is retrospective: it lists decisions whose record has already left
``S_raw``. A reviewer about to reverse needs the prospective figure, which this module
gives. It only reads: no append, no file created.
"""

from __future__ import annotations

from dataclasses import dataclass

from prismabib.prisma.engine import manual_fulltext_set
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project


@dataclass(frozen=True)
class ReversalImpact:
    """The warning figures shown before a reversal.

    Attributes:
        record_id: The registered record the figures are about.
        currently_included: Whether it is in ``M_full`` (the set sized by ``FlowCounts.included``).
        decisions_orphaned: Current decisions (one per stage, record, reviewer) on it.
    """

    record_id: str
    currently_included: bool
    decisions_orphaned: int


def reversal_impact(project: Project, record_id: str) -> ReversalImpact:
    """Count what a reversal of ``record_id`` would orphan, without writing anything.

    Raises:
        StoreError: If no Layer 1 store exists yet.
        ConfigError: If ``criteria.yaml`` is unreadable.
        LogError: If the decision log fails to load.
    """
    orphaned = sum(1 for _stage, rid, _reviewer in DecisionLog(project).fold() if rid == record_id)
    return ReversalImpact(
        record_id=record_id,
        currently_included=record_id in manual_fulltext_set(project),
        decisions_orphaned=orphaned,
    )


__all__ = ["ReversalImpact", "reversal_impact"]
