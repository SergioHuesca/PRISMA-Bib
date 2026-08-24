"""Performance regression gate on ``build_store`` (BUILD_PLAN Stage 3 Tests table, line 925).

A single ``pytest-benchmark`` measurement (``rounds=1``): each round deletes
and fully rebuilds the Layer 1 store from the 120-record reference fixture,
so multiple rounds would only multiply an already several-second operation
without adding statistical value the way a microbenchmark's rounds do.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from prismabib.store.load import StoreStats, build_store
from tests.store_helpers import copy_reference_project

_BUDGET_SECONDS = 5.0


@pytest.mark.benchmark
def test_build_store__120_records__completes_under_5s(tmp_path: Path, benchmark: Any) -> None:
    project = copy_reference_project(tmp_path)

    def _rebuild() -> StoreStats:
        # `missing_ok=True` rather than an `is_file()` guard: same effect, and it keeps
        # the §3.7.3 rule-9 scan clean rather than relying on a reader to classify a
        # setup branch as benign.
        project.db_path.unlink(missing_ok=True)
        return build_store(project, rebuild=True)

    stats = benchmark.pedantic(_rebuild, rounds=1, iterations=1)

    assert stats.records_loaded == 120
    assert benchmark.stats.stats.max < _BUDGET_SECONDS
