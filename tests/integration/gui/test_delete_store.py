"""Delete the built store, store only (ticket #337)."""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.errors import ProjectLockedError, ValidationError
from prismabib.gui import build_layer as bl
from prismabib.gui import delete_store_layer as dl
from prismabib.gui import layer, tasks
from prismabib.gui.build import build_build
from prismabib.gui.delete_store import build_delete_store, confirmation_text
from prismabib.project import Project
from prismabib.store.stamp import stamp_path
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.integration.gui.helpers import text as _text
from tests.integration.gui.test_home import unlabelled_controls
from tests.prisma_helpers import CorpusSpec, RecordSpec, build_project

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "settings.env"))
    tasks.reset_runners()
    yield
    tasks.reset_runners()


@pytest.fixture
def project(tmp_path: Path) -> Project:
    built = build_project(tmp_path, CorpusSpec(records=[RecordSpec(number=n) for n in range(1, 4)]))
    built.decisions_path.write_bytes(
        f'{{"schema_version": {layer.CURRENT_PROJECT_VERSION}, "decision": "kept"}}\n'.encode()
    )
    (built.root / "criteria.yaml").write_text("version: '0.2.0'\n", encoding="utf-8")
    return built


def _kept(project: Project) -> dict[str, str]:
    """Hash of everything the app must never delete: all files outside the store folder."""
    store = project.db_path.parent
    return {
        p.relative_to(project.root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(project.root.rglob("*"))
        if p.is_file() and store not in p.parents and p.name != layer.LOCK_NAME
    }


def _button(view: pn.viewable.Viewable, name: str) -> pn.widgets.Button:
    return next(b for b in view.select(pn.widgets.Button) if b.name == name)


def _delete(project: Project) -> None:
    with layer.Session(project) as session:
        dl.delete_store(session, confirmed=True)


def test_confirmation__text__names_what_goes_what_stays_and_that_the_app_never_deletes_it() -> None:
    text = confirmation_text()

    assert "built store" in text.split("This stays")[0]
    for kept in (
        "captured searches",
        "Sealed run",
        "Decision log",
        "settings file",
        "Criteria file",
    ):
        assert kept in text.split("This stays")[1]
    assert "never deletes" in text


def test_delete__confirmed__store_gone_everything_else_byte_identical_and_rebuild_works(
    project: Project,
) -> None:
    kept = _kept(project)
    assert project.db_path.is_file()

    _delete(project)

    assert not project.db_path.exists() and not stamp_path(project.db_path).exists()
    assert _kept(project) == kept
    assert project.slug in [p.name for p in project.root.parent.iterdir()]
    reopened = layer.open_project(project.slug, root=project.root.parent)
    assert "Build the store" in [b.name for b in build_build(reopened).select(pn.widgets.Button)]
    with layer.Session(reopened) as session:
        bl.start_build(session)
    tasks.runner_for(reopened).join(30)
    assert bl.build_status(reopened).outcome == "done"
    assert reopened.db_path.is_file()
    assert _kept(reopened) == kept


def test_delete__not_confirmed__changes_nothing(project: Project) -> None:
    before = (_kept(project), project.db_path.read_bytes())

    with layer.Session(project) as session, pytest.raises(ValidationError):
        dl.delete_store(session, confirmed=False)

    assert (_kept(project), project.db_path.read_bytes()) == before


def test_delete__no_store__is_refused(project: Project) -> None:
    _delete(project)

    with layer.Session(project) as session, pytest.raises(ValidationError, match="no built store"):
        dl.delete_store(session, confirmed=True)


def test_delete__project_open_in_another_session__is_refused_and_the_store_stays(
    project: Project,
) -> None:
    holder = layer.open_session(project.slug, root=project.root.parent)
    second = layer.open_session(project.slug, root=project.root.parent)
    try:
        with pytest.raises(ProjectLockedError):
            dl.delete_store(second, confirmed=True)
        view, _ = build_delete_store(project)
        _button(view, "Delete the built store").clicks += 1
        _button(view, "Yes, delete the store").clicks += 1
        assert "Another window" in _text(view)
    finally:
        holder.close()
    assert project.db_path.is_file()


def test_delete__store_in_use__says_what_to_do_and_keeps_it(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = Path.unlink

    def busy(self: Path, missing_ok: bool = False) -> None:
        if self == project.db_path:
            raise PermissionError("in use")
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", busy)
    with layer.Session(project) as session, pytest.raises(ValidationError, match="open"):
        dl.delete_store(session, confirmed=True)
    monkeypatch.undo()
    assert project.db_path.is_file()


def test_delete__only_store_files_are_unlinked(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []
    real = Path.unlink

    def spy(self: Path, missing_ok: bool = False) -> None:
        seen.append(self)
        real(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", spy)
    _delete(project)

    store = project.db_path.parent
    assert seen and all(p.parent == store for p in seen if p.name != layer.LOCK_NAME)


def test_enumeration__no_operation_or_control_deletes_layer0_sealed_runs_log_or_settings(
    project: Project,
) -> None:
    risky = ("delete", "remove", "discard", "erase", "clear", "wipe")
    names = [n for n in layer.WRITE_OPERATIONS if any(w in n for w in risky)]
    assert sorted(names) == [
        "delete_store",
        "discard_capture",
    ]  # discard only marks an unsealed run
    buttons = [b.name for b in build_build(project).select(pn.widgets.Button)]
    assert [n for n in buttons if any(w in n.lower() for w in risky)] == [
        "Delete the built store",
        "Yes, delete the store",
    ]


def test_dialog__controls__are_labelled_and_reachable_and_the_smoke_build_works(
    project: Project,
) -> None:
    view, _ = build_delete_store(project)
    full = build_build(project)

    assert unlabelled_controls(view) == [] and unlabelled_controls(full) == []
    assert [b.name for b in view.select(pn.widgets.Button)] == [
        "Delete the built store",
        "Yes, delete the store",
        "Keep the store",
    ]
    assert not any(isinstance(w, pn.widgets.TextInput) for w in view.select(pn.widgets.Widget))


def test_dialog__keep__changes_nothing_and_yes__deletes_and_offers_the_rebuild(
    project: Project,
) -> None:
    view = build_build(project)
    kept = _kept(project)

    _button(view, "Delete the built store").clicks += 1
    assert "This stays" in _text(view)
    _button(view, "Keep the store").clicks += 1
    assert project.db_path.is_file()

    _button(view, "Delete the built store").clicks += 1
    _button(view, "Yes, delete the store").clicks += 1

    assert not project.db_path.exists() and _kept(project) == kept
    assert "The store was deleted" in _text(view)
    assert _button(view, "Build the store").disabled is False
    assert _button(view, "Delete the built store").disabled
