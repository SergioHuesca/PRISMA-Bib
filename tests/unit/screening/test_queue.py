"""Unit tests for the screening queue's ordering rule (BUILD_PLAN §Stage 5, line 1070).

Everything here exercises
:func:`prismabib.screening.queue.ordered_record_ids`, which is pure: a slug,
a bag of record ids, and no I/O at all. The queue's *stateful* half --
resumption, per-reviewer folding, undo -- needs a real Layer 1 store and a
real ``decisions.jsonl`` and therefore lives in
``tests/integration/screening/test_queue.py`` (§3.7.2: a unit test mocks
nothing because it touches nothing).

BUILD_PLAN's Stage 5 table labels ``test_queue__different_slug__ordering_differs``
a unit test and it is one. Its sibling
``test_queue__same_project_slug__ordering_is_identical_across_runs`` is
labelled unit there too but is an integration test here, and deliberately:
the defect it exists to catch -- keying the order on ``hash()``, which is
salted per process by ``PYTHONHASHSEED`` -- is *invisible inside a single
process*. An in-process "build it twice, compare" test passes on that defect
every time. Proving the claim takes two interpreters, so that test spawns
them.
"""

from __future__ import annotations

import pytest

from prismabib.screening.queue import ordered_record_ids

#: A small corpus in Scopus's real record-id shape. Ten records is enough for
#: 10! = 3,628,800 possible orders, so the slug tests below cannot pass by
#: coincidence.
PINNED_IDS = tuple(f"scopus:2-s2.0-8510123456{index}" for index in range(10))

#: The exact order ``PINNED_IDS`` takes under the slug ``"vad-2026"``.
#:
#: This constant is the ordering rule's fingerprint, and it is checked in for
#: two reasons. First, the order a review was screened in is a property of
#: that review: silently changing it renumbers a half-finished queue and
#: nobody finds out. Any deliberate change to the rule bumps
#: ``ORDERING_NAMESPACE``'s ``/v1`` and rewrites this constant in the same
#: commit. Second, it is a standing check that the order is a fixed function
#: of its inputs on every machine, interpreter and run -- a ``hash()``-keyed
#: implementation would match this list roughly once in ten factorial.
PINNED_ORDER = (
    "scopus:2-s2.0-85101234566",
    "scopus:2-s2.0-85101234560",
    "scopus:2-s2.0-85101234569",
    "scopus:2-s2.0-85101234565",
    "scopus:2-s2.0-85101234563",
    "scopus:2-s2.0-85101234568",
    "scopus:2-s2.0-85101234562",
    "scopus:2-s2.0-85101234561",
    "scopus:2-s2.0-85101234567",
    "scopus:2-s2.0-85101234564",
)

#: A corpus big enough that two slugs producing the same order by chance is
#: not a thing that happens (200! orders).
WIDE_IDS = tuple(f"scopus:2-s2.0-9{index:011d}" for index in range(200))


@pytest.mark.unit
def test_queue__different_slug__ordering_differs() -> None:
    alpha = ordered_record_ids("alpha-review", WIDE_IDS)
    beta = ordered_record_ids("beta-review", WIDE_IDS)

    assert alpha != beta
    assert sorted(alpha) == sorted(beta)


@pytest.mark.unit
def test_ordered_record_ids__pinned_corpus__matches_the_recorded_order() -> None:
    assert ordered_record_ids("vad-2026", PINNED_IDS) == PINNED_ORDER


@pytest.mark.unit
@pytest.mark.parametrize(
    "supplied",
    [
        pytest.param(PINNED_IDS, id="as-a-tuple"),
        pytest.param(tuple(reversed(PINNED_IDS)), id="reversed"),
        pytest.param(tuple(sorted(PINNED_IDS, reverse=True)), id="sorted-descending"),
        pytest.param(frozenset(PINNED_IDS), id="as-a-frozenset"),
        pytest.param(PINNED_IDS + PINNED_IDS, id="with-every-id-twice"),
    ],
)
def test_ordered_record_ids__input_order_and_container__do_not_affect_the_result(
    supplied: tuple[str, ...] | frozenset[str],
) -> None:
    assert ordered_record_ids("vad-2026", supplied) == PINNED_ORDER


@pytest.mark.unit
def test_ordered_record_ids__any_corpus__is_a_permutation_of_its_distinct_ids() -> None:
    ordered = ordered_record_ids("vad-2026", WIDE_IDS)

    assert sorted(ordered) == sorted(WIDE_IDS)
    assert len(ordered) == len(set(ordered))


@pytest.mark.unit
def test_ordered_record_ids__empty_corpus__is_empty() -> None:
    assert ordered_record_ids("vad-2026", []) == ()


@pytest.mark.unit
def test_ordered_record_ids__unicode_slug_and_ids__is_stable() -> None:
    ids = ("scopus:ré-01", "scopus:日本-02", "scopus:naïve-03")

    first = ordered_record_ids("révision-2026", ids)
    second = ordered_record_ids("révision-2026", ids)

    assert first == second
    assert sorted(first) == sorted(ids)
