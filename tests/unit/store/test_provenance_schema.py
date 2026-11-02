"""ADR 0041 Decisions 2, 3 and 4: the provenance columns, their CHECKs, the event models.

Stage 1 of PR3. Every test names the defect it fails on.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from prismabib.countries import _ISO_SHORT_NAMES, ALPHA2_TO_ALPHA3, normalise_country
from prismabib.models import (
    ConflictRecord,
    ConflictResolution,
    ConflictReverse,
    ManualEntry,
    ManualRecord,
    ManualReverse,
    conflict_resolutions_log,
    load_conflict_events,
    load_manual_events,
    manual_entries_log,
)
from prismabib.project import Project
from prismabib.store.checksums import TABLE_NAMES
from tests.store_helpers import create_schema

FIELDS = ("abstract", "authors", "affiliations", "keywords")
SOURCES = {
    "abstract": [("scopus-complete", 1), ("openalex", 4), ("manual", 5)],
    "authors": [("scopus-complete", 1), ("openalex", 4), ("manual", 5)],
    "affiliations": [
        ("scopus-complete", 1),
        ("scopus-standard", 2),
        ("scopus-meta", 3),
        ("openalex", 4),
        ("manual", 5),
    ],
    "keywords": [("scopus-complete", 1), ("openalex-topic", 4)],
}
SHA = "a" * 64
ULID_1 = "01JCZ0000000000000000000A1"
ULID_2 = "01JCZ0000000000000000000A2"
TS = "2026-11-02T12:00:00.000Z"


def _connection() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    create_schema(connection)
    return connection


def _insert_record(connection: duckdb.DuckDBPyConnection, **override: object) -> None:
    row: dict[str, object] = {"record_id": "scopus:1"}
    for field in FIELDS:
        row |= {
            f"{field}_source": "unavailable",
            f"{field}_level": 0,
            f"{field}_outcome": "unavailable",
            f"{field}_payload_ref": None,
        }
    row |= override
    columns = ", ".join(row)
    connection.execute(
        f"INSERT INTO records ({columns}) VALUES ({', '.join('?' for _ in row)})",
        list(row.values()),
    )


@pytest.mark.unit
def test_records__all_unavailable__is_accepted() -> None:
    connection = _connection()
    _insert_record(connection)
    assert connection.execute("SELECT count(*) FROM records").fetchone() == (1,)


@pytest.mark.unit
def test_records__sixteen_provenance_columns_follow_payload_line_in_order() -> None:
    connection = _connection()
    names = [r[1] for r in connection.execute("PRAGMA table_info('records')").fetchall()]
    expected = [f"{f}_{s}" for f in FIELDS for s in ("source", "level", "outcome", "payload_ref")]
    assert names[-16:] == expected
    assert names[-17] == "payload_line"


@pytest.mark.unit
@pytest.mark.parametrize(("field", "pair"), [(f, p) for f in FIELDS for p in SOURCES[f]])
def test_records__every_allowed_source_level_pair__is_accepted(
    field: str, pair: tuple[str, int]
) -> None:
    connection = _connection()
    source, level = pair
    _insert_record(
        connection,
        **{
            f"{field}_source": source,
            f"{field}_level": level,
            f"{field}_outcome": "value",
            f"{field}_payload_ref": "run/page-0000.jsonl#0",
        },
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field", "source", "level"),
    [
        ("abstract", "scopus-standard", 2),  # levels 2 and 3 never set abstract (Decision 1e)
        ("abstract", "scopus-meta", 3),
        ("authors", "scopus-standard", 2),
        ("keywords", "scopus-meta", 3),
        ("keywords", "manual", 5),  # keywords take no manual value
        ("keywords", "openalex", 4),  # the keyword label is openalex-topic
        ("affiliations", "openalex-topic", 4),
        ("abstract", "scopus-complete", 4),  # source and level are tied
        ("abstract", "openalex", 1),
        ("abstract", "bogus", 1),
    ],
)
def test_records__pair_outside_the_closed_set__is_refused(
    field: str, source: str, level: int
) -> None:
    connection = _connection()
    with pytest.raises(duckdb.ConstraintException):
        _insert_record(
            connection,
            **{
                f"{field}_source": source,
                f"{field}_level": level,
                f"{field}_outcome": "value",
                f"{field}_payload_ref": "x#0",
            },
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "override",
    [
        {"abstract_outcome": "value"},  # unavailable source, value outcome, no ref
        {"abstract_payload_ref": "x#0"},  # unavailable with a reference
        {"abstract_source": "openalex", "abstract_level": 4, "abstract_outcome": "unavailable"},
        {"abstract_source": "openalex", "abstract_level": 4, "abstract_outcome": "value"},  # no ref
        {"abstract_outcome": "absent"},  # attempt outcomes are not Layer 1 outcomes
    ],
)
def test_records__outcome_and_reference_out_of_step__is_refused(
    override: dict[str, object],
) -> None:
    connection = _connection()
    with pytest.raises(duckdb.ConstraintException):
        _insert_record(connection, **override)


@pytest.mark.unit
def test_records__null_provenance_cell__is_refused() -> None:
    connection = _connection()
    with pytest.raises(duckdb.ConstraintException):
        _insert_record(connection, authors_source=None)


# -- provenance_conflicts --------------------------------------------------------------------


def _conflict(connection: duckdb.DuckDBPyConnection, **override: object) -> None:
    row: dict[str, object] = {
        "record_id": "scopus:1",
        "field": "abstract",
        "scopus_level": 1,
        "scopus_source": "scopus-complete",
        "scopus_payload_ref": "r/page-0000.jsonl#0",
        "scopus_value": '"a"',
        "openalex_payload_ref": "openalex/o/response-0000.jsonl#W1",
        "openalex_value": '"b"',
        "status": "open",
        "resolved_side": None,
        "resolution_event_id": None,
    }
    row |= override
    connection.execute(
        f"INSERT INTO provenance_conflicts ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
        list(row.values()),
    )


@pytest.mark.unit
def test_provenance_conflicts__has_eleven_columns_and_a_checksum_sort_key() -> None:
    connection = _connection()
    columns = connection.execute("PRAGMA table_info('provenance_conflicts')").fetchall()
    assert len(columns) == 11
    assert "provenance_conflicts" in TABLE_NAMES


@pytest.mark.unit
@pytest.mark.parametrize(
    "override",
    [
        {"status": "resolved", "resolved_side": "openalex", "resolution_event_id": ULID_1},
        {"scopus_level": 2, "scopus_source": "scopus-standard"},
        {"scopus_level": 3, "scopus_source": "scopus-meta"},
    ],
)
def test_provenance_conflicts__consistent_rows__are_accepted(override: dict[str, object]) -> None:
    _conflict(_connection(), **override)


@pytest.mark.unit
@pytest.mark.parametrize(
    "override",
    [
        {"status": "resolved"},  # resolved without a resolution
        {"status": "resolved", "resolved_side": "openalex"},  # no event id
        {"status": "open", "resolution_event_id": ULID_1},  # open with an event id
        {"status": "open", "resolved_side": "scopus"},  # open with a side
        {"status": "pending"},
        {"status": "resolved", "resolved_side": "nobody", "resolution_event_id": ULID_1},
        {"scopus_level": 4, "scopus_source": "openalex"},
        {"scopus_level": 1, "scopus_source": "scopus-standard"},  # pair mismatch
        {"scopus_level": 0, "scopus_source": "unavailable"},
    ],
)
def test_provenance_conflicts__inconsistent_rows__are_refused(override: dict[str, object]) -> None:
    with pytest.raises(duckdb.ConstraintException):
        _conflict(_connection(), **override)


@pytest.mark.unit
def test_provenance_conflicts__second_row_for_one_key__is_refused() -> None:
    connection = _connection()
    _conflict(connection)
    with pytest.raises(duckdb.ConstraintException):
        _conflict(connection)


# -- registered_records and records_all ------------------------------------------------------


@pytest.mark.unit
def test_registered_records__ends_with_three_nullable_json_columns() -> None:
    connection = _connection()
    info = connection.execute("PRAGMA table_info('registered_records')").fetchall()
    assert [r[1] for r in info[-3:]] == ["authors_json", "affiliations_json", "keywords_json"]
    assert not any(r[3] for r in info[-3:])


@pytest.mark.unit
def test_records_all__carries_the_sixteen_columns_for_both_origins() -> None:
    connection = _connection()
    _insert_record(
        connection,
        abstract_source="manual",
        abstract_level=5,
        abstract_outcome="value",
        abstract_payload_ref="decisions/manual_entries.jsonl#E",
    )
    connection.execute(
        "INSERT INTO registered_records VALUES ('registered:e','e','10.1/x','T','An abstract',"
        "2020,'article',NULL,'journal','J','https://openalex.org/W9','openalex/o/response-0000.jsonl',"
        "0,'doi','kp','2026-01-01 00:00:00','[\"Ann Lee\"]',NULL,'[\"Graphs\"]')"
    )
    cols = [r[1] for r in connection.execute("PRAGMA table_info('records_all')").fetchall()]
    assert cols[-16:] == [
        f"{f}_{s}" for f in FIELDS for s in ("source", "level", "outcome", "payload_ref")
    ]
    scopus = connection.execute(
        "SELECT abstract_source, abstract_level, authors_source FROM records_all "
        "WHERE origin = 'scopus'"
    ).fetchone()
    assert scopus == ("manual", 5, "unavailable")
    reg = connection.execute(
        "SELECT abstract_source, abstract_level, abstract_outcome, abstract_payload_ref, "
        "authors_source, authors_level, authors_outcome, authors_payload_ref, "
        "affiliations_source, affiliations_level, affiliations_outcome, affiliations_payload_ref, "
        "keywords_source, keywords_level, keywords_outcome, keywords_payload_ref "
        "FROM records_all WHERE origin = 'registered'"
    ).fetchone()
    ref = "openalex/o/response-0000.jsonl#https://openalex.org/W9"
    assert reg == (
        "openalex", 4, "value", ref,
        "openalex", 4, "value", ref,
        "unavailable", 0, "unavailable", None,
        "openalex-topic", 4, "value", ref,
    )  # fmt: skip


# -- countries -------------------------------------------------------------------------------


@pytest.mark.unit
def test_alpha2_to_alpha3__every_target_is_an_iso_code_the_table_knows() -> None:
    assert set(ALPHA2_TO_ALPHA3.values()) <= set(_ISO_SHORT_NAMES.values())
    assert all(len(k) == 2 and k.isupper() for k in ALPHA2_TO_ALPHA3)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("alpha2", "alpha3"),
    [("US", "USA"), ("GB", "GBR"), ("KR", "KOR"), ("VN", "VNM"), ("DE", "DEU"), ("CN", "CHN")],
)
def test_alpha2_to_alpha3__common_codes(alpha2: str, alpha3: str) -> None:
    assert ALPHA2_TO_ALPHA3[alpha2] == alpha3


@pytest.mark.unit
def test_normalise_country__name_and_alpha3__map_and_unmapped_is_kept() -> None:
    assert normalise_country("South Korea") == ("KOR", True)
    assert normalise_country("KOR") == ("KOR", True)
    assert normalise_country("Atlantis") == ("Atlantis", False)


# -- event models ----------------------------------------------------------------------------


def _manual(**override: object) -> dict[str, object]:
    return {
        "event_id": ULID_1,
        "ts": TS,
        "project": "p",
        "reviewer": "kp",
        "action": "manual_entry",
        "record_id": "scopus:2-s2.0-1",
        "field": "abstract",
        "value": "Text\nmore",
        "doi": None,
    } | override


@pytest.mark.unit
def test_manual_entry__valid__round_trips_with_a_utc_millisecond_ts() -> None:
    event = ManualEntry.model_validate(_manual())
    assert event.schema_version == 1
    assert '"ts":"2026-11-02T12:00:00.000Z"' in event.model_dump_json()


@pytest.mark.unit
@pytest.mark.parametrize(
    "override",
    [
        {"field": "keywords"},
        {"field": "title"},
        {"action": "reverse"},
        {"reviewer": " "},
        {"event_id": ""},
        {"ts": "2026-11-02T12:00:00"},  # naive
        {"schema_version": 2},
        {"unknown": 1},
        {"value": "  "},
    ],
)
def test_manual_entry__invalid__is_refused(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ManualEntry.model_validate(_manual(**override))


@pytest.mark.unit
def test_manual_reverse__reasons_are_a_closed_set_and_other_needs_a_note() -> None:
    base = {
        "event_id": ULID_2,
        "ts": TS,
        "project": "p",
        "reviewer": "kp",
        "action": "reverse",
        "reverses": ULID_1,
    }
    for reason in ("wrong_value", "wrong_field", "duplicate_entry"):
        assert ManualReverse.model_validate(base | {"reason": reason}).reason == reason
    with pytest.raises(ValidationError):
        ManualReverse.model_validate(base | {"reason": "wrong_side"})
    with pytest.raises(ValidationError):
        ManualReverse.model_validate(base | {"reason": "other"})
    ManualReverse.model_validate(base | {"reason": "other", "note": "why"})


def _resolution(**override: object) -> dict[str, object]:
    return {
        "event_id": ULID_1,
        "ts": TS,
        "project": "p",
        "reviewer": "kp",
        "action": "conflict_resolution",
        "record_id": "scopus:2-s2.0-1",
        "field": "authors",
        "chosen_side": "openalex",
        "scopus_digest": SHA,
        "openalex_digest": SHA,
    } | override


@pytest.mark.unit
def test_conflict_resolution__valid_sides__are_accepted() -> None:
    for side in ("scopus", "openalex"):
        assert ConflictResolution.model_validate(_resolution(chosen_side=side)).value is None
    assert (
        ConflictResolution.model_validate(_resolution(chosen_side="manual", value="Lee, Ann")).value
        == "Lee, Ann"
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "override",
    [
        {"chosen_side": "manual"},  # a manual side needs a value
        {"chosen_side": "manual", "value": " "},
        {"chosen_side": "nobody"},
        {"field": "keywords"},  # keywords never conflict
        {"scopus_digest": "A" * 64},  # lowercase hex only
        {"openalex_digest": "abc"},
        {"scopus_digest": "g" * 64},
    ],
)
def test_conflict_resolution__invalid__is_refused(override: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ConflictResolution.model_validate(_resolution(**override))


@pytest.mark.unit
def test_conflict_reverse__reasons_are_wrong_side_wrong_value_other() -> None:
    base = {
        "event_id": ULID_2,
        "ts": TS,
        "project": "p",
        "reviewer": "kp",
        "action": "reverse",
        "reverses": ULID_1,
    }
    for reason in ("wrong_side", "wrong_value"):
        ConflictReverse.model_validate(base | {"reason": reason})
    with pytest.raises(ValidationError):
        ConflictReverse.model_validate(base | {"reason": "duplicate_entry"})


@pytest.mark.unit
def test_project__layer2_paths__sit_beside_the_registrations_log(tmp_path: Path) -> None:
    project = Project.init("pp", title="P", root=tmp_path)
    assert project.manual_entries_path == project.root / "decisions" / "manual_entries.jsonl"
    assert (
        project.conflict_resolutions_path
        == project.root / "decisions" / "conflict_resolutions.jsonl"
    )
    assert project.manual_entries_path.parent == project.registrations_path.parent


@pytest.mark.unit
def test_logs__append_and_read_back_through_append_only_log(tmp_path: Path) -> None:
    manual = tmp_path / "manual_entries.jsonl"
    conflicts = tmp_path / "conflict_resolutions.jsonl"
    assert load_manual_events(manual) == []  # a missing file is empty and is not created
    assert load_conflict_events(conflicts) == []
    assert not manual.exists()
    entry = ManualEntry.model_validate(_manual())
    reverse = ManualReverse.model_validate(
        {
            "event_id": ULID_2,
            "ts": TS,
            "project": "p",
            "reviewer": "kp",
            "action": "reverse",
            "reverses": ULID_1,
            "reason": "wrong_value",
        }
    )
    log = manual_entries_log(manual)
    log.write_event(ManualRecord(entry))
    log.write_event(ManualRecord(reverse))
    assert load_manual_events(manual) == [entry, reverse]
    resolution = ConflictResolution.model_validate(_resolution())
    conflict_resolutions_log(conflicts).write_event(ConflictRecord(resolution))
    assert load_conflict_events(conflicts) == [resolution]
