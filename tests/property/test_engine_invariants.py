"""Set-algebra property and stateful tests (BUILD_PLAN §Stage 4, lines 1006-1019).

Every test in this module drives the *real* engine against a *real* Layer 1
store and a *real* ``decisions.jsonl`` -- what Hypothesis generates is the
event stream, not a substitute for any part of the system. Nothing is
monkeypatched (§3.7.3 rule 1); determinism comes from generated events
carrying explicit ``ts``/``event_id`` values rather than from freezing a
clock the engine never reads.

Two shapes of test live here:

* ``@given`` tests over whole generated streams, one per row of BUILD_PLAN's
  set-algebra table (lines 1008-1016);
* :class:`DecisionLogMachine` (lines 1017-1019), a
  :class:`~hypothesis.stateful.RuleBasedStateMachine` that interleaves
  appends, reversals, reloads and criteria amendments and re-checks every
  Stage 4 invariant after *every* rule -- containment, the on-disk checksum,
  fold-equals-model, and ``FlowCounts.assert_consistent()``.

**Why events are written with explicit ids and timestamps.** ``append_event``
takes a fully-formed :class:`~prismabib.prisma.events.DecisionEvent`, so a
generated stream can pin both fields. That is what makes
``test_sets__event_order_permuted_by_timestamp__yields_same_membership``
possible at all (the same events, written to the file in a different order),
and it removes the wall clock from every other test here as a side effect.

**Why the project is built once per module.** Building a Layer 1 store costs
~100 ms; doing it per example would put these tests an order of magnitude
over §3.7.2's 10-second property budget. The store is immutable for the
duration -- only ``decisions.jsonl`` and ``criteria.yaml`` change -- so a
shared store is not shared *state* in any sense a test could depend on, and
each example resets the log before it writes.
"""

from __future__ import annotations

import shutil
import tempfile
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Final

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import (
    RuleBasedStateMachine,
    invariant,
    precondition,
    rule,
    run_state_machine_as_test,
)

from prismabib.errors import LogError
from prismabib.prisma import engine
from prismabib.prisma.events import Decision, DecisionEvent
from prismabib.prisma.flow import compute_flow_counts
from prismabib.prisma.log import DecisionLog, FoldKey
from prismabib.project import Project
from prismabib.stage import PrismaStage
from tests.prisma_helpers import (
    CorpusSpec,
    CriteriaSpec,
    RecordSpec,
    build_project,
    rewrite_sidecar,
    sidecar_matches_log,
    sidecar_path,
    write_criteria,
)

# ---------------------------------------------------------------------------
# The corpus every property in this module is asserted over
# ---------------------------------------------------------------------------

IN_SCOPE = [RecordSpec(number=index) for index in (1, 2, 3, 4)]
OUT_OF_YEAR = RecordSpec(number=5, year=1999)
NON_ENGLISH = RecordSpec(number=6, language="German")
WRONG_DOC_TYPE = RecordSpec(number=7, doc_type="Review")
UNLISTED_CONFERENCE = RecordSpec(
    number=8,
    doc_type="Conference Paper",
    aggregation_type="Conference Proceeding",
    venue_name="Workshop on Nothing in Particular",
)
ALL_RECORDS = [*IN_SCOPE, OUT_OF_YEAR, NON_ENGLISH, WRONG_DOC_TYPE, UNLISTED_CONFERENCE]

CRITERIA = CriteriaSpec(
    version="1.0.0",
    year_start=2016,
    year_end=2026,
    doc_types_include=("ar", "cp"),
    conference_whitelist=("CVPR",),
    languages=("English",),
    abstract_reason_codes=("OFF_TOPIC",),
    fulltext_reason_codes=("INACCESSIBLE",),
)

RECORD_IDS: Final[tuple[str, ...]] = tuple(record.record_id for record in ALL_RECORDS)
OUT_OF_SCOPE_IDS: Final[tuple[str, ...]] = (
    OUT_OF_YEAR.record_id,
    WRONG_DOC_TYPE.record_id,
    UNLISTED_CONFERENCE.record_id,
)
REVIEWERS: Final[tuple[str, ...]] = ("kp", "mm")
STAGES: Final[tuple[PrismaStage, ...]] = (PrismaStage.TITLE_ABSTRACT, PrismaStage.FULLTEXT)
REASON_CODE_FOR_STAGE: Final[dict[PrismaStage, str]] = {
    PrismaStage.TITLE_ABSTRACT: "OFF_TOPIC",
    PrismaStage.FULLTEXT: "INACCESSIBLE",
}

#: Every generated event's ``ts`` is this instant plus its index, so streams
#: are strictly ordered without consulting a clock the engine never reads.
BASE_TS: Final[datetime] = datetime(2026, 1, 18, 14, 22, 7, 412000, tzinfo=UTC)


@dataclass(frozen=True)
class DecisionDraft:
    """One generated decision, before it is given an id and a timestamp."""

    stage: PrismaStage
    record_id: str
    reviewer: str
    decision: Decision


def drafts(record_ids: tuple[str, ...] = RECORD_IDS) -> st.SearchStrategy[DecisionDraft]:
    """A strategy for one decision over ``record_ids``."""
    return st.builds(
        DecisionDraft,
        stage=st.sampled_from(STAGES),
        record_id=st.sampled_from(record_ids),
        reviewer=st.sampled_from(REVIEWERS),
        decision=st.sampled_from(("include", "exclude", "unsure")),
    )


def streams(
    record_ids: tuple[str, ...] = RECORD_IDS, *, min_size: int = 0, max_size: int = 10
) -> st.SearchStrategy[list[DecisionDraft]]:
    """A strategy for a whole event stream over ``record_ids``."""
    return st.lists(drafts(record_ids), min_size=min_size, max_size=max_size)


def to_event(project: Project, index: int, draft: DecisionDraft) -> DecisionEvent:
    """Give a draft the id and timestamp it will carry on disk."""
    return DecisionEvent(
        event_id=f"ev-{index:04d}",
        ts=BASE_TS + timedelta(seconds=index),
        project=project.slug,
        stage=draft.stage,
        record_id=draft.record_id,
        reviewer=draft.reviewer,
        decision=draft.decision,
        reason_code={"exclude": REASON_CODE_FOR_STAGE[draft.stage]}.get(draft.decision),
        criteria_version=project.criteria.version,
    )


def force_unsure(stream: list[DecisionDraft], position: int) -> list[DecisionDraft]:
    """Make the draft at ``position`` an ``unsure`` that survives the fold.

    Turning one draft into an ``unsure`` is not enough on its own: a later
    draft for the same ``(stage, record_id, reviewer)`` would supersede it,
    and the stream would then contain an ``unsure`` that no longer decides
    anything -- leaving the property with nothing to bite on. Later drafts
    sharing that fold key are therefore dropped, so the generated stream
    still carries an ``unsure`` at an arbitrary position *and* that
    ``unsure`` is what the fold reports for its key.

    Args:
        stream: The generated drafts.
        position: The 0-based index to force to ``unsure``.

    Returns:
        A new stream, same order, with the forced draft surviving.
    """
    target = replace(stream[position], decision="unsure")
    key = (target.stage, target.record_id, target.reviewer)
    survivors = [
        draft
        for draft in stream[position + 1 :]
        if (draft.stage, draft.record_id, draft.reviewer) != key
    ]
    return [*stream[:position], target, *survivors]


def reset_log(project: Project) -> None:
    """Return the project's decision log to its just-initialised state."""
    project.decisions_path.write_bytes(b"")
    sidecar_path(project).unlink(missing_ok=True)


def write_stream(
    project: Project, stream: list[DecisionDraft], *, file_order: list[int] | None = None
) -> list[DecisionEvent]:
    """Write a generated stream to ``project``'s log, oldest event id first.

    Args:
        project: The project whose log to write.
        stream: The drafts to write. Each draft's position in ``stream``
            fixes its ``ts`` and ``event_id``, independently of the order
            the lines are actually appended in.
        file_order: The order to append the lines in, as indices into
            ``stream``. Defaults to ``stream``'s own order.

    Returns:
        The events, in ``stream`` order (not file order).
    """
    log = DecisionLog(project)
    events = [to_event(project, index, draft) for index, draft in enumerate(stream)]
    for index in file_order or list(range(len(events))):
        log.append_event(events[index])
    return events


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """A Layer 1 store shared by every example in this module (see the module docstring)."""
    root = tmp_path_factory.mktemp("prisma-property")
    return build_project(root, CorpusSpec(records=ALL_RECORDS, criteria=CRITERIA), slug="property")


PROPERTY_SETTINGS = settings(
    max_examples=25,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


# ---------------------------------------------------------------------------
# BUILD_PLAN's set-algebra table (lines 1008-1016)
# ---------------------------------------------------------------------------


@pytest.mark.property
@pytest.mark.acceptance("S04-AC1")
@given(stream=streams())
@PROPERTY_SETTINGS
def test_sets__any_event_stream__monotone_containment_holds(
    project: Project, stream: list[DecisionDraft]
) -> None:
    reset_log(project)
    write_stream(project, stream)

    raw = engine.raw_set(project)
    automated = engine.automated_set(project)
    language = engine.language_set(project)
    abstract = engine.manual_abstract_set(project)
    corpus = engine.corpus(project)

    assert corpus <= abstract <= (raw & automated & language) <= raw
    assert len(corpus) <= len(abstract) <= len(raw & automated & language) <= len(raw)


@pytest.mark.property
@given(first=streams(), second=streams())
@PROPERTY_SETTINGS
def test_sets__automated_filter__is_pure_function_of_criteria(
    project: Project, first: list[DecisionDraft], second: list[DecisionDraft]
) -> None:
    reset_log(project)
    write_stream(project, first)
    after_first = (engine.automated_set(project), engine.language_set(project))

    reset_log(project)
    write_stream(project, second)
    after_second = (engine.automated_set(project), engine.language_set(project))

    expected_automated = {record.record_id for record in [*IN_SCOPE, NON_ENGLISH]}
    assert after_first == after_second
    assert after_first[0] == expected_automated
    assert after_first[1] == {record.record_id for record in IN_SCOPE}


@pytest.mark.property
@given(stream=streams(OUT_OF_SCOPE_IDS, min_size=1))
@PROPERTY_SETTINGS
def test_sets__no_event__cannot_add_a_record_to_A(
    project: Project, stream: list[DecisionDraft]
) -> None:
    reset_log(project)
    baseline = (engine.automated_set(project), engine.language_set(project))

    write_stream(project, stream)

    assert (engine.automated_set(project), engine.language_set(project)) == baseline
    assert engine.corpus(project).isdisjoint(OUT_OF_SCOPE_IDS)


@pytest.mark.property
@given(stream=streams(min_size=1), unsure_at=st.integers(min_value=0))
@PROPERTY_SETTINGS
def test_sets__unsure_decisions__never_appear_in_C(
    project: Project, stream: list[DecisionDraft], unsure_at: int
) -> None:
    forced = force_unsure(stream, unsure_at % len(stream))
    reset_log(project)
    write_stream(project, forced)

    folded_by_stage_and_decision: dict[tuple[PrismaStage, str], set[str]] = defaultdict(set)
    for (stage, record_id, _reviewer), event in DecisionLog(project).fold().items():
        folded_by_stage_and_decision[stage, event.decision].add(record_id)
    unsure_at_abstract = folded_by_stage_and_decision[PrismaStage.TITLE_ABSTRACT, "unsure"]
    unsure_at_fulltext = folded_by_stage_and_decision[PrismaStage.FULLTEXT, "unsure"]

    assert unsure_at_abstract | unsure_at_fulltext
    assert engine.corpus(project).isdisjoint(unsure_at_abstract | unsure_at_fulltext)
    assert engine.manual_abstract_set(project).isdisjoint(unsure_at_abstract)


@pytest.mark.property
@given(stream=streams(min_size=2), seed=st.integers(min_value=0, max_value=2**32))
@PROPERTY_SETTINGS
def test_sets__event_order_permuted_by_timestamp__yields_same_membership(
    project: Project, stream: list[DecisionDraft], seed: int
) -> None:
    forward = list(range(len(stream)))
    permuted = forward[seed % len(stream) :] + forward[: seed % len(stream)]

    reset_log(project)
    write_stream(project, stream, file_order=forward)
    in_order = (
        DecisionLog(project).fold(),
        engine.manual_abstract_set(project),
        engine.corpus(project),
    )

    reset_log(project)
    write_stream(project, stream, file_order=permuted)
    rotated = (
        DecisionLog(project).fold(),
        engine.manual_abstract_set(project),
        engine.corpus(project),
    )

    assert rotated == in_order


@pytest.mark.property
@given(stream=streams(min_size=1), replay_at=st.integers(min_value=0))
@PROPERTY_SETTINGS
def test_sets__duplicate_event_ids__raise_log_error(
    project: Project, stream: list[DecisionDraft], replay_at: int
) -> None:
    reset_log(project)
    events = write_stream(project, stream)
    replayed = events[replay_at % len(events)]

    with pytest.raises(LogError, match="duplicate event_id"):
        DecisionLog(project).append_event(replayed)

    assert DecisionLog(project).load() == events


# ---------------------------------------------------------------------------
# The stateful machine (BUILD_PLAN lines 1017-1019)
# ---------------------------------------------------------------------------

#: The criteria amendments `bump_criteria_version` cycles through. Only the
#: version string and the temporal window move; the exclude-reason-code sets
#: are constant, so an amendment never retroactively invalidates a reason
#: code already written into the log.
CRITERIA_VERSIONS: Final[tuple[CriteriaSpec, ...]] = (
    CRITERIA,
    CriteriaSpec(
        version="1.1.0",
        year_start=1990,
        year_end=2026,
        doc_types_include=("ar", "cp"),
        conference_whitelist=("CVPR",),
        languages=("English",),
        abstract_reason_codes=("OFF_TOPIC",),
        fulltext_reason_codes=("INACCESSIBLE",),
    ),
    CriteriaSpec(
        version="2.0.0",
        year_start=2021,
        year_end=2026,
        doc_types_include=("ar", "cp"),
        conference_whitelist=("CVPR",),
        languages=("English",),
        abstract_reason_codes=("OFF_TOPIC",),
        fulltext_reason_codes=("INACCESSIBLE",),
    ),
)

#: What reversing each decision produces. A dict rather than a branch so the
#: rule body stays free of conditionals (§3.7.3 rule 9).
REVERSAL: Final[dict[str, str]] = {
    "include": "exclude",
    "exclude": "include",
    "unsure": "include",
}


def clone_project(prototype: Project) -> Project:
    """A private copy of ``prototype``: same Layer 1 store, empty decision log.

    Copying the built store (a few milliseconds) rather than rebuilding it
    (~100 ms) is what keeps a whole machine run inside the property budget.
    """
    root = Path(tempfile.mkdtemp(dir=prototype.root.parent))
    clone = Project.init(prototype.slug, title="DecisionLogMachine", root=root)
    shutil.copy(prototype.db_path, clone.db_path)
    shutil.copy(prototype.root / "criteria.yaml", clone.root / "criteria.yaml")
    rewrite_sidecar(clone)
    return clone


class DecisionLogMachine(RuleBasedStateMachine):
    """Append / reload / fold, with every Stage 4 invariant re-checked per step.

    The model this machine compares against is deliberately *not*
    :func:`~prismabib.prisma.log.fold_events`: it is a plain "the most
    recent append for this ``(stage, record_id, reviewer)`` wins" dictionary
    maintained in :meth:`_record`. Because every event this machine writes
    carries a strictly increasing ``ts``, "most recent append" and "greatest
    ``(ts, event_id)``" must agree -- so the ``fold_matches_model``
    invariant checks the production fold against an independent statement of
    the same rule, rather than against itself.

    Invariants (BUILD_PLAN line 1019), checked after every rule:

    * ``containment_holds`` -- ``C ⊆ M_abs ⊆ L ⊆ A ⊆ S_raw``;
    * ``checksum_matches`` -- the sidecar describes the file on disk;
    * ``fold_matches_model`` -- folding the file equals folding the model;
    * ``flow_counts_close`` -- ``FlowCounts.assert_consistent()`` passes.
    """

    def __init__(self, prototype: Project) -> None:
        """Start a run against a private copy of ``prototype``."""
        super().__init__()
        self.project = clone_project(prototype)
        self.log = DecisionLog(self.project)
        self.appended: list[DecisionEvent] = []
        self.model: dict[FoldKey, DecisionEvent] = {}
        self.criteria_index = 0

    def teardown(self) -> None:
        """Delete this run's private project copy."""
        shutil.rmtree(self.project.root.parent, ignore_errors=True)

    def _append(
        self, stage: PrismaStage, record_id: str, reviewer: str, decision: Decision
    ) -> None:
        """Append one event and mirror it into the model (helper, not a rule)."""
        event = DecisionEvent(
            event_id=f"ev-{len(self.appended):04d}",
            ts=BASE_TS + timedelta(seconds=len(self.appended)),
            project=self.project.slug,
            stage=stage,
            record_id=record_id,
            reviewer=reviewer,
            decision=decision,
            reason_code={"exclude": REASON_CODE_FOR_STAGE[stage]}.get(decision),
            criteria_version=self.project.criteria.version,
        )
        self.log.append_event(event)
        self.appended.append(event)
        self.model[stage, record_id, reviewer] = event

    @rule(
        stage=st.sampled_from(STAGES),
        record_id=st.sampled_from(RECORD_IDS),
        reviewer=st.sampled_from(REVIEWERS),
    )
    def append_include(self, stage: PrismaStage, record_id: str, reviewer: str) -> None:
        """Log an ``include``."""
        self._append(stage, record_id, reviewer, "include")

    @rule(
        stage=st.sampled_from(STAGES),
        record_id=st.sampled_from(RECORD_IDS),
        reviewer=st.sampled_from(REVIEWERS),
    )
    def append_exclude(self, stage: PrismaStage, record_id: str, reviewer: str) -> None:
        """Log an ``exclude`` with a reason code declared for that stage."""
        self._append(stage, record_id, reviewer, "exclude")

    @rule(
        stage=st.sampled_from(STAGES),
        record_id=st.sampled_from(RECORD_IDS),
        reviewer=st.sampled_from(REVIEWERS),
    )
    def append_unsure(self, stage: PrismaStage, record_id: str, reviewer: str) -> None:
        """Log an ``unsure``."""
        self._append(stage, record_id, reviewer, "unsure")

    @precondition(lambda self: bool(self.appended))
    @rule()
    def reverse_last(self) -> None:
        """Reverse the most recent decision with a new, superseding event."""
        last = self.appended[-1]
        self._append(
            last.stage,
            last.record_id,
            last.reviewer,
            REVERSAL[last.decision],  # type: ignore[arg-type]
        )

    @rule()
    def reload_from_disk(self) -> None:
        """Drop the in-process handle and re-read the whole log from disk."""
        self.log = DecisionLog(self.project)
        assert self.log.load() == self.appended

    @rule()
    def bump_criteria_version(self) -> None:
        """Amend ``criteria.yaml`` to the next version in the cycle."""
        self.criteria_index = (self.criteria_index + 1) % len(CRITERIA_VERSIONS)
        write_criteria(self.project, CRITERIA_VERSIONS[self.criteria_index])

    @invariant()
    def containment_holds(self) -> None:
        """``C ⊆ M_abs ⊆ (S_raw ∩ A ∩ L) ⊆ S_raw``, whatever has been logged."""
        raw = engine.raw_set(self.project)
        automated = engine.automated_set(self.project)
        language = engine.language_set(self.project)
        abstract = engine.manual_abstract_set(self.project)
        corpus = engine.corpus(self.project)

        assert corpus <= abstract <= (raw & automated & language) <= raw

    @invariant()
    def checksum_matches(self) -> None:
        """The sidecar always describes the log's current bytes."""
        assert sidecar_matches_log(self.project)

    @invariant()
    def fold_matches_model(self) -> None:
        """Folding the file equals folding the independently-maintained model."""
        assert self.log.fold() == self.model

    @invariant()
    def flow_counts_close(self) -> None:
        """Every PRISMA accounting identity closes at every step."""
        compute_flow_counts(self.project).assert_consistent()


@pytest.mark.property
@pytest.mark.acceptance("S04-AC1")
def test_decision_log_machine__append_reload_and_fold__preserve_every_invariant(
    project: Project,
) -> None:
    run_state_machine_as_test(
        lambda: DecisionLogMachine(project),
        settings=settings(
            max_examples=8,
            stateful_step_count=5,
            deadline=None,
            derandomize=True,
            suppress_health_check=[
                HealthCheck.function_scoped_fixture,
                HealthCheck.too_slow,
            ],
        ),
    )
