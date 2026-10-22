"""Integration tests for :meth:`prismabib.log.AppendOnlyLog.append_if` (ADR 0029 section 8).

The decision-log behaviour of the shared machinery is covered in
``tests/integration/prisma/test_log.py``; this file covers only what
``append_if`` adds: a caller's check and the append run in one exclusive lock.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
import threading
from collections.abc import Sequence
from pathlib import Path

import pytest
from pydantic import BaseModel

from prismabib.errors import LogError
from prismabib.log import AppendOnlyLog


class _Reg(BaseModel):
    schema_version: int = 1
    event_id: str
    candidate_id: str


def _log(path: Path) -> AppendOnlyLog[_Reg]:
    return AppendOnlyLog(
        path,
        model=_Reg,
        current_schema_version=1,
        event_id=lambda event: event.event_id,
        noun="registration",
        event_description="registration",
        recovery_hint="the registration writer",
        writer_name="the registration writer",
        labour="human registration",
    )


def _refuse_if_registered(candidate_id: str):  # type: ignore[no-untyped-def]
    def check(existing: Sequence[_Reg]) -> None:
        if any(event.candidate_id == candidate_id for event in existing):
            raise ValueError(f"{candidate_id} is already registered")

    return check


@pytest.mark.integration
def test_append_if__check_returns__appends_and_updates_the_sidecar(tmp_path: Path) -> None:
    log = _log(tmp_path / "decisions" / "registrations.jsonl")
    event = _Reg(event_id="e1", candidate_id="c1")

    log.append_if(event, _refuse_if_registered("c1"))

    assert log.load() == [event]
    digest = hashlib.sha256(log.path.read_bytes()).hexdigest()
    assert log.checksum_path.read_text(encoding="utf-8").split()[0] == digest


@pytest.mark.integration
def test_append_if__check__sees_the_confirmed_events_in_file_order(tmp_path: Path) -> None:
    log = _log(tmp_path / "registrations.jsonl")
    first, second = _Reg(event_id="e1", candidate_id="c1"), _Reg(event_id="e2", candidate_id="c2")
    log.write_event(first)
    seen: list[Sequence[_Reg]] = []

    log.append_if(second, lambda existing: seen.append(list(existing)))

    assert seen == [[first]]


@pytest.mark.integration
def test_append_if__check_raises__propagates_and_writes_nothing(tmp_path: Path) -> None:
    log = _log(tmp_path / "registrations.jsonl")
    log.write_event(_Reg(event_id="e1", candidate_id="c1"))
    before = (log.path.read_bytes(), log.checksum_path.read_bytes())

    with pytest.raises(ValueError, match="already registered"):
        log.append_if(_Reg(event_id="e2", candidate_id="c1"), _refuse_if_registered("c1"))

    assert (log.path.read_bytes(), log.checksum_path.read_bytes()) == before
    # The lock was released on the way out: the log is still usable.
    log.write_event(_Reg(event_id="e3", candidate_id="c3"))


@pytest.mark.integration
def test_append_if__duplicate_event_id__still_raises_log_error(tmp_path: Path) -> None:
    log = _log(tmp_path / "registrations.jsonl")
    log.write_event(_Reg(event_id="e1", candidate_id="c1"))

    with pytest.raises(LogError, match="duplicate event_id"):
        log.append_if(_Reg(event_id="e1", candidate_id="c2"), lambda existing: None)


@pytest.mark.integration
def test_append_if__check_touches_the_same_log__raises_the_reentrancy_error(
    tmp_path: Path,
) -> None:
    log = _log(tmp_path / "registrations.jsonl")

    with pytest.raises(LogError, match="not re-entrant"):
        log.append_if(_Reg(event_id="e1", candidate_id="c1"), lambda existing: log.load())

    assert not log.path.read_bytes()


@pytest.mark.integration
def test_append_if__two_handles_same_candidate__exactly_one_event_is_written(
    tmp_path: Path,
) -> None:
    """ADR 0029 V5: the check and the append share one lock, so the race has one winner."""
    path = tmp_path / "registrations.jsonl"
    logs = [_log(path), _log(path)]
    barrier = threading.Barrier(len(logs))
    outcomes: list[str] = []

    def register(log: AppendOnlyLog[_Reg], event_id: str) -> None:
        barrier.wait()
        try:
            log.append_if(_Reg(event_id=event_id, candidate_id="c1"), _refuse_if_registered("c1"))
        except ValueError:
            outcomes.append("refused")
        else:
            outcomes.append("appended")

    threads = [
        threading.Thread(target=register, args=(log, f"e{index}")) for index, log in enumerate(logs)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert sorted(outcomes) == ["appended", "refused"]
    assert len(_log(path).load()) == 1


@pytest.mark.integration
def test_append_if__check_returns_a_value__the_value_is_handed_back(tmp_path: Path) -> None:
    log = _log(tmp_path / "registrations.jsonl")
    log.write_event(_Reg(event_id="e1", candidate_id="c1"))

    result = log.append_if(_Reg(event_id="e2", candidate_id="c2"), lambda existing: len(existing))

    assert (result, len(log.load())) == (1, 2)


_CHILD = """
import sys, time
from pathlib import Path
from pydantic import BaseModel
from prismabib.log import AppendOnlyLog


class Reg(BaseModel):
    schema_version: int = 1
    event_id: str
    candidate_id: str


path, go, event_id = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
log = AppendOnlyLog(
    path, model=Reg, current_schema_version=1, event_id=lambda e: e.event_id,
    noun="registration", event_description="registration", recovery_hint="x",
    writer_name="x", labour="x",
)


def refuse_duplicate(existing):
    if any(e.candidate_id == "c1" for e in existing):
        raise ValueError("already registered")


while not go.exists():
    time.sleep(0.001)
try:
    log.append_if(Reg(event_id=event_id, candidate_id="c1"), refuse_duplicate)
except ValueError:
    print("refused")
else:
    print("appended")
"""


@pytest.mark.integration
def test_append_if__two_processes_same_candidate__exactly_one_event_is_written(
    tmp_path: Path,
) -> None:
    """The same race as the thread test, across real processes (ADR 0029 V5)."""
    path, go = tmp_path / "registrations.jsonl", tmp_path / "go"
    children = [
        subprocess.Popen(
            [sys.executable, "-c", _CHILD, str(path), str(go), event_id],
            stdout=subprocess.PIPE,
            text=True,
        )
        for event_id in ("e1", "e2")
    ]
    go.touch()
    outputs = [child.communicate(timeout=120)[0].strip() for child in children]

    assert (sorted(outputs), [child.returncode for child in children]) == (
        ["appended", "refused"],
        [0, 0],
    )
    assert len(_log(path).load()) == 1
