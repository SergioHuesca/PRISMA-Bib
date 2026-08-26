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

import json
import os
import shutil
import threading
from pathlib import Path

import pytest

from prismabib.errors import LogError, ValidationError
from prismabib.prisma import engine
from prismabib.prisma.events import DecisionEvent
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.stage import PrismaStage
from tests.conftest import SeededIdFactory
from tests.prisma_helpers import (
    CorpusSpec,
    CriteriaSpec,
    RecordSpec,
    append_raw_bytes,
    build_project,
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
