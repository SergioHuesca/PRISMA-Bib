"""Unit tests for :func:`prismabib.prisma.log.fold_events` (BUILD_PLAN §Stage 4, line 972).

The fold itself is a pure function over a sequence of events, so it is
tested here without touching a file. Everything about ``decisions.jsonl``
*as a file* -- appending, fsync, the checksum sidecar, tamper and crash
detection, concurrency -- lives in
``tests/integration/prisma/test_log.py``.

BUILD_PLAN line 972 pins two rules that these tests exist to hold in place:
"later events supersede earlier ones for the same ``(stage, record_id,
reviewer)``", and "folding takes the last event by ``ts``, ties broken by
``event_id``".
"""

from __future__ import annotations

import itertools
from datetime import UTC, datetime

import pytest

from prismabib.prisma.events import DecisionEvent
from prismabib.prisma.log import fold_events
from prismabib.stage import PrismaStage

T0 = datetime(2026, 1, 18, 14, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 1, 18, 15, 0, 0, tzinfo=UTC)


def event(
    event_id: str,
    *,
    ts: datetime = T0,
    stage: PrismaStage = PrismaStage.TITLE_ABSTRACT,
    record_id: str = "scopus:r1",
    reviewer: str = "kp",
    decision: str = "include",
    reason_code: str | None = None,
) -> DecisionEvent:
    """Build one event with everything but the fold-relevant fields fixed."""
    return DecisionEvent(
        event_id=event_id,
        ts=ts,
        project="vad-2026",
        stage=stage,
        record_id=record_id,
        reviewer=reviewer,
        decision=decision,  # type: ignore[arg-type]
        reason_code=reason_code,
        criteria_version="1.0.0",
    )


@pytest.mark.unit
def test_fold_events__no_events__is_empty() -> None:
    assert fold_events([]) == {}


@pytest.mark.unit
def test_fold_events__two_events_for_one_key__keeps_the_later_timestamp() -> None:
    earlier = event("id-000001", ts=T0, decision="include")
    later = event("id-000002", ts=T1, decision="exclude", reason_code="OFF_TOPIC")

    folded = fold_events([earlier, later])

    assert folded == {(PrismaStage.TITLE_ABSTRACT, "scopus:r1", "kp"): later}


@pytest.mark.unit
def test_fold_events__later_event_supplied_first__still_wins() -> None:
    earlier = event("id-000001", ts=T0, decision="include")
    later = event("id-000002", ts=T1, decision="unsure")

    folded = fold_events([later, earlier])

    assert folded[PrismaStage.TITLE_ABSTRACT, "scopus:r1", "kp"] is later


@pytest.mark.unit
def test_fold_events__identical_timestamps__tie_is_broken_by_the_greater_event_id() -> None:
    # Both input orders are asserted deliberately. Feeding only the order in
    # which the winner happens to arrive first would pass just as happily
    # against a fold that ignored `event_id` altogether and kept whichever
    # event it saw first -- which is a real defect (two decisions logged in
    # the same millisecond would resolve by file order, not by ULID order).
    first = event("id-000001", ts=T0, decision="include")
    second = event("id-000002", ts=T0, decision="exclude", reason_code="OFF_TOPIC")
    key = (PrismaStage.TITLE_ABSTRACT, "scopus:r1", "kp")

    winners = (fold_events([first, second])[key], fold_events([second, first])[key])

    assert winners == (second, second)


@pytest.mark.unit
def test_fold_events__same_record_different_stages__do_not_supersede_each_other() -> None:
    abstract = event("id-000001", stage=PrismaStage.TITLE_ABSTRACT, decision="include")
    fulltext = event("id-000002", stage=PrismaStage.FULLTEXT, ts=T1, decision="unsure")

    folded = fold_events([abstract, fulltext])

    assert folded == {
        (PrismaStage.TITLE_ABSTRACT, "scopus:r1", "kp"): abstract,
        (PrismaStage.FULLTEXT, "scopus:r1", "kp"): fulltext,
    }


@pytest.mark.unit
def test_fold_events__same_record_different_reviewers__do_not_supersede_each_other() -> None:
    kp = event("id-000001", reviewer="kp", decision="include")
    mm = event("id-000002", reviewer="mm", ts=T1, decision="exclude", reason_code="OFF_TOPIC")

    folded = fold_events([kp, mm])

    assert folded == {
        (PrismaStage.TITLE_ABSTRACT, "scopus:r1", "kp"): kp,
        (PrismaStage.TITLE_ABSTRACT, "scopus:r1", "mm"): mm,
    }


@pytest.mark.unit
def test_fold_events__every_permutation_of_one_stream__yields_the_same_mapping() -> None:
    # Two of these share both a fold key and a timestamp (ids 000004/000005),
    # so permutation invariance here also covers the same-millisecond case,
    # where only the `event_id` tie-break can decide a winner.
    events = [
        event("id-000001", ts=T0, decision="include"),
        event("id-000002", ts=T1, decision="exclude", reason_code="OFF_TOPIC"),
        event("id-000003", ts=T0, reviewer="mm", decision="unsure"),
        event("id-000004", ts=T1, stage=PrismaStage.FULLTEXT, decision="include"),
        event("id-000005", ts=T1, stage=PrismaStage.FULLTEXT, decision="unsure"),
    ]
    expected = {
        (PrismaStage.TITLE_ABSTRACT, "scopus:r1", "kp"): events[1],
        (PrismaStage.TITLE_ABSTRACT, "scopus:r1", "mm"): events[2],
        (PrismaStage.FULLTEXT, "scopus:r1", "kp"): events[4],
    }

    folds = [fold_events(list(permutation)) for permutation in itertools.permutations(events)]

    assert folds == [expected] * len(folds)


@pytest.mark.unit
def test_fold_events__only_unsure_events_for_a_key__folds_to_unsure_not_include() -> None:
    first = event("id-000001", ts=T0, decision="unsure")
    second = event("id-000002", ts=T1, decision="unsure")

    folded = fold_events([first, second])

    assert folded[PrismaStage.TITLE_ABSTRACT, "scopus:r1", "kp"].decision == "unsure"
