"""Integration tests for ``store/db.py::connect`` (BUILD_PLAN §Stage 3 contract, line 892).

Real filesystem, real DuckDB, no network (§3.7.2) -- both failure paths
``connect`` documents (a missing store opened read-only, and a file DuckDB
itself refuses to open) are exercised against a real ``Project`` skeleton,
never monkeypatched (§3.7.3 rule 1: calling is not mocking).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from prismabib.errors import StoreError
from prismabib.project import Project
from prismabib.store.db import connect
from prismabib.store.load import build_store
from tests.store_helpers import make_entry, write_sealed_run

_RUN_ID = "20250101T000000Z-11111111"
_RUN_STARTED_AT = datetime(2025, 1, 1, tzinfo=UTC)


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


@pytest.mark.integration
def test_connect__store_missing_a_table_this_build_expects__raises_pointing_at_rebuild(
    tmp_path: Path,
) -> None:
    """A store built before a schema addition is refused with instructions.

    `schema.sql` is applied once at creation and never migrated; ADRs 0012,
    0013 and 0018 have each added a table since. A store predating one of them
    simply lacks it, and the first query against that table raised a raw
    DuckDB ``CatalogException`` -- complete with a "Did you mean
    record_authors?" suggestion -- at a researcher who did nothing wrong and
    had no way to read it as "rebuild your store".

    This is not hypothetical: v0.15.0 broke every store built before it, which
    is how the gap was found. Dropping a table from a real store is the
    honest way to reproduce that, since it is exactly the state an older
    prismabib left behind.
    """
    project = Project.init("stale", title="Stale", root=tmp_path)
    write_sealed_run(
        project.raw_dir,
        _RUN_ID,
        [make_entry(eid="2-s2.0-900000000001")],
        started_at=_RUN_STARTED_AT,
        total_results=1,
    )
    build_store(project, rebuild=True)
    writable = connect(project, read_only=False)
    try:
        writable.execute("DROP TABLE record_subject_area_coverage")
    finally:
        writable.close()

    with pytest.raises(StoreError, match="--rebuild") as excinfo:
        connect(project, read_only=True)

    assert "record_subject_area_coverage" in str(excinfo.value)
    assert "no API quota is spent" in str(excinfo.value)


@pytest.mark.integration
def test_connect__a_current_store__opens_without_complaint(tmp_path: Path) -> None:
    """The guard does not fire on a store this build just created.

    Paired with the test above deliberately: a check that refuses everything
    would satisfy that one on its own.
    """
    project = Project.init("current", title="Current", root=tmp_path)
    write_sealed_run(
        project.raw_dir,
        _RUN_ID,
        [make_entry(eid="2-s2.0-900000000001")],
        started_at=_RUN_STARTED_AT,
        total_results=1,
    )
    build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        assert connection.execute("SELECT count(*) FROM records").fetchone() == (1,)
    finally:
        connection.close()
