"""One function per required figure (BUILD_PLAN §Stage 9; ADR 0025).

**"Figure functions must not compute anything" (ADR 0025 Decision 1) is the
load-bearing rule of this module, enforced mechanically.**
``tests/unit/viz/test_figures.py::test_figures__no_module_performs_arithmetic``
scans this file's AST for arithmetic operators and aggregation calls
(``tests/no_arithmetic_scan.py``); every value a figure draws is read
straight off an :class:`~prismabib.bibliometrics.base.AnalysisResult`'s
``data``/``params``, or off a handful of fixed presentation constants in
:mod:`prismabib.viz.theme`. Where a chart form needs a numeric summary a
figure is not allowed to derive (a histogram's bin edges, a choropleth's
colour-scale normalisation, a graph layout's node coordinates), that
summary is computed **inside the rendering library** (matplotlib's
``ax.hist``, Plotly's ``colorscale`` normalisation, ``networkx``'s
``spring_layout``) -- the same "the library aggregates, this module never
does" precedent this project already uses for a histogram's own binning.

**Every figure function returns ``(plotly_figure, matplotlib_figure)``.**
Eight of the nine take exactly one
:class:`~prismabib.bibliometrics.base.AnalysisResult` -- BUILD_PLAN's own
general phrasing. Two (:func:`taxonomy_distribution_figure`,
:func:`taxonomy_evolution_figure`) take a ``Sequence`` of them, one per
taxonomy dimension, because BUILD_PLAN figure 7/8 each draw "one panel per
dimension" and :mod:`prismabib.taxonomy.analysis`'s two results are
deliberately scoped to one dimension each (see that module's docstring for
why). A sequence of :class:`~prismabib.bibliometrics.base.AnalysisResult`
is still "taking an AnalysisResult" in the sense the rule protects --
nothing in it was computed by this module -- so this is recorded as a
disclosed, narrow exception rather than a quiet deviation.

**Matplotlib is used through its object-oriented API only.** Every figure
here is built via ``matplotlib.figure.Figure()`` directly and
``fig.add_subplot``/``fig.subplots`` -- never ``pyplot.figure()`` or
``plt.gca()`` -- because ``pyplot``'s global figure registry leaks state
across tests under ``pytest -n auto`` (a known trap this project has hit
before). No figure returned here is ever implicitly registered with
``pyplot``; a caller that wants to close one calls
``matplotlib.figure.Figure.clf()``/lets it be garbage-collected.

**Determinism.** :func:`network_figure` is the one figure whose layout is
randomised (a force-directed graph). It reuses the *same* seed the network
:class:`~prismabib.bibliometrics.base.AnalysisResult` already carries in
``params["seed"]`` (the Louvain clustering seed) rather than inventing a
second knob -- so the same result renders the same coordinates on every
run, on every machine, which is what makes the SVG goldens meaningful.

**Captions.** :func:`_caption` is every figure's caption path: it starts
from :meth:`~prismabib.bibliometrics.base.AnalysisResult.caption` (which
states ``n``, the corpus's as-at date, and the citation snapshot where
applicable -- ADR 0022) and appends the criteria version(s) from
``provenance.criteria_versions``, which
:meth:`~prismabib.bibliometrics.base.AnalysisResult.caption` itself does
not render (S09-AC2 requires it; changing the Stage 7 contract to add it
there would move every existing bibliometrics/taxonomy golden caption, so
this stage adds the clause here instead -- pure string formatting over
already-computed provenance fields, not a new computation).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("Agg")

import networkx as nx
import plotly.graph_objects as go
from matplotlib.figure import Figure as MplFigure

from prismabib.bibliometrics.base import AnalysisResult
from prismabib.report.flow_diagram import flow_diagram_svg
from prismabib.viz import theme

if TYPE_CHECKING:
    from prismabib.prisma.flow import FlowCounts
    from prismabib.project import Project

#: The pair every figure function returns: the interactive Plotly figure
#: (dashboard, notebook), and the camera-ready Matplotlib figure (static
#: export, SVG goldens).
FigurePair = tuple[go.Figure, MplFigure]


def _caption(result: AnalysisResult) -> str:
    """``result.caption()`` plus the criteria-version clause S09-AC2 requires.

    Args:
        result: Any analysis result a figure function draws from.

    Returns:
        See this module's docstring's "Captions" section.
    """
    base = result.caption()
    versions = result.provenance.criteria_versions
    if not versions:
        return base
    return f"{base} criteria version(s): {', '.join(versions)}."


def _mpl_figure(*, width_in: float = theme.SINGLE_COLUMN_WIDTH_IN, ncols: int = 1) -> MplFigure:
    """A themed :class:`~matplotlib.figure.Figure`, never via ``pyplot``.

    Args:
        width_in: Figure width in inches.
        ncols: How many side-by-side subplot columns to create.

    Returns:
        The figure, with ``ncols`` axes already attached
        (``fig.axes``) and themed for light mode (the default export mode;
        :func:`_theme_mpl_figure` re-themes for dark mode on request).
    """
    fig = MplFigure(figsize=(width_in, theme.FIGURE_HEIGHT_IN), dpi=theme.EXPORT_DPI)
    fig.subplots(1, ncols)
    _theme_mpl_figure(fig, mode="light")
    return fig


def _theme_mpl_figure(fig: MplFigure, *, mode: str) -> None:
    """Apply :mod:`prismabib.viz.theme` to every axes ``fig`` already has.

    Args:
        fig: The figure to theme, in place.
        mode: ``"light"`` or ``"dark"``.
    """
    palette = theme.palette_for_mode(mode, backend="matplotlib")
    fig.set_facecolor(palette.surface)
    for axes in fig.axes:
        axes.set_facecolor(palette.surface)
        axes.tick_params(colors=palette.text_secondary, labelsize=theme.LABEL_FONT_PT)
        axes.xaxis.label.set_color(palette.text_primary)
        axes.yaxis.label.set_color(palette.text_primary)
        axes.title.set_color(palette.text_primary)
        for spine_name in ("top", "right"):
            axes.spines[spine_name].set_visible(False)
        for spine_name in ("left", "bottom"):
            axes.spines[spine_name].set_color(palette.baseline)
        axes.grid(visible=True, color=palette.gridline, linewidth=0.5)
        for tick_labels in (axes.get_xticklabels(), axes.get_yticklabels()):
            for label in tick_labels:
                label.set_fontfamily(theme.MATPLOTLIB_FONT_FAMILY)
                label.set_fontsize(theme.LABEL_FONT_PT)


def _plotly_figure(*, mode: str = "light") -> go.Figure:
    """A themed, empty Plotly figure -- traces are added by the caller.

    Args:
        mode: ``"light"`` or ``"dark"``.

    Returns:
        The figure, with its layout already themed.
    """
    palette = theme.palette_for_mode(mode, backend="plotly")
    figure = go.Figure()
    figure.update_layout(
        paper_bgcolor=palette.page,
        plot_bgcolor=palette.surface,
        font={
            "family": palette.font_family,
            "color": palette.text_primary,
            "size": theme.LABEL_FONT_PT,
        },
        xaxis={"gridcolor": palette.gridline, "linecolor": palette.baseline},
        yaxis={"gridcolor": palette.gridline, "linecolor": palette.baseline},
    )
    return figure


# ---------------------------------------------------------------------------
# Figure 1 -- annual publication trend, with partial-year shading
# ---------------------------------------------------------------------------


def trend_figure(result: AnalysisResult) -> FigurePair:
    """Annual publication trend, with partial-year shading (BUILD_PLAN figure 1).

    Args:
        result: :func:`prismabib.bibliometrics.trends.annual_counts`'s
            result. ``is_partial`` (ADR 0022 Decision 3b) decides shading
            here; it is never recomputed.

    Returns:
        See :data:`FigurePair`.
    """
    palette = theme.palette_for_mode("light")
    years = result.data["year"].to_list()
    counts = result.data["count"].to_list()
    partial = result.data["is_partial"].to_list()
    colours = [
        theme.STATUS_WARNING if is_partial else palette.categorical[0] for is_partial in partial
    ]
    caption = _caption(result)

    plotly_figure = _plotly_figure()
    plotly_figure.add_trace(
        go.Bar(x=years, y=counts, marker_color=colours, name="Records", hovertext=partial)
    )
    plotly_figure.update_layout(
        title="Annual publication trend",
        xaxis_title="Year",
        yaxis_title="Records",
        showlegend=False,
    )

    fig = _mpl_figure(width_in=theme.DOUBLE_COLUMN_WIDTH_IN)
    axes = fig.axes[0]
    axes.bar([str(year) for year in years], counts, color=colours)
    # `strict=False`: matplotlib pads its own tick-label list with placeholder
    # ticks on an empty axes, so its length need not agree with `years`'s
    # (zero, on an empty corpus) -- `zip` stopping at the shorter sequence is
    # exactly "no partial year to bold" in that case.
    for label, _year, is_partial in zip(axes.get_xticklabels(), years, partial, strict=False):
        if is_partial:
            label.set_fontweight("bold")
    axes.set_xlabel("Year")
    axes.set_ylabel("Records")
    axes.set_title("Annual publication trend")
    if any(partial):
        axes.annotate(
            "partial year: capture ended before this year closed",
            xy=(1, 1),
            xycoords="axes fraction",
            xytext=(-4, -4),
            textcoords="offset points",
            ha="right",
            va="top",
            fontsize=theme.LABEL_FONT_PT,
            color=theme.STATUS_WARNING,
        )
    fig.suptitle(caption, fontsize=theme.LABEL_FONT_PT, color=palette.text_secondary, wrap=True)

    return plotly_figure, fig


# ---------------------------------------------------------------------------
# Figure 2 -- geographic distribution (share bar + choropleth)
# ---------------------------------------------------------------------------


def geography_figure(result: AnalysisResult) -> FigurePair:
    """Geographic distribution: a share bar chart, plus a Plotly choropleth (figure 2).

    Args:
        result: :func:`prismabib.bibliometrics.geography.country_counts`'s
            result. ``country`` is already ISO3 (or ``"UNK"``).

    Returns:
        See :data:`FigurePair`. Countries are a *nominal* category here
        (no natural order), so the bar panel uses one flat colour -- a
        value ramp on nominal bars is the ``dataviz`` skill's own
        anti-pattern. The choropleth is a genuine magnitude encoding and
        takes the sequential blue ramp (ADR 0025 Decision 3); Plotly
        computes its own colour normalisation from ``z``'s min/max
        internally (:data:`prismabib.viz.theme.SEQUENTIAL_BLUE_COLORSCALE`),
        so this function never derives a value range itself. Matplotlib has
        no bundled choropleth geometry (no ``geopandas``/``cartopy``
        dependency in this project) -- its companion panel here is the same
        share bars, camera-ready; the interactive choropleth is Plotly-only.
    """
    palette = theme.palette_for_mode("light")
    countries = result.data["country"].to_list()
    shares = result.data["share"].to_list()
    counts = result.data["count"].to_list()
    caption = _caption(result)

    plotly_figure = _plotly_figure()
    plotly_figure.add_trace(
        go.Choropleth(
            locations=countries,
            z=shares,
            locationmode="ISO-3",
            colorscale=theme.SEQUENTIAL_BLUE_COLORSCALE,
            colorbar_title="Share",
        )
    )
    plotly_figure.update_layout(title="Geographic distribution (share by country)")

    fig = _mpl_figure(width_in=theme.DOUBLE_COLUMN_WIDTH_IN)
    axes = fig.axes[0]
    axes.barh(countries, counts, color=palette.categorical[0])
    axes.invert_yaxis()
    axes.set_xlabel("Records")
    axes.set_ylabel("Country (ISO3)")
    axes.set_title("Geographic distribution (share chart; choropleth is Plotly-only)")
    fig.suptitle(caption, fontsize=theme.LABEL_FONT_PT, color=palette.text_secondary, wrap=True)

    return plotly_figure, fig


# ---------------------------------------------------------------------------
# Figure 3 -- top-N venues
# ---------------------------------------------------------------------------


def venues_figure(result: AnalysisResult) -> FigurePair:
    """Top-N venues by record count (BUILD_PLAN figure 3).

    Args:
        result: :func:`prismabib.bibliometrics.venues.top_venues`'s result,
            already sorted and truncated to ``params["top_n"]``.

    Returns:
        See :data:`FigurePair`. One series, one colour (categorical slot 1)
        -- venues are nominal, not a value ramp.
    """
    palette = theme.palette_for_mode("light")
    venue_names = result.data["venue"].to_list()
    counts = result.data["count"].to_list()
    caption = _caption(result)

    plotly_figure = _plotly_figure()
    plotly_figure.add_trace(
        go.Bar(x=counts, y=venue_names, orientation="h", marker_color=palette.categorical[0])
    )
    plotly_figure.update_layout(title="Top venues", xaxis_title="Records", showlegend=False)

    fig = _mpl_figure(width_in=theme.DOUBLE_COLUMN_WIDTH_IN)
    axes = fig.axes[0]
    axes.barh(venue_names, counts, color=palette.categorical[0])
    axes.invert_yaxis()
    axes.set_xlabel("Records")
    axes.set_title("Top venues")
    fig.suptitle(caption, fontsize=theme.LABEL_FONT_PT, color=palette.text_secondary, wrap=True)

    return plotly_figure, fig


# ---------------------------------------------------------------------------
# Figure 4 -- keyword frequency and keyword-evolution stream
# ---------------------------------------------------------------------------


def keywords_figure(result: AnalysisResult) -> FigurePair:
    """Keyword frequency (stacked by year) and its evolution stream (BUILD_PLAN figure 4).

    Args:
        result: :func:`prismabib.bibliometrics.keywords.keyword_evolution`'s
            result (``year``, ``term``, ``count``). One data source draws
            both panels. The Plotly panel is a stacked bar whose ``barmode
            ="stack"`` layout setting makes *Plotly itself* compute each
            bar's cumulative offset -- this function never sums across
            terms. Matplotlib's ``bar`` has no such auto-stack (a caller
            must pass a ``bottom=`` array, and deriving one is exactly the
            cumulative-sum arithmetic this module may not perform), so its
            companion panel is a multi-line stream instead -- the same
            data, unstacked, which is a legible camera-ready substitute
            without a running total. Colour is assigned by first appearance
            in the already-sorted frame (a disclosed simplification of "top
            overall frequency", which this function cannot rank without
            aggregating) -- see :func:`prismabib.viz.theme.entity_colours`.

    Returns:
        See :data:`FigurePair`.
    """
    palette = theme.palette_for_mode("light")
    years = result.data["year"].to_list()
    terms = result.data["term"].to_list()
    counts = result.data["count"].to_list()
    colours_by_term = theme.entity_colours(terms, palette)
    caption = _caption(result)

    plotly_figure = _plotly_figure()
    for term in colours_by_term:
        mask = [candidate == term for candidate in terms]
        term_years = [year for year, keep in zip(years, mask, strict=True) if keep]
        term_counts = [count for count, keep in zip(counts, mask, strict=True) if keep]
        plotly_figure.add_trace(
            go.Bar(
                x=term_years,
                y=term_counts,
                name=term,
                marker_color=colours_by_term[term],
            )
        )
    plotly_figure.update_layout(
        title="Keyword frequency and evolution", barmode="stack", xaxis_title="Year"
    )

    fig = _mpl_figure(width_in=theme.DOUBLE_COLUMN_WIDTH_IN)
    axes = fig.axes[0]
    for term in colours_by_term:
        mask = [candidate == term for candidate in terms]
        term_years = [year for year, keep in zip(years, mask, strict=True) if keep]
        term_counts = [count for count, keep in zip(counts, mask, strict=True) if keep]
        axes.plot(
            term_years,
            term_counts,
            marker="o",
            color=colours_by_term[term],
            label=term,
        )
    axes.set_xlabel("Year")
    axes.set_ylabel("Mentions")
    axes.set_title("Keyword evolution stream")
    axes.legend(fontsize=theme.LABEL_FONT_PT, frameon=False)
    fig.suptitle(caption, fontsize=theme.LABEL_FONT_PT, color=palette.text_secondary, wrap=True)

    return plotly_figure, fig


# ---------------------------------------------------------------------------
# Figure 5 -- keyword co-occurrence network
# ---------------------------------------------------------------------------


def network_figure(result: AnalysisResult) -> FigurePair:
    """Keyword/co-authorship co-occurrence network (BUILD_PLAN figure 5; ADR 0025 Decision 2/4).

    Args:
        result: :func:`prismabib.bibliometrics.network.keyword_cooccurrence_network`
            or :func:`~prismabib.bibliometrics.network.coauthorship_network`'s
            result -- long-format with a ``row_kind`` column. Node size is
            ``frequency``, edge width is ``weight``, colour is
            ``cluster``/``cluster_size`` (top-3 plus "Other" -- ADR 0025
            Decision 4). Layout is seeded from ``params["seed"]`` -- the
            same seed the clustering itself used, not a second knob -- so
            the drawn coordinates are reproducible.

    Returns:
        See :data:`FigurePair`. The caption states how many communities are
        shown vs. folded to "Other"
        (:func:`prismabib.viz.theme.cluster_fold_caption`), counted over the
        *drawn* subgraph's own ``cluster``/``cluster_size`` columns -- ADR
        0025's own measurement is phrased the same way ("Drawn ... graph:
        ... 16 clusters"), so "N of M" means M drawn communities, not the
        untruncated graph's community count in ``params["communities"]``
        (which can be larger still, for nodes no edge survived truncation
        for at all).
    """
    palette = theme.palette_for_mode("light")
    nodes = result.data.filter(result.data["row_kind"] == "node")
    edges = result.data.filter(result.data["row_kind"] == "edge")
    node_ids = nodes["id"].to_list()
    labels = dict(zip(node_ids, nodes["label"].to_list(), strict=True))
    frequency = dict(zip(node_ids, nodes["frequency"].to_list(), strict=True))
    cluster_of = dict(zip(node_ids, nodes["cluster"].to_list(), strict=True))
    cluster_size_of = dict(
        zip(nodes["cluster"].to_list(), nodes["cluster_size"].to_list(), strict=True)
    )

    fold_caption = (
        theme.cluster_fold_caption(cluster_size_of) if cluster_size_of else "no drawn nodes."
    )
    colours_by_cluster = theme.cluster_colours(cluster_size_of, palette)

    graph = nx.Graph()
    graph.add_nodes_from(node_ids)
    for row in edges.iter_rows(named=True):
        graph.add_edge(row["node_a"], row["node_b"], weight=row["weight"])
    seed = result.params.get("seed", 0)
    positions = nx.spring_layout(graph, seed=seed) if node_ids else {}

    caption = f"{_caption(result)} {fold_caption}"

    plotly_figure = _plotly_figure()
    for row in edges.iter_rows(named=True):
        x0, y0 = positions[row["node_a"]]
        x1, y1 = positions[row["node_b"]]
        plotly_figure.add_trace(
            go.Scatter(
                x=[x0, x1],
                y=[y0, y1],
                mode="lines",
                line={"width": row["weight"], "color": palette.gridline},
                hoverinfo="skip",
                showlegend=False,
            )
        )
    plotly_figure.add_trace(
        go.Scatter(
            x=[positions[node_id][0] for node_id in node_ids],
            y=[positions[node_id][1] for node_id in node_ids],
            mode="markers+text",
            text=[labels[node_id] for node_id in node_ids],
            marker={
                "size": [frequency[node_id] for node_id in node_ids],
                "color": [colours_by_cluster[cluster_of[node_id]] for node_id in node_ids],
            },
            showlegend=False,
        )
    )
    # The fold sentence goes on **both** backends. It reached only
    # matplotlib in the first version, and the dashboard and the notebook
    # both render the Plotly figure -- so the surface a reviewer actually
    # looks at painted three colours plus grey over sixteen communities and
    # said nothing about it. ADR 0025 Decision 4 calls this sentence "the
    # point": a reader seeing three colours on a 16-community graph must be
    # told, or they will read three communities.
    plotly_figure.update_layout(
        title=f"Co-occurrence network ({fold_caption})",
        xaxis_visible=False,
        yaxis_visible=False,
        annotations=[
            {
                "text": caption,
                "showarrow": False,
                "xref": "paper",
                "yref": "paper",
                "x": 0.0,
                "y": -0.08,
                "xanchor": "left",
                "font": {"size": theme.LABEL_FONT_PT, "color": palette.text_secondary},
            }
        ],
    )

    fig = _mpl_figure(width_in=theme.DOUBLE_COLUMN_WIDTH_IN)
    axes = fig.axes[0]
    for row in edges.iter_rows(named=True):
        x0, y0 = positions[row["node_a"]]
        x1, y1 = positions[row["node_b"]]
        axes.plot([x0, x1], [y0, y1], color=palette.gridline, linewidth=row["weight"], zorder=1)
    axes.scatter(
        [positions[node_id][0] for node_id in node_ids],
        [positions[node_id][1] for node_id in node_ids],
        s=[frequency[node_id] for node_id in node_ids],
        c=[colours_by_cluster[cluster_of[node_id]] for node_id in node_ids],
        zorder=2,
    )
    for node_id in node_ids:
        x, y = positions[node_id]
        axes.annotate(labels[node_id], xy=(x, y), fontsize=theme.LABEL_FONT_PT)
    axes.set_xticks([])
    axes.set_yticks([])
    axes.set_title(f"Co-occurrence network ({fold_caption})")
    fig.suptitle(caption, fontsize=theme.LABEL_FONT_PT, color=palette.text_secondary, wrap=True)

    return plotly_figure, fig


# ---------------------------------------------------------------------------
# Figure 6 -- citation distribution (log-scale histogram + top-N bar)
# ---------------------------------------------------------------------------


def citation_distribution_figure(result: AnalysisResult) -> FigurePair:
    """Citation distribution: log-scale histogram, plus the top-N bar (BUILD_PLAN figure 6).

    Args:
        result: :func:`prismabib.bibliometrics.citations.citation_distribution`'s
            result -- every record with a citation snapshot, sorted
            ``cited_by_count`` descending. ``params["top_n"]`` decides the
            bar panel's row count via :meth:`polars.DataFrame.head`, a
            selection over data this function already sorted, never a
            recomputation.

    Returns:
        See :data:`FigurePair`. The histogram bins are computed by the
        plotting library (matplotlib's ``ax.hist``, Plotly's ``go.Histogram``),
        not by this function -- the same precedent as the choropleth's
        colour-scale normalisation.
    """
    palette = theme.palette_for_mode("light")
    counts = result.data["cited_by_count"].to_list()
    top_n = result.params.get("top_n", 20)
    top = result.data.head(top_n)
    top_titles = top["title"].to_list()
    top_counts = top["cited_by_count"].to_list()
    caption = _caption(result)

    plotly_figure = _plotly_figure()
    plotly_figure.add_trace(
        go.Histogram(x=counts, marker_color=palette.categorical[0], name="Records")
    )
    plotly_figure.update_layout(
        title="Citation distribution", xaxis_title="Citations", yaxis_type="log"
    )

    fig = _mpl_figure(width_in=theme.DOUBLE_COLUMN_WIDTH_IN, ncols=2)
    hist_axes, bar_axes = fig.axes
    hist_axes.hist(counts, bins=30, color=palette.categorical[0])
    hist_axes.set_yscale("log")
    hist_axes.set_xlabel("Citations")
    hist_axes.set_ylabel("Records (log scale)")
    hist_axes.set_title("Citation distribution")

    bar_axes.barh(top_titles, top_counts, color=palette.categorical[0])
    bar_axes.invert_yaxis()
    bar_axes.set_xlabel("Citations")
    bar_axes.set_title("Most-cited records")
    fig.suptitle(caption, fontsize=theme.LABEL_FONT_PT, color=palette.text_secondary, wrap=True)

    return plotly_figure, fig


# ---------------------------------------------------------------------------
# Figure 7 -- taxonomy distribution, one panel per dimension
# ---------------------------------------------------------------------------


def taxonomy_distribution_figure(results: Sequence[AnalysisResult]) -> FigurePair:
    """Taxonomy distribution, one panel per dimension, unit stated per panel (BUILD_PLAN figure 7).

    Args:
        results: One :func:`prismabib.taxonomy.analysis.dimension_distribution`
            result per declared dimension -- see this module's docstring for
            why this figure takes a sequence rather than a single result.

    Returns:
        See :data:`FigurePair`. Each panel's own caption states its
        dimension's counting unit; the returned overall caption is every
        panel's caption, one per line.
    """
    palette = theme.palette_for_mode("light")
    captions = [_caption(result) for result in results]

    plotly_figure = _plotly_figure()
    for index, result in enumerate(results, start=1):
        categories = result.data["category"].to_list()
        counts = result.data["count"].to_list()
        dimension = result.params.get("dimension", f"dimension {index}")
        plotly_figure.add_trace(
            go.Bar(x=categories, y=counts, name=str(dimension), marker_color=palette.categorical[0])
        )
    plotly_figure.update_layout(title="Taxonomy distribution")

    fig = _mpl_figure(width_in=theme.DOUBLE_COLUMN_WIDTH_IN, ncols=len(results) or 1)
    for axes, result in zip(fig.axes, results, strict=False):
        categories = result.data["category"].to_list()
        counts = result.data["count"].to_list()
        dimension = result.params.get("dimension", "")
        axes.barh(categories, counts, color=palette.categorical[0])
        axes.invert_yaxis()
        axes.set_title(str(dimension))
    fig.suptitle(
        "\n".join(captions), fontsize=theme.LABEL_FONT_PT, color=palette.text_secondary, wrap=True
    )

    return plotly_figure, fig


# ---------------------------------------------------------------------------
# Figure 8 -- taxonomy evolution over time
# ---------------------------------------------------------------------------


def taxonomy_evolution_figure(results: Sequence[AnalysisResult]) -> FigurePair:
    """Taxonomy evolution over time, one panel per dimension (BUILD_PLAN figure 8).

    Args:
        results: One :func:`prismabib.taxonomy.analysis.dimension_evolution`
            result per declared dimension.

    Returns:
        See :data:`FigurePair`.
    """
    palette = theme.palette_for_mode("light")
    captions = [_caption(result) for result in results]

    plotly_figure = _plotly_figure()
    for result in results:
        dimension = str(result.params.get("dimension", ""))
        categories = result.data["category"].to_list()
        colours_by_category = theme.entity_colours(categories, palette)
        years = result.data["year"].to_list()
        counts = result.data["count"].to_list()
        for category in colours_by_category:
            mask = [candidate == category for candidate in categories]
            cat_years = [year for year, keep in zip(years, mask, strict=True) if keep]
            cat_counts = [count for count, keep in zip(counts, mask, strict=True) if keep]
            plotly_figure.add_trace(
                go.Scatter(
                    x=cat_years,
                    y=cat_counts,
                    mode="lines+markers",
                    name=f"{dimension}: {category}",
                    line={"color": colours_by_category[category]},
                )
            )
    plotly_figure.update_layout(title="Taxonomy evolution over time", xaxis_title="Year")

    fig = _mpl_figure(width_in=theme.DOUBLE_COLUMN_WIDTH_IN, ncols=len(results) or 1)
    for axes, result in zip(fig.axes, results, strict=False):
        dimension = str(result.params.get("dimension", ""))
        categories = result.data["category"].to_list()
        colours_by_category = theme.entity_colours(categories, palette)
        years = result.data["year"].to_list()
        counts = result.data["count"].to_list()
        for category in colours_by_category:
            mask = [candidate == category for candidate in categories]
            cat_years = [year for year, keep in zip(years, mask, strict=True) if keep]
            cat_counts = [count for count, keep in zip(counts, mask, strict=True) if keep]
            axes.plot(
                cat_years,
                cat_counts,
                marker="o",
                color=colours_by_category[category],
                label=category,
            )
        axes.set_title(dimension)
        axes.set_xlabel("Year")
        axes.legend(fontsize=theme.LABEL_FONT_PT, frameon=False)
    fig.suptitle(
        "\n".join(captions), fontsize=theme.LABEL_FONT_PT, color=palette.text_secondary, wrap=True
    )

    return plotly_figure, fig


# ---------------------------------------------------------------------------
# Figure 9 -- PRISMA 2020 flow diagram (wraps report/flow_diagram.py)
# ---------------------------------------------------------------------------


def prisma_flow_figure(counts: FlowCounts, project: Project) -> tuple[str, MplFigure]:
    """The PRISMA 2020 flow diagram, wrapped for the dashboard (BUILD_PLAN figure 9; ADR 0025 Decision 5).

    ``report/flow_diagram.py`` already exists, is pinned to
    :class:`~prismabib.prisma.flow.FlowCounts` by
    ``test_flow_diagram__generated_numbers__equal_flowcounts``, and computes
    nothing itself. This function adds nothing but the caption and a
    dashboard-friendly return shape -- ADR 0025 Decision 5 forbids a second
    PRISMA diagram implementation outright, so there is no Plotly
    counterpart to draw; the SVG text *is* the figure, in both surfaces.

    Args:
        counts: The project's :class:`~prismabib.prisma.flow.FlowCounts`.
        project: The project, for its title and declared criteria version.

    Returns:
        ``(svg_text, a Matplotlib figure holding the same SVG as an image)``
        -- the second element exists only so this function's return shape
        can still participate in "renders both backends"-style sweeps
        alongside the other eight; it carries no separate rendering.
    """
    svg = flow_diagram_svg(counts, title=project.title)
    fig = MplFigure(
        figsize=(theme.SINGLE_COLUMN_WIDTH_IN, theme.FIGURE_HEIGHT_IN), dpi=theme.EXPORT_DPI
    )
    axes = fig.add_subplot(111)
    axes.axis("off")
    axes.text(
        0,
        1,
        f"PRISMA 2020 flow -- see accompanying SVG (n included = {counts.included}; "
        f"criteria version {project.criteria.version})",
        fontsize=theme.LABEL_FONT_PT,
        va="top",
    )
    return svg, fig


def prisma_flow_caption(counts: FlowCounts, project: Project) -> str:
    """The caption for :func:`prisma_flow_figure`.

    Args:
        counts: The project's :class:`~prismabib.prisma.flow.FlowCounts`.
        project: The project, for its declared criteria version.

    Returns:
        A one-sentence caption stating ``n`` (``counts.included``) and the
        criteria version -- the two facts S09-AC2 requires of every figure
        caption, read directly off already-computed fields.
    """
    return f"n = {counts.included} (included); criteria version {project.criteria.version}."


__all__ = [
    "FigurePair",
    "citation_distribution_figure",
    "geography_figure",
    "keywords_figure",
    "network_figure",
    "prisma_flow_caption",
    "prisma_flow_figure",
    "taxonomy_distribution_figure",
    "taxonomy_evolution_figure",
    "trend_figure",
    "venues_figure",
]
