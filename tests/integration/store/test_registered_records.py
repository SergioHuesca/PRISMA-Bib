"""Layer 1 readers against a registered record (ADR 0032 Decision 1).

The registration fold that fills ``registered_records`` is a later batch, so every
test inserts a row through ``insert_registered_record`` and then reads it back
through the reader under test. Each test is chosen to fail if its reader is still
on ``records``, if ``records_all`` lacks the registered half, or if the table has
no checksum sort key.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from prismabib.bibliometrics.base import build_provenance
from prismabib.errors import StoreError
from prismabib.prisma import engine
from prismabib.project import Project
from prismabib.report.numbers import _corpus_numbers, _venue_numbers
from prismabib.screening import ui
from prismabib.stage import PrismaStage
from prismabib.store.checksums import table_checksum
from prismabib.store.db import connect
from prismabib.store.load import Corpus, _stats_from_connection
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project, write_criteria
from tests.store_helpers import insert_registered_record

REGISTERED = "registered:ev-1"
SCOPUS = RecordSpec(number=1)  # year 2020, journal
EMPTY_SHA256 = (
    "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # pragma: allowlist secret
)
VENUE = "Journal of Registered Things"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    spec = CorpusSpec(records=[SCOPUS, RecordSpec(number=2, year=2019)], criteria=CriteriaSpec())
    return build_project(tmp_path, spec)


def _write(project: Project, sql: str, params: list[object]) -> None:
    connection = connect(project, read_only=False)
    try:
        connection.execute(sql, params)
    finally:
        connection.close()


def _insert(project: Project, **overrides: object) -> None:
    connection = connect(project, read_only=False)
    try:
        insert_registered_record(connection, REGISTERED, **overrides)
    finally:
        connection.close()


def _reader(project: Project) -> tuple[duckdb.DuckDBPyConnection, Corpus]:
    connection = connect(project, read_only=True)
    return connection, Corpus(connection)


# -- the table and the view -------------------------------------------------


@pytest.mark.integration
def test_registered_records__fresh_build__is_empty_and_checksums_as_empty(project: Project) -> None:
    connection = connect(project, read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM registered_records").fetchone() == (0,)
        assert table_checksum(connection, "registered_records") == EMPTY_SHA256
    finally:
        connection.close()


@pytest.mark.integration
def test_table_checksum__registered_row__moves_the_checksum(project: Project) -> None:
    _insert(project)
    connection = connect(project, read_only=True)
    try:
        assert table_checksum(connection, "registered_records") != EMPTY_SHA256
    finally:
        connection.close()


@pytest.mark.integration
def test_records_all__scopus_row_without_venue__keeps_null_venue_columns(project: Project) -> None:
    _write(project, "UPDATE records SET venue_id = NULL WHERE record_id = ?", [SCOPUS.record_id])
    connection = connect(project, read_only=True)
    try:
        row = connection.execute(
            "SELECT origin, venue_type, venue_name FROM records_all WHERE record_id = ?",
            [SCOPUS.record_id],
        ).fetchone()
    finally:
        connection.close()
    assert row == ("scopus", None, None)  # no COALESCE in the view (ADR 0032)


@pytest.mark.integration
def test_records_all__registered_row__has_nulls_for_run_cover_date_venue_and_open_access(
    project: Project,
) -> None:
    _insert(project, abstract=None)
    connection = connect(project, read_only=True)
    try:
        row = connection.execute(
            "SELECT origin, run_id, cover_date, venue_id, open_access, abstract, language, "
            "venue_type, venue_name, payload_line FROM records_all WHERE record_id = ?",
            [REGISTERED],
        ).fetchone()
    finally:
        connection.close()
    assert row == ("registered", None, None, None, None, None, None, "journal", VENUE, 0)


# -- Corpus accessors -------------------------------------------------------


@pytest.mark.integration
def test_corpus_records__raw_with_registered_row__includes_it_with_16_columns(
    project: Project,
) -> None:
    _insert(project)
    connection, corpus = _reader(project)
    try:
        frame = corpus.records(PrismaStage.RAW)
    finally:
        connection.close()
    assert frame.width == 16
    assert set(frame.get_column("origin").to_list()) == {"scopus", "registered"}
    row = frame.filter(frame["record_id"] == REGISTERED).row(0, named=True)
    assert (row["run_id"], row["cover_date"], row["venue_id"], row["open_access"]) == (None,) * 4


@pytest.mark.integration
def test_corpus_records__language_stage__includes_registered_row(project: Project) -> None:
    _insert(project)
    frame = Corpus.open(project).records(PrismaStage.LANGUAGE)
    assert REGISTERED in frame.get_column("record_id").to_list()


@pytest.mark.integration
def test_corpus_venues__registered_row__is_kept_with_null_venue_id_and_its_own_name(
    project: Project,
) -> None:
    _insert(project)
    connection, corpus = _reader(project)
    try:
        frame = corpus.venues(PrismaStage.RAW)
    finally:
        connection.close()
    row = frame.filter(frame["record_id"] == REGISTERED).row(0, named=True)
    assert row == {
        "record_id": REGISTERED,
        "venue_id": None,
        "name": VENUE,
        "issn": None,
        "eissn": None,
        "venue_type": "journal",
        "abbreviation": None,
    }
    assert frame.height == 3  # two Scopus rows + the registration


@pytest.mark.integration
def test_corpus_venues__scopus_row_without_a_venues_row__stays_absent(project: Project) -> None:
    _write(project, "UPDATE records SET venue_id = 'nope' WHERE record_id = ?", [SCOPUS.record_id])
    connection, corpus = _reader(project)
    try:
        frame = corpus.venues(PrismaStage.RAW)
    finally:
        connection.close()
    assert SCOPUS.record_id not in frame.get_column("record_id").to_list()


@pytest.mark.integration
def test_corpus_citations__registered_row__is_absent(project: Project) -> None:
    """ADR 0032: citations stay snapshot-only; the registration is `unavailable` elsewhere."""
    _insert(project)
    connection, corpus = _reader(project)
    try:
        frame = corpus.citations()
    finally:
        connection.close()
    assert REGISTERED not in frame.get_column("record_id").to_list()


# -- the engine -------------------------------------------------------------


@pytest.mark.integration
def test_raw_set__registered_row__is_in_s_raw_and_the_automated_set(project: Project) -> None:
    _insert(project)
    assert REGISTERED in engine.raw_set(project)
    assert REGISTERED in engine.automated_set(project)
    assert REGISTERED in engine.language_set(project)  # NULL language passes (ADR 0032 Cons. 7)


@pytest.mark.integration
def test_automated_set__registered_row_outside_the_year_window__is_excluded(
    project: Project,
) -> None:
    _insert(project, year=1990)
    assert REGISTERED in engine.raw_set(project)
    assert REGISTERED not in engine.automated_set(project)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("venue_name", "expected"),
    [("Proceedings of CVPR 2021", True), ("Proceedings of the Obscure Workshop", False)],
)
def test_automated_set__registered_conference__is_tested_against_the_whitelist(
    project: Project, venue_name: str, expected: bool
) -> None:
    write_criteria(project, CriteriaSpec(conference_whitelist=("CVPR",)))
    _insert(project, venue_type="conference", venue_name=venue_name, doc_type="conference paper")
    assert (REGISTERED in engine.automated_set(project)) is expected


@pytest.mark.integration
def test_automated_set__registered_journal__ignores_the_whitelist(project: Project) -> None:
    write_criteria(project, CriteriaSpec(conference_whitelist=("CVPR",)))
    _insert(project)
    assert REGISTERED in engine.automated_set(project)


@pytest.mark.integration
def test_automated_set__registered_row_with_blank_venue_name__is_refused_not_excluded(
    project: Project,
) -> None:
    write_criteria(project, CriteriaSpec(conference_whitelist=("CVPR",)))
    _insert(project, venue_type="conference", venue_name="   ")
    with pytest.raises(StoreError, match="blank venue_name"):
        engine.automated_set(project)


@pytest.mark.integration
def test_automated_set__registered_row__is_filtered_by_its_own_subject_area(
    project: Project,
) -> None:
    write_criteria(project, CriteriaSpec(subject_areas=("COMP",)))
    _insert(project)
    _write(project, "INSERT INTO subject_areas VALUES (?, '2700')", [REGISTERED])  # MEDI
    assert REGISTERED not in engine.automated_set(project)


# -- screening, numbers, provenance ----------------------------------------


@pytest.mark.integration
def test_load_records__registered_id__loads_title_abstract_venue_and_no_citation(
    project: Project,
) -> None:
    _insert(project)
    record = ui.load_records(project, [REGISTERED])[REGISTERED]
    assert (record.title, record.abstract, record.venue, record.year) == (
        "A Registered Work",
        "An OpenAlex abstract.",
        VENUE,
        2021,
    )
    assert record.cited_by is None
    assert record.authors == () and record.keywords == ()


@pytest.mark.integration
def test_corpus_numbers__registered_row__counts_in_size_and_year_bounds(project: Project) -> None:
    _insert(project, year=2031)
    connection = connect(project, read_only=True)
    try:
        numbers = _corpus_numbers(connection)
    finally:
        connection.close()
    assert numbers["corpus.size"] == 3
    assert numbers["corpus.year_max"] == 2031


@pytest.mark.integration
def test_venue_numbers__registered_row__counts_in_venues_total(project: Project) -> None:
    connection, corpus = _reader(project)
    try:
        before = _venue_numbers(corpus)["venues.total"]
    finally:
        connection.close()
    _insert(project)
    connection, corpus = _reader(project)
    try:
        assert _venue_numbers(corpus)["venues.total"] == before + 1
    finally:
        connection.close()


@pytest.mark.integration
def test_build_provenance__registered_row__counts_in_n_and_adds_no_run(project: Project) -> None:
    _insert(project)
    connection, corpus = _reader(project)
    try:
        frame = corpus.records(PrismaStage.RAW)
        provenance = build_provenance(corpus, stage=PrismaStage.RAW, records=frame)
    finally:
        connection.close()
    assert provenance.corpus_size == 3
    assert None not in provenance.run_ids


# -- readers ADR 0032 leaves unchanged --------------------------------------


@pytest.mark.integration
def test_store_stats__registered_row__records_loaded_stays_scopus_only(project: Project) -> None:
    _insert(project)
    connection = connect(project, read_only=True)
    try:
        stats = _stats_from_connection(connection, rebuilt=False)
    finally:
        connection.close()
    assert stats.records_loaded == 2
