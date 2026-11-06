"""Live test for ``src/prismabib/sources/openalex.py`` (Nightly live suite, issue #301).

Hits the real OpenAlex works API once, fetching exactly the first page of a
search (``fetch_search_page`` with the ``*`` cursor), with no credential: the
first page needs no ``OPENALEX_API_KEY``. Deselected by default (``-m "not
live"`` is baked into ``pyproject.toml``'s ``addopts``); runs nightly and on
demand (``pytest -m live``) per ``tests/live/conftest.py``, which lifts the
socket ban for this whole directory.

**Never asserts a count.** ``meta.count``, how many results a page holds, and
which records they are all drift as OpenAlex's index grows. What it asserts is
*shape*: the ``meta`` fields the search loop reads and every field named in
``SEARCH_SELECT`` (the fields ``parse_work`` and ``openalex_map`` rely on), so
an upstream schema change is caught here before it breaks a later stage.
"""

from __future__ import annotations

import json

import pytest

from prismabib.capture.openalex_search import FIRST_CURSOR
from prismabib.config import Settings
from prismabib.sources.openalex import SEARCH_SELECT, OpenAlexClient

pytestmark = pytest.mark.live

_QUERY = "video anomaly detection"


def test_live__search_first_page__has_the_fields_the_client_reads() -> None:
    with OpenAlexClient(Settings(openalex_api_key=None)) as client:
        page = client.fetch_search_page(_QUERY, FIRST_CURSOR)

    assert page.status == 200
    body = json.loads(page.body)

    assert "count" in body["meta"]
    assert "next_cursor" in body["meta"]
    results = body["results"]
    assert isinstance(results, list)
    assert results, "the reference query is expected to match at least one live record"

    # select= makes OpenAlex return every requested key on every record (null when empty).
    for field_name in SEARCH_SELECT.split(","):
        records_missing_field = [r["id"] for r in results if field_name not in r]
        assert records_missing_field == [], field_name
