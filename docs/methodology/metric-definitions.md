# Metric Definitions

Every quantitative finding this system produces is computed by
`src/prismabib/bibliometrics/` (BUILD_PLAN Stage 7) and returned as an
`AnalysisResult` (`bibliometrics/base.py`) — `data` (a figure-ready
`polars.DataFrame`), `params` (every knob that affected `data`), and
`provenance` (the corpus and the capture it was computed from). `caption()`
renders those three into one sentence, generated rather than typed, so a
figure's caption cannot drift from the number it shows. This page documents
the formula and the convention behind every number; each links to the test
that pins it, so a reader can verify the documented convention is the one
actually implemented.

Full design rationale lives in
[ADR 0022](../architecture/adr/0022-the-analysis-result-contract-and-its-provenance.md);
this page is the formulas, not the reasoning behind the contract shape.

## The provenance every number carries

`Provenance` (`bibliometrics/base.py`) accompanies every `AnalysisResult`:

| Field | Meaning |
| --- | --- |
| `corpus_size` | `n` — record count in the requested PRISMA stage, `records.height` |
| `stage` | Which `PrismaStage` (`RAW`, `INCLUDED`, ...) `n` is over |
| `retrieved_at` | `max(runs.started_at)` — the corpus's own as-at date, independent of `stage` |
| `run_ids` | Every sealed search run that contributed a record to this result, sorted |
| `criteria_versions` | The distinct `criteria.yaml` versions those runs were captured under, sorted |
| `citation_snapshot` | The latest `retrieved_at` among the citation rows read, or `None` if this result never read citations |
| `citation_snapshot_is_uniform` | `False` when the citation rows disagree about `retrieved_at` |

`caption()` always states `n` and `retrieved_at`; it adds a citation-snapshot
clause only when `citation_snapshot` is not `None`, and every scalar entry of
`params` is rendered as `key=value`. This is what makes
`test_keywords__min_occurrence_change__changes_params_and_caption` (S07-AC4)
true for every analysis generically: changing a parameter changes `params`,
and the caption is derived from `params`, so it changes too — no module
writes its own caption logic.

**"Partial final year" is derived from the corpus, never from the wall
clock** (ADR 0022 Decision 2): `first_incomplete_year(corpus)` is
`year(max(runs.started_at))`. No module under `bibliometrics/` calls
`datetime.now()`, `date.today()` or `time.time()` —
`tests/unit/bibliometrics/test_no_clock.py` scans every file's AST for
exactly those calls.

## Trends (`bibliometrics/trends.py`)

### Annual publication counts — `annual_counts(corpus, *, stage=INCLUDED)`

`data`: `year`, `count` — one row per publication year with at least one
record, sorted by `year`. A record with no `year` (Layer 1 does not require
one, though a real capture can never actually produce one — see below)
counts toward `n` but contributes no row: reporting it under a made-up year
would be a worse dishonesty than an undercount a reader can see is smaller
than `n`.

Pinned by `tests/unit/bibliometrics/test_trends.py::test_annual_counts_frame__*`.

### CAGR — `cagr(corpus, *, stage=INCLUDED, include_partial_final_year=False)`

```
CAGR = (V_end / V_start) ** (1 / (Y_end - Y_start)) - 1
```

computed over **complete years only** by default — every year
`>= first_incomplete_year(corpus)` is excluded (ADR 0022 Decision 2).
`include_partial_final_year=True` overrides this; the choice is recorded in
`params` and therefore the caption either way.

`data` is one row exposing `cagr`, `v_start`, `v_end`, `year_start`,
`year_end`, `span_years`, `partial_years_excluded` — a bare growth rate is
not checkable, so every input to the formula is in the output beside it.

Raises `AnalysisError` rather than returning a number for:

| Input | Why |
| --- | --- |
| `v_start == 0` | the ratio is infinite; "infinite growth" is not a finding |
| Fewer than two distinct years after partial-year exclusion | a single year (or none) has no growth rate — this also covers `span_years == 0`, since two *distinct* years can never span zero |

An empty corpus is the one case in this whole package that raises rather
than returning a zero-row result (ADR 0022 Decision 10): there is no growth
rate over no years, and `0.0` would be a number a reader could quote.

Pinned by `tests/unit/bibliometrics/test_trends.py`:
`test_cagr__known_geometric_series__matches_analytic_value` (S07-AC2,
exact 30% growth returns `0.30` to `1e-9`),
`test_cagr__partial_final_year__is_excluded_by_default`,
`test_cagr__partial_final_year_forced_in__result_is_lower_and_flagged`,
`test_cagr_bounds__zero_start_value__raises_rather_than_returning_inf`,
`test_cagr__result__exposes_v_start_v_end_and_span`.

## Geography (`bibliometrics/geography.py`)

### Country counts — `country_counts(corpus, *, stage=INCLUDED, method="full")`

Two counting methods, always in `params` and therefore the caption:

- **`"full"`** (default): each distinct country on a record counts once for
  that record. Shares are over records, so a heavily co-authored corpus's
  shares sum to **more than 100%** — documented, intended behaviour, not a
  bug.
- **`"fractional"`**: each record contributes exactly `1.0`, split `1/k`
  over its `k` distinct countries. Shares sum to `1.0` within float
  tolerance.

A record with no country-bearing affiliation at all — no affiliation data,
or every affiliation's country is unmapped/absent — is bucketed under the
explicit country `"UNK"`. Both modes account for **every** record in
`stage`: dropping the unknowns would make the denominator quietly smaller
than the corpus and every other share quietly larger.

`data`: `country`, `count`, `share`, sorted by `count` descending then
`country` ascending.

Pinned by `test_geography__full_counting__shares_sum_within_tolerance`,
`test_geography__fractional_counting__shares_sum_to_one`,
`test_geography__unknown_country_bucket__preserves_record_total`,
`test_geography__no_affiliation_data_at_all__every_record_is_unk`.

### Citation impact by country — `citation_impact_by_country(corpus, *, stage=INCLUDED, method="full", at=None)`

The same counting method applied to citation counts: `n_records` (the
weighted country membership `country_counts` also reports),
`total_citations`, `mean_citations`. A record with no citation snapshot
contributes `0` citations rather than being dropped, so the denominator
matches `country_counts`'s for the same record set.

## Venues (`bibliometrics/venues.py`)

### Normalisation

Deliberately conservative (ADR 0022 Decision 5) — casefold, collapse
whitespace, strip a trailing parenthetical, strip a leading `The`, unify
`&`/`and`, drop trailing punctuation. Nothing merges venues on a similarity
score: a wrong merge silently invents a venue that published papers it did
not. The display name for a normalised group is the **most frequent** raw
variant, ties broken lexicographically — deterministic and reproducible.

Pinned by `test_normalise_venue_name__variants__fold_to_the_same_key` and
`test_venues__name_variants__group_together`.

### Top venues — `top_venues(corpus, *, stage=INCLUDED, top_n=20)`

`data`: `venue`, `venue_type`, `count`, sorted by `count` descending then
`venue` ascending, truncated to `top_n`. `venue_type` is the single type
when every raw variant agrees, `"mixed"` when Scopus indexes the same
venue under more than one `prism:aggregationType` — naming the disagreement
rather than silently picking one.

`report/numbers.py::_venue_numbers` and `report/tables.py::top_venues_table`
delegate to this function (over `PrismaStage.RAW`, their historical,
unfiltered scope) rather than grouping by exact `venues.name` themselves —
see [ADR 0022 Decision 5](../architecture/adr/0022-the-analysis-result-contract-and-its-provenance.md#5-venue-normalisation-is-defined-here-and-stage-10-delegates-to-it)
and [Limitations](limitations.md) for the measured effect on the live
corpus.

### Venue-type split — `venue_type_split(corpus, *, stage=INCLUDED)`

`data`: `venue_type`, `count`. A venue with no `venue_type` is reported
under `"unknown"`, never dropped.

## Citations (`bibliometrics/citations.py`)

`Corpus.citations()` carries no `stage` parameter (it is a Stage 3 accessor
over every record the store holds); both functions here read
`Corpus.records(stage)` first and restrict citation rows to that record set,
which is also why every result here carries a citation snapshot in its
`Provenance`.

### Citation statistics — `citation_statistics(corpus, *, stage=INCLUDED, at=None)`

`data` is one row: `records_with_a_snapshot`, `total`, `mean`, `median`,
`max`, `h_index`, `zero_cited_share`.

**h-index**: the largest `h` such that `h` records each have at least `h`
citations, computed by sorting citation counts descending and finding the
last position where `count >= position`. `0` for an empty or all-zero
input. Monotone non-decreasing under adding a citation to any record, or
adding a newly-cited record — checked as a Hypothesis property,
`test_hindex__monotone_under_added_citations`, over generated citation
lists, not merely on examples.

Pinned by `test_hindex__hand_computed_fixture__matches` (S07-AC3, the
textbook `[10, 8, 5, 4, 3] -> h=4` example) and
`test_citation_statistics__hand_computed_counts__matches`.

Unlike CAGR, an all-zero citation table on an empty corpus is a real,
reportable state (ADR 0022 Decision 10) — every field is `0`/`0.0`, not an
error.

### Citations by year — `citations_by_year(corpus, *, stage=INCLUDED, at=None)`

`data`: `year`, `mean_citations`, `records`. `report/numbers.py::_citation_numbers`
delegates to `citation_statistics` (over `PrismaStage.RAW`) rather than
re-querying `citation_snapshots` itself.

## Keywords (`bibliometrics/keywords.py`)

Full counting over `term_norm` — every record carrying a term counts once
toward that term, unweighted by how many keywords the record carries
overall.

### The stopword list is project data, never a source literal (ADR 0022 Decision 6)

A default list ships as a package data file,
`src/prismabib/bibliometrics/data/stopwords.txt`; a caller may pass a
project's own override — conventionally `<project>/config/stopwords.txt` —
via `stopwords_path`. The module resolves no project path itself: it only
has a `Corpus`, not a `Project`, so the caller (a notebook, a CLI command,
`prismabib.report`) decides whether an override exists.
`test_stopwords__come_from_project_data_not_source` scans the module's own
source text for the default list's terms, so hardcoding the list back into
Python reds it.

### Keyword frequency — `keyword_frequency(corpus, *, stage=INCLUDED, kind="author", min_occurrence=1, stopwords_path=None)`

`data`: `term`, `count`, restricted to `count >= min_occurrence`, sorted by
`count` descending then `term` ascending. `min_occurrence` and `kind` are
in `params` (S07-AC4).

### Keyword evolution — `keyword_evolution(corpus, *, stage=INCLUDED, kind="author", min_occurrence=1, stopwords_path=None)`

`data`: `year`, `term`, `count` — the same `min_occurrence` threshold
applied to a term's *total* count across every year, so this reports on
the same term set `keyword_frequency` does.

## Networks (`bibliometrics/network.py`)

Edge weight = the number of records in which both terms (or both authors)
appear — never a similarity score.

### Clustering: Louvain, seeded

`networkx.algorithms.community.louvain_communities(graph, resolution=, seed=)`.
Louvain is randomised, so `seed` (default `0`) is always recorded in
`params` alongside `min_occurrence`, `top_n` and `resolution` — an unseeded
clustering is not reproducible and therefore not publishable (ADR 0022
Decision 7). Leiden is not used: it requires `igraph`, not a project
dependency, and the supply-chain surface is not worth a marginally better
modularity score here.

**Community-label determinism.** `louvain_communities` returns a list of
Python `set`s, and `PYTHONHASHSEED` (which `pytest-randomly` varies)
changes `set` iteration order, not the algorithm's own seeded randomness.
Community ids are therefore assigned by sorting each community's members
and then sorting the communities by their lexicographically smallest
member — independent of any hash seed. Pinned by
`test_network__clustering__is_deterministic_under_fixed_seed`, run under
`pytest-randomly`.

### Keyword co-occurrence — `keyword_cooccurrence_network(corpus, *, stage=INCLUDED, kind="author", min_occurrence=2, top_n=50, resolution=1.0, seed=0, stopwords_path=None)`

`data`: `node_a`, `node_a_label`, `node_b`, `node_b_label`, `weight` — the
top `top_n` edges by weight. `params["communities"]` carries
`node_id -> community_id` for every node in the (untruncated) graph.

Pinned by `test_network__cooccurrence__edge_weight_equals_manual_count`, a
six-record fixture an analyst can count by hand.

### Co-authorship — `coauthorship_network(corpus, *, stage=INCLUDED, min_occurrence=1, top_n=50, resolution=1.0, seed=0)`

Same shape; node ids are Scopus `author_id` values, labels are surnames.

### VOSviewer export

`map.txt` (`id`, `label`, `weight`, `cluster`) and `network.txt` (`id1`,
`id2`, `weight`), tab-separated, `\n` line endings on every platform. File
format pinned by a golden test,
`tests/golden/bibliometrics/test_vosviewer_export.py`, against a checked-in
fixture computed from the same six-record co-occurrence example.

## Why `Provenance` is a dataclass and not a JSON-scalar dict

Unlike `report/numbers.py`'s `numbers_map` (every value a JSON scalar, for
substitution into manuscript prose), `AnalysisResult.params` is not
restricted to scalars: a network's community assignment is a legitimate
`params` entry with no sensible scalar form. `caption()` renders only the
scalar entries of `params`; every entry, scalar or not, is part of what
`test_all_analyses__recomputed_twice__is_bit_identical` (S07-AC5) compares
— as canonical JSON bytes (`json.dumps(sort_keys=True)`), never as two
in-process objects compared for equality, which would pass while the bytes
a reader actually receives differed.

## The `Corpus` accessors this stage adds

`Corpus.venues(stage)`, `Corpus.affiliations(stage)`, `Corpus.authors(stage)`
(ADR 0022 Decision 9) — the frozen Stage 3 contract's first amendment. Each
follows `Corpus.records`/`Corpus.keywords`'s own delegation to the Stage 4
PRISMA engine exactly: same total ordering, same `stage`-filtered record
set, one implementation of "what stage means" shared by every accessor. See
`src/prismabib/store/load.py`'s `Corpus` class and
`tests/integration/store/test_corpus_bibliometrics_accessors.py`.

## Related pages

- [ADR 0022: The `AnalysisResult` contract and its provenance](../architecture/adr/0022-the-analysis-result-contract-and-its-provenance.md)
- [PRISMA Mapping](prisma-mapping.md) — the same "one producer per number" discipline, one layer down
- [Testing](../testing.md) — why every metric here is tested against an independently derived value, never against the function's own prior output
- [Limitations](limitations.md)
