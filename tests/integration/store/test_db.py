"""Integration tests for ``store/db.py::connect`` (BUILD_PLAN §Stage 3 contract, line 892).

Real filesystem, real DuckDB, no network (§3.7.2) -- both failure paths
``connect`` documents (a missing store opened read-only, and a file DuckDB
itself refuses to open) are exercised against a real ``Project`` skeleton,
never monkeypatched (§3.7.3 rule 1: calling is not mocking).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.errors import StoreError
from prismabib.project import Project
from prismabib.store.db import connect


@pytest.mark.integration
def test_connect__read_only_and_store_missing__raises_store_error(tmp_path: Path) -> None:
    project = Project.init("no-store-yet", title="No Store Yet", root=tmp_path)

    with pytest.raises(StoreError, match="No Layer 1 store"):
        connect(project, read_only=True)


@pytest.mark.integration
def test_connect__corrupt_database_file__raises_store_error(tmp_path: Path) -> None:
    project = Project.init("corrupt-store", title="Corrupt Store", root=tmp_path)
    project.db_path.write_bytes(b"not a real duckdb file")

    with pytest.raises(StoreError, match="Could not open the Layer 1 store"):
        connect(project, read_only=True)
