"""Ctrl+C or a closed terminal: the app cancels what runs, waits, and frees its locks."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from pytest_socket import enable_socket, socket_allow_hosts

pytest.importorskip("panel")

from prismabib.errors import OperationCancelled
from prismabib.gui import layer, stop_model, tasks
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
    layer.release_all()
    tasks.reset_runners()
    stop_model.install_stopper(None)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return layer.create_project(
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
    held = layer.open_session("demo", root=project.root.parent)
    capture = _Capture(project)

    thread, urls, errors = _serve(_interrupt)
    thread.join(60)

    assert not thread.is_alive() and not errors
    assert urls and urls[0].startswith("http://127.0.0.1:")  # Ctrl+C is a normal way to stop
    assert capture.saw_cancel.is_set()  # the cancel flag was set, not left running
    assert tasks.runner_for(project).status().running is False  # and the worker was joined
    assert not (project.root / layer.LOCK_NAME).exists()
    assert held.read_only
    out = capsys.readouterr().out
    assert "The Scopus capture" in out and "pages already fetched" in out


def test_serve__stop_button_path__loop_stops_and_everything_is_cleaned_up(
    project: Project,
) -> None:
    layer.open_session("demo", root=project.root.parent)
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
    assert not (project.root / layer.LOCK_NAME).exists()


def test_serve__a_task_that_does_not_stop_in_time__is_named_and_its_lock_kept_until_it_ends(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    layer.open_session("demo", root=project.root.parent)
    capture = _Capture(project, stubborn=30)

    thread, _urls, errors = _serve(_interrupt, shutdown_wait=0.2)
    thread.join(60)

    out = capsys.readouterr().out
    assert not errors and capture.saw_cancel.is_set()
    assert "did not stop" in out and "The Scopus capture" in out
    assert (project.root / layer.LOCK_NAME).exists()  # still writing: the lock stays
    capture.finish.set()
    tasks.runner_for(project).join(10)
    assert not (project.root / layer.LOCK_NAME).exists()  # released the moment it ended


def test_serve__nothing_running__says_it_stopped_and_frees_the_lock(
    project: Project, capsys: pytest.CaptureFixture[str]
) -> None:
    layer.open_session("demo", root=project.root.parent)

    thread, _urls, errors = _serve(_interrupt)
    thread.join(60)

    assert not errors
    assert not (project.root / layer.LOCK_NAME).exists()
    assert "stopped" in capsys.readouterr().out.lower()
