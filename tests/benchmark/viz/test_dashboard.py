"""S09-AC4: "Dashboard responds to a filter change in < 1 s on the reference corpus."

Deliberately not using the ``benchmark`` fixture -- ``pytest-benchmark``
disables itself under ``pytest-xdist``, and CI's ``full`` job runs
``-n auto``, so ``benchmark.stats`` is ``None`` there; see
``tests/benchmark/screening/test_ui.py`` for the same reasoning applied to
S05-AC3. A plain ``time.perf_counter()`` measurement is what stays
meaningful in the only environment §3.7.7 treats as authoritative.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from prismabib.viz.dashboard import Dashboard
from tests.bibliometrics_helpers import (
    AffiliationSpec,
    AuthorSpec,
    BibCorpusSpec,
    BibRecordSpec,
    build_bib_project,
    include_everything,
)

if TYPE_CHECKING:
    from pathlib import Path

    from prismabib.project import Project

#: The criterion itself (BUILD_PLAN's S09-AC4), in seconds.
BUDGET_SECONDS = 1.0

RECORD_COUNT = 40


@pytest.fixture
def project(tmp_path: Path) -> Project:
    records = [
        BibRecordSpec(
            number=i,
            year=2019 + (i % 4),
            cited_by_count=i,
            author_keywords=("baseball", "vision") if i % 2 else ("baseball", "audio"),
            affiliations=(AffiliationSpec(afid=f"AF{i}", country="USA" if i % 2 else "JPN"),),
            authors=(AuthorSpec(author_id=f"A{i % 3}", surname=f"Sur{i % 3}"),),
        )
        for i in range(1, RECORD_COUNT + 1)
    ]
    built = build_bib_project(
        tmp_path,
        BibCorpusSpec(records=records, run_started_ats=(datetime(2025, 6, 15, tzinfo=UTC),)),
        slug="dashboard-benchmark",
    )
    include_everything(built)
    return built


@pytest.mark.benchmark
@pytest.mark.acceptance("S09-AC4")
def test_dashboard__filter_change__completes_under_1s(project: Project) -> None:
    dashboard = Dashboard(project)

    start = time.perf_counter()
    dashboard.apply_filters(year_start=2020, year_end=2021)
    elapsed = time.perf_counter() - start

    assert elapsed < BUDGET_SECONDS
