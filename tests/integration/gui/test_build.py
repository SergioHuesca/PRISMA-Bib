"""The build screen and its layer functions, over stubbed Layer 0 (ticket #331)."""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.errors import ConfigError, ProjectLockedError, ValidationError
from prismabib.gui import build_layer as bl
from prismabib.gui import layer, tasks
from prismabib.gui.build import build_build
from prismabib.gui.new_project import build_stepper
from prismabib.project import Project
from prismabib.store.load import build_store
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.integration.gui.helpers import hold_at, returns_while_held
from tests.integration.gui.helpers import text as _text
from tests.integration.gui.test_home import unlabelled_controls
from tests.prisma_helpers import RUN_STARTED_AT, CorpusSpec, RecordSpec, build_project
from tests.store_helpers import write_sealed_run

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "settings.env"))
    tasks.reset_runners()
    yield
    tasks.reset_runners()


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """Five Records built once, a second sealed search holding the same five, a Decision log."""
    built = build_project(tmp_path, CorpusSpec(records=[RecordSpec(number=n) for n in range(1, 6)]))
    write_sealed_run(
        built.raw_dir,
        "20250102T000000Z-second0ab",
        [RecordSpec(number=n).to_entry() for n in range(1, 6)],
        started_at=RUN_STARTED_AT.replace(day=2),
        query='TITLE-ABS-KEY("another query")',  # a duplicate only counts across different queries
    )
    line = f'{{"schema_version": {layer.CURRENT_PROJECT_VERSION}, "decision": "kept"}}\n'
    built.decisions_path.write_bytes(line.encode())
    return built


def _tree(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _start(project: Project) -> None:
    with layer.Session(project) as session:
        bl.start_build(session)


def _hold_at(
    monkeypatch: pytest.MonkeyPatch, phase: str
) -> tuple[threading.Event, threading.Event]:
    """Make the real build wait for ``release`` when it reports ``phase``; ``arrived`` says when."""
    return hold_at(monkeypatch, bl, "build_store_cancellable", phase)


def _button(view: pn.viewable.Viewable, name: str) -> pn.widgets.Button:
    return next(b for b in view.select(pn.widgets.Button) if b.name == name)


def test_build__finished__summary_states_records_and_cross_search_duplicates(
    project: Project,
) -> None:
    _start(project)
    tasks.runner_for(project).join(30)

    status = bl.build_status(project)

    assert status.outcome == "done"
    assert status.result == bl.BuildSummary(records=5, duplicates_removed=5)
    view = build_build(project)
    assert "5 Records" in _text(view) and "5 duplicates across searches" in _text(view)


def test_build__no_capture__is_refused_with_the_next_step(tmp_path: Path) -> None:
    empty = layer.create_project(
        "demo", title="Demo", root=tmp_path / "p", source_mode="scopus", reviewer="A"
    )
    with layer.Session(empty) as session, pytest.raises(ValidationError, match="capture"):
        bl.start_build(session)


def test_status__while_the_build_runs__answers_without_waiting_and_shows_cascade_progress(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    arrived, release = _hold_at(monkeypatch, "cascade_runs")
    _start(project)
    assert arrived.wait(10)

    # both calls return while the worker is still held at "cascade_runs"
    status = returns_while_held(lambda: bl.build_status(project), release)
    text = returns_while_held(lambda: _text(build_build(project)), release)
    release.set()
    tasks.runner_for(project).join(30)

    assert status.running and bl.progress_text(status.progress).startswith("Adding the OpenAlex")
    assert "Adding the OpenAlex cascade" in text and "Layer" not in text


def test_cancel__during_the_build__keeps_the_previous_store_readable(
    project: Project, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = _tree(project.db_path.parent)
    arrived, release = _hold_at(monkeypatch, "registrations")
    _start(project)
    assert arrived.wait(10)
    bl.cancel_build(project)
    release.set()
    tasks.runner_for(project).join(30)

    assert bl.build_status(project).outcome == "cancelled"
    assert _tree(project.db_path.parent) == before  # no temporary file, nothing changed
    assert build_store(project).records_loaded == 5
    assert "previous store was kept" in _text(build_build(project))


@pytest.mark.parametrize(
    "error", [KeyboardInterrupt(), ConfigError("A setting is missing.")], ids=["interrupt", "error"]
)
def test_build__interrupted_or_failed__keeps_the_previous_store_and_says_what_to_do(
    project: Project, monkeypatch: pytest.MonkeyPatch, error: BaseException
) -> None:
    before = _tree(project.db_path.parent)

    def boom(*_: object, **__: object) -> None:
        raise error

    monkeypatch.setattr("prismabib.store.load._write_accumulator", boom)
    _start(project)
    tasks.runner_for(project).join(30)

    assert bl.build_status(project).outcome == "failed"
    assert _tree(project.db_path.parent) == before
    assert build_store(project).records_loaded == 5
    text = _text(build_build(project))
    assert "The build failed." in text and layer.translate(error).next_step in text


def test_rebuild__sealed_runs_and_decision_log__stay_byte_identical(project: Project) -> None:
    raw, decisions = _tree(project.raw_dir), project.decisions_path.read_bytes()

    _start(project)
    tasks.runner_for(project).join(30)

    assert bl.build_status(project).outcome == "done"
    assert _tree(project.raw_dir) == raw
    assert project.decisions_path.read_bytes() == decisions
    assert "Decision log are never touched" in _text(build_build(project))


def test_build_screen__every_control_is_labelled_and_none_offers_the_cascade(
    project: Project,
) -> None:
    for view in (build_build(project), build_stepper(project)):
        assert unlabelled_controls(view) == []
        names = " ".join(w.name for w in view.select(pn.widgets.Widget))
        assert "cascade" not in names.lower()
    assert _button(build_build(project), "Rebuild the store").button_type == "primary"


def test_build_screen__button_click__runs_the_build_and_shows_the_summary(
    project: Project,
) -> None:
    view = build_build(project)

    _button(view, "Rebuild the store").clicks += 1
    tasks.runner_for(project).join(30)
    _button(view, "Cancel the build").clicks += 0  # present, labelled, and idle

    assert bl.build_status(project).outcome == "done"
    assert _button(view, "Cancel the build").disabled
    assert "5 Records" in _text(build_build(project))


def test_build_screen__read_only_session__shows_the_sentence(project: Project) -> None:
    holder = layer.open_session(project.slug, root=project.root.parent)
    try:
        view = build_build(project)
        _button(view, "Rebuild the store").clicks += 1
        assert "Another window" in _text(view)
    finally:
        holder.close()
    with pytest.raises(ProjectLockedError):
        bl.start_build(holder)
