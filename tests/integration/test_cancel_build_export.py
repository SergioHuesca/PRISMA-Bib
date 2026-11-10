"""Ticket #316: the build and the export stop safely and report progress.

A cancel at any declared safe point leaves the previous store and the previous bundle
byte-identical, leaves no partial output, and never touches Layer 0 (checked by hash).
"""

from __future__ import annotations

import hashlib
import re
import threading
from typing import TYPE_CHECKING

import pytest

from prismabib.errors import OperationCancelled
from prismabib.progress import ProgressEvent
from prismabib.report.export import EXPORT_SAFE_POINTS, export_project
from prismabib.store.load import BUILD_SAFE_POINTS, build_store, build_store_cancellable
from tests.integration.store.test_provenance_load import _standard, _std_entry
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project
from tests.store_helpers import write_sealed_cascade_run

if TYPE_CHECKING:
    from pathlib import Path

    from prismabib.progress import ProgressCallback
    from prismabib.project import Project

CASCADE_RUNS = ("20260101T000000Z-cccccccc", "20260102T000000Z-dddddddd")


def _digest(root: Path) -> dict[str, str]:
    return {
        p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _cancel_at(phase: str, done: int = 0) -> tuple[threading.Event, ProgressCallback]:
    """A cancel signal, and a progress callback that sets it when the matching event arrives."""
    cancel = threading.Event()

    def on_progress(event: ProgressEvent) -> None:
        if event.phase == phase and event.done == done:
            cancel.set()

    return cancel, on_progress


@pytest.fixture
def project(tmp_path: Path) -> Project:
    spec = CorpusSpec(
        records=[RecordSpec(number=n) for n in range(1, 6)],
        criteria=CriteriaSpec(abstract_reason_codes=("OFF_TOPIC",)),
    )
    built = build_project(tmp_path, spec, slug="cancel")
    for index, run_id in enumerate(CASCADE_RUNS):
        doi = f"10.1000/cascade{index}"
        write_sealed_cascade_run(
            built.raw_dir,
            run_id,
            [
                {
                    "view": "STANDARD",
                    "identifiers": [doi],
                    "body": _standard([_std_entry(f"80000000000{index}", doi)]),
                }
            ],
        )
    return built


@pytest.mark.integration
@pytest.mark.parametrize("phase", BUILD_SAFE_POINTS)
def test_build_cancel__at_each_safe_point__keeps_the_previous_store(
    project: Project, phase: str
) -> None:
    store_before = _digest(project.db_path.parent)
    raw_before = _digest(project.raw_dir)
    cancel, on_progress = _cancel_at(phase)

    with pytest.raises(OperationCancelled):
        build_store_cancellable(
            project,
            rebuild=True,
            cancel=cancel,
            on_progress=on_progress,
        )

    assert _digest(project.db_path.parent) == store_before  # no temp file, nothing changed
    assert _digest(project.raw_dir) == raw_before
    assert build_store(project).records_loaded == 5  # still readable


@pytest.mark.integration
@pytest.mark.parametrize("phase", BUILD_SAFE_POINTS)
def test_build_cancel__with_no_previous_store__leaves_no_partial_store(
    project: Project, phase: str
) -> None:
    for leftover in project.db_path.parent.iterdir():
        leftover.unlink()
    cancel, on_progress = _cancel_at(phase)

    with pytest.raises(OperationCancelled):
        build_store_cancellable(
            project,
            rebuild=True,
            cancel=cancel,
            on_progress=on_progress,
        )

    assert list(project.db_path.parent.iterdir()) == []


@pytest.mark.integration
def test_build_cancel__between_cascade_runs__keeps_the_previous_store(project: Project) -> None:
    store_before = hashlib.sha256(project.db_path.read_bytes()).hexdigest()
    cancel, on_progress = _cancel_at("cascade_runs", done=1)

    with pytest.raises(OperationCancelled):
        build_store_cancellable(
            project,
            rebuild=True,
            cancel=cancel,
            on_progress=on_progress,
        )

    assert hashlib.sha256(project.db_path.read_bytes()).hexdigest() == store_before


@pytest.mark.integration
def test_build__reports_every_safe_point_and_the_cascade_progress(project: Project) -> None:
    seen: list[ProgressEvent] = []

    build_store_cancellable(project, rebuild=True, on_progress=seen.append)

    assert {e.phase for e in seen} == set(BUILD_SAFE_POINTS)
    assert [e for e in seen if e.phase == "cascade_runs"] == [
        ProgressEvent("cascade_runs", 0, 2),
        ProgressEvent("cascade_runs", 1, 2),
        ProgressEvent("cascade_runs", 2, 2),
    ]


@pytest.mark.integration
def test_export_cancel__at_each_safe_point__keeps_the_previous_bundle(project: Project) -> None:
    export_project(project)
    exports = project.root / "exports"
    bundle_before = _digest(exports)
    layer0_before = _digest(project.raw_dir)

    for phase in EXPORT_SAFE_POINTS:
        cancel, on_progress = _cancel_at(phase)
        with pytest.raises(OperationCancelled):
            export_project(
                project,
                cancel=cancel,
                on_progress=on_progress,
            )
        assert _digest(exports) == bundle_before, phase

    assert _digest(project.raw_dir) == layer0_before


@pytest.mark.integration
@pytest.mark.parametrize("phase", EXPORT_SAFE_POINTS)
def test_export_cancel__first_export__writes_nothing(project: Project, phase: str) -> None:
    cancel, on_progress = _cancel_at(phase)

    with pytest.raises(OperationCancelled):
        export_project(
            project,
            cancel=cancel,
            on_progress=on_progress,
        )

    assert _digest(project.root / "exports") == {}


@pytest.mark.integration
def test_cli_build_and_export__output__is_unchanged_by_the_progress_hooks(
    project: Project,
) -> None:
    """The CLI passes no callback, so it prints what it printed before: counts, no phases."""
    from typer.testing import CliRunner

    from prismabib.cli import app

    root = str(project.root.parent)
    build = CliRunner().invoke(app, ["build", project.slug, "--root", root, "--rebuild"])
    export = CliRunner().invoke(app, ["export", project.slug, "--root", root])

    assert build.exit_code == 0 and export.exit_code == 0
    assert re.search(r"^  records\s+5$", build.output, re.MULTILINE)
    assert "numbers.json" in export.output
    assert "ProgressEvent" not in build.output + export.output


@pytest.mark.integration
def test_export__reports_every_safe_point_and_table_progress(project: Project) -> None:
    seen: list[ProgressEvent] = []

    export_project(project, on_progress=seen.append)

    assert tuple(e.phase for e in seen[: len(EXPORT_SAFE_POINTS)]) == EXPORT_SAFE_POINTS
    tables = [e for e in seen if e.phase == "tables"]
    assert tables
    assert tables[-1].done == tables[-1].total
