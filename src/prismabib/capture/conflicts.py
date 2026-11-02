"""Conflict resolution: the Layer 2 writer and its reversal (ADR 0041 Decision 2).

The only writer of ``decisions/conflict_resolutions.jsonl``. It reads the open conflict row from
the store and records the digests of both sides exactly as the reviewer saw them; the loader
applies the event only while they still match. Refusals write nothing.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final, cast, get_args

from prismabib.capture.manual import (
    FIELDS,
    ManualEntryRefusal,
    _default_ids,
    _utc_now,
    active_events,
    check_field_and_value,
    require_reviewer,
)
from prismabib.errors import ValidationError
from prismabib.ids import IdFactory
from prismabib.models import (
    ChosenSide,
    ConflictRecord,
    ConflictResolution,
    ConflictReverse,
    ConflictReverseReason,
    ManualField,
    conflict_resolutions_log,
)
from prismabib.project import Project
from prismabib.store.db import connect

SIDES: Final[tuple[str, ...]] = get_args(ChosenSide)
CONFLICT_REVERSE_REASONS: Final[tuple[str, ...]] = get_args(ConflictReverseReason)


class ConflictRefusal(ManualEntryRefusal):
    """The writer refused a resolution or a reversal; nothing was written."""


@dataclass(frozen=True)
class OpenConflict:
    """One open ``provenance_conflicts`` row: both stored value texts (canonical JSON)."""

    record_id: str
    field: str
    scopus_value: str
    openalex_value: str

    @property
    def digests(self) -> tuple[str, str]:
        """SHA-256 of each side's stored text, what ``scopus_digest``/``openalex_digest`` hold."""
        return tuple(  # type: ignore[return-value]
            hashlib.sha256(v.encode("utf-8")).hexdigest()
            for v in (self.scopus_value, self.openalex_value)
        )


def open_conflicts(project: Project) -> list[OpenConflict]:
    """Every open conflict in the store, ordered by record and field."""
    connection = connect(project, read_only=True)
    try:
        rows = connection.execute(
            "SELECT record_id, field, scopus_value, openalex_value FROM provenance_conflicts "
            "WHERE status = 'open' ORDER BY record_id, field"
        ).fetchall()
    finally:
        connection.close()
    return [OpenConflict(*row) for row in rows]


def resolve_conflict(
    project: Project,
    *,
    record_id: str,
    field: str,
    chosen_side: str,
    reviewer: str,
    value: str | None = None,
    note: str | None = None,
    now: Callable[[], datetime] = _utc_now,
    new_id: IdFactory = _default_ids,
) -> ConflictResolution:
    """Append a ``conflict_resolution`` event, or refuse and write nothing (Decision 2).

    Raises:
        ValidationError: For a blank reviewer, an unknown field or side, a blank ``manual``
            value, or a malformed affiliation line.
        ConflictRefusal: If the key has no open conflict in the store, or an active resolution
            already exists for it.
    """
    require_reviewer(reviewer)
    if chosen_side not in SIDES:
        raise ValidationError(f"chosen_side {chosen_side!r} is not one of {', '.join(SIDES)}")
    if field not in FIELDS:
        raise ValidationError(f"field {field!r} is not one of {', '.join(FIELDS)}")
    if chosen_side == "manual":
        check_field_and_value(field, value or "")
    conflict = next(
        (c for c in open_conflicts(project) if (c.record_id, c.field) == (record_id, field)), None
    )
    if conflict is None:
        raise ConflictRefusal(f"no open conflict for {record_id} {field} in the store")
    scopus_digest, openalex_digest = conflict.digests
    event = ConflictResolution(
        event_id=new_id(),
        ts=now(),
        project=project.slug,
        reviewer=reviewer,
        record_id=record_id,
        field=cast(ManualField, field),
        chosen_side=cast(ChosenSide, chosen_side),
        value=value if chosen_side == "manual" else None,
        scopus_digest=scopus_digest,
        openalex_digest=openalex_digest,
        note=note if (note or "").strip() else None,
    )

    def check(existing: Sequence[ConflictRecord]) -> None:
        for active in active_events(
            [r.root for r in existing], ConflictResolution, ConflictReverse
        ):
            if (active.record_id, active.field) == (record_id, field):
                raise ConflictRefusal(
                    f"an active resolution already exists for {record_id} {field} "
                    f"({active.event_id}); reverse it first"
                )

    conflict_resolutions_log(project.conflict_resolutions_path).append_if(
        ConflictRecord(event), check
    )
    return event


def reverse_conflict(
    project: Project,
    *,
    reverses: str,
    reviewer: str,
    reason: str,
    note: str | None = None,
    now: Callable[[], datetime] = _utc_now,
    new_id: IdFactory = _default_ids,
) -> ConflictReverse:
    """Append a ``reverse`` event naming an active resolution, or refuse (nothing deleted)."""
    require_reviewer(reviewer)
    if reason not in CONFLICT_REVERSE_REASONS:
        raise ValidationError(
            f"reversal reason {reason!r} is not one of {', '.join(CONFLICT_REVERSE_REASONS)}"
        )
    if reason == "other" and not (note or "").strip():
        raise ValidationError("reversal reason 'other' requires a non-empty note")
    event = ConflictReverse(
        event_id=new_id(),
        ts=now(),
        project=project.slug,
        reviewer=reviewer,
        reverses=reverses,
        reason=cast(ConflictReverseReason, reason),
        note=note if (note or "").strip() else None,
    )
    refusal = ConflictRefusal(f"{reverses!r} is not an active conflict resolution")
    if not project.conflict_resolutions_path.is_file():  # no empty log for a refusal
        raise refusal

    def check(existing: Sequence[ConflictRecord]) -> None:
        active = active_events([r.root for r in existing], ConflictResolution, ConflictReverse)
        if reverses not in {e.event_id for e in active}:
            raise refusal

    conflict_resolutions_log(project.conflict_resolutions_path).append_if(
        ConflictRecord(event), check
    )
    return event


__all__ = [
    "CONFLICT_REVERSE_REASONS",
    "SIDES",
    "ConflictRefusal",
    "OpenConflict",
    "open_conflicts",
    "resolve_conflict",
    "reverse_conflict",
]
