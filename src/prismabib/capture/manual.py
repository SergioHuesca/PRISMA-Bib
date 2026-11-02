"""Manual entry (level 5): the Layer 2 writer and its reversal (ADR 0041 Decision 4).

The only writer of ``decisions/manual_entries.jsonl``; the CLI and the panel both call it. The
PR 3 loader folds the log and fails the build on a malformed affiliation line or a blank
reviewer, so the writer refuses both here, before anything is written. Checks that read the log
run inside :meth:`~prismabib.log.AppendOnlyLog.append_if`, under the append lock.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, cast, get_args

from prismabib.errors import ValidationError
from prismabib.ids import IdFactory, MonotonicUlidFactory
from prismabib.models import (
    ManualEntry,
    ManualField,
    ManualRecord,
    ManualReverse,
    ManualReverseReason,
    manual_entries_log,
)
from prismabib.project import Project
from prismabib.store.db import connect

FIELDS: Final[tuple[str, ...]] = get_args(ManualField)
MANUAL_REVERSE_REASONS: Final[tuple[str, ...]] = get_args(ManualReverseReason)
SCOPUS_PREFIX: Final = "scopus:"

_default_ids: IdFactory = MonotonicUlidFactory()


class ManualEntryRefusal(ValidationError):
    """The writer refused a manual entry or a reversal; nothing was written."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def require_reviewer(reviewer: str) -> None:
    """Refuse a blank reviewer; the loader would fail the build on one."""
    if not reviewer.strip():
        raise ValidationError("a named reviewer is required (the reviewer must not be blank)")


def check_field_and_value(field: str, value: str) -> None:
    """Refuse an unknown field, a blank value or a malformed affiliation line (ADR 0041 Dec. 4)."""
    if field not in FIELDS:
        raise ValidationError(f"field {field!r} is not one of {', '.join(FIELDS)}")
    if not value.strip():
        raise ValidationError("the value is blank")
    if field == "affiliations":
        # Mirror store.load._typed_candidate exactly (CRLF/CR -> LF, split on "\n" only, strip);
        # str.splitlines() also splits on \x0c, U+2028 etc., which the loader does not.
        text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
        for line in (part.strip() for part in text.split("\n")):
            if line and len(line.split(";")) > 3:
                raise ValidationError(
                    f"the affiliation line {line.strip()!r} has more than three ';' parts; "
                    "use 'Name; City; Country' and shorten a name that contains ';'"
                )


def active_events(events: Sequence[Any], kind: type[Any], reverse: type[Any]) -> list[Any]:
    """The ``kind`` events, in file order, that no ``reverse`` names."""
    removed = {e.reverses for e in events if isinstance(e, reverse)}
    return [e for e in events if isinstance(e, kind) and e.event_id not in removed]


@dataclass(frozen=True)
class ManualTarget:
    """What the store says about one Scopus record: its DOI and the fields still unavailable."""

    record_id: str
    doi: str | None
    unavailable: tuple[str, ...]


def manual_target(project: Project, record_id: str) -> ManualTarget:
    """Read ``record_id`` from the Layer 1 store.

    Raises:
        ManualEntryRefusal: If it is not an existing ``scopus:`` record.
    """
    if record_id.startswith(SCOPUS_PREFIX):
        connection = connect(project, read_only=True)
        try:
            row = connection.execute(
                "SELECT doi, abstract_outcome, authors_outcome, affiliations_outcome "
                "FROM records WHERE record_id = ?",
                [record_id],
            ).fetchone()
        finally:
            connection.close()
        if row is not None:
            unavailable = tuple(
                f for f, outcome in zip(FIELDS, row[1:], strict=True) if outcome != "value"
            )
            return ManualTarget(record_id, row[0], unavailable)
    raise ManualEntryRefusal(f"{record_id!r} is no scopus record in the store")


def add_manual_entry(
    project: Project,
    *,
    record_id: str,
    field: str,
    value: str,
    reviewer: str,
    note: str | None = None,
    now: Callable[[], datetime] = _utc_now,
    new_id: IdFactory = _default_ids,
) -> ManualEntry:
    """Append a ``manual_entry`` event, or refuse and write nothing (ADR 0041 Decision 4).

    Raises:
        ValidationError: For a blank reviewer or value, an unknown field, or a malformed
            affiliation line.
        ManualEntryRefusal: For a record that is not a ``scopus:`` record, a field that is not
            ``unavailable`` in the store, or an active entry for the key.
    """
    require_reviewer(reviewer)
    check_field_and_value(field, value)
    target = manual_target(project, record_id)
    if field not in target.unavailable:
        raise ManualEntryRefusal(f"{field} of {record_id} is not unavailable in the store")
    event = ManualEntry(
        event_id=new_id(),
        ts=now(),
        project=project.slug,
        reviewer=reviewer,
        record_id=record_id,
        field=cast(ManualField, field),
        value=value,
        doi=target.doi,
        note=note if (note or "").strip() else None,
    )

    def check(existing: Sequence[ManualRecord]) -> None:
        for active in active_events([r.root for r in existing], ManualEntry, ManualReverse):
            if (active.record_id, active.field) == (record_id, field):
                raise ManualEntryRefusal(
                    f"an active manual entry already exists for {record_id} {field} "
                    f"({active.event_id}); reverse it first"
                )

    manual_entries_log(project.manual_entries_path).append_if(ManualRecord(event), check)
    return event


def reverse_manual_entry(
    project: Project,
    *,
    reverses: str,
    reviewer: str,
    reason: str,
    note: str | None = None,
    now: Callable[[], datetime] = _utc_now,
    new_id: IdFactory = _default_ids,
) -> ManualReverse:
    """Append a ``reverse`` event naming an active manual entry, or refuse (nothing deleted)."""
    require_reviewer(reviewer)
    if reason not in MANUAL_REVERSE_REASONS:
        raise ValidationError(
            f"reversal reason {reason!r} is not one of {', '.join(MANUAL_REVERSE_REASONS)}"
        )
    if reason == "other" and not (note or "").strip():
        raise ValidationError("reversal reason 'other' requires a non-empty note")
    event = ManualReverse(
        event_id=new_id(),
        ts=now(),
        project=project.slug,
        reviewer=reviewer,
        reverses=reverses,
        reason=cast(ManualReverseReason, reason),
        note=note if (note or "").strip() else None,
    )
    refusal = ManualEntryRefusal(f"{reverses!r} is not an active manual entry")
    if not project.manual_entries_path.is_file():  # do not create an empty log for a refusal
        raise refusal

    def check(existing: Sequence[ManualRecord]) -> None:
        if reverses not in {
            e.event_id
            for e in active_events([r.root for r in existing], ManualEntry, ManualReverse)
        }:
            raise refusal

    manual_entries_log(project.manual_entries_path).append_if(ManualRecord(event), check)
    return event


__all__ = [
    "FIELDS",
    "MANUAL_REVERSE_REASONS",
    "ManualEntryRefusal",
    "ManualTarget",
    "active_events",
    "add_manual_entry",
    "check_field_and_value",
    "manual_target",
    "require_reviewer",
    "reverse_manual_entry",
]
