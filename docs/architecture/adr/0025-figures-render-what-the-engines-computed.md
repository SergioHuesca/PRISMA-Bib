# ADR 0025: Figures Render What the Engines Computed, and the Palette Is Validated Rather Than Chosen

## Status

Accepted — 2026-09-07. Implements BUILD_PLAN Stage 9. Amends the Stage 7 `network.py` and Stage 8
`taxonomy` contracts — see Decision 2, which is the reason this ADR exists before any code.

Adopts the `dataviz` skill's reference palette wholesale (Decision 3); this ADR records the
constraints that come with it and where the BUILD_PLAN's figure list collides with them.

## Context

BUILD_PLAN §Stage 9 fixes the layering rule in one sentence: **"Figure functions must not compute
anything. If a figure function contains arithmetic, that arithmetic belongs in Stage 7."** It is
enforced by an AST scan, so it is not advisory.

Applied honestly, that rule says something uncomfortable: **three of the nine required figures
cannot be drawn from what Stages 7 and 8 currently return.** Discovering that in the figure layer
would produce exactly the defect the rule exists to prevent — a figure quietly computing, or a
figure showing less than the spec asks because the data was not there.

Measured on the live `Baseball-CVPR` store, which also decides Decision 4:

| what | measured |
| --- | --- |
| keyword co-occurrence, drawn subgraph | 41 nodes, 50 edges, **6 clusters** (12, 10, 8, 8, 2, 1) |
| co-authorship, drawn subgraph | 48 nodes, 50 edges, **16 clusters** (7, 4, then twelve of 2–3) |
| `network` result's `data` | edge list only — `node_a`, `node_b`, `weight` |
| node → community map | in `params`, not in `data` |
| taxonomy distribution | returns `Distribution`, not `AnalysisResult` |
| taxonomy evolution over time | does not exist |

## Decision

### 1. The layering rule is enforced mechanically, and it is the load-bearing rule of this stage

`test_figures__no_module_performs_arithmetic` scans `viz/figures.py`'s AST for arithmetic
operators and aggregation calls outside formatting. A figure that computes is a number with no
provenance, no `params`, and no caption obligation — the one thing Stages 7 and 8 were built to
make impossible.

The scan must be able to fail: a planted `sum(...)` and a planted `a / b` in a figure body are
both pinned, the way the wall-clock scan pins its own spellings. A guard whose failure mode is
untested is the shape this project has shipped three times.

### 2. Three engine contracts move, because the alternative is a figure that computes

**`network.py`'s `data` gains node rows.** BUILD_PLAN figure 5 requires *node size = frequency,
edge width = co-occurrence, colour = cluster*. Today `data` carries only edges, and the
community map lives in `params` — which is the wrong home twice over: `params` is the record of
*knobs that affected the number*, not a second data channel, and a figure reading a dict out of
`params` to size its nodes is computing with extra steps.

So the frame becomes long-format with a `row_kind` column (`node` | `edge`), carrying node id,
label, frequency and cluster for node rows. One `AnalysisResult` then holds everything figure 5
draws, and the single-argument figure contract survives intact.

**Taxonomy gains `AnalysisResult`-returning functions.** `distribution()` returns a
`Distribution`, which is right for Stage 8's own guard but is not what a figure takes. And
BUILD_PLAN figure 8 — *taxonomy evolution over time* — **does not exist in any stage.** Both are
Stage 8 work, done here because Stage 9 is where the gap surfaces; putting either in `viz/` would
break the rule Decision 1 enforces mechanically.

That these three landed as Stage 9 changes is worth stating plainly rather than hiding in a
diff: the stage boundary is a build order, not a wall, and the rule that actually matters is that
arithmetic lives where its provenance can be recorded.

### 3. The palette is the `dataviz` reference instance, and its constraints are inherited whole

Not chosen by taste, and not re-derived. The eight-slot categorical order, the blue sequential
ramp and the light/dark surface pairs are adopted as published, with their validator output
recorded here as provenance:

- worst adjacent-pair CVD ΔE **9.1 light / 8.4 dark** (OKLab ×100, ≥8 target)
- worst adjacent-pair normal-vision ΔE **19.6 light / 19.3 dark** (≥15 floor)
- **first three slots only** clear the *all-pairs* gate (CVD ΔE 9.2 light / 9.4 dark) — verified
  by running the validator, not by reading the table

Four rules follow, and none is negotiable in `theme.py`:

- **Categorical hues in fixed order, never cycled**, and assigned to the *entity*, never to its
  rank. A dashboard filter that drops a series must not repaint the survivors — a chart whose
  colours mean something different after a filter change is a chart that lied before or after.
- **All-pairs forms cap at three categorical slots.** A network, a scatter, small multiples: any
  two marks can be adjacent, so the adjacent-pair gate does not apply. Past three, fold to
  "Other" or facet.
- **Sequential is one hue, light→dark; diverging is two hues with a neutral midpoint.** The
  choropleth is a magnitude encoding and takes the blue ramp — never the categorical palette.
- **No dual-axis chart, ever.** Two measures of different scale become two charts, small
  multiples, or an indexed common base.

Dark mode is a **selected** set of steps validated against the dark surface, never a programmatic
inversion of the light one.

### 4. Cluster colour is top-3 plus "Other", and the caption says how many were folded

Decision 3's all-pairs cap collides with BUILD_PLAN figure 5's *"colour = cluster"*, and the live
corpus decides it: the drawn keyword graph has 6 clusters, the co-authorship graph 16 — twelve of
them of size two or three.

Sixteen near-equal clusters cannot be told apart by colour under any palette, and a figure that
paints them anyway shows structure it cannot support. So the three largest clusters take slots
1–3, everything else takes the neutral "Other" grey, and **the caption states the total cluster
count and how many are folded** — "3 of 16 communities shown; 13 folded to Other".

That sentence is the point. A reader seeing three colours on a 16-community graph must be told,
or they will read three communities.

### 5. Figure 9 is `report/flow_diagram.py`, not a second implementation

The PRISMA diagram already exists, generated from `FlowCounts` and pinned by
`test_flow_diagram__generated_numbers__equal_flowcounts` (Stage 10, shipped). Stage 9 wraps it for
the dashboard and adds nothing. Two PRISMA diagrams in one bundle is precisely the drift ADR 0022
Decision 5 fought over venues, in the figure this project exists to get right.

### 6. Colour-blindness is measured with CIEDE2000, as the frozen spec says

BUILD_PLAN's test is `test_theme__categorical_palette__is_colourblind_safe` — *"pairwise CIEDE2000
distance under a deuteranopia simulation exceeds a threshold"*. The `dataviz` validator uses
OKLab ΔE instead. The frozen spec wins: the test implements CIEDE2000.

The two metrics are recorded together rather than one being quietly swapped for the other. A
palette that passes one and fails the other is a finding, not a rounding difference, and the
threshold this project pins must be the one BUILD_PLAN named.

Separately, S09-AC5's *"contrast ratio of text against background ≥ 4.5:1"* measures **text**, not
marks. Three light-mode slots sit below 3:1 against the light surface as *marks*; the palette's
relief rule covers that with visible direct labels or a table view. Two different measurements,
both asserted, neither standing in for the other.

### 7. The dashboard's tabs share one `Corpus` handle and one immutable filter state

BUILD_PLAN: *"All filters operate on the same `Corpus` handle so every tab is consistent by
construction."* Construction, not convention — so it is asserted, not assumed: a test reads the
handle identity from every tab and compares.

The filter state is a frozen value passed down, never mutated in place by a tab. A tab that can
write to the filter state can make two tabs disagree, and "consistent by construction" is then a
comment rather than a property.

### 8. Visual regression stays out of scope

Pixel-diffing is brittle across matplotlib versions and font stacks and would gate CI on
cosmetics (BUILD_PLAN §8, deferred — a closed list). Golden tests operate on **SVG text content
and structure**, which is stable and semantically meaningful. The 300 dpi legibility criterion is
*computed* from figure size and font spec, never eyeballed.

## Alternatives considered

**Let figure 5 read `communities` out of `params`.** Rejected in Decision 2: `params` records the
knobs, and a figure indexing a dict to size a node is computing.

**Colour all clusters, cycling the palette.** Rejected in Decision 4 and forbidden outright by the
palette's own rule — a ninth series is never a generated hue.

**Re-step the palette so more slots clear the all-pairs gate.** Rejected: the published palette is
validated as a set, and re-deriving it here would put an unvalidated palette in the one project
whose premise is that numbers and pictures are checked rather than chosen.

**Draw a second PRISMA diagram suited to the dashboard.** Rejected in Decision 5.

## Consequences

1. **Stage 7's `network.py` and Stage 8's taxonomy surface both change in this PR.** Declared,
   not incidental. `network`'s `data` schema change is visible to anything reading it — today
   only the VOSviewer export and its golden.
2. **A reader of the co-authorship figure sees three colours and a caption saying 3 of 16.** That
   is the honest rendering of a graph with no dominant community structure, and it will look
   less impressive than a sixteen-colour version. That is the correct trade.
3. **`theme.py` becomes the single place colour is decided**, for both Plotly and Matplotlib. A
   figure that hardcodes a hex is a defect the review should catch.
4. **Two colour-metrics are maintained** (CIEDE2000 for the frozen test, OKLab for the palette's
   provenance). The cost is one extra function; the benefit is that swapping the palette later
   cannot silently swap the standard it was held to.
5. **The dashboard is not covered by CI beyond a headless smoke test and a benchmark.** Its real
   surface is a browser, which this stage does not automate — stated so that green CI is not
   read as "the dashboard works".

## Constraints

- No arithmetic or aggregation in `viz/figures.py`, enforced by an AST scan whose own failure is
  tested.
- Every figure function takes an `AnalysisResult` and returns `(plotly_fig, matplotlib_fig)`.
- Every caption is generated, contains `n` and the criteria version, and the citation-snapshot
  date wherever citations entered the number.
- Categorical colour is assigned by entity in fixed slot order, capped at three for all-pairs
  forms; no cycling, no generated hues.
- Sequential encodings use one hue light→dark. No dual-axis chart.
- Dark mode is selected and validated against the dark surface.
- No `theme.py` value is duplicated in a figure module.
- Goldens assert SVG text and structure, never pixels.

## Related decisions

- [ADR 0022](0022-the-analysis-result-contract-and-its-provenance.md) — the `AnalysisResult`
  contract these figures consume, and the `is_partial` column figure 1 shades
- [ADR 0023](0023-taxonomy-assignments-are-derived-and-overrides-replace-a-dimension.md) — the
  counting unit every taxonomy figure must state in its caption
- [ADR 0015](0015-stage-order-and-stage-10-scope.md) — why Stage 10 shipped first, and therefore
  why figure 9 already exists
