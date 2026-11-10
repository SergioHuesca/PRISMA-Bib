"""Export the citable bundle as the screen sees it (GUI spec #312, ticket #335).

The export runs on the Project's task runner through the library's ``export_project`` and its
progress and cancel hooks; a cancel stops at a safe point before anything is deleted, so no
partial bundle is left. The manifest records git state; nothing here commits or pushes. The
export reads no keys, so no settings are passed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from prismabib.errors import ValidationError
from prismabib.gui import tasks
from prismabib.gui.layer import Session, write_operation
from prismabib.progress import ProgressEvent
from prismabib.project import Project
from prismabib.report import export as export_module

TASK_NAME: Final = "The export"

PHASE_TEXT: Final = {
    "start": "Starting.",
    "counts": "Counting the flow of records.",
    "render": "Preparing the figures and tables.",
    "write": "Writing the bundle.",
    "tables": "Writing the tables",
}

NO_STORE: Final = "There is nothing to export yet. Finish the Build step first."


@dataclass(frozen=True)
class ExportSummary:
    """Where the bundle is and what it holds."""

    folder: Path
    figures: int
    tables: int
    numbers: int
    warnings: tuple[str, ...]


def progress_text(progress: object) -> str:
    """A sentence for the last progress event the export reported."""
    if not isinstance(progress, ProgressEvent):
        return ""
    text = PHASE_TEXT.get(progress.phase, progress.phase)
    if progress.phase == "tables":
        return f"{text}: {progress.done} of {progress.total} done."
    return text


def git_warnings(commit: str | None, dirty: bool, pushed: bool) -> tuple[str, ...]:
    """What the researcher is told about the git state the manifest records (spec story 51).

    The library records the state of the prismabib code that ran the export, not of the Project
    folder, and the GUI never commits or pushes: committing is the researcher's own task.
    """
    if commit is None:
        text = (
            "This copy of prismabib is not inside a git repository, so the bundle cannot name a "
            "commit. manifest.json records no commit and marks the state as uncommitted. The "
            "package version in manifest.json still says which release produced the numbers; "
            "cite that."
        )
        return (text,)
    out = []
    if dirty:
        out.append(
            "The code that made this bundle has uncommitted changes, so the recorded commit does "
            "not describe it. manifest.json records this. To fix it, commit your changes outside "
            "this app, then export again."
        )
    if not pushed:
        out.append(
            "The recorded commit is on no remote branch, so a reader cannot fetch the code these "
            "numbers came from. manifest.json records this. Push the commit outside this app, "
            "then export again."
        )
    return tuple(out)


def current_git_warnings() -> tuple[str, ...]:
    """The warnings an export started now would carry (reads git, writes nothing)."""
    state = export_module._git_provenance(export_module._code_root())
    return git_warnings(state["commit"], state["dirty"], state["commit_is_pushed"])


@write_operation
def start_export(session: Session) -> None:
    """Write the citable bundle in the background.

    Raises:
        ProjectLockedError: If the session is read-only.
        ValidationError: With no built store, or while a task is running.
    """
    project = session.require_writable()
    if not project.db_path.is_file():
        raise ValidationError(NO_STORE)

    def work(cancel: threading.Event, report: Any) -> ExportSummary:
        result = export_module.export_project(project, cancel=cancel, on_progress=report)
        m = result.manifest
        return ExportSummary(
            result.root,
            len(result.figures),
            len(result.tables),
            len(result.numbers),
            git_warnings(m["git_commit"], m["dirty"], m["commit_is_pushed"]),
        )

    tasks.runner_for(project).start(TASK_NAME, work)


def cancel_export(project: Project) -> None:
    """Stop the running export at its next safe point; the previous bundle stays."""
    tasks.runner_for(project).cancel()


def export_status(project: Project) -> tasks.TaskStatus:
    """The runner's status when it is about the export, else an idle one (a memory read)."""
    status = tasks.runner_for(project).status()
    return status if status.name == TASK_NAME else tasks.TaskStatus()
