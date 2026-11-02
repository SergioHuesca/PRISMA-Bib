"""ADR 0041 Decisions 1-3, 6: per-field provenance, the merge rule and the cascade loader.

Real filesystem and DuckDB, no network, synthetic payloads only (no licensed Scopus data).
Each test names the defect it fails on.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from prismabib.capture.layout import NON_RUN_DIRNAMES
from prismabib.errors import StoreError
from prismabib.project import Project
from prismabib.store.checksums import table_checksums
from prismabib.store.db import connect
from prismabib.store.load import (
    _normal_form,
    _sealed_cascade_run_dirs,
    build_store,
)
from tests.store_helpers import (
    copy_reference_project,
    make_entry,
    make_openalex_work,
    scopus_affiliation,
    write_sealed_cascade_run,
    write_sealed_openalex_run,
    write_sealed_run,
)

SCOPUS_CASCADE_NAME = "scopus-cascade"
SEARCH_RUN = "20250101T000000Z-11111111"
OA_1 = "20250102T000000Z-aaaaaaaa"
OA_2 = "20250103T000000Z-bbbbbbbb"
CAS_1 = "20260101T000000Z-cccccccc"
CAS_2 = "20260102T000000Z-dddddddd"
R1, R2, R3 = (f"scopus:2-s2.0-80000000000{n}" for n in (1, 2, 3))
D1, D2, D3 = "10.1000/one", "10.1000/two", "10.1000/three"
SOURCES = {
    "abstract": {"scopus-complete", "openalex", "manual", "unavailable"},
    "authors": {"scopus-complete", "openalex", "manual", "unavailable"},
    "affiliations": {
        "scopus-complete",
        "scopus-standard",
        "scopus-meta",
        "openalex",
        "manual",
        "unavailable",
    },
    "keywords": {"scopus-complete", "openalex-topic", "unavailable"},
}


def _project(tmp_path: Path, *, with_r3_doi: bool = False) -> Project:
    """R1 is complete in Scopus; R2 and R3 have no abstract, authors, affiliations or keywords."""
    project = Project.init("prov", title="Provenance", root=tmp_path)
    entries = [
        make_entry(
            eid="2-s2.0-800000000001",
            title="Alpha Study",
            cover_date="2020-03-01",
            doi=D1,
            description="Scopus abstract one",
            authkeywords="Graphs | Vision",  # pragma: allowlist secret
            author=[
                {"authid": "A1", "surname": "Smith", "given-name": "Ann"},
                {"authid": "A2", "surname": "Lee", "given-name": "Bo"},
            ],
            affiliation=[
                {
                    "afid": "60001",
                    "affilname": "Massachusetts Institute of Technology",
                    "affiliation-city": "Cambridge",
                    "affiliation-country": "United States",
                }
            ],
        ),
        make_entry(
            eid="2-s2.0-800000000002",
            title="Beta Study",
            cover_date="2021-03-01",
            doi=D2,
            description=None,
        ),
        make_entry(
            eid="2-s2.0-800000000003",
            title="Gamma Study",
            cover_date="2022-03-01",
            doi=D3 if with_r3_doi else None,
            description=None,
        ),
    ]
    write_sealed_run(
        project.raw_dir, SEARCH_RUN, entries, started_at=datetime(2025, 1, 1, tzinfo=UTC)
    )
    return project


def _sql(project: Project, sql: str, *params: object) -> list[tuple[Any, ...]]:
    connection = connect(project, read_only=True)
    try:
        return connection.execute(sql, list(params)).fetchall()
    finally:
        connection.close()


def _prov(project: Project, record_id: str, field: str) -> tuple[Any, ...]:
    (row,) = _sql(
        project,
        f"SELECT {field}_source, {field}_level, {field}_outcome, {field}_payload_ref "
        "FROM records WHERE record_id = ?",
        record_id,
    )
    return row


def _oa_work(oid: int, doi: str, title: str, year: int, **kwargs: Any) -> dict[str, Any]:
    return make_openalex_work(
        openalex_id=f"https://openalex.org/W{oid}", doi=doi, title=title, year=year, **kwargs
    )


def _oa_run(project: Project, run_id: str, *works: dict[str, Any]) -> None:
    dois = [w["doi"].removeprefix("https://doi.org/") for w in works]
    write_sealed_openalex_run(
        project.raw_dir,
        run_id,
        [{"lookup": "doi_batch", "identifiers": dois, "works": list(works)}],
    )


def _standard(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {"search-results": {"entry": entries}}


def _std_entry(scopus_id: str, doi: str, *affiliations: dict[str, str]) -> dict[str, Any]:
    entry: dict[str, Any] = {"dc:identifier": f"SCOPUS_ID:{scopus_id}", "prism:doi": doi}
    if affiliations:
        entry["affiliation"] = list(affiliations)
    return entry


def _meta(scopus_id: str, *affiliations: dict[str, str]) -> dict[str, Any]:
    body: dict[str, Any] = {"coredata": {"dc:identifier": f"SCOPUS_ID:{scopus_id}"}}
    if affiliations:
        body["affiliation"] = list(affiliations)
    return {"abstracts-retrieval-response": body}


MIT = scopus_affiliation("MIT", "Cambridge", "United States")
KAIST = scopus_affiliation("KAIST", "Daejeon", "South Korea")


# -- level 1 ---------------------------------------------------------------------------------


@pytest.mark.integration
def test_level1__scopus_fields__carry_source_level_and_the_records_own_payload_address(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    build_store(project, rebuild=True)
    ref = f"{SEARCH_RUN}/page-0000.jsonl#0"
    for field in ("abstract", "authors", "affiliations", "keywords"):
        assert _prov(project, R1, field) == ("scopus-complete", 1, "value", ref)
        assert _prov(project, R2, field) == ("unavailable", 0, "unavailable", None)


@pytest.mark.integration
def test_no_blanks__reference_fixture__every_cell_is_set_and_in_its_closed_set(
    tmp_path: Path,
) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    for field, allowed in SOURCES.items():
        rows = _sql(
            project,
            f"SELECT {field}_source, {field}_level, {field}_outcome, {field}_payload_ref, "
            "payload_file, payload_line FROM records",
        )
        assert len(rows) == 120
        for source, level, outcome, ref, payload_file, payload_line in rows:
            assert source in allowed
            assert outcome == ("unavailable" if source == "unavailable" else "value")
            assert (level == 0) == (source == "unavailable")
            if source == "scopus-complete":
                assert ref == f"{payload_file}#{payload_line}"
            else:
                assert ref is None


@pytest.mark.integration
def test_level1__outcome_matches_the_tables_the_loader_already_fills(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    (bad,) = _sql(
        project,
        """SELECT count(*) FROM records r WHERE
           (r.abstract IS NOT NULL) <> (r.abstract_outcome = 'value')
        OR (EXISTS (SELECT 1 FROM record_keywords k WHERE k.record_id = r.record_id))
           <> (r.keywords_outcome = 'value')
        OR (EXISTS (SELECT 1 FROM record_affiliations a WHERE a.record_id = r.record_id))
           <> (r.affiliations_outcome = 'value')""",
    )
    assert bad == (0,)


# -- level 4: fills, never overrides ---------------------------------------------------------


@pytest.mark.integration
def test_level4__fills_every_blank_field_with_its_own_provenance(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _oa_run(
        project,
        OA_1,
        _oa_work(
            2,
            D2,
            "Beta Study",
            2021,
            abstract_words=["An", "open", "abstract"],
            authors=["Carla Diaz", "Eve"],
            institutions=[("Seoul National University", "KR"), ("Nowhere Institute", None)],
            topics=["Graph Theory"],
        ),
    )
    build_store(project, rebuild=True)
    ref = f"openalex/{OA_1}/response-0000.jsonl#https://openalex.org/W2"
    assert _prov(project, R2, "abstract") == ("openalex", 4, "value", ref)
    assert _prov(project, R2, "authors") == ("openalex", 4, "value", ref)
    assert _prov(project, R2, "affiliations") == ("openalex", 4, "value", ref)
    assert _prov(project, R2, "keywords") == ("openalex-topic", 4, "value", ref)
    assert _sql(project, "SELECT abstract FROM records WHERE record_id = ?", R2) == [
        ("An open abstract",)
    ]
    assert _sql(
        project,
        "SELECT a.surname, a.given_name, ra.position FROM record_authors ra "
        "JOIN authors a USING (author_id) WHERE ra.record_id = ? ORDER BY ra.position",
        R2,
    ) == [("Diaz", "Carla", 1), ("Eve", None, 2)]  # plain fill keeps the given name
    assert _sql(
        project,
        "SELECT a.name, a.city, a.country_iso3 FROM record_affiliations ra "
        "JOIN affiliations a USING (afid) WHERE ra.record_id = ? ORDER BY a.name",
        R2,
    ) == [("Nowhere Institute", None, None), ("Seoul National University", None, "KOR")]
    assert _sql(project, "SELECT count(*) FROM provenance_conflicts") == [(0,)]


@pytest.mark.integration
def test_never_overrides__a_differing_openalex_value_keeps_scopus_and_records_the_pair(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _oa_run(
        project,
        OA_1,
        _oa_work(
            1,
            D1,
            "Alpha Study",
            2020,
            abstract_words=["A", "different", "abstract"],
            authors=["Ann Smith", "Bo Park"],
            institutions=[("Harvard University", "US")],
            topics=["Graph Theory"],
        ),
    )
    build_store(project, rebuild=True)
    assert _sql(project, "SELECT abstract FROM records WHERE record_id = ?", R1) == [
        ("Scopus abstract one",)
    ]
    for field in ("abstract", "authors", "affiliations"):
        assert _prov(project, R1, field)[:3] == ("scopus-complete", 1, "value")
    assert _prov(project, R1, "keywords")[:2] == ("scopus-complete", 1)  # keywords never conflict
    rows = _sql(
        project,
        "SELECT field, scopus_level, scopus_source, scopus_payload_ref, scopus_value, "
        "openalex_payload_ref, openalex_value, status, resolved_side, resolution_event_id "
        "FROM provenance_conflicts WHERE record_id = ? ORDER BY field",
        R1,
    )
    oa_ref = f"openalex/{OA_1}/response-0000.jsonl#https://openalex.org/W1"
    s_ref = f"{SEARCH_RUN}/page-0000.jsonl#0"
    assert rows == [
        (
            "abstract",
            1,
            "scopus-complete",
            s_ref,
            '"Scopus abstract one"',
            oa_ref,
            '"A different abstract"',
            "open",
            None,
            None,
        ),
        (
            "affiliations",
            1,
            "scopus-complete",
            s_ref,
            '["Massachusetts Institute of Technology"]',
            oa_ref,
            '["Harvard University"]',
            "open",
            None,
            None,
        ),
        (
            "authors",
            1,
            "scopus-complete",
            s_ref,
            '["Smith, Ann","Lee, Bo"]',
            oa_ref,
            '["Ann Smith","Bo Park"]',
            "open",
            None,
            None,
        ),
    ]
    # the Scopus rows are still the record's rows
    assert _sql(
        project,
        "SELECT a.surname FROM record_authors ra JOIN authors a USING (author_id) "
        "WHERE ra.record_id = ? ORDER BY ra.position",
        R1,
    ) == [("Smith",), ("Lee",)]


@pytest.mark.integration
def test_conflict__values_equal_under_the_comparison_rule__make_no_row(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _oa_run(
        project,
        OA_1,
        _oa_work(
            1,
            D1,
            "Alpha Study",
            2020,
            abstract_words=["SCOPUS", "abstract", "one"],  # case differs only
            authors=["Ann Smith", "Bo Lee"],  # same surnames in the same order
            institutions=[("massachusetts institute of technology", "US")],  # case differs
        ),
    )
    build_store(project, rebuild=True)
    assert _sql(project, "SELECT count(*) FROM provenance_conflicts") == [(0,)]


@pytest.mark.integration
def test_conflict__author_order_is_part_of_the_value(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _oa_run(
        project,
        OA_1,
        _oa_work(
            1,
            D1,
            "Alpha Study",
            2020,
            abstract_words=["Scopus", "abstract", "one"],
            authors=["Bo Lee", "Ann Smith"],
            institutions=[("Massachusetts Institute of Technology", "US")],
        ),
    )
    build_store(project, rebuild=True)
    assert _sql(project, "SELECT field FROM provenance_conflicts") == [("authors",)]


@pytest.mark.integration
def test_level4__latest_sealed_run_with_a_value_wins_and_a_newer_absent_never_replaces_it(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _oa_run(project, OA_1, _oa_work(2, D2, "Beta Study", 2021, abstract_words=["old", "text"]))
    _oa_run(project, OA_2, _oa_work(2, D2, "Beta Study", 2021, abstract_words=["new", "text"]))
    build_store(project, rebuild=True)
    assert _sql(project, "SELECT abstract FROM records WHERE record_id = ?", R2) == [("new text",)]
    assert _prov(project, R2, "abstract")[3].startswith(f"openalex/{OA_2}/")

    third = "20250104T000000Z-cccccccc"
    _oa_run(project, third, _oa_work(2, D2, "Beta Study", 2021))  # abstract absent
    build_store(project, rebuild=True)
    assert _sql(project, "SELECT abstract FROM records WHERE record_id = ?", R2) == [("new text",)]
    assert _prov(project, R2, "abstract")[3].startswith(f"openalex/{OA_2}/")


# -- levels 2 and 3 --------------------------------------------------------------------------


@pytest.mark.integration
def test_level2__fills_affiliations_only_and_addresses_the_entry_by_scopus_id(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    write_sealed_cascade_run(
        project.raw_dir,
        CAS_1,
        [
            {
                "view": "STANDARD",
                "identifiers": [D2],
                "body": _standard([_std_entry("800000000002", D2, MIT, KAIST)]),
            }
        ],
    )
    build_store(project, rebuild=True)
    ref = f"scopus-cascade/{CAS_1}/response-0000.jsonl#800000000002"
    assert _prov(project, R2, "affiliations") == ("scopus-standard", 2, "value", ref)
    for field in ("abstract", "authors", "keywords"):
        assert _prov(project, R2, field)[0] == "unavailable"
    assert _sql(
        project,
        "SELECT a.name, a.city, a.country_iso3 FROM record_affiliations ra "
        "JOIN affiliations a USING (afid) WHERE ra.record_id = ? ORDER BY a.name",
        R2,
    ) == [("KAIST", "Daejeon", "KOR"), ("MIT", "Cambridge", "USA")]


@pytest.mark.integration
def test_level2__entry_without_a_scopus_id__is_addressed_by_doi(tmp_path: Path) -> None:
    project = _project(tmp_path)
    entry = {"prism:doi": D2.upper(), "affiliation": [MIT]}
    write_sealed_cascade_run(
        project.raw_dir,
        CAS_1,
        [{"view": "STANDARD", "identifiers": [D2], "body": _standard([entry])}],
    )
    build_store(project, rebuild=True)
    assert (
        _prov(project, R2, "affiliations")[3]
        == f"scopus-cascade/{CAS_1}/response-0000.jsonl#doi:{D2}"
    )


@pytest.mark.integration
def test_level3__fills_what_level2_left_and_level2_wins_where_it_has_a_value(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, with_r3_doi=True)
    write_sealed_cascade_run(
        project.raw_dir,
        CAS_1,
        [
            {
                "view": "STANDARD",
                "identifiers": [D2, D3],
                "body": _standard(
                    [_std_entry("800000000002", D2, MIT), _std_entry("800000000003", D3)]
                ),  # R3: present, no affiliation => absent, triggers level 3
            },
            {"view": "META", "identifiers": ["800000000003"], "body": _meta("800000000003", KAIST)},
            {"view": "META", "identifiers": ["800000000002"], "body": _meta("800000000002", KAIST)},
        ],
    )
    build_store(project, rebuild=True)
    assert _prov(project, R2, "affiliations") == (
        "scopus-standard",
        2,
        "value",
        f"scopus-cascade/{CAS_1}/response-0000.jsonl#800000000002",
    )
    assert _prov(project, R3, "affiliations") == (
        "scopus-meta",
        3,
        "value",
        f"scopus-cascade/{CAS_1}/response-0001.jsonl#800000000003",
    )
    assert _sql(
        project,
        "SELECT a.country_iso3 FROM record_affiliations ra JOIN affiliations a USING (afid) "
        "WHERE ra.record_id = ?",
        R3,
    ) == [("KOR",)]


@pytest.mark.integration
def test_level2__never_replaces_a_level1_affiliation_and_never_conflicts_on_equal_countries(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    write_sealed_cascade_run(
        project.raw_dir,
        CAS_1,
        [
            {
                "view": "STANDARD",
                "identifiers": [D1],
                "body": _standard([_std_entry("800000000001", D1, KAIST)]),
            }
        ],
    )
    build_store(project, rebuild=True)
    assert _prov(project, R1, "affiliations")[:2] == ("scopus-complete", 1)
    assert _sql(project, "SELECT count(*) FROM provenance_conflicts") == [(0,)]  # no OpenAlex yet


@pytest.mark.integration
def test_conflict__level2_side_is_compared_by_country_sets_and_stored_as_codes(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    write_sealed_cascade_run(
        project.raw_dir,
        CAS_1,
        [
            {
                "view": "STANDARD",
                "identifiers": [D2],
                "body": _standard([_std_entry("800000000002", D2, MIT, KAIST)]),
            }
        ],
    )
    # Same countries, different institution names: not a conflict. 'ZZ' is unmapped and removed.
    _oa_run(
        project,
        OA_1,
        _oa_work(
            2,
            D2,
            "Beta Study",
            2021,
            institutions=[("Some Lab", "KR"), ("Other Lab", "US"), ("Odd Lab", "ZZ")],
        ),
    )
    build_store(project, rebuild=True)
    assert _sql(project, "SELECT count(*) FROM provenance_conflicts") == [(0,)]

    _oa_run(
        project,
        OA_2,
        _oa_work(
            2, D2, "Beta Study", 2021, institutions=[("Some Lab", "KR"), ("Berlin Lab", "DE")]
        ),
    )
    build_store(project, rebuild=True)
    (row,) = _sql(
        project,
        "SELECT scopus_level, scopus_source, scopus_value, openalex_value FROM provenance_conflicts",
    )
    assert row == (2, "scopus-standard", '["KOR","USA"]', '["Berlin Lab","Some Lab"]')


@pytest.mark.integration
def test_conflict__one_side_empty_after_removing_unmapped__is_no_conflict(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_sealed_cascade_run(
        project.raw_dir,
        CAS_1,
        [
            {
                "view": "STANDARD",
                "identifiers": [D2],
                "body": _standard(
                    [_std_entry("800000000002", D2, scopus_affiliation("X", "Y", "Atlantis"))]
                ),
            }
        ],
    )
    _oa_run(project, OA_1, _oa_work(2, D2, "Beta Study", 2021, institutions=[("Lab", "DE")]))
    build_store(project, rebuild=True)
    assert _sql(project, "SELECT count(*) FROM provenance_conflicts") == [(0,)]


# -- cascade discovery and the outcome check -------------------------------------------------


@pytest.mark.integration
def test_sealed_cascade_run_dirs__discovers_by_manifest_only_never_by_progress(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    assert SCOPUS_CASCADE_NAME in NON_RUN_DIRNAMES
    assert _sealed_cascade_run_dirs(project.raw_dir) == []
    write_sealed_cascade_run(
        project.raw_dir,
        CAS_1,
        [{"view": "META", "identifiers": ["800000000002"], "body": _meta("800000000002", MIT)}],
    )
    unsealed = project.raw_dir / SCOPUS_CASCADE_NAME / CAS_2
    unsealed.mkdir()
    (unsealed / "progress.json").write_text("{}", encoding="utf-8")
    (unsealed / "response-0000.jsonl").write_text("{}\n", encoding="utf-8")
    assert [p.name for p in _sealed_cascade_run_dirs(project.raw_dir)] == [CAS_1]
    build_store(project, rebuild=True)  # the unsealed run is ignored, not an error


@pytest.mark.integration
def test_cascade_run__sealed_without_opt_in__fails_the_build(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_sealed_cascade_run(
        project.raw_dir,
        CAS_1,
        [{"view": "META", "identifiers": ["800000000002"], "body": _meta("800000000002", MIT)}],
        fallback_opt_in=False,
    )
    with pytest.raises(StoreError, match="fallback_opt_in"):
        build_store(project, rebuild=True)


@pytest.mark.integration
def test_cascade_run__manifest_outcome_disagreeing_with_the_bytes__fails_the_build(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    write_sealed_cascade_run(
        project.raw_dir,
        CAS_1,
        [{"view": "META", "identifiers": ["800000000002"], "body": _meta("800000000002", MIT)}],
        bad_outcomes=True,
    )
    with pytest.raises(StoreError, match="disagree"):
        build_store(project, rebuild=True)


@pytest.mark.integration
def test_cascade_run__unreadable_manifest__fails_the_build(tmp_path: Path) -> None:
    project = _project(tmp_path)
    run = project.raw_dir / SCOPUS_CASCADE_NAME / CAS_1
    run.mkdir(parents=True)
    (run / "manifest.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(StoreError, match="manifest"):
        build_store(project, rebuild=True)


@pytest.mark.integration
def test_cascade_run__malformed_200_body__becomes_a_malformed_entries_row(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_sealed_cascade_run(
        project.raw_dir,
        CAS_1,
        [{"view": "META", "identifiers": ["800000000002"], "raw_bytes": b'{"unexpected": 1}'}],
    )
    stats = build_store(project, rebuild=True)
    assert _sql(project, "SELECT payload_file, reason FROM malformed_entries") == [
        (f"scopus-cascade/{CAS_1}/response-0000.jsonl", "malformed_cascade_body")
    ]
    assert stats.records_loaded == 3  # a malformed enrichment body never costs a record


@pytest.mark.integration
def test_cascade_run__latest_run_with_a_value_wins(tmp_path: Path) -> None:
    project = _project(tmp_path)
    for run_id, aff in ((CAS_1, MIT), (CAS_2, KAIST)):
        write_sealed_cascade_run(
            project.raw_dir,
            run_id,
            [{"view": "META", "identifiers": ["800000000002"], "body": _meta("800000000002", aff)}],
        )
    build_store(project, rebuild=True)
    assert _prov(project, R2, "affiliations")[3].startswith(f"scopus-cascade/{CAS_2}/")
    # a newer run that found nothing never replaces the older value
    write_sealed_cascade_run(
        project.raw_dir,
        "20260103T000000Z-eeeeeeee",
        [{"view": "META", "identifiers": ["800000000002"], "body": _meta("800000000002")}],
    )
    build_store(project, rebuild=True)
    assert _prov(project, R2, "affiliations")[3].startswith(f"scopus-cascade/{CAS_2}/")


# -- determinism and the comparison rule -----------------------------------------------------


def _full_project(tmp_path: Path) -> Project:
    project = _project(tmp_path, with_r3_doi=True)
    _oa_run(
        project,
        OA_1,
        _oa_work(1, D1, "Alpha Study", 2020, abstract_words=["Other", "words"]),
        _oa_work(
            2,
            D2,
            "Beta Study",
            2021,
            abstract_words=["An", "abstract"],
            authors=["Carla Diaz"],
            institutions=[("Seoul National University", "KR")],
            topics=["T"],
        ),
    )
    write_sealed_cascade_run(
        project.raw_dir,
        CAS_1,
        [
            {
                "view": "STANDARD",
                "identifiers": [D3],
                "body": _standard([_std_entry("800000000003", D3, MIT)]),
            }
        ],
    )
    return project


@pytest.mark.integration
def test_rebuild__twice_and_after_deleting_the_store__reproduces_provenance_and_counts(
    tmp_path: Path,
) -> None:
    project = _full_project(tmp_path)

    def snapshot() -> tuple[dict[str, str], list[tuple[Any, ...]], int]:
        connection = connect(project, read_only=True)
        try:
            sums = dict(table_checksums(connection))
            provenance = connection.execute("SELECT * FROM records ORDER BY record_id").fetchall()
            conflicts = connection.execute("SELECT count(*) FROM provenance_conflicts").fetchone()
        finally:
            connection.close()
        assert conflicts is not None
        return sums, provenance, conflicts[0]

    first_stats = build_store(project, rebuild=True)
    first = snapshot()
    build_store(project, rebuild=True)
    assert snapshot() == first
    project.db_path.unlink()
    second_stats = build_store(project, rebuild=False)
    assert snapshot() == first
    assert second_stats.records_loaded == first_stats.records_loaded == 3
    assert first[2] == 1  # R1's abstract conflict, and nothing else


@pytest.mark.unit
def test_normal_form__nfkc_casefold_and_whitespace() -> None:
    assert _normal_form("  \uff26oo \n\tBAR  ") == "foo bar"
    assert _normal_form("Stra\xdfe") == _normal_form("STRASSE")  # casefold, not lower
    assert _normal_form("e\u0301") == _normal_form("\xe9")  # NFKC composes


@pytest.mark.unit
def test_unicode_database__is_the_pinned_version() -> None:
    # Risk 3: NFKC and casefold read the interpreter's Unicode database (3.11 ships 14.0.0). A
    # different interpreter could change conflict counts, so the version is pinned and reported.
    assert unicodedata.unidata_version == "14.0.0"


@pytest.mark.integration
def test_build_report__records_the_unicode_version_and_the_fold_counters(tmp_path: Path) -> None:
    project = _project(tmp_path)
    stats = build_store(project, rebuild=True)
    assert stats.provenance_report["unidata_version"] == unicodedata.unidata_version
    for key in (
        "manual_superseded",
        "manual_orphaned",
        "conflict_resolution_orphaned",
        "conflict_resolution_stale",
    ):
        assert stats.provenance_report[key] == 0


@pytest.mark.integration
def test_build_report__previous_store_without_provenance__is_named(tmp_path: Path) -> None:
    import duckdb

    project = _project(tmp_path)
    project.db_path.parent.mkdir(parents=True, exist_ok=True)
    old = duckdb.connect(str(project.db_path))
    old.execute("CREATE TABLE records (record_id TEXT)")
    old.close()
    stats = build_store(project, rebuild=True)
    assert stats.provenance_report["previous_store_had_provenance"] == 0
    stats = build_store(project, rebuild=True)
    assert stats.provenance_report["previous_store_had_provenance"] == 1


@pytest.mark.integration
def test_layer0__is_byte_identical_before_and_after_the_build(tmp_path: Path) -> None:
    project = _full_project(tmp_path)

    def digest() -> str:
        h = hashlib.sha256()
        for path in sorted(project.raw_dir.rglob("*")):
            if path.is_file():
                h.update(path.relative_to(project.raw_dir).as_posix().encode())
                h.update(path.read_bytes())
        return h.hexdigest()

    before = digest()
    build_store(project, rebuild=True)
    assert digest() == before
    assert json.loads(next((project.raw_dir / SEARCH_RUN).glob("manifest.json")).read_text())


@pytest.mark.integration
def test_level4__same_surname_different_given_names__stay_distinct_rows(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _oa_run(
        project,
        OA_1,
        _oa_work(2, D2, "Beta Study", 2021, authors=["J Smith", "K Smith", "Maria de la Cruz"]),
    )
    build_store(project, rebuild=True)
    assert _sql(
        project,
        "SELECT a.surname, a.given_name FROM record_authors ra JOIN authors a USING (author_id) "
        "WHERE ra.record_id = ? ORDER BY ra.position",
        R2,
    ) == [("Smith", "J"), ("Smith", "K"), ("Cruz", "Maria de la")]


@pytest.mark.integration
def test_build_report__unreadable_previous_store__is_not_reported_as_lacking_provenance(
    tmp_path: Path,
) -> None:
    import structlog

    project = _project(tmp_path)
    project.db_path.parent.mkdir(parents=True, exist_ok=True)
    project.db_path.write_bytes(b"not a duckdb file")
    with structlog.testing.capture_logs() as logs:
        stats = build_store(project, rebuild=True)
    events = {e["event"] for e in logs}
    assert "store.load.previous_store_unreadable" in events
    assert "store.load.previous_store_had_no_provenance" not in events
    assert "previous_store_had_provenance" not in stats.provenance_report
