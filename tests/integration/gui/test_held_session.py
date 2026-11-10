"""A window holds the Project lock while its stepper is open (story 64, single-writer rule)."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.gui import layer, tasks
from prismabib.gui.app import build_app
from prismabib.gui.new_project import build_stepper
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.integration.gui.test_home import _button, _text

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "settings.env"))
    tasks.reset_runners()
    yield
    layer.release_all()
    tasks.reset_runners()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    layer.create_project(
        "demo", title="Demo", root=tmp_path / "p", source_mode="openalex", reviewer="Ada"
    )
    return tmp_path / "p"


def _lock(root: Path) -> Path:
    return root / "demo" / layer.LOCK_NAME


def _open(tabs: pn.Tabs) -> pn.Column:
    page = tabs[0]
    _button(page, "Open demo").clicks += 1
    return page


def _body_controls(page: pn.viewable.Viewable) -> list[pn.widgets.Button]:
    """Every action button on the open screen, except the stepper's own navigation."""
    nav = ("Source mode", "Query (", "Criteria (", "Size check", "Build (", "Screening (")
    banner = ("Take over", "Yes, take over", "Keep it read-only")
    return [
        b
        for b in page.select(pn.widgets.Button)
        if b.name != "Back to Projects"
        and not b.name.startswith(nav)
        and not b.name.startswith(("Flow counts (", "Export (", *banner))
    ]


def test_app__open_project__holds_the_lock_until_back_to_projects(root: Path) -> None:
    page = _open(build_app(root))

    assert _lock(root).exists()
    _button(page, "Back to Projects").clicks += 1
    assert not _lock(root).exists()
    _button(page, "Open demo").clicks += 1
    assert _lock(root).exists()  # reopening after the release takes it again


def test_app__two_windows_one_project__the_second_is_read_only_with_the_reason(
    root: Path,
) -> None:
    first = _open(build_app(root))
    second = _open(build_app(root))

    assert "Another window" in _text(second) and "read-only" in _text(second)
    assert "Another window" not in _text(first)
    for button in _body_controls(second):
        assert button.disabled or button.name.startswith("Refresh"), button.name
    assert any(not b.disabled for b in _body_controls(first))  # the holder keeps its controls


def test_read_only_window__build_step__start_is_disabled_and_stays_disabled(root: Path) -> None:
    layer.open_session("demo", root=root)  # another window holds the lock
    project = layer.open_project("demo", root=root)
    stepper = build_stepper(project, layer.open_session("demo", root=root))
    build_row = next(b for b in stepper.select(pn.widgets.Button) if b.name.startswith("Build"))
    build_row.clicks += 1

    start = _button(stepper, "Build the store")
    assert start.disabled
    start.disabled = False  # a screen's own refresh must not re-enable it
    assert start.disabled


def test_app__stop__releases_every_held_lock(root: Path) -> None:
    _open(build_app(root))
    assert _lock(root).exists()

    layer.release_all()  # what the launcher does when the server returns

    assert not _lock(root).exists()


def test_screens__held_session__are_used_instead_of_opening_their_own(root: Path) -> None:
    project = layer.open_project("demo", root=root)
    held = layer.open_session("demo", root=root)
    stepper = build_stepper(project, held)
    _button(stepper, _name(stepper, "Criteria")).clicks += 1
    _button(stepper, "Save Criteria").clicks += 1

    text = _text(stepper)
    assert "Another window" not in text and "saved to criteria.yaml" in text
    assert _lock(root).exists()  # the screen did not release the window's lock


def _name(view: pn.viewable.Viewable, prefix: str) -> str:
    return next(b.name for b in view.select(pn.widgets.Button) if b.name.startswith(prefix))


def test_back_to_projects__task_running__lock_stays_until_the_task_ends(root: Path) -> None:
    page = _open(build_app(root))
    project = layer.open_project("demo", root=root)
    release = threading.Event()

    def work(cancel: threading.Event, report: Any) -> None:
        assert release.wait(10)

    tasks.runner_for(project).start("A test task", work)
    _button(page, "Back to Projects").clicks += 1

    assert _lock(root).exists()
    assert "still running" in _text(page)
    release.set()
    tasks.runner_for(project).join(10)
    assert not _lock(root).exists()


def test_stale_lock__window_offers_a_confirmed_take_over(root: Path) -> None:
    _lock(root).write_text(json.dumps({"pid": 2**22 + 12345}), encoding="utf-8")
    page = _open(build_app(root))

    assert "closed unexpectedly" in _text(page)
    ask = _button(page, "Take over this Project")
    ask.clicks += 1
    assert "removes the lock" in _text(page)  # names what it does before it does it
    assert layer.open_session("demo", root=root).read_only  # nothing changed yet
    _button(page, "Yes, take over this Project").clicks += 1

    assert "closed unexpectedly" not in _text(page)
    assert any(not b.disabled for b in _body_controls(page))
    assert json.loads(_lock(root).read_text(encoding="utf-8"))["pid"] != 2**22 + 12345


def test_stale_lock__declined__stays_read_only(root: Path) -> None:
    _lock(root).write_text(json.dumps({"pid": 2**22 + 12345}), encoding="utf-8")
    page = _open(build_app(root))

    _button(page, "Take over this Project").clicks += 1
    _button(page, "Keep it read-only").clicks += 1

    assert json.loads(_lock(root).read_text(encoding="utf-8"))["pid"] == 2**22 + 12345
    assert all(b.disabled or b.name.startswith("Refresh") for b in _body_controls(page))
