"""Integration tests for ``store/load.py`` (BUILD_PLAN Stage 3 Tests table, lines 911-924).

Real filesystem, real DuckDB, no network (§3.7.2) -- every test here calls
:func:`prismabib.store.load.build_store` against either a temp copy of the
frozen reference fixture (:func:`tests.store_helpers.copy_reference_project`)
or a small hand-written Layer 0 run
(:func:`tests.store_helpers.write_sealed_run`), never against
``tests/fixtures/projects/reference/`` itself -- that would write
``store/corpus.duckdb`` into a checked-in directory.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import polars as pl
import pytest
from structlog.testing import capture_logs

from prismabib.errors import StoreError
from prismabib.models import PayloadRef
from prismabib.prisma import engine
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.checksums import TABLE_NAMES, table_checksums
from prismabib.store.db import connect
from prismabib.store.load import Corpus, build_store
from tests.store_helpers import copy_reference_project, make_entry, write_sealed_run

_TRAILING_DIGITS_RE = re.compile(r"(\d+)$")


def _trailing_digits(value: str) -> str:
    """Extract the trailing digit run of ``value`` (helper, not a test)."""
    match = _TRAILING_DIGITS_RE.search(value)
    return match.group(1) if match else value


# BUILD_PLAN schema (lines 847-879), transcribed as (column, sql_type, is_primary_key)
# per table -- an independent expectation, not derived from schema.sql itself, so a
# drift in the file is what this test is meant to catch.
_EXPECTED_COLUMNS: dict[str, tuple[tuple[str, str, bool], ...]] = {
    "runs": (
        ("run_id", "VARCHAR", True),
        ("started_at", "TIMESTAMP", False),
        ("query", "VARCHAR", False),
        ("view", "VARCHAR", False),
        ("total_results", "INTEGER", False),
        ("payload_sha256", "VARCHAR", False),
        ("criteria_version", "VARCHAR", False),
    ),
    "records": (
        ("record_id", "VARCHAR", True),
        ("run_id", "VARCHAR", False),
        ("doi", "VARCHAR", False),
        ("title", "VARCHAR", False),
        ("abstract", "VARCHAR", False),
        ("year", "INTEGER", False),
        ("cover_date", "DATE", False),
        ("doc_type", "VARCHAR", False),
        ("language", "VARCHAR", False),
        ("venue_id", "VARCHAR", False),
        ("open_access", "BOOLEAN", False),
        ("payload_file", "VARCHAR", False),
        ("payload_line", "INTEGER", False),
    ),
    "venues": (
        ("venue_id", "VARCHAR", True),
        ("name", "VARCHAR", False),
        ("issn", "VARCHAR", False),
        ("eissn", "VARCHAR", False),
        ("venue_type", "VARCHAR", False),
        ("abbreviation", "VARCHAR", False),
    ),
    "authors": (
        ("author_id", "VARCHAR", True),
        ("surname", "VARCHAR", False),
        ("given_name", "VARCHAR", False),
    ),
    "record_authors": (
        ("record_id", "VARCHAR", False),
        ("author_id", "VARCHAR", False),
        ("position", "INTEGER", False),
    ),
    "affiliations": (
        ("afid", "VARCHAR", True),
        ("name", "VARCHAR", False),
        ("city", "VARCHAR", False),
        ("country_iso3", "VARCHAR", False),
    ),
    "record_affiliations": (
        ("record_id", "VARCHAR", False),
        ("afid", "VARCHAR", False),
    ),
    "keywords": (
        ("keyword_id", "VARCHAR", True),
        ("term_raw", "VARCHAR", False),
        ("term_norm", "VARCHAR", False),
    ),
    "record_keywords": (
        ("record_id", "VARCHAR", False),
        ("keyword_id", "VARCHAR", False),
        ("kind", "VARCHAR", False),
    ),
    "subject_areas": (
        ("record_id", "VARCHAR", False),
        ("area_code", "VARCHAR", False),
    ),
    "citation_snapshots": (
        ("record_id", "VARCHAR", True),
        ("retrieved_at", "TIMESTAMP", True),
        ("cited_by_count", "INTEGER", False),
    ),
}


@pytest.mark.integration
@pytest.mark.acceptance("S03-AC4")
def test_build_store__run_twice__is_idempotent(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)

    first = build_store(project, rebuild=True)
    second = build_store(project, rebuild=False)

    connection = connect(project, read_only=True)
    try:
        (total_rows,) = connection.execute("SELECT COUNT(*) FROM records").fetchone()
        (distinct_rows,) = connection.execute(
            "SELECT COUNT(DISTINCT record_id) FROM records"
        ).fetchone()
    finally:
        connection.close()

    assert second.rebuilt is False
    assert (second.records_loaded, second.duplicate_records) == (
        first.records_loaded,
        first.duplicate_records,
    )
    assert total_rows == distinct_rows


@pytest.mark.integration
@pytest.mark.acceptance("S03-AC3")
def test_build_store__after_deleting_db__reproduces_identical_checksums(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    connection = connect(project, read_only=True)
    before = table_checksums(connection)
    connection.close()

    project.db_path.unlink()
    build_store(project)

    connection = connect(project, read_only=True)
    after = table_checksums(connection)
    connection.close()

    assert after == before


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "provoked by making the directory unwritable, which is a POSIX mechanism -- "
        "chmod does not deny a Windows owner. Windows reaches the same StoreError by "
        "its own ordinary route, an open connection, and that path has no test yet"
    ),
)
@pytest.mark.skipif(
    # `os.getuid` does not exist on Windows and this is evaluated at collection
    # time, so it must not be reached there -- a bare `os.getuid()` here raised
    # AttributeError during collection and took down the whole Windows job,
    # including every test that had nothing to do with permissions.
    getattr(os, "getuid", lambda: -1)() == 0,
    reason="root ignores directory permissions",
)
def test_build_store__undeletable_store__raises_store_error_naming_the_cause(
    tmp_path: Path,
) -> None:
    """A rebuild that cannot delete the old store must say why, not raise OSError.

    On POSIX this is nearly unreachable -- ``unlink`` succeeds on an open
    file -- so it is provoked here by making the directory unwritable. On
    Windows it is the *ordinary* outcome of an ordinary mistake: the file
    cannot be deleted while any connection to it is open, so a researcher
    who calls ``build_store(project, rebuild=True)`` in a notebook that
    still holds a ``Corpus`` gets a bare ``PermissionError`` naming a file
    they own and no hint that the process refusing them is their own.
    """
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    store_dir = project.db_path.parent
    store_dir.chmod(0o500)

    try:
        with pytest.raises(StoreError) as excinfo:
            build_store(project, rebuild=True)
    finally:
        store_dir.chmod(0o700)

    message = str(excinfo.value)
    assert str(project.db_path) in message
    assert "while any connection to it is open" in message
    assert "Nothing has been changed" in message


@pytest.mark.integration
@pytest.mark.acceptance("S03-AC2")
def test_build_store__every_record__payload_ref_resolves(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        rows = connection.execute(
            "SELECT record_id, payload_file, payload_line FROM records ORDER BY record_id"
        ).fetchall()
    finally:
        connection.close()

    record_ids = [_trailing_digits(record_id) for record_id, _, _ in rows]
    resolved_ids = [
        _trailing_digits(
            json.loads(
                PayloadRef(path=project.raw_dir / payload_file, line=payload_line).resolve()
            )["dc:identifier"]
        )
        for _, payload_file, payload_line in rows
    ]

    assert rows
    assert resolved_ids == record_ids


@pytest.mark.integration
def test_country__unmapped_string__emits_warning_and_is_counted_as_unknown(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)

    with capture_logs() as logs:
        stats = build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        (records_with_geography,) = connection.execute(
            "SELECT COUNT(DISTINCT record_id) FROM record_affiliations"
        ).fetchone()
    finally:
        connection.close()

    unmapped_warnings = [
        entry for entry in logs if entry.get("event") == "store.load.unmapped_countries"
    ]

    assert stats.unmapped_country_values == ("Korea",)
    assert unmapped_warnings == [
        {"event": "store.load.unmapped_countries", "log_level": "warning", "values": ("Korea",)}
    ]
    assert records_with_geography == stats.records_loaded


@pytest.mark.integration
def test_citations__two_snapshots__both_retained(tmp_path: Path) -> None:
    project = Project.init("citations-demo", title="Citations Demo", root=tmp_path)
    record_id = "scopus:2-s2.0-800000000001"

    write_sealed_run(
        project.raw_dir,
        "20250101T000000Z-aaaaaaaa",
        [make_entry(eid="2-s2.0-800000000001", citedby_count=10)],
        started_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    write_sealed_run(
        project.raw_dir,
        "20250601T000000Z-bbbbbbbb",
        [make_entry(eid="2-s2.0-800000000001", citedby_count=25)],
        started_at=datetime(2025, 6, 1, tzinfo=UTC),
    )

    build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        rows = connection.execute(
            "SELECT retrieved_at, cited_by_count FROM citation_snapshots "
            "WHERE record_id = ? ORDER BY retrieved_at",
            [record_id],
        ).fetchall()
    finally:
        connection.close()

    assert [cited_by_count for _, cited_by_count in rows] == [10, 25]
    assert rows[0][0] != rows[1][0]


@pytest.mark.integration
def test_corpus__records_by_stage__delegates_to_prisma_engine(tmp_path: Path) -> None:
    # Stage 4 landed, so the strict xfail this test carried is gone -- and so is
    # its original assertion, `included.height <= raw_count`, which a
    # `records(INCLUDED)` that always returned zero rows would have satisfied
    # (§5 risk 12). What is asserted instead is identity with the engine's own
    # answer, over a decision log this test wrote itself, so the frame is
    # pinned to a *known, non-empty* set of records rather than to a bound.
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    log = DecisionLog(project)
    eligible = sorted(engine.language_set(project))[:6]
    for record_id in eligible:
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record_id,
            reviewer="kp",
            decision="include",
        )
    for record_id in eligible[:4]:
        log.append(
            stage=PrismaStage.FULLTEXT, record_id=record_id, reviewer="kp", decision="include"
        )
    corpus = Corpus.open(project)
    raw_count = corpus.records(stage=PrismaStage.RAW).height

    included = corpus.records(stage=PrismaStage.INCLUDED)

    assert set(included["record_id"].to_list()) == engine.corpus(project) == set(eligible[:4])
    assert included.height == 4
    assert 4 < raw_count == 120


@pytest.mark.integration
@pytest.mark.parametrize("read_only", [True, False])
def test_corpus__non_raw_stage__answers_the_same_on_a_writable_and_a_read_only_handle(
    tmp_path: Path, read_only: bool
) -> None:
    # `read_only` is part of `Corpus.open`'s frozen signature, so
    # `Corpus.open(project, read_only=False).records(stage=INCLUDED)` is a
    # reachable public path -- and it used to be a hard crash: the PRISMA
    # engine opened a *second*, read-only connection to the file this Corpus
    # already held writable, and DuckDB refuses two connections to one
    # database file whose configurations disagree ("Can't open a connection
    # to same database file with a different configuration than existing
    # connections"). The engine now borrows the Corpus's own connection, so
    # the flag no longer decides whether a stage can be resolved at all.
    #
    # Exactly one Corpus is opened per parametrised case, and every engine
    # call is made before it. Two open handles on one file in one test would
    # reproduce that same collision from the test's own side and read as the
    # bug under test.
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    log = DecisionLog(project)
    eligible = sorted(engine.language_set(project))[:5]
    for record_id in eligible:
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record_id,
            reviewer="kp",
            decision="include",
        )
    for record_id in eligible[:3]:
        log.append(
            stage=PrismaStage.FULLTEXT, record_id=record_id, reviewer="kp", decision="include"
        )
    expected_included = eligible[:3]
    corpus = Corpus.open(project, read_only=read_only)

    included = corpus.records(stage=PrismaStage.INCLUDED)
    language = corpus.records(stage=PrismaStage.LANGUAGE)

    assert included["record_id"].to_list() == expected_included
    # LANGUAGE is checked alongside INCLUDED because the two take different
    # routes through the borrowed connection: `L` is Layer 1 and
    # `criteria.yaml` only, while `INCLUDED` also folds the decision log.
    # `copy_reference_project` keeps `Project.init`'s permissive default
    # criteria (every list empty, 1900-2026), so `L` here is every record.
    assert language.height == 120
    assert set(expected_included) < set(language["record_id"].to_list())


@pytest.mark.integration
def test_schema__sql_file__matches_live_duckdb_introspection(tmp_path: Path) -> None:
    project = Project.init("schema-check", title="Schema Check", root=tmp_path)
    build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        live_tables = {
            row[0]
            for row in connection.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
            ).fetchall()
        }
        live_columns = {
            table: tuple(
                (name, sql_type, bool(pk))
                for _, name, sql_type, _, _, pk in connection.execute(
                    f"PRAGMA table_info('{table}')"
                ).fetchall()
            )
            for table in TABLE_NAMES
        }
    finally:
        connection.close()

    assert live_tables == set(TABLE_NAMES)
    assert live_columns == _EXPECTED_COLUMNS


@pytest.mark.integration
def test_load_run__malformed_lines_in_page_file__skipped_without_error(tmp_path: Path) -> None:
    project = Project.init("malformed-lines", title="Malformed Lines", root=tmp_path)
    good_entry = make_entry(eid="2-s2.0-800000000101", title="Kept Record")
    run_dir = write_sealed_run(
        project.raw_dir,
        "20250101T000000Z-aaaaaaaa",
        [good_entry],
        started_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    page_path = run_dir / "page-0000.jsonl"
    non_dict_line = json.dumps([1, 2, 3])
    good_line = json.dumps(good_entry, sort_keys=True, separators=(",", ":"))
    page_path.write_text(f"\n{non_dict_line}\n{good_line}\n", encoding="utf-8")

    stats = build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        (title,) = connection.execute("SELECT title FROM records").fetchone()
    finally:
        connection.close()

    assert stats.records_loaded == 1
    assert title == "Kept Record"


@pytest.mark.integration
def test_load_run__scopus_empty_result_placeholder__skipped(tmp_path: Path) -> None:
    project = Project.init("empty-placeholder", title="Empty Placeholder", root=tmp_path)
    good_entry = make_entry(eid="2-s2.0-800000000102")
    write_sealed_run(
        project.raw_dir,
        "20250101T000000Z-bbbbbbbb",
        [{"error": "Result set was empty"}, good_entry],
        started_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    stats = build_store(project, rebuild=True)

    assert stats.records_loaded == 1


@pytest.mark.integration
def test_load_run__entry_missing_eid__warns_and_is_skipped(tmp_path: Path) -> None:
    project = Project.init("missing-eid", title="Missing EID", root=tmp_path)
    bad_entry = make_entry(eid="2-s2.0-800000000103")
    del bad_entry["eid"]
    good_entry = make_entry(eid="2-s2.0-800000000104")
    run_id = "20250101T000000Z-cccccccc"
    write_sealed_run(
        project.raw_dir,
        run_id,
        [bad_entry, good_entry],
        started_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    with capture_logs() as logs:
        stats = build_store(project, rebuild=True)

    warnings = [entry for entry in logs if entry.get("event") == "store.load.entry_missing_eid"]

    assert stats.records_loaded == 1
    assert warnings == [
        {
            "event": "store.load.entry_missing_eid",
            "log_level": "warning",
            "run_id": run_id,
            "payload_file": "page-0000.jsonl",
            "line": 0,
        }
    ]


@pytest.mark.integration
def test_load_run__entry_without_citedby_count__creates_record_with_no_citation_snapshot(
    tmp_path: Path,
) -> None:
    project = Project.init("no-citedby", title="No Citedby", root=tmp_path)
    record_id = "scopus:2-s2.0-800000000105"
    entry = make_entry(eid="2-s2.0-800000000105")
    del entry["citedby-count"]
    write_sealed_run(
        project.raw_dir,
        "20250101T000000Z-dddddddd",
        [entry],
        started_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        (record_count,) = connection.execute(
            "SELECT COUNT(*) FROM records WHERE record_id = ?", [record_id]
        ).fetchone()
        (snapshot_count,) = connection.execute(
            "SELECT COUNT(*) FROM citation_snapshots WHERE record_id = ?", [record_id]
        ).fetchone()
    finally:
        connection.close()

    assert record_count == 1
    assert snapshot_count == 0


@pytest.mark.integration
def test_load_run__author_without_authid__record_loads_with_no_author_row(tmp_path: Path) -> None:
    project = Project.init("author-no-id", title="Author No Id", root=tmp_path)
    record_id = "scopus:2-s2.0-800000000106"
    entry = make_entry(
        eid="2-s2.0-800000000106", author=[{"surname": "NoId", "given-name": "Test"}]
    )
    write_sealed_run(
        project.raw_dir,
        "20250101T000000Z-eeeeeeee",
        [entry],
        started_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        (record_count,) = connection.execute(
            "SELECT COUNT(*) FROM records WHERE record_id = ?", [record_id]
        ).fetchone()
        (author_link_count,) = connection.execute(
            "SELECT COUNT(*) FROM record_authors WHERE record_id = ?", [record_id]
        ).fetchone()
    finally:
        connection.close()

    assert record_count == 1
    assert author_link_count == 0


@pytest.mark.integration
def test_load_run__affiliation_without_afid__record_loads_with_no_affiliation_row(
    tmp_path: Path,
) -> None:
    project = Project.init("affiliation-no-id", title="Affiliation No Id", root=tmp_path)
    record_id = "scopus:2-s2.0-800000000107"
    entry = make_entry(
        eid="2-s2.0-800000000107",
        affiliation=[
            {
                "affilname": "No Afid University",
                "affiliation-city": "Nowhere",
                "affiliation-country": "USA",
            }
        ],
    )
    write_sealed_run(
        project.raw_dir,
        "20250101T000000Z-ffffffff",
        [entry],
        started_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        (record_count,) = connection.execute(
            "SELECT COUNT(*) FROM records WHERE record_id = ?", [record_id]
        ).fetchone()
        (affiliation_link_count,) = connection.execute(
            "SELECT COUNT(*) FROM record_affiliations WHERE record_id = ?", [record_id]
        ).fetchone()
    finally:
        connection.close()

    assert record_count == 1
    assert affiliation_link_count == 0


@pytest.mark.integration
def test_load_run__entry_with_subject_areas__loads_subject_area_rows(tmp_path: Path) -> None:
    project = Project.init("subject-areas", title="Subject Areas", root=tmp_path)
    record_id = "scopus:2-s2.0-800000000108"
    entry = make_entry(eid="2-s2.0-800000000108")
    entry["subject-area"] = [{"@code": "1000"}, {"$": "2000"}]
    write_sealed_run(
        project.raw_dir,
        "20250101T000000Z-gggggggg",
        [entry],
        started_at=datetime(2025, 1, 1, tzinfo=UTC),
    )

    stats = build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        codes = {
            row[0]
            for row in connection.execute(
                "SELECT area_code FROM subject_areas WHERE record_id = ?", [record_id]
            ).fetchall()
        }
    finally:
        connection.close()

    assert stats.subject_area_links_loaded == 2
    assert codes == {"1000", "2000"}


@pytest.mark.integration
def test_build_store__reused_store_with_wrong_schema__raises_store_error(tmp_path: Path) -> None:
    project = Project.init("wrong-schema", title="Wrong Schema", root=tmp_path)
    connection = duckdb.connect(str(project.db_path))
    connection.execute("CREATE TABLE not_a_layer1_table (x INTEGER)")
    connection.close()

    with pytest.raises(StoreError, match="does not look like a Layer 1 store"):
        build_store(project, rebuild=False)


@pytest.mark.integration
def test_build_store__rebuild_true_twice__reflects_newly_added_run(tmp_path: Path) -> None:
    project = Project.init("rebuild-twice", title="Rebuild Twice", root=tmp_path)
    write_sealed_run(
        project.raw_dir,
        "20250101T000000Z-hhhhhhhh",
        [make_entry(eid="2-s2.0-800000000109")],
        started_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    first = build_store(project, rebuild=True)

    write_sealed_run(
        project.raw_dir,
        "20250601T000000Z-iiiiiiii",
        [make_entry(eid="2-s2.0-800000000110")],
        started_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    second = build_store(project, rebuild=True)

    assert first.records_loaded == 1
    assert second.records_loaded == 2


@pytest.mark.integration
def test_corpus_keywords__non_raw_stage__is_computed_over_exactly_the_engine_set(
    tmp_path: Path,
) -> None:
    # Renamed from `..._stage_argument__only_raw_is_supported`: since Stage 4
    # landed, a non-RAW stage no longer raises `NotImplementedError`, so the
    # old name described behaviour that no longer exists. The assertion is now
    # the same shape as `records`': the returned occurrences must be exactly
    # the RAW occurrences restricted to the engine's set for that stage.
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    log = DecisionLog(project)
    eligible = sorted(engine.language_set(project))[:5]
    for record_id in eligible:
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record_id,
            reviewer="kp",
            decision="include",
        )
    for record_id in eligible[:3]:
        log.append(
            stage=PrismaStage.FULLTEXT, record_id=record_id, reviewer="kp", decision="include"
        )
    corpus = Corpus.open(project)
    raw_keywords = corpus.keywords(stage=PrismaStage.RAW)

    included_keywords = corpus.keywords(stage=PrismaStage.INCLUDED)

    included_set = engine.corpus(project)
    assert included_set == set(eligible[:3])
    assert included_keywords.equals(
        raw_keywords.filter(pl.col("record_id").is_in(sorted(included_set)))
    )
    assert 0 < included_keywords.height < raw_keywords.height


@pytest.mark.integration
def test_citations__query_with_date__uses_snapshot_at_or_before(tmp_path: Path) -> None:
    project = Project.init("citations-at-date", title="Citations At Date", root=tmp_path)
    record_id = "scopus:2-s2.0-800000000111"
    write_sealed_run(
        project.raw_dir,
        "20250101T000000Z-jjjjjjjj",
        [make_entry(eid="2-s2.0-800000000111", citedby_count=10)],
        started_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    write_sealed_run(
        project.raw_dir,
        "20250601T000000Z-kkkkkkkk",
        [make_entry(eid="2-s2.0-800000000111", citedby_count=25)],
        started_at=datetime(2025, 6, 1, tzinfo=UTC),
    )
    build_store(project, rebuild=True)
    corpus = Corpus.open(project)

    result = corpus.citations(at=datetime(2025, 3, 1, tzinfo=UTC))

    assert result["record_id"].to_list() == [record_id]
    assert result["cited_by_count"].to_list() == [10]


@pytest.mark.integration
@pytest.mark.parametrize(
    "stage",
    [
        pytest.param(PrismaStage.AUTOMATED, id="automated"),
        pytest.param(PrismaStage.LANGUAGE, id="language"),
    ],
)
def test_corpus__layer1_only_stage__ignores_an_unreadable_decision_log(
    tmp_path: Path, stage: PrismaStage
) -> None:
    """``A`` and ``L`` must not depend on Layer 2, even to fail.

    BUILD_PLAN line 950 makes ``AUTOMATED``/``LANGUAGE`` pure functions of
    Layer 1 and ``criteria.yaml`` -- computed, never logged. An
    implementation that answers them from a snapshot which folds the
    decision log gives the right *answer* while acquiring a dependency the
    methodology says they do not have: a reviewer with a corrupt
    ``decisions.jsonl`` would be unable to ask how many records survived the
    automated filter, a number whose value that file cannot influence.
    """
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    project.decisions_path.parent.mkdir(parents=True, exist_ok=True)
    project.decisions_path.write_text("{ not valid json\n", encoding="utf-8")

    result = Corpus.open(project).records(stage=stage)

    assert result.height > 0


@pytest.mark.integration
def test_corpus__layer1_only_stage__does_not_create_a_decision_log(tmp_path: Path) -> None:
    """Asking a Layer 1 question must not manufacture Layer 2 state.

    ``DecisionLog`` opens ``decisions.jsonl`` with ``O_CREAT``, so folding it
    to answer ``LANGUAGE`` would leave a screening log behind for a project
    that has never screened anything -- a file whose presence tells a later
    reader that human screening began.
    """
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    # `copy_reference_project` lays down an empty log, so remove it first --
    # otherwise this test passes on an implementation that does create one,
    # having never established its own precondition.
    project.decisions_path.unlink(missing_ok=True)
    assert not project.decisions_path.exists()

    Corpus.open(project).records(stage=PrismaStage.LANGUAGE)

    assert not project.decisions_path.exists()


@pytest.mark.integration
def test_build_store__one_malformed_entry__is_skipped_and_named_rather_than_aborting(
    tmp_path: Path,
) -> None:
    """One bad entry must not make every other record unloadable.

    This is not hypothetical. The first real capture run against this tool
    returned 1,945 records, of which exactly one arrived without a
    ``dc:title`` -- a field Scopus always sends. ``build_store`` raised, and
    the other 1,944 records became unloadable with no way forward: Layer 0 is
    immutable, and re-capturing means a drifted index, so the corpus that had
    already cost a weekly quota could not be turned into a store at all.

    Skipping silently would be the opposite error -- a smaller corpus that
    looks complete is exactly BUILD_PLAN §1.4's failure mode. So the entry is
    named by payload file and line in ``StoreStats``, logged individually, and
    warned about at the end of the build. The operator's next question is
    always *which record*, and Layer 0 being immutable means the answer has to
    survive in the artefact.
    """
    project = copy_reference_project(tmp_path)
    run_dir = next(d for d in project.raw_dir.iterdir() if (d / "manifest.json").is_file())
    page = run_dir / "page-0000.jsonl"
    lines = page.read_text(encoding="utf-8").splitlines()
    entry = json.loads(lines[2])
    del entry["dc:title"]
    lines[2] = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    page.write_text("\n".join(lines) + "\n", encoding="utf-8")

    stats = build_store(project, rebuild=True)

    assert stats.records_loaded == 119
    assert stats.malformed_entries_skipped == (f"{run_dir.name}/page-0000.jsonl:2",)
