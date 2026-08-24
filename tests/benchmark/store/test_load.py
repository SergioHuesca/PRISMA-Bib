"""Performance regression gate on ``build_store`` (BUILD_PLAN Stage 3 Tests table, line 925).

Deliberately NOT using the ``benchmark`` fixture. ``pytest-benchmark`` disables
itself whenever ``pytest-xdist`` is active -- and CI's ``full`` job runs
``pytest -n auto`` (§3.7.7 line 567), so the fixture is always disabled there.
When disabled it still injects an object, but ``benchmark.stats`` is ``None``,
and reading ``benchmark.stats.stats.max`` raised ``AttributeError`` on every CI
run while passing locally, where the suite runs single-process.

That is the worst shape a performance gate can take: green on the developer's
machine, crashing in the one environment whose verdict §3.7.7 says is
authoritative. Timing the call directly costs nothing here -- this measures a
single multi-second operation, not a microbenchmark needing warmup, outlier
rejection, or repeated rounds -- and it works identically with and without xdist.

The budget is a *ceiling on a regression*, not a target. ``build_store`` on this
fixture runs in roughly 0.2 s; it previously took 5.4 s because DuckDB's
``executemany`` charges a large per-call cost, and that regression is exactly
what this test exists to catch before it reaches a 1,771-record corpus.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from prismabib.store.load import build_store
from tests.store_helpers import copy_reference_project

_BUDGET_SECONDS = 5.0


@pytest.mark.benchmark
def test_build_store__120_records__completes_under_5s(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    project.db_path.unlink(missing_ok=True)

    started = time.perf_counter()
    stats = build_store(project, rebuild=True)
    elapsed = time.perf_counter() - started

    assert stats.records_loaded == 120
    assert elapsed < _BUDGET_SECONDS, (
        f"build_store took {elapsed:.2f}s for 120 records, over the "
        f"{_BUDGET_SECONDS}s budget of BUILD_PLAN line 925"
    )
