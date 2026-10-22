"""The Layer 2 append-only decision log (BUILD_PLAN §2.2 lines 107-118, §Stage 4 lines 970-974).

``decisions.jsonl`` is never mutated, only appended to (BUILD_PLAN line
108: "Screening decisions ... are *events*, never mutations"). Current set
membership is *derived* by folding the log (line 114), not stored anywhere
-- :class:`DecisionLog` is the single place that append happens and the
single place the fold is defined, so every consumer folds the same way.

**Rules this module enforces** (BUILD_PLAN lines 970-974, plus the crash-
and-concurrency contract spelled out for this stage):

1. *Append-only, fsynced per write, checksum-guarded.* Every append opens
   ``decisions.jsonl`` for read/write **in binary mode**, takes an
   exclusive lock, writes exactly one line in one ``write(2)`` call,
   ``fsync``s it, and then rewrites the ``decisions.jsonl.sha256`` sidecar
   (via write-temp-then-``os.replace``, itself ``fsync``d before the
   rename) to cover the file's new content. :meth:`DecisionLog.load` takes
   a *shared* lock and recomputes the same checksum; a mismatch raises
   :class:`LogError` -- this is what makes hand-editing detectable.
2. *The fold key is ``(stage, record_id, reviewer)``, not ``record_id``
   alone.* :func:`fold_events` takes, for each key, the event with the
   greatest ``(ts, event_id)`` -- last by timestamp, ties broken by the
   (monotonic) ``event_id``. A fulltext decision never overwrites a
   title/abstract one because they fold under different keys; one
   reviewer's decision never overwrites another's for the same reason.
3. *``reason_code`` is mandatory for ``exclude``* and must be a member of
   the exclude-reason-code set the project's current ``criteria.yaml``
   declares for that event's stage. Enforced in
   :meth:`DecisionLog._validate_business_rules`, which needs the owning
   :class:`~prismabib.project.Project` for its ``criteria`` -- context
   :class:`~prismabib.prisma.events.DecisionEvent` itself does not have.
4. *An unknown ``schema_version`` raises.* Checked per-event against
   :data:`~prismabib.prisma.events.CURRENT_SCHEMA_VERSION` before the line
   is even handed to Pydantic, so a future schema bump is refused loudly
   rather than silently coerced or ignored.
5. *A truncated final line raises, naming the line number.* See "Crash
   safety" below.
6. *A duplicate ``event_id`` raises.* Checked both while reading an
   existing file (a corrupted or hand-duplicated file) and while appending
   (a replayed append handed the exact same, already-constructed event
   twice).
7. *Appends are line-atomic under two open handles.* The exclusive lock
   around the read-verify-write-checksum sequence serialises two
   concurrent ``DecisionLog`` instances (in the same process or two
   separate ones) so neither can observe, or produce, a torn line or a
   checksum that has fallen out of step with the file it describes.
8. *A reversal is a new event, never an edit.* This module exposes no way
   to delete or rewrite an existing line; :meth:`DecisionLog.append` is the
   only write path, and it only ever adds.

**File locking is platform-specific; the contract is not.** POSIX takes a
whole-file ``fcntl.flock``; Windows has no such call, so
:class:`_WindowsLockBackend` drives ``msvcrt.locking`` instead. Both are
reached through :class:`_LockBackend`, both are selected on
:data:`sys.platform`, and neither module is imported until the platform
that has it asks for it -- a module-level ``import fcntl`` made this whole
file unimportable on Windows, which meant a Windows reviewer could capture
and build a store but could not record one screening decision. ADR 0010
records the one behavioural deviation: Windows has no shared byte-range
lock, so :meth:`DecisionLog.load`'s *shared* lock degrades to an exclusive
one there (never weaker -- only less concurrent).

**The bytes on disk are LF, on every platform.** Both ``os.open`` calls
here add ``O_BINARY`` where the platform has it. Without it the Windows C
runtime rewrites every ``\\n`` this module writes as ``\\r\\n`` and hides
the change again on read, so the process that wrote the log would still
agree with its own sidecar while an external ``sha256sum`` would not. The
sidecar is deliberately byte-identical to ``sha256sum decisions.jsonl``
output (it is what the truncated-line recovery instructions tell the user
to run), and a tamper-detection digest that no outside tool can confirm is
worth less than none.

**Crash safety.** A process killed mid-append can only be caught between
two of this module's steps:

- *Before the line's ``write(2)`` returns:* the file on disk is exactly as
  it was before the append -- nothing to detect, nothing lost.
- *After the line is written but before it is ``fsync``d, or the OS/disk
  itself only persists part of the bytes:* the file's tail is a partial
  JSON object with no terminating ``\\n``. :meth:`DecisionLog.load` detects
  this structurally (the file's confirmed content -- everything up to and
  including the last complete, newline-terminated line -- still matches
  the sidecar checksum, but bytes remain after it) and raises
  :class:`LogError` naming the 1-based line number of the truncated line,
  rather than trying to interpret or discard the partial write silently.
  Because the confirmed prefix still checksums correctly, this case is
  distinguishable from case below and reported with a distinct, more
  actionable message.
- *After the line is durably written but before the checksum sidecar is
  rewritten:* the sidecar under-counts the file's true content. This is
  reported the same way as any other checksum mismatch (case 1's rule) --
  deliberately not special-cased, since from a reader's perspective "the
  sidecar does not match the file" is exactly what it is, whether the
  cause was hand-editing or an interrupted append; recovery is manual
  either way (verify the trailing line, then rewrite the sidecar).

Both partial-write scenarios are also why :meth:`DecisionLog.append` always
re-verifies the whole file (locked) before adding a new line: it refuses to
append on top of an already-inconsistent log rather than compounding the
corruption.
"""

from __future__ import annotations

from collections.abc import Iterable
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError as PydanticValidationError

from prismabib.errors import LogError, ValidationError

# The private names are re-exported so existing importers (tests) keep working
# after ADR 0029 section 8 moved the implementation to `prismabib.log`.
from prismabib.log import (
    _LOCK_BACKEND,  # noqa: F401
    AppendOnlyLog,
    LockKind,
    _ByteRangeLocking,  # noqa: F401
    _LockBackend,  # noqa: F401
    _PosixLockBackend,  # noqa: F401
    _select_lock_backend,  # noqa: F401
    _WindowsLockBackend,  # noqa: F401
)
from prismabib.prisma.events import (
    CURRENT_SCHEMA_VERSION,
    Decision,
    DecisionEvent,
    IdFactory,
    MonotonicUlidFactory,
)
from prismabib.project import Project
from prismabib.stage import PrismaStage

#: The fold key BUILD_PLAN pins events to: stage, record, and reviewer
#: together -- not ``record_id`` alone (ADR 0002). A later event for the
#: same key supersedes an earlier one; different keys never interact.
FoldKey = tuple[PrismaStage, str, str]


def fold_events(events: Iterable[DecisionEvent]) -> dict[FoldKey, DecisionEvent]:
    """Fold a sequence of decision events into current membership by key.

    For each ``(stage, record_id, reviewer)`` key, keeps the event with the
    greatest ``(ts, event_id)`` pair -- the most recent decision, ties
    (same millisecond) broken by the lexicographically greatest, and
    therefore most recently minted, monotonic ``event_id``.

    Args:
        events: Decision events in any order. Because the fold compares
            ``(ts, event_id)`` for every event against the current winner
            for its key, the result does not depend on the iteration
            order: permuting ``events`` yields an identical mapping.

    Returns:
        A mapping from fold key to that key's winning event. A key whose
        only events are all ``"unsure"`` maps to an ``"unsure"`` event --
        folding never resolves a decision into ``"include"``; it only ever
        reports the latest decision actually logged, whatever it was.
    """
    latest: dict[FoldKey, DecisionEvent] = {}
    for event in events:
        key: FoldKey = (event.stage, event.record_id, event.reviewer)
        current = latest.get(key)
        if current is None or (event.ts, event.event_id) > (current.ts, current.event_id):
            latest[key] = event
    return latest


class DecisionLog:
    """The append-only, checksum-guarded ``decisions.jsonl`` for one project.

    See the module docstring for the full set of invariants this class
    enforces -- now implemented by the generic :class:`AppendOnlyLog` this
    class composes (see that class's own docstring). What stays here is
    what is specific to a *screening decision*: the
    ``(stage, record_id, reviewer)`` fold key (:func:`fold_events`) and the
    ``reason_code``/``criteria.yaml`` business rule
    (:meth:`_validate_business_rules`). Every public method that touches the
    file takes the appropriate ``flock`` for its whole read-or-write
    critical section (via :attr:`_store`), so two ``DecisionLog`` instances
    -- in one process or two -- never interleave.
    """

    def __init__(self, project: Project, *, id_factory: IdFactory | None = None) -> None:
        """Open a decision log bound to ``project``.

        Args:
            project: The owning project. Supplies both the log's path
                (:attr:`~prismabib.project.Project.decisions_path`) and,
                via :attr:`~prismabib.project.Project.criteria`, the
                per-stage exclude-reason-code sets that
                :meth:`append` validates against.
            id_factory: Generates each new event's ``event_id``. Defaults
                to a fresh :class:`~prismabib.prisma.events.MonotonicUlidFactory`.
                Tests substitute a seeded, deterministic
                :class:`~prismabib.prisma.events.IdFactory` here.
        """
        self._project = project
        self._id_factory: IdFactory = (
            id_factory if id_factory is not None else MonotonicUlidFactory()
        )
        self._store: AppendOnlyLog[DecisionEvent] = AppendOnlyLog(
            project.decisions_path,
            model=DecisionEvent,
            current_schema_version=CURRENT_SCHEMA_VERSION,
            event_id=lambda event: event.event_id,
            noun="decision",
            event_description="screening decision",
            recovery_hint="the UI or DecisionLog.append",
            writer_name="DecisionLog",
            labour="human screening",
        )

    @property
    def path(self) -> Path:
        """The ``decisions.jsonl`` path this log reads and appends to."""
        return self._store.path

    @property
    def checksum_path(self) -> Path:
        """The ``decisions.jsonl.sha256`` sidecar path this log maintains."""
        return self._store.checksum_path

    # -- reading -----------------------------------------------------------

    def load(self) -> list[DecisionEvent]:
        """Read and validate every event currently in the log.

        Returns:
            Every event, in file order (oldest first). Callers that want
            current membership should pass this to :func:`fold_events`.

        Raises:
            LogError: If the checksum sidecar does not match the file's
                confirmed content, the final line is truncated, any line
                declares an unknown ``schema_version``, any line fails to
                parse as a well-formed :class:`~prismabib.prisma.events.DecisionEvent`,
                or the same ``event_id`` appears twice.
        """
        return self._store.load()

    def fold(self) -> dict[FoldKey, DecisionEvent]:
        """Load the log and fold it into current per-key membership.

        Returns:
            :func:`fold_events` applied to :meth:`load`'s result.

        Raises:
            LogError: Anything :meth:`load` raises.
        """
        return fold_events(self.load())

    # -- writing -------------------------------------------------------------

    def append(
        self,
        *,
        stage: PrismaStage,
        record_id: str,
        reviewer: str,
        decision: Decision,
        reason_code: str | None = None,
        note: str = "",
        criteria_version: str | None = None,
    ) -> DecisionEvent:
        """Construct and append a new decision event.

        Args:
            stage: Which screening stage this decision belongs to. Must be
                :attr:`~prismabib.stage.PrismaStage.TITLE_ABSTRACT` or
                :attr:`~prismabib.stage.PrismaStage.FULLTEXT`.
            record_id: The bibliographic record this decision applies to.
            reviewer: The reviewer's identifier.
            decision: One of ``"include"``, ``"exclude"``, ``"unsure"``.
            reason_code: Required when ``decision == "exclude"``; must then
                be a member of the current ``criteria.yaml``'s
                exclude-reason-code set for ``stage``. Ignored for other
                decisions.
            note: A free-text annotation. Defaults to ``""``.
            criteria_version: The ``criteria.yaml`` version this decision
                was made under. Defaults to the project's current
                ``criteria.version`` when omitted -- pass this explicitly
                only when replaying/backfilling decisions made under a
                superseded version (BUILD_PLAN line 118).

        Returns:
            The appended :class:`~prismabib.prisma.events.DecisionEvent`,
            including its generated ``event_id`` and ``ts``.

        Raises:
            ValidationError: If the constructed event fails schema
                validation (BUILD_PLAN's decision-event shape).
            LogError: If ``reason_code`` is missing or not declared for
                ``stage`` when ``decision == "exclude"``, if the resulting
                ``event_id`` already exists in the log, or if the existing
                log fails any of :meth:`load`'s checks.
        """
        resolved_criteria_version = (
            criteria_version if criteria_version is not None else self._project.criteria.version
        )
        try:
            event = DecisionEvent(
                event_id=self._id_factory(),
                schema_version=CURRENT_SCHEMA_VERSION,
                ts=datetime.now(UTC),
                project=self._project.slug,
                stage=stage,
                record_id=record_id,
                reviewer=reviewer,
                decision=decision,
                reason_code=reason_code,
                note=note,
                criteria_version=resolved_criteria_version,
            )
        except PydanticValidationError as exc:
            raise ValidationError(f"invalid decision event: {exc}") from exc
        self.append_event(event)
        return event

    def append_event(self, event: DecisionEvent) -> None:
        """Append an already-constructed event, enforcing every log invariant.

        The lower-level counterpart to :meth:`append`: takes a fully-formed
        :class:`~prismabib.prisma.events.DecisionEvent` (its ``event_id``
        already fixed) rather than building one, so a caller -- or a test
        -- can append the exact same event object twice to exercise the
        duplicate-``event_id`` rule.

        Args:
            event: The event to append.

        Raises:
            LogError: If ``reason_code`` is missing or not declared for
                ``event.stage`` when ``event.decision == "exclude"``, if
                ``event.event_id`` already exists in the log (a replayed
                append or ULID collision), or if the existing log fails
                any of :meth:`load`'s checks.
        """
        self._validate_business_rules(event)
        self._store.write_event(event)

    def _locked(self, kind: LockKind) -> AbstractContextManager[int]:
        """Forward to the underlying :class:`AppendOnlyLog`'s critical section.

        Kept as a method on :class:`DecisionLog` -- rather than requiring
        every caller to reach through :attr:`_store` -- because it is
        exercised directly by ``tests/integration/prisma/test_log.py``'s
        nested-lock reentrancy test, which predates this class's
        composition-based refactor and is unchanged by it.

        Args:
            kind: ``"shared"`` or ``"exclusive"``.

        Returns:
            The context manager :meth:`AppendOnlyLog._locked` produces.
        """
        return self._store._locked(kind)

    # -- business rules ------------------------------------------------------

    def _validate_business_rules(self, event: DecisionEvent) -> None:
        """Enforce the ``reason_code``/``criteria.yaml`` rule for ``exclude``.

        Args:
            event: The candidate event, not yet written.

        Raises:
            LogError: If ``event.decision == "exclude"`` and either
                ``event.reason_code`` is missing, or is not a member of the
                current ``criteria.yaml``'s exclude-reason-code set for
                ``event.stage``.
        """
        if event.decision != "exclude":
            return
        if not event.reason_code:
            # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
            raise LogError(
                "reason_code is required when decision='exclude' "
                f"(stage={event.stage.value!r}, record_id={event.record_id!r}, "
                f"reviewer={event.reviewer!r})"
            )
            # pragma: no mutate end
        allowed = self._exclude_reason_codes(event.stage)
        if event.reason_code not in allowed:
            # Computed above the pragma, not interpolated inside it -- see the note in
            # `criteria._run_git`.
            declared = sorted(allowed)
            # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
            raise LogError(
                f"reason_code {event.reason_code!r} is not declared in criteria.yaml's "
                f"{event.stage.value} exclude_reason_codes {declared!r} "
                f"(criteria_version={event.criteria_version!r})"
            )
            # pragma: no mutate end

    def _exclude_reason_codes(self, stage: PrismaStage) -> frozenset[str]:
        """Look up the current criteria's exclude-reason-code set for ``stage``.

        Args:
            stage: :attr:`~prismabib.stage.PrismaStage.TITLE_ABSTRACT` or
                :attr:`~prismabib.stage.PrismaStage.FULLTEXT`.

        Returns:
            The project's current ``criteria.yaml`` ``manual_abstract``
            (for ``TITLE_ABSTRACT``) or ``manual_fulltext`` (for
            ``FULLTEXT``) ``exclude_reason_codes``, as a set.
        """
        criteria = self._project.criteria
        codes = (
            criteria.manual_abstract.exclude_reason_codes
            if stage is PrismaStage.TITLE_ABSTRACT
            else criteria.manual_fulltext.exclude_reason_codes
        )
        return frozenset(codes)


__all__ = ["AppendOnlyLog", "DecisionLog", "FoldKey", "fold_events"]
