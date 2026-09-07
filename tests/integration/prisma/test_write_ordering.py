"""The append-only write ordering, which nothing protected before ADR 0024.

`prisma/log.py`'s module docstring spends four paragraphs on the order these
syscalls must happen in, and until this file that order was covered by no
test at all. Demonstrated during Stage 8's review by injection: writing the
sidecar *before* the data write and fsync left the full 282-test prisma and
taxonomy suite green, and deleting the data `fsync` outright also left it
green.

That matters because the ordering is the whole crash-safety argument. The
data must be durable *before* the sidecar that vouches for it, or a machine
losing power between the two comes back with a checksum attesting to bytes
that were never written -- and the next `load()` then reports tampering on a
log nobody touched, over an event the reviewer will believe they recorded.

These tests observe the real syscalls. They patch `os`, which is a system
boundary in the same sense `respx` patches the HTTP one; nothing under
`prismabib.*` is patched (BUILD_PLAN §3.7.3).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from prismabib.stage import PrismaStage
from prismabib.taxonomy.overrides import OverrideLog
from prismabib.taxonomy.schema import load_dimensions
from tests.bibliometrics_helpers import include_everything
from tests.conftest import SeededIdFactory
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project
from tests.taxonomy_helpers import (
    LEARNING_PARADIGM_RULES_V1,
    TaxonomyRecordSpec,
    build_taxonomy_project,
    write_dimensions,
    write_rule_file,
)


def _trace(monkeypatch: pytest.MonkeyPatch, log_name: str) -> list[str]:
    """Record the ``os`` calls that touch ``log_name`` or its sidecar, in order.

    Args:
        monkeypatch: pytest's patcher, scoped to the calling test.
        log_name: The log file's basename, e.g. ``"decisions.jsonl"``.

    Returns:
        A list that fills as the traced calls happen: ``"write:data"``,
        ``"fsync:data"``, ``"write:sidecar"``, ``"fsync:sidecar"``,
        ``"replace:sidecar"``.
    """
    events: list[str] = []
    fds: dict[int, str] = {}
    real_open, real_write, real_fsync, real_replace = os.open, os.write, os.fsync, os.replace

    def _kind(path: str) -> str | None:
        if path.endswith(log_name):
            return "data"
        return "sidecar" if log_name in path else None

    def open_(path: Any, *args: Any, **kwargs: Any) -> int:
        fd = real_open(path, *args, **kwargs)
        kind = _kind(str(path))
        if kind is not None:
            fds[fd] = kind
        return fd

    def write_(fd: int, data: Any) -> int:
        if fd in fds:
            events.append(f"write:{fds[fd]}")
        return real_write(fd, data)

    def fsync_(fd: int) -> None:
        if fd in fds:
            events.append(f"fsync:{fds[fd]}")
        real_fsync(fd)

    def replace_(src: Any, dst: Any) -> None:
        kind = _kind(str(dst))
        if kind is not None:
            events.append(f"replace:{kind}")
        real_replace(src, dst)

    monkeypatch.setattr(os, "open", open_)
    monkeypatch.setattr(os, "write", write_)
    monkeypatch.setattr(os, "fsync", fsync_)
    monkeypatch.setattr(os, "replace", replace_)
    return events


def _assert_durable_before_attested(events: list[str], label: str) -> None:
    """Assert the exact syscall sequence one append must produce.

    Args:
        events: A :func:`_trace` result for exactly one append.
        label: Which log, for the failure message.

    Asserts the whole **sequence**, not orderings between first
    occurrences. An earlier version used `events.index(...)`, which returns
    the first match and therefore constrained only the first `write:data`:
    splitting the append into two writes with the fsync between them --

        os.write(fd, line[:half]); os.fsync(fd); os.write(fd, line[half:])

    -- left that version green, and 303 tests around it green, while the
    appended event was only half durable at fsync time. That is precisely
    the crash window this file exists to close, and the file content and
    checksum both end up correct, so nothing downstream notices. A future
    author converting the single `os.write` into a short-write-safe loop
    (this module already has `_read_all` in that shape) lands in it.
    """
    expected = [
        "write:data",
        "fsync:data",
        "write:sidecar",
        "fsync:sidecar",
        "replace:sidecar",
    ]
    assert events == expected, (
        f"{label}: the append's syscall sequence was {events}, expected {expected}. "
        "The data must be durable before the sidecar that vouches for it is published: a "
        "crash between the two leaves a checksum attesting to bytes that were never "
        "written, and the next load() reports tampering on a log nobody touched."
    )


@pytest.mark.integration
def test_decision_log__append__fsyncs_data_before_publishing_the_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR 0024 Decision 2, for the log whose contents cannot be recomputed."""
    project = build_project(
        tmp_path,
        CorpusSpec(
            records=[RecordSpec(number=1)],
            criteria=CriteriaSpec(version="1.0.0", abstract_reason_codes=("OFF_TOPIC",)),
        ),
        slug="ordering",
    )
    log_name = project.decisions_path.name
    from prismabib.prisma.log import DecisionLog

    decision_log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="ev"))
    events = _trace(monkeypatch, log_name)

    decision_log.append(
        record_id="scopus:2-s2.0-85100000001",
        stage=PrismaStage.TITLE_ABSTRACT,
        decision="include",
        reviewer="alice",
        criteria_version="0.1.0",
    )

    _assert_durable_before_attested(events, "DecisionLog")


@pytest.mark.integration
def test_override_log__append__fsyncs_data_before_publishing_the_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same guarantee for the second log, which is why it composes the same writer."""
    project = build_taxonomy_project(
        tmp_path, [TaxonomyRecordSpec(number=1, title="A AlphaMarker approach")], slug="ord"
    )
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    schema = load_dimensions(project)
    override_log = OverrideLog(project, id_factory=SeededIdFactory(seed=0, prefix="ov"))
    log_name = override_log.path.name
    events = _trace(monkeypatch, log_name)

    override_log.append(
        record_id="scopus:2-s2.0-85100000001",
        dimension="learning_paradigm",
        categories=("supervised",),
        reviewer="alice",
        reason="ordering probe",
        schema=schema,
    )

    _assert_durable_before_attested(events, "OverrideLog")
