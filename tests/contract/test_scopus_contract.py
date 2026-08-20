"""Contract tests against sanitised Scopus cassettes (BUILD_PLAN Stage 2 Tests table, lines 829-830).

BUILD_PLAN §3.7.2: contract tests assert the *shape* of an upstream payload
we depend on, against sanitised cassettes, and "fail when Scopus changes its
response schema." No mocking, no network, no ``ScopusClient`` at all here --
these load a cassette from ``tests/fixtures/cassettes/`` (produced by
``tests/fixtures/sanitise.py``; see ``tests/fixtures/README.md`` for the
recording procedure) and assert directly on its structure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_CASSETTES_DIR = Path(__file__).parent.parent / "fixtures" / "cassettes"


def _load_cassette(name: str) -> dict[str, Any]:
    with (_CASSETTES_DIR / name).open(encoding="utf-8") as handle:
        return dict(json.load(handle))


@pytest.mark.contract
def test_contract__search_response__has_required_fields() -> None:
    """A ``view=COMPLETE`` page carries every field prismabib's later stages depend on.

    ``dc:identifier``, ``prism:coverDate``, and ``subtypeDescription`` are
    required on *every* entry (they are core bibliographic fields present
    regardless of record type). ``authkeywords`` and
    ``affiliation[].affiliation-country`` are only required on *some* entry
    -- BUILD_PLAN's own cassette (``tests/fixtures/README.md``) documents
    that 3 of this page's 25 entries are proceedings-level records with no
    author list at all, so asserting those two fields on every entry would
    be asserting something false about real Scopus data.
    """
    page = _load_cassette("complete-page-0000.json")

    entries = page["search-results"]["entry"]
    assert entries, "cassette must carry at least one entry to be a useful contract"

    for entry in entries:
        assert "dc:identifier" in entry
        assert "prism:coverDate" in entry
        assert "subtypeDescription" in entry

    entries_with_keywords = [entry for entry in entries if "authkeywords" in entry]
    assert entries_with_keywords, "at least one entry must carry authkeywords"

    entries_with_country = [
        entry
        for entry in entries
        if isinstance(entry.get("affiliation"), list)
        and any("affiliation-country" in affiliation for affiliation in entry["affiliation"])
    ]
    assert entries_with_country, "at least one entry must carry affiliation[].affiliation-country"


@pytest.mark.contract
def test_contract__standard_view_response__lacks_authkeywords() -> None:
    """``view=STANDARD`` never carries ``authkeywords`` -- pinning why COMPLETE is mandatory.

    BUILD_PLAN line 763: STANDARD omits ``authkeywords`` and full
    affiliation data, "which kills the keyword co-occurrence network and
    the geography analysis." This test exists so a future maintainer who
    considers "simplifying" ``ScopusClient`` to use STANDARD (e.g. to save
    quota) breaks a test immediately, rather than silently degrading the
    corpus (BUILD_PLAN §5 risk 1).
    """
    page = _load_cassette("standard-page-0000.json")

    entries = page["search-results"]["entry"]
    assert entries, "cassette must carry at least one entry to be a useful contract"

    entries_with_keywords = [entry for entry in entries if "authkeywords" in entry]
    assert entries_with_keywords == []
