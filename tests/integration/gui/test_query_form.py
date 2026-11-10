"""The Query step, built headless (ticket #327)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.gui import layer
from prismabib.gui.new_project import build_stepper
from prismabib.gui.query_form import build_query_form
from prismabib.gui.query_form_model import ENRICHMENT_REASON
from prismabib.project import Project
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.integration.gui.test_home import unlabelled_controls

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _user_file(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path_factory.mktemp("u") / "settings.env"))


def _make(tmp_path: Path, mode: str = "openalex") -> Project:
    return layer.create_project(
        "demo",
        title="Demo",
        root=tmp_path,
        source_mode=mode,  # type: ignore[arg-type]
        reviewer="A",
    )


def _w(view: pn.viewable.Viewable, kind: type[pn.widgets.Widget], name: str) -> pn.widgets.Widget:
    return next(w for w in view.select(kind) if w.name == name)


def _text(view: pn.viewable.Viewable) -> str:
    return "\n".join(str(p.object) for p in view.select(pn.pane.Markdown))


def _add(view: pn.viewable.Viewable, label: str, text: str) -> None:
    _w(view, pn.widgets.TextInput, f"Add to: {label}").value = text


def _save(view: pn.viewable.Viewable) -> None:
    _w(view, pn.widgets.Button, "Save query").clicks += 1


def test_form__terms_and_years_entered__saved_without_seeing_the_file(tmp_path: Path) -> None:
    project = _make(tmp_path)
    view = build_query_form(project)
    _add(view, "Search terms", "carbon capture")
    _add(view, "Search terms", "policy")
    _w(view, pn.widgets.IntInput, "First year").value = 2015
    _w(view, pn.widgets.IntInput, "Last year").value = 2024
    _w(view, pn.widgets.MultiChoice, "Subject areas").value = ["COMP"]
    _save(view)
    assert "Query saved" in _text(view)
    reopened = build_query_form(project)
    assert _w(reopened, pn.widgets.MultiChoice, "Search terms").value == ["carbon capture", "policy"]
    assert _w(reopened, pn.widgets.IntInput, "First year").value == 2015
    assert _w(reopened, pn.widgets.MultiChoice, "Subject areas").value == ["COMP"]
    assert not (project.root / "criteria.yaml.tmp").exists()


def test_form__invalid_term__message_sits_next_to_its_field_and_nothing_is_written(
    tmp_path: Path,
) -> None:
    project = _make(tmp_path)
    before = (project.root / "project.toml").read_bytes()
    view = build_query_form(project)
    _add(view, "Search terms", "a (b)")
    _save(view)
    column = view[2]  # the terms chips, then the pane that holds their message
    assert "parentheses" in str(view[3].object) and column is view[2]
    assert "Not saved" in _text(view)
    assert (project.root / "project.toml").read_bytes() == before


@pytest.mark.parametrize(
    ("mode", "enabled"), [("openalex", True), ("scopus", False), ("scopus+openalex", False)]
)
def test_form__subject_areas__enabled_only_in_openalex_mode(
    tmp_path: Path, mode: str, enabled: bool
) -> None:
    view = build_query_form(_make(tmp_path, mode))
    assert (not _w(view, pn.widgets.MultiChoice, "Subject areas").disabled) is enabled
    assert (ENRICHMENT_REASON in _text(view)) is (not enabled)


def test_form__after_the_first_run__is_read_only_with_a_reason_and_save_does_nothing(
    tmp_path: Path,
) -> None:
    project = _make(tmp_path)
    (project.raw_dir / "openalex_search" / "run1").mkdir(parents=True)
    before = (project.root / "project.toml").read_bytes()
    view = build_query_form(project)
    assert "read-only because a Run has started" in _text(view)
    assert all(w.disabled for w in view.select(pn.widgets.Widget))
    _save(view)
    assert (project.root / "project.toml").read_bytes() == before


def test_form__held_by_another_window__shows_the_lock_sentence(tmp_path: Path) -> None:
    project = _make(tmp_path)
    layer.Session(project)  # this window holds the lock
    view = build_query_form(project)
    _add(view, "Search terms", "carbon")
    _save(view)
    assert "Another window" in _text(view)


def test_form__every_control__has_a_label(tmp_path: Path) -> None:
    for mode in ("openalex", "scopus"):
        assert unlabelled_controls(build_query_form(_make(tmp_path / mode, mode))) == []


def test_stepper__query_step__builds_the_form(tmp_path: Path) -> None:
    project = _make(tmp_path)
    stepper = build_stepper(project)
    next(
        b for b in stepper.select(pn.widgets.Button) if b.name.startswith("Query")
    ).clicks += 1
    assert any(w.name == "Save query" for w in stepper.select(pn.widgets.Button))
