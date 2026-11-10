"""The background-task runner every long step reuses.

One task at a time per Project, run on a non-daemon thread (so a stop of the app lets a capture
reach its next page boundary before the process exits), with a progress value the worker reports
and a status that is only a memory read, so it can be polled while the task runs. The runner
registers its task with :mod:`prismabib.gui.stop_model` so "Stop the app" cancels it.

Runners are kept per Project folder for the life of the process: a second browser tab, or the
same one reopened, reads the same status.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from prismabib.errors import CaptureCancelledError, OperationCancelled, ValidationError
from prismabib.gui import stop_model
from prismabib.project import Project

Outcome = Literal["done", "cancelled", "failed"]
Work = Callable[[threading.Event, Callable[[Any], None]], Any]


@dataclass(frozen=True)
class TaskStatus:
    """A snapshot: ``outcome`` is ``None`` while running or before any task has run."""

    name: str | None = None
    running: bool = False
    cancelling: bool = False
    progress: Any = None
    outcome: Outcome | None = None
    result: Any = None
    error: BaseException | None = None


class TaskRunner:
    """Runs one task at a time; ``work(cancel, report)`` calls ``report(progress)`` as it goes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()
        self._status = TaskStatus()
        self._when_idle: list[Callable[[], None]] = []

    def start(self, name: str, work: Work) -> None:
        """Start ``work``; refused while another task of this Project is running."""
        with self._lock:
            if self._status.running:
                raise ValidationError(
                    f"{self._status.name} is still running. Wait for it to finish or cancel it."
                )
            before = self._status
            self._cancel = cancel = threading.Event()
            self._status = TaskStatus(name=name, running=True)
            task = stop_model.register(name, cancel)
            thread = threading.Thread(target=self._run, args=(task, work, cancel))
            try:
                thread.start()
            except BaseException:  # no thread, so nothing will ever clear "running" or unregister
                stop_model.unregister(task)
                self._status = before
                raise
            self._thread = thread

    def _report(self, progress: Any) -> None:
        with self._lock:
            self._status = TaskStatus(
                self._status.name, True, self._cancel.is_set(), progress=progress
            )

    def _run(self, task: stop_model.RunningTask, work: Work, cancel: threading.Event) -> None:
        outcome: Outcome = "done"
        result: Any = None
        error: BaseException | None = None
        try:
            result = work(cancel, self._report)
        except (CaptureCancelledError, OperationCancelled):
            outcome = "cancelled"
        except BaseException as exc:  # noqa: BLE001 - kept in the status; the screen words it
            outcome, error = "failed", exc
        finally:
            stop_model.unregister(task)
            with self._lock:
                self._status = TaskStatus(
                    task.name, False, False, self._status.progress, outcome, result, error
                )
                waiting, self._when_idle = self._when_idle, []
            for callback in waiting:
                callback()

    def cancel(self) -> None:
        """Ask the running task to stop at its next safe point; a no-op when none runs."""
        with self._lock:
            if self._status.running:
                self._cancel.set()
                s = self._status
                self._status = TaskStatus(s.name, True, True, progress=s.progress)

    def when_idle(self, callback: Callable[[], None]) -> None:
        """Call ``callback`` now, or as soon as the running task ends (on that task's thread)."""
        with self._lock:
            if self._status.running:
                self._when_idle.append(callback)
                return
        callback()

    def status(self) -> TaskStatus:
        with self._lock:
            return self._status

    def join(self, timeout: float | None = None) -> None:
        thread = self._thread
        if thread is not None:
            thread.join(timeout)


_runners: dict[Path, TaskRunner] = {}
_registry_lock = threading.Lock()


def runner_for(project: Project) -> TaskRunner:
    """The one runner of ``project`` (created on first use)."""
    key = project.root.resolve()
    with _registry_lock:
        return _runners.setdefault(key, TaskRunner())


def status_of(project: Project, name: str) -> TaskStatus:
    """The runner's status when it is about task ``name``, else an idle one (a memory read).

    The runner is shared by every task of a Project, so a screen reads its own task's status
    only: another task's progress is not its progress.
    """
    status = runner_for(project).status()
    return status if status.name == name else TaskStatus()


def cancel_named(project: Project, *names: str) -> None:
    """Stop the running task if it is one of ``names``; any other running task is left alone."""
    runner = runner_for(project)
    if runner.status().name in names:
        runner.cancel()


def stop_all(wait: float) -> list[str]:
    """Ask every running task to stop, wait up to ``wait`` seconds in all, and name the stragglers."""
    stop_model.cancel_all()
    with _registry_lock:
        runners = list(_runners.values())
    deadline = time.monotonic() + wait
    for runner in runners:
        runner.join(max(0.0, deadline - time.monotonic()))
    statuses = [r.status() for r in runners]
    return [str(s.name) for s in statuses if s.running]


def reset_runners() -> None:
    """Forget every runner (a test's stand-in for the app having been stopped and restarted)."""
    with _registry_lock:
        _runners.clear()
