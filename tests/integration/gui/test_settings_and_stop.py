"""The Settings view and "Stop the app", headless (ticket #323). Only the HTTP transport is stubbed."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from pytest_socket import enable_socket, socket_allow_hosts

pytest.importorskip("panel")

import panel as pn

from prismabib.capture.layout import is_sealed
from prismabib.capture.progress import CaptureProgress
from prismabib.capture.writer import capture_search
from prismabib.errors import CaptureCancelledError
from prismabib.gui import stop_model
from prismabib.gui.home import build_home
from prismabib.gui.launch import serve
from prismabib.gui.settings import build_settings
from prismabib.project import Project
from prismabib.user_settings import (
    REVIEWER_NAME_KEY,
    USER_FILE_OVERRIDE_VAR,
    read_user_setting,
)
from tests.integration.gui.test_home import PASSING, _button, _text, unlabelled_controls

pytestmark = pytest.mark.integration

_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"


@pytest.fixture(autouse=True)
def _user_file(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path_factory.mktemp("user") / "settings.env"
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(path))
    return path


@pytest.fixture(autouse=True)
def _no_stopper() -> Iterator[None]:
    stop_model.install_stopper(None)
    yield
    stop_model.install_stopper(None)


def test_build_settings__headless__shows_both_locations_and_every_control_is_labelled(
    tmp_path: Path, _user_file: Path
) -> None:
    view = build_settings(tmp_path)
    text = _text(view)
    assert str(tmp_path) in text and str(_user_file.resolve()) in text
    assert [w.name for w in view.select(pn.widgets.Widget)] == [
        "Reviewer name",
        "Save reviewer name",
    ]
    assert unlabelled_controls(view) == []


def test_build_settings__saving_a_name__is_read_back_from_the_file_and_the_next_view(
    tmp_path: Path,
) -> None:
    view = build_settings(tmp_path)
    next(iter(view.select(pn.widgets.TextInput))).value = "Ada L."
    _button(view, "Save reviewer name").clicks += 1
    assert read_user_setting(REVIEWER_NAME_KEY) == "Ada L."
    assert next(iter(build_settings(tmp_path).select(pn.widgets.TextInput))).value == "Ada L."


def test_build_settings__blank_name__says_what_to_do(tmp_path: Path) -> None:
    view = build_settings(tmp_path)
    _button(view, "Save reviewer name").clicks += 1
    assert "Enter the reviewer name" in _text(view)
    assert read_user_setting(REVIEWER_NAME_KEY) is None


def test_stop_button__nothing_running__stops_the_app(tmp_path: Path) -> None:
    stopped: list[int] = []
    stop_model.install_stopper(lambda: stopped.append(1))
    view = build_home(tmp_path, checks=lambda: PASSING)
    _button(view, "Stop the app").clicks += 1
    assert stopped == [1]
    assert not _button(view, "Yes, stop the app").visible


def test_stop_button__task_running__confirms_first_then_stops_on_yes(tmp_path: Path) -> None:
    stopped: list[int] = []
    stop_model.install_stopper(lambda: stopped.append(1))
    cancel = threading.Event()
    task = stop_model.register("capture", cancel)
    try:
        view = build_home(tmp_path, checks=lambda: PASSING)
        _button(view, "Stop the app").clicks += 1
        assert stopped == [] and "unsealed run" in _text(view)
        assert _button(view, "Yes, stop the app").visible

        _button(view, "Keep the app running").clicks += 1  # declining changes nothing
        assert stopped == [] and not cancel.is_set()
        assert not _button(view, "Yes, stop the app").visible

        _button(view, "Stop the app").clicks += 1
        _button(view, "Yes, stop the app").clicks += 1
        assert stopped == [1] and cancel.is_set()
    finally:
        stop_model.unregister(task)


def test_stop_button__every_control__is_labelled(tmp_path: Path) -> None:
    view = build_home(tmp_path, checks=lambda: PASSING)
    assert {"Stop the app", "Yes, stop the app", "Keep the app running"} <= {
        w.name for w in view.select(pn.widgets.Button)
    }
    assert unlabelled_controls(view) == []


class _Served:
    """``serve(no_browser=True)`` on a thread with its own loop, as the CLI runs it."""

    def __init__(self) -> None:
        self.server: Any = None
        self.url: str | None = None
        ready = threading.Event()

        def run(server: Any) -> None:
            self.server = server
            server.start()
            ready.set()
            server.io_loop.start()

        def target() -> None:
            asyncio.set_event_loop(asyncio.new_event_loop())
            self.url = serve(port=0, no_browser=True, run=run)

        self.thread = threading.Thread(target=target, daemon=True)
        self.thread.start()
        assert ready.wait(60)


def test_stop__no_browser_server__request_stop_ends_serve() -> None:
    enable_socket()
    socket_allow_hosts(["127.0.0.1"], allow_unix_socket=True)
    served = _Served()
    assert served.thread.is_alive()
    assert stop_model.request_stop(confirmed=False).stopping
    served.thread.join(30)
    assert not served.thread.is_alive() and served.url is not None


def _page(ids: list[str], current: str, next_cursor: str | None) -> dict[str, Any]:
    return {
        "search-results": {
            "opensearch:totalResults": "3",
            "entry": [{"dc:identifier": f"SCOPUS_ID:{i}"} for i in ids],
            "cursor": {"@current": current, "@next": next_cursor or ""},
        }
    }


def test_stop__capture_running__confirmed_stop_leaves_an_unsealed_run_a_later_capture_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = Project.init("demo", title="Demo", root=tmp_path)
    pages = [_page(["1"], "*", "C1"), _page(["2"], "C1", "C2"), _page(["3"], "C2", None)]
    cancel = threading.Event()
    on_page_one, release = threading.Event(), threading.Event()
    errors: list[BaseException] = []

    def on_progress(p: CaptureProgress) -> None:
        if p.event == "page" and p.pages_done == 1:
            on_page_one.set()
            assert release.wait(30)

    def work() -> None:
        task = stop_model.register("capture", cancel)
        try:
            capture_search(
                project, query='TITLE-ABS-KEY("x")', cancel=cancel, on_progress=on_progress
            )
        except CaptureCancelledError as exc:
            errors.append(exc)
        finally:
            stop_model.unregister(task)

    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(side_effect=[httpx.Response(200, json=pages[0])])
        thread = threading.Thread(target=work)
        thread.start()
        assert on_page_one.wait(30)

        asked = stop_model.request_stop(confirmed=False)
        assert asked.confirm and not cancel.is_set()  # nothing stopped before the confirmation
        assert stop_model.request_stop(confirmed=True).stopping
        release.set()
        thread.join(30)

        assert len(errors) == 1 and route.call_count == 1
        (run_dir,) = [d for d in project.raw_dir.iterdir() if d.is_dir() and d.name != "_cache"]
        assert not is_sealed(run_dir)

        route.side_effect = [httpx.Response(200, json=p) for p in pages[1:]]
        manifest = capture_search(project, query='TITLE-ABS-KEY("x")')

    assert manifest.run_id == run_dir.name and manifest.pages_fetched == 3
