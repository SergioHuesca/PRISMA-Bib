"""Ctrl+C or a closed terminal: the app cancels what runs, waits, and frees its locks."""

from __future__ import annotations

import asyncio
import os
import queue
import signal
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pytest_socket import enable_socket, socket_allow_hosts

pytest.importorskip("panel")

from prismabib.errors import OperationCancelled
from prismabib.gui import service, stop_model, tasks
from prismabib.gui.launch import serve
from prismabib.project import Project
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "settings.env"))
    enable_socket()
    socket_allow_hosts(["127.0.0.1"], allow_unix_socket=True)
    tasks.reset_runners()
    stop_model.install_stopper(None)
    yield
    service.release_all()
    tasks.reset_runners()
    stop_model.install_stopper(None)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return service.create_project(
        "demo", title="Demo", root=tmp_path / "p", source_mode="scopus", reviewer="A"
    )


class _Capture:
    """A fake capture: runs until its cancel flag is set, then stops as a real one does."""

    def __init__(self, project: Project, *, stubborn: float = 0.0) -> None:
        self.started, self.saw_cancel = threading.Event(), threading.Event()
        self.finish = threading.Event()

        def work(cancel: threading.Event, report: Callable[[object], None]) -> None:
            self.started.set()
            assert cancel.wait(30)
            self.saw_cancel.set()
            if stubborn:  # a page that takes a while to finish saving
                self.finish.wait(stubborn)
            raise OperationCancelled("stopped")

        tasks.runner_for(project).start("The Scopus capture", work)
        assert self.started.wait(10)


def _serve(
    run: Callable[[Any], None], **kwargs: Any
) -> tuple[threading.Thread, list[str], list[BaseException]]:
    """``serve`` on its own thread (it needs an event loop), returning (thread, urls, errors)."""
    urls: list[str] = []
    errors: list[BaseException] = []

    def target() -> None:
        asyncio.set_event_loop(asyncio.new_event_loop())
        try:
            urls.append(serve(port=0, no_browser=True, run=run, **kwargs))
        except BaseException as exc:  # noqa: BLE001 - handed to the test thread to assert on
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread, urls, errors


def _interrupt(_server: Any) -> None:
    raise KeyboardInterrupt


def test_serve__ctrl_c_with_a_capture_running__cancels_it_waits_and_releases_the_lock(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    held = service.open_session("demo", root=project.root.parent)
    capture = _Capture(project)

    thread, urls, errors = _serve(_interrupt)
    thread.join(60)

    assert not thread.is_alive() and not errors
    assert urls and urls[0].startswith("http://127.0.0.1:")  # Ctrl+C is a normal way to stop
    assert capture.saw_cancel.is_set()  # the cancel flag was set, not left running
    assert tasks.runner_for(project).status().running is False  # and the worker was joined
    assert not (project.root / service.LOCK_NAME).exists()
    assert held.read_only
    out = capsys.readouterr().out
    assert "The Scopus capture" in out and "pages already fetched" in out


def test_serve__stop_button_path__loop_stops_and_everything_is_cleaned_up(
    project: Project,
) -> None:
    service.open_session("demo", root=project.root.parent)
    capture = _Capture(project)
    server_box: list[Any] = []
    ready = threading.Event()

    def run(server: Any) -> None:  # what the Stop button ends: the loop stops, run returns
        server_box.append(server)
        server.start()
        ready.set()
        server.io_loop.start()

    thread, _urls, errors = _serve(run)
    assert ready.wait(60)
    loop = server_box[0].io_loop
    loop.add_callback(loop.stop)
    thread.join(60)

    assert not errors and capture.saw_cancel.is_set()
    assert not (project.root / service.LOCK_NAME).exists()


def test_serve__a_task_that_does_not_stop_in_time__is_named_and_its_lock_kept_until_it_ends(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    service.open_session("demo", root=project.root.parent)
    capture = _Capture(project, stubborn=30)

    thread, _urls, errors = _serve(_interrupt, shutdown_wait=0.2)
    thread.join(60)

    out = capsys.readouterr().out
    assert not errors and capture.saw_cancel.is_set()
    assert "did not stop" in out and "The Scopus capture" in out
    assert (project.root / service.LOCK_NAME).exists()  # still writing: the lock stays
    capture.finish.set()
    tasks.runner_for(project).join(10)
    assert not (project.root / service.LOCK_NAME).exists()  # released the moment it ended


def test_serve__nothing_running__says_it_stopped_and_frees_the_lock(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    service.open_session("demo", root=project.root.parent)

    thread, _urls, errors = _serve(_interrupt)
    thread.join(60)

    assert not errors
    assert not (project.root / service.LOCK_NAME).exists()
    assert "stopped" in capsys.readouterr().out.lower()


# --- a system stop (SIGTERM, SIGHUP) takes the same road as Ctrl+C --------------------------------

_CHILD = """
import asyncio, sys, threading
from pathlib import Path
from prismabib.errors import OperationCancelled
from prismabib.gui import service, tasks
from prismabib.gui.launch import serve

root, capture = Path(sys.argv[1]), sys.argv[2] == "capture"
project = service.open_session("demo", root=root).project
if capture:
    started = threading.Event()

    def work(cancel, report):
        started.set()
        cancel.wait(30)
        raise OperationCancelled("stopped")

    tasks.runner_for(project).start("The Scopus capture", work)
    assert started.wait(10)
asyncio.set_event_loop(asyncio.new_event_loop())
serve(port=0, no_browser=True)
"""


def _stop_child(project: Project, sig: int, *, capture: bool) -> tuple[int | None, str]:
    """Run the launcher in a child on its main thread, send ``sig``; return (exit code, output)."""
    mode = "capture" if capture else "idle"
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", _CHILD, str(project.root.parent), mode],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines: queue.Queue[str] = queue.Queue()
    threading.Thread(target=lambda: [lines.put(x) for x in child.stdout or ()], daemon=True).start()
    seen = ""
    try:
        while "is running at" not in seen:  # a hung child fails here, it does not hang the run
            seen += lines.get(timeout=120)
        os.kill(child.pid, sig)
        child.wait(timeout=120)
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(30)
    while not lines.empty():
        seen += lines.get()
    return child.returncode, seen


@pytest.mark.skipif(sys.platform == "win32", reason="os.kill(SIGTERM) ends the process on Windows")
@pytest.mark.parametrize("capture", [True, False])
def test_serve__sigterm__frees_the_lock_and_says_it_stopped(
    project: Project, capture: bool
) -> None:
    code, out = _stop_child(project, signal.SIGTERM, capture=capture)

    assert code == 0, out
    assert not (project.root / service.LOCK_NAME).exists()
    assert "prismabib has stopped" in out
    assert ("pages already fetched" in out) is capture


@pytest.mark.skipif(not hasattr(signal, "SIGHUP"), reason="no SIGHUP on this platform")
def test_serve__sighup__frees_the_lock_and_says_it_stopped(project: Project) -> None:
    code, out = _stop_child(project, signal.SIGHUP, capture=True)  # type: ignore[attr-defined,unused-ignore]

    assert code == 0, out
    assert not (project.root / service.LOCK_NAME).exists()
    assert "prismabib has stopped" in out


def _handlers() -> dict[signal.Signals, Any]:
    return {s: signal.getsignal(s) for s in signal.valid_signals()}


def test_serve__on_the_main_thread__handlers_are_installed_during_and_restored_after(
    project: Project,
) -> None:
    before = _handlers()
    during: list[Any] = []

    def run(_server: Any) -> None:
        during.append(signal.getsignal(signal.SIGTERM))
        raise KeyboardInterrupt

    asyncio.set_event_loop(asyncio.new_event_loop())
    serve(port=0, no_browser=True, run=run)

    assert during[0] is not before[signal.SIGTERM]  # installed on every platform
    assert _handlers() == before


def _serve_here(**kwargs: Any) -> BaseException | None:
    """``serve`` on this (main) thread; return what escaped it, so a stray interrupt fails the test."""
    asyncio.set_event_loop(asyncio.new_event_loop())
    try:
        serve(port=0, no_browser=True, **kwargs)
    except BaseException as exc:  # noqa: BLE001 - the test asserts on it
        return exc
    return None


def test_serve__signal_during_the_shutdown__does_not_interrupt_it_and_the_lock_is_freed(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    service.open_session("demo", root=project.root.parent)
    before = _handlers()
    real_stop_all = tasks.stop_all

    def stubborn_stop_all(wait: float) -> list[str]:
        signal.raise_signal(signal.SIGTERM)  # runs the Python handler synchronously
        return real_stop_all(wait)

    monkeypatch.setattr(tasks, "stop_all", stubborn_stop_all)

    escaped = _serve_here(run=lambda _server: None)  # the Stop button: run returns normally

    assert escaped is None
    assert not (project.root / service.LOCK_NAME).exists()
    assert _handlers() == before


def test_serve__signal_while_the_server_is_created__still_shuts_down_and_restores_handlers(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismabib.gui import launch

    service.open_session("demo", root=project.root.parent)
    before = _handlers()
    shut_down: list[float] = []
    real_shut_down = launch._shut_down

    def create_then_signal(*_args: Any) -> Any:
        signal.raise_signal(signal.SIGTERM)

    def spy(wait: float, **kwargs: Any) -> None:
        shut_down.append(wait)
        real_shut_down(wait, **kwargs)

    monkeypatch.setattr(launch, "_create_server", create_then_signal)
    monkeypatch.setattr(launch, "_shut_down", spy)

    escaped = _serve_here(run=lambda _server: None)

    assert escaped is None and len(shut_down) == 1
    assert not (project.root / service.LOCK_NAME).exists()
    assert _handlers() == before


def test_serve__server_creation_fails__no_stopped_line_and_the_error_propagates(
    project: Project, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from prismabib.errors import ConfigError
    from prismabib.gui import launch

    before = _handlers()

    def no_port(*_args: Any) -> Any:
        raise ConfigError("No free port on 127.0.0.1.")

    monkeypatch.setattr(launch, "_create_server", no_port)

    escaped = _serve_here(run=lambda _server: None)

    assert isinstance(escaped, ConfigError)
    assert "has stopped" not in capsys.readouterr().out
    assert _handlers() == before


def test_serve__on_a_worker_thread__installs_no_handler(project: Project) -> None:
    before = _handlers()
    during: list[Any] = []

    def run(_server: Any) -> None:
        during.append(_handlers())
        raise KeyboardInterrupt

    thread, _urls, errors = _serve(run)
    thread.join(60)

    assert not errors and during == [before]
