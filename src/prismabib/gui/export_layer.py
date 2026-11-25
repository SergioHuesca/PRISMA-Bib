"""Export the citable bundle as the screen sees it: start, cancel, summary, git warnings.

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
from prismabib.gui.service import Session, write_operation
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


def progress_text(progress: ProgressEvent | None) -> str:
    """A sentence for the last progress event the export reported (none yet: nothing)."""
    if progress is None:
        return ""
    text = PHASE_TEXT.get(progress.phase, progress.phase)
    if progress.phase == "tables":
        return f"{text}: {progress.done} of {progress.total} done."
    return text


def _manifest_clause(code: dict[str, Any]) -> str:
    """What manifest.json records: the state of the installed prismabib code (a frozen contract)."""
    commit = code["commit"]
    return (
        "manifest.json records the state of the installed prismabib code instead: "
        f"{'commit ' + commit[:12] if commit else 'no commit'}, "
        f"{'with' if code['dirty'] else 'without'} uncommitted changes, "
        f"{'on a remote branch' if code['commit_is_pushed'] else 'on no remote branch'}."
    )


def _is_own_repo(folder: Path) -> bool:
    """True when git's top-level folder for ``folder`` is ``folder`` itself (read-only)."""
    top = export_module._git(["rev-parse", "--show-toplevel"], cwd=folder)
    if top.returncode != 0 or not top.stdout.strip():
        return False
    try:
        return Path(top.stdout.strip()).samefile(folder)
    except OSError:
        return False


def project_git_warnings(project: Project, code: dict[str, Any] | None = None) -> tuple[str, ...]:
    """What the researcher is told about the git state of the Project folder (spec item 4).

    The Project folder counts only when it is its own repository, not nested in another one.
    The dirty and remote tests are the library helper's, run on the Project folder; the remote
    test reads only the last fetch. Each warning also says what manifest.json records, which is
    the installed code's state (``code``, read now when not given). Only read-only git is run.
    """
    code = code or export_module._git_provenance(export_module._code_root())
    clause = _manifest_clause(code)
    state = export_module._git_provenance(project.root) if _is_own_repo(project.root) else None
    if state is None or state["commit"] is None:
        text = (
            "The bundle cannot name the state of your Project folder, because it is not its own "
            f"git repository or has no commit yet. {clause}"
        )
        return (text,)
    out = []
    if state["dirty"]:
        out.append(
            "Your Project folder has uncommitted changes, so the bundle does not describe a "
            f"committed state of it. {clause} Commit outside this app, then export again."
        )
    if not state["commit_is_pushed"]:
        out.append(
            "As far as the last fetch shows, the latest commit of your Project folder is on no "
            f"remote branch, so a reader cannot fetch that state. {clause} Push outside this "
            "app, then export again."
        )
    return tuple(out)


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
            project_git_warnings(  # the code state the manifest just recorded
                project,
                {
                    "commit": m["git_commit"],
                    "dirty": m["dirty"],
                    "commit_is_pushed": m["commit_is_pushed"],
                },
            ),
        )

    tasks.runner_for(project).start(TASK_NAME, work)


def cancel_export(project: Project) -> None:
    """Stop the running export at its next safe point; the previous bundle stays.

    Only the export is stopped: another task of the Project (a capture) is left running.
    """
    tasks.cancel_named(project, TASK_NAME)


def export_status(project: Project) -> tasks.TaskStatus:
    """The runner's status when it is about the export, else an idle one (a memory read)."""
    return tasks.status_of(project, TASK_NAME)
