"""Stopping the app from its own screen.

A background task (the runner of ticket #17) registers itself here with its cancel flag. Stopping
while one runs needs a confirmation first; once confirmed, every flag is set, so a capture ends at
its next page boundary as an unsealed run that a later capture resumes, and the server stops.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

CONFIRM_TEXT: Final = (
    "A task is still running ({names}). If you stop the app now, a running capture stops as an "
    "unsealed run: the pages already fetched are kept and a later capture resumes it."
)


@dataclass(frozen=True)
class RunningTask:
    """A task in progress and the flag that asks it to stop."""

    name: str
    cancel: threading.Event


@dataclass(frozen=True)
class StopRequest:
    """Either the app is stopping, or ``confirm`` says what the researcher must agree to first."""

    stopping: bool
    confirm: str | None = None


_lock = threading.Lock()
_tasks: list[RunningTask] = []
_stopper: Callable[[], object] | None = None


def register(name: str, cancel: threading.Event) -> RunningTask:
    """Record a running task; the caller passes the result to :func:`unregister` when it ends."""
    task = RunningTask(name, cancel)
    with _lock:
        _tasks.append(task)
    return task


def unregister(task: RunningTask) -> None:
    with _lock:
        if task in _tasks:
            _tasks.remove(task)


def running_names() -> list[str]:
    with _lock:
        return [t.name for t in _tasks]


def cancel_all() -> list[str]:
    """Set every registered cancel flag; returns the names of the tasks asked to stop."""
    with _lock:
        tasks = list(_tasks)
    for task in tasks:
        task.cancel.set()
    return [t.name for t in tasks]


def install_stopper(stopper: Callable[[], object] | None) -> None:
    """Set what stops the server (the launcher installs it once the server exists)."""
    global _stopper
    _stopper = stopper


def request_stop(*, confirmed: bool) -> StopRequest:
    """Stop the app, or say what needs confirming when a task is running."""
    with _lock:
        tasks = list(_tasks)
    if tasks and not confirmed:
        return StopRequest(False, CONFIRM_TEXT.format(names=", ".join(t.name for t in tasks)))
    cancel_all()
    if _stopper is not None:
        _stopper()
    return StopRequest(True)
