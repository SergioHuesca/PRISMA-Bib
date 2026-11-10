"""The background-task runner: what happens when it cannot start, and when_idle."""

from __future__ import annotations

import threading
from collections.abc import Callable

import pytest

from prismabib.gui import stop_model, tasks

pytestmark = pytest.mark.unit


def _work(_cancel: threading.Event, _report: Callable[[object], None]) -> str:
    return "done"


def test_start__thread_cannot_start__status_and_registration_are_cleaned_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = tasks.TaskRunner()

    class Failing(threading.Thread):
        def start(self) -> None:
            raise RuntimeError("can't start new thread")

    with monkeypatch.context() as patch:
        patch.setattr(tasks.threading, "Thread", Failing)
        with pytest.raises(RuntimeError):
            runner.start("A task", _work)

    assert not runner.status().running  # not stuck on "running"
    assert stop_model.running_names() == []  # and no leaked registration
    runner.start("A task", _work)  # the runner is usable afterwards
    runner.join(10)
    assert runner.status().outcome == "done"


def test_when_idle__nothing_running__calls_back_at_once() -> None:
    calls: list[str] = []

    tasks.TaskRunner().when_idle(lambda: calls.append("now"))

    assert calls == ["now"]


def test_when_idle__task_running__calls_back_when_it_ends() -> None:
    runner, release, calls = tasks.TaskRunner(), threading.Event(), []

    def work(_cancel: threading.Event, _report: Callable[[object], None]) -> None:
        assert release.wait(10)

    runner.start("A task", work)
    runner.when_idle(lambda: calls.append("later"))
    assert calls == []
    release.set()
    runner.join(10)

    assert calls == ["later"]
