"""Live test for ``src/prismabib/sources/scopus.py`` (BUILD_PLAN Stage 2 Tests table, line 831).

Hits the real Scopus Search API once, fetching exactly one page
(``next(iter(...))`` on the ``search`` generator never advances past the
first yielded item, so this costs one request against the weekly quota, not
one per page of the full result set). Deselected by default (``-m "not
live"`` is baked into ``pyproject.toml``'s ``addopts``); runs nightly and on
demand (``pytest -m live``) per ``tests/live/conftest.py``, which lifts the
socket ban for this whole directory.

**Never asserts a count.** ``opensearch:totalResults``, how many entries a
page holds, and which specific records they are all drift week to week as
Scopus's index grows -- asserting any of those would fail this test every
week for a reason that has nothing to do with a broken client. What it
asserts instead is *shape*: the same required-field set
``tests/contract/test_scopus_contract.py`` already pins from a sanitised
cassette, so a real upstream schema change is caught here first, as an early
warning, before it silently breaks a later stage that assumes those fields
exist.
"""

from __future__ import annotations

import pytest

from prismabib.config import Settings
from prismabib.sources.scopus import ScopusClient

pytestmark = pytest.mark.live

_QUERY = 'TITLE-ABS-KEY("video anomaly detection")'


def test_live__search_one_page__matches_recorded_shape() -> None:
    with ScopusClient(Settings()) as client:
        page = next(iter(client.search(_QUERY)))

    results = page["search-results"]
    entries = results["entry"]

    assert "opensearch:totalResults" in results
    assert isinstance(entries, list)
    assert entries, "the Stage 1 reference query is expected to match at least one live record"
    assert "@next" in results.get("cursor", {})

    required_on_every_entry = ("dc:identifier", "prism:coverDate", "subtypeDescription")
    for field_name in required_on_every_entry:
        entries_missing_field = [entry for entry in entries if field_name not in entry]
        assert entries_missing_field == []

    entries_with_keywords = [entry for entry in entries if "authkeywords" in entry]
    assert entries_with_keywords, (
        "view=COMPLETE must still return authkeywords on at least one entry"
    )
