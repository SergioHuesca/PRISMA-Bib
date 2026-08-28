"""Integration tests for :class:`prismabib.screening.queue.ScreeningQueue` (BUILD_PLAN §Stage 5).

Every test here drives the real queue against a real Layer 1 store and a real
``decisions.jsonl``. Nothing in ``prismabib`` is monkeypatched (§3.7.3 rule
1): resumption is asserted by *rebuilding the queue from the log on disk*,
which is what the operator's kernel restart actually does, and undo is
asserted by reading back the bytes that were appended.

**One store, twelve logs.** Building a Layer 1 store costs the better part of
a second, and every test here needs the same one; the store is immutable for
the duration of the module, so it is built once. What each test needs
*fresh* is the decision log, and ``_reset_log`` truncates it before every
test -- the same arrangement ``tests/property/test_engine_invariants.py``
already uses for the same reason.

**Why the ordering test spawns interpreters.** BUILD_PLAN's Stage 5 table
labels ``test_queue__same_project_slug__ordering_is_identical_across_runs`` a
unit test. Written as one -- build the queue twice in this process, compare
-- it would pass on the exact defect it exists to catch: an order keyed on
``hash()``, which is stable within a process and salted differently in the
next one by ``PYTHONHASHSEED``. "Identical across runs" is a claim about two
interpreters, so the test runs two.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest

from prismabib.errors import LogError, ValidationError
from prismabib.prisma import engine
from prismabib.prisma.log import DecisionLog
from prismabib.screening.queue import ScreeningQueue, screening_queue
from prismabib.stage import PrismaStage
from tests.conftest import SeededIdFactory
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project, sidecar_path

if TYPE_CHECKING:
    from pathlib import Path

    from prismabib.project import Project

#: The project slug the whole module screens under. Also the ordering seed,
#: so every order asserted below is this slug's.
SLUG = "screening"

#: The reviewer these tests screen as. A second one appears only in
#: ``test_queue__decided_by_other_reviewer__still_appears_for_this_reviewer``.
REVIEWER = "kp"

#: Thirty records, of which the last three are non-English. With
#: ``languages: [English]`` in force that makes ``L`` 27 records -- a
#: non-trivial eligible set (so "the queue's domain is ``L``" is a claim with
#: content), and comfortably more than the 21 that
#: ``test_queue__20_decided__resumes_at_21`` needs.
ENGLISH_RECORDS = 27
NON_ENGLISH_RECORDS = 3

CORPUS = CorpusSpec(
    records=[RecordSpec(number=number) for number in range(1, ENGLISH_RECORDS + 1)]
    + [
        RecordSpec(number=number, language="Chinese")
        for number in range(ENGLISH_RECORDS + 1, ENGLISH_RECORDS + NON_ENGLISH_RECORDS + 1)
    ],
    criteria=CriteriaSpec(languages=("English",)),
)

#: Run in a *fresh interpreter* by
#: ``test_queue__same_project_slug__ordering_is_identical_across_runs``: opens
#: the project this module built, constructs the real queue, and writes its
#: order out as JSON. Written to a file rather than stdout because structlog
#: shares stdout with anything the store logs on open.
_ORDER_PROBE = """
import json
import sys
from pathlib import Path

from prismabib.project import Project
from prismabib.screening.queue import screening_queue
from prismabib.stage import PrismaStage

slug, root, reviewer, destination = sys.argv[1:5]
project = Project.open(slug, root=Path(root))
queue = screening_queue(project, PrismaStage.TITLE_ABSTRACT, reviewer)
Path(destination).write_text(json.dumps(list(queue.order)), encoding="utf-8")
"""


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Project:
    """The shared Layer 1 store every test in this module screens (see the module docstring)."""
    root = tmp_path_factory.mktemp("screening-queue")
    return build_project(root, CORPUS, slug=SLUG)


@pytest.fixture(autouse=True)
def _reset_log(project: Project) -> None:
    """Return the decision log to empty before every test."""
    project.decisions_path.write_bytes(b"")
    sidecar_path(project).unlink(missing_ok=True)


def title_abstract_queue(project: Project, reviewer: str = REVIEWER) -> ScreeningQueue:
    """A freshly folded title/abstract queue -- what re-opening the notebook produces."""
    return screening_queue(project, PrismaStage.TITLE_ABSTRACT, reviewer)


# ---------------------------------------------------------------------------
# Ordering (BUILD_PLAN line 1070)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_queue__same_project_slug__ordering_is_identical_across_runs(
    project: Project, tmp_path: Path
) -> None:
    destinations = [tmp_path / "seed-0.json", tmp_path / "seed-1.json"]
    probes = [
        subprocess.Popen(
            [
                sys.executable,
                "-c",
                _ORDER_PROBE,
                SLUG,
                str(project.root.parent),
                REVIEWER,
                str(out),
            ],
            env={**os.environ, "PYTHONHASHSEED": hash_seed},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        for hash_seed, out in zip(["0", "1"], destinations, strict=True)
    ]

    failures = [probe.communicate()[1] for probe in probes]
    orders = [json.loads(out.read_text(encoding="utf-8")) for out in destinations]

    assert [probe.returncode for probe in probes] == [0, 0], failures
    assert orders[0] == orders[1]
    assert tuple(orders[0]) == title_abstract_queue(project).order


@pytest.mark.integration
def test_queue__construction__is_bound_to_its_project_stage_and_reviewer(project: Project) -> None:
    queue = title_abstract_queue(project)

    assert queue.project is project
    assert queue.stage is PrismaStage.TITLE_ABSTRACT
    assert queue.reviewer == REVIEWER


@pytest.mark.integration
def test_queue__title_abstract_stage__domain_is_the_language_set(project: Project) -> None:
    queue = title_abstract_queue(project)

    assert set(queue.order) == engine.language_set(project)
    assert queue.total == ENGLISH_RECORDS


@pytest.mark.integration
def test_queue__fulltext_stage__domain_is_the_manual_abstract_set(project: Project) -> None:
    abstract = title_abstract_queue(project)
    included = [abstract.decide("include").record_id for _ in range(4)]

    fulltext = screening_queue(project, PrismaStage.FULLTEXT, REVIEWER)

    assert set(fulltext.order) == engine.manual_abstract_set(project)
    assert sorted(fulltext.order) == sorted(included)


@pytest.mark.integration
@pytest.mark.parametrize(
    "stage",
    [PrismaStage.RAW, PrismaStage.AUTOMATED, PrismaStage.LANGUAGE, PrismaStage.INCLUDED],
)
def test_queue__computed_stage__raises_validation_error(
    project: Project, stage: PrismaStage
) -> None:
    with pytest.raises(ValidationError, match="not screened by a human"):
        screening_queue(project, stage, REVIEWER)


@pytest.mark.integration
@pytest.mark.parametrize("reviewer", ["", "   "], ids=["empty", "whitespace"])
def test_queue__blank_reviewer__raises_validation_error(project: Project, reviewer: str) -> None:
    with pytest.raises(ValidationError, match="reviewer must not be empty"):
        title_abstract_queue(project, reviewer)


# ---------------------------------------------------------------------------
# Resumability (BUILD_PLAN line 1076, S05-AC2)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.acceptance("S05-AC2")
def test_queue__20_decided__resumes_at_21(project: Project) -> None:
    first_session = title_abstract_queue(project)
    order = first_session.order
    for _ in range(10):
        first_session.decide("include")
    for _ in range(10):
        first_session.decide("exclude", reason_code="OFF_TOPIC")

    resumed = title_abstract_queue(project)

    assert resumed.current == order[20]
    assert resumed.order == order
    assert resumed.pending == order[20:]
    assert resumed.position == 0
    assert resumed.decided == 20
    assert resumed.remaining == ENGLISH_RECORDS - 20


@pytest.mark.integration
def test_queue__decided_by_other_reviewer__still_appears_for_this_reviewer(
    project: Project,
) -> None:
    other = title_abstract_queue(project, "second-coder")
    for _ in range(5):
        other.decide("include")

    mine = title_abstract_queue(project, REVIEWER)

    assert mine.pending == mine.order
    assert mine.decided == 0
    assert mine.decision_for(mine.order[0]) is None


@pytest.mark.integration
def test_queue__unsure_record__remains_in_queue(project: Project) -> None:
    first_session = title_abstract_queue(project)
    unsure_record = first_session.decide("unsure").record_id

    resumed = title_abstract_queue(project)

    assert resumed.current == unsure_record
    assert unsure_record in resumed.pending
    assert resumed.decision_for(unsure_record) == "unsure"
    assert resumed.decided == 0
    assert resumed.remaining == ENGLISH_RECORDS


@pytest.mark.integration
def test_queue__decision_for__reports_only_this_reviewers_decision(project: Project) -> None:
    other = title_abstract_queue(project, "second-coder")
    contested = other.decide("exclude", reason_code="OFF_TOPIC").record_id

    mine = title_abstract_queue(project, REVIEWER)

    assert mine.decision_for(contested) is None
    assert mine.is_resolved(contested) is False


# ---------------------------------------------------------------------------
# Undo (BUILD_PLAN line 1078)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_undo__last_decision__appends_reversal_and_steps_back(project: Project) -> None:
    queue = title_abstract_queue(project)
    first_record = queue.order[0]
    original = queue.decide("include")

    reversal = queue.undo()

    events = DecisionLog(project).load()
    assert [event.decision for event in events] == ["include", "unsure"]
    assert events[0].event_id == original.event_id
    assert events[1].event_id == reversal.event_id  # type: ignore[union-attr]
    assert {event.record_id for event in events} == {first_record}
    assert events[1].note == f"undo: supersedes {original.event_id}"
    assert queue.position == 0
    assert queue.current == first_record
    assert queue.decided == 0
    assert first_record not in engine.manual_abstract_set(project)


@pytest.mark.integration
def test_undo__at_first_record__is_a_no_op(project: Project) -> None:
    queue = title_abstract_queue(project)
    first_record = queue.current

    reversal = queue.undo()

    assert reversal is None
    assert queue.position == 0
    assert queue.current == first_record
    assert DecisionLog(project).load() == []


@pytest.mark.integration
@pytest.mark.parametrize("decision", ["unsure", "none"])
def test_undo__unresolved_previous_record__steps_back_without_appending(
    project: Project, decision: str
) -> None:
    queue = title_abstract_queue(project)
    stepper = {"unsure": lambda: queue.decide("unsure").record_id, "none": queue.advance}
    stepper[decision]()
    events_before = len(DecisionLog(project).load())

    reversal = queue.undo()

    assert reversal is None
    assert queue.position == 0
    assert queue.current == queue.order[0]
    assert len(DecisionLog(project).load()) == events_before


@pytest.mark.integration
def test_undo__then_a_new_decision__the_latest_event_wins_the_fold(project: Project) -> None:
    queue = title_abstract_queue(project)
    record_id = queue.order[0]
    queue.decide("include")
    queue.undo()

    final = queue.decide("exclude", reason_code="REVIEW_OR_SURVEY")

    events = DecisionLog(project).load()
    fold = DecisionLog(project).fold()
    assert [event.decision for event in events] == ["include", "unsure", "exclude"]
    assert fold[(PrismaStage.TITLE_ABSTRACT, record_id, REVIEWER)].event_id == final.event_id
    assert queue.decision_for(record_id) == "exclude"
    assert record_id not in engine.manual_abstract_set(project)


# ---------------------------------------------------------------------------
# Deciding, progress, and navigation
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_queue__decide__advances_the_cursor_and_updates_progress(project: Project) -> None:
    queue = title_abstract_queue(project)

    event = queue.decide("exclude", reason_code="OFF_TOPIC")

    assert event.record_id == queue.order[0]
    assert event.stage is PrismaStage.TITLE_ABSTRACT
    assert event.reviewer == REVIEWER
    assert queue.position == 1
    assert queue.current == queue.order[1]
    assert queue.decided == 1
    assert queue.remaining == ENGLISH_RECORDS - 1
    assert queue.is_resolved(event.record_id) is True


@pytest.mark.integration
def test_decide__exclude_without_reason_code__raises_and_changes_nothing(
    project: Project,
) -> None:
    queue = title_abstract_queue(project)

    with pytest.raises(LogError, match="reason_code"):
        queue.decide("exclude")

    assert queue.position == 0
    assert queue.current == queue.order[0]
    assert DecisionLog(project).load() == []


@pytest.mark.integration
def test_decide__exhausted_queue__raises_validation_error(project: Project) -> None:
    queue = title_abstract_queue(project)
    for _ in range(ENGLISH_RECORDS):
        queue.decide("include")

    with pytest.raises(ValidationError, match="exhausted"):
        queue.decide("include")

    assert queue.is_exhausted is True
    assert queue.current is None
    assert queue.decided == ENGLISH_RECORDS
    assert queue.remaining == 0


@pytest.mark.integration
def test_navigation__at_the_boundaries__clamps(project: Project) -> None:
    queue = title_abstract_queue(project)

    assert queue.step_back() == queue.order[0]
    assert queue.position == 0

    for _ in range(ENGLISH_RECORDS + 2):
        queue.advance()

    assert queue.position == ENGLISH_RECORDS
    assert queue.current is None
    assert queue.step_back() == queue.order[ENGLISH_RECORDS - 1]


@pytest.mark.integration
def test_queue__injected_log__decisions_are_appended_through_it(project: Project) -> None:
    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="queue"))
    queue = ScreeningQueue(project, PrismaStage.TITLE_ABSTRACT, REVIEWER, log=log)

    first = queue.decide("include")
    second = queue.decide("unsure")

    assert queue.log is log
    assert [first.event_id, second.event_id] == ["queue-000000", "queue-000001"]
    assert [event.event_id for event in log.load()] == ["queue-000000", "queue-000001"]
