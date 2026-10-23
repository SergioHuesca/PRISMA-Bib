"""Unit tests for the registration events and the body-to-columns derivation (ADR 0029, 0032)."""

from __future__ import annotations

import copy
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from prismabib.capture import registrations
from prismabib.capture.registrations import (
    RECORD_ID_PREFIX,
    REVERSAL_REASONS,
    PayloadAddress,
    RegisterEvent,
    RegistrationRecord,
    RegistrationRefusal,
    ReverseEvent,
    SupersedeEvent,
    active_registrations,
    classify_work,
)
from prismabib.errors import LogError
from prismabib.openalex_map import (
    MAP_SHA256,
    MAP_VERSION,
    SOURCE_TYPE_TO_VENUE_TYPE,
    SUBFIELD_TO_ASJC,
    TYPE_TO_DOC_TYPE,
    ClassificationRefusal,
)

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "openalex" / "work_registration.json"
TS = datetime(2026, 10, 23, 12, 0, tzinfo=UTC)


def _works() -> list[dict[str, Any]]:
    return copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))["results"])


def _register(event_id: str = "E1", **overrides: Any) -> RegisterEvent:
    fields: dict[str, Any] = {
        "event_id": event_id,
        "ts": TS,
        "project": "p",
        "record_id": RECORD_ID_PREFIX + event_id,
        "candidate_id": "c1",
        "reviewer": "sh",
        "match_rule": "doi",
        "source_level": 4,
        "source_payload_ref": PayloadAddress(
            file="openalex/r/response-0000.jsonl", openalex_id="https://openalex.org/W1"
        ),
        "source_payload_sha256": "0" * 64,
        "doi": "10.9999/x",
        "subject_map_version": MAP_VERSION,
        "subject_map_sha256": MAP_SHA256,
    }
    fields.update(overrides)
    return RegisterEvent(**fields)


def _reverse(event_id: str, target: str) -> ReverseEvent:
    return ReverseEvent(
        event_id=event_id, ts=TS, project="p", reviewer="sh", reverses=target, reason="not_eligible"
    )


def _supersede(event_id: str, target: str) -> SupersedeEvent:
    return SupersedeEvent(
        event_id=event_id,
        ts=TS,
        project="p",
        reverses=target,
        scopus_record_id="scopus:2-s2.0-1",
        match_rule="doi",
    )


# -- the recorded-works fixture and the map ----------------------------------------------------


@pytest.mark.unit
def test_registration__source_type_path__matches_recorded_works_fixture() -> None:
    """ADR 0032 Decision 3(c): the path ``primary_location.source.type`` exists on a work.

    The fixture is synthetic (see its ``_comment``); the test pins the path the code reads and
    that every type, source type and subfield in it is covered by the map.
    """
    works = _works()
    expected = {
        "article": ("article", "journal", "1702"),
        "conference-paper": ("conference paper", "conference", "2208"),
        "book-chapter": ("book chapter", "book", "3312"),
    }
    for work in works:
        assert work["primary_location"]["source"]["type"] in SOURCE_TYPE_TO_VENUE_TYPE
        assert work["type"] in TYPE_TO_DOC_TYPE
        subfield_id = work["primary_topic"]["subfield"]["id"]
        assert subfield_id.rsplit("/", 1)[1] in SUBFIELD_TO_ASJC
        derived = classify_work(work)
        got = (derived.doc_type, derived.venue_type, derived.subject_code)
        assert got == expected[work["type"]]
    assert {w["type"] for w in works} == set(expected)


@pytest.mark.unit
def test_classify_work__fixture_article__derives_every_column() -> None:
    derived = classify_work(_works()[0])

    assert derived.doi == "10.9999/synthetic.reg.one"
    assert derived.venue_name == "Journal of Synthetic Registrations"
    assert derived.abstract == "A synthetic abstract"
    assert (derived.title, derived.year) == ("A Synthetic Registered Article", 2021)


@pytest.mark.unit
@pytest.mark.parametrize("name", ["", "   ", "\t\n"])
def test_classify_work__whitespace_venue_name__is_refused(name: str) -> None:
    work = _works()[0]
    work["primary_location"]["source"]["display_name"] = name

    with pytest.raises(RegistrationRefusal, match="venue_name"):
        classify_work(work)


@pytest.mark.unit
@pytest.mark.parametrize("shape", ["no_key", "null", "not_a_string"])
def test_classify_work__missing_venue_name__is_refused(shape: str) -> None:
    work = _works()[0]
    source = work["primary_location"]["source"]
    if shape == "no_key":
        del source["display_name"]
    else:
        source["display_name"] = None if shape == "null" else 7

    with pytest.raises(RegistrationRefusal, match="venue_name"):
        classify_work(work)


@pytest.mark.unit
def test_classify_work__padded_venue_name__is_stored_stripped() -> None:
    work = _works()[1]
    work["primary_location"]["source"]["display_name"] = "  Proceedings of X \n"

    assert classify_work(work).venue_name == "Proceedings of X"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda w: w.update(type="preprint"), "type 'preprint' is not in classification map"),
        (lambda w: w.pop("type"), "type None is not in classification map"),
        (
            lambda w: w["primary_location"]["source"].update(type="podcast"),
            "source type 'podcast' is not in classification map",
        ),
        (
            lambda w: w["primary_location"].update(source=None),
            "source type None is not in classification map",
        ),
        (
            lambda w: w["primary_topic"]["subfield"].update(id="https://openalex.org/subfields/9"),
            "subfield 'https://openalex.org/subfields/9' is not in classification map",
        ),
        (lambda w: w.update(primary_topic=None), "primary_topic.subfield.id is missing"),
    ],
)
def test_classify_work__value_outside_the_map__is_refused_naming_input_and_value(
    mutate: Any, message: str
) -> None:
    work = _works()[0]
    mutate(work)

    with pytest.raises(ClassificationRefusal, match=re.escape(message)):
        classify_work(work)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda w: w.update(doi=None), "no DOI"),
        (lambda w: w.update(title="  "), "no title"),
        (lambda w: w.update(publication_year=None), "no publication_year"),
    ],
)
def test_classify_work__missing_identity_field__is_refused(mutate: Any, message: str) -> None:
    work = _works()[0]
    mutate(work)

    with pytest.raises(RegistrationRefusal, match=message):
        classify_work(work)


# -- the events ----------------------------------------------------------------------------------


@pytest.mark.unit
def test_register_event__record_id_not_registered_plus_event_id__is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="record_id must be exactly"):
        _register(record_id="registered:other")
    with pytest.raises(PydanticValidationError, match="record_id must be exactly"):
        _register(record_id="scopus:E1")


@pytest.mark.unit
@pytest.mark.parametrize("level", [1, 2, 3, 5])
def test_register_event__source_level_other_than_four__is_refused_on_load(level: int) -> None:
    line = _register().model_dump(mode="json") | {"source_level": level}

    with pytest.raises(PydanticValidationError):
        RegistrationRecord.model_validate(line)


@pytest.mark.unit
def test_register_event__blank_doi_or_reviewer__is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="requires a DOI"):
        _register(doi=" ")
    with pytest.raises(PydanticValidationError, match="reviewer must not be blank"):
        _register(reviewer=" ")


@pytest.mark.unit
def test_register_event__serialised__is_utc_millis_with_z_and_posix_ref() -> None:
    dumped = json.loads(_register().model_dump_json())

    assert dumped["ts"] == "2026-10-23T12:00:00.000Z"
    assert dumped["schema_version"] == 1
    assert dumped["action"] == "register"
    assert dumped["source_payload_ref"] == {
        "file": "openalex/r/response-0000.jsonl",
        "line": 0,
        "openalex_id": "https://openalex.org/W1",
    }


@pytest.mark.unit
def test_registration_record__discriminated_by_action__round_trips_each_kind() -> None:
    for event in (_register(), _reverse("E2", "E1"), _supersede("E3", "E1")):
        raw = json.loads(event.model_dump_json())
        assert RegistrationRecord.model_validate(raw).root == event


@pytest.mark.unit
def test_reversal_reasons__closed_set__is_the_adr_0033_set() -> None:
    assert REVERSAL_REASONS == (
        "wrong_doi",
        "wrong_title",
        "duplicate_of_scopus",
        "not_eligible",
        "other",
    )


@pytest.mark.unit
def test_reverse_event__reason_outside_closed_set_or_other_without_note__is_refused() -> None:
    base: dict[str, Any] = {"event_id": "E2", "ts": TS, "project": "p", "reviewer": "sh"}
    with pytest.raises(PydanticValidationError):
        ReverseEvent(**base, reverses="E1", reason="mistake")
    with pytest.raises(PydanticValidationError, match="requires a non-empty note"):
        ReverseEvent(**base, reverses="E1", reason="other", note="  ")
    assert ReverseEvent(**base, reverses="E1", reason="other", note="why").note == "why"


@pytest.mark.unit
def test_supersede_event__non_scopus_id__is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="must begin 'scopus:'"):
        SupersedeEvent(
            event_id="E3",
            ts=TS,
            project="p",
            reverses="E1",
            scopus_record_id="registered:E9",
            match_rule="doi",
        )


@pytest.mark.unit
def test_event__naive_ts__is_refused() -> None:
    with pytest.raises(PydanticValidationError, match="timezone-aware"):
        _register(ts=datetime(2026, 10, 23, 12, 0))  # noqa: DTZ001  (naive on purpose)


# -- ADR 0029 V2: active registrations ---------------------------------------------------------


@pytest.mark.unit
def test_active_registrations__none__is_empty() -> None:
    assert active_registrations([]) == []


@pytest.mark.unit
def test_active_registrations__one_register__is_active() -> None:
    assert [e.event_id for e in active_registrations([_register("E1")])] == ["E1"]


@pytest.mark.unit
def test_active_registrations__register_then_reverse__is_empty() -> None:
    assert active_registrations([_register("E1"), _reverse("E2", "E1")]) == []


@pytest.mark.unit
def test_active_registrations__register_reverse_register__is_the_new_one_only() -> None:
    events = [_register("E1"), _reverse("E2", "E1"), _register("E3")]

    assert [e.record_id for e in active_registrations(events)] == ["registered:E3"]


@pytest.mark.unit
def test_active_registrations__register_then_supersede__is_empty() -> None:
    assert active_registrations([_register("E1"), _supersede("E2", "E1")]) == []


@pytest.mark.unit
def test_active_registrations__removal_naming_unknown_event__raises_log_error() -> None:
    with pytest.raises(LogError, match="not a register event"):
        active_registrations([_register("E1"), _reverse("E2", "NOPE")])


# -- ADR 0029 section 5 / V6 -------------------------------------------------------------------


@pytest.mark.unit
def test_registrations_module__source__never_imports_decisionlog_or_calls_write_event() -> None:
    source = Path(registrations.__file__).read_text(encoding="utf-8")

    assert "DecisionLog" not in source
    assert ".write_event(" not in source
    assert "append_event" not in source
    assert ".append_if(" in source
