"""Every screen action is a native, labelled Panel control, and the list is complete (ticket #338).

What this proves: each action a screen builds is a Panel widget that renders a native element
(button, text box, select, checkbox), each carries a name, and the controls a screen builds are
exactly the ones listed here. An action added to a screen, or a screen or builder added to the
GUI, fails this test until it is listed. The screening keys are checked against their mouse
buttons, because the keys themselves run through a browser script bridge.

What this does not prove: tab order, that a browser actually focuses each control, or that a
focus indicator shows. Those need a browser walkthrough by the project owner (see the audit note).
Controls that appear only after an action (a confirm box) are checked in each screen own test.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.gui import layer, palette
from prismabib.gui import new_project_model as npm
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
from prismabib.gui.settings import build_settings
from prismabib.gui.size_check import build_size_check
from prismabib.screening import ui
from prismabib.screening.ui import KEY_MAP
from prismabib.stage import PrismaStage
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.integration.gui.test_home import PASSING, unlabelled_controls
from tests.integration.gui.test_screening import make_project

pytestmark = pytest.mark.integration

GUI_DIR = Path(layer.__file__).parent
NATIVE = (
    pn.widgets.Button,
    pn.widgets.Checkbox,
    pn.widgets.IntInput,
    pn.widgets.MultiChoice,
    pn.widgets.PasswordInput,
    pn.widgets.RadioButtonGroup,
    pn.widgets.Select,
    pn.widgets.TextAreaInput,
    pn.widgets.TextInput,
)
CRITERIA_FIELDS = (
    "Criteria version (MAJOR.MINOR.PATCH)",
    "Include from year",
    "Include up to year",
    "Include subject areas (four-letter codes, one per line; empty = any)",
    "Include document types (codes such as ar, re, cp; empty = any)",
    "Conference proceedings to keep (name fragments)",
    "Include languages (empty = any)",
    "Reason codes for excluding at abstract screening",
    "Reason codes for excluding at full-text screening",
)
SCREENING_BUTTONS = ("include (i)", "unsure (u)", "undo (z)", "prev (p)", "next (n)", "help (?)")


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "settings.env"))


def _button(view: pn.viewable.Viewable, name: str) -> pn.widgets.Button:
    return next(b for b in view.select(pn.widgets.Button) if b.name == name)


# The view holds exactly the listed controls; each is native and labelled.
def check_screen(view: pn.viewable.Viewable, expected: set[str]) -> None:
    interactive = [
        w for w in view.select(pn.widgets.Widget) if not isinstance(w, pn.indicators.Progress)
    ]
    stray = [type(w).__name__ for w in interactive if not isinstance(w, NATIVE)]
    assert stray == [], "a non-native control is not reachable by keyboard"
    names = {w.name for w in interactive}
    assert names - expected == set(), "an action is not listed in this test"
    assert expected - names == set(), "a listed action is not on the screen"
    assert unlabelled_controls(view) == []


def test_home__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    make_project(tmp_path)
    view = build_home(tmp_path, checks=lambda: PASSING, on_open=lambda *_: None)
    check_screen(
        view,
        {
            "Projects folder",
            "Use this folder",
            "Create this folder",
            "New Project",
            "Stop the app",
            "Yes, stop the app",
            "Keep the app running",
            "Open demo",
        },
    )


def test_settings__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    check_screen(build_settings(tmp_path), {"Reviewer name", "Save reviewer name"})


def test_new_project__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    check_screen(
        build_new_project(tmp_path),
        {
            "Project name",
            "Reviewer name",
            "Do you have Scopus access?",
            "Also fill missing fields of Scopus records from OpenAlex",
            "Create Project",
        },
    )


def test_stepper__every_step_button__is_native_and_labelled(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    rows = npm.stepper_rows(project)
    view = build_stepper(project)
    steps = [
        b
        for b in view.select(pn.widgets.Button)
        if any(b.name.startswith(r.label + " (") for r in rows)
    ]
    assert len(steps) == len(rows)
    assert unlabelled_controls(view) == []


def test_keys__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    view = build_keys(project, user_file=tmp_path / "settings.env")
    check_screen(
        view,
        {
            "OpenAlex API key",
            "Save OpenAlex key",
            "Test OpenAlex key",
            "Test Scopus key",
            "Scopus API key",
            "Save key",
        },
    )


def test_criteria__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    check_screen(build_criteria(project), {*CRITERIA_FIELDS, "Save Criteria"})


def test_query__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    expected = {"Subject areas", "First year", "Last year", "Save query"}
    for chip in ("Search terms", "Compound terms"):
        expected |= {chip, f"Add to: {chip}", f"Add to: {chip} (button)"}
    check_screen(build_query_form(project), expected)


def test_size_check__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    check_screen(
        build_size_check(project),
        {
            "Check the size of the Scopus search",
            "I understand this is a large set and want to continue",
            "Continue to capture",
            "Go to the keys step",
        },
    )


def test_capture__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    size_check = {
        "Check the size of the Scopus search",
        "I understand this is a large set and want to continue",
        "Continue to capture",
        "Go to the keys step",
    }
    capture = {
        "Page budget (pages of 100 works)",
        "Capture again (a new run)",
        "I confirm: capture this search now",
        "Start the capture",
        "Cancel the capture",
        "Refresh the status",
        "Discard the unsealed run",
        "Yes, discard it",
        "Keep it",
    }
    check_screen(build_capture_step(project), size_check | capture)


def test_build__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    delete = {"Delete the built store", "Yes, delete the store", "Keep the store"}
    check_screen(
        build_build(make_project(tmp_path)), {"Rebuild the store", "Cancel the build"} | delete
    )  # the label depends on the store


def test_delete_store__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    view, _refresh = build_delete_store(make_project(tmp_path))
    check_screen(view, {"Delete the built store", "Yes, delete the store", "Keep the store"})


def test_screening__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    view = build_screening(make_project(tmp_path))
    blind = "Blind mode: hide author names, citation counts and other reviewers' Decisions"
    check_screen(view, {sl.REVIEWER_LABEL, "Stage", blind, "Start screening"})


def test_screener__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    screener = sl.open_screening(make_project(tmp_path), PrismaStage.TITLE_ABSTRACT, "Ada")
    view = screener.view()
    buttons = {f"{d}: {code}" for d, code in screener.palette.items()} | set(SCREENING_BUTTONS)
    check_screen(view, buttons)


def test_screener__every_keyboard_key__has_a_mouse_button(tmp_path: Path) -> None:
    screener = sl.open_screening(make_project(tmp_path), PrismaStage.TITLE_ABSTRACT, "Ada")
    labels = [b.name for b in screener.view().select(pn.widgets.Button)]
    actions = {b.action for b in KEY_MAP}
    assert actions == {
        "include",
        "begin_exclude",
        "unsure",
        "next_record",
        "previous_record",
        "undo",
        "show_help",
    } | {f"reason_{d}" for d in ui.REASON_DIGITS}, "a new key is not listed here"
    for binding in KEY_MAP:
        if binding.action.startswith("reason_"):
            if binding.key in screener.palette:
                assert any(label.startswith(f"{binding.key}:") for label in labels), binding
        elif binding.action == "begin_exclude":
            assert any(label.startswith("1:") for label in labels), (
                "reason buttons arm the exclusion"
            )
        else:
            assert any(label.endswith(f"({binding.key})") for label in labels), binding


def test_flow__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    check_screen(
        build_flow(project),
        {
            "Refresh the Flow counts",
            "Reviewer name",
            "Records the search missed: one line each, title then year then DOI (DOI optional)",
            "Add these as candidates",
            "Candidate",
            "Match rule",
            "Captured OpenAlex response",
            "I confirm this work is this candidate",
            "Register this Record",
        },
    )


def test_export__every_action__is_native_and_labelled(tmp_path: Path) -> None:
    check_screen(
        build_export(make_project(tmp_path)), {"Export the citable bundle", "Cancel the export"}
    )


def test_app__back_to_projects__is_native_and_labelled(tmp_path: Path) -> None:
    make_project(tmp_path)
    tabs = build_app(tmp_path)
    page = tabs[0]
    _button(page, "New Project").clicks += 1
    check_screen(
        page,
        {
            "Back to Projects",
            "Project name",
            "Reviewer name",
            "Do you have Scopus access?",
            "Also fill missing fields of Scopus records from OpenAlex",
            "Create Project",
        },
    )


# Every build_ function in the GUI is a screen this file enumerates, or a named helper.
SCREEN_BUILDERS = {
    "build_app",
    "build_home",
    "build_settings",
    "build_new_project",
    "build_stepper",
    "build_keys",
    "build_criteria",
    "build_query_form",
    "build_size_check",
    "build_capture",
    "build_capture_step",
    "build_build",
    "build_delete_store",
    "build_screening",
    "build_flow",
    "build_export",
}  # build_capture is the capture panel that build_capture_step wraps
HELPER_BUILDERS = {"build_status"}  # the background-build status, not a screen
SCREEN_MODULES = {
    "app",
    "build",
    "capture",
    "criteria",
    "delete_store",
    "export",
    "flow",
    "home",
    "keys",
    "new_project",
    "query_form",
    "screening",
    "settings",
    "size_check",
}
NON_SCREEN_MODULES = {
    "__init__",
    "build_layer",
    "capture_layer",
    "criteria_model",
    "delete_store_layer",
    "export_layer",
    "flow_layer",
    "home_model",
    "keys_model",
    "launch",
    "layer",
    "new_project_model",
    "openalex_capture_layer",
    "query_form_model",
    "screening_layer",
    "server",
    "settings_model",
    "size_check_model",
    "stop_model",
    "tasks",
    "palette",
}


def test_gui_builders__each_is_enumerated_or_a_named_helper() -> None:
    found = set()
    for path in GUI_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found |= {
            n.name
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name.startswith("build_")
        }
    assert found == SCREEN_BUILDERS | HELPER_BUILDERS, found ^ (SCREEN_BUILDERS | HELPER_BUILDERS)


def test_gui_modules__each_is_classified_as_screen_or_support() -> None:
    modules = {p.stem for p in GUI_DIR.glob("*.py")}
    assert not (SCREEN_MODULES & NON_SCREEN_MODULES)
    assert modules == SCREEN_MODULES | NON_SCREEN_MODULES, modules ^ (
        SCREEN_MODULES | NON_SCREEN_MODULES
    )


def test_palette__overrides_are_handed_to_the_widgets_that_need_them(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    home = build_home(tmp_path, checks=lambda: PASSING)
    assert palette.BUTTON_CSS in _button(home, "New Project").stylesheets
    assert palette.BUTTON_CSS in _button(home, "Yes, stop the app").stylesheets
    delete, _refresh = build_delete_store(project)
    assert palette.BUTTON_CSS in _button(delete, "Yes, delete the store").stylesheets
    assert palette.BUTTON_CSS in _button(build_capture_step(project), "Yes, discard it").stylesheets
    query = build_query_form(project)
    chips = list(query.select(pn.widgets.MultiChoice))
    assert chips and all(palette.CHIP_CSS in chip.stylesheets for chip in chips)
    entries = [w for w in query.select(pn.widgets.TextInput) if w.placeholder]
    assert entries and all(palette.PLACEHOLDER_CSS in w.stylesheets for w in entries)
