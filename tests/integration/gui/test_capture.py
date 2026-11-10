"""Scopus capture through the layer and the screen, over a stubbed transport (ticket #329)."""

from __future__ import annotations

import hashlib
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import httpx
import pytest
import respx

pytest.importorskip("panel")

import panel as pn

from prismabib.capture.layout import SealedRunError, is_sealed
from prismabib.capture.writer import capture_search
from prismabib.errors import ProjectLockedError, ValidationError
from prismabib.gui import capture_layer as cl
from prismabib.gui import layer, stop_model, tasks
from prismabib.gui.capture import build_capture, build_capture_step
from prismabib.gui.capture_layer import CaptureState as S
from prismabib.gui.query_form_model import QueryInputs
from prismabib.project import Project
from prismabib.query import build_query_for_project
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.integration.gui.test_home import unlabelled_controls

pytestmark = pytest.mark.integration

_URL = "https://api.elsevier.com/content/search/scopus"


def _page(ids: list[str], current: str, nxt: str | None) -> dict[str, object]:
    return {
        "search-results": {
            "opensearch:totalResults": "3",
            "entry": [{"dc:identifier": f"SCOPUS_ID:{i}"} for i in ids],
            "cursor": {"@current": current, "@next": nxt or ""},
        }
    }


PAGES = [_page(["1"], "*", "C1"), _page(["2"], "C1", "C2"), _page(["3"], "C2", None)]


class Stub:
    """Serves ``PAGES`` in order; once ``hold`` is armed the next request waits for ``release``."""

    def __init__(self, fail_at: int | None = None, error: Callable[[], object] | None = None):
        self.calls = 0
        self.arrived = threading.Event()
        self.release = threading.Event()
        self.hold = False
        self.fail_at, self.error = fail_at, error

    def __call__(self, request: httpx.Request) -> httpx.Response:
        index, self.calls = self.calls, self.calls + 1
        if self.hold and index >= 1:
            self.arrived.set()
            assert self.release.wait(10)
        if index == self.fail_at and self.error is not None:
            raise self.error()  # type: ignore[misc]
        cursor = request.url.params.get("cursor", "*")
        order = ["*", "C1", "C2"]
        return httpx.Response(200, json=PAGES[order.index(cursor)])


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "settings.env"))
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    tasks.reset_runners()
    yield
    tasks.reset_runners()


def _project(tmp_path: Path) -> Project:
    project = layer.create_project(
        "demo", title="Demo", root=tmp_path / "p", source_mode="scopus", reviewer="A"
    )
    with layer.Session(project) as session:
        layer.save_query(session, QueryInputs(terms=["carbon"], year_start=2015, year_end=2024))
    return project


def _start(project: Project, *, checked: bool = True, confirmed: bool = True) -> None:
    if checked:
        cl.note_size_check(project, build_query_for_project(project))
    with layer.Session(project) as session:
        cl.start_capture(session, confirmed=confirmed)


def _wait(project: Project, done: Callable[[cl.CaptureStatus], bool]) -> cl.CaptureStatus:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        status = cl.capture_status(project)
        if done(status):
            return status
        time.sleep(0.01)
    raise AssertionError(f"state never reached; last {cl.capture_status(project)}")


def _finish(project: Project) -> cl.CaptureStatus:
    tasks.runner_for(project).join(10)
    return cl.capture_status(project)


def _tree(root: Path, skip: Callable[[Path], bool] = lambda p: False) -> str:
    h = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not skip(p)):
        h.update(path.relative_to(root).as_posix().encode() + path.read_bytes())
    return h.hexdigest()


def _runs(project: Project) -> list[Path]:
    return [p for p in project.raw_dir.iterdir() if p.is_dir() and p.name != "_cache"]


def _text(view: pn.viewable.Viewable) -> str:
    return "\n".join(str(p.object) for p in view.select(pn.pane.Markdown))


# -- the confirmation gate ---------------------------------------------------------------------


def test_start__without_confirmation_or_size_check__is_refused(tmp_path: Path) -> None:
    project = _project(tmp_path)
    with respx.mock:
        route = respx.get(_URL).mock(side_effect=Stub())
        with pytest.raises(ValidationError, match="Confirm"):
            _start(project, confirmed=False)
        cl.note_size_check(project, None)
        with pytest.raises(ValidationError, match="size"):
            _start(project, checked=False)
        cl.note_size_check(project, "an older query")  # a check of a query that is not the current
        with pytest.raises(ValidationError, match="size"):
            _start(project, checked=False)
        assert route.call_count == 0
    assert cl.capture_status(project).state is S.NOT_STARTED


def test_start__read_only_session__is_refused(tmp_path: Path) -> None:
    project = _project(tmp_path)
    cl.note_size_check(project, build_query_for_project(project))
    layer.Session(project)  # the first window holds the lock
    with layer.Session(project) as second, pytest.raises(ProjectLockedError):
        cl.start_capture(second, confirmed=True)


# -- running, polling, cancel, resume ----------------------------------------------------------


def test_capture__runs_cancels_resumes_and_seals_with_a_pollable_status(tmp_path: Path) -> None:
    project = _project(tmp_path)
    stub = Stub()
    with respx.mock:
        respx.get(_URL).mock(side_effect=stub)
        stub.hold = True
        assert cl.capture_status(project).state is S.NOT_STARTED
        _start(project)
        assert stop_model.running_names() == [cl.TASK_NAME]
        assert stub.arrived.wait(10)  # page 1 is saved, the request for page 2 is held
        running = _wait(project, lambda s: s.pages_done == 1)
        t0 = time.monotonic()
        polled = cl.capture_status(project)
        assert time.monotonic() - t0 < 1
        assert polled.state is S.RUNNING
        assert (polled.pages_done, polled.records_seen, polled.total_results) == (1, 1, 3)
        with pytest.raises(ValidationError, match="still running"):
            _start(project)  # one task at a time

        cl.cancel_capture(project)
        assert cl.capture_status(project).state is S.CANCELLING
        stub.release.set()
        cancelled = _finish(project)
        assert running.run_id == cancelled.run_id
        assert cancelled.state is S.UNSEALED
        assert cancelled.pages_done == 2
        assert stop_model.running_names() == []
        (run,) = _runs(project)
        assert not is_sealed(run)

        stub.hold = False
        _start(project)  # a new capture resumes the unsealed run
        sealed = _finish(project)
    assert sealed.state is S.SEALED
    assert _runs(project) == [run] and is_sealed(run)
    with pytest.raises(ValidationError, match="Sealed"):
        _start(project)


def test_stop_the_app__running_capture__is_cancelled_through_the_registry(tmp_path: Path) -> None:
    project = _project(tmp_path)
    stub = Stub()
    stub.hold = True
    with respx.mock:
        respx.get(_URL).mock(side_effect=stub)
        _start(project)
        assert stub.arrived.wait(10)
        assert stop_model.request_stop(confirmed=False).confirm is not None
        stop_model.request_stop(confirmed=True)
        stub.release.set()
        assert _finish(project).state is S.UNSEALED


def test_runner__worker_thread__is_not_a_daemon(tmp_path: Path) -> None:
    project = _project(tmp_path)
    seen: list[bool] = []
    runner = tasks.runner_for(project)
    runner.start("t", lambda cancel, report: seen.append(threading.current_thread().daemon))
    runner.join(5)
    assert seen == [False]
    assert runner.status().outcome == "done"


# -- network loss and quota --------------------------------------------------------------------


def test_network_loss__keeps_pages_and_a_rerun_continues(tmp_path: Path) -> None:
    project = _project(tmp_path)
    stub = Stub(fail_at=2, error=lambda: httpx.ConnectError("offline"))
    with respx.mock:
        respx.get(_URL).mock(side_effect=stub)
        _start(project)
        stopped = _finish(project)
        assert stopped.state is S.NETWORK_STOPPED
        assert "run the step again" in stopped.message
        (run,) = _runs(project)
        kept = {p.name: p.read_bytes() for p in run.glob("page-*.jsonl")}
        assert sorted(kept) == ["page-0000.jsonl", "page-0001.jsonl"]

        _start(project)
        assert _finish(project).state is S.SEALED
    assert all((run / name).read_bytes() == data for name, data in kept.items())


def test_quota_error__states_a_sentence_and_the_next_step(tmp_path: Path) -> None:
    project = _project(tmp_path)
    reset = str(time.time() + 10 * 86400)
    with respx.mock:
        respx.get(_URL).mock(
            return_value=httpx.Response(
                429, headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": reset}
            )
        )
        _start(project)
        status = _finish(project)
    assert status.state is S.QUOTA_WAIT
    assert "quota" in status.message and "Wait for the allowance to reset" in status.message


# -- discard -----------------------------------------------------------------------------------


def test_discard__needs_confirmation_and_changes_nothing_else(tmp_path: Path) -> None:
    project = _project(tmp_path)
    flag = threading.Event()
    with respx.mock:
        respx.get(_URL).mock(side_effect=Stub())
        sealed = capture_search(project, query='TITLE-ABS-KEY("other")')
        flag.set()  # cancelled after its first page: unsealed
        with pytest.raises(Exception, match="cancelled"):
            capture_search(project, cancel=flag)
    unsealed = next(r for r in _runs(project) if not is_sealed(r))
    sealed_dir = project.raw_dir / sealed.run_id
    before = _tree(project.raw_dir, lambda p: p == unsealed / "cursor.json")
    sealed_before = _tree(sealed_dir)

    with layer.Session(project) as session:
        with pytest.raises(ValidationError, match="Confirm"):
            cl.discard_capture(session, unsealed.name, confirmed=False)
        assert cl.capture_status(project).state is S.UNSEALED
        cl.discard_capture(session, unsealed.name, confirmed=True)
        with pytest.raises(SealedRunError):
            cl.discard_capture(session, sealed.run_id, confirmed=True)

    assert _tree(project.raw_dir, lambda p: p == unsealed / "cursor.json") == before
    assert _tree(sealed_dir) == sealed_before
    assert cl.capture_status(project).state is S.SEALED  # the discarded run no longer counts


def test_discard__only_unsealed_run__state_is_discarded(tmp_path: Path) -> None:
    project = _project(tmp_path)
    flag = threading.Event()
    flag.set()
    with respx.mock:
        respx.get(_URL).mock(side_effect=Stub())
        with pytest.raises(Exception, match="cancelled"):
            capture_search(project, query=build_query_for_project(project), cancel=flag)
    (run,) = _runs(project)
    with layer.Session(project) as session:
        cl.discard_capture(session, run.name, confirmed=True)
    assert cl.capture_status(project).state is S.DISCARDED


def test_operations_and_controls__none_deletes_or_overwrites_a_sealed_run(tmp_path: Path) -> None:
    project = _project(tmp_path)
    ops = set(layer.WRITE_OPERATIONS) | {n for n in dir(cl) if not n.startswith("_")}
    assert not [n for n in ops if any(w in n for w in ("delete", "remove", "overwrite", "erase"))]
    view = build_capture(project)
    names = [w.name.lower() for w in view.select(pn.widgets.Widget)]
    assert not [n for n in names if any(w in n for w in ("delete", "remove", "overwrite"))]


# -- the screen --------------------------------------------------------------------------------


def test_screen__each_of_the_eight_states__has_its_own_text(tmp_path: Path) -> None:
    project = _project(tmp_path)
    shown: dict[S, str] = {}

    def see() -> None:
        state = cl.capture_status(project).state
        shown[state] = _text(build_capture(project))
        assert cl.STATE_TEXT[state] in shown[state]

    see()  # not started
    stub = Stub()
    with respx.mock:
        respx.get(_URL).mock(side_effect=stub)
        stub.hold = True
        _start(project)
        assert stub.arrived.wait(10)
        see()  # running
        cl.cancel_capture(project)
        see()  # cancelling
        stub.release.set()
        _finish(project)
        see()  # unsealed
        stub.hold = False
        _start(project)
        _finish(project)
        see()  # sealed
    other = _project(tmp_path / "x")
    flag = threading.Event()
    flag.set()
    with respx.mock:
        respx.get(_URL).mock(side_effect=Stub())
        with pytest.raises(Exception, match="cancelled"):
            capture_search(other, query="TITLE-ABS-KEY(x)", cancel=flag)
    with layer.Session(other) as session:
        cl.discard_capture(session, _runs(other)[0].name, confirmed=True)
    shown[cl.capture_status(other).state] = _text(build_capture(other))
    net = _project(tmp_path / "n")
    with respx.mock:
        respx.get(_URL).mock(side_effect=Stub(fail_at=1, error=lambda: httpx.ConnectError("x")))
        _start(net)
        _finish(net)
        shown[cl.capture_status(net).state] = _text(build_capture(net))
        respx.get(_URL).mock(
            return_value=httpx.Response(
                429,
                headers={
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(time.time() + 10 * 86400),
                },
            )
        )
        _start(net)
        _finish(net)
        shown[cl.capture_status(net).state] = _text(build_capture(net))

    assert set(shown) == set(S)
    for state, text in shown.items():
        assert cl.STATE_TEXT[state] in text
    assert len(set(cl.STATE_TEXT.values())) == 8


def test_screen__reopened_while_running_or_after_the_app_stopped__shows_the_state(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    stub = Stub()
    stub.hold = True
    with respx.mock:
        respx.get(_URL).mock(side_effect=stub)
        _start(project)
        assert stub.arrived.wait(10)
        reopened = Project.open("demo", root=tmp_path / "p")
        assert cl.STATE_TEXT[S.RUNNING] in _text(build_capture(reopened))
        cl.cancel_capture(project)
        stub.release.set()
        _finish(project)
    tasks.reset_runners()  # the app was stopped and started again
    text = _text(build_capture(Project.open("demo", root=tmp_path / "p")))
    assert cl.STATE_TEXT[S.UNSEALED] in text and "Pages done: 2" in text


def test_screen__start_button__runs_the_capture_only_after_the_confirmation(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    cl.note_size_check(project, build_query_for_project(project))
    view = build_capture(project)
    start = next(b for b in view.select(pn.widgets.Button) if b.name == "Start the capture")
    with respx.mock:
        respx.get(_URL).mock(side_effect=Stub())
        start.clicks += 1
        assert "Confirm" in _text(view)
        assert not _runs(project)
        next(iter(view.select(pn.widgets.Checkbox))).value = True
        start.clicks += 1
        assert _finish(project).state is S.SEALED
    assert is_sealed(_runs(project)[0])


def test_screen__headless_build__every_control_is_labelled(tmp_path: Path) -> None:
    project = _project(tmp_path)
    view = build_capture(project)
    assert unlabelled_controls(view) == []
    step = build_capture_step(project)
    assert unlabelled_controls(step) == []
    assert "Capture" in _text(step)
