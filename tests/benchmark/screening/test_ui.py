"""The autosave latency criterion, S05-AC3 (BUILD_PLAN line 1090).

*"Every decision appears in `decisions.jsonl` within 100 ms."* BUILD_PLAN's
test table adds where to measure it: **"on the append path, not the UI"**.
What is timed here is one keystroke arriving at :meth:`Screener.handle_key`
and returning after the log has been written and fsynced -- a superset of the
append path, and therefore a stricter claim than the criterion asks for, while
still containing no browser and no rendered pixel.

**Timed against a log that is already long.** ``DecisionLog.append`` verifies
and re-reads the existing log on every write, so its cost grows with the file;
measured against an empty log the test would assert the cheapest append a
review ever performs and stay green through a regression that only bites at
record 900. The log is seeded first, by *other reviewers*, so it is long
without resolving anything in this reviewer's queue.

Deliberately not using the ``benchmark`` fixture, for the reason
``tests/benchmark/store/test_load.py`` records: ``pytest-benchmark`` disables
itself under ``pytest-xdist``, and CI's ``full`` job runs ``-n auto``, so
``benchmark.stats`` is ``None`` there and every reference to it raises. A gate
that crashes in the only environment §3.7.7 treats as authoritative is worse
than no gate.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import pytest

from prismabib.prisma.log import DecisionLog
from prismabib.screening import ui
from prismabib.screening.queue import screening_queue
from prismabib.stage import PrismaStage
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project

if TYPE_CHECKING:
    from pathlib import Path

    from prismabib.project import Project

#: The criterion itself (BUILD_PLAN line 1090), in seconds.
BUDGET_SECONDS = 0.100

#: Decisions to time. Twenty is BUILD_PLAN's own unit of a screening burst
#: (S05-AC2 restarts the kernel after twenty), and enough that one scheduling
#: hiccup cannot be mistaken for the typical cost.
TIMED_DECISIONS = 20

#: Events pre-written by other reviewers, so the timed appends are made
#: against a log of a size a real review reaches -- not an empty file.
SEEDED_EVENTS = 150

RECORD_COUNT = 24


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A small project whose decision log has already been made long."""
    built = build_project(
        tmp_path,
        CorpusSpec(
            records=[RecordSpec(number=number) for number in range(1, RECORD_COUNT + 1)],
            criteria=CriteriaSpec(),
        ),
        slug="latency",
    )
    log = DecisionLog(built)
    record_ids = [RecordSpec(number=number).record_id for number in range(1, RECORD_COUNT + 1)]
    for index in range(SEEDED_EVENTS):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record_ids[index % RECORD_COUNT],
            reviewer=f"other-{index}",
            decision="include",
        )
    return built


@pytest.mark.benchmark
@pytest.mark.acceptance("S05-AC3")
def test_screener__decision_persisted_within_100ms(project: Project) -> None:
    """Every decision reaches ``decisions.jsonl`` within 100 ms.

    The slowest of twenty is asserted, not the mean: the criterion is about
    *every* decision, and a mean hides the one append that stalled the
    reviewer. The count on disk is asserted as well -- a timing test whose
    subject silently did nothing would otherwise be the fastest test in the
    suite.
    """
    queue = screening_queue(project, PrismaStage.TITLE_ABSTRACT, "kp")
    screener = ui.Screener(queue, ui.load_records(project, queue.pending))
    assert queue.decided == 0, "another reviewer's decisions must not resolve this queue"

    worst_seconds = 0.0
    for _ in range(TIMED_DECISIONS):
        started = time.perf_counter()
        screener.handle_key("i")
        worst_seconds = max(worst_seconds, time.perf_counter() - started)

    mine = [
        json.loads(line)
        for line in project.decisions_path.read_text(encoding="utf-8").splitlines()
        if json.loads(line)["reviewer"] == "kp"
    ]
    assert len(mine) == TIMED_DECISIONS
    assert worst_seconds < BUDGET_SECONDS, (
        f"the slowest of {TIMED_DECISIONS} decisions took {worst_seconds * 1000:.1f} ms "
        f"against a {SEEDED_EVENTS}-event log, over the 100 ms of BUILD_PLAN line 1090"
    )
