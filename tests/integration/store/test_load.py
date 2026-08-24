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
import re
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest
from structlog.testing import capture_logs

from prismabib.errors import StoreError
from prismabib.models import PayloadRef
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
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Needs the Stage 4 PRISMA engine (prismabib.prisma), which does not exist "
        "yet; Corpus.records() raises NotImplementedError for every stage but RAW. "
        "Flip this in the same PR that lands Stage 4 (BUILD_PLAN line 923)."
    ),
)
def test_corpus__records_by_stage__delegates_to_prisma_engine(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    corpus = Corpus.open(project)
    raw_count = corpus.records(stage=PrismaStage.RAW).height

    included = corpus.records(stage=PrismaStage.INCLUDED)

    assert included.height <= raw_count


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
def test_corpus_keywords__stage_argument__only_raw_is_supported(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    corpus = Corpus.open(project)

    raw_keywords = corpus.keywords(stage=PrismaStage.RAW)

    with pytest.raises(NotImplementedError, match="Stage 4 PRISMA engine"):
        corpus.keywords(stage=PrismaStage.INCLUDED)

    assert raw_keywords.height > 0


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
