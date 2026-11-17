"""The export screen and its layer functions, over the real library export (ticket #335)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.errors import ConfigError, ProjectLockedError, ValidationError
from prismabib.gui import export_layer as xl
from prismabib.gui import service, tasks
from prismabib.gui.export import build_export
from prismabib.gui.new_project import build_stepper
from prismabib.project import Project
from prismabib.report import export as export_module
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.integration.gui.helpers import hold_at
from tests.integration.gui.helpers import text as _text
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
    with service.Session(project) as session:
        xl.start_export(session)


def _button(view: pn.viewable.Viewable, name: str) -> pn.widgets.Button:
    return next(b for b in view.select(pn.widgets.Button) if b.name == name)


def _hold_at(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> tuple[threading.Event, threading.Event]:
    return hold_at(monkeypatch, export_module, "export_project", phase)


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


def test_export__no_store__is_refused_with_the_next_step(tmp_path: Path) -> None:
    empty = make_project(tmp_path, built=False)
    with service.Session(empty) as session, pytest.raises(ValidationError, match="Build"):
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


def _git(folder: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.org", *args],
        cwd=folder,
        check=True,
        capture_output=True,
    )


def _own_repo(folder: Path, *, pushed: bool, dirty: bool) -> None:
    """Make ``folder`` its own repository; everything in it is ignored but the .gitignore."""
    _git(folder, "init")
    (folder / ".gitignore").write_text("*\n", encoding="utf-8")
    _git(folder, "add", "-f", ".gitignore")
    _git(folder, "commit", "-m", "x")
    if pushed:
        _git(folder, "update-ref", "refs/remotes/origin/main", "HEAD")
    if dirty:
        (folder / ".gitignore").write_text("*\n# edit\n", encoding="utf-8")


def _real_for_the_project(monkeypatch: pytest.MonkeyPatch) -> None:
    """Real git for the Project folder; the installed code stays reported as CLEAN."""
    code = export_module._code_root()
    monkeypatch.setattr(
        export_module,
        "_git_provenance",
        lambda cwd: dict(CLEAN) if cwd == code else REAL_PROVENANCE(cwd),
    )


def _after_export(project: Project) -> tuple[str, ...]:
    _start(project)
    tasks.runner_for(project).join(300)  # generous: a loaded machine, and git runs per call
    return xl.export_status(project).result.warnings


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"pushed": True, "dirty": True}, "uncommitted changes"),
        ({"pushed": False, "dirty": False}, "on no remote branch"),
    ],
    ids=["uncommitted", "unpushed"],
)
def test_git_state__project_repository__shows_its_warning_before_and_after_the_export(
    project: Project, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, bool], expected: str
) -> None:
    _own_repo(project.root, **kwargs)
    _real_for_the_project(monkeypatch)
    assert "your project folder" in _text(build_export(project)).lower()
    assert expected in _text(build_export(project))

    warnings = _after_export(project)

    assert expected in " ".join(warnings)
    assert expected in _text(build_export(project))


def test_git_state__unpushed__says_it_reads_only_the_last_fetch(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _own_repo(project.root, pushed=False, dirty=False)
    _real_for_the_project(monkeypatch)

    text = _text(build_export(project))

    assert "As far as the last fetch shows" in text and "cannot fetch that state" in text


@pytest.mark.parametrize("nested", [False, True], ids=["no-git", "nested-in-other-repo"])
def test_git_state__project_not_its_own_repository__cannot_name_its_state(
    project: Project, monkeypatch: pytest.MonkeyPatch, nested: bool
) -> None:
    if nested:  # a clean, pushed repository around the Project folder must not be adopted
        _own_repo(project.root.parent, pushed=True, dirty=False)
    _real_for_the_project(monkeypatch)
    expected = "cannot name the state of your Project folder"
    assert expected in _text(build_export(project))

    assert expected in " ".join(_after_export(project))


@pytest.mark.parametrize(
    "kwargs",
    [{"pushed": True, "dirty": True}, {"pushed": False, "dirty": False}, None],
    ids=["uncommitted", "unpushed", "no-git"],
)
def test_git_state__each_warning__says_what_the_manifest_records(
    project: Project, monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, bool] | None
) -> None:
    if kwargs is not None:
        _own_repo(project.root, **kwargs)
    code = export_module._code_root()  # the installed code: dirty and unpushed
    monkeypatch.setattr(
        export_module,
        "_git_provenance",
        lambda cwd: (
            {**CLEAN, "dirty": True, "commit_is_pushed": False}
            if cwd == code
            else REAL_PROVENANCE(cwd)
        ),
    )
    warnings = _after_export(project)
    manifest = json.loads((project.root / "exports" / "manifest.json").read_text("utf-8"))

    assert warnings
    for w in warnings:
        assert f"commit {manifest['git_commit'][:12]}" in w
        assert ("with" if manifest["dirty"] else "without") + " uncommitted changes" in w
        assert "on no remote branch" in w and not manifest["commit_is_pushed"]


def test_git_state__clean__shows_no_warning(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    _own_repo(project.root, pushed=True, dirty=False)
    _real_for_the_project(monkeypatch)

    assert "Warning" not in _text(build_export(project))
    assert _after_export(project) == ()


def test_export__gui__performs_no_commit_or_push(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guard (passes on main too): no git command that changes a repository is run."""
    _own_repo(project.root, pushed=False, dirty=True)  # real git, run before the guard is set
    real = subprocess.run
    forbidden = {"commit", "push", "add", "tag", "reset", "checkout"}

    def guarded(cmd: Any, *a: Any, **k: Any) -> Any:
        if cmd[:1] == ["git"] and cmd[1:2] and cmd[1] in forbidden:
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
    assert "The export failed." in text and service.translate(err).next_step in text


def test_export_screen__every_control_is_labelled_and_the_stepper_offers_it(
    project: Project,
) -> None:
    for view in (build_export(project), build_stepper(project)):
        assert unlabelled_controls(view) == []
    assert _button(build_export(project), "Cancel the export").disabled


def test_export_screen__read_only_session__shows_the_sentence(project: Project) -> None:
    holder = service.open_session(project.slug, root=project.root.parent)
    try:
        view = build_export(project)
        _button(view, "Export the citable bundle").clicks += 1
        assert "Another window" in _text(view)
    finally:
        holder.close()
    with pytest.raises(ProjectLockedError):
        xl.start_export(holder)
