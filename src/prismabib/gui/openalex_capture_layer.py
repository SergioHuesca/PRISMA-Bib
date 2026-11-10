"""OpenAlex capture as the screen sees it (GUI spec #312, ticket #330): start, refresh, state.

Same rules as the Scopus capture in :mod:`prismabib.gui.capture_layer`, whose states, state texts,
cancel and confirmed discard (``discard_run`` handles both kinds of run) it reuses: a confirmation
that follows a size check of the query as it is now, the Project's one task runner, and no
delete or overwrite of a Sealed run. The only write is ``capture_openalex_search``.
"""

from __future__ import annotations

import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from prismabib.capture.layout import OPENALEX_SEARCH_DIRNAME, is_sealed
from prismabib.capture.openalex_search import (  # pyright: ignore[reportPrivateUsage]
    OpenAlexSearchResult,
    _read_progress,
    capture_openalex_search,
)
from prismabib.errors import ValidationError
from prismabib.gui import capture_layer as cl
from prismabib.gui import keys_model as km
from prismabib.gui import tasks
from prismabib.gui.capture_layer import CaptureState, CaptureStatus
from prismabib.gui.layer import ErrorClass, Session, translate, write_operation
from prismabib.gui.size_check_model import local_reset
from prismabib.project import Project
from prismabib.query import build_openalex_query_for_project
from prismabib.user_settings import load_settings

TASK_NAME: Final = "The OpenAlex capture"

BLOCKED_TEXT: Final = (
    "This run can never seal. It still blocks a change of Source mode, because its pages stay "
    "on disk as an audit trail."
)

#: The Scopus texts, except the two that name the source or the Source mode rule.
STATE_TEXT: Final[dict[CaptureState, str]] = {
    **cl.STATE_TEXT,
    CaptureState.QUOTA_WAIT: (
        "Waiting for the OpenAlex daily allowance to reset. The pages already fetched are kept."
    ),
    CaptureState.DISCARDED: f"{cl.STATE_TEXT[CaptureState.DISCARDED]} {BLOCKED_TEXT}",
}


def _disk(project: Project) -> CaptureStatus:
    unsealed = discarded = None
    sealed = False
    search = project.raw_dir / OPENALEX_SEARCH_DIRNAME
    for run in sorted(p for p in search.iterdir() if p.is_dir()) if search.is_dir() else []:
        if is_sealed(run):
            sealed = True
        elif (progress := _read_progress(run)) is not None:
            found = CaptureStatus(
                CaptureState.DISCARDED if progress.discarded else CaptureState.UNSEALED,
                run.name,
                len(progress.requests),
                total_results=progress.total_count,
            )
            if progress.discarded:
                discarded = found
            else:
                unsealed = found
    if unsealed:
        return unsealed
    if sealed:
        return CaptureStatus(CaptureState.SEALED)
    return discarded or CaptureStatus(CaptureState.NOT_STARTED)


def reset_text(finished: datetime, resets_in_seconds: float | None) -> str:
    """When the daily allowance returns, in local time (the server's wait, else 00:00 UTC)."""
    if resets_in_seconds is not None:
        moment = finished + timedelta(seconds=resets_in_seconds)
    else:
        moment = (finished + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return f"It resets at {local_reset(moment)} (local time)."


def openalex_status(project: Project) -> CaptureStatus:
    """The OpenAlex capture's state; while it runs this reads memory only."""
    task = tasks.runner_for(project).status()
    if task.name != TASK_NAME:  # another task (the Scopus capture) is not this screen's
        return _disk(project)
    seen = task.progress
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
    if task.outcome == "done" and task.result is not None:
        result, finished = task.result
        disk = _disk(project)
        match result.status:
            case "daily_budget":
                text = "OpenAlex's daily allowance is spent. " + reset_text(
                    finished, result.resets_in_seconds
                )
                return replace(disk, state=CaptureState.QUOTA_WAIT, message=text)
            case "budget_stop":
                return replace(disk, message=result.message)
            case "discarded":
                return replace(disk, message=f"{result.message}. {BLOCKED_TEXT}")
    return _disk(project)


@write_operation
def start_openalex_capture(
    session: Session,
    *,
    budget: int,
    confirmed: bool,
    refresh: bool = False,
    user_file: Path | None = None,
) -> None:
    """Start, resume or (``refresh``) repeat the OpenAlex capture in the background.

    Raises:
        ProjectLockedError: If the session is read-only.
        ValidationError: Without the confirmation, without a size check of the current query,
            for a budget below 1 page, for a Sealed capture without ``refresh``, or while a
            task is running.
    """
    project = session.require_writable()
    if not confirmed:
        raise ValidationError("Confirm that you want to capture before it starts.")
    query = build_openalex_query_for_project(project)
    if cl._checked.get(project.root.resolve()) != query:  # pyright: ignore[reportPrivateUsage]
        raise ValidationError("Check the size of the saved query first; it has changed or not run.")
    if budget < 1:
        raise ValidationError("The page budget must be at least 1 page.")
    if not refresh and _disk(project).state is CaptureState.SEALED:
        raise ValidationError("This capture is already Sealed. Refresh it to capture again.")
    settings = load_settings(km.project_env_file(project), user_file)

    def work(cancel: threading.Event, report: Any) -> tuple[OpenAlexSearchResult, datetime]:
        result = capture_openalex_search(
            project,
            query=query,
            budget=budget,
            refresh=refresh,
            settings=settings,
            cancel=cancel,
            on_progress=report,
        )
        return result, datetime.now(UTC)

    tasks.runner_for(project).start(TASK_NAME, work)
