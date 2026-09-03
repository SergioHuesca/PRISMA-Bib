"""Unit tests for ``src/prismabib/publishers.py`` (ADR 0019).

Not itself named in BUILD_PLAN's Stage 6 Tests table -- the table's coverage
tests already exercise it end to end -- but the ``(value, matched)``
contract this module shares with :mod:`prismabib.countries` and
:mod:`prismabib.asjc` is worth pinning directly, the same way those two
modules are.
"""

from __future__ import annotations

import pytest

from prismabib.publishers import UNKNOWN_PUBLISHER, publisher_from_doi


@pytest.mark.unit
@pytest.mark.parametrize(
    ("doi", "expected"),
    [
        pytest.param("10.1016/j.knosys.2021.107762", "Elsevier", id="elsevier"),
        pytest.param("10.1109/tpami.2021.100001", "IEEE", id="ieee"),
        pytest.param("10.1007/s10994-021-100001", "Springer", id="springer"),
        pytest.param("10.3390/s21010001", "MDPI", id="mdpi"),
        pytest.param("10.48550/arXiv.2101.00001", "arXiv", id="arxiv"),
    ],
)
def test_publisher_from_doi__known_prefix__matches(doi: str, expected: str) -> None:
    value, matched = publisher_from_doi(doi)

    assert value == expected
    assert matched is True


#: One case per DOI registrant prefix currently in
#: ``publishers._PREFIX_TO_PUBLISHER``, transcribed independently from the DOI
#: Foundation's public prefix registry (https://doi.org, and each publisher's
#: own "cite this article" pages) -- not read off the module under test, which
#: would let a wrong entry agree with itself and pass regardless (the failure
#: this table exists to catch: an earlier version of the module mapped
#: ``10.24963`` to "AAAI" instead of IJCAI, and every prefix but 5 had zero
#: test coverage at all). A change to the module's table that is not also a
#: change here is either a new prefix this test does not yet know about (add a
#: case) or a wrong one (this test fails).
_ALL_PREFIX_CASES = [
    pytest.param("10.1016", "Elsevier", id="elsevier"),
    pytest.param("10.1109", "IEEE", id="ieee"),
    pytest.param("10.1007", "Springer", id="springer"),
    pytest.param("10.3390", "MDPI", id="mdpi"),
    pytest.param("10.1145", "ACM", id="acm"),
    pytest.param("10.1002", "Wiley", id="wiley"),
    pytest.param("10.1080", "Taylor & Francis", id="taylor-and-francis"),
    pytest.param("10.1177", "SAGE", id="sage"),
    pytest.param("10.1038", "Nature Portfolio", id="nature-portfolio"),
    pytest.param("10.1371", "PLOS", id="plos"),
    pytest.param("10.3389", "Frontiers", id="frontiers"),
    pytest.param("10.1155", "Hindawi", id="hindawi"),
    pytest.param("10.1093", "Oxford University Press", id="oup"),
    pytest.param("10.1017", "Cambridge University Press", id="cup"),
    pytest.param("10.1088", "IOP Publishing", id="iop"),
    pytest.param("10.1063", "AIP Publishing", id="aip"),
    pytest.param("10.1103", "American Physical Society", id="aps"),
    pytest.param("10.48550", "arXiv", id="arxiv-full"),
    pytest.param("10.1201", "CRC Press (Taylor & Francis)", id="crc-press"),
    pytest.param("10.1049", "IET", id="iet"),
    pytest.param("10.1186", "BioMed Central (Springer Nature)", id="biomed-central"),
    pytest.param("10.3233", "IOS Press", id="ios-press"),
    pytest.param("10.2139", "SSRN", id="ssrn"),
    pytest.param("10.1287", "INFORMS", id="informs"),
    pytest.param("10.1061", "ASCE", id="asce"),
    pytest.param("10.2514", "AIAA", id="aiaa"),
    pytest.param("10.1115", "ASME", id="asme"),
    pytest.param("10.1029", "AGU", id="agu"),
    pytest.param("10.1039", "Royal Society of Chemistry", id="rsc"),
    pytest.param("10.1021", "American Chemical Society", id="acs"),
    pytest.param("10.1073", "PNAS", id="pnas"),
    pytest.param("10.1126", "AAAS (Science)", id="aaas-science"),
    pytest.param("10.1364", "Optica Publishing Group", id="optica"),
    pytest.param("10.4230", "Dagstuhl (LIPIcs)", id="dagstuhl-lipics"),
    # The defect this table exists to catch: 10.24963 is IJCAI's registrant
    # prefix (International Joint Conferences on Artificial Intelligence).
    # AAAI's own prefix -- distinct, and also pinned below -- is 10.1609.
    pytest.param("10.24963", "IJCAI", id="ijcai"),
    pytest.param("10.1609", "AAAI", id="aaai"),
    pytest.param("10.1137", "SIAM", id="siam"),
    pytest.param("10.2200", "Morgan & Claypool", id="morgan-and-claypool"),
    pytest.param("10.1142", "World Scientific", id="world-scientific"),
]


@pytest.mark.unit
@pytest.mark.parametrize(("prefix", "expected"), _ALL_PREFIX_CASES)
def test_publisher_from_doi__every_mapped_prefix__matches_the_registrant(
    prefix: str, expected: str
) -> None:
    value, matched = publisher_from_doi(f"{prefix}/example.2026.000001")

    assert value == expected
    assert matched is True


@pytest.mark.unit
def test_publisher_from_doi__every_mapped_prefix__table_has_no_untested_entries() -> None:
    """Guard the guard: every key the module actually declares is pinned above.

    A prefix added to ``_PREFIX_TO_PUBLISHER`` without a corresponding case in
    :data:`_ALL_PREFIX_CASES` would ship with zero coverage again, exactly the
    state 34 of 39 prefixes were in before this test existed.
    """
    from prismabib.publishers import _PREFIX_TO_PUBLISHER

    tested_prefixes = {param.values[0] for param in _ALL_PREFIX_CASES}
    assert tested_prefixes == set(_PREFIX_TO_PUBLISHER)


@pytest.mark.unit
def test_publisher_from_doi__url_wrapped_doi__still_matches() -> None:
    value, matched = publisher_from_doi("https://doi.org/10.1016/j.knosys.2021.107762")

    assert value == "Elsevier"
    assert matched is True


@pytest.mark.unit
def test_publisher_from_doi__no_doi__is_unknown_and_counted() -> None:
    value, matched = publisher_from_doi(None)

    assert value == UNKNOWN_PUBLISHER
    assert matched is False


@pytest.mark.unit
def test_publisher_from_doi__blank_doi__is_unknown_and_counted() -> None:
    value, matched = publisher_from_doi("   ")

    assert value == UNKNOWN_PUBLISHER
    assert matched is False


@pytest.mark.unit
def test_publisher_from_doi__unmapped_prefix__preserved_never_guessed() -> None:
    value, matched = publisher_from_doi("10.9999/unmapped.example")

    assert value == "10.9999"
    assert matched is False


@pytest.mark.unit
def test_publisher_from_doi__unparseable_doi__preserved_never_guessed() -> None:
    value, matched = publisher_from_doi("not-a-doi-at-all")

    assert value == "not-a-doi-at-all"
    assert matched is False
