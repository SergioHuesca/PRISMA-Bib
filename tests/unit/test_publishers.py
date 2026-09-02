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
