"""The export screen and its layer functions, over the real library export (ticket #335)."""

from __future__ import annotations

import hashlib
import subprocess
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.errors import ConfigError, ProjectLockedError, ValidationError
from prismabib.gui import export_layer as xl
from prismabib.gui import layer, tasks
from prismabib.gui.export import build_export
from prismabib.gui.new_project import build_stepper
from prismabib.project import Project
from prismabib.report import export as export_module
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.integration.gui.test_home import unlabelled_controls
from tests.integration.gui.test_screening import make_project

pytestmark = pytest.mark.integration

REAL_PROVENANCE = export_module._git_provenance
CLEAN = {"commit": "a" * 40, "dirty": False, "commit_is_pushed": True}


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "settings.env"))
    monkeypatch.setattr(export_module, "_git_provenance", lambda cwd: dict(CLEAN))
    tasks.reset_runners()
    yield
    tasks.reset_runners()


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return make_project(tmp_path)


def _tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _start(project: Project) -> None:
    with layer.Session(project) as session:
        xl.start_export(session)


def _text(view: pn.viewable.Viewable) -> str:
    return "\n".join(str(p.object) for p in view.select(pn.pane.Markdown))


def _button(view: pn.viewable.Viewable, name: str) -> pn.widgets.Button:
    return next(b for b in view.select(pn.widgets.Button) if b.name == name)


def _hold_at(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> tuple[threading.Event, threading.Event]:
    arrived, release = threading.Event(), threading.Event()
    real = export_module.export_project

    def held(project: Project, **kwargs: Any) -> Any:
        report: Callable[[Any], None] = kwargs["on_progress"]

        def on_progress(event: Any) -> None:
            report(event)
            if event.phase == phase:
                arrived.set()
                assert release.wait(10)

        return real(project, **{**kwargs, "on_progress": on_progress})

    monkeypatch.setattr(export_module, "export_project", held)
    return arrived, release


def test_export__button__writes_the_bundle_and_the_summary_names_the_folder(
    project: Project,
) -> None:
    view = build_export(project)

    _button(view, "Export the citable bundle").clicks += 1
    tasks.runner_for(project).join(30)

    status = xl.export_status(project)
    assert status.outcome == "done"
    assert (project.root / "exports" / "numbers.json").is_file()
    assert str(project.root / "exports") in _text(build_export(project))
    assert status.result.warnings == ()


def test_export__no_store__is_refused_with_the_next_step(tmp_path: Path) -> None:
    empty = make_project(tmp_path, built=False)
    with layer.Session(empty) as session, pytest.raises(ValidationError, match="Build"):
        xl.start_export(session)


def test_status__while_exporting__shows_progress_in_words(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    arrived, release = _hold_at(monkeypatch, "render")
    _start(project)
    assert arrived.wait(10)

    text = _text(build_export(project))
    release.set()
    tasks.runner_for(project).join(30)

    assert "Preparing the figures and tables." in text


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ({"commit": None, "dirty": True, "commit_is_pushed": False}, "not inside a git repository"),
        ({**CLEAN, "dirty": True}, "uncommitted changes"),
        ({**CLEAN, "commit_is_pushed": False}, "on no remote branch"),
    ],
    ids=["no-git", "uncommitted", "unpushed"],
)
def test_git_state__each_kind__shows_its_warning_before_and_after_the_export(
    project: Project, monkeypatch: pytest.MonkeyPatch, state: dict[str, Any], expected: str
) -> None:
    monkeypatch.setattr(export_module, "_git_provenance", lambda cwd: dict(state))
    assert expected in _text(build_export(project))

    _start(project)
    tasks.runner_for(project).join(30)

    assert expected in _text(build_export(project))
    assert expected in " ".join(xl.export_status(project).result.warnings)


def test_git_state__clean__shows_no_warning(project: Project) -> None:
    assert "Warning" not in _text(build_export(project))


def test_export__gui__performs_no_commit_or_push(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = subprocess.run

    def guarded(cmd: Any, *a: Any, **k: Any) -> Any:
        if cmd[:1] == ["git"] and cmd[1:2] and cmd[1] in {"commit", "push", "add", "tag"}:
            raise AssertionError(f"the GUI ran git {cmd[1]}")
        return real(cmd, *a, **k)

    monkeypatch.setattr(export_module, "_git_provenance", REAL_PROVENANCE)  # real git calls
    monkeypatch.setattr(subprocess, "run", guarded)

    _start(project)
    tasks.runner_for(project).join(30)

    assert xl.export_status(project).outcome == "done"


def test_cancel__during_the_export__leaves_no_partial_bundle(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    arrived, release = _hold_at(monkeypatch, "render")
    _start(project)
    assert arrived.wait(10)
    xl.cancel_export(project)
    release.set()
    tasks.runner_for(project).join(30)

    assert xl.export_status(project).outcome == "cancelled"
    assert _tree(project.root / "exports") == {}  # the folder may exist empty; no file in it
    assert "Nothing was changed" in _text(build_export(project))


def test_cancel__second_export__keeps_the_previous_bundle(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _start(project)
    tasks.runner_for(project).join(30)
    before = _tree(project.root / "exports")
    arrived, release = _hold_at(monkeypatch, "render")
    _start(project)
    assert arrived.wait(10)
    xl.cancel_export(project)
    release.set()
    tasks.runner_for(project).join(30)

    assert _tree(project.root / "exports") == before


def test_export__failure__says_what_happened_and_what_to_do(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_: object, **__: object) -> None:
        raise ConfigError("A setting is missing.")

    monkeypatch.setattr(export_module, "export_project", boom)
    _start(project)
    tasks.runner_for(project).join(30)

    assert xl.export_status(project).outcome == "failed"
    text = _text(build_export(project))
    err = ConfigError("A setting is missing.")
    assert "The export failed." in text and layer.translate(err).next_step in text


def test_export_screen__every_control_is_labelled_and_the_stepper_offers_it(
    project: Project,
) -> None:
    for view in (build_export(project), build_stepper(project)):
        assert unlabelled_controls(view) == []
    assert _button(build_export(project), "Cancel the export").disabled


def test_export_screen__read_only_session__shows_the_sentence(project: Project) -> None:
    holder = layer.open_session(project.slug, root=project.root.parent)
    try:
        view = build_export(project)
        _button(view, "Export the citable bundle").clicks += 1
        assert "Another window" in _text(view)
    finally:
        holder.close()
    with pytest.raises(ProjectLockedError):
        xl.start_export(holder)
