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

import errno
import hashlib
import importlib
import json
import os
import random
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Literal, Protocol

from pydantic import ValidationError as PydanticValidationError

from prismabib.errors import LogError, ValidationError
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

_READ_CHUNK_SIZE = 65536

#: ``os.O_BINARY`` where the platform defines it (Windows only), ``0``
#: everywhere else -- ``0`` is the identity for ``|`` in an ``os.open`` flag
#: word, so the POSIX call is bit-for-bit what it always was. Without this
#: flag the Windows CRT translates ``\n`` to ``\r\n`` on the way out and back
#: again on the way in, which is invisible in-process and fatal to a sidecar
#: that is supposed to match ``sha256sum``.
_O_BINARY = getattr(os, "O_BINARY", 0)

LockKind = Literal["shared", "exclusive"]
"""What a critical section needs: ``"shared"`` to read, ``"exclusive"`` to write.

Deliberately not an ``fcntl`` constant. ``_locked(fcntl.LOCK_SH)`` names one
platform's API in the signature of code that has to run on two, and there is
no ``fcntl`` at all on the platform this abstraction exists for.
"""

#: The byte range :class:`_WindowsLockBackend` locks, chosen far past any
#: plausible end of file. Two properties follow, and both are load-bearing.
#: The range never moves as ``decisions.jsonl`` grows, so a lock taken when
#: the file was 200 bytes long still collides with one taken at 2 MB. And it
#: covers no *data*: Windows byte-range locks are mandatory, not advisory, so
#: locking byte 0 would make an ordinary reader's ``read()`` fail rather than
#: merely wait -- including the byte-level readers this project's own tests
#: use to check the log from outside.
_SENTINEL_LOCK_OFFSET = 0x7FFF_FFFF
_SENTINEL_LOCK_LENGTH = 1

#: How long :class:`_WindowsLockBackend` keeps retrying before it gives up and
#: raises. POSIX ``flock`` blocks indefinitely instead; that asymmetry is
#: forced (see :class:`_WindowsLockBackend`) and recorded in ADR 0010.
_LOCK_TIMEOUT_SECONDS = 10.0

#: The first retry's nominal wait, doubled on each attempt up to
#: :data:`_LOCK_MAX_RETRY_SECONDS` and then jittered. Jitter matters because
#: the contending processes here are typically two notebook kernels started
#: from the same script: without it they retry in lockstep forever.
_LOCK_FIRST_RETRY_SECONDS = 0.01
_LOCK_MAX_RETRY_SECONDS = 0.25


class _LockBackend(Protocol):
    """One platform's whole-file advisory lock, as this module needs it.

    Implementations must guarantee, for locks taken through *any* handle in
    *any* process on the same file:

    * ``"exclusive"`` excludes ``"exclusive"``;
    * ``"shared"`` excludes ``"exclusive"`` (whether it also excludes
      ``"shared"`` is the one permitted difference between backends);
    * :meth:`release` leaves the file unlocked;
    * the caller's file position is what it was, after both calls;
    * an :meth:`acquire` that raises leaves no lock behind;
    * :meth:`acquire` on a descriptor this backend already holds raises
      rather than blocking, upgrading, or silently succeeding.
    """

    def acquire(self, fd: int, kind: LockKind, path: Path) -> None:
        """Take a ``kind`` lock on ``fd``, blocking until it is granted.

        Args:
            fd: The open descriptor to lock.
            kind: ``"shared"`` or ``"exclusive"``.
            path: The file ``fd`` refers to, for error messages only.

        Raises:
            LogError: If the lock cannot be taken.
        """
        ...

    def release(self, fd: int) -> None:
        """Release the lock :meth:`acquire` took on ``fd``.

        Args:
            fd: The descriptor whose lock to drop.
        """
        ...


def _refuse_reentrant_lock(held: frozenset[int] | set[int], fd: int, path: Path) -> None:
    """Refuse a second lock on a descriptor this backend already holds.

    POSIX ``flock`` would quietly re-apply (or convert) the lock;
    ``msvcrt.locking`` fails on the already-locked region. Rather than let
    that difference be discovered on Windows, both backends refuse here, so
    the mistake reads the same on every platform.

    Args:
        held: The descriptors this backend currently holds a lock on.
        fd: The descriptor being locked.
        path: The file ``fd`` refers to, for the message.

    Raises:
        LogError: If ``fd`` is already in ``held``.
    """
    if fd in held:
        # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
        raise LogError(
            f"{path}: the decision-log lock is not re-entrant -- descriptor {fd} already "
            "holds it. Take one lock per critical section; nesting them deadlocks on "
            "POSIX and fails outright on Windows."
        )
        # pragma: no mutate end


class _PosixLockBackend:
    """``fcntl.flock`` over the whole file: the original, unchanged behaviour.

    ``flock`` blocks until the lock is granted, with no timeout and no retry
    loop, which is what every existing caller has always got on Linux and
    macOS. The only addition is the shared non-reentrancy check.
    """

    def __init__(self) -> None:
        self._held: set[int] = set()

    def acquire(self, fd: int, kind: LockKind, path: Path) -> None:
        """Take a blocking ``flock`` of ``kind`` on ``fd``.

        Args:
            fd: The open descriptor to lock.
            kind: ``"shared"`` (``LOCK_SH``) or ``"exclusive"`` (``LOCK_EX``).
            path: The file ``fd`` refers to, for error messages only.

        Raises:
            LogError: If ``fd`` already holds this backend's lock.
        """
        import fcntl

        _refuse_reentrant_lock(self._held, fd, path)
        fcntl.flock(fd, fcntl.LOCK_SH if kind == "shared" else fcntl.LOCK_EX)
        self._held.add(fd)

    def release(self, fd: int) -> None:
        """Drop ``fd``'s ``flock``.

        Args:
            fd: The descriptor whose lock to drop.
        """
        import fcntl

        self._held.discard(fd)
        fcntl.flock(fd, fcntl.LOCK_UN)


class _LockingCall(Protocol):
    """``msvcrt.locking``'s signature: lock ``nbytes`` at ``fd``'s position."""

    def __call__(self, fd: int, mode: int, nbytes: int, /) -> None:
        """Lock, or unlock, a byte range of ``fd``.

        Args:
            fd: The descriptor to lock.
            mode: One of the ``LK_*`` mode constants.
            nbytes: How many bytes, starting at ``fd``'s current position.
        """
        ...


@dataclass(frozen=True)
class _ByteRangeLocking:
    """The three names :class:`_WindowsLockBackend` needs from ``msvcrt``.

    Bundled into an injectable value rather than reached for as a global so
    the backend's logic -- the retry loop, the seek dance, the error
    translation -- can be exercised on a machine that has no ``msvcrt``.

    Attributes:
        locking: ``msvcrt.locking``.
        nonblocking_exclusive: ``msvcrt.LK_NBLCK``. The *blocking* mode
            (``LK_LOCK``) is never used; see :class:`_WindowsLockBackend`.
        unlock: ``msvcrt.LK_UNLCK``.
    """

    locking: _LockingCall
    nonblocking_exclusive: int
    unlock: int

    @classmethod
    def from_module(cls, module: ModuleType) -> _ByteRangeLocking:
        """Read the three names off an ``msvcrt``-shaped module.

        Args:
            module: ``msvcrt``, or a stand-in exposing the same three names.

        Returns:
            The bundled primitive.
        """
        return cls(
            locking=module.locking,
            nonblocking_exclusive=module.LK_NBLCK,
            unlock=module.LK_UNLCK,
        )


class _WindowsLockBackend:
    """``msvcrt.locking`` on a sentinel byte range, with our own retry loop.

    Four differences from ``flock`` shape this class, and each is answered
    here rather than left to surprise a Windows user (ADR 0010):

    1. **The lock is a byte range, not a file**, and it is taken at the
       descriptor's *current position*, which the call then moves. So every
       lock and unlock seeks to :data:`_SENTINEL_LOCK_OFFSET` and puts the
       caller's position back, including when the attempt fails.
    2. **There is no shared mode.** ``LK_RLCK`` is documented as identical
       to ``LK_LOCK``. A ``"shared"`` request is therefore satisfied with an
       exclusive lock: never weaker than asked for, only less concurrent.
    3. **``LK_LOCK`` is not usable as a blocking mode.** It retries ten
       times at one-second intervals and then raises -- an unconfigurable
       ten-second ceiling reported as a bare ``OSError``. This class uses
       the non-blocking mode and does its own jittered backoff to an
       explicit deadline, so exhausting it raises a :class:`LogError` that
       names the file and how long it waited.
    4. **Re-locking a region from the same handle fails**, where POSIX
       quietly succeeds; :func:`_refuse_reentrant_lock` makes that failure
       uniform instead.

    Args:
        byte_range_locking: The ``msvcrt`` primitive to drive.
        timeout: How long :meth:`acquire` retries before raising.
        sleep: Injected blocking primitive, defaulting to ``time.sleep``.
        monotonic: Injected clock, defaulting to ``time.monotonic``.
            Monotonic, not wall-clock: a deadline must not move because the
            machine's clock was corrected mid-wait.
        jitter: Injected source of the ``[0, 1)`` jitter factor, defaulting
            to ``random.random``.
    """

    def __init__(
        self,
        byte_range_locking: _ByteRangeLocking,
        *,
        timeout: float = _LOCK_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self._locking = byte_range_locking
        self._timeout = timeout
        self._sleep = sleep
        self._monotonic = monotonic
        self._jitter = jitter
        self._held: set[int] = set()

    def acquire(self, fd: int, kind: LockKind, path: Path) -> None:
        """Take an exclusive sentinel-range lock on ``fd``, retrying until granted.

        Args:
            fd: The open descriptor to lock.
            kind: ``"shared"`` or ``"exclusive"``. Both take an exclusive
                lock here; the argument is used only in the error message,
                so that a failure says which one the caller asked for.
            path: The file ``fd`` refers to, for the error message.

        Raises:
            LogError: If ``fd`` already holds this backend's lock, or if the
                lock is still held by someone else when ``timeout`` expires.
            OSError: Anything ``msvcrt.locking`` raises that is not the
                "region already locked" ``EACCES`` -- a bad descriptor is a
                bug here, not contention, and must not be retried for ten
                seconds and then reported as a busy file.
        """
        _refuse_reentrant_lock(self._held, fd, path)
        deadline = self._monotonic() + self._timeout
        wait = _LOCK_FIRST_RETRY_SECONDS
        while True:
            try:
                self._at_sentinel(fd, self._locking.nonblocking_exclusive)
            except OSError as exc:
                if exc.errno != errno.EACCES:
                    raise
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
                    raise LogError(
                        f"{path}: could not take the {kind} lock after waiting "
                        f"{self._timeout:g}s -- another process still holds it. "
                        "Close any other prismabib session (a second notebook kernel, "
                        "a CLI run, an editor plugin) that has this project open, then "
                        "retry. No decision has been written."
                    ) from exc
                    # pragma: no mutate end
                self._sleep(min(wait * (0.5 + self._jitter()), remaining))
                wait = min(wait * 2.0, _LOCK_MAX_RETRY_SECONDS)
            else:
                self._held.add(fd)
                return

    def release(self, fd: int) -> None:
        """Unlock the same sentinel range :meth:`acquire` locked.

        ``msvcrt`` requires the unlock to name the region exactly, which is
        the other reason the offset and length are fixed constants.

        Args:
            fd: The descriptor whose lock to drop.
        """
        self._held.discard(fd)
        self._at_sentinel(fd, self._locking.unlock)

    def _at_sentinel(self, fd: int, mode: int) -> None:
        """Run one ``locking`` call over the sentinel range, restoring the position.

        Args:
            fd: The descriptor to operate on.
            mode: ``LK_NBLCK`` or ``LK_UNLCK``.

        Raises:
            OSError: Whatever ``locking`` raises. The caller's file position
                is restored first: an append that had seeked to the end must
                not silently resume from wherever a failed lock attempt left
                the descriptor.
        """
        position = os.lseek(fd, 0, os.SEEK_CUR)
        try:
            os.lseek(fd, _SENTINEL_LOCK_OFFSET, os.SEEK_SET)
            self._locking.locking(fd, mode, _SENTINEL_LOCK_LENGTH)
        finally:
            os.lseek(fd, position, os.SEEK_SET)


def _select_lock_backend(
    platform: str = sys.platform,
    *,
    load_module: Callable[[str], ModuleType] = importlib.import_module,
) -> _LockBackend:
    """Pick the lock backend for ``platform``, importing only what it needs.

    The import is *inside* this function on purpose. A module-level
    ``import fcntl`` is what made this module fail to import on Windows, and
    a module-level ``import msvcrt`` would fail the same way everywhere
    else.

    Args:
        platform: A :data:`sys.platform` value. Defaults to the running
            interpreter's; passed explicitly by tests, which is the only way
            the Windows arm can be reached on a POSIX machine.
        load_module: How to import the platform module by name. Injected so
            that the Windows arm can be selected without an ``msvcrt`` to
            import.

    Returns:
        A :class:`_WindowsLockBackend` on ``"win32"``, otherwise a
        :class:`_PosixLockBackend`.
    """
    if platform == "win32":
        return _WindowsLockBackend(_ByteRangeLocking.from_module(load_module("msvcrt")))
    return _PosixLockBackend()


#: Chosen once, at import, for the running platform. Shared by every
#: :class:`DecisionLog`: the non-reentrancy bookkeeping is per *descriptor*,
#: and two logs in two threads never share one.
_LOCK_BACKEND: _LockBackend = _select_lock_backend()


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
    enforces. Every public method that touches the file takes the
    appropriate ``flock`` for its whole read-or-write critical section, so
    two ``DecisionLog`` instances -- in one process or two -- never
    interleave.
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
        self._path = project.decisions_path
        self._checksum_path = self._path.with_name(self._path.name + ".sha256")
        self._backend: _LockBackend = _LOCK_BACKEND
        self._lock_held = False

    @property
    def path(self) -> Path:
        """The ``decisions.jsonl`` path this log reads and appends to."""
        return self._path

    @property
    def checksum_path(self) -> Path:
        """The ``decisions.jsonl.sha256`` sidecar path this log maintains."""
        return self._checksum_path

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
        with self._locked("shared") as fd:
            _confirmed, events = self._verify_and_load_locked(fd)
        return events

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
        with self._locked("exclusive") as fd:
            confirmed, existing = self._verify_and_load_locked(fd)
            if event.event_id in {existing_event.event_id for existing_event in existing}:
                # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
                raise LogError(
                    f"{self._path}: duplicate event_id {event.event_id!r} -- already present "
                    "in the decision log (replayed append or ULID collision)"
                )
                # pragma: no mutate end
            line_bytes = (event.model_dump_json() + "\n").encode("utf-8")
            os.lseek(fd, 0, os.SEEK_END)
            os.write(fd, line_bytes)
            os.fsync(fd)
            self._write_checksum_sidecar(confirmed + line_bytes)

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
            # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
            raise LogError(
                f"reason_code {event.reason_code!r} is not declared in criteria.yaml's "
                f"{event.stage.value} exclude_reason_codes {sorted(allowed)!r} "
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

    # -- locking and low-level I/O --------------------------------------------

    @contextmanager
    def _locked(self, kind: LockKind) -> Iterator[int]:
        """Open :attr:`_path`, hold a lock on it, and yield its file descriptor.

        The parent directory is created and the file opened read/write with
        ``O_CREAT``, so a :class:`DecisionLog` works even before
        :meth:`~prismabib.project.Project.init` has run. Creating the
        directory is not redundant with ``init``: git cannot store an empty
        directory, so a project cloned with ``track_decisions = false``
        (§2.5 line 291) arrives without ``decisions/``, and the first
        screening decision would otherwise die on ``FileNotFoundError``.

        ``O_BINARY`` is what keeps the file's bytes LF-terminated on
        Windows; see the module docstring. It is ``0`` on POSIX.

        **This is not re-entrant.** Each call opens a *new* descriptor, so a
        nested call would ask the OS for a second, conflicting lock on the
        same file from the same thread -- which blocks forever on POSIX and
        fails on Windows. Nothing in this class nests today; the guard makes
        sure that stays true and reports it as an error rather than a hang.

        Args:
            kind: ``"shared"`` for a read (allows concurrent readers,
                excludes writers -- except on Windows, which has no shared
                mode and takes an exclusive lock instead) or ``"exclusive"``
                for a write. Held for the caller's entire critical section,
                so a concurrent reader can never observe a write
                half-applied, and two concurrent writers can never
                interleave.

        Yields:
            The open file descriptor, positioned at its start.

        Raises:
            LogError: If this :class:`DecisionLog` is already inside a
                locked section, or if the lock cannot be taken.
        """
        if self._lock_held:
            # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
            raise LogError(
                f"{self._path}: DecisionLog._locked is not re-entrant -- a "
                f"{kind} lock was requested while this log already holds one. "
                "One lock per critical section."
            )
            # pragma: no mutate end
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT | _O_BINARY, 0o644)
        self._lock_held = True
        try:
            self._backend.acquire(fd, kind, self._path)
            try:
                yield fd
            finally:
                self._backend.release(fd)
        finally:
            self._lock_held = False
            os.close(fd)

    def _read_all(self, fd: int) -> bytes:
        """Read an open file descriptor's entire content from its current position.

        Args:
            fd: An open file descriptor, positioned wherever the caller
                wants reading to start (:meth:`_verify_and_load_locked`
                seeks to the start first).

        Returns:
            Every byte read until EOF.
        """
        chunks: list[bytes] = []
        while True:
            chunk = os.read(fd, _READ_CHUNK_SIZE)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    @staticmethod
    def _split_confirmed(raw: bytes) -> tuple[bytes, bytes]:
        """Split raw file bytes into complete lines and a trailing partial line.

        Args:
            raw: The file's full current content.

        Returns:
            ``(confirmed, fragment)``: ``confirmed`` is every complete,
            newline-terminated line (``raw`` unchanged if it is empty or
            already ends with ``\\n``); ``fragment`` is whatever bytes
            follow the last ``\\n``, or ``b""`` if there is nothing
            unterminated.
        """
        if not raw or raw.endswith(b"\n"):
            return raw, b""
        split_at = raw.rfind(b"\n") + 1
        return raw[:split_at], raw[split_at:]

    def _verify_checksum_bytes(self, confirmed: bytes) -> None:
        """Verify ``confirmed`` against the checksum sidecar.

        Args:
            confirmed: The file's complete-lines-only content (see
                :meth:`_split_confirmed`).

        Raises:
            LogError: If the sidecar is missing while ``confirmed`` is
                non-empty (an unprotected, untrusted log), or if its
                recorded digest does not match ``confirmed``'s actual
                SHA-256.
        """
        expected = hashlib.sha256(confirmed).hexdigest()
        if not self._checksum_path.is_file():
            if confirmed:
                # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
                raise LogError(
                    f"missing checksum sidecar {self._checksum_path} for a non-empty "
                    f"decision log -- {self._path} may have been created or edited "
                    "outside DecisionLog"
                )
                # pragma: no mutate end
            return
        recorded_text = self._checksum_path.read_text(encoding="utf-8").strip()
        recorded = recorded_text.split(maxsplit=1)[0] if recorded_text else ""
        if recorded == expected:
            return

        # Before calling it tampering, check whether the sidecar describes a *prefix*
        # of the current file. That is the signature of a crash between the durable
        # append and the sidecar rewrite -- the one unavoidable window in this
        # two-step write -- and it is not hand-editing.
        #
        # The distinction is worth the code. This file holds screening decisions that
        # cannot be regenerated: a reviewer who loses power partway through 1,500
        # records and is then told the log "may have been edited by hand" has been
        # told something false about their own work, and the plausible response to
        # that message is to distrust and discard it. Both cases still RAISE -- the
        # log is genuinely inconsistent either way and a human must look -- but the
        # diagnosis names what actually happened.
        uncovered = self._lines_after_checksummed_prefix(confirmed, recorded)
        if uncovered is not None:
            # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
            raise LogError(
                f"{self._path} has {uncovered} decision line(s) not covered by the "
                f"checksum sidecar {self._checksum_path}. The sidecar matches this "
                "file's earlier content exactly, which is what an interrupted append "
                "looks like (the line reached disk; the sidecar rewrite did not) -- "
                "not hand-editing. Inspect the trailing line(s); if they are decisions "
                "you intended, the log is intact and only the sidecar needs rewriting."
            )
            # pragma: no mutate end

        # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
        raise LogError(
            f"checksum mismatch for {self._path}: sidecar {self._checksum_path} records "
            f"{recorded!r}, but content hashes to {expected!r} -- decisions.jsonl may "
            "have been edited by hand"
        )
        # pragma: no mutate end

    def _lines_after_checksummed_prefix(self, confirmed: bytes, recorded: str) -> int | None:
        """How many trailing lines lie beyond the prefix the sidecar checksums.

        Args:
            confirmed: The file's complete-lines-only content.
            recorded: The digest the sidecar records.

        Returns:
            The number of whole lines present in ``confirmed`` but not covered by
            ``recorded``, when ``recorded`` matches some line-aligned prefix of
            ``confirmed``; otherwise ``None``, meaning the sidecar does not describe
            any prefix of this file and the difference is not an interrupted append.
        """
        lines = confirmed.splitlines(keepends=True)
        prefix = b""
        for index, line in enumerate(lines):
            if hashlib.sha256(prefix).hexdigest() == recorded:
                return len(lines) - index
            prefix += line
        return None

    def _write_checksum_sidecar(self, content: bytes) -> None:
        """Atomically rewrite the checksum sidecar to describe ``content``.

        Writes to a temporary file in the same directory, ``fsync``s it,
        then ``os.replace``s it over :attr:`_checksum_path` -- a reader can
        therefore only ever see the old sidecar or the fully-written new
        one, never a partial one.

        The sidecar's own bytes are written binary for the same reason the
        log's are: its one line must be exactly what ``sha256sum`` writes,
        or ``sha256sum --check`` on it stops being a thing a reviewer can
        run.

        Args:
            content: The exact bytes the sidecar should describe -- the
                decision log's full content after the append that
                triggered this call.
        """
        digest = hashlib.sha256(content).hexdigest()
        payload = f"{digest}  {self._path.name}\n".encode()
        tmp_path = self._checksum_path.with_name(self._checksum_path.name + ".tmp")
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | _O_BINARY, 0o644)
        try:
            os.write(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, self._checksum_path)

    def _parse_events(self, confirmed: bytes) -> list[DecisionEvent]:
        """Parse every complete line of ``confirmed`` into a validated event.

        Args:
            confirmed: The file's complete-lines-only content, already
                checksum-verified by the caller.

        Returns:
            One :class:`~prismabib.prisma.events.DecisionEvent` per line,
            in file order.

        Raises:
            LogError: If any line declares a ``schema_version`` other than
                :data:`~prismabib.prisma.events.CURRENT_SCHEMA_VERSION`,
                fails to parse as valid JSON, fails
                :class:`~prismabib.prisma.events.DecisionEvent` validation,
                or repeats an ``event_id`` already seen earlier in the
                file.
        """
        events: list[DecisionEvent] = []
        seen_ids: set[str] = set()
        lines = confirmed.decode("utf-8").split("\n")[:-1]
        for line_number, raw_line in enumerate(lines, start=1):
            try:
                payload = json.loads(raw_line)
            except json.JSONDecodeError as exc:
                raise LogError(f"{self._path}:{line_number}: malformed JSON: {exc}") from exc
            schema_version = payload.get("schema_version")
            if schema_version != CURRENT_SCHEMA_VERSION:
                # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
                raise LogError(
                    f"{self._path}:{line_number}: unknown schema_version "
                    f"{schema_version!r} (expected {CURRENT_SCHEMA_VERSION})"
                )
                # pragma: no mutate end
            try:
                event = DecisionEvent.model_validate(payload)
            except PydanticValidationError as exc:
                # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
                raise LogError(
                    f"{self._path}:{line_number}: malformed decision event: {exc}"
                ) from exc
                # pragma: no mutate end
            if event.event_id in seen_ids:
                raise LogError(f"{self._path}:{line_number}: duplicate event_id {event.event_id!r}")
            seen_ids.add(event.event_id)
            events.append(event)
        return events

    def _verify_and_load_locked(self, fd: int) -> tuple[bytes, list[DecisionEvent]]:
        """Read, checksum-verify, and parse the file behind an already-held lock.

        Args:
            fd: An open file descriptor for :attr:`_path`, with the
                caller already holding the appropriate ``flock``.

        Returns:
            ``(confirmed, events)`` -- the file's checksum-verified,
            complete-lines-only content and its parsed events, in file
            order. Callers that are about to append reuse ``confirmed`` as
            the prefix for the new checksum.

        Raises:
            LogError: If the checksum does not match, any event fails
                validation, a duplicate ``event_id`` is found, or the
                file's final line is truncated (no terminating ``\\n``).
        """
        os.lseek(fd, 0, os.SEEK_SET)
        raw = self._read_all(fd)
        confirmed, fragment = self._split_confirmed(raw)
        self._verify_checksum_bytes(confirmed)
        events = self._parse_events(confirmed)
        if fragment:
            line_number = confirmed.count(b"\n") + 1
            # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
            raise LogError(
                f"{self._path}: truncated final line at line {line_number} "
                f"({len(fragment)} byte(s) with no terminating newline) -- the process "
                "likely crashed mid-write.\n"
                "\n"
                "Every complete line before this one is intact and verified against the "
                "checksum sidecar, so no screening decision has been lost except "
                "possibly the last one, which never finished being written.\n"
                "\n"
                "To recover:\n"
                f"  1. Look at the trailing {len(fragment)} byte(s) of {self._path} and "
                "decide whether that partial decision is one you want to keep.\n"
                "  2. Delete the incomplete final line, leaving the file ending in a "
                "newline. Do not edit any earlier line -- they are checksummed.\n"
                f"  3. Regenerate the sidecar: sha256sum {self._path.name} > "
                f"{self._checksum_path.name} (run it in {self._path.parent}).\n"
                "  4. Re-log that decision through the UI or DecisionLog.append if you "
                "wanted to keep it.\n"
                "\n"
                "Back the file up before step 2 -- it is the record of human screening "
                "labour and nothing else can reconstruct it."
            )
            # pragma: no mutate end
        return confirmed, events


__all__ = ["DecisionLog", "FoldKey", "fold_events"]
