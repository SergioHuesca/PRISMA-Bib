"""Every button and tab in the built GUI carries the page-level focus setting (ticket #343).

What this proves: after the start-up step, each Button and the tab row of every built screen
has the page CSS (2 px solid ring, gap, default button edge) in the stylesheets of its Bokeh
model, the step is idempotent, and the kinds of control the screens build are exactly the listed
ones. A new kind of control, or a button built without the setting, fails here, naming the
screen and the control. Every field control and the radio group carries the palette field
stylesheet (#344).

What this does not prove: that a browser applies the setting over each component's own default,
that the ring is not clipped, or how anything looks. That is the owner's manual pass (#353).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.gui import palette
from prismabib.gui import screening_layer as sl
from prismabib.gui.app import build_app
from prismabib.gui.build import build_build
from prismabib.gui.capture import build_capture_step
from prismabib.gui.criteria import build_criteria
from prismabib.gui.delete_store import build_delete_store
from prismabib.gui.export import build_export
from prismabib.gui.flow import build_flow
from prismabib.gui.home import build_home
from prismabib.gui.keys import build_keys
from prismabib.gui.new_project import build_new_project, build_stepper
from prismabib.gui.query_form import build_query_form
from prismabib.gui.screening import build_screening
from prismabib.gui.server import install_page_css
from prismabib.gui.settings import build_settings
from prismabib.gui.size_check import build_size_check
from prismabib.stage import PrismaStage
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.integration.gui.test_home import PASSING
from tests.integration.gui.test_keyboard_actions import HELPER_BUILDERS, SCREEN_BUILDERS
from tests.integration.gui.test_screening import make_project

pytestmark = pytest.mark.integration

# Every kind of control the screens build: page CSS (Button, Tabs) or field CSS (the rest).
CONTROL_KINDS = {
    "Button",
    "Tabs",
    "TextInput",
    "PasswordInput",
    "TextAreaInput",
    "IntInput",
    "Select",
    "MultiChoice",
    "Checkbox",
    "RadioButtonGroup",
}
FIELD_KINDS = CONTROL_KINDS - {"Button", "Tabs"}
INDICATORS = {"Progress"}  # not controls: nothing to focus
# Builders that are not a screen of their own, or are built under another screen's name below.
BUILT_WITH_ANOTHER = {"build_capture"}  # wrapped by build_capture_step
NOT_BUILT_HERE = HELPER_BUILDERS


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "settings.env"))
    saved = list(pn.config.raw_css)
    pn.config.raw_css = []
    yield
    pn.config.raw_css = saved


def _screens(tmp_path: Path) -> dict[str, pn.viewable.Viewable]:
    project = make_project(tmp_path)
    delete, _refresh = build_delete_store(project)
    app = build_app(tmp_path)
    next(b for b in app[0].select(pn.widgets.Button) if b.name == "New Project").clicks += 1
    screener = sl.open_screening(project, PrismaStage.TITLE_ABSTRACT, "Ada")
    builders: dict[str, Callable[[], pn.viewable.Viewable]] = {
        "build_app": lambda: app,
        "build_home": lambda: build_home(tmp_path, checks=lambda: PASSING, on_open=lambda *_: None),
        "build_settings": lambda: build_settings(tmp_path),
        "build_new_project": lambda: build_new_project(tmp_path),
        "build_stepper": lambda: build_stepper(project),
        "build_keys": lambda: build_keys(project, user_file=tmp_path / "settings.env"),
        "build_criteria": lambda: build_criteria(project),
        "build_query_form": lambda: build_query_form(project),
        "build_size_check": lambda: build_size_check(project),
        "build_capture_step": lambda: build_capture_step(project),
        "build_build": lambda: build_build(project),
        "build_delete_store": lambda: delete,
        "build_screening": lambda: build_screening(project),
        "build_flow": lambda: build_flow(project),
        "build_export": lambda: build_export(project),
        "screener (the screening buttons)": screener.view,
    }
    covered = {n for n in builders if n.startswith("build_")} | BUILT_WITH_ANOTHER
    assert covered == SCREEN_BUILDERS, "a screen is not built here: " + str(
        covered ^ SCREEN_BUILDERS
    )
    return {name: make() for name, make in builders.items()}


def _controls(view: pn.viewable.Viewable) -> list[pn.viewable.Viewable]:
    found = [
        *view.select(pn.widgets.Widget),
        *view.select(pn.Tabs),
    ]
    return [c for c in found if type(c).__name__ not in INDICATORS]


def _looks(control: pn.viewable.Viewable) -> list[str]:
    return [s for s in control.get_root().stylesheets if isinstance(s, str)]


def test_install_page_css__called_twice__leaves_the_setting_once() -> None:
    install_page_css()
    install_page_css()
    assert pn.config.raw_css.count(palette.PAGE_CSS) == 1


def test_page_css__has_the_ring_width_style_and_default_edge() -> None:
    for token in ("--outline-width:2px", "--outline-style:solid", palette.DEFAULT_EDGE):
        assert token in palette.PAGE_CSS
    assert palette.FOCUS_OUTLINE in palette.PAGE_CSS


def test_screens__control_kinds_are_exactly_the_listed_ones(tmp_path: Path) -> None:
    kinds = {type(c).__name__ for view in _screens(tmp_path).values() for c in _controls(view)}
    assert kinds - CONTROL_KINDS == set(), "a new kind of control is not covered or listed"
    assert {"Button", "Tabs"} <= kinds


def test_screens__every_button_and_tab_row__carries_the_page_css(tmp_path: Path) -> None:
    install_page_css()
    screens = _screens(tmp_path)
    missing = [
        f"{screen}: {type(c).__name__} {getattr(c, 'name', '') or ''!r}"
        for screen, view in screens.items()
        for c in _controls(view)
        if isinstance(c, (pn.widgets.Button, pn.Tabs)) and palette.PAGE_CSS not in _looks(c)
    ]
    assert missing == [], "controls without the page-level focus setting"
    assert list(screens["build_app"].select(pn.Tabs)), "the app tab row was not found"


def test_screens__every_field_control_and_radio_group__carries_the_field_css(
    tmp_path: Path,
) -> None:
    fields = tuple(getattr(pn.widgets, kind) for kind in FIELD_KINDS)
    screens = _screens(tmp_path)
    missing = [
        f"{screen}: {type(c).__name__} {getattr(c, 'name', '') or ''!r}"
        for screen, view in screens.items()
        for c in _controls(view)
        if isinstance(c, fields) and palette.FIELD_CSS not in _looks(c)
    ]
    assert missing == [], "controls without the field stylesheet"
    built = {type(c).__name__ for v in screens.values() for c in _controls(v)}
    assert {"TextInput", "RadioButtonGroup"} <= built, "the fields were not found"
