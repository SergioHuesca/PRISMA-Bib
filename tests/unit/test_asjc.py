"""Unit tests for :mod:`prismabib.asjc`.

The table bridges two forms of the same fact: Layer 1 stores Scopus's
four-digit ASJC ``@code``, ``criteria.yaml`` declares the four-letter
grouping. Getting the bridge wrong does not weaken the subject-area filter,
it inverts it -- see ADR 0017 and the integration test in
``tests/integration/prisma/test_engine.py``.
"""

from __future__ import annotations

import pytest

from prismabib.asjc import KNOWN_ABBREVS, area_abbrev


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Transcribed from the recorded Scopus response in
        # tests/fixtures/cassettes/abstract-full-multi-subject-area.json, which
        # carries @code and @abbrev side by side -- so these pairs are Scopus's
        # own, not this table restating itself.
        pytest.param("2202", "ENGI", id="aerospace-engineering"),
        pytest.param("2205", "ENGI", id="civil-and-structural-engineering"),
        pytest.param("1702", "COMP", id="artificial-intelligence"),
        pytest.param("2611", "MATH", id="modelling-and-simulation"),
        pytest.param("2746", "MEDI", id="surgery"),
        pytest.param("1000", "MULT", id="multidisciplinary"),
    ],
)
def test_area_abbrev__a_four_digit_asjc_code__maps_to_its_grouping(raw: str, expected: str) -> None:
    assert area_abbrev(raw) == (expected, True)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("COMP", "COMP", id="already-an-abbreviation"),
        pytest.param("engi", "ENGI", id="lower-case"),
        pytest.param("  MATH  ", "MATH", id="surrounding-whitespace"),
    ],
)
def test_area_abbrev__an_abbreviation__passes_through_normalised(raw: str, expected: str) -> None:
    """A store built from a capture that already held abbreviations still matches."""
    assert area_abbrev(raw) == (expected, True)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        pytest.param("9999", id="unknown-numeric-prefix"),
        pytest.param("XXXX", id="unknown-abbreviation"),
        pytest.param("170", id="three-digits"),
        pytest.param("17022", id="five-digits"),
        pytest.param("", id="empty"),
    ],
)
def test_area_abbrev__an_unrecognised_value__is_a_miss_and_is_preserved(raw: str) -> None:
    """An unmapped value is reported as a miss, never guessed at and never dropped.

    §5 risk 8's discipline, applied to subject areas: a code this table does
    not know must not silently drop a record from a review, and the caller
    must be able to see that it happened.
    """
    value, matched = area_abbrev(raw)

    assert matched is False
    assert value == raw.strip().upper()


@pytest.mark.unit
def test_known_abbrevs__is_the_full_asjc_top_level_set() -> None:
    """All 27 ASJC groupings are present, so a valid criteria value is never a miss.

    Written out rather than derived from the table under test: an expectation
    built from ``_PREFIX_TO_ABBREV`` would agree with itself no matter which
    grouping was dropped.
    """
    expected = {
        "AGRI",
        "ARTS",
        "BIOC",
        "BUSI",
        "CENG",
        "CHEM",
        "COMP",
        "DECI",
        "DENT",
        "EART",
        "ECON",
        "ENER",
        "ENGI",
        "ENVI",
        "HEAL",
        "IMMU",
        "MATE",
        "MATH",
        "MEDI",
        "MULT",
        "NEUR",
        "NURS",
        "PHAR",
        "PHYS",
        "PSYC",
        "SOCI",
        "VETE",
    }

    assert expected == KNOWN_ABBREVS
