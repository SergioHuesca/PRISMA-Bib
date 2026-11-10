"""Scopus capture as the screen sees it (GUI spec #312, ticket #329): start, cancel, discard, state.

Starting needs an explicit confirmation that follows a size check of the query as it is now.
The capture runs on the Project's task runner (:mod:`prismabib.gui.tasks`). Nothing here deletes
or overwrites a Sealed run: the only writes are ``capture_search`` (which seals a new run or
resumes an unsealed one) and the library's ``discard_run`` (which marks an unsealed run).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

from prismabib.capture.discard import discard_run
from prismabib.capture.layout import CACHE_DIRNAME, NON_RUN_DIRNAMES, is_sealed
from prismabib.capture.progress import CaptureProgress
from prismabib.capture.writer import (  # pyright: ignore[reportPrivateUsage]
    _CURSOR_FILENAME,
    _load_cursor_state,
    capture_search,
)
from prismabib.errors import ValidationError
from prismabib.gui import keys_model as km
from prismabib.gui import tasks
from prismabib.gui.layer import ErrorClass, Session, translate, write_operation
from prismabib.project import Project
from prismabib.query import build_query_for_project
from prismabib.user_settings import load_settings

TASK_NAME: Final = "The Scopus capture"
OPENALEX_TASK_NAME: Final = "The OpenAlex capture"
#: The tasks the capture screen's Cancel may stop.
CAPTURE_TASK_NAMES: Final = frozenset({TASK_NAME, OPENALEX_TASK_NAME})


class CaptureState(StrEnum):
    NOT_STARTED = "not started"
    RUNNING = "running"
    CANCELLING = "cancelling"
    UNSEALED = "unsealed"
    DISCARDED = "discarded"
    SEALED = "sealed"
    NETWORK_STOPPED = "network stopped"
    QUOTA_WAIT = "quota wait"


#: The text each state is shown with.
STATE_TEXT: Final[dict[CaptureState, str]] = {
    CaptureState.NOT_STARTED: "Not started. Check the size of the search, then confirm to capture.",
    CaptureState.RUNNING: "Running. You can close the browser; the capture keeps going.",
    CaptureState.CANCELLING: "Cancelling. It stops after the page being fetched is saved.",
    CaptureState.UNSEALED: (
        "Unsealed and resumable. The pages already fetched are kept; capture again to continue."
    ),
    CaptureState.DISCARDED: "Discarded. Its pages stay on disk as an audit trail.",
    CaptureState.SEALED: "Sealed. The capture is finished and can no longer be changed.",
    CaptureState.NETWORK_STOPPED: (
        "Stopped by a network failure. The pages already fetched are kept."
    ),
    CaptureState.QUOTA_WAIT: "Waiting for the Scopus quota to reset. The pages already fetched are kept.",
}


@dataclass(frozen=True)
class CaptureStatus:
    """What the capture screen shows. ``message`` is a sentence and next step after a failure."""

    state: CaptureState
    run_id: str | None = None
    pages_done: int = 0
    records_seen: int = 0
    total_results: int | None = None
    message: str = ""


_checked: dict[Path, str] = {}


def note_size_check(project: Project, query: str | None) -> None:
    """Record that ``query`` was size-checked and may be captured (``None`` withdraws it)."""
    key = project.root.resolve()
    if query is None:
        _checked.pop(key, None)
    else:
        _checked[key] = query


def _disk(project: Project) -> CaptureStatus:
    unsealed: CaptureStatus | None = None
    discarded: CaptureStatus | None = None
    sealed = False
    raw = project.raw_dir
    runs = sorted(raw.iterdir()) if raw.is_dir() else []
    for run in runs:
        if not run.is_dir() or run.name in NON_RUN_DIRNAMES or run.name == CACHE_DIRNAME:
            continue
        if is_sealed(run):
            sealed = True
        elif (run / _CURSOR_FILENAME).is_file():
            try:
                cursor = _load_cursor_state(run / _CURSOR_FILENAME)
            except (OSError, ValueError):
                continue
            found = CaptureStatus(
                CaptureState.DISCARDED if cursor.discarded else CaptureState.UNSEALED,
                run.name,
                len(cursor.payload_files),
            )
            if cursor.discarded:
                discarded = found
            else:
                unsealed = found
    if unsealed:
        return unsealed
    if sealed:
        return CaptureStatus(CaptureState.SEALED)
    return discarded or CaptureStatus(CaptureState.NOT_STARTED)


def capture_status(project: Project) -> CaptureStatus:
    """The capture's state. While it runs this reads memory only, so it returns at once."""
    task = tasks.runner_for(project).status()
    if task.name != TASK_NAME:  # another task (a build, an export) is not this screen's
        return _disk(project)
    seen: CaptureProgress | None = task.progress
    shown: dict[str, Any] = (
        {}
        if seen is None
        else {
            "run_id": seen.run_id,
            "pages_done": seen.pages_done,
            "records_seen": seen.records_seen,
            "total_results": seen.total_results,
        }
    )
    if task.running:
        state = CaptureState.CANCELLING if task.cancelling else CaptureState.RUNNING
        return CaptureStatus(state, **shown)
    if task.outcome == "failed" and task.error is not None:
        message = translate(task.error)
        text = f"{message.sentence} {message.next_step}"
        by_class = {
            ErrorClass.NETWORK: CaptureState.NETWORK_STOPPED,
            ErrorClass.QUOTA: CaptureState.QUOTA_WAIT,
        }
        if message.error_class in by_class:
            return CaptureStatus(by_class[message.error_class], message=text, **shown)
        return replace(_disk(project), message=text)
    return _disk(project)


@write_operation
def start_capture(session: Session, *, confirmed: bool, user_file: Path | None = None) -> None:
    """Start (or resume) the Scopus capture in the background.

    Raises:
        ProjectLockedError: If the session is read-only.
        ValidationError: Without the confirmation, without a size check of the current query,
            when the capture is Sealed already, or while a task is running.
    """
    project = session.require_writable()
    if not confirmed:
        raise ValidationError("Confirm that you want to capture before it starts.")
    if _checked.get(project.root.resolve()) != build_query_for_project(project):
        raise ValidationError("Check the size of the saved query first; it has changed or not run.")
    if _disk(project).state is CaptureState.SEALED:
        raise ValidationError("This capture is already Sealed; it cannot be run again.")
    settings = load_settings(km.project_env_file(project), user_file)

    def work(cancel: threading.Event, report: Any) -> object:
        return capture_search(project, cancel=cancel, on_progress=report, settings=settings)

    tasks.runner_for(project).start(TASK_NAME, work)


def cancel_capture(project: Project) -> None:
    """Stop the running capture at its next page; the run stays unsealed and resumable.

    Only a capture is stopped: another task of the Project (a build, an export) is left running.
    """
    runner = tasks.runner_for(project)
    if runner.status().name in CAPTURE_TASK_NAMES:
        runner.cancel()


@write_operation
def discard_capture(session: Session, run_id: str, *, confirmed: bool) -> None:
    """Mark an unsealed run discarded after a confirmation; never touches a Sealed run."""
    project = session.require_writable()
    if not confirmed:
        raise ValidationError("Confirm that you want to discard this unsealed capture.")
    if tasks.runner_for(project).status().running:
        raise ValidationError("The capture is running. Cancel it before discarding.")
    discard_run(project, run_id)
