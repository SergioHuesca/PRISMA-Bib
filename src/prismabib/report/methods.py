"""Methods-text sentences that quote the registered and orphaned counts (ADR 0032 C8, ADR 0033).

Pure functions of numbers already computed: the sentence quotes
``flow.registered`` from the ``numbers.json`` mapping, so the text cannot
disagree with the data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from prismabib.prisma.orphans import OrphanedDecision


def registered_sentence(numbers: Mapping[str, Any]) -> str:
    """ADR 0032 Consequence 8's sentence, or ``""`` when nothing was registered.

    Args:
        numbers: The ``numbers.json`` mapping (needs ``corpus.size`` and
            ``flow.registered``).

    Returns:
        ``"N records, of which M are registrations from a manual Google Scholar
        search (ADR 0029)."``; empty for a project without registrations,
        where the sentence would assert nothing.
    """
    registered = int(numbers["flow.registered"])
    if not registered:
        return ""
    return (
        f"{numbers['corpus.size']} records, of which {registered} are registrations "
        "from a manual Google Scholar search (ADR 0029)."
    )


def orphaned_decisions_text(orphans: Sequence[OrphanedDecision]) -> str:
    """The methods paragraph listing orphaned decisions (ADR 0033 §3).

    Args:
        orphans: The orphans, from :func:`~prismabib.prisma.orphans.orphaned_decisions`.

    Returns:
        ``""`` when there are none; otherwise a count line and one line per
        orphan with its ``event_id``, ``record_id``, stage and reason.
    """
    if not orphans:
        return ""
    lines = [
        (
            f"{len(orphans)} screening decision(s) refer to a record no longer in the "
            "record set. They are kept in the decision log and are not counted:"
        )
    ]
    lines += [
        f"- {o.record_id} ({o.stage}, {o.decision}, event {o.event_id}): {o.reason}"
        for o in orphans
    ]
    return "\n".join(lines)


__all__ = ["orphaned_decisions_text", "registered_sentence"]
