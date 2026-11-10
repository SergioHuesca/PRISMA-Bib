"""Helpers the GUI integration tests share: reading a screen, waiting, holding a worker."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Any, TypeVar

import panel as pn
import pytest

T = TypeVar("T")


def text(view: pn.viewable.Viewable) -> str:
    """Every Markdown pane of ``view``, joined: what the researcher reads."""
    return "\n".join(str(p.object) for p in view.select(pn.pane.Markdown))


def wait_for(read: Callable[[], T], done: Callable[[T], bool], *, timeout: float = 10) -> T:
    """Poll ``read`` until ``done`` accepts its value; fail with the last value after ``timeout``."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = read()
        if done(value):
            return value
        time.sleep(0.01)
    raise AssertionError(f"state never reached; last {read()}")


def hold_at(
    monkeypatch: pytest.MonkeyPatch, owner: object, name: str, phase: str
) -> tuple[threading.Event, threading.Event]:
    """Make ``owner.name`` (a long task taking ``on_progress=``) wait when it reports ``phase``.

    Returns ``(arrived, release)``: ``arrived`` is set once the worker is held at ``phase``, and
    the worker continues when ``release`` is set.
    """
    arrived, release = threading.Event(), threading.Event()
    real = getattr(owner, name)

    def held(project: Any, **kwargs: Any) -> Any:
        report: Callable[[Any], None] = kwargs["on_progress"]

        def on_progress(event: Any) -> None:
            report(event)
            if event.phase == phase:
                arrived.set()
                assert release.wait(10)

        return real(project, **{**kwargs, "on_progress": on_progress})

    monkeypatch.setattr(owner, name, held)
    return arrived, release


def returns_while_held(call: Callable[[], T], release: threading.Event) -> T:
    """Run ``call`` on another thread and require it to return while the worker is still held.

    Deterministic: the worker only continues when ``release`` is set, after ``call`` returned.
    A call that waited for the worker would still be running when the generous join times out.
    """
    box: list[T] = []
    thread = threading.Thread(target=lambda: box.append(call()), daemon=True)
    thread.start()
    thread.join(5)
    assert not thread.is_alive(), "the call waited for the held worker"
    assert not release.is_set()
    return box[0]
