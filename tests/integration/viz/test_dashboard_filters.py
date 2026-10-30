"""The dashboard's sidebar controls are wired: each one changes what a tab renders.

View-model level, no browser (ADR 0025 Decision 5). Each test below failed
before the controls were wired: the year slider rebuilt every tab with
unfiltered data, the two selectors had no watcher, and an empty or
single-year corpus produced a Bokeh E-1021 (equal slider start/end).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import polars as pl
import pytest
from bokeh.core.validation import check_integrity

from prismabib.bibliometrics.base import restrict_to_years
from prismabib.errors import AnalysisError, ConfigError
from prismabib.viz.dashboard import Dashboard, FilterState, _year_filtered_frame
from tests.bibliometrics_helpers import (
    BibCorpusSpec,
    BibRecordSpec,
    build_bib_project,
    include_everything,
)
from tests.taxonomy_helpers import (
    ARCHITECTURE_RULES_V1,
    LEARNING_PARADIGM_RULES_V1,
    TaxonomyRecordSpec,
    build_taxonomy_project,
    write_dimensions,
    write_rule_file,
)

if TYPE_CHECKING:
    from pathlib import Path

    from prismabib.project import Project


def _texts(obj: Any) -> str:
    """Every Markdown string and Tabulator value under a Panel object, newline-joined."""
    found: list[str] = []

    def walk(node: Any) -> None:
        if hasattr(node, "object") and isinstance(node.object, str):
            found.append(node.object)
        if hasattr(node, "value") and hasattr(node.value, "to_csv"):
            found.append(node.value.to_csv(index=False))
        for child in getattr(node, "objects", ()):
            walk(child)

    walk(obj)
    return "\n".join(found)


def _tab(dashboard: Dashboard, name: str) -> Any:
    return dict(zip(dashboard._tabs._names, dashboard._tabs.objects, strict=True))[name]


def _bib_project(
    tmp_path: Path,
    years: list[int],
    *,
    include: bool = True,
    run_started_ats: tuple[datetime, ...] = (datetime(2025, 6, 15, tzinfo=UTC),),
    **kw: Any,
) -> Project:
    project = build_bib_project(
        tmp_path,
        BibCorpusSpec(
            records=[
                BibRecordSpec(number=i, year=year, cited_by_count=10 * i, **kw)
                for i, year in enumerate(years, start=1)
            ],
            run_started_ats=run_started_ats,
        ),
    )
    if include:
        include_everything(project)
    return project


@pytest.mark.integration
@pytest.mark.parametrize(
    ("years", "include", "disabled"),
    [
        pytest.param([2020, 2020, 2020], True, True, id="single-year"),
        pytest.param([2019, 2021], False, True, id="empty-stage"),
        pytest.param([2019, 2021], True, False, id="multi-year"),
    ],
)
def test_year_slider__any_corpus__passes_bokeh_integrity(
    tmp_path: Path, years: list[int], include: bool, disabled: bool
) -> None:
    dashboard = Dashboard(_bib_project(tmp_path, years, include=include))
    slider = dashboard._year_range

    issues = check_integrity([slider.get_root()])

    assert issues.error == []
    assert slider.start < slider.end
    assert slider.disabled is disabled
    assert ("Year range (" in slider.name) is disabled


@pytest.mark.integration
def test_year_range__changed__reaches_every_year_aware_tab(tmp_path: Path) -> None:
    dashboard = Dashboard(
        _bib_project(tmp_path, [2019, 2020, 2021, 2022], author_keywords=("baseball",))
    )
    before = {name: _texts(_tab(dashboard, name)) for name in ("Overview", "Corpus browser")}

    dashboard._year_range.value = (2020, 2021)

    assert dashboard.filter_state.year_start == 2020
    assert dashboard.filter_state.year_end == 2021
    browser = _texts(_tab(dashboard, "Corpus browser"))
    assert "2 of 4 records" in browser
    assert browser != before["Corpus browser"]
    overview = _texts(_tab(dashboard, "Overview"))
    assert overview != before["Overview"]
    # Trends: only the in-range years are plotted.
    trends_years = {int(x) for trace in _tab(dashboard, "Trends")[0].object.data for x in trace.x}
    assert trends_years == {2020, 2021}
    # The PRISMA/flow count is never year-filtered.
    assert "n included = 4" in overview


@pytest.mark.integration
def test_year_range__views_without_per_year_rows__say_they_are_unfiltered(tmp_path: Path) -> None:
    dashboard = Dashboard(_bib_project(tmp_path, [2019, 2020, 2021]))

    dashboard._year_range.value = (2020, 2021)

    for name in ("Geography", "Venues", "Network"):
        assert "does not apply to this view" in _texts(_tab(dashboard, name))


@pytest.fixture
def taxonomy_project(tmp_path: Path) -> Project:
    project = build_taxonomy_project(
        tmp_path,
        [
            TaxonomyRecordSpec(
                number=i, title=f"AlphaMarker {i}", author_keywords=("DeltaMarker",), year=year
            )
            for i, year in enumerate((2019, 2020, 2021), start=1)
        ],
    )
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    write_rule_file(project, "architecture", ARCHITECTURE_RULES_V1)
    return project


def _taxonomy_dimensions_drawn(dashboard: Dashboard) -> set[str]:
    figure = _tab(dashboard, "Taxonomy")[0].object
    return {trace.name for trace in figure.data}


@pytest.mark.integration
def test_taxonomy_selector__picks_the_dimension_the_tab_renders(taxonomy_project: Project) -> None:
    dashboard = Dashboard(taxonomy_project)
    everything = _taxonomy_dimensions_drawn(dashboard)

    dashboard._taxonomy_dimension_selector.value = "architecture"
    architecture_only = _taxonomy_dimensions_drawn(dashboard)
    dashboard._taxonomy_dimension_selector.value = "(all)"

    assert dashboard.filter_state.taxonomy_dimension is None
    assert _taxonomy_dimensions_drawn(dashboard) == everything
    assert architecture_only != everything
    assert architecture_only < everything


@pytest.mark.integration
def test_taxonomy_dimension__undeclared__fails_loudly(taxonomy_project: Project) -> None:
    dashboard = Dashboard(taxonomy_project)

    with pytest.raises(ConfigError, match="not_a_dimension"):
        dashboard.apply_filters(taxonomy_dimension="not_a_dimension")


@pytest.mark.integration
def test_citation_snapshot_selector__changes_the_snapshot_the_citation_views_read(
    tmp_path: Path,
) -> None:
    early, late = datetime(2025, 3, 1, tzinfo=UTC), datetime(2025, 9, 1, tzinfo=UTC)
    # Two runs, one record each: record 1 is snapshotted at `early`, record 2 at `late`.
    project = _bib_project(tmp_path, [2020, 2021], run_started_ats=(early, late))
    dashboard = Dashboard(project)
    latest = _texts(_tab(dashboard, "Overview"))
    options = dashboard._citation_snapshot_selector.options
    assert len(options) == 3

    dashboard._citation_snapshot_selector.value = options[1]  # the earlier snapshot

    assert dashboard.filter_state.citation_snapshot is not None
    as_of_early = _texts(_tab(dashboard, "Overview"))
    assert as_of_early != latest
    assert "2025-03-01" in as_of_early

    dashboard._citation_snapshot_selector.value = "(latest)"

    assert dashboard.filter_state.citation_snapshot is None
    assert _texts(_tab(dashboard, "Overview")) == latest


@pytest.mark.integration
def test_apply_filters__start_after_end__raises_and_leaves_state_and_tabs_untouched(
    tmp_path: Path,
) -> None:
    dashboard = Dashboard(_bib_project(tmp_path, [2019, 2020, 2021, 2022]))
    dashboard.apply_filters(year_start=2020, year_end=2021)
    before = dashboard.filter_state
    browser_before = _texts(_tab(dashboard, "Corpus browser"))

    with pytest.raises(AnalysisError, match=r"2022.*2021"):
        dashboard.apply_filters(year_start=2022)

    assert dashboard.filter_state is before
    assert _texts(_tab(dashboard, "Corpus browser")) == browser_before
    dashboard.apply_filters(year_end=2022)  # the next valid change still rebuilds
    assert dashboard.filter_state.year_end == 2022
    assert "3 of 4 records" in _texts(_tab(dashboard, "Corpus browser"))


@pytest.mark.integration
def test_filter_state__start_after_end__raises_on_construction() -> None:
    with pytest.raises(AnalysisError):
        FilterState(year_start=2022, year_end=2020)
    assert FilterState(year_start=2020, year_end=2020).year_start == 2020


@pytest.mark.integration
def test_apply_filters__undeclared_dimension__keeps_the_previous_state(
    taxonomy_project: Project,
) -> None:
    dashboard = Dashboard(taxonomy_project)
    before = dashboard.filter_state

    with pytest.raises(ConfigError):
        dashboard.apply_filters(taxonomy_dimension="not_a_dimension")

    assert dashboard.filter_state is before
    dashboard.apply_filters(taxonomy_dimension="architecture")  # no longer poisoned
    assert dashboard.filter_state.taxonomy_dimension == "architecture"


_YEAR_CASES = [
    pytest.param((2020, 2021), id="inclusive-both"),
    pytest.param((2021, None), id="open-end"),
    pytest.param((None, 2020), id="open-start"),
    pytest.param((2022, 2022), id="single-year"),
    pytest.param((1990, 1991), id="outside-the-data"),
    pytest.param((None, None), id="no-range"),
]


@pytest.mark.integration
@pytest.mark.parametrize("years", _YEAR_CASES)
def test_year_filtered_frame__agrees_with_restrict_to_years(
    years: tuple[int | None, int | None],
) -> None:
    frame = pl.DataFrame(
        {"record_id": ["a", "b", "c", "d", "e", "f"], "year": [2020, None, 2019, 2022, None, 2021]},
        schema={"record_id": pl.String, "year": pl.Int64},
    )

    filtered = _year_filtered_frame(frame, FilterState(year_start=years[0], year_end=years[1]))

    assert filtered.equals(restrict_to_years(frame, years)[0])


@pytest.mark.integration
def test_year_filtered_frame__frame_without_year__is_returned_unchanged() -> None:
    frame = pl.DataFrame({"k": [1, 2]})

    assert _year_filtered_frame(frame, FilterState(year_start=2020)) is frame
