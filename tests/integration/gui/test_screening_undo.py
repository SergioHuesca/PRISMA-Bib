"""Undo through the screen, and the 10,000-Record queue (ticket #333).

The large store is generated (synthetic titles, no real or licensed data) and built once per module.
The two time bounds are the spec's: first Record shown within 2 seconds of the Screening step
opening, a status request within 1 second while the queue loads. They are asserted as stated; a
failure prints the measured time so a slow runner is told apart from a real regression.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.errors import ProjectLockedError
from prismabib.gui import screening_layer as sl
from prismabib.gui import service, tasks
from prismabib.gui.screening import build_screening
from prismabib.prisma import engine
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.load import build_store
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.integration.gui.test_screening import STAGE, make_project
from tests.prisma_helpers import RUN_ID, RUN_STARTED_AT, CriteriaSpec, RecordSpec, write_criteria
from tests.store_helpers import write_sealed_run

pytestmark = pytest.mark.integration

RECORDS = 10_000
FIRST_RECORD_BOUND_S = 2.0
STATUS_BOUND_S = 1.0


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "settings.env"))
    tasks.reset_runners()
    yield
    tasks.reset_runners()


@pytest.fixture(scope="module")
def big_project(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Project]:
    """10,000 synthetic Records, built into a real store (about a second on a developer laptop)."""
    patch = pytest.MonkeyPatch()
    root = tmp_path_factory.mktemp("big")
    patch.setenv(USER_FILE_OVERRIDE_VAR, str(root / "settings.env"))
    project = service.create_project(
        "big", title="Big", root=root, source_mode="scopus", reviewer="Ada"
    )
    write_criteria(project, CriteriaSpec())
    entries = [RecordSpec(number=n).to_entry() for n in range(1, RECORDS + 1)]
    write_sealed_run(
        project.raw_dir, RUN_ID, entries, started_at=RUN_STARTED_AT, criteria_version="1.0.0"
    )
    build_store(project, rebuild=True)
    yield project
    patch.undo()


def _lines(project: Project) -> list[str]:
    return project.decisions_path.read_text(encoding="utf-8").splitlines()


def test_undo__after_include__appends_unsure_and_restores_flow_and_progress(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    before = engine.manual_abstract_set(project)
    screener = sl.open_screening(project, STAGE, "Ada")

    screener.handlers["include"]()
    during = engine.manual_abstract_set(project)
    first_line = _lines(project)[0]
    screener.handlers["undo"]()

    lines = _lines(project)
    assert len(lines) == 2 and lines[0] == first_line  # grew by one; the earlier line is untouched
    undo_event = json.loads(lines[1])
    assert undo_event["decision"] == "unsure" and undo_event["reviewer"] == "Ada"
    assert json.loads(first_line)["event_id"] in undo_event["note"]
    assert len(during) == len(before) + 1  # the Include was in the flow counts ...
    assert engine.manual_abstract_set(project) == before  # ... and undo takes it back out
    assert screener.queue.decided == 0 and screener.queue.remaining == 5
    assert screener.queue.current == screener.queue.pending[0]  # looking at what was undone


def test_undo__screen_text__says_the_record_is_unsure_not_unscreened(tmp_path: Path) -> None:
    screener = sl.open_screening(make_project(tmp_path), STAGE, "Ada")

    screener.handlers["include"]()
    screener.handlers["undo"]()

    assert screener.status == sl.UNDONE_STATUS and "Unsure" in screener.status
    assert sl.counts(screener.queue) == {"unscreened": 4, "unsure": 1, "total": 5}
    assert "**Unscreened:** 4 · **Unsure:** 1" in str(screener._header.object)
    assert sl.UNDO_NOTE in str(screener._header.object)


def test_undo__nothing_to_undo__appends_nothing(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    screener = sl.open_screening(project, STAGE, "Ada")

    screener.handlers["undo"]()

    assert screener.status == "nothing to undo"
    assert not project.decisions_path.exists() or not _lines(project)


def test_undo__covers_only_the_researchers_own_decision_in_the_window_that_made_it(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    window_a = sl.open_screening(project, STAGE, "Ada")
    record = window_a.queue.current or ""
    window_a.handlers["include"]()
    window_b = sl.open_screening(project, STAGE, "Bea")  # another Reviewer, same Record
    window_b.handlers["include"]()
    count = len(_lines(project))

    # A new window for Ada starts past her decided Record: nothing of hers there to undo.
    reopened = sl.LockedQueue(project, STAGE, "Ada")
    assert reopened.undo() is None
    assert len(_lines(project)) == count

    # Ada's own undo withdraws hers and leaves Bea's Decision on the same Record alone.
    window_a.handlers["undo"]()
    assert window_b.queue.decision_for(record) == "include"
    fold = window_a.queue.log.fold()
    assert fold[(STAGE, record, "Ada")].decision == "unsure"
    assert fold[(STAGE, record, "Bea")].decision == "include"


def test_undo__read_only_session__is_refused_and_logs_nothing(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    screener = sl.open_screening(project, STAGE, "Ada")
    screener.handlers["include"]()
    count = len(_lines(project))
    holder = service.Session(project)  # another window holds the write lock

    with pytest.raises(ProjectLockedError):
        screener.queue.undo()
    screener.dispatch("undo")  # the screen shows the refusal instead of raising

    assert "Another window" in screener.status and len(_lines(project)) == count
    holder.close()


def _decision_lines(project: Project) -> list[str]:
    return _lines(project) if project.decisions_path.exists() else []


def _decide(screener: sl.HostedScreener, decision: str) -> None:
    if decision == "exclude":
        screener.handlers["begin_exclude"]()
        screener.handlers["reason_1"]()
    else:
        screener.handlers[decision]()


def test_undo__after_exclude__restores_decided_and_remaining_counts(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    screener = sl.open_screening(project, STAGE, "Ada")
    before = (screener.queue.decided, screener.queue.remaining)
    assert before == (0, 5)

    _decide(screener, "exclude")
    assert (screener.queue.decided, screener.queue.remaining) == (1, 4)
    screener.handlers["undo"]()

    assert (screener.queue.decided, screener.queue.remaining) == before
    events = [json.loads(line) for line in _lines(project)]
    assert [e["decision"] for e in events] == ["exclude", "unsure"]
    assert screener.queue.current == screener.queue.pending[0]


def test_undo__on_a_record_on_screen_with_own_exclude__withdraws_it_and_stays(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    screener = sl.open_screening(project, STAGE, "Ada")
    record = screener.queue.pending[0]
    _decide(screener, "exclude")
    screener.handlers["previous_record"]()  # back on the excluded Record
    assert screener.queue.current == record

    screener.handlers["undo"]()

    events = [json.loads(line) for line in _lines(project)]
    assert [e["decision"] for e in events] == ["exclude", "unsure"]
    assert events[1]["record_id"] == record and events[1]["reviewer"] == "Ada"
    assert events[0]["event_id"] in events[1]["note"]
    assert screener.queue.decision_for(record) == "unsure"
    assert screener.queue.current == record and screener.queue.position == 0
    assert (screener.queue.decided, screener.queue.remaining) == (0, 5)


@pytest.mark.parametrize("decision", ["include", "exclude"])
@pytest.mark.parametrize("cursor", ["unscreened", "unsure", "past_last"])
def test_undo__cursor_not_on_a_decided_record__withdraws_the_previous_one_and_moves_to_it(
    tmp_path: Path, cursor: str, decision: str
) -> None:
    project = make_project(tmp_path)
    screener = sl.open_screening(project, STAGE, "Ada")
    pending = screener.queue.pending
    if cursor == "past_last":
        for _ in pending[:-1]:
            screener.handlers["include"]()
        _decide(screener, decision)  # the last Record carries the Decision under test
        assert screener.queue.current is None
        target = pending[-1]
    else:
        _decide(screener, decision)
        if cursor == "unsure":
            screener.handlers["unsure"]()
            screener.handlers["previous_record"]()  # on screen: the Unsure Record
        target = pending[0]
        assert screener.queue.current == pending[1]
    count = len(_lines(project))

    screener.handlers["undo"]()

    events = [json.loads(line) for line in _lines(project)]
    assert len(events) == count + 1
    assert events[-1]["record_id"] == target and events[-1]["decision"] == "unsure"
    assert events[-1]["reviewer"] == "Ada"
    assert screener.queue.decision_for(target) == "unsure"
    assert screener.queue.current == target
    assert screener.queue.position == pending.index(target)


def test_undo__unresolved_first_record__appends_nothing_and_moves_nothing(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    screener = sl.open_screening(project, STAGE, "Ada")

    screener.handlers["undo"]()

    assert _decision_lines(project) == []
    assert screener.queue.position == 0 and screener.queue.current == screener.queue.pending[0]
    assert screener.status == "nothing to undo"


def test_undo__unsure_first_record_on_screen__appends_nothing_and_moves_nothing(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    screener = sl.open_screening(project, STAGE, "Ada")
    screener.handlers["unsure"]()
    screener.handlers["previous_record"]()
    before = _lines(project)

    screener.handlers["undo"]()

    assert _lines(project) == before
    assert screener.queue.position == 0 and screener.queue.current == screener.queue.pending[0]
    assert screener.status == "nothing to undo"


@pytest.mark.parametrize("previous", ["unscreened", "unsure"])
def test_undo__previous_record_also_unresolved__moves_back_one_and_appends_nothing(
    tmp_path: Path, previous: str
) -> None:
    project = make_project(tmp_path)
    screener = sl.open_screening(project, STAGE, "Ada")
    screener.handlers["unsure" if previous == "unsure" else "next_record"]()
    assert screener.queue.position == 1
    before = _decision_lines(project)

    screener.handlers["undo"]()

    assert _decision_lines(project) == before
    assert screener.queue.position == 0 and screener.queue.current == screener.queue.pending[0]
    assert screener.status == "nothing to undo"


def _open_and_time(project: Project) -> tuple[float, sl.HostedScreener]:
    start = time.perf_counter()
    step = build_screening(project)
    next(b for b in step.select(pn.widgets.Button) if b.name == "Start screening").clicks += 1
    tasks.runner_for(project).join(60)
    step.poll()
    elapsed = time.perf_counter() - start
    texts = " ".join(str(p.object) for p in step.select(pn.pane.Markdown))
    assert "Synthetic Record" in texts, "the first Record was not on screen"
    return elapsed, sl.finish_loading(project, blind=True) or pytest.fail("not loaded")


def test_large_store__first_record_shown_within_two_seconds(big_project: Project) -> None:
    elapsed, screener = _open_and_time(big_project)

    assert screener.queue.total == RECORDS
    assert elapsed < FIRST_RECORD_BOUND_S, (
        f"first Record took {elapsed:.2f}s to show for {RECORDS} Records; the bound is "
        f"{FIRST_RECORD_BOUND_S}s (a loaded runner can be slow; a developer laptop takes ~0.3s)"
    )


def test_large_store__status_request_while_the_queue_loads_returns_within_one_second(
    big_project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    entered = threading.Event()
    real_load = sl._load

    def signalling_load(*args: object, **kwargs: object) -> object:
        entered.set()  # the real load runs after this, so the reads below overlap with it
        return real_load(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(sl, "_load", signalling_load)
    sl.start_loading(big_project, PrismaStage.TITLE_ABSTRACT, "Ada")
    assert entered.wait(30)

    slowest, reads, overlapped = 0.0, 0, False
    while (status := sl.load_status(big_project)).running or reads == 0:
        began = time.perf_counter()
        status = sl.load_status(big_project)
        slowest = max(slowest, time.perf_counter() - began)
        overlapped = overlapped or status.running
        reads += 1
    tasks.runner_for(big_project).join(60)

    assert overlapped, "the load finished before any status read, so nothing was measured"
    assert slowest < STATUS_BOUND_S, (
        f"a status request took {slowest:.3f}s while {RECORDS} Records loaded ({reads} reads); "
        f"the bound is {STATUS_BOUND_S}s"
    )
    assert sl.load_status(big_project).outcome == "done"
