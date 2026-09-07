"""The append-only log conformance suite, shared by Stage 4 and Stage 8 (ADR 0002/0023).

BUILD_PLAN's own Stage 8 test table names ``test_override__is_appended_not_edited``
as reusing "the Stage 4 log tests via a shared parameterised suite" -- this is
that suite. It is not a test module pytest collects on its own (no
``test_*`` function lives at module scope here); it exports a small adapter,
:class:`LogUnderTest`, and a handful of ``append_only_log__*`` check
functions that assert the invariants :class:`prismabib.prisma.log.AppendOnlyLog`
promises regardless of which event type it is storing. Each concrete log's
own test module builds a :class:`LogUnderTest` for its log and calls these
functions from an ordinary ``test_*`` wrapper --
``tests/integration/prisma/test_log.py`` for :class:`~prismabib.prisma.log.DecisionLog`,
``tests/integration/taxonomy/test_overrides.py`` for
:class:`~prismabib.taxonomy.overrides.OverrideLog`.

Kept out of :mod:`tests.prisma_helpers`/:mod:`tests.taxonomy_helpers`
because it is shaped by *both* logs at once, not by either module's own
corpus/criteria fixture machinery.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import pytest

from prismabib.errors import LogError
from tests.log_bytes_helpers import (
    append_raw_bytes,
    overwrite_bytes,
    read_bytes,
    rewrite_sidecar,
    sidecar_matches,
    sidecar_path_for,
)


class _Event(Protocol):
    """The one thing this suite needs from an event: it can be dumped to JSON."""

    def model_dump_json(self) -> str: ...


@dataclass
class LogUnderTest:
    """One append-only log's testing surface, normalised for this suite.

    Attributes:
        path: The log's JSONL path.
        append_one: Appends one new, distinct, valid event and returns it.
            Distinct on every call (a fresh ``record_id``/equivalent), so
            repeated calls never collide on a business-rule uniqueness
            constraint that has nothing to do with the append-only mechanics
            under test here.
        load: Reads and validates every event currently in the log, in file
            order.
        event_id_of: Extracts one event's ``event_id``.
    """

    path: Path
    append_one: Callable[[], _Event]
    load: Callable[[], list[Any]]
    event_id_of: Callable[[Any], str]

    @property
    def checksum_path(self) -> Path:
        """The log's ``.sha256`` sidecar path."""
        return sidecar_path_for(self.path)


def append_only_log__append__is_fsynced_and_checksummed(log: LogUnderTest) -> None:
    """Every append is immediately visible on disk, with the sidecar covering it.

    Args:
        log: The log under test.
    """
    for expected_lines in (1, 2, 3):
        log.append_one()
        assert read_bytes(log.path).count(b"\n") == expected_lines
        assert sidecar_matches(log.path)


def append_only_log__is_appended_not_edited(log: LogUnderTest) -> None:
    """The append-only guarantee itself: an earlier line's bytes never change.

    This is the fixture-independent core of BUILD_PLAN's
    ``test_override__is_appended_not_edited`` / (ADR 0002 rule 8) "a
    reversal is a new event, never an edit": nothing in this module ever
    rewrites a byte that already reached disk, only appends past the current
    end of file.

    Args:
        log: The log under test.
    """
    first = log.append_one()
    after_first_append = read_bytes(log.path)

    second = log.append_one()
    after_second_append = read_bytes(log.path)

    assert after_second_append.startswith(after_first_append)
    assert after_second_append != after_first_append
    reloaded = log.load()
    assert [log.event_id_of(event) for event in reloaded] == [
        log.event_id_of(first),
        log.event_id_of(second),
    ]


def append_only_log__hand_edited_file__raises_log_error_on_load(log: LogUnderTest) -> None:
    """Editing an already-written line, from outside the log's own writer, is detected.

    Args:
        log: The log under test.
    """
    log.append_one()
    original = read_bytes(log.path)
    edited = original.replace(b'"', b"'", 1)
    assert edited != original, "guard the guard: the tamper must actually change the bytes"
    overwrite_bytes(log.path, edited)

    with pytest.raises(LogError, match="may have been edited by hand"):
        log.load()


def append_only_log__truncated_final_line__raises_with_line_number(log: LogUnderTest) -> None:
    """A crash mid-append leaves a partial final line, detected and named by line number.

    Args:
        log: The log under test.
    """
    log.append_one()
    log.append_one()
    append_raw_bytes(log.path, b'{"event_id": "deliberately-unterminated"')

    with pytest.raises(LogError, match=r"truncated final line at line 3") as excinfo:
        log.load()
    assert "crashed mid-write" in str(excinfo.value)


def append_only_log__duplicate_event_id_inside_the_file__raises(log: LogUnderTest) -> None:
    """Two lines sharing one ``event_id`` -- a corrupted or hand-duplicated file -- raise.

    Args:
        log: The log under test.
    """
    log.append_one()
    line = read_bytes(log.path)
    overwrite_bytes(log.path, line + line)
    rewrite_sidecar(log.path)

    with pytest.raises(LogError, match=r":2: duplicate event_id"):
        log.load()


def append_only_log__unknown_schema_version__raises(log: LogUnderTest) -> None:
    """A ``schema_version`` this reader does not know about fails loudly, not silently.

    Args:
        log: The log under test.
    """
    log.append_one()
    first_line = read_bytes(log.path).decode("utf-8").splitlines()[0]
    payload = json.loads(first_line)
    payload["schema_version"] = 999
    overwrite_bytes(log.path, (json.dumps(payload) + "\n").encode("utf-8"))
    rewrite_sidecar(log.path)

    with pytest.raises(LogError, match=r"unknown schema_version 999"):
        log.load()


__all__ = [
    "LogUnderTest",
    "append_only_log__append__is_fsynced_and_checksummed",
    "append_only_log__duplicate_event_id_inside_the_file__raises",
    "append_only_log__hand_edited_file__raises_log_error_on_load",
    "append_only_log__is_appended_not_edited",
    "append_only_log__truncated_final_line__raises_with_line_number",
    "append_only_log__unknown_schema_version__raises",
]
