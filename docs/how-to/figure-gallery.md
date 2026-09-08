# Figure Gallery

Every figure in `prismabib.viz.figures` follows the same shape: an analysis
function in `prismabib.bibliometrics` (or `prismabib.taxonomy.analysis`)
computes a result, and a figure function draws it. The figure function
itself computes nothing — see [ADR 0025](../architecture/adr/0025-figures-render-what-the-engines-computed.md)
for why that split is enforced mechanically, not just by convention.

Every example below opens a `Corpus` and produces a `(plotly_figure,
matplotlib_figure)` pair. Display the Plotly figure in a notebook cell for
interactivity; use the Matplotlib figure's `.savefig(path, format="svg")`
for a camera-ready, publication-quality export.

```python
from prismabib.project import Project
from prismabib.store.load import Corpus

project = Project.open("your-project-slug")
corpus = Corpus.open(project, read_only=True)
```

## 1. Annual publication trend

```python
from prismabib.bibliometrics import trends
from prismabib.viz import figures

result = trends.annual_counts(corpus)
plotly_fig, mpl_fig = figures.trend_figure(result)
plotly_fig  # in a notebook cell
```

Partial-year shading reads `result.data`'s `is_partial` column directly
(ADR 0022 Decision 3b) — the figure never recomputes which year is
incomplete.

## 2. Geographic distribution

```python
from prismabib.bibliometrics import geography

result = geography.country_counts(corpus)
plotly_fig, mpl_fig = figures.geography_figure(result)
```

The Plotly figure includes an interactive choropleth (ISO3 country codes,
sequential blue ramp). The Matplotlib companion is a share bar chart only —
this project has no `geopandas`/`cartopy` dependency to draw a static map.

## 3. Top venues

```python
from prismabib.bibliometrics import venues

result = venues.top_venues(corpus, top_n=20)
plotly_fig, mpl_fig = figures.venues_figure(result)
```

## 4. Keyword frequency and evolution

```python
from prismabib.bibliometrics import keywords

result = keywords.keyword_evolution(corpus, min_occurrence=2)
plotly_fig, mpl_fig = figures.keywords_figure(result)
```

## 5. Keyword co-occurrence network

```python
from prismabib.bibliometrics import network

result = network.keyword_cooccurrence_network(corpus, min_occurrence=2, seed=0)
plotly_fig, mpl_fig = figures.network_figure(result)
```

Node size is `frequency`, edge width is co-occurrence `weight`, colour is
cluster (top 3 communities by size, everything else folded to a neutral
"Other" — [ADR 0025 Decision 4](../architecture/adr/0025-figures-render-what-the-engines-computed.md#decision)).
The caption states how many communities are shown versus folded, e.g. *"3
of 16 communities shown; 13 folded to Other."* Layout is force-directed
(`networkx.spring_layout`), seeded from `result.params["seed"]` — the same
seed the clustering used — so the same result always renders the same
coordinates.

## 6. Citation distribution

```python
from prismabib.bibliometrics import citations

result = citations.citation_distribution(corpus, top_n=20)
plotly_fig, mpl_fig = figures.citation_distribution_figure(result)
```

## 7 & 8. Taxonomy distribution and evolution over time

These two figures each take a *sequence* of results, one per declared
taxonomy dimension — the one deliberate exception to "one figure, one
`AnalysisResult`" (see `viz/figures.py`'s own module docstring for why).

```python
from prismabib.taxonomy.analysis import dimension_distribution, dimension_evolution
from prismabib.taxonomy.coder import CountingUnit, code
from prismabib.taxonomy.overrides import OverrideLog
from prismabib.taxonomy.rules import load_rule_file
from prismabib.taxonomy.schema import load_dimensions

schema = load_dimensions(project)
rule_files = [
    load_rule_file(path, schema=schema)
    for path in sorted(project.taxonomy_rules_dir.glob("*.yaml"))
]
overrides = OverrideLog(project).load()
coding_result = code(corpus, rule_files, overrides)

distribution_results = [
    dimension_distribution(corpus, coding_result, schema, dimension.id, CountingUnit.PAPERS)
    for dimension in schema.dimensions
]
plotly_fig, mpl_fig = figures.taxonomy_distribution_figure(distribution_results)

evolution_results = [
    dimension_evolution(corpus, coding_result, schema, dimension.id, CountingUnit.PAPERS)
    for dimension in schema.dimensions
]
plotly_fig, mpl_fig = figures.taxonomy_evolution_figure(evolution_results)
```

## 9. PRISMA 2020 flow diagram

Wraps `report/flow_diagram.py` — see [ADR 0025 Decision 5](../architecture/adr/0025-figures-render-what-the-engines-computed.md#decision)
for why this stage does not draw a second one.

```python
from prismabib.prisma.flow import compute_flow_counts

counts = compute_flow_counts(project)
svg_text, mpl_fig = figures.prisma_flow_figure(counts, project)
caption = figures.prisma_flow_caption(counts, project)
```

## The dashboard

All nine figures are also available together in `prismabib.viz.dashboard`:

```python
from prismabib.viz.dashboard import dashboard

dashboard(project)  # in a notebook cell, or `panel serve` the module
```

The dashboard's sidebar (project selector, year range, taxonomy dimension,
citation snapshot) and its nine tabs (Overview, PRISMA, Trends, Geography,
Venues, Keywords, Network, Taxonomy, Corpus browser) all read the same
`Corpus` handle, so every tab is consistent by construction — never a
second, independently-filtered copy of the corpus.

## Captions

Every figure's caption states `n`, the corpus's as-at date, the citation
snapshot where applicable, and the criteria version — read straight off the
`AnalysisResult`'s own provenance, never typed by hand:

```python
from prismabib.viz.figures import _caption

print(_caption(result))
# "n = 120 (included); corpus as at 2026-01-15; criteria version(s): 1.0.0."
```

## Theme and colour

`prismabib.viz.theme` is the one place colour is decided, for both
backends — the reference palette from the `dataviz` skill, adopted whole
(not chosen by taste; see ADR 0025 Decision 3). Never hardcode a hex value
in a figure function.
