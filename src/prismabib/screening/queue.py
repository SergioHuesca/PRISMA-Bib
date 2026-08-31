"""The screening queue -- the pure-logic half of Stage 5 (BUILD_PLAN §Stage 5, lines 1063-1090).

BUILD_PLAN line 1081 freezes the contract::

    def screening_queue(project: Project, stage: PrismaStage, reviewer: str) -> ScreeningQueue: ...

and the design requirements this module implements are lines 1069-1078:
stable unbiased ordering (1), resumability (4), and undo as a *superseding
reversal* (8). Keyboard bindings, autosave, pace, blinding and the reason
palette belong to ``screening/ui.py``; nothing here imports ``panel`` or
knows a widget exists.

**Ordering is a bias control, not a convenience.** BUILD_PLAN line 1070:
records are presented in a deterministic order seeded from the project slug,
"*not* by relevance, score, or any ranking. Ranked order would introduce
order effects into a human-only protocol." A reviewer who meets the
highest-cited papers first calibrates on them and screens the rest against
that calibration; the resulting corpus is then a function of the ranking as
well as of the criteria. The order therefore has to be *arbitrary* with
respect to every property of the record, and *fixed* with respect to the
project, so that a re-run, a second machine, or a re-opened kernel presents
the same sequence to the same reviewer.

**The seed derivation, in full.** The project slug is the only seed
material. Each eligible record gets the sort key

    (sha256(ORDERING_NAMESPACE + "\\x1f" + slug + "\\x1f" + record_id), record_id)

and the queue is that key's ascending sort. Four properties follow, and each
is one of the ways this could have gone wrong:

1. *It is identical across runs, processes and machines.* SHA-256 is a fixed
   function of its bytes, forever, on every platform and interpreter. The
   obvious alternatives are not: Python's built-in ``hash()`` over ``str`` is
   salted per process by ``PYTHONHASHSEED``, so a ``hash()``-keyed order is
   reproducible *within* a session and different in the next one -- this
   codebase has shipped exactly that defect before. Iterating the
   ``frozenset`` the PRISMA engine returns has the same problem for the same
   reason.
2. *It is independent of every property of the record except its id.* Title,
   citation count, venue and year are not inputs, so the order cannot
   correlate with any of them beyond chance;
   ``test_queue__ordering__is_uncorrelated_with_citation_count`` measures
   that as a Spearman correlation rather than asserting it in a comment.
3. *It is stable under corpus growth.* Because each record's key depends only
   on ``(slug, record_id)`` and not on which other records are present, adding
   or removing records leaves the relative order of the rest untouched. A
   ``random.Random(seed).shuffle(...)`` of the eligible list -- the other
   natural implementation -- does not have this property: one extra record
   reshuffles the whole queue, so a reviewer who re-captures mid-review loses
   their place in a way no log can reconstruct. It would also stake
   reproducibility on the Mersenne Twister's seeding staying byte-compatible
   across Python releases.
4. *It differs per project.* The slug is inside the hashed material, so two
   projects over the same records screen them in unrelated orders.

``ORDERING_NAMESPACE`` carries a version suffix: changing the ordering rule
means changing that string, which makes the change visible and deliberate
rather than a silent renumbering of somebody's half-finished review.

**Resumability folds the log; it does not re-derive it.** BUILD_PLAN line
1076: "On load, the queue skips records already decided at that stage by that
reviewer." The fold key is ``(stage, record_id, reviewer)`` -- ``log.py``
owns it, this module only reads it -- so a record another reviewer has
already decided is still queued for this one, which is what makes a second
coder (and therefore an inter-rater agreement statistic) possible later
without a schema change.

**``unsure`` does not resolve.** It is a real, logged decision, but it leaves
the record in the queue: BUILD_PLAN line 973's rule that ``unsure`` never
folds into inclusion is a statement about the *corpus*, and the queue's
counterpart is that an unsure record comes back around rather than
disappearing into a set nobody revisits.

**Undo appends; it never edits.** ``decisions.jsonl`` is append-only
(BUILD_PLAN §2.2 line 108), so stepping back writes a *superseding* event --
an ``unsure`` for the same fold key, which both neutralises the previous
decision (the record is no longer in ``M_abs``/``M_full``) and returns it to
the queue. Two events exist afterwards, the original still readable, which is
what lets a replay reconstruct that the reviewer changed their mind rather
than showing a decision that was never made.

**The queue's domain is the PRISMA engine's, not its own.** ``L``
(``language_set``) feeds title/abstract screening and ``M_abs``
(``manual_abstract_set``) feeds full-text screening. Both are read from
``prisma.engine``; re-deriving either here would be a second definition of a
PRISMA set, and the day the two disagreed the flow diagram and the queue
would both look right.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Final

from prismabib.errors import ValidationError
from prismabib.prisma import engine
from prismabib.prisma.log import DecisionLog
from prismabib.stage import PrismaStage

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from prismabib.prisma.events import Decision, DecisionEvent
    from prismabib.project import Project

#: Domain separator hashed into every sort key. The ``/v1`` suffix versions
#: the *ordering rule itself*: any future change to how the queue is ordered
#: bumps this string, so the change is a visible edit rather than a silent
#: reshuffle of an in-progress review. The namespace also stops a digest
#: computed here from colliding with one computed for some other purpose over
#: the same slug and record id.
ORDERING_NAMESPACE: Final[str] = "prismabib/screening/queue/v1"

#: ASCII unit separator. Joining the hashed fields with a control character
#: no slug or Scopus record id contains keeps the encoding injective: with a
#: plain concatenation, ``("ab", "c")`` and ``("a", "bc")`` would hash
#: identically, and one project's order would leak into another's.
_FIELD_SEPARATOR: Final[str] = "\x1f"

#: The decisions that take a record *out* of the queue. ``"unsure"`` is
#: deliberately absent (BUILD_PLAN line 973, and the module docstring).
RESOLVING_DECISIONS: Final[frozenset[Decision]] = frozenset({"include", "exclude"})

#: Which PRISMA set each screening stage draws its records from (BUILD_PLAN
#: lines 944-946). Read from :mod:`prismabib.prisma.engine`, never
#: reimplemented. The three computed stages (``RAW``, ``AUTOMATED``,
#: ``LANGUAGE``) and the derived ``INCLUDED`` are absent because no human
#: screens them -- asking for a queue over one is a programming error, not an
#: empty queue.
_STAGE_DOMAIN: Final[Mapping[PrismaStage, Callable[[Project], frozenset[str]]]] = {
    PrismaStage.TITLE_ABSTRACT: engine.language_set,
    PrismaStage.FULLTEXT: engine.manual_abstract_set,
}


def _order_key(slug: str, record_id: str) -> tuple[bytes, str]:
    """The sort key that places ``record_id`` in ``slug``'s screening order.

    Args:
        slug: The project slug -- the entire seed (see the module docstring).
        record_id: The record to place.

    Returns:
        ``(digest, record_id)``. The digest is SHA-256 over the namespaced,
        separator-joined UTF-8 encoding of both inputs, so it is a fixed
        function of them on every platform and interpreter. ``record_id``
        is appended as a tie-break purely to make the ordering *total*: two
        distinct records would otherwise be incomparable in the (practically
        impossible) event of a SHA-256 collision, and a non-total key would
        make the result depend on the input's iteration order.
    """
    material = _FIELD_SEPARATOR.join((ORDERING_NAMESPACE, slug, record_id))
    return hashlib.sha256(material.encode("utf-8")).digest(), record_id


def ordered_record_ids(slug: str, record_ids: Iterable[str]) -> tuple[str, ...]:
    """Put ``record_ids`` into ``slug``'s deterministic screening order.

    Public because it *is* the methodological rule (BUILD_PLAN line 1070),
    not an implementation detail of :class:`ScreeningQueue`: the order a
    review was screened in is a property of the review, and both the tests
    that pin it and any future audit of it need to be able to name it.

    Args:
        slug: The project slug. The only seed material.
        record_ids: The records to order, in any order and from any
            container -- typically the ``frozenset`` a
            :mod:`prismabib.prisma.engine` set function returns. Duplicates
            collapse.

    Returns:
        Every distinct id, ordered. The result depends only on ``slug`` and
        on the *set* of ids: permuting the input, or passing a set whose
        iteration order the interpreter chose, yields the identical tuple.
    """
    return tuple(sorted(set(record_ids), key=lambda record_id: _order_key(slug, record_id)))


def eligible_record_ids(project: Project, stage: PrismaStage) -> frozenset[str]:
    """The set of records a human screens at ``stage`` (BUILD_PLAN lines 944-946).

    Args:
        project: The project to compute the set for.
        stage: :attr:`~prismabib.stage.PrismaStage.TITLE_ABSTRACT` (whose
            domain is ``L``, :func:`~prismabib.prisma.engine.language_set`)
            or :attr:`~prismabib.stage.PrismaStage.FULLTEXT` (whose domain
            is ``M_abs``,
            :func:`~prismabib.prisma.engine.manual_abstract_set`).

    Returns:
        The PRISMA engine's set for that stage, unmodified.

    Raises:
        ValidationError: If ``stage`` is one of the computed sets
            (``RAW``/``AUTOMATED``/``LANGUAGE``) or the derived ``INCLUDED``.
            None of them is screened by a human, so a queue over one would be
            a category error dressed up as an empty list.
        ConfigError: If ``project``'s ``criteria.yaml`` fails to parse.
        LogError: If the decision log fails to load (``FULLTEXT`` only --
            ``M_abs`` is folded from it).
        StoreError: If no Layer 1 store exists yet for ``project``.
    """
    compute = _STAGE_DOMAIN.get(stage)
    if compute is None:
        screenable = sorted(candidate.value for candidate in _STAGE_DOMAIN)
        raise ValidationError(
            f"stage {stage.value!r} is not screened by a human; expected one of {screenable}"
        )
    return compute(project)


class ScreeningQueue:
    """One reviewer's ordered, resumable working list for one screening stage.

    Construction reads the stage's eligible set from the PRISMA engine,
    orders it (see the module docstring), folds the decision log, and drops
    from the *working list* every record this reviewer has already resolved
    at this stage. The full order stays available as :attr:`order`.

    **The working list is a snapshot; the cursor is what moves.** Deciding a
    record does not remove it from :attr:`pending` mid-session -- it appends
    an event and advances :attr:`position` by one. That is what makes
    :meth:`undo` and plain backwards navigation coherent (a list that
    reshuffled itself under the cursor would step back to a different record
    than the one just decided), and it costs nothing: the next construction
    re-folds the log and the resolved records are gone. :attr:`decided` and
    :attr:`remaining` are recomputed from the fold as it changes, so progress
    is live even though the list is not.

    Not thread-safe, and not intended to be: one reviewer, one kernel, one
    queue. Concurrency *between* queues is handled where it belongs, by
    :class:`~prismabib.prisma.log.DecisionLog`'s file lock.
    """

    def __init__(
        self,
        project: Project,
        stage: PrismaStage,
        reviewer: str,
        *,
        log: DecisionLog | None = None,
    ) -> None:
        """Build the queue for ``reviewer`` at ``stage``.

        Args:
            project: The project being screened. Supplies the eligible set,
                the slug the order is seeded from, and the decision log.
            stage: The screening stage (see :func:`eligible_record_ids`).
            reviewer: The reviewer's identifier. Half of the fold key, so a
                blank one would silently merge two people's decisions.
            log: The decision log to fold and append to. Defaults to
                ``DecisionLog(project)``. Injectable so the UI can share one
                instance, and so a test can supply a seeded
                :class:`~prismabib.prisma.events.IdFactory` -- the frozen
                ``screening_queue`` contract stays exactly as BUILD_PLAN
                line 1081 specifies it.

        Raises:
            ValidationError: If ``reviewer`` is blank, or ``stage`` is not a
                human-screened stage.
            ConfigError: If ``project``'s ``criteria.yaml`` fails to parse.
            LogError: If the decision log fails to load.
            StoreError: If no Layer 1 store exists yet for ``project``.
        """
        if not reviewer.strip():
            raise ValidationError("reviewer must not be empty")

        self._project = project
        self._stage = stage
        self._reviewer = reviewer
        self._log = log if log is not None else DecisionLog(project)

        eligible = eligible_record_ids(project, stage)
        self._order = ordered_record_ids(project.slug, eligible)
        self._latest: dict[str, DecisionEvent] = {
            record_id: event
            for (event_stage, record_id, event_reviewer), event in self._log.fold().items()
            if event_stage is stage and event_reviewer == reviewer and record_id in eligible
        }
        self._pending = tuple(
            record_id for record_id in self._order if not self.is_resolved(record_id)
        )
        self._position = 0

    # -- identity ------------------------------------------------------------

    @property
    def project(self) -> Project:
        """The project being screened."""
        return self._project

    @property
    def stage(self) -> PrismaStage:
        """The screening stage this queue covers."""
        return self._stage

    @property
    def reviewer(self) -> str:
        """The reviewer this queue is folded for."""
        return self._reviewer

    @property
    def log(self) -> DecisionLog:
        """The decision log this queue folds and appends to."""
        return self._log

    # -- the order and the working list --------------------------------------

    @property
    def order(self) -> tuple[str, ...]:
        """Every eligible record, in screening order, decided or not."""
        return self._order

    @property
    def pending(self) -> tuple[str, ...]:
        """The records this reviewer had not resolved when the queue was built.

        A snapshot, in :attr:`order`'s order -- see the class docstring for
        why it does not shrink as decisions are made.
        """
        return self._pending

    @property
    def position(self) -> int:
        """The cursor's index into :attr:`pending`.

        Equals ``len(pending)`` once the working list is exhausted.
        """
        return self._position

    @property
    def current(self) -> str | None:
        """The record under the cursor, or ``None`` if the queue is exhausted."""
        if self._position >= len(self._pending):
            return None
        return self._pending[self._position]

    @property
    def is_exhausted(self) -> bool:
        """Whether the cursor has run off the end of :attr:`pending`."""
        return self._position >= len(self._pending)

    # -- progress ------------------------------------------------------------

    @property
    def total(self) -> int:
        """``N`` -- how many records are eligible at this stage in total."""
        return len(self._order)

    @property
    def decided(self) -> int:
        """``n`` -- how many of them this reviewer has resolved.

        Counts ``include`` and ``exclude`` only: an ``unsure`` record is
        logged but unresolved, and counting it as progress would overstate
        how much of the review is actually finished.
        """
        return sum(1 for event in self._latest.values() if event.decision in RESOLVING_DECISIONS)

    @property
    def remaining(self) -> int:
        """How many eligible records this reviewer has still to resolve."""
        return self.total - self.decided

    # -- per-record state ----------------------------------------------------

    def decision_for(self, record_id: str) -> Decision | None:
        """This reviewer's current decision for ``record_id`` at this stage.

        Args:
            record_id: An eligible record.

        Returns:
            ``"include"``, ``"exclude"``, ``"unsure"``, or ``None`` when this
            reviewer has never logged a decision for it. Another reviewer's
            decision is never reported here -- the fold key includes the
            reviewer.
        """
        event = self._latest.get(record_id)
        return None if event is None else event.decision

    def is_resolved(self, record_id: str) -> bool:
        """Whether ``record_id`` is settled for this reviewer.

        Args:
            record_id: An eligible record.

        Returns:
            ``True`` only for a current ``include`` or ``exclude``. An
            ``unsure`` record is *not* resolved, which is precisely why it
            stays in the queue.
        """
        return self.decision_for(record_id) in RESOLVING_DECISIONS

    # -- navigation ----------------------------------------------------------

    def advance(self) -> str | None:
        """Move the cursor forward one record (``n``), stopping at the end.

        Returns:
            The new :attr:`current`, or ``None`` if the queue is now
            exhausted.
        """
        self._position = min(self._position + 1, len(self._pending))
        return self.current

    def step_back(self) -> str | None:
        """Move the cursor back one record (``p``), stopping at the first.

        Returns:
            The new :attr:`current`, or ``None`` if :attr:`pending` is empty.
        """
        self._position = max(self._position - 1, 0)
        return self.current

    # -- deciding ------------------------------------------------------------

    def decide(
        self,
        decision: Decision,
        *,
        reason_code: str | None = None,
        note: str = "",
    ) -> DecisionEvent:
        """Log ``decision`` for :attr:`current` and advance the cursor.

        The append happens first and synchronously: if
        :meth:`~prismabib.prisma.log.DecisionLog.append` raises -- an
        ``exclude`` with no reason code, a corrupted log -- the cursor has
        not moved and nothing about this queue has changed.

        Args:
            decision: ``"include"``, ``"exclude"`` or ``"unsure"``. An
                ``"unsure"`` advances the cursor like any other decision but
                leaves the record unresolved, so the next construction of the
                queue offers it again.
            reason_code: Required for ``"exclude"``, and validated against
                the project's ``criteria.yaml`` by the log, not here.
            note: A free-text annotation.

        Returns:
            The appended :class:`~prismabib.prisma.events.DecisionEvent`.

        Raises:
            ValidationError: If the queue is exhausted, so there is no
                record under the cursor to decide.
            LogError: Anything
                :meth:`~prismabib.prisma.log.DecisionLog.append` raises --
                notably a missing or undeclared ``reason_code`` on an
                ``exclude``.
        """
        record_id = self.current
        if record_id is None:
            raise ValidationError(
                f"the {self._stage.value} queue for {self._reviewer!r} is exhausted: "
                f"all {self.total} eligible records have been offered"
            )
        event = self._log.append(
            stage=self._stage,
            record_id=record_id,
            reviewer=self._reviewer,
            decision=decision,
            reason_code=reason_code,
            note=note,
        )
        self._latest[record_id] = event
        self._position += 1
        return event

    def undo(self) -> DecisionEvent | None:
        """Reverse the decision on the record under the cursor (``z``).

        BUILD_PLAN line 1078. The decision is *reversed by appending*, never
        by editing or deleting the original event: the reversal is an
        ``unsure`` for the same ``(stage, record_id, reviewer)`` fold key,
        which wins the fold (it is later), takes the record back out of
        ``M_abs``/``M_full``, and leaves it unresolved and therefore queued.
        Both events remain readable, so a replay shows a reviewer who changed
        their mind rather than one who never decided.

        *Which* record is reversed follows one rule: the one on screen. When
        the record under the cursor already carries a resolving decision --
        the case whenever the reviewer stepped back with ``p`` to re-read
        something they had decided -- that is the decision withdrawn, and the
        cursor does not move. Otherwise the cursor sits past the last
        decision, which is the ordinary decide-then-``z`` case, so it steps
        back one record and reverses what it lands on.

        Either way the cursor ends on the record whose decision was just
        withdrawn, so the reviewer is looking at what they changed. Taking
        ``position - 1`` unconditionally did not: after a ``p`` it reversed
        the record *before* the one on screen, so the paper the reviewer meant
        to reconsider kept its decision while one they had never looked at
        silently lost its own -- two wrong outcomes from one keystroke, under
        a status line that said "undone" either way.

        Returns:
            The appended reversal, or ``None`` when there was nothing to
            reverse: with an unresolved record under the cursor at
            :attr:`position` ``== 0`` this is a complete no-op -- no event, no
            cursor movement -- and when the record stepped back to is merely
            unresolved (never decided, or last decided ``unsure``) the cursor
            moves without appending a second, meaningless ``unsure``.

        Raises:
            LogError: Anything
                :meth:`~prismabib.prisma.log.DecisionLog.append` raises.
        """
        current = self.current
        if current is not None and self.is_resolved(current):
            return self._supersede(current)

        if self._position == 0:
            return None

        target = self._position - 1
        record_id = self._pending[target]
        self._position = target
        if not self.is_resolved(record_id):
            return None
        return self._supersede(record_id)

    def _supersede(self, record_id: str) -> DecisionEvent:
        """Append the ``unsure`` that withdraws ``record_id``'s decision.

        Args:
            record_id: A record whose latest decision by this reviewer
                resolves it -- callers check with :meth:`is_resolved`.

        Returns:
            The appended reversal, which becomes the record's current
            decision.
        """
        superseded = self._latest[record_id]
        reversal = self._log.append(
            stage=self._stage,
            record_id=record_id,
            reviewer=self._reviewer,
            decision="unsure",
            note=f"undo: supersedes {superseded.event_id}",
        )
        self._latest[record_id] = reversal
        return reversal


def screening_queue(project: Project, stage: PrismaStage, reviewer: str) -> ScreeningQueue:
    """Build ``reviewer``'s screening queue for ``stage`` (BUILD_PLAN line 1081).

    The frozen entry point. Equivalent to ``ScreeningQueue(project, stage,
    reviewer)``; the class additionally accepts an injected
    :class:`~prismabib.prisma.log.DecisionLog`, which this signature
    deliberately does not expose.

    Args:
        project: The project being screened.
        stage: :attr:`~prismabib.stage.PrismaStage.TITLE_ABSTRACT` or
            :attr:`~prismabib.stage.PrismaStage.FULLTEXT`.
        reviewer: The reviewer's identifier.

    Returns:
        A :class:`ScreeningQueue` positioned at the first record this
        reviewer has not yet resolved.

    Raises:
        ValidationError: If ``reviewer`` is blank, or ``stage`` is not a
            human-screened stage.
        ConfigError: If ``project``'s ``criteria.yaml`` fails to parse.
        LogError: If the decision log fails to load.
        StoreError: If no Layer 1 store exists yet for ``project``.
    """
    return ScreeningQueue(project, stage, reviewer)


__all__ = [
    "ORDERING_NAMESPACE",
    "RESOLVING_DECISIONS",
    "ScreeningQueue",
    "eligible_record_ids",
    "ordered_record_ids",
    "screening_queue",
]
