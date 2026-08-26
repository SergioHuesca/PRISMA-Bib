"""The Layer 2 append-only decision log (BUILD_PLAN §2.2 lines 107-118, §Stage 4 lines 970-974).

``decisions.jsonl`` is never mutated, only appended to (BUILD_PLAN line
108: "Screening decisions ... are *events*, never mutations"). Current set
membership is *derived* by folding the log (line 114), not stored anywhere
-- :class:`DecisionLog` is the single place that append happens and the
single place the fold is defined, so every consumer folds the same way.

**Rules this module enforces** (BUILD_PLAN lines 970-974, plus the crash-
and-concurrency contract spelled out for this stage):

1. *Append-only, fsynced per write, checksum-guarded.* Every append opens
   ``decisions.jsonl`` for read/write, takes an exclusive ``flock``, writes
   exactly one line in one ``write(2)`` call, ``fsync``s it, and then
   rewrites the ``decisions.jsonl.sha256`` sidecar (via write-temp-then-
   ``os.replace``, itself ``fsync``d before the rename) to cover the file's
   new content. :meth:`DecisionLog.load` takes a *shared* lock and
   recomputes the same checksum; a mismatch raises :class:`LogError` --
   this is what makes hand-editing detectable.
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
7. *Appends are line-atomic under two open handles.* The exclusive
   ``flock`` around the read-verify-write-checksum sequence serialises two
   concurrent ``DecisionLog`` instances (in the same process or two
   separate ones) so neither can observe, or produce, a torn line or a
   checksum that has fallen out of step with the file it describes.
8. *A reversal is a new event, never an edit.* This module exposes no way
   to delete or rewrite an existing line; :meth:`DecisionLog.append` is the
   only write path, and it only ever adds.

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

import fcntl
import hashlib
import json
import os
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

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
        with self._locked(fcntl.LOCK_SH) as fd:
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
        with self._locked(fcntl.LOCK_EX) as fd:
            confirmed, existing = self._verify_and_load_locked(fd)
            if event.event_id in {existing_event.event_id for existing_event in existing}:
                raise LogError(
                    f"{self._path}: duplicate event_id {event.event_id!r} -- already present "
                    "in the decision log (replayed append or ULID collision)"
                )
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
            raise LogError(
                "reason_code is required when decision='exclude' "
                f"(stage={event.stage.value!r}, record_id={event.record_id!r}, "
                f"reviewer={event.reviewer!r})"
            )
        allowed = self._exclude_reason_codes(event.stage)
        if event.reason_code not in allowed:
            raise LogError(
                f"reason_code {event.reason_code!r} is not declared in criteria.yaml's "
                f"{event.stage.value} exclude_reason_codes {sorted(allowed)!r} "
                f"(criteria_version={event.criteria_version!r})"
            )

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
    def _locked(self, lock_operation: int) -> Iterator[int]:
        """Open :attr:`_path`, hold a ``flock``, and yield its file descriptor.

        The parent directory is created and the file opened read/write with
        ``O_CREAT``, so a :class:`DecisionLog` works even before
        :meth:`~prismabib.project.Project.init` has run. Creating the
        directory is not redundant with ``init``: git cannot store an empty
        directory, so a project cloned with ``track_decisions = false``
        (§2.5 line 291) arrives without ``decisions/``, and the first
        screening decision would otherwise die on ``FileNotFoundError``.
        ``fcntl.flock`` is POSIX-only, matching this project's Linux/macOS
        development and CI targets (BUILD_PLAN §2.4).

        Args:
            lock_operation: ``fcntl.LOCK_SH`` for a read (allows concurrent
                readers, excludes writers) or ``fcntl.LOCK_EX`` for a write
                (excludes everyone else). Held for the caller's entire
                critical section, so a concurrent reader can never observe
                a write half-applied, and two concurrent writers can never
                interleave.

        Yields:
            The open file descriptor, positioned at its start.
        """
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, lock_operation)
            try:
                yield fd
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
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
                raise LogError(
                    f"missing checksum sidecar {self._checksum_path} for a non-empty "
                    f"decision log -- {self._path} may have been created or edited "
                    "outside DecisionLog"
                )
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
            raise LogError(
                f"{self._path} has {uncovered} decision line(s) not covered by the "
                f"checksum sidecar {self._checksum_path}. The sidecar matches this "
                "file's earlier content exactly, which is what an interrupted append "
                "looks like (the line reached disk; the sidecar rewrite did not) -- "
                "not hand-editing. Inspect the trailing line(s); if they are decisions "
                "you intended, the log is intact and only the sidecar needs rewriting."
            )

        raise LogError(
            f"checksum mismatch for {self._path}: sidecar {self._checksum_path} records "
            f"{recorded!r}, but content hashes to {expected!r} -- decisions.jsonl may "
            "have been edited by hand"
        )

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

        Args:
            content: The exact bytes the sidecar should describe -- the
                decision log's full content after the append that
                triggered this call.
        """
        digest = hashlib.sha256(content).hexdigest()
        payload = f"{digest}  {self._path.name}\n".encode()
        tmp_path = self._checksum_path.with_name(self._checksum_path.name + ".tmp")
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
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
                raise LogError(
                    f"{self._path}:{line_number}: unknown schema_version "
                    f"{schema_version!r} (expected {CURRENT_SCHEMA_VERSION})"
                )
            try:
                event = DecisionEvent.model_validate(payload)
            except PydanticValidationError as exc:
                raise LogError(
                    f"{self._path}:{line_number}: malformed decision event: {exc}"
                ) from exc
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
            raise LogError(
                f"{self._path}: truncated final line at line {line_number} "
                f"({len(fragment)} byte(s) with no terminating newline) -- the process "
                "likely crashed mid-write; recover manually before appending again"
            )
        return confirmed, events


__all__ = ["DecisionLog", "FoldKey", "fold_events"]
