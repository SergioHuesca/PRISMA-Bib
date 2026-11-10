"""New Project and the stepper, built headless (ticket #324)."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

pytest.importorskip("panel")

import panel as pn

from prismabib.cli import app as cli_app
from prismabib.gui import layer
from prismabib.gui.app import build_app
from prismabib.gui.new_project import build_new_project, build_stepper
from prismabib.project import Project
from prismabib.user_settings import (
    REVIEWER_NAME_KEY,
    USER_FILE_OVERRIDE_VAR,
    read_user_setting,
    write_user_settings,
)
from tests.integration.gui.helpers import text as _text
from tests.integration.gui.test_home import unlabelled_controls

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _user_file(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path_factory.mktemp("user") / "settings.env"
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(path))
    return path


def _w(view: pn.viewable.Viewable, kind: type[pn.widgets.Widget], name: str) -> pn.widgets.Widget:
    return next(w for w in view.select(kind) if w.name == name)


def _fill(view: pn.Column, name: str, reviewer: str = "Ada", access: str | None = "No") -> None:
    _w(view, pn.widgets.TextInput, "Project name").value = name
    _w(view, pn.widgets.TextInput, "Reviewer name").value = reviewer
    _w(view, pn.widgets.RadioButtonGroup, "Do you have Scopus access?").value = access


def _create(view: pn.Column) -> None:
    _w(view, pn.widgets.Button, "Create Project").clicks += 1


def _digest(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(root.rglob("*")):
        h.update(p.relative_to(root).as_posix().encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


def _stepper(project: Project) -> pn.Row:
    return build_stepper(project)


def _make(tmp_path: Path, mode: str = "openalex") -> Project:
    return layer.create_project("demo", title="Demo", root=tmp_path, source_mode=mode, reviewer="A")  # type: ignore[arg-type]


# -- New Project ---------------------------------------------------------------------------------


def test_new_project__name_typed__shows_the_derived_folder_name(tmp_path: Path) -> None:
    view = build_new_project(tmp_path)
    _w(view, pn.widgets.TextInput, "Project name").value = "Baseball CV 2026"
    assert "baseball-cv-2026" in _text(view)


def test_new_project__existing_folder__refused_and_disk_unchanged(tmp_path: Path) -> None:
    (tmp_path / "demo").mkdir()
    (tmp_path / "demo" / "keep.txt").write_text("mine", encoding="utf-8")
    before = _digest(tmp_path)
    view = build_new_project(tmp_path)
    _fill(view, "Demo")
    assert "another name" in _text(view)
    _create(view)
    assert "another name" in _text(view)
    assert _digest(tmp_path) == before


def test_new_project__reviewer__prefilled_saved_and_never_empty(tmp_path: Path) -> None:
    write_user_settings({REVIEWER_NAME_KEY: "Grace"})
    view = build_new_project(tmp_path)
    assert _w(view, pn.widgets.TextInput, "Reviewer name").value == "Grace"
    _fill(view, "One", reviewer="  ")
    _create(view)
    assert not (tmp_path / "one").exists() and "reviewer name" in _text(view)
    _fill(view, "One", reviewer="Ada")
    _create(view)
    assert (tmp_path / "one" / "project.toml").is_file()
    assert read_user_setting(REVIEWER_NAME_KEY) == "Ada"


def test_new_project__no_scopus_answer__refused_nothing_written(tmp_path: Path) -> None:
    view = build_new_project(tmp_path)
    _fill(view, "One", access=None)
    _create(view)
    assert not (tmp_path / "one").exists() and "Scopus access" in _text(view)


@pytest.mark.parametrize(
    ("answer", "fill", "mode"),
    [("Yes", True, "scopus+openalex"), ("Yes", False, "scopus"), ("Not sure", True, "openalex")],
)
def test_new_project__answer__creates_the_matching_mode(
    tmp_path: Path, answer: str, fill: bool, mode: str
) -> None:
    created: list[Project] = []
    view = build_new_project(tmp_path, on_created=created.append)
    _fill(view, "One", access=answer)
    _w(
        view, pn.widgets.Checkbox, "Also fill missing fields of Scopus records from OpenAlex"
    ).value = fill
    _create(view)
    assert created[0].source_mode == mode


# -- the stepper ---------------------------------------------------------------------------------


def _steps(view: pn.Row) -> dict[str, pn.widgets.Button]:
    return {b.name: b for b in view.select(pn.widgets.Button)}


def test_stepper__new_project__marks_source_mode_done_and_resumes_at_the_query(
    tmp_path: Path,
) -> None:
    view = _stepper(_make(tmp_path))
    names = _steps(view)
    assert "Source mode and keys (done)" in names
    assert "Query (next)" in names and "Export (not done)" in names
    assert "### Query" in _text(view)


def test_stepper__mid_capture__capture_is_not_done_and_query_is_read_only(tmp_path: Path) -> None:
    project = _make(tmp_path)
    (project.raw_dir / "openalex-search" / "r1").mkdir(parents=True)
    names = _steps(_stepper(project))
    assert "Query (done, read-only)" not in names  # no query written yet
    assert any(n.startswith("Query") and "read-only" in n for n in names)
    assert "Size check and capture (not done)" in names


def test_stepper__built_project__build_done_and_resumes_at_the_first_gap(tmp_path: Path) -> None:
    project = _make(tmp_path)
    run = project.raw_dir / "20260101T000000Z-aaaaaaaa"
    run.mkdir(parents=True)
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    project.db_path.write_bytes(b"duckdb")
    names = _steps(_stepper(project))
    assert "Build (done)" in names and "Size check and capture (done)" in names
    assert "### Query" in _text(_stepper(project))


def test_stepper__cli_made_project__is_listed_and_continues_from_the_stepper(
    tmp_path: Path,
) -> None:
    result = CliRunner().invoke(
        cli_app, ["init", "cli", "--actor", "Ada", "--sources", "scopus", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    page = build_app(tmp_path)
    next(b for b in page.select(pn.widgets.Button) if b.name == "Open cli").clicks += 1
    assert "Source mode and keys (done)" in _steps(page)
    assert "### Query" in _text(page)


def test_stepper__finished_step__can_be_revisited_and_shows_what_was_set(tmp_path: Path) -> None:
    view = _stepper(_make(tmp_path))
    _steps(view)["Source mode and keys (done)"].clicks += 1
    assert "Source mode: openalex." in _text(view)


def test_stepper__source_mode_lock__shows_read_only_with_the_reason(tmp_path: Path) -> None:
    project = _make(tmp_path)
    project.decisions_path.write_text('{"schema_version": 1}\n', encoding="utf-8")
    view = _stepper(project)
    button = next(b for n, b in _steps(view).items() if n.startswith("Source mode"))
    assert "read-only" in button.name
    button.clicks += 1
    assert "Read-only. " + str(layer.locks(project)["source_mode"]) in _text(view)


def test_stepper__query_lock__shows_read_only_with_the_reason(tmp_path: Path) -> None:
    project = _make(tmp_path)
    (project.raw_dir / "openalex-search" / "r1").mkdir(parents=True)
    view = _stepper(project)
    next(b for n, b in _steps(view).items() if n.startswith("Query")).clicks += 1
    assert "Read-only. The query is read-only because a Run has started." in _text(view)


def test_stepper__criteria_lock__shows_read_only_with_the_reason(tmp_path: Path) -> None:
    project = _make(tmp_path)
    project.decisions_path.write_text('{"schema_version": 1}\n', encoding="utf-8")
    view = _stepper(project)
    next(b for n, b in _steps(view).items() if n.startswith("Criteria")).clicks += 1
    assert "Read-only. The Criteria are read-only because a Decision" in _text(view)


def test_stepper__discarded_openalex_run__says_a_change_is_still_blocked(tmp_path: Path) -> None:
    project = _make(tmp_path)
    run = project.raw_dir / "openalex-search" / "r1"
    run.mkdir(parents=True)
    (run / "progress.json").write_text('{"discarded": true}', encoding="utf-8")
    view = _stepper(project)
    next(b for n, b in _steps(view).items() if n.startswith("Source mode")).clicks += 1
    text = _text(view)
    assert "discarded OpenAlex run" in text and "still blocked" in text
    assert "a discarded Run still counts" in text  # the reason


def test_stepper__a_run_that_is_not_discarded__does_not_claim_a_discard(tmp_path: Path) -> None:
    project = _make(tmp_path)
    (project.raw_dir / "openalex-search" / "r1").mkdir(parents=True)
    assert "discarded OpenAlex run" not in _text(_stepper(project))


# -- text and keyboard ---------------------------------------------------------------------------


def _screens(tmp_path: Path) -> list[pn.viewable.Viewable]:
    project = _make(tmp_path / "p")
    (project.raw_dir / "openalex-search" / "r1").mkdir(parents=True)
    return [
        build_new_project(tmp_path),
        _stepper(project),
        build_app(tmp_path),
    ]


def _strings(view: pn.viewable.Viewable) -> list[str]:
    return [str(p.object) for p in view.select(pn.pane.Markdown)] + [
        str(w.name) for w in view.select(pn.widgets.Widget)
    ]


def test_screens__text__says_project_never_review_for_the_created_thing(tmp_path: Path) -> None:
    strings = [s for view in _screens(tmp_path) for s in _strings(view)]
    assert any("Project" in s for s in strings)
    assert [s for s in strings if re.search(r"\breviews?\b", s, re.IGNORECASE)] == []


def test_screens__every_control__is_labelled(tmp_path: Path) -> None:
    for view in _screens(tmp_path):
        assert view.select(pn.widgets.Widget)
        assert unlabelled_controls(view) == []


def test_build_app__new_project_button__shows_the_form_and_creating_opens_the_stepper(
    tmp_path: Path,
) -> None:
    page = build_app(tmp_path)
    next(b for b in page.select(pn.widgets.Button) if b.name == "New Project").clicks += 1
    _fill(page, "Demo", access="No")
    _create(page)
    assert (tmp_path / "demo" / "project.toml").is_file()
    assert "### Query" in _text(page)
