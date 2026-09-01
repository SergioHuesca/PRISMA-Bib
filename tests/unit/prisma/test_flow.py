"""Unit tests for :class:`prismabib.prisma.flow.FlowCounts` (BUILD_PLAN §Stage 4).

:meth:`~prismabib.prisma.flow.FlowCounts.assert_consistent` is pure
arithmetic over a frozen dataclass -- no store, no log, no clock -- so it is
tested here rather than in ``tests/integration/prisma/test_flow.py``, which
owns everything that needs a real project on disk.

The failure tests are the point of this module. BUILD_PLAN line 993 calls
``assert_consistent()`` "the guard that prevents the class of error the
source manuscript exhibits"; a guard is only worth anything if it fires, and
a *diagnostic* guard is only worth anything if its message says which
equation broke and by how much (§3.7.3 rule 12). There is therefore one
failing case per equation, each asserting the message names that equation
verbatim and both of its sides.
"""

from __future__ import annotations

import dataclasses

import pytest

from prismabib.errors import ValidationError
from prismabib.prisma.flow import FlowCounts

#: A closed, internally consistent set of counts: 100 identified, 10 removed
#: by the automated filter, 5 by language, then 85 = 20 excluded + 5 unsure +
#: 60 retrieved, and 60 = (7 + 8) excluded + 5 unsure + 40 included. Every
#: failing case below is this instance with exactly one field perturbed, so
#: the perturbation is unambiguously the cause of the failure.
CONSISTENT = FlowCounts(
    identified=100,
    duplicates_across_searches=0,
    removed_other_reasons=0,
    excluded_automated=10,
    excluded_automated_by_reason={"year": 10},
    after_automated=90,
    excluded_language=5,
    after_language=85,
    excluded_title_abstract=20,
    unsure_title_abstract=5,
    retrieved_fulltext=60,
    excluded_fulltext={"INACCESSIBLE": 7, "NOT_PRIMARY_RESEARCH": 8},
    unsure_fulltext=5,
    included=40,
)


@pytest.mark.unit
def test_flow_counts__consistent_counts__assert_consistent_returns_none() -> None:
    assert CONSISTENT.assert_consistent() is None


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field_name", "delta", "equation", "left", "right"),
    [
        pytest.param(
            "after_automated",
            1,
            "identified - duplicates_across_searches - removed_other_reasons - excluded_automated == after_automated",
            90,
            91,
            id="equation-1-identification",
        ),
        pytest.param(
            "after_language",
            -1,
            "after_automated - excluded_language == after_language",
            85,
            84,
            id="equation-2-language",
        ),
        pytest.param(
            "excluded_title_abstract",
            1,
            (
                "after_language == excluded_title_abstract + unsure_title_abstract "
                "+ retrieved_fulltext"
            ),
            85,
            86,
            id="equation-3-title-abstract",
        ),
        pytest.param(
            "included",
            2,
            "retrieved_fulltext == sum(excluded_fulltext.values()) + unsure_fulltext + included",
            60,
            62,
            id="equation-4-fulltext",
        ),
    ],
)
def test_flow_counts__one_equation_violated__message_names_that_equation_and_both_sides(
    field_name: str, delta: int, equation: str, left: int, right: int
) -> None:
    broken = dataclasses.replace(
        CONSISTENT, **{field_name: getattr(CONSISTENT, field_name) + delta}
    )

    with pytest.raises(ValidationError) as excinfo:
        broken.assert_consistent()

    message = str(excinfo.value)
    assert equation in message
    assert f"{left} != {right}" in message
    assert f"off by {left - right}" in message


@pytest.mark.unit
def test_flow_counts__equation_2_violated_by_a_later_field__equation_1_is_not_blamed() -> None:
    # Equation 2 is the only one broken here; the message must not blame
    # equation 1, which still closes. Each equation is checked independently
    # precisely so a reader is pointed at the right step of the diagram.
    broken = dataclasses.replace(CONSISTENT, excluded_language=6)

    with pytest.raises(ValidationError) as excinfo:
        broken.assert_consistent()

    message = str(excinfo.value)
    assert "after_automated - excluded_language == after_language" in message
    assert "identified - duplicates_across_searches" not in message


@pytest.mark.unit
def test_flow_counts__empty_excluded_fulltext_breakdown__still_closes() -> None:
    all_included = FlowCounts(
        identified=3,
        duplicates_across_searches=0,
        removed_other_reasons=0,
        excluded_automated=0,
        excluded_automated_by_reason={"year": 0},
        after_automated=3,
        excluded_language=0,
        after_language=3,
        excluded_title_abstract=0,
        unsure_title_abstract=0,
        retrieved_fulltext=3,
        excluded_fulltext={},
        unsure_fulltext=0,
        included=3,
    )

    assert all_included.assert_consistent() is None


@pytest.mark.unit
def test_flow_counts__instances_with_equal_fields__compare_equal() -> None:
    # §3.7.3 rule 8/12: golden tests compare whole `FlowCounts` objects and
    # rely on pytest's dataclass diff to explain a mismatch. That only works
    # if the dataclass has value equality.
    assert dataclasses.replace(CONSISTENT) == CONSISTENT


# ---------------------------------------------------------------------------
# The non-negativity precondition (ADR 0007)
# ---------------------------------------------------------------------------
#
# Every count is a cardinality, so none of them can be below zero -- and the
# four equations cannot enforce that on their own, because each is an equality
# between two *sums* and a negative term closes one exactly as happily as a
# positive one. `unsure_title_abstract` and `unsure_fulltext` are computed by
# `compute_flow_counts` as the remainders of their partitions, so an
# over-count anywhere else in a partition drives its remainder negative and
# leaves the equation closing perfectly. Every case in the first
# parametrisation below is `CONSISTENT` with a *compensated* perturbation --
# all four equations still hold -- so the raise it asserts can only have come
# from the precondition. The two tests after it break an equation as well, and
# assert that the precondition is nonetheless what speaks.


@pytest.mark.unit
@pytest.mark.parametrize(
    ("broken", "field_name", "value"),
    [
        pytest.param(
            dataclasses.replace(CONSISTENT, excluded_title_abstract=29, unsure_title_abstract=-4),
            "unsure_title_abstract",
            -4,
            id="title-abstract-remainder-absorbs-an-overstated-exclusion",
        ),
        pytest.param(
            dataclasses.replace(CONSISTENT, included=48, unsure_fulltext=-3),
            "unsure_fulltext",
            -3,
            id="fulltext-remainder-absorbs-an-overstated-inclusion",
        ),
        pytest.param(
            dataclasses.replace(
                CONSISTENT,
                excluded_fulltext={
                    "NOT_PRIMARY_RESEARCH": -1,
                    "INACCESSIBLE": -2,
                    "DUPLICATE_PUBLICATION": 18,
                },
            ),
            "excluded_fulltext['INACCESSIBLE']",
            -2,
            id="excluded-fulltext-entries-are-walked-in-sorted-reason-code-order",
        ),
    ],
)
def test_flow_counts__a_negative_count__is_rejected_naming_the_field_and_its_value(
    broken: FlowCounts, field_name: str, value: int
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        broken.assert_consistent()

    message = str(excinfo.value)
    assert f"{field_name} is negative: {value}" in message
    assert "does not hold" not in message


@pytest.mark.unit
def test_flow_counts__negative_count_and_a_broken_equation__blames_the_negative_count() -> None:
    # `identified - (-10) == 110`, not 90, so equation 1 is broken here too.
    # The precondition runs first, and its message must be the one raised:
    # "equation 1 is off by 20" would send a reader looking at the
    # identification step rather than at the impossible count in front of them.
    broken = dataclasses.replace(CONSISTENT, excluded_automated=-10)

    with pytest.raises(ValidationError) as excinfo:
        broken.assert_consistent()

    message = str(excinfo.value)
    assert "excluded_automated is negative: -10" in message
    assert "does not hold" not in message


@pytest.mark.unit
@pytest.mark.parametrize(
    ("broken", "named", "not_named"),
    [
        pytest.param(
            dataclasses.replace(CONSISTENT, after_automated=-90, unsure_fulltext=-5),
            "after_automated is negative: -90",
            "unsure_fulltext",
            id="the-earlier-declared-field-wins",
        ),
        pytest.param(
            dataclasses.replace(
                CONSISTENT,
                unsure_fulltext=-5,
                excluded_fulltext={"DUPLICATE_PUBLICATION": -7, "INACCESSIBLE": 22},
            ),
            "unsure_fulltext is negative: -5",
            "DUPLICATE_PUBLICATION",
            id="every-integer-field-before-any-excluded-fulltext-entry",
        ),
    ],
)
def test_flow_counts__several_negative_counts__names_the_first_in_field_order(
    broken: FlowCounts, named: str, not_named: str
) -> None:
    with pytest.raises(ValidationError) as excinfo:
        broken.assert_consistent()

    message = str(excinfo.value)
    assert named in message
    assert not_named not in message


@pytest.mark.unit
def test_flow_counts__every_count_zero__is_not_treated_as_negative() -> None:
    # The boundary the precondition sits on: an unstarted project is all
    # zeros, and zero is a perfectly ordinary cardinality.
    empty = FlowCounts(
        identified=0,
        duplicates_across_searches=0,
        removed_other_reasons=0,
        excluded_automated=0,
        excluded_automated_by_reason={"year": 0},
        after_automated=0,
        excluded_language=0,
        after_language=0,
        excluded_title_abstract=0,
        unsure_title_abstract=0,
        retrieved_fulltext=0,
        excluded_fulltext={"INACCESSIBLE": 0},
        unsure_fulltext=0,
        included=0,
    )

    assert empty.assert_consistent() is None
