"""Golden checksums of the OpenAlex search tables (ADR 0042 Decision 5, PR 4a).

The reference fixture holds no OpenAlex search run, so its golden only pins the two new tables as
empty. This one builds a small synthetic OpenAlex-only project (two overlapping queries, a refresh,
a DOI twin, a work with no title and one with no mapped subject area) and pins the checksum of every
table the OpenAlex loader writes, so a change in what is stored, or a dependence on the machine
(timezone, ordering), moves a number in a reviewable diff. Regenerate only on a deliberate loader
change: ``python -m tests.golden.store.test_openalex_search_checksums``.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

import pytest

from prismabib.store.checksums import table_checksums
from prismabib.store.db import connect
from prismabib.store.load import build_store
from tests.openalex_search_helpers import openalex_project, work, write_search_run

_GOLDEN_PATH = Path(__file__).parent / "__snapshots__" / "openalex_search_table_checksums.json"
_TABLES = (
    "openalex_records",
    "openalex_search_runs",
    "run_duplicates",
    "malformed_entries",
    "record_subject_area_coverage",
    "subject_areas",
    "citation_snapshots",
    "authors",
    "record_authors",
    "affiliations",
    "record_affiliations",
    "keywords",
    "record_keywords",
)

#: A country-only authorship: no institution, an authorship-level `countries` (owner decision
#: 2026-11-04). Added on purpose, so the golden pins the `openalex-country:` affiliation row.
_COUNTRY_ONLY = [{"author": {"display_name": "Ana Sola"}, "institutions": [], "countries": ["ES"]}]


def _checksums(base: Path) -> dict[str, dict[str, str]]:
    project = openalex_project(base)
    write_search_run(
        project,
        "20261103T100000Z-aaaaaaaa",
        "query a",
        [
            [work(1), work(2), work(3, primary_topic=None, authorships=_COUNTRY_ONLY)],
            [work(4, title=None)],
        ],
    )
    write_search_run(
        project,
        "20261103T110000Z-bbbbbbbb",
        "query b",
        [[work(2), work(5, doi="https://doi.org/10.1/ITEM-1"), work(6, type="dataset")]],
        offset_minutes=60,
    )
    write_search_run(
        project,
        "20261103T120000Z-cccccccc",
        "query  a",
        [[work(1, cited_by_count=77)]],
        offset_minutes=120,
    )
    build_store(project, rebuild=True)
    connection = connect(project, read_only=True)
    try:
        return {"table_checksums": table_checksums(connection, _TABLES)}
    finally:
        connection.close()


@pytest.mark.golden
def test_build_store__synthetic_openalex_project__table_checksums_match_golden(
    tmp_path: Path,
) -> None:
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))

    assert _checksums(tmp_path) == golden


if __name__ == "__main__":  # pragma: no cover - the reviewed way to write the snapshot
    with tempfile.TemporaryDirectory() as tmp:
        text = json.dumps(_checksums(Path(tmp)), indent=2, sort_keys=True) + "\n"
    _GOLDEN_PATH.write_text(text, encoding="utf-8", newline="\n")
    sys.stdout.write(text)
