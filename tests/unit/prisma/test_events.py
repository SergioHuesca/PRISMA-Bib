"""Unit tests for :mod:`prismabib.prisma.events` (BUILD_PLAN §Stage 4, lines 952-973).

The event schema is pure: no filesystem, no store, no network. The only
double used anywhere here is ``time-machine`` -- the clock boundary §3.7.3
rule 1 explicitly permits -- and it is used only to pin
:class:`~prismabib.prisma.events.MonotonicUlidFactory`'s notion of "the same
millisecond", which is the exact condition its monotonicity guarantee is
about.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
import time_machine
from pydantic import ValidationError as PydanticValidationError

from prismabib.prisma.events import (
    CURRENT_SCHEMA_VERSION,
    DecisionEvent,
    MonotonicUlidFactory,
)
from prismabib.stage import PrismaStage

BUILD_PLAN_INSTANT = datetime(2026, 1, 18, 14, 22, 7, 412000, tzinfo=UTC)


def make_event(**overrides: object) -> DecisionEvent:
    """Build a valid event, overriding named fields (helper, not a test)."""
    fields: dict[str, object] = {
        "event_id": "01HV7000000000000000000000",
        "ts": BUILD_PLAN_INSTANT,
        "project": "vad-2026",
        "stage": PrismaStage.TITLE_ABSTRACT,
        "record_id": "scopus:2-s2.0-85101234567",
        "reviewer": "kp",
        "decision": "exclude",
        "reason_code": "REVIEW_OR_SURVEY",
        "criteria_version": "1.0.0",
    }
    fields.update(overrides)
    return DecisionEvent(**fields)  # type: ignore[arg-type]


@pytest.mark.unit
def test_event__build_plan_example__round_trips_through_json_unchanged() -> None:
    event = make_event()

    restored = DecisionEvent.model_validate_json(event.model_dump_json())

    assert restored == event


@pytest.mark.unit
def test_event__serialised_ts__is_millisecond_utc_with_a_z_suffix() -> None:
    event = make_event(ts=BUILD_PLAN_INSTANT)

    payload = event.model_dump_json()

    assert '"ts":"2026-01-18T14:22:07.412Z"' in payload


@pytest.mark.unit
def test_event__non_utc_ts__is_normalised_to_utc() -> None:
    tehran = timezone(timedelta(hours=3, minutes=30))

    event = make_event(ts=datetime(2026, 1, 18, 17, 52, 7, 412000, tzinfo=tehran))

    assert event.ts == BUILD_PLAN_INSTANT


@pytest.mark.unit
def test_event__naive_ts__is_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="ts must be timezone-aware"):
        make_event(ts=datetime(2026, 1, 18, 14, 22, 7, 412000))  # noqa: DTZ001


@pytest.mark.unit
@pytest.mark.parametrize(
    "stage", [PrismaStage.RAW, PrismaStage.AUTOMATED, PrismaStage.LANGUAGE, PrismaStage.INCLUDED]
)
def test_event__computed_set_stage__is_rejected(stage: PrismaStage) -> None:
    with pytest.raises(PydanticValidationError, match="is not a screening stage"):
        make_event(stage=stage)


@pytest.mark.unit
@pytest.mark.parametrize("stage", [PrismaStage.TITLE_ABSTRACT, PrismaStage.FULLTEXT])
def test_event__loggable_stage__is_accepted(stage: PrismaStage) -> None:
    assert make_event(stage=stage).stage is stage


@pytest.mark.unit
@pytest.mark.parametrize(
    "field_name", ["event_id", "project", "record_id", "reviewer", "criteria_version"]
)
@pytest.mark.parametrize("blank", ["", "   "])
def test_event__blank_identifier_field__is_rejected(field_name: str, blank: str) -> None:
    with pytest.raises(PydanticValidationError, match=f"{field_name} must not be empty"):
        make_event(**{field_name: blank})


@pytest.mark.unit
def test_event__unknown_field__is_rejected() -> None:
    with pytest.raises(PydanticValidationError, match="Extra inputs are not permitted"):
        make_event(adjudicated_by="mm")


@pytest.mark.unit
def test_event__decision_outside_the_closed_enum__is_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        make_event(decision="maybe")


@pytest.mark.unit
def test_event__constructed_without_schema_version__defaults_to_the_current_one() -> None:
    assert make_event().schema_version == CURRENT_SCHEMA_VERSION


@pytest.mark.unit
def test_event__mutating_a_field__is_rejected() -> None:
    event = make_event()

    with pytest.raises(PydanticValidationError):
        event.decision = "include"  # type: ignore[misc]


@pytest.mark.unit
def test_ulid_factory__ids__are_26_crockford_base32_characters() -> None:
    factory = MonotonicUlidFactory()

    ids = [factory() for _ in range(5)]

    assert [len(value) for value in ids] == [26] * 5
    assert set("".join(ids)) <= set("0123456789ABCDEFGHJKMNPQRSTVWXYZ")


@pytest.mark.unit
def test_ulid_factory__same_millisecond__ids_still_increase_lexicographically(
    frozen_time: None,
) -> None:
    factory = MonotonicUlidFactory()

    ids = [factory() for _ in range(50)]

    assert ids == sorted(ids)
    assert len(set(ids)) == 50


@pytest.mark.unit
def test_ulid_factory__clock_moved_backwards__ids_still_increase() -> None:
    factory = MonotonicUlidFactory()

    with time_machine.travel(datetime(2026, 6, 1, tzinfo=UTC), tick=False):
        later = factory()
    with time_machine.travel(datetime(2020, 1, 1, tzinfo=UTC), tick=False):
        after_rollback = factory()

    assert after_rollback > later


@pytest.mark.unit
def test_ulid_factory__clock_advanced__id_reflects_the_new_millisecond() -> None:
    factory = MonotonicUlidFactory()

    with time_machine.travel(datetime(2020, 1, 1, tzinfo=UTC), tick=False):
        earlier = factory()
    with time_machine.travel(datetime(2026, 6, 1, tzinfo=UTC), tick=False):
        later = factory()

    assert earlier < later
    assert earlier[:10] != later[:10]
