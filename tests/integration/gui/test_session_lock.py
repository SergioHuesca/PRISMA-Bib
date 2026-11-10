"""Single-writer lock and read-only second session (ticket #321)."""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

import pytest

from prismabib.errors import ProjectLockedError, ValidationError
from prismabib.gui import build_layer as bl
from prismabib.gui import capture_layer as cl
from prismabib.gui import delete_store_layer as dsl
from prismabib.gui import export_layer as xl
from prismabib.gui import flow_layer as fl
from prismabib.gui import layer
from prismabib.gui import openalex_capture_layer as oal
from prismabib.gui import screening_layer as sl
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
        "save_criteria": lambda: layer.save_criteria(session, session.project.criteria),
        "start_capture": lambda: cl.start_capture(session, confirmed=True),
        "start_build": lambda: bl.start_build(session),
        "start_export": lambda: xl.start_export(session),
        "start_openalex_capture": lambda: oal.start_openalex_capture(
            session, budget=1, confirmed=True
        ),
        "delete_store": lambda: dsl.delete_store(session, confirmed=True),
        "discard_capture": lambda: cl.discard_capture(session, "run", confirmed=True),
        "add_candidates": lambda: fl.add_candidates(session, "T, 2020", reviewer="Ada"),
        "register_record": lambda: fl.register_record(
            session, candidate_id="c", reviewer="Ada", payload_file="p", match_rule="doi"
        ),
        "decide": lambda: sl.decide(session, None, "include"),  # type: ignore[arg-type]
        "undo": lambda: sl.undo(session, None),  # type: ignore[arg-type]
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


# -- stale locks: a reused pid, a take-over race, closing a lock that is no longer ours ------------


def _write_lock(root: Path, **fields: object) -> None:
    (root / "demo" / layer.LOCK_NAME).write_text(json.dumps(fields), encoding="utf-8")


def _holder(root: Path) -> dict[str, object]:
    return json.loads((root / "demo" / layer.LOCK_NAME).read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def test_stale_lock__live_pid_with_another_start_time__is_stale_not_live(root: Path) -> None:
    if layer.process_start_token(os.getpid()) is None:
        pytest.skip("no process start time on this platform: the pid check is all there is")
    # the pid exists (it is this process) but belongs to a different process than the one that
    # wrote the lock: the pid was reused after a crash
    _write_lock(root, pid=os.getpid(), start="not-this-process")

    session = layer.open_session("demo", root=root)

    assert session.read_only and session.stale
    assert session.reason is not None and "closed unexpectedly" in session.reason


def test_live_lock__pid_and_start_time_match__is_not_stale(root: Path) -> None:
    token = layer.process_start_token(os.getpid())
    _write_lock(root, pid=os.getpid(), start=token)

    session = layer.open_session("demo", root=root)

    assert session.read_only and not session.stale


def test_lock_file__written_with_the_pid_the_start_token_and_a_session_token(root: Path) -> None:
    session = layer.open_session("demo", root=root)

    held = _holder(root)

    assert held["pid"] == os.getpid()
    assert held["start"] == layer.process_start_token(os.getpid())
    assert isinstance(held["token"], str) and held["token"]
    session.close()


def test_take_over__two_confirming_windows__only_one_ends_up_writable(root: Path) -> None:
    _write_lock(root, pid=2**22 + 12345)
    first = layer.open_session("demo", root=root)
    second = layer.open_session("demo", root=root)
    assert first.stale and second.stale

    first.take_over(confirmed=True)
    second.take_over(confirmed=True)  # the loser stays read-only; it does not steal the lock

    assert not first.read_only
    assert second.read_only and not second.stale
    assert _holder(root)["token"] == first.token


def test_take_over__concurrent_windows__exactly_one_wins(root: Path) -> None:
    _write_lock(root, pid=2**22 + 12345)
    sessions = [layer.open_session("demo", root=root) for _ in range(6)]
    barrier = threading.Barrier(len(sessions))

    def take(session: layer.Session) -> None:
        barrier.wait()
        session.take_over(confirmed=True)

    threads = [threading.Thread(target=take, args=(s,)) for s in sessions]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert sum(not s.read_only for s in sessions) == 1


def test_close__lock_now_owned_by_another_window__is_left_alone(root: Path) -> None:
    first = layer.open_session("demo", root=root)
    _write_lock(root, pid=os.getpid(), start="x", token="someone-else")  # taken over from under it

    first.close()

    assert _holder(root)["token"] == "someone-else"
    assert first.read_only
