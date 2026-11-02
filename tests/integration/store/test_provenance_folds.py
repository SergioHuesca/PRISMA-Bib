"""ADR 0041 Decisions 2 and 4: the manual-entry fold and the conflict-resolution fold.

The writers are PR 6's; these tests append events straight to the Layer 2 logs through
``AppendOnlyLog`` and check what the loader makes of them. Each test names the defect it fails on.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from prismabib.errors import LogError, StoreError
from prismabib.models import (
    ConflictRecord,
    ConflictResolution,
    ConflictReverse,
    ManualEntry,
    ManualRecord,
    ManualReverse,
    conflict_resolutions_log,
    manual_entries_log,
)
from prismabib.project import Project
from prismabib.store.checksums import table_checksums
from prismabib.store.db import connect
from prismabib.store.load import StoreStats, build_store
from tests.store_helpers import (
    make_entry,
    make_openalex_work,
    write_sealed_openalex_run,
    write_sealed_run,
)

SEARCH_RUN = "20250101T000000Z-11111111"
OA_1 = "20250102T000000Z-aaaaaaaa"
OA_2 = "20250103T000000Z-bbbbbbbb"
R1, R2 = "scopus:2-s2.0-800000000001", "scopus:2-s2.0-800000000002"
D1, D2 = "10.1000/one", "10.1000/two"
TS = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
OA_REF_1 = f"openalex/{OA_1}/response-0000.jsonl#https://openalex.org/W1"
MIT_NAME = "Massachusetts Institute of Technology"


def _ulid(n: int) -> str:
    return f"01FOLD{n:020d}"


def _project(tmp_path: Path) -> Project:
    """R1 has every Scopus field; R2 has none. An OpenAlex run disagrees with R1 on three."""
    project = Project.init("fold", title="Folds", root=tmp_path)
    write_sealed_run(
        project.raw_dir,
        SEARCH_RUN,
        [
            make_entry(
                eid="2-s2.0-800000000001",
                title="Alpha Study",
                cover_date="2020-03-01",
                doi=D1,
                description="Scopus abstract one",
                author=[
                    {"authid": "A1", "surname": "Smith", "given-name": "Ann"},
                    {"authid": "A2", "surname": "Lee", "given-name": "Bo"},
                ],
                affiliation=[
                    {
                        "afid": "60001",
                        "affilname": MIT_NAME,
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
        ],
        started_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    _oa(project, OA_1, "An openalex abstract")
    return project


def _oa(project: Project, run_id: str, abstract: str) -> None:
    work = make_openalex_work(
        openalex_id="https://openalex.org/W1",
        doi=D1,
        title="Alpha Study",
        year=2020,
        abstract_words=abstract.split(),
        authors=["Ann Smith", "Bo Park"],
        institutions=[("Harvard University", "US"), ("Seoul National University", "KR")],
    )
    write_sealed_openalex_run(
        project.raw_dir, run_id, [{"lookup": "doi_batch", "identifiers": [D1], "works": [work]}]
    )


def _sql(project: Project, sql: str, *params: object) -> list[tuple[Any, ...]]:
    connection = connect(project, read_only=True)
    try:
        return connection.execute(sql, list(params)).fetchall()
    finally:
        connection.close()


def _checksums(project: Project) -> dict[str, str]:
    connection = connect(project, read_only=True)
    try:
        return dict(table_checksums(connection))
    finally:
        connection.close()


def _manual(project: Project, n: int, record_id: str, field: str, value: str) -> str:
    event = ManualEntry(
        event_id=_ulid(n),
        ts=TS,
        project="fold",
        reviewer="kp",
        record_id=record_id,
        field=field,  # type: ignore[arg-type]
        value=value,
    )
    manual_entries_log(project.manual_entries_path).write_event(ManualRecord(event))
    return event.event_id


def _unmanual(project: Project, n: int, reverses: str) -> None:
    event = ManualReverse(
        event_id=_ulid(n),
        ts=TS,
        project="fold",
        reviewer="kp",
        reverses=reverses,
        reason="wrong_value",
    )
    manual_entries_log(project.manual_entries_path).write_event(ManualRecord(event))


def _digests(project: Project, record_id: str, field: str) -> tuple[str, str]:
    ((scopus, openalex),) = _sql(
        project,
        "SELECT scopus_value, openalex_value FROM provenance_conflicts "
        "WHERE record_id = ? AND field = ?",
        record_id,
        field,
    )
    return (
        hashlib.sha256(scopus.encode("utf-8")).hexdigest(),
        hashlib.sha256(openalex.encode("utf-8")).hexdigest(),
    )


def _resolve(
    project: Project,
    n: int,
    field: str,
    side: str,
    *,
    value: str | None = None,
    record_id: str = R1,
    digests: tuple[str, str] | None = None,
) -> str:
    scopus, openalex = digests or _digests(project, record_id, field)
    event = ConflictResolution(
        event_id=_ulid(n),
        ts=TS,
        project="fold",
        reviewer="kp",
        record_id=record_id,
        field=field,  # type: ignore[arg-type]
        chosen_side=side,  # type: ignore[arg-type]
        value=value,
        scopus_digest=scopus,
        openalex_digest=openalex,
    )
    conflict_resolutions_log(project.conflict_resolutions_path).write_event(ConflictRecord(event))
    return event.event_id


def _unresolve(project: Project, n: int, reverses: str) -> None:
    event = ConflictReverse(
        event_id=_ulid(n),
        ts=TS,
        project="fold",
        reviewer="kp",
        reverses=reverses,
        reason="wrong_side",
    )
    conflict_resolutions_log(project.conflict_resolutions_path).write_event(ConflictRecord(event))


def _conflict(project: Project, field: str) -> tuple[Any, ...]:
    (row,) = _sql(
        project,
        "SELECT status, resolved_side, resolution_event_id FROM provenance_conflicts "
        "WHERE record_id = ? AND field = ?",
        R1,
        field,
    )
    return row


def _prov(project: Project, record_id: str, field: str) -> tuple[Any, ...]:
    (row,) = _sql(
        project,
        f"SELECT {field}_source, {field}_level, {field}_outcome, {field}_payload_ref "
        "FROM records WHERE record_id = ?",
        record_id,
    )
    return row


def _build(project: Project) -> StoreStats:
    return build_store(project, rebuild=True)


# -- manual entry ----------------------------------------------------------------------------


@pytest.mark.integration
def test_manual__fills_an_unavailable_abstract_with_level_5_and_the_event_id(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    event_id = _manual(project, 1, R2, "abstract", "  Typed by\r\na reviewer.  ")
    _build(project)
    assert _prov(project, R2, "abstract") == (
        "manual",
        5,
        "value",
        f"decisions/manual_entries.jsonl#{event_id}",
    )
    # stored with \n line endings and no outer whitespace (Risk 3: CRLF is machine-dependent)
    assert _sql(project, "SELECT abstract FROM records WHERE record_id = ?", R2) == [
        ("Typed by\na reviewer.",)
    ]


@pytest.mark.integration
def test_manual__authors_and_affiliations__get_ids_derived_from_the_event(tmp_path: Path) -> None:
    project = _project(tmp_path)
    a = _manual(project, 1, R2, "authors", "Park, Min\nLee, Bo")
    b = _manual(
        project,
        2,
        R2,
        "affiliations",
        "MIT; Cambridge; United States\nSome Lab\nOdd; Town; Atlantis",
    )
    _build(project)
    assert _sql(
        project,
        "SELECT a.author_id, a.surname, a.given_name, ra.position FROM record_authors ra "
        "JOIN authors a USING (author_id) WHERE ra.record_id = ? ORDER BY ra.position",
        R2,
    ) == [(f"manual:{a}:1", "Park", "Min", 1), (f"manual:{a}:2", "Lee", "Bo", 2)]
    assert _sql(
        project,
        "SELECT a.afid, a.name, a.city, a.country_iso3 FROM record_affiliations ra "
        "JOIN affiliations a USING (afid) WHERE ra.record_id = ? ORDER BY a.afid",
        R2,
    ) == [
        (f"manual:{b}:1", "MIT", "Cambridge", "USA"),
        (f"manual:{b}:2", "Some Lab", None, None),
        (f"manual:{b}:3", "Odd", "Town", None),  # an unmatched country gives NULL, not the raw text
    ]
    assert _prov(project, R2, "authors")[:2] == ("manual", 5)
    assert _prov(project, R2, "affiliations")[:2] == ("manual", 5)


@pytest.mark.integration
def test_manual__entry_for_a_supplied_field__is_not_applied_and_is_counted(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _manual(project, 1, R1, "abstract", "A manual abstract that must not win")
    stats = _build(project)
    assert _sql(project, "SELECT abstract FROM records WHERE record_id = ?", R1) == [
        ("Scopus abstract one",)
    ]
    assert _prov(project, R1, "abstract")[0] == "scopus-complete"
    assert stats.provenance_report["manual_superseded"] == 1
    assert stats.provenance_report["manual_orphaned"] == 0


@pytest.mark.integration
def test_manual__entry_for_a_record_not_in_the_build__is_orphaned_and_never_fails(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _manual(project, 1, "scopus:2-s2.0-999", "abstract", "Nobody's abstract")
    stats = _build(project)
    assert stats.provenance_report["manual_orphaned"] == 1
    assert stats.records_loaded == 2


@pytest.mark.integration
def test_manual__two_active_entries_for_one_key__fail_the_build_naming_both(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    first = _manual(project, 1, R2, "abstract", "One")
    second = _manual(project, 2, R2, "abstract", "Two")
    with pytest.raises(StoreError) as info:
        _build(project)
    assert first in str(info.value)
    assert second in str(info.value)
    # reversing one unblocks the build, and the reversed entry is not applied
    _unmanual(project, 3, first)
    _build(project)
    assert _sql(project, "SELECT abstract FROM records WHERE record_id = ?", R2) == [("Two",)]


@pytest.mark.integration
def test_manual__reversed_entry__is_not_applied_and_the_field_is_unavailable_again(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    entry = _manual(project, 1, R2, "abstract", "Typed")
    _unmanual(project, 2, entry)
    _build(project)
    assert _prov(project, R2, "abstract") == ("unavailable", 0, "unavailable", None)


@pytest.mark.integration
def test_manual__reverse_naming_an_unknown_event__is_a_log_error(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _unmanual(project, 1, _ulid(99))
    with pytest.raises(LogError, match="not a manual"):
        _build(project)


@pytest.mark.integration
def test_manual__affiliation_line_with_more_than_three_parts__fails_naming_the_event(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    event_id = _manual(project, 1, R2, "affiliations", "A; B; C; D")
    with pytest.raises(StoreError, match=event_id):
        _build(project)


@pytest.mark.integration
def test_manual__keywords_have_no_manual_path(tmp_path: Path) -> None:
    # The model refuses the field, so no event can exist; the loader has nothing to fold.
    with pytest.raises(ValueError):
        ManualEntry(
            event_id=_ulid(1),
            ts=TS,
            project="fold",
            reviewer="kp",
            record_id=R2,
            field="keywords",  # type: ignore[arg-type]
            value="x",
        )


# -- conflict resolution ---------------------------------------------------------------------


@pytest.mark.integration
def test_resolution__openalex_abstract__replaces_the_value_and_its_provenance_not_layer0(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    page = project.raw_dir / SEARCH_RUN / "page-0000.jsonl"
    before = page.read_bytes()
    _build(project)
    assert _conflict(project, "abstract") == ("open", None, None)

    event_id = _resolve(project, 1, "abstract", "openalex")
    _build(project)

    assert _sql(project, "SELECT abstract FROM records WHERE record_id = ?", R1) == [
        ("An openalex abstract",)
    ]
    assert _prov(project, R1, "abstract") == ("openalex", 4, "value", OA_REF_1)
    assert _conflict(project, "abstract") == ("resolved", "openalex", event_id)
    assert page.read_bytes() == before  # the Scopus value stays in Layer 0
    # the other two conflicts were not touched
    assert _conflict(project, "authors") == ("open", None, None)


@pytest.mark.integration
def test_resolution__openalex__moves_the_count_from_level1_to_level4(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _build(project)
    query = "SELECT abstract_level, count(*) FROM records GROUP BY 1 ORDER BY 1"
    assert _sql(project, query) == [(0, 1), (1, 1)]
    _resolve(project, 1, "abstract", "openalex")
    _build(project)
    assert _sql(project, query) == [(0, 1), (4, 1)]


@pytest.mark.integration
def test_resolution__scopus__changes_no_value_and_only_marks_the_row_resolved(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _build(project)
    before = _sql(project, "SELECT * FROM records ORDER BY record_id")
    event_id = _resolve(project, 1, "abstract", "scopus")
    _build(project)
    assert _sql(project, "SELECT * FROM records ORDER BY record_id") == before
    assert _conflict(project, "abstract") == ("resolved", "scopus", event_id)


@pytest.mark.integration
def test_resolution__manual__takes_the_reviewers_value_with_the_event_as_its_reference(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _build(project)
    event_id = _resolve(project, 1, "abstract", "manual", value="The reviewer's own abstract")
    _build(project)
    assert _sql(project, "SELECT abstract FROM records WHERE record_id = ?", R1) == [
        ("The reviewer's own abstract",)
    ]
    assert _prov(project, R1, "abstract") == (
        "manual",
        5,
        "value",
        f"decisions/conflict_resolutions.jsonl#{event_id}",
    )
    assert _conflict(project, "abstract") == ("resolved", "manual", event_id)


@pytest.mark.integration
def test_resolution__reversal__reopens_the_row_restores_scopus_and_stays_open(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _build(project)
    scopus_row = _sql(project, "SELECT * FROM records ORDER BY record_id")
    event_id = _resolve(project, 1, "abstract", "openalex")
    _build(project)
    assert _conflict(project, "abstract")[0] == "resolved"

    _unresolve(project, 2, event_id)
    _build(project)

    assert _conflict(project, "abstract") == ("open", None, None)
    assert _sql(project, "SELECT * FROM records ORDER BY record_id") == scopus_row
    _build(project)  # and it stays open until a new resolution is written
    assert _conflict(project, "abstract") == ("open", None, None)


@pytest.mark.integration
def test_resolution__two_active_for_one_key__fail_the_build_naming_both(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _build(project)
    first = _resolve(project, 1, "abstract", "openalex")
    second = _resolve(project, 2, "abstract", "scopus")
    with pytest.raises(StoreError) as info:
        _build(project)
    assert first in str(info.value)
    assert second in str(info.value)
    _unresolve(project, 3, first)
    _build(project)
    assert _conflict(project, "abstract") == ("resolved", "scopus", second)


@pytest.mark.integration
def test_resolution__for_a_key_with_no_conflict_row__is_orphaned_and_never_fails(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _build(project)
    digest = "0" * 64
    _resolve(project, 1, "abstract", "scopus", record_id=R2, digests=(digest, digest))
    stats = _build(project)
    assert stats.provenance_report["conflict_resolution_orphaned"] == 1
    assert stats.provenance_report["conflict_resolution_stale"] == 0


@pytest.mark.integration
@pytest.mark.parametrize(
    ("side", "index"), [("scopus", 0), ("openalex", 1), ("manual", 0), ("manual", 1)]
)
def test_resolution__digest_of_a_named_side_that_differs__is_stale_and_never_applied(
    tmp_path: Path, side: str, index: int
) -> None:
    project = _project(tmp_path)
    _build(project)
    good = list(_digests(project, R1, "abstract"))
    good[index] = "f" * 64  # one side no longer matches what the reviewer saw
    value = "manual text" if side == "manual" else None
    _resolve(project, 1, "abstract", side, value=value, digests=(good[0], good[1]))
    stats = _build(project)
    assert stats.provenance_report["conflict_resolution_stale"] == 1
    assert _conflict(project, "abstract") == ("open", None, None)
    assert _sql(project, "SELECT abstract FROM records WHERE record_id = ?", R1) == [
        ("Scopus abstract one",)
    ]


@pytest.mark.integration
def test_resolution__digest_of_the_side_not_named__is_not_consulted(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _build(project)
    _, openalex = _digests(project, R1, "abstract")
    _resolve(project, 1, "abstract", "openalex", digests=("f" * 64, openalex))
    stats = _build(project)
    assert stats.provenance_report["conflict_resolution_stale"] == 0
    assert _conflict(project, "abstract")[:2] == ("resolved", "openalex")


@pytest.mark.integration
def test_resolution__value_changed_by_one_character__is_detected_as_stale(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _build(project)
    _resolve(project, 1, "abstract", "openalex")
    _oa(project, OA_2, "An openalex abstract.")  # a later run: one character more
    stats = _build(project)
    assert stats.provenance_report["conflict_resolution_stale"] == 1
    assert _conflict(project, "abstract") == ("open", None, None)
    assert _sql(project, "SELECT abstract FROM records WHERE record_id = ?", R1) == [
        ("Scopus abstract one",)
    ]


@pytest.mark.integration
def test_resolution__openalex_affiliations__rewrites_rows_with_countries_from_the_body(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _build(project)
    event_id = _resolve(project, 1, "affiliations", "openalex")
    _build(project)
    assert _sql(
        project,
        "SELECT a.afid, a.name, a.city, a.country_iso3 FROM record_affiliations ra "
        "JOIN affiliations a USING (afid) WHERE ra.record_id = ? ORDER BY a.afid",
        R1,
    ) == [
        (f"resolved:{event_id}:1", "Harvard University", None, "USA"),
        (f"resolved:{event_id}:2", "Seoul National University", None, "KOR"),
    ]
    assert _prov(project, R1, "affiliations") == ("openalex", 4, "value", OA_REF_1)
    # the replaced Scopus institution is referenced by no record, so it is not left behind
    assert _sql(project, "SELECT count(*) FROM affiliations WHERE afid = '60001'") == [(0,)]


@pytest.mark.integration
def test_resolution__scopus_affiliations__leaves_the_scopus_rows_unchanged(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _build(project)
    rows = _sql(project, "SELECT * FROM record_affiliations ORDER BY 1, 2")
    _resolve(project, 1, "affiliations", "scopus")
    _build(project)
    assert _sql(project, "SELECT * FROM record_affiliations ORDER BY 1, 2") == rows
    assert _sql(project, "SELECT name, country_iso3 FROM affiliations") == [(MIT_NAME, "USA")]


@pytest.mark.integration
def test_resolution__openalex_authors__rewrites_rows_with_the_comparison_surname(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _build(project)
    event_id = _resolve(project, 1, "authors", "openalex")
    _build(project)
    assert _sql(
        project,
        "SELECT a.author_id, a.surname, a.given_name, ra.position FROM record_authors ra "
        "JOIN authors a USING (author_id) WHERE ra.record_id = ? ORDER BY ra.position",
        R1,
    ) == [
        (f"resolved:{event_id}:1", "Smith", None, 1),
        (f"resolved:{event_id}:2", "Park", None, 2),
    ]
    assert _sql(project, "SELECT count(*) FROM authors WHERE author_id IN ('A1', 'A2')") == [(0,)]


@pytest.mark.integration
def test_resolution__manual_affiliations__are_parsed_with_the_manual_line_shape(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _build(project)
    event_id = _resolve(
        project, 1, "affiliations", "manual", value="Lab One; Paris; France\nLab Two"
    )
    _build(project)
    assert _sql(
        project,
        "SELECT a.afid, a.name, a.city, a.country_iso3 FROM record_affiliations ra "
        "JOIN affiliations a USING (afid) WHERE ra.record_id = ? ORDER BY a.afid",
        R1,
    ) == [
        (f"resolved:{event_id}:1", "Lab One", "Paris", "FRA"),
        (f"resolved:{event_id}:2", "Lab Two", None, None),
    ]


@pytest.mark.integration
def test_resolution__reverse_naming_an_unknown_event__is_a_log_error(tmp_path: Path) -> None:
    project = _project(tmp_path)
    _unresolve(project, 1, _ulid(99))
    with pytest.raises(LogError, match="not a conflict"):
        _build(project)


@pytest.mark.integration
def test_rebuild__with_manual_entries_resolutions_and_a_reversal__reproduces_every_checksum(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    _build(project)
    _manual(project, 1, R2, "authors", "Park, Min")
    first = _resolve(project, 2, "abstract", "openalex")
    _unresolve(project, 3, first)
    _resolve(project, 4, "affiliations", "openalex")
    _resolve(project, 5, "authors", "manual", value="Someone Else")
    _build(project)
    once = _checksums(project)
    _build(project)
    assert _checksums(project) == once
    project.db_path.unlink()
    build_store(project)
    assert _checksums(project) == once
    assert _conflict(project, "abstract") == ("open", None, None)
    assert _conflict(project, "affiliations")[:2] == ("resolved", "openalex")
    assert _conflict(project, "authors")[:2] == ("resolved", "manual")
