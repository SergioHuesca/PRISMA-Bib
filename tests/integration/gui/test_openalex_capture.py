"""OpenAlex capture through the layer and the screen, over a stubbed transport (ticket #330)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

pytest.importorskip("panel")

import panel as pn

from prismabib.capture.layout import OPENALEX_SEARCH_DIRNAME, is_sealed
from prismabib.errors import ConfigError, StepLockedError, ValidationError
from prismabib.gui import capture_layer as cl
from prismabib.gui import layer, stop_model, tasks
from prismabib.gui import openalex_capture_layer as oal
from prismabib.gui.capture import build_capture, build_capture_step
from prismabib.gui.capture_layer import CaptureState as S
from prismabib.gui.query_form_model import QueryInputs
from prismabib.gui.size_check_model import local_reset
from prismabib.project import Project
from prismabib.query import build_openalex_query_for_project
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR, write_user_settings
from tests.integration.capture.test_openalex_search import (
    _DAILY_BODY,
    _DAILY_HEADERS,
    Fake,
    works,
)
from tests.integration.gui.test_home import unlabelled_controls

pytestmark = pytest.mark.integration

_URL = "https://api.openalex.org/works"
_PAGES = [works(1, 2), works(3, 4), works(5)]


class Stub:
    """The shared fake with a hold (the second request waits for ``release``) and a daily stop."""

    def __init__(self, *, daily_at: int | None = None) -> None:
        self.fake = Fake(_PAGES)
        self.calls = 0
        self.hold = False
        self.arrived = threading.Event()
        self.release = threading.Event()
        self.daily_at = daily_at

    def __call__(self, request: httpx.Request) -> httpx.Response:
        index, self.calls = self.calls, self.calls + 1
        if self.hold and index >= 1:
            self.arrived.set()
            assert self.release.wait(10)
        if index == self.daily_at:
            return httpx.Response(429, json=_DAILY_BODY, headers=_DAILY_HEADERS)
        return self.fake.default(request)


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "settings.env"))
    for var in ("OPENALEX_API_KEY", "SCOPUS_API_KEY"):  # only the keys saved in the app count
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # a stray ./.env must not be what supplies the key
    write_user_settings({"OPENALEX_API_KEY": "oa-test-key-not-real"}, tmp_path / "settings.env")
    tasks.reset_runners()
    yield
    tasks.reset_runners()


def _project(tmp_path: Path, name: str = "p") -> Project:
    project = layer.create_project(
        "demo", title="Demo", root=tmp_path / name, source_mode="openalex", reviewer="A"
    )
    with layer.Session(project) as session:
        layer.save_query(session, QueryInputs(terms=["carbon"], year_start=2015, year_end=2024))
    return project


def _start(
    project: Project, *, budget: int = 10, refresh: bool = False, confirmed: bool = True
) -> None:
    cl.note_size_check(project, build_openalex_query_for_project(project))
    with layer.Session(project) as session:
        oal.start_openalex_capture(session, budget=budget, confirmed=confirmed, refresh=refresh)


def _finish(project: Project) -> cl.CaptureStatus:
    tasks.runner_for(project).join(10)
    return oal.openalex_status(project)


def _wait(project: Project, done: Callable[[cl.CaptureStatus], bool]) -> cl.CaptureStatus:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = oal.openalex_status(project)
        if done(status):
            return status
        time.sleep(0.01)
    raise AssertionError(f"state never reached; last {oal.openalex_status(project)}")


def _runs(project: Project) -> list[Path]:
    return sorted((project.raw_dir / OPENALEX_SEARCH_DIRNAME).iterdir())


def _text(view: pn.viewable.Viewable) -> str:
    return "\n".join(str(p.object) for p in view.select(pn.pane.Markdown))


def test_start__without_confirmation_size_check_or_budget__is_refused(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with respx.mock:
        route = respx.get(_URL).mock(side_effect=Stub())
        with pytest.raises(ValidationError, match="Confirm"):
            _start(project, confirmed=False)
        cl.note_size_check(project, None)
        with layer.Session(project) as session, pytest.raises(ValidationError, match="size"):
            oal.start_openalex_capture(session, budget=5, confirmed=True)
        with pytest.raises(ValidationError, match="at least 1"):
            _start(project, budget=0)
        assert route.call_count == 0
    assert oal.openalex_status(project).state is S.NOT_STARTED


def test_capture__budget_progress_cancel_resume_and_seal(tmp_path: Path) -> None:
    project = _project(tmp_path)
    stub = Stub()
    with respx.mock:
        respx.get(_URL).mock(side_effect=stub)
        _start(project, budget=1)
        stopped = _finish(project)
        assert stopped.state is S.UNSEALED and stopped.pages_done == 1  # the budget, not the end
        assert "page budget of 1" in stopped.message
        assert stub.calls == 1

        stub.hold = True
        _start(project, budget=10)
        assert stop_model.running_names() == [oal.TASK_NAME]
        assert stub.arrived.wait(10)
        running = _wait(project, lambda s: s.pages_done == 1 and s.state is S.RUNNING)
        assert running.total_results == 5
        cl.cancel_capture(project)  # the shared runner's cancel
        assert oal.openalex_status(project).state is S.CANCELLING
        stub.release.set()
        cancelled = _finish(project)
        assert cancelled.state is S.UNSEALED and cancelled.pages_done == 2
        (run,) = _runs(project)
        assert not is_sealed(run)

        stub.hold = False
        _start(project)  # resumes the unsealed run
        sealed = _finish(project)
    assert sealed.state is S.SEALED and _runs(project) == [run] and is_sealed(run)
    with pytest.raises(ValidationError, match="Sealed"):
        _start(project)


def test_discard__confirmed__marks_the_run_and_still_blocks_a_source_mode_change(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    with respx.mock:
        respx.get(_URL).mock(side_effect=Stub())
        _start(project, budget=1)
        _finish(project)
    (run,) = _runs(project)
    pages = {p.name: p.read_bytes() for p in run.glob("response-*.jsonl")}
    with layer.Session(project) as session:
        with pytest.raises(ValidationError, match="Confirm"):
            cl.discard_capture(session, run.name, confirmed=False)
        assert oal.openalex_status(project).state is S.UNSEALED
        cl.discard_capture(session, run.name, confirmed=True)
    status = oal.openalex_status(project)
    assert status.state is S.DISCARDED
    assert "Source mode" in _text(build_capture(project))
    assert {p.name: p.read_bytes() for p in run.glob("response-*.jsonl")} == pages
    with layer.Session(project) as session, pytest.raises((ConfigError, StepLockedError)):
        layer.set_source_mode(session, "scopus", reviewer="A")


def test_daily_allowance__runs_out__says_when_it_resets_and_keeps_the_pages(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    before = datetime.now(UTC)
    with respx.mock:
        respx.get(_URL).mock(side_effect=Stub(daily_at=2))
        _start(project)
        status = _finish(project)
    assert status.state is S.QUOTA_WAIT and status.pages_done == 2
    assert "(local time)" in status.message
    # the server's wait is 34265 s: the stated time is that far after the stop, in this zone
    stated = status.message.split("resets at ")[1].split(" (local")[0]
    assert stated in {
        local_reset(datetime.fromtimestamp(before.timestamp() + 34265 + d, UTC)) for d in range(60)
    }
    (run,) = _runs(project)
    assert len(list(run.glob("response-*.jsonl"))) == 2
    assert oal.STATE_TEXT[S.QUOTA_WAIT] in _text(build_capture(project))


def test_openalex_only_project__reaches_a_sealed_run_with_no_scopus_key(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert project.source_mode == "openalex"
    with respx.mock:
        respx.get(_URL).mock(side_effect=Stub())
        _start(project)
        assert _finish(project).state is S.SEALED
    assert [is_sealed(r) for r in _runs(project)] == [True]


def test_refresh__sealed_run__starts_a_second_run_and_seals_it(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with respx.mock:
        respx.get(_URL).mock(side_effect=Stub())
        _start(project)
        _finish(project)
        sealed_files = {p: p.read_bytes() for r in _runs(project) for p in r.iterdir()}
        view = build_capture(project)
        again = next(
            b for b in view.select(pn.widgets.Button) if b.name.startswith("Capture again")
        )
        assert not again.disabled
        _start(project, refresh=True)
        assert _finish(project).state is S.SEALED
    assert len(_runs(project)) == 2 and all(is_sealed(r) for r in _runs(project))
    assert all(p.read_bytes() == data for p, data in sealed_files.items())


def test_screen__headless_build__every_control_is_labelled_and_budget_is_set(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    view = build_capture(project)
    assert unlabelled_controls(view) == []
    assert unlabelled_controls(build_capture_step(project)) == []
    budget = next(iter(view.select(pn.widgets.IntInput)))
    assert budget.visible and budget.start == 1
    assert "Scopus" not in _text(view)


def test_screen__start_button__runs_with_the_budget_the_user_set(tmp_path: Path) -> None:
    project = _project(tmp_path)
    cl.note_size_check(project, build_openalex_query_for_project(project))
    view = build_capture(project)
    start = next(b for b in view.select(pn.widgets.Button) if b.name == "Start the capture")
    next(iter(view.select(pn.widgets.IntInput))).value = 2
    stub = Stub()
    with respx.mock:
        respx.get(_URL).mock(side_effect=stub)
        start.clicks += 1
        assert "Confirm" in _text(view)
        next(iter(view.select(pn.widgets.Checkbox))).value = True
        start.clicks += 1
        status = _finish(project)
    assert stub.calls == 2 and status.state is S.UNSEALED
