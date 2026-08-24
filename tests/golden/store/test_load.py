"""Golden snapshot test for ``build_store`` (BUILD_PLAN Stage 3 Tests table, line 910).

BUILD_PLAN §5 risk 11: golden snapshots must never be regenerated to make a
failing test pass without review. The committed snapshot at
``__snapshots__/reference_table_checksums.json`` is a plain, hand-reviewable
JSON file (row counts plus :func:`prismabib.store.checksums.table_checksums`'s
own output) -- never produced by ``syrupy --snapshot-update`` -- so a PR that
changes it shows a normal, line-by-line diff, per this stage's own note on
golden checksums (BUILD_PLAN line 927): "recomputed only when the reference
fixture or the loader semantics deliberately change, and the PR must say
which."

Uses :func:`prismabib.store.checksums.table_checksums` directly -- the one
definition of "the checksum" the module docstring names -- never a
second, test-owned hashing scheme.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismabib.store.checksums import table_checksums
from prismabib.store.db import connect
from prismabib.store.load import build_store
from tests.store_helpers import copy_reference_project

_GOLDEN_PATH = Path(__file__).parent / "__snapshots__" / "reference_table_checksums.json"


@pytest.mark.golden
@pytest.mark.acceptance("S03-AC1")
def test_build_store__reference_fixture__table_checksums_match_golden(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)

    stats = build_store(project, rebuild=True)
    connection = connect(project, read_only=True)
    try:
        checksums = table_checksums(connection)
    finally:
        connection.close()

    row_counts = stats.model_dump(mode="json")
    row_counts.pop("rebuilt")
    golden = json.loads(_GOLDEN_PATH.read_text(encoding="utf-8"))

    assert {"row_counts": row_counts, "table_checksums": checksums} == golden
