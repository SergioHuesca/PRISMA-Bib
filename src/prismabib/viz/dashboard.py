"""The Panel dashboard (BUILD_PLAN §Stage 9).

Sidebar: project selector, year range, a taxonomy dimension selector, a
citation-snapshot selector. Main area: tabs -- Overview, PRISMA, Trends,
Geography, Venues, Keywords, Network, Taxonomy, Corpus browser.

**ADR 0025 Decision 7: "All filters operate on the same ``Corpus`` handle so
every tab is consistent by construction."** Construction, not convention --
:class:`Dashboard` opens exactly one :class:`~prismabib.store.load.Corpus`
in ``__init__`` and every tab-building method reads it off ``self._corpus``;
:meth:`Dashboard.corpus_handle_for_tab` exists so a test can read that
identity back per tab and compare (``test_dashboard__filter_change__all_tabs_read_the_same_corpus_handle``)
rather than merely asserting it by inspection.

**The filter state is frozen, never mutated in place.** :class:`FilterState`
is an immutable dataclass; :meth:`Dashboard.apply_filters` replaces it with
``dataclasses.replace`` and re-renders from the new state, so no tab can
hold a private, drifted copy.

This module is not itself covered by ``viz/figures.py``'s AST scan --
that rule is scoped to the figure layer specifically (ADR 0025 Constraints:
"No arithmetic ... in ``viz/figures.py``"). Filtering an already-computed
:class:`~prismabib.bibliometrics.base.AnalysisResult`'s ``data`` down to a
sidebar-selected year range here is presentation-layer row selection over a
number Stage 7 already computed, the same category of operation
``viz/figures.py`` itself performs via ``.head()``/``.filter()`` -- not a
new computation.

**On test coverage (ADR 0025 Decision 5).** "The dashboard is not covered
by CI beyond a headless smoke test and a benchmark. Its real surface is a
browser, which this stage does not automate." Stated here again because it
bears repeating at the one call site a reader is most likely to open next.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

import pandas as pd
import panel as pn
import param
import polars as pl

from prismabib.bibliometrics import citations, geography, keywords, network, trends, venues
from prismabib.bibliometrics.base import (
    AnalysisResult,
    normalise_years,
    restrict_to_years,
    years_label,
)
from prismabib.errors import ConfigError
from prismabib.prisma.flow import compute_flow_counts
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus
from prismabib.taxonomy.analysis import dimension_distribution, dimension_evolution
from prismabib.taxonomy.coder import code
from prismabib.taxonomy.overrides import OverrideLog
from prismabib.taxonomy.rules import load_rule_file
from prismabib.taxonomy.schema import CountingUnit, load_dimensions
from prismabib.viz import figures

if TYPE_CHECKING:
    from collections.abc import Sequence

    from prismabib.project import Project
    from prismabib.taxonomy.coder import CodingResult
    from prismabib.taxonomy.schema import TaxonomySchema

#: The tab order BUILD_PLAN's dashboard layout names, verbatim.
TAB_NAMES: tuple[str, ...] = (
    "Overview",
    "PRISMA",
    "Trends",
    "Geography",
    "Venues",
    "Keywords",
    "Network",
    "Taxonomy",
    "Corpus browser",
)


@dataclass(frozen=True)
class FilterState:
    """The dashboard's whole filter state -- one immutable value, replaced, never mutated.

    Attributes:
        stage: Which named PRISMA-flow set every tab reads.
        year_start: Inclusive lower bound on ``year``, or ``None`` for no
            lower bound.
        year_end: Inclusive upper bound on ``year``, or ``None`` for no
            upper bound.
        taxonomy_dimension: Which declared dimension the Taxonomy tab shows,
            or ``None`` for "every declared dimension".
        citation_snapshot: Which citation snapshot to read "as of", or
            ``None`` for the latest per record.
    """

    stage: PrismaStage = PrismaStage.INCLUDED
    year_start: int | None = None
    year_end: int | None = None
    taxonomy_dimension: str | None = None
    citation_snapshot: datetime | None = None

    def __post_init__(self) -> None:
        # A start after the end raises here, so no code path can hold (or assign)
        # an invalid state; the same `AnalysisError` the engines raise (ADR 0040).
        normalise_years((self.year_start, self.year_end))


def _year_filtered(result: AnalysisResult, state: FilterState) -> AnalysisResult:
    """``result`` with its ``data`` restricted to ``state``'s year range, if it has a ``year`` column.

    Args:
        result: Any analysis result.
        state: The active filter state.

    Returns:
        ``result`` unchanged if ``data`` carries no ``year`` column, or if
        neither bound is set; otherwise a new
        :class:`~prismabib.bibliometrics.base.AnalysisResult` with the same
        ``params``/``provenance`` and a row-filtered ``data``.
    """
    if state.year_start is None and state.year_end is None:
        return result
    # `provenance` (hence the caption's `n`) deliberately stays the stage's
    # count, which equals the PRISMA flow count; the range is stated beside it.
    return dataclasses.replace(result, data=_year_filtered_frame(result.data, state))


def _year_filtered_frame(frame: pl.DataFrame, state: FilterState) -> pl.DataFrame:
    """``frame`` restricted to ``state``'s inclusive year range, if it has a ``year`` column.

    A null ``year`` is in no range, so such rows drop once any bound is set;
    callers that show counts say so.
    """
    if "year" not in frame.columns:
        return frame
    return restrict_to_years(frame, (state.year_start, state.year_end))[0]


def _year_range_text(state: FilterState) -> str:
    """The active year range as ``"2020-2021"`` (open end as ``"..."``); ``""`` when unbounded."""
    return years_label((state.year_start, state.year_end)) or ""


def _load_taxonomy(
    project: Project, corpus: Corpus
) -> tuple[TaxonomySchema, CodingResult, dict[str, CountingUnit]] | None:
    """Load and code every declared taxonomy dimension for ``project``, if any exists.

    Args:
        project: The project to load ``taxonomy/dimensions.yaml`` and every
            ``taxonomy/rules/*.yaml`` file for.

    Returns:
        ``(schema, coding_result)``, or ``None`` when the project has not
        declared a taxonomy at all -- an ordinary state (the reference
        fixture is one), not an error: the Taxonomy tab renders a message
        instead of a figure in that case.
    """
    try:
        schema = load_dimensions(project)
    except ConfigError:
        return None
    rule_files = [
        load_rule_file(path, schema=schema)
        for path in sorted(project.taxonomy_rules_dir.glob("*.yaml"))
    ]
    if not rule_files:
        return None
    overrides = OverrideLog(project).load()
    # The caller's handle, never a second one opened here. Coding against a
    # privately-opened `Corpus` made the Taxonomy tab's data come from a
    # different handle than every other tab -- including when a caller had
    # explicitly supplied one -- which is precisely what ADR 0025 Decision 7
    # forbids, and it leaked the extra handle besides.
    result = code(corpus, rule_files, overrides)
    # The unit each rule file *declares*, not a hardcoded one. ADR 0023
    # makes `counting_unit` mandatory in every rule file precisely so the
    # figure states the unit its author chose; the dashboard passing
    # `PAPERS` for everything meant a multi-label dimension was captioned
    # "counting_unit=papers" while its counts summed past the paper count --
    # which is the reading ADR 0023 Decision 4 forbids.
    units = {rule_file.dimension: rule_file.counting_unit for rule_file in rule_files}
    return schema, result, units


class Dashboard:
    """One reviewer's dashboard session: one project, one ``Corpus`` handle, nine tabs.

    See this module's docstring for the "same handle" and "frozen filter
    state" guarantees.
    """

    def __init__(self, project: Project, *, corpus: Corpus | None = None) -> None:
        """Build every tab against one freshly-opened (or supplied) corpus.

        Args:
            project: The project to open a dashboard onto.
            corpus: An already-open :class:`~prismabib.store.load.Corpus`
                handle, for a caller that already has one (a test, or a
                notebook cell reusing a handle across cells). ``None`` opens
                a fresh read-only one.
        """
        self._project = project
        self._corpus = corpus if corpus is not None else Corpus.open(project, read_only=True)
        self._filter_state = FilterState()
        self._taxonomy = _load_taxonomy(project, self._corpus)
        self._tab_corpus_handles: dict[str, Corpus] = {}
        self._panes: dict[str, Any] = {}
        self._tabs = self._build_tabs()
        self._sidebar = self._build_sidebar()

    @property
    def corpus(self) -> Corpus:
        """The one :class:`~prismabib.store.load.Corpus` handle every tab reads."""
        return self._corpus

    @property
    def filter_state(self) -> FilterState:
        """The dashboard's current, immutable filter state."""
        return self._filter_state

    def corpus_handle_for_tab(self, tab_name: str) -> Corpus:
        """The :class:`~prismabib.store.load.Corpus` handle a given tab was built against.

        Args:
            tab_name: One of :data:`TAB_NAMES`.

        Returns:
            The handle -- always :attr:`corpus`, by construction (ADR 0025
            Decision 7). Exists so a test reads the identity back per tab
            rather than asserting it by code inspection.
        """
        return self._tab_corpus_handles[tab_name]

    def view(self) -> pn.viewable.Viewable:
        """The full dashboard: sidebar plus tabs."""
        return pn.Row(self._sidebar, self._tabs)

    # -- sidebar --------------------------------------------------------

    def _build_sidebar(self) -> pn.viewable.Viewable:
        years = (
            trends.annual_counts(self._corpus, stage=self._filter_state.stage)
            .data["year"]
            .to_list()
        )
        # First and last of an already-sorted column, not `min`/`max` over
        # it: `annual_counts` sorts by year, so the bounds are an *index*
        # into the engine's output rather than an aggregation recomputed
        # here. The distinction is the whole layering rule -- a sidebar that
        # derives the corpus's year range itself is a second definition of
        # it, and the AST guard now says so.
        # Bokeh rejects start == end (E-1021), and this layer may not do
        # arithmetic (so no widening to (y, y + 1)): fewer than two distinct
        # years means there is no range to choose, hence a disabled placeholder.
        year_label = "Year range"
        selectable = bool(years) and years[0] != years[-1]
        if selectable:
            year_bounds = (years[0], years[-1])
        else:
            year_bounds = (0, 1)
            year_label = (
                f"Year range (only {years[0]} at this stage)"
                if years
                else "Year range (no dated records at this stage)"
            )

        self._project_selector = pn.widgets.Select(
            label="Project", options=[self._project.slug], value=self._project.slug
        )
        self._year_range = pn.widgets.IntRangeSlider(
            label=year_label,
            start=year_bounds[0],
            end=year_bounds[1],
            value=year_bounds,
            disabled=not selectable,
        )
        self._year_range.param.watch(self._on_year_range_changed, "value")

        dimension_options = ["(all)"]
        if self._taxonomy is not None:
            schema, _result, _units = self._taxonomy
            dimension_options.extend(dimension.id for dimension in schema.dimensions)
        self._taxonomy_dimension_selector = pn.widgets.Select(
            label="Taxonomy dimension", options=dimension_options, value=dimension_options[0]
        )
        self._taxonomy_dimension_selector.param.watch(self._on_taxonomy_dimension_changed, "value")

        snapshot_frame = self._corpus.citations()
        snapshot_options = ["(latest)"]
        if snapshot_frame.height:
            snapshot_options.extend(
                sorted({value.isoformat() for value in snapshot_frame["retrieved_at"].to_list()})
            )
        self._citation_snapshot_selector = pn.widgets.Select(
            label="Citation snapshot", options=snapshot_options, value=snapshot_options[0]
        )
        self._citation_snapshot_selector.param.watch(self._on_citation_snapshot_changed, "value")

        return pn.Column(
            pn.pane.Markdown(f"## {self._project.title}"),
            self._project_selector,
            self._year_range,
            self._taxonomy_dimension_selector,
            self._citation_snapshot_selector,
        )

    def _on_year_range_changed(self, event: param.parameterized.Event) -> None:
        start, end = event.new
        self.apply_filters(year_start=start, year_end=end)

    # These two must be able to go *back* to "(all)"/"(latest)", i.e. to
    # `None`, which `apply_filters` reads as "keep the current value".
    def _on_taxonomy_dimension_changed(self, event: param.parameterized.Event) -> None:
        dimension = None if event.new == "(all)" else str(event.new)
        self._set_filter_state(
            dataclasses.replace(self._filter_state, taxonomy_dimension=dimension)
        )

    def _on_citation_snapshot_changed(self, event: param.parameterized.Event) -> None:
        snapshot = None if event.new == "(latest)" else datetime.fromisoformat(str(event.new))
        self._set_filter_state(dataclasses.replace(self._filter_state, citation_snapshot=snapshot))

    def apply_filters(
        self,
        *,
        stage: PrismaStage | None = None,
        year_start: int | None = None,
        year_end: int | None = None,
        taxonomy_dimension: str | None = None,
        citation_snapshot: datetime | None = None,
    ) -> None:
        """Replace :attr:`filter_state` and re-render every tab affected.

        Args:
            stage: New value, or ``None`` to keep the current one.
            year_start: New value, or ``None`` to keep the current one --
                **not** "clear the bound"; pass the field's own ``None``
                meaning by constructing a fresh :class:`FilterState`
                directly if that is genuinely what is wanted.
            year_end: See ``year_start``.
            taxonomy_dimension: New value, or ``None`` to keep the current one.
            citation_snapshot: New value, or ``None`` to keep the current one.

        Raises:
            AnalysisError: The resulting year range has its start after its end.
            ConfigError: The taxonomy dimension is not declared.
            In both cases :attr:`filter_state` and the tabs are left as they were.
        """
        current = self._filter_state
        self._set_filter_state(
            FilterState(
                stage=stage if stage is not None else current.stage,
                year_start=year_start if year_start is not None else current.year_start,
                year_end=year_end if year_end is not None else current.year_end,
                taxonomy_dimension=(
                    taxonomy_dimension
                    if taxonomy_dimension is not None
                    else current.taxonomy_dimension
                ),
                citation_snapshot=(
                    citation_snapshot
                    if citation_snapshot is not None
                    else current.citation_snapshot
                ),
            )
        )

    def _set_filter_state(self, state: FilterState) -> None:
        """Adopt ``state`` and rebuild the tabs; if the rebuild raises, keep everything as it was.

        "Everything" is the filter state, the tabs, and the two dicts the builders fill as
        they run (``_tab_corpus_handles``, ``_panes``), which a mid-build failure would
        otherwise leave half-populated.

        An invalid ``state`` never gets here (``FilterState`` validates on construction).
        Without the restore, a state that builds but then fails to render (an undeclared
        taxonomy dimension) stayed assigned and made every later rebuild raise too.
        """
        previous = self._filter_state
        handles, panes = dict(self._tab_corpus_handles), dict(self._panes)
        self._filter_state = state
        try:
            self._rebuild_tabs()
        except Exception:
            self._filter_state = previous
            self._tab_corpus_handles.clear()
            self._tab_corpus_handles.update(handles)
            self._panes.clear()
            self._panes.update(panes)
            raise

    def _rebuild_tabs(self) -> None:
        """Rebuild every tab against the current filter state.

        The first version refreshed a single pane -- ``Trends`` was the only
        tab that registered one -- so a filter change left Geography,
        Venues, Keywords, Network, Taxonomy and the Corpus browser showing
        pre-filter data. Sharing one ``Corpus`` handle guarantees nothing
        against that: "every tab is consistent by construction" (ADR 0025
        Decision 7) is defeated just as thoroughly by re-rendering one of
        nine as by reading two handles.

        It also made S09-AC4 measure the wrong thing. The <1 s budget was
        being timed against one redrawn Plotly figure rather than against
        the filter change the criterion describes.
        """
        rebuilt = self._build_tabs()
        self._tabs.objects = rebuilt.objects

    # -- tabs -------------------------------------------------------------

    def _build_tabs(self) -> pn.Tabs:
        builders = (
            ("Overview", self._overview_tab),
            ("PRISMA", self._prisma_tab),
            ("Trends", self._trends_tab),
            ("Geography", self._geography_tab),
            ("Venues", self._venues_tab),
            ("Keywords", self._keywords_tab),
            ("Network", self._network_tab),
            ("Taxonomy", self._taxonomy_tab),
            ("Corpus browser", self._corpus_browser_tab),
        )
        # Deliberately *not* `self._tab_corpus_handles[name] = self._corpus`
        # here. Writing the handle from this loop made
        # `corpus_handle_for_tab` a dict every value of which the loop had
        # just set, so the consistency test compared `self._corpus is
        # self._corpus` nine times and no tab could ever fail it. The
        # recording now happens inside `_corpus_for`, at the point a tab
        # actually reads the handle, so a tab that opens its own is absent
        # from the mapping and the test says so.
        self._tab_corpus_handles.clear()
        panels = [(name, builder()) for name, builder in builders]
        return pn.Tabs(*panels)

    def _corpus_for(self, tab: str) -> Corpus:
        """The shared handle, recording that ``tab`` read it.

        Args:
            tab: The tab's display name.

        Returns:
            :attr:`corpus` -- the one handle every tab must share (ADR 0025
            Decision 7). Recording happens here rather than in the tab loop
            so that the record is produced by the code under test.
        """
        self._tab_corpus_handles[tab] = self._corpus
        return self._corpus

    def _caption_pane(self, text: str, *, by_year: bool = True) -> pn.pane.Markdown:
        """A caption, plus how the year range touched this view (when one is set).

        ``by_year=False`` marks a view whose engine returns no per-year rows to
        restrict, so it is said to be unfiltered rather than left looking filtered.
        """
        years = _year_range_text(self._filter_state)
        note = (
            f" Year range {years} applied."
            if by_year
            else f" Year range {years} does not apply to this view (not broken down by year)."
        )
        return pn.pane.Markdown(f"*{text}{note if years else ''}*")

    def _overview_tab(self) -> pn.viewable.Viewable:
        counts = compute_flow_counts(self._project)
        corpus = self._corpus_for("Overview")
        state = self._filter_state
        stats = citations.citation_statistics(corpus, stage=state.stage, at=state.citation_snapshot)
        per_year = _year_filtered(
            citations.citations_by_year(corpus, stage=state.stage, at=state.citation_snapshot),
            state,
        )
        per_year_table = pn.widgets.Tabulator(
            pd.DataFrame({name: per_year.data[name].to_list() for name in per_year.data.columns}),
            disabled=True,
        )
        # `n included` is the flow count and is never year-filtered: it must
        # equal the PRISMA tab's.
        return pn.Column(
            pn.pane.Markdown(f"# {self._project.title}"),
            pn.pane.Markdown(
                f"n included = {counts.included}; criteria version {self._project.criteria.version}"
            ),
            self._caption_pane(figures._caption(stats), by_year=False),
            per_year_table,
            self._caption_pane(figures._caption(per_year)),
        )

    def _prisma_tab(self) -> pn.viewable.Viewable:
        counts = compute_flow_counts(self._project)
        svg, _mpl_figure = figures.prisma_flow_figure(counts, self._project)
        return pn.Column(
            pn.pane.HTML(svg),
            self._caption_pane(figures.prisma_flow_caption(counts, self._project)),
        )

    def _trends_tab(self) -> pn.viewable.Viewable:
        result = _year_filtered(
            trends.annual_counts(self._corpus_for("Trends"), stage=self._filter_state.stage),
            self._filter_state,
        )
        plotly_figure, _mpl_figure = figures.trend_figure(result)
        pane = pn.pane.Plotly(plotly_figure)
        self._panes["Trends"] = pane
        return pn.Column(pane, self._caption_pane(figures._caption(result)))

    # Geography, Venues and Network aggregate over the whole stage: their
    # engines take no year range and return no per-year rows, so the year
    # filter cannot reach them and their captions say so.
    def _geography_tab(self) -> pn.viewable.Viewable:
        result = geography.country_counts(
            self._corpus_for("Geography"), stage=self._filter_state.stage
        )
        plotly_figure, _mpl_figure = figures.geography_figure(result)
        return pn.Column(
            pn.pane.Plotly(plotly_figure),
            self._caption_pane(figures._caption(result), by_year=False),
        )

    def _venues_tab(self) -> pn.viewable.Viewable:
        result = venues.top_venues(self._corpus_for("Venues"), stage=self._filter_state.stage)
        plotly_figure, _mpl_figure = figures.venues_figure(result)
        return pn.Column(
            pn.pane.Plotly(plotly_figure),
            self._caption_pane(figures._caption(result), by_year=False),
        )

    def _keywords_tab(self) -> pn.viewable.Viewable:
        result = _year_filtered(
            keywords.keyword_evolution(
                self._corpus_for("Keywords"), stage=self._filter_state.stage, min_occurrence=1
            ),
            self._filter_state,
        )
        plotly_figure, _mpl_figure = figures.keywords_figure(result)
        return pn.Column(
            pn.pane.Plotly(plotly_figure), self._caption_pane(figures._caption(result))
        )

    def _network_tab(self) -> pn.viewable.Viewable:
        result = network.keyword_cooccurrence_network(
            self._corpus_for("Network"), stage=self._filter_state.stage, min_occurrence=1
        )
        plotly_figure, _mpl_figure = figures.network_figure(result)
        return pn.Column(
            pn.pane.Plotly(plotly_figure),
            self._caption_pane(figures._caption(result), by_year=False),
        )

    def _taxonomy_tab(self) -> pn.viewable.Viewable:
        # Registered before the early return: this tab is bound to the
        # shared handle whether or not the project declares a taxonomy, and
        # "no dimensions declared" is a fact about the project rather than a
        # reason to read a different corpus.
        corpus = self._corpus_for("Taxonomy")
        if self._taxonomy is None:
            return pn.pane.Markdown(
                "No `taxonomy/dimensions.yaml` declared for this project -- nothing to show."
            )
        schema, coding_result, units = self._taxonomy
        wanted = self._filter_state.taxonomy_dimension
        dimension_ids = [d.id for d in schema.dimensions if wanted in (None, d.id)]
        if not dimension_ids:
            raise ConfigError(f"Taxonomy dimension {wanted!r} is not declared in dimensions.yaml.")
        distribution_results: Sequence[AnalysisResult] = [
            dimension_distribution(corpus, coding_result, schema, dim_id, units[dim_id])
            for dim_id in dimension_ids
        ]
        evolution_results: Sequence[AnalysisResult] = [
            _year_filtered(
                dimension_evolution(corpus, coding_result, schema, dim_id, units[dim_id]),
                self._filter_state,
            )
            for dim_id in dimension_ids
        ]
        dist_plotly, _dist_mpl = figures.taxonomy_distribution_figure(distribution_results)
        evo_plotly, _evo_mpl = figures.taxonomy_evolution_figure(evolution_results)
        return pn.Column(
            pn.pane.Plotly(dist_plotly),
            self._caption_pane("Distribution over every year.", by_year=False),
            pn.pane.Plotly(evo_plotly),
            self._caption_pane("Evolution by year."),
        )

    def _corpus_browser_tab(self) -> pn.viewable.Viewable:
        all_records = self._corpus_for("Corpus browser").records(self._filter_state.stage)
        records = _year_filtered_frame(all_records, self._filter_state)
        # `origin` is mandatory (ADR 0032): a missing column must fail loudly
        # here, never vanish from the table.
        columns = [
            "record_id",
            "origin",
            *(column for column in ("title", "year", "doc_type") if column in records.columns),
        ]
        # `polars.DataFrame.to_pandas()` requires `pyarrow`, not a declared
        # dependency (§2.4's closed list) -- `pandas.DataFrame(dict)` over
        # the columns as plain Python lists needs only `pandas`, which
        # already is one.
        as_pandas = pd.DataFrame({column: records[column].to_list() for column in columns})
        table = pn.widgets.Tabulator(as_pandas, disabled=True, page_size=25)
        years = _year_range_text(self._filter_state)
        summary = (
            f"{records.height} of {all_records.height} records (year range {years}; "
            "records without a year are excluded)"
            if years
            else f"{records.height} records"
        )
        return pn.Column(pn.pane.Markdown(summary), table)


def dashboard(project: Project, *, corpus: Corpus | None = None) -> pn.viewable.Viewable:
    """Return the dashboard's Panel view; display in a notebook cell or ``panel serve`` it.

    Mirrors :func:`prismabib.screening.ui.screener`'s own contract: one
    construction satisfies both the notebook and the served-app case via
    :meth:`~panel.viewable.Viewable.servable`.

    Args:
        project: The project to open a dashboard onto.
        corpus: See :meth:`Dashboard.__init__`.

    Returns:
        A Panel ``Viewable``.
    """
    pn.extension("plotly", "tabulator")
    instance = Dashboard(project, corpus=corpus)
    view = instance.view()
    return view.servable()


__all__ = ["TAB_NAMES", "Dashboard", "FilterState", "dashboard"]
