"""The home screen, built headless (ticket #322)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.doctor_checks import Check
from prismabib.gui import layer
from prismabib.gui.home import build_home
from prismabib.gui.home_model import FIREWALL_NOTE
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.integration.gui.helpers import text as _text  # re-exported: other modules import it

pytestmark = pytest.mark.integration

PASSING = {"python": Check("ok", "p"), "git": Check("ok", "g"), "uv": Check("ok", "u")}


@pytest.fixture(autouse=True)
def _user_file(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path_factory.mktemp("user") / "settings.env"
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(path))
    return path


def _button(view: pn.viewable.Viewable, name: str) -> pn.widgets.Button:
    return next(b for b in view.select(pn.widgets.Button) if b.name == name)


def unlabelled_controls(view: pn.viewable.Viewable) -> list[pn.widgets.Widget]:
    """Every widget (each is a native, tabbable control) without a visible label."""
    return [w for w in view.select(pn.widgets.Widget) if not (w.name or "").strip()]


def test_build_home__passing_machine__lists_projects_and_offers_new_project(
    tmp_path: Path,
) -> None:
    layer.create_project("demo", title="Demo", root=tmp_path, source_mode="openalex", reviewer="A")
    view = build_home(tmp_path, checks=lambda: PASSING)
    text = _text(view)
    assert "Demo" in text
    assert "was not found" not in text
    assert _button(view, "New Project").button_type == "success"


def test_build_home__failed_check__shows_sentence_and_fix(tmp_path: Path) -> None:
    failing = {**PASSING, "uv": Check("FAIL", "uv: not found")}
    text = _text(build_home(tmp_path, checks=lambda: failing))
    assert "uv was not found." in text and "Install uv" in text


def test_build_home__firewall_note__first_run_only(tmp_path: Path) -> None:
    assert FIREWALL_NOTE in _text(build_home(tmp_path, checks=lambda: PASSING))
    assert FIREWALL_NOTE not in _text(build_home(tmp_path, checks=lambda: PASSING))


def test_build_home__choice_is_remembered_on_the_next_launch(tmp_path: Path) -> None:
    view = build_home(tmp_path / "x", checks=lambda: PASSING)
    target = tmp_path / "chosen"
    next(iter(view.select(pn.widgets.TextInput))).value = str(target)
    _button(view, "Create this folder").clicks += 1
    assert target.is_dir()
    again = build_home(checks=lambda: PASSING)
    assert next(iter(again.select(pn.widgets.TextInput))).value == str(target)


def test_build_home__long_path__is_refused_with_the_limit(tmp_path: Path) -> None:
    view = build_home(tmp_path, checks=lambda: PASSING)
    next(iter(view.select(pn.widgets.TextInput))).value = str(tmp_path / ("a" * 250))
    _button(view, "Create this folder").clicks += 1
    assert "240" in _text(view)


def test_build_home__remembered_folder_gone__says_so_and_the_field_can_change_it(
    tmp_path: Path,
) -> None:
    first = build_home(tmp_path / "x", checks=lambda: PASSING)
    next(iter(first.select(pn.widgets.TextInput))).value = str(tmp_path / "gone")
    _button(first, "Create this folder").clicks += 1
    (tmp_path / "gone").rmdir()
    view = build_home(checks=lambda: PASSING)
    assert "no longer exists" in _text(view)


def test_build_home__every_control__is_labelled(tmp_path: Path) -> None:
    view = build_home(tmp_path, checks=lambda: PASSING)
    names = {w.name for w in view.select(pn.widgets.Widget)}
    assert {"Projects folder", "Use this folder", "New Project", "Stop the app"} <= names
    assert unlabelled_controls(view) == []


def test_unlabelled_controls__a_control_without_a_name__is_found() -> None:
    assert len(unlabelled_controls(pn.Column(pn.widgets.Button()))) == 1


def test_build_app__headless__builds_the_home_screen() -> None:
    from prismabib.gui.app import build_app

    assert "prismabib" in _text(build_app())
