"""Single-writer lock and read-only second session (ticket #321)."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from prismabib.errors import ProjectLockedError, ValidationError
from prismabib.gui import layer
from prismabib.gui.layer import Session
from prismabib.gui.query_form_model import QueryInputs

pytestmark = pytest.mark.integration


@pytest.fixture
def root(tmp_path: Path) -> Path:
    layer.create_project(
        "demo", title="Demo", root=tmp_path, source_mode="openalex", reviewer="Ada"
    )
    return tmp_path


def _write_calls(session: Session) -> dict[str, Callable[[], object]]:
    return {
        "set_source_mode": lambda: layer.set_source_mode(session, "scopus", reviewer="Ada"),
        "save_query": lambda: layer.save_query(session, QueryInputs(terms=["a"])),
    }


def test_second_session__open_project__is_read_only_with_a_reason(root: Path) -> None:
    first = layer.open_session("demo", root=root)
    second = layer.open_session("demo", root=root)

    assert not first.read_only
    assert second.read_only
    assert second.reason is not None and "Another window" in second.reason
    assert not second.stale


def test_read_only_session__every_write_operation__is_refused(root: Path) -> None:
    layer.open_session("demo", root=root)
    second = layer.open_session("demo", root=root)
    calls = _write_calls(second)

    assert set(calls) == set(layer.WRITE_OPERATIONS)  # a new write operation needs a case here
    for call in calls.values():
        with pytest.raises(ProjectLockedError):
            call()
    assert layer.translate(ProjectLockedError("x")).error_class is layer.ErrorClass.PROJECT_LOCKED


def test_close__first_session__lets_the_second_take_write_rights(root: Path) -> None:
    first = layer.open_session("demo", root=root)
    first.close()

    second = layer.open_session("demo", root=root)

    assert not second.read_only
    assert first.read_only  # a closed session no longer writes
    second.close()
    assert not (root / "demo" / layer.LOCK_NAME).exists()


def test_close__read_only_session__leaves_the_holders_lock(root: Path) -> None:
    layer.open_session("demo", root=root)
    layer.open_session("demo", root=root).close()

    assert (root / "demo" / layer.LOCK_NAME).exists()


def test_stale_lock__crashed_process__reported_and_taken_over_only_when_confirmed(
    root: Path,
) -> None:
    dead_pid = 2**22 + 12345  # above Linux pid_max; a just-exited pid can be reused under xdist
    (root / "demo" / layer.LOCK_NAME).write_text(json.dumps({"pid": dead_pid}), encoding="utf-8")

    session = layer.open_session("demo", root=root)

    assert session.read_only and session.stale
    assert session.reason is not None and "closed unexpectedly" in session.reason
    with pytest.raises(ValidationError):
        session.take_over(confirmed=False)
    assert session.read_only
    session.take_over(confirmed=True)
    assert not session.read_only


def test_live_lock__take_over__refused(root: Path) -> None:
    layer.open_session("demo", root=root)
    second = layer.open_session("demo", root=root)

    with pytest.raises(ProjectLockedError):
        second.take_over(confirmed=True)


def test_lock__is_excluded_from_version_control() -> None:
    done = subprocess.run(
        ["git", "check-ignore", "-q", "examples/demo/" + layer.LOCK_NAME],
        capture_output=True,
        cwd=Path(__file__).parent,
        check=False,
    )

    assert done.returncode == 0


def test_restart__fresh_session__reports_the_same_step_state(root: Path) -> None:
    with layer.open_session("demo", root=root) as first:
        layer.set_source_mode(first, "scopus", reviewer="Ada")
        before = layer.step_progress(first.project)

    again = layer.open_session("demo", root=root)

    assert layer.step_progress(again.project) == before
    assert not again.read_only
