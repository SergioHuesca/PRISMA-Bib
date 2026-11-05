"""The OpenAlex counterpart of the unloadable-capture guard (mode ``openalex`` only)."""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from prismabib.errors import StoreError
from prismabib.store import stamp as store_stamp
from prismabib.store.load import build_store
from tests.openalex_search_helpers import openalex_project, work, write_search_run

pytestmark = pytest.mark.integration

_RUN_A = "20261103T100000Z-aaaaaaaa"
_RUN_B = "20261103T110000Z-bbbbbbbb"


def _bad(start: int, count: int) -> list[dict[str, object]]:
    return [work(n, title=None) for n in range(start, start + count)]


def _records(db_path: Path, table: str = "openalex_records") -> int:
    con = duckdb.connect(str(db_path), read_only=True)
    try:
        return int(con.execute(f"SELECT count(*) FROM {table}").fetchone()[0])  # type: ignore[index]
    finally:
        con.close()


def test_guard__every_work_malformed__refused_and_no_store_written(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [_bad(1, 3)])

    with pytest.raises(StoreError, match="OpenAlex") as exc:
        build_store(project, rebuild=True)

    assert "malformed_entries" in str(exc.value)
    assert "capture again" in str(exc.value)
    assert "Scopus" not in str(exc.value)
    assert not project.db_path.exists()
    assert not store_stamp.stamp_path(project.db_path).exists()


def test_guard__one_malformed_of_108__builds(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(n) for n in range(1, 108)] + _bad(200, 1)])

    build_store(project, rebuild=True)

    assert _records(project.db_path) == 107


def test_guard__systematic_share_of_malformed__refused(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(n) for n in range(1, 91)] + _bad(200, 10)])

    with pytest.raises(StoreError, match="10 of the 100"):
        build_store(project, rebuild=True)

    assert not project.db_path.exists()


def test_guard__below_ten_skips__never_fires_even_above_five_percent(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(n) for n in range(1, 21)] + _bad(200, 9)])

    build_store(project, rebuild=True)

    assert _records(project.db_path) == 20


def test_guard__malformed_copies_of_loaded_works__are_not_skips(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    good = [work(n) for n in range(1, 11)]
    copies = [work(n, title=None) for n in range(1, 11)]
    write_search_run(project, _RUN_A, "a", [good])
    write_search_run(project, _RUN_B, "b", [copies])

    build_store(project, rebuild=True)

    assert _records(project.db_path) == 10


def test_guard__refresh_run_of_the_same_query__does_not_dilute_the_ratio(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    good = [work(n) for n in range(1, 91)]
    write_search_run(project, _RUN_A, "q", [good + _bad(200, 10)])
    write_search_run(project, _RUN_B, "q", [good], offset_minutes=60)

    with pytest.raises(StoreError, match="10 of the 100"):
        build_store(project, rebuild=True)


def test_guard__scopus_mode_ignores_openalex_search_runs(tmp_path: Path) -> None:
    project = openalex_project(tmp_path, "scopus")
    write_search_run(project, _RUN_A, "q", [_bad(1, 3)])

    build_store(project, rebuild=True)

    assert _records(project.db_path, "records") == 0
