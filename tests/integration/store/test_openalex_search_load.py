"""The Layer 1 loader for OpenAlex-primary records (ADR 0042 Decision 5, PR 4a).

Real DuckDB and real files under ``tmp_path``; the runs are synthetic and hand-written
(``tests/openalex_search_helpers.py``). No network.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest

from prismabib.capture.layout import OPENALEX_SEARCH_DIRNAME, RUN_MANIFEST_FILENAME
from prismabib.errors import StoreError
from prismabib.project import Project
from prismabib.store import stamp as store_stamp
from prismabib.store.checksums import TABLE_NAMES, table_checksums
from prismabib.store.db import connect
from prismabib.store.load import build_store
from tests.openalex_search_helpers import openalex_project, work, write_search_run
from tests.store_helpers import REFERENCE_PROJECT_DIR

pytestmark = pytest.mark.integration

_NEW_TABLES = ("openalex_records", "openalex_search_runs")
_RUN_A = "20261103T100000Z-aaaaaaaa"
_RUN_B = "20261103T110000Z-bbbbbbbb"
# DuckDB TIMESTAMP is naive UTC: the helper runs start at 10:00 UTC and the refresh an hour later.
_AT_10 = datetime.fromisoformat("2026-11-03T10:00:00")
_AT_11 = datetime.fromisoformat("2026-11-03T11:00:00")


def rows(project: Project, sql: str) -> list[tuple[Any, ...]]:
    connection = connect(project, read_only=True)
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def one_run(tmp_path: Path, *works: dict[str, Any]) -> Project:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "carbon capture", [list(works)])
    build_store(project, rebuild=True)
    return project


# -- the tables and the checksums ----------------------------------------------------------------


def test_table_names__include_the_openalex_tables() -> None:
    assert set(_NEW_TABLES) <= set(TABLE_NAMES)


def test_store_schema__bumped_by_this_pr_and_written_to_the_stamp(tmp_path: Path) -> None:
    project = one_run(tmp_path, work(1))

    stamp = json.loads(store_stamp.stamp_path(project.db_path).read_text("utf-8"))
    assert store_stamp.STORE_SCHEMA == 2
    assert stamp == {"schema": 2, "source_mode": "openalex"}


def test_openalex_tables__project_without_search_runs__are_empty(tmp_path: Path) -> None:
    project = openalex_project(tmp_path, "scopus+openalex")
    build_store(project, rebuild=True)

    for table in _NEW_TABLES:
        assert rows(project, f"SELECT COUNT(*) FROM {table}") == [(0,)]
    empty = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"  # pragma: allowlist secret
    connection = connect(project, read_only=True)
    try:
        sums = table_checksums(connection, _NEW_TABLES)
    finally:
        connection.close()
    assert sums == dict.fromkeys(_NEW_TABLES, empty)


# -- what is read: sealed runs only ---------------------------------------------------------------


def test_loader__sealed_run__loads_each_work_with_its_payload_reference(tmp_path: Path) -> None:
    project = one_run(tmp_path, work(1), work(2))

    got = rows(
        project,
        "SELECT record_id, openalex_id, doi, title, year, payload_file, payload_line, query_run_id,"
        " language FROM openalex_records ORDER BY record_id",
    )
    assert got == [
        (
            f"openalex:https://openalex.org/W{n}",
            f"https://openalex.org/W{n}",
            f"10.1/item-{n}",  # bare and lower case: response DOIs are URLs
            f"Synthetic title {n}",
            2020 + n % 3,
            f"{OPENALEX_SEARCH_DIRNAME}/{_RUN_A}/response-0000.jsonl",
            n - 1,  # the work's index in results[], not a line
            _RUN_A,
            None,
        )
        for n in (1, 2)
    ]


def test_loader__unsealed_run__is_ignored(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1)]], sealed=False)
    build_store(project, rebuild=True)

    assert rows(project, "SELECT COUNT(*) FROM openalex_records") == [(0,)]
    assert rows(project, "SELECT COUNT(*) FROM openalex_search_runs") == [(0,)]


def test_loader__discarded_run__is_ignored(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1)]], sealed=False, discarded=True)
    write_search_run(project, _RUN_B, "q", [[work(2)]])
    build_store(project, rebuild=True)

    assert rows(project, "SELECT openalex_id FROM openalex_records") == [
        ("https://openalex.org/W2",)
    ]
    assert rows(project, "SELECT run_id FROM openalex_search_runs") == [(_RUN_B,)]


def test_build__mode_openalex__scopus_runs_reported_unused_not_loaded(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    for source in (REFERENCE_PROJECT_DIR / "raw").iterdir():
        shutil.copytree(source, project.raw_dir / source.name)
    write_search_run(project, _RUN_A, "q", [[work(1)]])
    build_store(project, rebuild=True)

    assert rows(project, "SELECT COUNT(*) FROM runs") == [(0,)]
    assert rows(project, "SELECT COUNT(*) FROM records") == [(0,)]
    assert rows(project, "SELECT COUNT(*) FROM openalex_records") == [(1,)]


@pytest.mark.parametrize("mode", ["scopus", "scopus+openalex"])
def test_build__scopus_modes__openalex_search_runs_are_not_loaded(
    tmp_path: Path, mode: str
) -> None:
    project = openalex_project(tmp_path, mode)
    write_search_run(project, _RUN_A, "q", [[work(1)]])
    build_store(project, rebuild=True)

    assert rows(project, "SELECT COUNT(*) FROM openalex_records") == [(0,)]
    assert rows(project, "SELECT COUNT(*) FROM openalex_search_runs") == [(0,)]


def test_loader__one_row_per_sealed_run_in_openalex_search_runs(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "carbon   capture", [[work(1), work(2)], [work(3)]])
    build_store(project, rebuild=True)

    assert rows(project, "SELECT * FROM openalex_search_runs") == [
        (_RUN_A, "carbon capture", "carbon   capture", 3, 3)
    ]


# -- the view -------------------------------------------------------------------------------------


def test_records_all__openalex_branch__origin_openalex_and_32_columns(tmp_path: Path) -> None:
    project = one_run(tmp_path, work(1))
    connection = connect(project, read_only=True)
    try:
        cursor = connection.execute("SELECT * FROM records_all")
        columns = [d[0] for d in cursor.description]
        (row,) = cursor.fetchall()
    finally:
        connection.close()
    got = dict(zip(columns, row, strict=True))

    assert len(columns) == 32
    assert (got["origin"], got["run_id"]) == ("openalex", _RUN_A)
    assert got["record_id"] == "openalex:https://openalex.org/W1"
    assert (got["venue_type"], got["venue_name"]) == ("journal", "Journal 1")
    assert (got["cover_date"], got["venue_id"], got["open_access"]) == (None, None, None)


# -- the sixteen provenance columns ----------------------------------------------------------------


def test_loader__provenance__every_field_with_a_value_is_level_four_openalex(
    tmp_path: Path,
) -> None:
    project = one_run(tmp_path, work(1))
    ref = f"{OPENALEX_SEARCH_DIRNAME}/{_RUN_A}/response-0000.jsonl#https://openalex.org/W1"

    (row,) = rows(
        project,
        "SELECT abstract, abstract_source, abstract_level, abstract_outcome, abstract_payload_ref,"
        " authors_source, authors_level, authors_outcome, authors_payload_ref,"
        " affiliations_source, affiliations_level, affiliations_outcome, affiliations_payload_ref,"
        " keywords_source, keywords_level, keywords_outcome, keywords_payload_ref"
        " FROM openalex_records",
    )
    assert row == (
        "Synthetic abstract1",
        *("openalex", 4, "value", ref),
        *("openalex", 4, "value", ref),
        *("openalex", 4, "value", ref),
        *("openalex-topic", 4, "value", ref),
    )


def test_loader__provenance__missing_fields_are_unavailable(tmp_path: Path) -> None:
    bare = work(
        1, abstract_inverted_index=None, authorships=None, topics=None, primary_location=None
    )
    project = one_run(tmp_path, bare)

    (row,) = rows(
        project,
        "SELECT abstract, abstract_source, abstract_level, abstract_outcome, abstract_payload_ref,"
        " authors_source, authors_outcome, affiliations_source, affiliations_outcome,"
        " keywords_source, keywords_outcome, keywords_level FROM openalex_records",
    )
    assert row == (
        *(None, "unavailable", 0, "unavailable", None),
        *("unavailable", "unavailable"),
        *("unavailable", "unavailable"),
        *("unavailable", "unavailable", 0),
    )


# -- authors, affiliations, keywords, citations, subject areas --------------------------------------


def test_loader__authors_and_affiliations__rows_with_openalex_ids(tmp_path: Path) -> None:
    named_only = work(
        2,
        authorships=[
            {"author": {"display_name": "Grace  Hopper"}, "institutions": []},
            {
                "author": {"id": "https://openalex.org/A9", "display_name": "Alan Turing"},
                "institutions": [
                    {"display_name": "No Id Institute", "country_code": "GB"},
                    {
                        "id": "https://openalex.org/I9",
                        "display_name": "Known",
                        "country_code": "xx",
                    },
                    {
                        "id": "https://openalex.org/I9",
                        "display_name": "Known",
                        "country_code": "xx",
                    },
                ],
            },
        ],
    )
    project = one_run(tmp_path, work(1), named_only)

    assert rows(project, "SELECT author_id, surname, given_name FROM authors ORDER BY 1") == [
        ("openalex-author:https://openalex.org/A1", "Author1", "Ada"),
        ("openalex-author:https://openalex.org/A9", "Turing", "Alan"),
        ("openalex-name:grace hopper", "Hopper", "Grace"),
    ]
    assert rows(
        project,
        "SELECT record_id, author_id, position FROM record_authors ORDER BY record_id, position",
    ) == [
        ("openalex:https://openalex.org/W1", "openalex-author:https://openalex.org/A1", 1),
        ("openalex:https://openalex.org/W2", "openalex-name:grace hopper", 1),
        ("openalex:https://openalex.org/W2", "openalex-author:https://openalex.org/A9", 2),
    ]
    assert rows(project, "SELECT afid, name, city, country_iso3 FROM affiliations ORDER BY 1") == [
        ("openalex-inst:https://openalex.org/I1", "Institute 1", None, "ESP"),
        ("openalex-inst:https://openalex.org/I9", "Known", None, None),
        ("openalex-name:no id institute", "No Id Institute", None, "GBR"),
    ]
    assert rows(
        project, "SELECT record_id, afid FROM record_affiliations ORDER BY record_id, afid"
    ) == [
        ("openalex:https://openalex.org/W1", "openalex-inst:https://openalex.org/I1"),
        ("openalex:https://openalex.org/W2", "openalex-inst:https://openalex.org/I9"),
        ("openalex:https://openalex.org/W2", "openalex-name:no id institute"),
    ]


def test_loader__topics__are_openalex_topic_keywords(tmp_path: Path) -> None:
    project = one_run(
        tmp_path, work(1, topics=[{"display_name": "Carbon"}, {"display_name": "!!"}])
    )

    assert rows(project, "SELECT term_raw, term_norm FROM keywords") == [("Carbon", "carbon")]
    assert rows(project, "SELECT record_id, kind FROM record_keywords") == [
        ("openalex:https://openalex.org/W1", "openalex-topic")
    ]


def test_citations__openalex_record__snapshot_row_written(tmp_path: Path) -> None:
    project = one_run(tmp_path, work(1), work(2, cited_by_count=None))

    got = rows(project, "SELECT record_id, retrieved_at, cited_by_count FROM citation_snapshots")
    assert got == [("openalex:https://openalex.org/W1", _AT_10, 10)]


def test_subject_coverage__mapped_primary_topic__assigned_with_one_subject_row(
    tmp_path: Path,
) -> None:
    project = one_run(tmp_path, work(1))

    assert rows(project, "SELECT record_id, area_code FROM subject_areas") == [
        ("openalex:https://openalex.org/W1", "1702")
    ]
    assert rows(project, "SELECT * FROM record_subject_area_coverage") == [
        ("openalex:https://openalex.org/W1", _RUN_A, "assigned")
    ]


@pytest.mark.parametrize(
    "topic",
    [
        None,
        {"id": "https://openalex.org/T2"},
        {"subfield": {"id": "https://openalex.org/subfields/9999999"}},
    ],
    ids=["no-primary-topic", "no-subfield", "unmapped-subfield"],
)
def test_subject_coverage__openalex_no_primary_topic__status_none_assigned(
    tmp_path: Path, topic: dict[str, Any] | None
) -> None:
    project = one_run(tmp_path, work(1, primary_topic=topic))

    assert rows(project, "SELECT COUNT(*) FROM subject_areas") == [(0,)]
    assert rows(project, "SELECT * FROM record_subject_area_coverage") == [
        ("openalex:https://openalex.org/W1", _RUN_A, "none_assigned")
    ]
    assert rows(project, "SELECT COUNT(*) FROM openalex_records") == [(1,)]


# -- classification: stored, not refused ------------------------------------------------------------


def test_openalex_records__unmapped_type__stored_raw_not_refused(tmp_path: Path) -> None:
    project = one_run(
        tmp_path,
        work(1, type="dataset"),  # a refused type in the map: ADR 0032 would refuse it
        work(2, type="not-a-real-type"),
        work(3, type="book-chapter"),
        work(4, type=None),
    )

    assert rows(project, "SELECT openalex_id, doc_type FROM openalex_records ORDER BY 1") == [
        ("https://openalex.org/W1", "dataset"),
        ("https://openalex.org/W2", "not-a-real-type"),
        ("https://openalex.org/W3", "book chapter"),  # a mapped type takes the engine's form
        ("https://openalex.org/W4", None),
    ]
    assert rows(project, "SELECT COUNT(*) FROM malformed_entries") == [(0,)]


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        (None, (None, None)),
        ({"source": None}, (None, None)),
        ({"source": {"display_name": "J", "type": "unknown-kind"}}, (None, "J")),
        ({"source": {"display_name": "   ", "type": "journal"}}, ("journal", None)),
        ({"source": {"type": "journal"}}, ("journal", None)),
        (
            {"source": {"display_name": "  Spaced  ", "type": "conference"}},
            ("conference", "Spaced"),
        ),
    ],
    ids=["no-location", "null-source", "unmapped-source-type", "blank-name", "no-name", "stripped"],
)
def test_openalex_records__missing_source_or_unmapped_source_type__stored_null_not_refused(
    tmp_path: Path, location: dict[str, Any] | None, expected: tuple[str | None, str | None]
) -> None:
    project = one_run(tmp_path, work(1, primary_location=location))

    assert rows(project, "SELECT venue_type, venue_name FROM openalex_records") == [expected]
    assert rows(project, "SELECT COUNT(*) FROM malformed_entries") == [(0,)]


# -- malformed works --------------------------------------------------------------------------------


def test_openalex_records__work_without_title__counted_as_removed_other_reasons(
    tmp_path: Path,
) -> None:
    project = one_run(
        tmp_path, work(1), work(2, title=None), work(3, title="   "), work(4), work(5, id=None)
    )

    assert rows(project, "SELECT COUNT(*) FROM openalex_records") == [(2,)]
    ref = f"{OPENALEX_SEARCH_DIRNAME}/{_RUN_A}/response-0000.jsonl"
    assert rows(
        project,
        "SELECT run_id, payload_file, payload_line, record_id, reason FROM malformed_entries"
        " ORDER BY payload_line",
    ) == [
        (_RUN_A, ref, 1, "openalex:https://openalex.org/W2", "malformed_openalex_search_work"),
        (_RUN_A, ref, 2, "openalex:https://openalex.org/W3", "malformed_openalex_search_work"),
        (_RUN_A, ref, 4, None, "malformed_openalex_search_work"),
    ]


def test_malformed__two_bad_works_in_one_page__both_counted(tmp_path: Path) -> None:
    project = one_run(tmp_path, work(1, title=None), work(2, title=None), work(3))

    assert rows(project, "SELECT payload_line FROM malformed_entries ORDER BY 1") == [(0,), (1,)]


def test_loader__sealed_run_with_a_missing_response_file__raises_store_error(
    tmp_path: Path,
) -> None:
    project = openalex_project(tmp_path)
    run_dir = write_search_run(project, _RUN_A, "q", [[work(1)]])
    (run_dir / "response-0000.jsonl").unlink()

    with pytest.raises(StoreError, match="response-0000"):
        build_store(project, rebuild=True)


# -- duplicates -------------------------------------------------------------------------------------


def test_flow_data__two_overlapping_queries__duplicates_are_measured_by_the_loader(
    tmp_path: Path,
) -> None:
    """ADR 0042 Verification 46, loader side: A holds works 1-3, B holds 2-4."""
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "query a", [[work(1), work(2), work(3)]])
    write_search_run(project, _RUN_B, "query b", [[work(2), work(3), work(4)]])
    build_store(project, rebuild=True)

    assert rows(project, "SELECT COUNT(*) FROM openalex_records") == [(4,)]
    assert rows(project, "SELECT run_id, duplicates FROM run_duplicates") == [(_RUN_B, 2)]
    assert rows(project, "SELECT run_id, total_count FROM openalex_search_runs ORDER BY 1") == [
        (_RUN_A, 3),
        (_RUN_B, 3),
    ]
    assert rows(project, "SELECT DISTINCT query_run_id FROM openalex_records ORDER BY 1") == [
        (_RUN_A,),
        (_RUN_B,),
    ]
    # identified (6) - duplicates (2) - removed (0) == |S_raw| (4): the equation closes.
    assert rows(project, "SELECT COUNT(*) FROM records_all WHERE origin = 'openalex'") == [(4,)]


def test_dedup__doi_collision_within_earliest_run__counted_in_run_duplicates(
    tmp_path: Path,
) -> None:
    """Q-C: two OpenAlex ids with one DOI. The response DOI is a URL; the key is the bare form."""
    twin = work(2, doi="https://doi.org/10.1/ITEM-1")
    project = one_run(tmp_path, work(1), twin, work(3))

    assert rows(project, "SELECT openalex_id FROM openalex_records ORDER BY 1") == [
        ("https://openalex.org/W1",),
        ("https://openalex.org/W3",),
    ]
    assert rows(project, "SELECT run_id, duplicates FROM run_duplicates") == [(_RUN_A, 1)]


def test_dedup__no_doi__falls_back_to_the_openalex_id(tmp_path: Path) -> None:
    project = one_run(
        tmp_path, work(1, doi=None), work(1, doi=None), work(2, doi=None), work(3, doi="")
    )

    assert rows(project, "SELECT COUNT(*) FROM openalex_records") == [(3,)]
    assert rows(project, "SELECT run_id, duplicates FROM run_duplicates") == [(_RUN_A, 1)]


def test_dedup__doi_found_by_a_later_query_under_another_id__is_a_duplicate(
    tmp_path: Path,
) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "query a", [[work(1)]])
    write_search_run(project, _RUN_B, "query b", [[work(9, doi="https://doi.org/10.1/item-1")]])
    build_store(project, rebuild=True)

    assert rows(project, "SELECT openalex_id FROM openalex_records") == [
        ("https://openalex.org/W1",)
    ]
    assert rows(project, "SELECT run_id, duplicates FROM run_duplicates") == [(_RUN_B, 1)]


def test_flow_counts__same_query_refresh__not_a_duplicate_and_adds_no_membership(
    tmp_path: Path,
) -> None:
    """ADR 0013's condition. A refresh updates values and adds a snapshot; it adds no member."""
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "query a", [[work(1), work(2)]])
    refreshed = work(1, cited_by_count=99, abstract_inverted_index={"Fresh": [0], "words": [1]})
    write_search_run(
        project, _RUN_B, "query  a", [[refreshed, work(3)]], offset_minutes=60
    )  # same query_key, new work 3
    build_store(project, rebuild=True)

    assert rows(project, "SELECT openalex_id, query_run_id FROM openalex_records ORDER BY 1") == [
        ("https://openalex.org/W1", _RUN_A),
        ("https://openalex.org/W2", _RUN_A),
    ]
    assert rows(project, "SELECT COUNT(*) FROM run_duplicates") == [(0,)]
    assert rows(project, "SELECT run_id FROM openalex_search_runs ORDER BY 1") == [
        (_RUN_A,),
        (_RUN_B,),
    ]
    assert rows(
        project,
        "SELECT retrieved_at, cited_by_count FROM citation_snapshots"
        " WHERE record_id = 'openalex:https://openalex.org/W1' ORDER BY 1",
    ) == [(_AT_10, 10), (_AT_11, 99)]
    # ADR 0031 Decision 7: the later sealed run's value wins, and the reference follows it.
    assert rows(
        project,
        "SELECT abstract, abstract_payload_ref FROM openalex_records"
        " WHERE openalex_id = 'https://openalex.org/W1'",
    ) == [
        (
            "Fresh words",
            f"{OPENALEX_SEARCH_DIRNAME}/{_RUN_B}/response-0000.jsonl#https://openalex.org/W1",
        )
    ]


def test_refresh__a_field_the_new_run_lacks__keeps_the_older_value(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1)]])
    write_search_run(project, _RUN_B, "q", [[work(1, abstract_inverted_index=None)]])
    build_store(project, rebuild=True)

    assert rows(project, "SELECT abstract FROM openalex_records") == [("Synthetic abstract1",)]


# -- determinism and the stamp ----------------------------------------------------------------------


def _checksums(project: Project) -> dict[str, str]:
    connection = connect(project, read_only=True)
    try:
        return table_checksums(connection)
    finally:
        connection.close()


def test_rebuild__determinism__openalex_records_checksums_identical(tmp_path: Path) -> None:
    def build(base: Path) -> Project:
        project = openalex_project(base)
        write_search_run(project, _RUN_A, "query a", [[work(1), work(2)], [work(3)]])
        write_search_run(project, _RUN_B, "query b", [[work(3), work(4, title=None)]])
        build_store(project, rebuild=True)
        return project

    first = build(tmp_path / "one")
    second = build(tmp_path / "two")
    again = _checksums(first)
    build_store(first, rebuild=True)

    assert _checksums(first) == again == _checksums(second)
    assert again["openalex_records"] != again["records"]


@pytest.mark.parametrize("mode", ["openalex", "scopus", "scopus+openalex"])
def test_store__no_stamp__after_pr4a__refuses_needs_rebuild(tmp_path: Path, mode: str) -> None:
    """Whatever the mode (the legacy one included) and whatever tables the store holds."""
    project = openalex_project(tmp_path, mode)
    build_store(project, rebuild=True)
    store_stamp.stamp_path(project.db_path).unlink()

    with pytest.raises(StoreError, match=r"no build stamp.*--rebuild"):
        connect(project)
    with pytest.raises(StoreError, match=r"no build stamp.*--rebuild"):
        build_store(project)
    build_store(project, rebuild=True)  # the fix the message names
    connect(project).close()


def test_store__stamp_of_the_previous_schema__refused(tmp_path: Path) -> None:
    project = one_run(tmp_path, work(1))
    store_stamp.stamp_path(project.db_path).write_text(
        '{"schema": 1, "source_mode": "openalex"}', encoding="utf-8"
    )

    with pytest.raises(StoreError, match=r"schema 1.*--rebuild"):
        connect(project)


def test_store__legacy_store_without_the_new_tables__is_refused_never_read_as_zero(
    tmp_path: Path,
) -> None:
    import duckdb

    project = one_run(tmp_path, work(1))
    raw = duckdb.connect(str(project.db_path))
    raw.execute("DROP VIEW records_all")
    raw.execute("DROP TABLE openalex_records")
    raw.execute("DROP TABLE openalex_search_runs")
    raw.close()

    with pytest.raises(StoreError, match=r"missing 2 table.*openalex_records"):
        connect(project)


# -- review round: the loader does not trust page files beyond "exists and parses" -------------------


def _edit_manifest(run_dir: Path, edit: Any) -> None:
    path = run_dir / RUN_MANIFEST_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    edit(data)
    path.write_text(json.dumps(data), encoding="utf-8")


def _sealed(tmp_path: Path, *numbers: int) -> tuple[Project, Path]:
    project = openalex_project(tmp_path)
    run_dir = write_search_run(project, _RUN_A, "q", [[work(n) for n in numbers]])
    return project, run_dir


def _set_digest(run_dir: Path, data: bytes) -> None:
    digest = hashlib.sha256(data).hexdigest()
    _edit_manifest(run_dir, lambda m: m["requests"][0].update(sha256=digest))


def test_loader__truncated_results_with_a_matching_digest__raises_store_error(
    tmp_path: Path,
) -> None:
    project, run_dir = _sealed(tmp_path, 1, 2, 3)
    page = run_dir / "response-0000.jsonl"
    body = json.loads(page.read_text(encoding="utf-8"))
    body["results"] = [work(1)]
    data = json.dumps(body, separators=(",", ":")).encode("utf-8") + b"\n"
    page.write_bytes(data)
    _set_digest(run_dir, data)  # the digest follows; result_count still says 3

    with pytest.raises(StoreError, match=r"response-0000.*result count.*re-capture"):
        build_store(project, rebuild=True)


def test_loader__one_changed_byte__raises_store_error_naming_the_file(tmp_path: Path) -> None:
    project, run_dir = _sealed(tmp_path, 1, 2)
    page = run_dir / "response-0000.jsonl"
    page.write_bytes(page.read_bytes().replace(b"Synthetic title 1", b"Synthetic title X"))

    with pytest.raises(StoreError, match=r"response-0000.*digest.*re-capture"):
        build_store(project, rebuild=True)


def test_loader__invalid_json_page__raises_store_error(tmp_path: Path) -> None:
    project, run_dir = _sealed(tmp_path, 1)
    data = b"{not json\n"
    (run_dir / "response-0000.jsonl").write_bytes(data)
    _set_digest(run_dir, data)

    with pytest.raises(StoreError, match="response-0000"):
        build_store(project, rebuild=True)


def test_loader__sealed_without_total_count__raises_store_error(tmp_path: Path) -> None:
    project, run_dir = _sealed(tmp_path, 1)
    _edit_manifest(run_dir, lambda m: m.update(total_count=None))

    with pytest.raises(StoreError, match="total_count"):
        build_store(project, rebuild=True)


# -- review round: one odd work becomes a malformed entry, never a traceback ---------------------


@pytest.mark.parametrize(
    "odd",
    [
        {"authorships": ["x"]},
        {"authorships": {"a": 1}},
        {"topics": ["x"]},
        {"abstract_inverted_index": {"a": [None, 1]}},
    ],
    ids=["authorships-strings", "authorships-dict", "topics-strings", "inverted-index-null"],
)
def test_loader__odd_work_shape__is_malformed_entry_and_the_rest_load(
    tmp_path: Path, odd: dict[str, Any]
) -> None:
    project = one_run(tmp_path, work(1), work(2, **odd), work(3, **odd), work(4))

    assert rows(project, "SELECT openalex_id FROM openalex_records ORDER BY 1") == [
        ("https://openalex.org/W1",),
        ("https://openalex.org/W4",),
    ]
    assert rows(
        project, "SELECT payload_line, record_id, reason FROM malformed_entries ORDER BY 1"
    ) == [
        (1, "openalex:https://openalex.org/W2", "malformed_openalex_search_work"),
        (2, "openalex:https://openalex.org/W3", "malformed_openalex_search_work"),
    ]


# -- review round: the reuse path is mode-aware -------------------------------------------------------


def test_build__reuse_in_mode_openalex__scopus_runs_do_not_trip_the_empty_store_check(
    tmp_path: Path,
) -> None:
    project = openalex_project(tmp_path)
    for source in (REFERENCE_PROJECT_DIR / "raw").iterdir():
        shutil.copytree(source, project.raw_dir / source.name)
    write_search_run(project, _RUN_A, "q", [[work(1)]])
    build_store(project, rebuild=True)

    stats = build_store(project)  # the reuse path

    assert stats.rebuilt is False
    assert stats.openalex_records_loaded == 1


def test_build__reuse_in_mode_openalex__empty_store_over_a_populated_search_run_is_refused(
    tmp_path: Path,
) -> None:
    project = openalex_project(tmp_path)
    build_store(project, rebuild=True)  # an empty store, stamped
    write_search_run(project, _RUN_A, "q", [[work(1), work(2)]])

    with pytest.raises(StoreError, match=r"holds no records.*2 works.*--rebuild"):
        build_store(project)


def test_build__reuse_in_a_scopus_mode__still_refuses_an_empty_store_over_a_scopus_run(
    tmp_path: Path,
) -> None:
    project = openalex_project(tmp_path, "scopus")
    build_store(project, rebuild=True)
    for source in (REFERENCE_PROJECT_DIR / "raw").iterdir():
        shutil.copytree(source, project.raw_dir / source.name)

    with pytest.raises(StoreError, match="holds no records"):
        build_store(project)


# -- review round: details of the derivation ------------------------------------------------------------


def test_loader__same_author_twice_in_one_work__one_record_authors_row(tmp_path: Path) -> None:
    twice = work(
        1,
        authorships=[
            {"author": {"id": "https://openalex.org/A1", "display_name": "Ada One"}},
            {"author": {"id": "https://openalex.org/A2", "display_name": "Bo Two"}},
            {"author": {"id": "https://openalex.org/A1", "display_name": "Ada One"}},
        ],
    )
    project = one_run(tmp_path, twice)

    assert rows(project, "SELECT author_id, position FROM record_authors ORDER BY position") == [
        ("openalex-author:https://openalex.org/A1", 1),
        ("openalex-author:https://openalex.org/A2", 2),
    ]


def test_finalise__shared_author_id_with_different_spellings__name_independent_of_insertion_order(
    tmp_path: Path,
) -> None:
    def by(name: str) -> list[dict[str, Any]]:
        return [{"author": {"id": "https://openalex.org/A9", "display_name": name}}]

    # Page order W2 then W1; the sorted-first record (W1) supplies the spelling.
    project = one_run(
        tmp_path, work(2, authorships=by("Zed Spelling")), work(1, authorships=by("Ann Original"))
    )

    assert rows(project, "SELECT surname, given_name FROM authors") == [("Original", "Ann")]


def test_loader__non_utc_offset_in_the_manifest__is_stored_as_naive_utc(tmp_path: Path) -> None:
    """Fails on a UTC runner too: the page time carries +05:30, so the snapshot must read 10:00."""
    project, run_dir = _sealed(tmp_path, 1)
    _edit_manifest(
        run_dir, lambda m: m["requests"][0].update(retrieved_at="2026-11-03T15:30:00+05:30")
    )
    build_store(project, rebuild=True)

    assert rows(project, "SELECT retrieved_at FROM citation_snapshots") == [(_AT_10,)]
    assert rows(project, "SELECT retrieved_at FROM openalex_records") == [(_AT_10,)]


def test_store__no_stamp_message__is_mode_neutral(tmp_path: Path) -> None:
    project = one_run(tmp_path, work(1))
    store_stamp.stamp_path(project.db_path).unlink()

    with pytest.raises(StoreError) as caught:
        connect(project)

    text = str(caught.value)
    assert "no build stamp" in text and "--rebuild" in text
    assert "scopus+openalex" not in text.split("this project")[0]  # the store's "mode" is unknown
    assert "schema 0" not in text


def test_loader__country_only_authorship__affiliation_row_with_a_country_and_value_outcome(
    tmp_path: Path,
) -> None:
    only_country = work(
        1,
        authorships=[{"author": {"display_name": "A B"}, "institutions": [], "countries": ["ES"]}],
    )
    nothing = work(2, authorships=[{"author": {"display_name": "C D"}, "countries": ["ZZ", ""]}])
    project = one_run(tmp_path, only_country, nothing)

    assert rows(project, "SELECT afid, name, country_iso3 FROM affiliations") == [
        ("openalex-country:ES", "ES", "ESP")
    ]
    assert rows(project, "SELECT record_id, afid FROM record_affiliations") == [
        ("openalex:https://openalex.org/W1", "openalex-country:ES")
    ]
    assert rows(
        project, "SELECT openalex_id, affiliations_outcome FROM openalex_records ORDER BY 1"
    ) == [("https://openalex.org/W1", "value"), ("https://openalex.org/W2", "unavailable")]


# -- review follow-ups: messages and the unmapped country log -----------------------------------------


def test_loader__digest_mismatch_message__names_the_file_and_the_line_ending_cause(
    tmp_path: Path,
) -> None:
    project, run_dir = _sealed(tmp_path, 1)
    page = run_dir / "response-0000.jsonl"
    page.write_bytes(page.read_bytes().replace(b"\n", b"\r\n"))  # what core.autocrlf does

    with pytest.raises(StoreError) as caught:
        build_store(project, rebuild=True)

    text = str(caught.value)
    assert "response-0000" in text and "line-ending" in text and "core.autocrlf" in text


def test_build__reuse_in_mode_openalex__malformed_run__message_says_a_rebuild_reproduces_it(
    tmp_path: Path,
) -> None:
    # A capture with nothing loadable is now refused at build time, so the only way to meet an
    # empty store over populated raw/ is a store emptied after the build: simulate that residue.
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1, title=None), work(2, title=None), work(3)]])
    build_store(project, rebuild=True)
    connection = duckdb.connect(str(project.db_path))
    try:
        connection.execute("DELETE FROM openalex_records")
    finally:
        connection.close()

    with pytest.raises(StoreError) as caught:
        build_store(project)

    text = str(caught.value)
    assert "malformed_entries" in text and "reproduces" in text and "--rebuild" in text
    assert "Call build_store(project, rebuild=True)" not in text


def test_loader__unmapped_authorship_country_codes__logged_once_per_code_per_build(
    tmp_path: Path,
) -> None:
    from structlog.testing import capture_logs

    def by(*codes: str) -> list[dict[str, Any]]:
        return [{"author": {"display_name": "A B"}, "countries": list(codes)}]

    project = openalex_project(tmp_path)
    write_search_run(
        project,
        _RUN_A,
        "q",
        [[work(1, authorships=by("UK", "ES")), work(2, authorships=by("UK", "EU")), work(3)]],
    )

    with capture_logs() as logs:
        build_store(project, rebuild=True)

    events = [
        (e["code"], e["works"])
        for e in logs
        if e["event"] == "openalex_search.unmapped_country_code"
    ]
    assert sorted(events) == [("EU", 1), ("UK", 2)]  # one event per code; ES is mapped
