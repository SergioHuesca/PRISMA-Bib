"""Build the store as the screen sees it: start, cancel, summary.

The build runs on the Project's task runner (:mod:`prismabib.gui.tasks`) through the library's
``build_store_cancellable``, which always rebuilds from Layer 0 into a temporary file and swaps it
in only when complete: a cancel or a failure leaves the previous store as it was. Nothing here
deletes the store, touches ``raw/`` or writes to the Decision log. The build reads no keys, so no
settings are passed.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Final

from prismabib.capture.layout import is_sealed
from prismabib.errors import ValidationError
from prismabib.gui import tasks
from prismabib.gui.layer import Session, search_runs, write_operation
from prismabib.progress import ProgressEvent
from prismabib.project import Project
from prismabib.store.db import connect
from prismabib.store.load import StoreStats, build_store_cancellable

TASK_NAME: Final = "The store build"

#: What the researcher reads for each of the library's safe points (no mention of Layers).
PHASE_TEXT: Final = {
    "start": "Starting.",
    "search_runs": "Reading the captured searches.",
    "abstract_runs": "Reading the abstracts.",
    "openalex_runs": "Reading the OpenAlex data.",
    "cascade_runs": "Adding the OpenAlex cascade",
    "fulltext_runs": "Reading the full texts.",
    "registrations": "Reading the registered records.",
    "write": "Writing the store.",
}


@dataclass(frozen=True)
class BuildSummary:
    """How many Records the store holds and how many duplicates across searches were removed."""

    records: int
    duplicates_removed: int


def progress_text(progress: ProgressEvent | None) -> str:
    """A sentence for the last progress event the build reported (none yet: nothing)."""
    if progress is None:
        return ""
    text = PHASE_TEXT.get(progress.phase, progress.phase)
    if progress.phase == "cascade_runs":
        of = f" of {progress.total}" if progress.total else ""
        return f"{text}: {progress.done}{of} done."
    return text


def summarise(project: Project, stats: StoreStats) -> BuildSummary:
    """Record count (Scopus records, or OpenAlex works) and duplicates across searches."""
    records = stats.openalex_records_loaded or stats.records_loaded
    connection = connect(project, read_only=True)
    try:
        row = connection.execute(
            "SELECT COALESCE(SUM(duplicates), 0) FROM run_duplicates"
        ).fetchone()
    finally:
        connection.close()
    return BuildSummary(records, int(row[0]) if row else 0)


@write_operation
def start_build(session: Session) -> None:
    """Rebuild the store from the captured searches in the background.

    Raises:
        ProjectLockedError: If the session is read-only.
        ValidationError: With no Sealed capture to build from, or while a task is running.
    """
    project = session.require_writable()
    if not any(is_sealed(run) for run in search_runs(project)):
        raise ValidationError("There is nothing to build from yet. Finish the capture first.")

    def work(cancel: threading.Event, report: Any) -> BuildSummary:
        stats = build_store_cancellable(project, rebuild=True, cancel=cancel, on_progress=report)
        return summarise(project, stats)

    tasks.runner_for(project).start(TASK_NAME, work)


def cancel_build(project: Project) -> None:
    """Stop the running build at its next safe point; the previous store stays.

    Only the build is stopped: another task of the Project (a capture) is left running.
    """
    tasks.cancel_named(project, TASK_NAME)


def build_status(project: Project) -> tasks.TaskStatus:
    """The runner's status when it is about the build, else an idle one (a memory read)."""
    return tasks.status_of(project, TASK_NAME)
