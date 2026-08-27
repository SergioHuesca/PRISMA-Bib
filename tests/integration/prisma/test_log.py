"""Integration tests for :mod:`prismabib.prisma.log` (BUILD_PLAN §Stage 4, lines 1021-1032).

Real files, real ``flock``, real checksums -- nothing here is mocked, and in
particular nothing patches a ``prismabib.*`` symbol (§3.7.3 rule 1). The
corrupted-log cases are produced by writing bytes to ``decisions.jsonl``
from the outside, exactly as a text editor or a killed process would; that
is the situation the guard exists for, and simulating it any other way
would be testing the simulation.

On "fsynced" in
:func:`test_log__append__is_fsynced_and_checksummed`: whether ``os.fsync``
was called is not observable from inside the process without patching
stdlib, which the rules of engagement do not permit for something that is
not an HTTP or clock boundary. What *is* observable, and is what the rule
exists to guarantee, is asserted instead: after ``append`` returns, the new
line is already visible to an independent file descriptor (nothing is
sitting in a user-space buffer), and the sidecar has been rewritten to
cover it -- per write, not per batch.
"""

from __future__ import annotations

import contextlib
import errno
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from prismabib.errors import LogError, ValidationError
from prismabib.prisma import engine
from prismabib.prisma.events import DecisionEvent
from prismabib.prisma.log import (
    DecisionLog,
    LockKind,
    _ByteRangeLocking,
    _LockBackend,
    _PosixLockBackend,
    _WindowsLockBackend,
)
from prismabib.project import Project
from prismabib.stage import PrismaStage
from tests.conftest import SeededIdFactory
from tests.prisma_helpers import (
    CorpusSpec,
    CriteriaSpec,
    FakeWindowsLocking,
    RecordSpec,
    append_raw_bytes,
    build_project,
    fake_msvcrt,
    overwrite_log_bytes,
    read_log_bytes,
    rewrite_sidecar,
    sidecar_matches_log,
    sidecar_path,
)

RECORDS = [RecordSpec(number=index) for index in range(1, 6)]
CRITERIA = CriteriaSpec(
    version="1.0.0",
    abstract_reason_codes=("OFF_TOPIC", "REVIEW_OR_SURVEY"),
    fulltext_reason_codes=("INACCESSIBLE",),
)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A five-record project with a built Layer 1 store and an empty log."""
    return build_project(tmp_path, CorpusSpec(records=RECORDS, criteria=CRITERIA), slug="log")


def open_log(project: Project, *, prefix: str = "id") -> DecisionLog:
    """A :class:`DecisionLog` with a seeded, deterministic id factory."""
    return DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix=prefix))


def as_persisted(event: DecisionEvent) -> DecisionEvent:
    """The event as :meth:`DecisionLog.load` will read it back.

    ``DecisionEvent`` serialises ``ts`` at millisecond precision (BUILD_PLAN's
    own example instant, ``2026-01-18T14:22:07.412Z``), while
    :meth:`DecisionLog.append` stamps it from ``datetime.now(UTC)`` at
    microsecond precision. The append's *return value* therefore carries
    sub-millisecond digits that its own on-disk line does not, and comparing
    the two directly would fail for a reason that has nothing to do with the
    behaviour under test. This helper states that truncation explicitly
    rather than hiding it behind a looser assertion; it is pinned directly
    by ``test_log__sub_millisecond_append__is_truncated_to_milliseconds_on_disk``.
    """
    return DecisionEvent.model_validate_json(event.model_dump_json())


def read_through_a_fresh_descriptor(path: Path) -> bytes:
    """Read ``path`` via a brand-new file descriptor (helper, not a test)."""
    fd = os.open(path, os.O_RDONLY)
    try:
        return b"".join(iter(lambda: os.read(fd, 4096), b""))
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# BUILD_PLAN's log-integrity table
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_log__append__is_fsynced_and_checksummed(project: Project) -> None:
    log = open_log(project)

    observations = []
    for record in RECORDS[:3]:
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record.record_id,
            reviewer="kp",
            decision="include",
        )
        observations.append(
            (
                read_through_a_fresh_descriptor(project.decisions_path).count(b"\n"),
                sidecar_matches_log(project),
            )
        )

    assert observations == [(1, True), (2, True), (3, True)]


@pytest.mark.integration
@pytest.mark.acceptance("S04-AC3")
def test_log__hand_edited_file__raises_log_error_on_load(project: Project) -> None:
    log = open_log(project)
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )
    original = read_log_bytes(project)
    edited = original.replace(b'"decision":"include"', b'"decision":"exclude"')
    overwrite_log_bytes(project, edited)

    with pytest.raises(LogError, match="may have been edited by hand"):
        log.load()

    assert edited != original


@pytest.mark.integration
def test_log__hand_edited_file__is_also_refused_by_a_further_append(project: Project) -> None:
    log = open_log(project)
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )
    overwrite_log_bytes(project, read_log_bytes(project).replace(b'"kp"', b'"mm"'))

    with pytest.raises(LogError, match="checksum mismatch"):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=RECORDS[1].record_id,
            reviewer="kp",
            decision="include",
        )


@pytest.mark.integration
def test_log__truncated_final_line__raises_with_line_number(project: Project) -> None:
    log = open_log(project)
    for record in RECORDS[:2]:
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record.record_id,
            reviewer="kp",
            decision="include",
        )
    append_raw_bytes(project, b'{"event_id":"id-000002","schema_ver')

    with pytest.raises(LogError, match=r"truncated final line at line 3") as excinfo:
        log.load()

    assert "crashed mid-write" in str(excinfo.value)


@pytest.mark.integration
def test_log__appended_lines_with_stale_sidecar__are_diagnosed_as_an_interrupted_append(
    project: Project,
) -> None:
    log = open_log(project)
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )
    # The exact signature of a crash between the durable write and the sidecar
    # rewrite: two more complete lines reached disk, the sidecar still covers
    # the earlier prefix byte-for-byte.
    crashed_lines = read_log_bytes(project).replace(b"id-000000", b"id-000001")
    crashed_lines += crashed_lines.replace(b"id-000001", b"id-000002")
    append_raw_bytes(project, crashed_lines)

    with pytest.raises(LogError) as excinfo:
        log.load()

    message = str(excinfo.value)
    assert "2 decision line(s) not covered by the checksum sidecar" in message
    assert "interrupted append" in message
    assert "not hand-editing" in message
    assert "edited by hand" not in message


@pytest.mark.integration
@pytest.mark.acceptance("S04-AC2")
def test_log__reversal_event__flips_membership_and_preserves_original(project: Project) -> None:
    log = open_log(project)
    record_id = RECORDS[0].record_id
    original = log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=record_id,
        reviewer="kp",
        decision="include",
    )
    included_before = engine.manual_abstract_set(project)

    reversal = log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=record_id,
        reviewer="kp",
        decision="exclude",
        reason_code="OFF_TOPIC",
        note="on a second reading this is a survey",
    )

    key = (PrismaStage.TITLE_ABSTRACT, record_id, "kp")
    assert record_id in included_before
    assert record_id not in engine.manual_abstract_set(project)
    assert log.load() == [as_persisted(original), as_persisted(reversal)]
    assert log.fold()[key] == as_persisted(reversal)


@pytest.mark.integration
def test_log__reversal_of_a_reversal__restores_membership(project: Project) -> None:
    log = open_log(project)
    record_id = RECORDS[0].record_id
    for decision, reason in (("include", None), ("exclude", "OFF_TOPIC"), ("include", None)):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record_id,
            reviewer="kp",
            decision=decision,  # type: ignore[arg-type]
            reason_code=reason,
        )

    assert record_id in engine.manual_abstract_set(project)
    assert [event.decision for event in log.load()] == ["include", "exclude", "include"]


@pytest.mark.integration
def test_log__exclude_without_reason_code__is_rejected(project: Project) -> None:
    log = open_log(project)

    with pytest.raises(LogError, match="reason_code is required when decision='exclude'"):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=RECORDS[0].record_id,
            reviewer="kp",
            decision="exclude",
        )

    assert log.load() == []


@pytest.mark.integration
@pytest.mark.parametrize("reason_code", ["", "NOT_A_DECLARED_CODE"])
def test_log__reason_code_not_in_criteria__is_rejected(project: Project, reason_code: str) -> None:
    log = open_log(project)

    with pytest.raises(LogError):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=RECORDS[0].record_id,
            reviewer="kp",
            decision="exclude",
            reason_code=reason_code,
        )

    assert read_log_bytes(project) == b""


@pytest.mark.integration
def test_log__reason_code_declared_for_the_other_stage__is_rejected(project: Project) -> None:
    log = open_log(project)

    with pytest.raises(LogError, match=r"is not declared in criteria.yaml's fulltext"):
        log.append(
            stage=PrismaStage.FULLTEXT,
            record_id=RECORDS[0].record_id,
            reviewer="kp",
            decision="exclude",
            reason_code="OFF_TOPIC",
        )


@pytest.mark.integration
def test_log__reason_code_declared_for_that_stage__is_accepted(project: Project) -> None:
    log = open_log(project)

    event = log.append(
        stage=PrismaStage.FULLTEXT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="exclude",
        reason_code="INACCESSIBLE",
    )

    assert log.load() == [as_persisted(event)]


@pytest.mark.integration
def test_log__unknown_schema_version__raises_not_silently_ignored(project: Project) -> None:
    log = open_log(project)
    event = log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )
    payload = json.loads(event.model_dump_json())
    payload["schema_version"] = 2
    overwrite_log_bytes(project, (json.dumps(payload) + "\n").encode("utf-8"))
    rewrite_sidecar(project)

    with pytest.raises(LogError, match=r"unknown schema_version 2 \(expected 1\)"):
        log.load()


@pytest.mark.integration
def test_log__schema_version_absent_entirely__raises(project: Project) -> None:
    log = open_log(project)
    event = log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )
    payload = json.loads(event.model_dump_json())
    del payload["schema_version"]
    overwrite_log_bytes(project, (json.dumps(payload) + "\n").encode("utf-8"))
    rewrite_sidecar(project)

    with pytest.raises(LogError, match="unknown schema_version None"):
        log.load()


@pytest.mark.integration
def test_log__concurrent_appends_from_two_handles__no_interleaved_line(project: Project) -> None:
    appends_per_handle = 15
    logs = [open_log(project, prefix="kp"), open_log(project, prefix="mm")]

    def append_all(log: DecisionLog, reviewer: str) -> None:
        for index in range(appends_per_handle):
            log.append(
                stage=PrismaStage.TITLE_ABSTRACT,
                record_id=RECORDS[index % len(RECORDS)].record_id,
                reviewer=reviewer,
                decision="include",
            )

    threads = [
        threading.Thread(target=append_all, args=(logs[0], "kp")),
        threading.Thread(target=append_all, args=(logs[1], "mm")),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = read_log_bytes(project).decode("utf-8").splitlines()
    parsed = [json.loads(line) for line in lines]
    assert len(parsed) == 2 * appends_per_handle
    assert len({payload["event_id"] for payload in parsed}) == 2 * appends_per_handle
    assert sidecar_matches_log(project)
    assert len(logs[0].load()) == 2 * appends_per_handle


# ---------------------------------------------------------------------------
# The remaining refusal paths: every way a log can fail to load
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_log__empty_log_with_no_sidecar__loads_as_empty(project: Project) -> None:
    log = open_log(project)

    assert not sidecar_path(project).exists()
    assert log.load() == []
    assert log.fold() == {}


@pytest.mark.integration
def test_log__non_empty_log_with_no_sidecar__raises(project: Project) -> None:
    log = open_log(project)
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )
    sidecar_path(project).unlink()

    with pytest.raises(LogError, match="missing checksum sidecar"):
        log.load()


@pytest.mark.integration
def test_log__blank_sidecar__raises_checksum_mismatch(project: Project) -> None:
    log = open_log(project)
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )
    sidecar_path(project).write_text("   \n", encoding="utf-8")

    with pytest.raises(LogError, match="records ''"):
        log.load()


@pytest.mark.integration
def test_log__malformed_json_line__raises_naming_the_line_number(project: Project) -> None:
    overwrite_log_bytes(project, b'{"event_id": "id-000000"\n')
    rewrite_sidecar(project)

    with pytest.raises(LogError, match=r"decisions\.jsonl:1: malformed JSON"):
        open_log(project).load()


@pytest.mark.integration
def test_log__line_that_is_not_a_valid_event__raises_naming_the_line_number(
    project: Project,
) -> None:
    log = open_log(project)
    event = log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )
    payload = json.loads(event.model_dump_json())
    payload["stage"] = "raw"
    overwrite_log_bytes(project, read_log_bytes(project) + (json.dumps(payload) + "\n").encode())
    rewrite_sidecar(project)

    with pytest.raises(LogError, match=r"decisions\.jsonl:2: malformed decision event"):
        log.load()


@pytest.mark.integration
def test_log__duplicate_event_id_inside_the_file__raises_naming_the_line_number(
    project: Project,
) -> None:
    log = open_log(project)
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )
    line = read_log_bytes(project)
    overwrite_log_bytes(project, line + line)
    rewrite_sidecar(project)

    with pytest.raises(LogError, match=r"decisions\.jsonl:2: duplicate event_id 'id-000000'"):
        log.load()


@pytest.mark.integration
def test_log__appending_an_event_already_in_the_log__raises_duplicate_event_id(
    project: Project,
) -> None:
    log = open_log(project)
    event = log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )

    with pytest.raises(LogError, match="replayed append or ULID collision"):
        log.append_event(event)

    assert log.load() == [as_persisted(event)]


@pytest.mark.integration
def test_log__append_with_a_blank_record_id__raises_validation_error(project: Project) -> None:
    log = open_log(project)

    with pytest.raises(ValidationError, match="invalid decision event"):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id="   ",
            reviewer="kp",
            decision="include",
        )

    assert read_log_bytes(project) == b""


# ---------------------------------------------------------------------------
# criteria_version stamping, paths, and bulk reads
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_log__append_without_criteria_version__stamps_the_projects_current_version(
    project: Project,
) -> None:
    log = open_log(project)

    event = log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )

    assert event.criteria_version == project.criteria.version == "1.0.0"


@pytest.mark.integration
def test_log__append_with_an_explicit_criteria_version__records_it_verbatim(
    project: Project,
) -> None:
    log = open_log(project)

    event = log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
        criteria_version="0.9.0",
    )

    assert event.criteria_version == "0.9.0"
    assert log.load()[0].criteria_version == "0.9.0"


@pytest.mark.integration
def test_log__sub_millisecond_append__is_truncated_to_milliseconds_on_disk(
    project: Project,
) -> None:
    # Pins the round-trip asymmetry `as_persisted` exists for: `append`'s
    # return value keeps `datetime.now(UTC)`'s microseconds, the line it wrote
    # carries milliseconds (BUILD_PLAN's own event example is
    # "2026-01-18T14:22:07.412Z"). Both orderings the fold depends on -- `ts`
    # first, then the monotonic `event_id` -- survive that, because the
    # `event_id` tie-break is exactly what resolves two events sharing a
    # millisecond.
    log = open_log(project)

    appended = log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )
    (reloaded,) = log.load()

    assert reloaded.ts.microsecond % 1000 == 0
    assert reloaded.ts == appended.ts.replace(microsecond=appended.ts.microsecond // 1000 * 1000)
    assert reloaded.model_dump(exclude={"ts"}) == appended.model_dump(exclude={"ts"})


@pytest.mark.integration
def test_log__paths__are_the_projects_decision_log_and_its_sidecar(project: Project) -> None:
    log = open_log(project)

    assert log.path == project.decisions_path
    assert log.checksum_path == sidecar_path(project)


@pytest.mark.integration
def test_log__decisions_directory_absent_after_clone__append_recreates_it(tmp_path: Path) -> None:
    """A freshly-cloned project can have no ``decisions/`` directory at all.

    ``Project.init`` creates ``decisions/``, so every fixture-built project in
    this suite has one -- but **git cannot store an empty directory**. A
    project committed with ``track_decisions = false`` (BUILD_PLAN §2.5 line
    291) has nothing under ``decisions/`` to track, so the directory is simply
    absent from the tree, and ``git clone`` produces a working copy without
    it. The reviewer's very first screening decision is then an ``append``
    into a directory that does not exist.

    ``O_CREAT`` creates a missing *file*, never a missing *parent directory*,
    so this must be handled explicitly or the append dies on a raw
    ``FileNotFoundError`` -- and both the log and its checksum sidecar have to
    land, since a log without a sidecar refuses to load afterwards.
    """
    project = Project.init("cloned", title="Cloned project", root=tmp_path)
    shutil.rmtree(project.root / "decisions")
    log = open_log(project)

    event = log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )

    assert log.load() == [as_persisted(event)]
    assert log.checksum_path.exists()
    assert sidecar_matches_log(project)


@pytest.mark.integration
def test_log__file_larger_than_one_read_chunk__loads_every_event(project: Project) -> None:
    log = open_log(project)
    long_note = "n" * 2048
    expected = [
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=RECORDS[index % len(RECORDS)].record_id,
            reviewer=f"reviewer-{index}",
            decision="include",
            note=long_note,
        )
        for index in range(40)
    ]

    loaded = log.load()

    assert project.decisions_path.stat().st_size > 65536
    assert loaded == [as_persisted(event) for event in expected]


@pytest.mark.integration
def test_log__append_event_of_a_hand_built_event__is_stored_verbatim(project: Project) -> None:
    log = open_log(project)
    event = DecisionEvent(
        event_id="hand-built-0001",
        ts="2026-01-18T14:22:07.412Z",  # type: ignore[arg-type]
        project=project.slug,
        stage=PrismaStage.FULLTEXT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="unsure",
        note="needs adjudication",
        criteria_version="1.0.0",
    )

    log.append_event(event)

    assert log.load() == [event]


@pytest.mark.integration
@pytest.mark.parametrize(
    "required_phrase",
    [
        pytest.param("Back the file up", id="says-to-back-up-first"),
        pytest.param("sha256sum", id="gives-the-exact-sidecar-command"),
        pytest.param("no screening decision has been lost", id="says-what-survived"),
    ],
)
def test_log__truncated_final_line__message_is_recoverable_not_just_diagnostic(
    project: Project, required_phrase: str
) -> None:
    """A crashed log is the one failure where the user's own labour is at stake.

    The message used to end at "recover manually before appending again",
    which names no procedure at all -- while the *adjacent* crash-vs-tamper
    message three hundred lines away explains its case carefully. Somebody
    who has just lost power and is looking at an error about their
    irreplaceable screening record should not have to read the source to
    find out whether their work survived, or guess at the sidecar format.

    ``sha256sum`` is asserted specifically because the recovery instruction
    tells the user to regenerate the sidecar with it, and that only works
    because prismabib's sidecar is byte-identical to ``sha256sum``'s own
    output. If the sidecar format ever stops being compatible, this test
    should fail and the instruction should change with it.
    """
    log = DecisionLog(project)
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id="scopus:1",
        reviewer="kp",
        decision="include",
    )
    with log.path.open("ab") as handle:
        handle.write(b'{"partial": ')

    with pytest.raises(LogError) as excinfo:
        log.load()

    assert required_phrase in str(excinfo.value)


# ---------------------------------------------------------------------------
# The bytes on disk, and the sidecar an outsider has to be able to verify
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_log__bytes_on_disk__are_lf_only_and_hash_to_the_sidecar(project: Project) -> None:
    """The log's bytes must be what an external ``sha256sum`` hashes.

    This assertion is trivially true on Linux and is the whole point on
    Windows, where the C runtime silently rewrites every ``\\n`` an
    ``os.open``-without-``O_BINARY`` handle writes as ``\\r\\n``, and
    silently undoes it on read. In-process both sides of that translation
    cancel, so every other test in this file would still pass while the
    file on disk carried CRLF -- and the sidecar prismabib wrote would
    disagree with ``sha256sum decisions.jsonl``.

    That disagreement is not cosmetic. The sidecar is deliberately
    ``sha256sum``-compatible, the truncated-line recovery message tells the
    user to regenerate it with exactly that command, and a tamper-detection
    digest that no outside tool can confirm is worth less than no digest at
    all: it looks like independent verification and is not.
    """
    log = open_log(project)
    for record in RECORDS[:3]:
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record.record_id,
            reviewer="kp",
            decision="include",
        )

    log_bytes = project.decisions_path.read_bytes()
    sidecar_bytes = sidecar_path(project).read_bytes()

    assert (b"\r\n" in log_bytes, b"\r\n" in sidecar_bytes) == (False, False)
    assert sidecar_bytes == f"{hashlib.sha256(log_bytes).hexdigest()}  decisions.jsonl\n".encode()


@pytest.mark.integration
def test_log__log_read_back_after_a_reopen__is_byte_identical_to_what_was_written(
    project: Project,
) -> None:
    """Round-tripping through :meth:`load` must not rewrite a single byte.

    The complement of the assertion above: ``load`` opens the same
    descriptor flags that ``append`` does, so a newline-translating handle
    on the read side would hide a newline-translating handle on the write
    side. Comparing the bytes ``append`` produced against the bytes a fresh
    ``DecisionLog`` reads, and against the digest recorded between them,
    closes that loop.
    """
    written = open_log(project)
    event = written.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=RECORDS[0].record_id,
        reviewer="kp",
        decision="include",
    )
    expected_line = (as_persisted(event).model_dump_json() + "\n").encode("utf-8")

    reloaded = open_log(project).load()

    assert read_log_bytes(project) == expected_line
    assert (reloaded, sidecar_matches_log(project)) == ([as_persisted(event)], True)


# ---------------------------------------------------------------------------
# The reported bug: `import prismabib.prisma.log` on a machine with no fcntl
# ---------------------------------------------------------------------------

#: Run in a *subprocess*, not in this interpreter, for two reasons:
#: ``prismabib.prisma.log`` is already in this process's ``sys.modules``, so an
#: in-process re-import would be answered from cache and pass vacuously; and
#: blocking ``fcntl`` here would leak into every other test in the session.
#: ``sys.modules[name] = None`` is the documented way to make ``import name``
#: raise, and is precisely what a Windows interpreter presents for ``fcntl``.
_IMPORT_WITHOUT_PLATFORM_LOCK_MODULES = """
import sys

sys.modules["fcntl"] = None
# `msvcrt` is blocked only where it is not the platform's own primitive.
# Windows' `subprocess` imports `msvcrt` itself, so blocking it there breaks
# the interpreter before it reaches prismabib -- the first real Windows CI run
# failed here with ModuleNotFoundError: _posixsubprocess, which is the stdlib
# falling back to a POSIX path that does not exist. Blocking `fcntl` alone
# still proves the claim on Windows, because `fcntl` genuinely is absent there;
# on POSIX both are blocked, so the module cannot pass by secretly needing
# either one.
if sys.platform != "win32":
    sys.modules["msvcrt"] = None

from prismabib.prisma.log import DecisionLog, fold_events

print(DecisionLog.__name__, fold_events([]) == {})
"""


@pytest.mark.integration
def test_log__module_import__does_not_need_fcntl(tmp_path: Path) -> None:
    """A machine without ``fcntl`` must still be able to import this module.

    This is the reported defect, stated exactly: ``import fcntl`` at module
    scope made ``prismabib.prisma.log`` unimportable on Windows, so a
    Windows researcher could capture a corpus and build a store and then
    discover, at the first screening decision, that the one irreplaceable
    part of the pipeline had never been able to run for them.

    ``msvcrt`` is blocked as well, so this cannot pass by accident on a
    machine that happens to have neither: the module must import with *no*
    platform locking primitive available, and reach for one only when a
    lock is actually taken.
    """
    result = subprocess.run(
        [sys.executable, "-c", _IMPORT_WITHOUT_PLATFORM_LOCK_MODULES],
        capture_output=True,
        text=True,
        check=False,
        cwd=tmp_path,
        timeout=120,
    )

    assert (result.returncode, result.stdout.strip()) == (0, "DecisionLog True"), result.stderr


# ---------------------------------------------------------------------------
# The lock backends: one contract, stated once, asserted against both
# ---------------------------------------------------------------------------
#
# The Windows parameter drives the real `_WindowsLockBackend` against an
# injected stand-in for `msvcrt.locking` (`tests.prisma_helpers`). That is a
# constructor argument, not a patched `prismabib.*` symbol, so §3.7.3 rule 1
# holds -- but see docs/testing.md on what it is and is not evidence of: a
# fake that agrees with itself proves the backend's logic is self-consistent
# and nothing whatever about `msvcrt`. The `full-windows` CI job is what
# checks the model against the machine.

#: The byte range the Windows backend must lock, written out as literals here
#: rather than imported from the module under test, so that moving the
#: sentinel has to be a deliberate two-file change. Far past any plausible end
#: of ``decisions.jsonl``: the range then never moves as the file grows, and
#: because it covers no data, Windows' *mandatory* locking does not shut
#: ordinary readers out of bytes they are entitled to read.
SENTINEL_REGION = (0x7FFF_FFFF, 1)

#: The Windows backend has a deadline where POSIX has none, so the shared
#: conformance suite gives it one comfortably longer than any wait it stages.
CONFORMANCE_TIMEOUT_SECONDS = 5.0

#: How long a blocked probe is watched before concluding it really is blocked.
#: A false *positive* here is impossible -- the lock is held for the whole
#: window -- so this window trades only runtime, never reliability.
BLOCKED_WINDOW_SECONDS = 0.2

#: How long a probe may take to be granted a lock that is now free. Generous
#: on purpose: the failure this guards against is a lock never released, not
#: a slow scheduler.
GRANT_WINDOW_SECONDS = 5.0
PROBE_JOIN_SECONDS = 10.0


def fake_windows_backend(**kwargs: object) -> _WindowsLockBackend:
    """A real :class:`_WindowsLockBackend` over an injected fake ``msvcrt``.

    Args:
        **kwargs: Overrides forwarded to the backend's constructor.

    Returns:
        The backend, with a seeded jitter source (§3.7.3 rule 3: randomness
        in a test is seeded, never ambient) and a deadline long enough for
        the conformance suite's staged contention.
    """
    settings: dict[str, object] = {
        "timeout": CONFORMANCE_TIMEOUT_SECONDS,
        "jitter": random.Random(0).random,
    }
    settings.update(kwargs)
    return _WindowsLockBackend(
        _ByteRangeLocking.from_module(fake_msvcrt(FakeWindowsLocking())),
        **settings,  # type: ignore[arg-type]
    )


#: The backends the contract below is asserted against, by name. A dict rather
#: than an ``if`` in the fixture, per §3.7.3 rule 9.
LOCK_BACKENDS: dict[str, Callable[[], _LockBackend]] = {
    "posix": _PosixLockBackend,
    "windows": fake_windows_backend,
}


@pytest.fixture(
    params=[
        pytest.param(
            "posix",
            marks=pytest.mark.skipif(
                sys.platform == "win32", reason="fcntl does not exist on Windows"
            ),
        ),
        pytest.param("windows"),
    ]
)
def lock_backend(request: pytest.FixtureRequest) -> _LockBackend:
    """Each lock backend in turn. The Windows one runs on every platform."""
    name: str = request.param
    return LOCK_BACKENDS[name]()


@pytest.fixture
def lock_path(tmp_path: Path) -> Path:
    """A file to lock, named as the real one is."""
    path = tmp_path / "decisions.jsonl"
    path.write_bytes(b"")
    return path


class LockProbe:
    """A second handle taking the lock, in a thread, so blocking is observable.

    Both backends make the caller wait when the lock is unavailable -- POSIX
    inside ``flock``, Windows inside its retry loop -- so "is this file
    locked against another handle?" cannot be answered from the thread
    holding the lock. The probe answers it by attempting the acquisition
    elsewhere and reporting whether it completed within a window.

    A second *thread* rather than a second *process* is enough because both
    backends lock a file, not a Python object: POSIX ``flock`` conflicts
    across independent ``open`` calls in one process exactly as it does
    across two, and the injected ``msvcrt`` stand-in models ownership per
    descriptor for the same reason.

    Args:
        backend: The backend under test.
        path: The file to lock.
        kind: The lock to ask for.
    """

    def __init__(self, backend: _LockBackend, path: Path, kind: LockKind) -> None:
        self._backend = backend
        self._path = path
        self._kind = kind
        self._fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        self._acquired = threading.Event()
        self._may_release = threading.Event()
        self.failure: Exception | None = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._backend.acquire(self._fd, self._kind, self._path)
        except (LogError, OSError) as exc:
            # Reported to the test thread rather than raised into a worker
            # nobody is joining: a probe that gave up is a legitimate outcome
            # (the Windows backend has a deadline), and a probe that failed
            # for any other reason must not surface as a silent non-acquire.
            self.failure = exc
            return
        self._acquired.set()
        self._may_release.wait(PROBE_JOIN_SECONDS)
        self._backend.release(self._fd)

    def acquired_within(self, seconds: float) -> bool:
        """Whether the lock was granted within ``seconds``.

        Args:
            seconds: How long to wait.

        Returns:
            ``True`` once the probe holds the lock, ``False`` if it is still
            waiting (or has already given up) when the window closes.
        """
        return self._acquired.wait(seconds)

    def close(self) -> None:
        """Let the probe release, join it, and close its descriptor."""
        self._may_release.set()
        self._thread.join(timeout=PROBE_JOIN_SECONDS)
        # Only if the thread really finished: closing a descriptor another
        # thread is still blocked on inside flock() is how a failing test
        # turns into a hanging one.
        if not self._thread.is_alive():
            os.close(self._fd)


@pytest.fixture
def probe_lock(lock_path: Path) -> Iterator[Callable[[_LockBackend, LockKind], LockProbe]]:
    """Start :class:`LockProbe`s and guarantee they are joined afterwards."""
    started: list[LockProbe] = []

    def start(backend: _LockBackend, kind: LockKind) -> LockProbe:
        probe = LockProbe(backend, lock_path, kind)
        started.append(probe)
        return probe

    yield start
    for probe in started:
        probe.close()


@contextlib.contextmanager
def holding(backend: _LockBackend, path: Path, kind: LockKind) -> Iterator[int]:
    """Hold a ``kind`` lock on ``path`` through one descriptor.

    Args:
        backend: The backend to lock with.
        path: The file to lock.
        kind: The lock to take.

    Yields:
        The locked descriptor.
    """
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    backend.acquire(fd, kind, path)
    try:
        yield fd
    finally:
        backend.release(fd)
        os.close(fd)


@pytest.mark.integration
def test_lock_backend__exclusive__excludes_another_exclusive(
    lock_backend: _LockBackend,
    lock_path: Path,
    probe_lock: Callable[[_LockBackend, LockKind], LockProbe],
) -> None:
    with holding(lock_backend, lock_path, "exclusive"):
        probe = probe_lock(lock_backend, "exclusive")
        blocked_while_held = probe.acquired_within(BLOCKED_WINDOW_SECONDS)

    granted_after_release = probe.acquired_within(GRANT_WINDOW_SECONDS)

    assert (blocked_while_held, granted_after_release) == (False, True)


@pytest.mark.integration
def test_lock_backend__shared__excludes_an_exclusive(
    lock_backend: _LockBackend,
    lock_path: Path,
    probe_lock: Callable[[_LockBackend, LockKind], LockProbe],
) -> None:
    with holding(lock_backend, lock_path, "shared"):
        probe = probe_lock(lock_backend, "exclusive")
        blocked_while_held = probe.acquired_within(BLOCKED_WINDOW_SECONDS)

    granted_after_release = probe.acquired_within(GRANT_WINDOW_SECONDS)

    assert (blocked_while_held, granted_after_release) == (False, True)


@pytest.mark.integration
def test_lock_backend__release__leaves_the_file_unlocked(
    lock_backend: _LockBackend,
    lock_path: Path,
    probe_lock: Callable[[_LockBackend, LockKind], LockProbe],
) -> None:
    with holding(lock_backend, lock_path, "exclusive"):
        pass

    probe = probe_lock(lock_backend, "exclusive")

    assert probe.acquired_within(GRANT_WINDOW_SECONDS)


@pytest.mark.integration
def test_lock_backend__lock_and_unlock__restore_the_file_position(
    lock_backend: _LockBackend, lock_path: Path
) -> None:
    """Locking must not move the descriptor the caller is about to read.

    ``msvcrt.locking`` operates at, and advances past, the current file
    position; ``flock`` does not touch it. ``DecisionLog`` seeks
    deliberately -- to the start to verify, to the end to append -- so a
    backend that left the position wherever locking put it would append a
    decision into the middle of the log.
    """
    lock_path.write_bytes(b'{"event_id":"id-000000"}\n')
    fd = os.open(lock_path, os.O_RDWR)
    try:
        os.lseek(fd, 7, os.SEEK_SET)
        lock_backend.acquire(fd, "exclusive", lock_path)
        after_acquire = os.lseek(fd, 0, os.SEEK_CUR)
        lock_backend.release(fd)
        after_release = os.lseek(fd, 0, os.SEEK_CUR)
    finally:
        os.close(fd)

    assert (after_acquire, after_release) == (7, 7)


@pytest.mark.integration
def test_lock_backend__relocking_the_same_descriptor__raises_and_leaves_one_lock(
    lock_backend: _LockBackend,
    lock_path: Path,
    probe_lock: Callable[[_LockBackend, LockKind], LockProbe],
) -> None:
    """A second lock on a held descriptor is a bug, and must read as one.

    POSIX would silently re-apply it and Windows would fail on the
    already-locked region; both backends refuse instead, so the mistake
    cannot be a Windows-only discovery. The probe afterwards is the other
    half of the contract: the refused attempt must leave *no* extra lock
    behind, so one release is still enough to free the file.
    """
    with (
        holding(lock_backend, lock_path, "exclusive") as fd,
        pytest.raises(LogError, match="not re-entrant"),
    ):
        lock_backend.acquire(fd, "exclusive", lock_path)

    probe = probe_lock(lock_backend, "exclusive")

    assert probe.acquired_within(GRANT_WINDOW_SECONDS)


# ---------------------------------------------------------------------------
# Where the two backends deliberately differ (ADR 0010)
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.skipif(sys.platform == "win32", reason="fcntl does not exist on Windows")
def test_posix_lock_backend__shared__admits_a_second_shared_reader(
    lock_path: Path,
    probe_lock: Callable[[_LockBackend, LockKind], LockProbe],
) -> None:
    """POSIX gives real shared locks: two readers, no waiting."""
    backend = _PosixLockBackend()

    with holding(backend, lock_path, "shared"):
        probe = probe_lock(backend, "shared")

        assert probe.acquired_within(GRANT_WINDOW_SECONDS)


@pytest.mark.integration
def test_windows_lock_backend__shared__is_degraded_to_an_exclusive_lock(
    lock_path: Path,
    probe_lock: Callable[[_LockBackend, LockKind], LockProbe],
) -> None:
    """The one named deviation: Windows has no shared mode, so readers serialise.

    ``msvcrt`` documents ``LK_RLCK`` as identical to ``LK_LOCK`` -- there is
    no read lock to take. Asking for ``"shared"`` therefore yields an
    exclusive lock: never weaker than what was asked for, only less
    concurrent, which costs two simultaneous readers a wait and costs
    correctness nothing. This test exists so that the deviation is pinned
    rather than merely written down; if a future backend quietly gained a
    real shared mode, ADR 0010 would need rewriting and this test would say
    so.
    """
    backend = fake_windows_backend()

    with holding(backend, lock_path, "shared"):
        probe = probe_lock(backend, "shared")
        blocked_while_held = probe.acquired_within(BLOCKED_WINDOW_SECONDS)

    granted_after_release = probe.acquired_within(GRANT_WINDOW_SECONDS)

    assert (blocked_while_held, granted_after_release) == (False, True)


# ---------------------------------------------------------------------------
# The Windows retry loop, on a clock that only moves when something sleeps
# ---------------------------------------------------------------------------


class SteerableClock:
    """A monotonic clock that advances by exactly what is slept on it.

    The Windows backend's deadline is the only place in this module that
    depends on time passing, and a real ``time.sleep`` would make the
    retry-count assertions below both slow and machine-dependent (§3.7.3
    rule 3). Injected the same way ``sources/ratelimit.py`` injects its
    clock and sleep.

    Attributes:
        now: The current reading, in seconds.
        slept: Every duration slept, in order.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def monotonic(self) -> float:
        """The current reading."""
        return self.now

    def sleep(self, seconds: float) -> None:
        """Record ``seconds`` and advance :attr:`now` by it."""
        self.slept.append(seconds)
        self.now += seconds


def open_lock_file(path: Path) -> int:
    """Open ``path`` read/write, creating it, and return the descriptor."""
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o644)


@pytest.mark.integration
def test_windows_lock_backend__uncontended__locks_the_sentinel_range_without_waiting(
    lock_path: Path,
) -> None:
    """An uncontended lock takes one call, no sleep, on the sentinel range.

    The range is asserted, not just the outcome: it is what keeps the lock
    stable as the file grows, and what keeps Windows' mandatory locking off
    the bytes an outside reader is entitled to read.
    """
    clock = SteerableClock()
    locking = FakeWindowsLocking()
    backend = _WindowsLockBackend(
        _ByteRangeLocking.from_module(fake_msvcrt(locking)),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
        jitter=lambda: 0.0,
    )
    fd = open_lock_file(lock_path)

    try:
        backend.acquire(fd, "exclusive", lock_path)
    finally:
        os.close(fd)

    assert (clock.slept, locking.regions) == ([], [SENTINEL_REGION])
    assert locking.owner_of(SENTINEL_REGION) == fd


@pytest.mark.integration
def test_windows_lock_backend__contention_that_clears__acquires_after_retrying(
    lock_path: Path,
) -> None:
    """Contention is retried, with a growing wait, until the holder lets go."""
    retries_before_release = 3
    clock = SteerableClock()
    locking = FakeWindowsLocking()
    holder_fd = open_lock_file(lock_path)
    waiter_fd = open_lock_file(lock_path)

    def sleep_then_release_on_the_last_retry(seconds: float) -> None:
        clock.sleep(seconds)
        if len(clock.slept) == retries_before_release:
            backend.release(holder_fd)

    backend = _WindowsLockBackend(
        _ByteRangeLocking.from_module(fake_msvcrt(locking)),
        sleep=sleep_then_release_on_the_last_retry,
        monotonic=clock.monotonic,
        jitter=lambda: 0.0,
    )
    try:
        backend.acquire(holder_fd, "exclusive", lock_path)
        backend.acquire(waiter_fd, "exclusive", lock_path)
    finally:
        os.close(holder_fd)
        os.close(waiter_fd)

    assert clock.slept == [0.005, 0.01, 0.02]
    assert locking.owner_of(SENTINEL_REGION) == waiter_fd


@pytest.mark.integration
def test_windows_lock_backend__contention_that_never_clears__raises_naming_file_and_wait(
    lock_path: Path,
) -> None:
    """The deadline is explicit, and so is the message when it expires.

    ``msvcrt``'s own blocking mode would give up after ten one-second
    retries and raise a bare ``OSError``; a reviewer would see a permission
    error about a file they own. What they get instead names the log, says
    how long it waited, names the likely cause -- a second kernel or CLI
    run holding the same project -- and states that nothing was written.
    """
    timeout_seconds = 0.5
    clock = SteerableClock()
    locking = FakeWindowsLocking()

    def sleep_but_never_forever(seconds: float) -> None:
        # A deadline that never expires is an infinite retry loop, and a test
        # that hangs says less than a test that fails: CI would report a
        # six-hour job cancelled, not a broken deadline. This bound is far
        # above the eight retries the schedule below actually takes.
        clock.sleep(seconds)
        assert len(clock.slept) <= 20, "the acquire deadline never expired"

    backend = _WindowsLockBackend(
        _ByteRangeLocking.from_module(fake_msvcrt(locking)),
        timeout=timeout_seconds,
        sleep=sleep_but_never_forever,
        monotonic=clock.monotonic,
        jitter=lambda: 0.0,
    )
    holder_fd = open_lock_file(lock_path)
    waiter_fd = open_lock_file(lock_path)
    backend.acquire(holder_fd, "exclusive", lock_path)
    os.lseek(waiter_fd, 12, os.SEEK_SET)

    try:
        with pytest.raises(LogError) as excinfo:
            backend.acquire(waiter_fd, "shared", lock_path)
        position_after_failure = os.lseek(waiter_fd, 0, os.SEEK_CUR)
    finally:
        os.close(holder_fd)
        os.close(waiter_fd)

    message = str(excinfo.value)
    assert (str(lock_path) in message, "0.5s" in message, "shared" in message) == (True, True, True)
    assert "No decision has been written" in message
    # It waited the whole deadline before giving up, rather than rounding it
    # away on the last retry.
    assert clock.now >= timeout_seconds
    # The failed attempt left nothing behind: the original holder still owns
    # the range, and the waiter's descriptor is where its caller left it.
    assert (locking.owner_of(SENTINEL_REGION), position_after_failure) == (holder_fd, 12)


@pytest.mark.integration
def test_windows_lock_backend__error_that_is_not_contention__is_raised_immediately(
    lock_path: Path,
) -> None:
    """Only ``EACCES`` means "someone else has it"; everything else is a bug.

    Retrying a bad descriptor for ten seconds and then reporting it as a
    busy file would turn a programming error into a plausible-looking
    operational one.
    """
    clock = SteerableClock()

    def refuse(fd: int, mode: int, nbytes: int, /) -> None:
        raise OSError(errno.EBADF, "Bad file descriptor")

    backend = _WindowsLockBackend(
        _ByteRangeLocking(
            locking=refuse,
            nonblocking_exclusive=FakeWindowsLocking.LK_NBLCK,
            unlock=FakeWindowsLocking.LK_UNLCK,
        ),
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    fd = open_lock_file(lock_path)

    try:
        with pytest.raises(OSError) as excinfo:
            backend.acquire(fd, "exclusive", lock_path)
    finally:
        os.close(fd)

    assert (excinfo.value.errno, clock.slept) == (errno.EBADF, [])


# ---------------------------------------------------------------------------
# DecisionLog's own use of the lock
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_log__nested_locked_sections__raise_instead_of_deadlocking(project: Project) -> None:
    """Nesting the log's critical sections must fail loudly, not hang.

    Each ``_locked`` call opens a *new* descriptor, so a nested one asks the
    OS for a second, conflicting lock on the same file from the same thread:
    on POSIX that blocks forever -- a screening session that stops
    responding with no error and no traceback -- and on Windows it fails on
    the already-locked region. Nothing nests today; this keeps that true and
    makes the failure mode the same on both platforms.
    """
    log = open_log(project)
    outcome: list[str] = []

    def take_the_lock_again() -> None:
        try:
            with log._locked("shared"):
                outcome.append("acquired")
        except LogError as exc:
            outcome.append(str(exc))

    with contextlib.ExitStack() as stack:
        stack.enter_context(log._locked("exclusive"))
        # In a thread, and joined with a deadline, only so that the *absence*
        # of the guard is a failure rather than a hung run: without it this
        # second lock blocks inside flock(2) with nothing to interrupt it, and
        # a suite that never finishes reports less than one that goes red.
        nested = threading.Thread(target=take_the_lock_again, daemon=True)
        nested.start()
        nested.join(timeout=GRANT_WINDOW_SECONDS)
        refused_promptly = not nested.is_alive()

    assert refused_promptly, "nesting the decision log's lock blocked instead of raising"
    assert "not re-entrant" in outcome[0]
    assert log.load() == []
