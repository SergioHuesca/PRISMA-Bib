"""The Panel dashboard: headless construction, and per-tab handle consistency.

ADR 0025 Decision 7: *"All filters operate on the same ``Corpus`` handle so
every tab is consistent by construction."* Construction, not convention, so
it is asserted here rather than assumed. The dashboard's own speed budget
(S09-AC4) is a wall-clock benchmark and lives in
``tests/benchmark/viz/test_dashboard.py`` instead -- see that module's
docstring for why.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from prismabib.viz.dashboard import TAB_NAMES, Dashboard
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


@pytest.fixture
def rich_project(tmp_path: Path) -> Project:
    records = [
        BibRecordSpec(
            number=i,
            year=2019 + (i % 4),
            cited_by_count=i,
            author_keywords=("baseball", "vision") if i % 2 else ("baseball", "audio"),
            affiliations=(AffiliationSpec(afid=f"AF{i}", country="USA" if i % 2 else "JPN"),),
            authors=(AuthorSpec(author_id=f"A{i % 3}", surname=f"Sur{i % 3}"),),
        )
        for i in range(1, 21)
    ]
    built = build_bib_project(
        tmp_path,
        BibCorpusSpec(records=records, run_started_ats=(datetime(2025, 6, 15, tzinfo=UTC),)),
        slug="dashboard-fixture",
    )
    include_everything(built)
    return built


@pytest.mark.integration
def test_dashboard__constructs_headlessly(rich_project: Project) -> None:
    dashboard = Dashboard(rich_project)

    view = dashboard.view()

    assert view is not None
    assert len(dashboard._tabs) == len(TAB_NAMES)


#: The tabs that read corpus data, and must therefore share one handle.
#: `Overview` and `PRISMA` read `FlowCounts` from the project instead, so
#: they legitimately record no handle -- named here rather than left as a
#: silent absence, so a tab that *stops* reading the corpus shows up as a
#: diff in this list rather than as a test that quietly checks one fewer
#: thing.
CORPUS_READING_TABS = (
    "Trends",
    "Geography",
    "Venues",
    "Keywords",
    "Network",
    "Taxonomy",
    "Corpus browser",
)


@pytest.mark.integration
@pytest.mark.parametrize("tab_name", CORPUS_READING_TABS)
def test_dashboard__filter_change__all_tabs_read_the_same_corpus_handle(
    rich_project: Project, tab_name: str
) -> None:
    """ADR 0025 Decision 7: consistency by construction, asserted rather than assumed.

    The first version of this test could not fail. `_build_tabs` recorded
    the handle from its own loop -- `self._tab_corpus_handles[name] =
    self._corpus` before calling the builder -- so the assertion compared
    `self._corpus is self._corpus` nine times and no tab could influence
    it. A tab was injected that opened its own `Corpus`, and the whole
    suite stayed green.

    The recording now happens in `Dashboard._corpus_for`, at the point a
    tab actually reads the handle, so a tab that opens its own is simply
    absent from the mapping.
    """
    dashboard = Dashboard(rich_project)

    dashboard.apply_filters(year_start=2020, year_end=2021)

    assert dashboard.corpus_handle_for_tab(tab_name) is dashboard.corpus


@pytest.mark.integration
def test_dashboard__filter_change__rebuilds_every_corpus_reading_tab(
    rich_project: Project,
) -> None:
    """A filter change must reach every tab, not just the one that registered a pane.

    `apply_filters` used to call `_refresh_trends_pane`, and `Trends` was
    the only tab that registered a pane -- so six tabs kept showing
    pre-filter data. One shared handle does not save that: re-rendering one
    tab of nine defeats "consistent by construction" exactly as thoroughly
    as reading two handles does.
    """
    dashboard = Dashboard(rich_project)
    dashboard.view()
    dashboard._tab_corpus_handles.clear()

    dashboard.apply_filters(year_start=2020, year_end=2021)

    assert set(dashboard._tab_corpus_handles) == set(CORPUS_READING_TABS)
