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
        # The first three are transcribed from the recorded Scopus response in
        # tests/fixtures/cassettes/abstract-full-multi-subject-area.json, which
        # carries @code and @abbrev side by side -- Scopus's own answer, not
        # this table restating itself. That cassette contains only those three
        # pairs; the rest below are transcribed from Elsevier's published ASJC
        # category list, which is the only other authority available offline.
        # Both provenances are named because a claim about how a table was
        # checked has to be true -- see ADR 0017's own constraint.
        pytest.param("2202", "ENGI", id="cassette-aerospace-engineering"),
        pytest.param("2205", "ENGI", id="cassette-civil-and-structural-engineering"),
        pytest.param("1702", "COMP", id="cassette-artificial-intelligence"),
    ],
)
def test_area_abbrev__a_four_digit_asjc_code__maps_to_its_grouping(raw: str, expected: str) -> None:
    assert area_abbrev(raw) == (expected, True)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        pytest.param("1000", "MULT", id="multidisciplinary"),
        pytest.param("1105", "AGRI", id="ecology-evolution-behaviour-and-systematics"),
        pytest.param("1202", "ARTS", id="history"),
        pytest.param("1305", "BIOC", id="biotechnology"),
        pytest.param("1408", "BUSI", id="strategy-and-management"),
        pytest.param("1502", "CENG", id="bioengineering"),
        pytest.param("1602", "CHEM", id="analytical-chemistry"),
        pytest.param("1706", "COMP", id="computer-science-applications"),
        pytest.param("1802", "DECI", id="information-systems-and-management"),
        pytest.param("1908", "EART", id="geophysics"),
        pytest.param("2002", "ECON", id="economics-and-econometrics"),
        pytest.param("2105", "ENER", id="renewable-energy"),
        pytest.param("2207", "ENGI", id="biomedical-engineering"),
        pytest.param("2304", "ENVI", id="environmental-chemistry"),
        pytest.param("2403", "IMMU", id="immunology"),
        pytest.param("2504", "MATE", id="electronic-optical-and-magnetic-materials"),
        pytest.param("2611", "MATH", id="modelling-and-simulation"),
        pytest.param("2739", "MEDI", id="public-health"),
        pytest.param("2802", "NEUR", id="behavioural-neuroscience"),
        pytest.param("2902", "NURS", id="advanced-and-specialised-nursing"),
        pytest.param("3004", "PHAR", id="pharmacology"),
        pytest.param("3105", "PHYS", id="instrumentation"),
        pytest.param("3204", "PSYC", id="developmental-and-educational-psychology"),
        pytest.param("3306", "SOCI", id="health-social-science"),
        pytest.param("3403", "VETE", id="food-animals"),
        pytest.param("3506", "DENT", id="periodontics"),
        pytest.param("3605", "HEAL", id="speech-and-hearing"),
    ],
)
def test_area_abbrev__every_asjc_grouping__has_a_pinned_code(raw: str, expected: str) -> None:
    """One real ASJC code per grouping, so a permuted table cannot ship green.

    ``test_known_abbrevs__is_the_full_asjc_top_level_set`` compares the *value
    set*, so it catches a dropped or added grouping but is blind to any
    permutation of prefix to abbreviation. Swapping ``34: VETE`` with
    ``35: DENT`` -- every veterinary paper filed as dentistry -- passed the
    entire suite before this test existed.

    ``asjc.py`` is also outside the mutation gate (``only_mutate`` is
    ``src/prismabib/prisma/*``) and its 100% branch coverage says nothing
    about a table's factual content, so an explicit case per prefix is the
    only thing standing between a mis-transcribed digit and a wrongly
    filtered corpus.

    Codes are transcribed from Elsevier's published ASJC category list, not
    derived from ``_PREFIX_TO_ABBREV``.
    """
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
