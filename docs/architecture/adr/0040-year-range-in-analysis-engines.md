# ADR 0040: The Year Range Is an Engine Argument, and Every Caption Says Whether It Applied

## Status

Accepted — 2026-10-30, on the merge of the pull request that adds this record, which the owner approves. Until the
merge, this record is `Proposed` on the branch `docs/adr-0040-year-range-in-analysis-engines`. Accepting it changes no
code, test or golden: it fixes the design that five pull requests implement (Decision 11). The owner's answers to the
open points are in "Questions put to the owner, and the answers".

ADRs 0001 to 0039 are frozen. This record appends one dated pointer note to each of ADR 0022 and ADR 0025, and edits no
other older text.

## Context

PR #230 wired the dashboard year range through `_year_filtered` and `_year_filtered_frame` (`viz/dashboard.py`). That filter
works on a frame that carries a `year` column: the trend, keyword-evolution, citations-by-year, taxonomy-evolution and
corpus-browser frames. Four dashboard views have no such column, and their engines take no year range:

| Tab | Engines | What cannot be filtered |
| --- | --- | --- |
| Geography | `bibliometrics/geography.py`: `country_counts`, `citation_impact_by_country` | country counts and shares; citation impact by country |
| Venues | `bibliometrics/venues.py`: `top_venues`, `venue_type_split` | top-N venues; venue-type counts |
| Network | `bibliometrics/network.py`: `keyword_cooccurrence_network`, `coauthorship_network` | edges, communities, node frequency |
| Taxonomy | `taxonomy/analysis.py`: `dimension_distribution` | the per-dimension distribution |

These tabs caption themselves "does not apply" (`Dashboard._caption_pane(..., by_year=False)`). The caption is honest, but a
reviewer who sets 2020-2021 still sees a whole-corpus Geography tab, and the tab cannot apply the range until the engine
accepts one.

Post-filtering the result does not work for these frames. A share cannot be recomputed from a table of country counts. A
top-N cut taken before a filter can return venues with no paper in the range. A community structure computed over the whole
stage is not the structure of the range. The range has to reach the engine before any counting happens.

Two measured facts from the code shape the decisions below. `records_all.year` is nullable: a Scopus row can lack a year
(`store/schema.sql`, `records` and the `records_all` view). And `taxonomy/coder.py` never reads `year`, so one `code()` result
serves every range.

**Owner decisions of 2026-10-30.** "Owner decision N" in this record refers to the following, as the owner gave them:

1. Each engine gains an optional `years` argument, and filters the record set once, through one shared helper.
2. `years` is carried in `params` only when it is set.
3. `provenance.n` stays the stage count. The in-range count goes into `params` and the caption.
4. Citation impact by country is papers published in the range with their cumulative snapshot citations. Citations received
   within the range are not computed.
5. Network edges come from in-range papers only. Communities and the layout are recomputed, and the result is independent of
   input row order.
6. Top-N is taken after filtering. Fractional and full counting run inside the range, and the uncoded denominator is the
   in-range record count.
7. Records with a null year drop once a bound is set, and the caption reports how many dropped.
8. Report code keeps calling without `years`, so exports cannot change. The dashboard reaches the engines only through its tab
   builders.

## Decision

### 1. The argument, and the one helper

Each of the seven functions in the table above gains a keyword-only argument:

```python
years: YearRange | None = None  # YearRange = tuple[int | None, int | None]; inclusive; None bound = open
```

`YearRange` is defined in `bibliometrics/base.py`, beside `AnalysisResult`. The one helper, also in `base.py`, is:

```python
def restrict_to_years(records: pl.DataFrame, years: YearRange | None) -> tuple[pl.DataFrame, int]: ...
```

It returns the in-range rows of `records` and the number of rows it dropped for a null `year`. It is the only code that
compares `year`. Each engine calls it once, on `corpus.records(stage)`, before it reads any other frame. Every other frame the
engine reads (keywords, authors, affiliations, venues, citations, and the taxonomy record ids) is then semi-joined on
`record_id` to the in-range records. No engine filters a secondary frame on `year` itself. The unrestricted stage frame is
kept, because Decision 2 needs it (see the provenance rule there).

The helper normalises its input (owner-confirmed, 2026-10-30):

- `None`, and `(None, None)`, both mean "no range". The input comes back unchanged with a drop count of `0`.
- A start greater than an end raises `AnalysisError`.
- Each bound is an `int` or `None`.

### 2. `params` and `provenance` change only when a range is set

With no range, `params` and `provenance` are byte-identical to today, so `numbers.json` and the goldens do not move. With a
range set, `params` gains exactly these three keys:

| Key | Value | Why |
| --- | --- | --- |
| `years` | a string: `"2020-2021"`, `"2020-..."` or `"...-2021"` | A string keeps `params` scalar, so it reaches the caption and serialises the same way every time. The format matches `viz/dashboard.py::_year_range_text`. |
| `n_in_range` | int: records inside the range | The count the figures and shares are over (owner decision 3). |
| `n_no_year_dropped` | int: stage records with a null `year`, dropped | Decision 4 (owner decision 7). |

`years` is never written as `None`. A test asserts `"years" not in result.params` for every engine called without a range.

**Provenance rule.** Engines pass the unrestricted stage frame, `corpus.records(stage)` before `restrict_to_years` is applied, to
`build_provenance`. Only the analysis body uses the restricted rows. So `corpus_size` (`n`), `stage`, `run_ids` and
`criteria_versions` stay stage-level, and `n` equals the PRISMA flow count (owner decision 3). The restricted rows must never
reach `build_provenance`, because that would make `n` the in-range count.

**Exception to ADR 0022 Decision 1.** ADR 0022 Decision 1 defines `run_ids` as "every sealed search run contributing", and the
`Provenance` docstring in `bibliometrics/base.py` says the runs "that contributed at least one record to this particular
result". With a range set, this record keeps `run_ids` and `criteria_versions` stage-level as well, as the owner confirmed
(Question 2). So they can list a run that contributed no in-range record. This is a deliberate exception to that wording. It follows the precedent of `_year_filtered` in `viz/dashboard.py`, which keeps the stage's count for the same reason.
ADR 0022's pointer note records the exception too.

The one provenance field that follows the range is `citation_snapshot`: it is the latest `retrieved_at` among the citation rows
the analysis read (ADR 0022 Decision 1), so an analysis that reads only in-range citations reports only their snapshot.

### 3. The caption states the range

`AnalysisResult.caption()` gains two rules, both used only when `params` carries `years`.

- The clause "papers published in {years}" replaces the `years=` key=value entry.
- When the result also carries a citation snapshot (`provenance.citation_snapshot` is set), the existing citation clause is
  followed by the caveat "older papers have had longer to accumulate citations". The caveat is a property of the citation
  clause, not a new `params` key, so `params` stays at the three keys of Decision 2.

Every other scalar entry renders as before. `n_in_range` and `n_no_year_dropped` are scalar ints, so they render through the
existing key=value path. The opening clause `n = <stage count> (<stage>)` stays first.

The owner's wording for the citation-impact caption is "papers published in X-Y, citations as of snapshot; older papers have
had longer to accumulate citations". The citation clause that exists today reads "citations as at <date>", and this record
keeps that clause so that every caption uses one wording for the same fact. The owner's "as of snapshot" is rendered by it.

Illustration, with invented numbers and not a measurement, for a country-share result: `n = 1293 (included); corpus as at
2026-09-02; papers published in 2020-2021; method=full, n_in_range=640, n_no_year_dropped=3.` For citation impact, the same
sentence gains "citations as at 2026-09-02; older papers have had longer to accumulate citations" after the corpus clause.

### 4. A record with no year drops once a bound is set

A stage record with a null `year` lies in no range. Once any bound is set, such records drop from every frame the engine
returns, and the drop count goes to `n_no_year_dropped` (owner decision 7). With no bound, nothing drops, as today. The Corpus
browser already says this in its summary line (`_corpus_browser_tab`).

### 5. What each engine computes inside the range

- **`country_counts`.** Membership, the `full` and `fractional` weights and the counts all come from in-range records.
  Shares divide by `n_in_range`, not by the stage count, so they describe the range's own records (owner-confirmed,
  2026-10-30; owner decision 6). Records with no known country still go to `UNK` (ADR 0022 Decision 4).
- **`citation_impact_by_country`.** Papers published in the range, each with its cumulative citation count from the snapshot
  (`at`, or the latest per record). Citations received within the range are not computed, because the store holds
  snapshots of cumulative counts, not citation history (owner decision 4). A record with no snapshot counts `0`, as today.
  The caption carries the age caveat of Decision 3.
- **`top_venues`.** Venue rows are restricted first. Normalisation and grouping run over the in-range rows, and `top_n` is
  applied last (owner decision 6).
- **`venue_type_split`.** Counts over in-range rows. The `"unknown"` rule is unchanged.
- **`keyword_cooccurrence_network` and `coauthorship_network`.** `min_occurrence` eligibility, edge weights (the records in
  which both nodes appear), node `frequency` and `cluster_size`, Louvain communities and `top_n` all come from the in-range
  records, in that order (owner decision 5). Community ids restart at `0` for each range, so they are not comparable across
  ranges. The layout is computed by `viz/figures.py::network_figure` from the data it receives, so it follows without a change
  to the engine. Determinism: the existing construction sorts record and node ids before it iterates, so the output does not
  depend on the row order of any input frame. This property is kept and tested (Verification). The author label rule is
  pinned in Decision 12.
- **`dimension_distribution`.** `result` is replaced by a view whose `record_ids` are the in-range ids in their original
  order, via `dataclasses.replace`. `coder.distribution()` and its sum-to-|C| guard are unchanged, and they run over the
  in-range records. So `uncoded`, `reviewed_none` and `Distribution.corpus_size` count in-range records (owner decision 6).
  The coding is not rerun, because `coder.py` does not read `year`. `dimension_evolution` is unchanged, and the year-keyed
  engines keep the dashboard post-filter (Question 4).

### 6. Report code never passes a range

`report/numbers.py::_venue_numbers` and `report/tables.py::top_venues_table` keep calling `top_venues` without `years`, and
every other report caller does the same. Exports cannot change (owner decision 8).

### 7. The dashboard reaches the engines only through tab builders

`FilterState` gains a read-only property `years`: `None` when both `year_start` and `year_end` are `None`, otherwise
`(year_start, year_end)`. The Geography, Venues, Network and Taxonomy-distribution builders pass
`years=self._filter_state.years` (owner decision 8). Their captions move from `by_year=False` to the applied form.
`by_year=False` stays for the Overview's `citation_statistics`, which is not year-keyed and is out of scope. The figures layer
computes nothing new (ADR 0025 Decision 1).

Two range statements then appear in one view, and this is accepted. The engine caption ("papers published in X-Y", from
`AnalysisResult.caption()`) says what the number covers. The pane line ("Year range X-Y applied.", from `_caption_pane`,
`viz/dashboard.py` lines 427 to 433) says that the dashboard applied the filter. The first is the statement of the number, and
the second is the statement of the control.

`citation_impact_by_country` and `venue_type_split` have no dashboard caller today. This is a fact of the current code, and
PR 5 wires no view for either of them unless a view exists by then. They still gain `years` in PR 2 for engine parity.

### 8. An empty range is a defined case, per engine

A range with no in-range records gives `n_in_range = 0` and a caption that says so (ADR 0022 Decision 10). The frame it returns
depends on the engine:

- `country_counts`, `citation_impact_by_country`, `top_venues`, `venue_type_split`: an empty frame with its correct schema. These
  are the existing empty branches (`records.height == 0`, `venues.height == 0`), reached because the frames are restricted first.
- `keyword_cooccurrence_network`, `coauthorship_network`: an empty edge set and no node rows, so an empty frame with the graph
  schema.
- `dimension_distribution`: **not** an empty frame. `coder.distribution()` seeds every declared category and the
  `uncoded` and `reviewed_none` buckets with `0`, so the result has one zero-count row per bucket. Its `corpus_size` is `0`.

### 9. No clock and no new state

The helper compares integers and reads no clock (ADR 0022 Decision 2; `tests/unit/bibliometrics/test_no_clock.py` scans
`base.py`). `Corpus` gains no accessor and no argument, because ADR 0022 Decision 9 keeps the PRISMA-stage contract where it is.

### 10. Keyword-only, and backward compatible

`years` is keyword-only on each engine. A call that omits it produces the bytes it produced before this change, and no
existing caller changes.

### 11. Five pull requests, in this order

1. **This ADR, the helper and the caption rule.** `YearRange`, `restrict_to_years`, the caption clauses, the helper's unit
   tests, the two pointer notes and the nav line. No engine changes.
2. **Geography and Venues.** `country_counts`, `citation_impact_by_country`, `top_venues`, `venue_type_split`.
3. **Taxonomy distribution.** `dimension_distribution`.
4. **Networks.** `keyword_cooccurrence_network`, `coauthorship_network`, and the author label rule of Decision 12.
5. **Dashboard wiring.** `FilterState.years`, the four tab builders, and captions that read "applied". The PR 5 description
   must say that the hard-coded taxonomy caption `"Distribution over every year."` (`viz/dashboard.py`, the `_taxonomy_tab`
   method, line 551, `by_year=False`) changes when a range applies, and what it changes to. Today it says "every year" over a
   range-less view, which would be false under a range.

Every PR leaves `numbers.json`, the goldens and the report exports unchanged. The five-PR plan is confirmed (Question 1).

### 12. Author labels are pinned

`coauthorship_network` builds its label map as `dict(zip(author_id, surname))` over every author row (`network.py`, the
`labels` assignment in `coauthorship_network`). When one `author_id` has several surnames across its records, the last row in
the frame wins, and a range can change which row is last. So the label could change with the range, or with row order.

The rule is pinned in the networks PR: **the label of an author node is the lexicographically smallest non-null `surname` among
that author's in-range rows.** The minimum is order-independent, and it is computed over the rows the engine reads, consistent
with Decision 5. An author whose in-range rows all have a null surname gets no label entry, so `labels.get(node_id, node_id)`
falls back to the author id. Today such an author gets a `None` label. That is a change for a case that the fixtures must
show, and the PR says so.

Labels are per call, like community ids. The same author can carry a different label under a different range, and this is
accepted as part of Decision 5.

The same labels feed the VOSviewer export (`_export_vosviewer` reads `node_a_label` and `node_b_label` from the edge rows). If
the VOSviewer golden fixture contains an author with several surnames, the rule would move that golden, and the goldens rule
(Verification) forbids that in any PR. This record does not pre-approve such a change: PR 4 stops and the owner decides.

### 13. Future work, not part of this record: a venue-missing snippet

The owner decided, on 2026-10-30, that a later optional `methods.*` snippet will report how many stage records had no venue
name. Such a snippet is exempt from `report/fill.py`'s unused-key check, which exempts only `methods.*` (ADR 0039 Decision 3
records the same mechanism), so no existing template breaks.

It is future work and is not part of the year-range implementation or of the five PRs above. It adds a key to
`tests/golden/report/__snapshots__/numbers_keys.json`, which lists the keys of `numbers.json`, so that golden moves when the
snippet is built. It needs its own short ADR note at that time, and this record does not reserve or name the key.

## Alternatives considered

- **Post-filter on the dashboard side, as for year-keyed frames.** Rejected. These frames carry no year, and a share, a top-N cut
  or a community structure cannot be restricted after it was computed over the whole stage (Context).
- **Add a `year` column to these frames and keep the dashboard filter.** Rejected. Every consumer of the frames would have to
  handle a year dimension it does not use, and shares and rankings would move toward the figure layer.
- **A `years` argument on `Corpus.records()`.** Rejected for this stage. It would move a range into the PRISMA-stage contract
  that ADR 0022 Decision 9 keeps narrow, and `provenance.n` would then depend on the range, which owner decision 3 rules out.
- **`years` stored as a tuple in `params`.** Rejected. The caption would need a second special case, and a tuple has no
  written form for an open bound. The string form is canonical.
- **Always write `years` into `params`, as `None` when unset.** Rejected. It changes every existing `params` byte, which moves
  `numbers.json` and the goldens (owner decision 2).
- **Citations received within the range.** Rejected (owner decision 4). The store holds cumulative snapshots, not citation history.
- **A fourth `params` key for the age caveat.** Rejected. The caveat belongs to the citation clause, and a `params` entry would
  add a knob that changes no number.
- **Last-row-wins author labels, as today.** Rejected in Decision 12. The label would depend on row order and on the range.

## Consequences

- Each of the four tabs can be filtered, and says so in its caption. With no range set, the bytes are unchanged.
- Seven public signatures change, all additively. Their tests and the four tab captions change in PRs 2 to 5.
- Shares and rankings inside a range are over the range's records, so they do not match the stage's `n` on the PRISMA tab.
  The caption states `n_in_range` beside `n` so the difference is visible.
- Community numbering is per call. Communities in two ranges cannot be matched by id. Author labels are per call too (Decision 12).
- **Citation impact by range compares papers of different ages.** A 2020 paper has had longer to accrue citations than a 2025
  paper, so a range's mean reflects its age profile. Decision 3 puts the caveat in the caption (Question 6).
- A range drops records with no year. `n_no_year_dropped` puts the drop into the caption rather than leaving it silent.
- `run_ids` and `criteria_versions` can list runs with no in-range record when a range is set (Decision 2).

## Verification

Each PR runs `pytest -n auto`, as CI does, and once single-process. It also runs `mypy --strict` on `src/prismabib` at Python
3.11, and `ruff`. PR 1 additionally runs `mkdocs build --strict`.

For each engine (PRs 2 to 4), every new test is shown to fail on the defect it guards:

- **Unset range is byte-identical.** `_serialise` of the result equals the bytes from `main` on the reference fixture, and
  `"years" not in params`.
- **Hand-derived values.** An in-range count, share, weight or mean is checked against a value worked out by hand on a small
  fixture. It is not checked against the function's own earlier output (ADR 0022 Constraints).
- **Secondary frames are restricted.** A keyword, author, venue or citation row for an out-of-range record is present in the
  fixture and must not change the result. This guards the semi-join.
- **Provenance is stage-level.** With a range set, `provenance.corpus_size` equals the stage count and `run_ids` equals the
  stage's run set, including a run that has no in-range record. This guards the provenance rule of Decision 2.
- **Top-N after the filter.** In a fixture where the stage's first venue has no in-range paper, the in-range top-N starts with
  the next venue.
- **Null year.** A record with no year drops when a bound is set, and `n_no_year_dropped` equals the drop count. With no bound,
  it is kept.
- **Empty range, per engine.** The schema is correct for the frame-returning engines and `n_in_range = 0`. For
  `dimension_distribution`, the result has one zero-count row per declared category and per bucket, not an empty frame.
- **Row order.** Every input frame is reversed and `_serialise` of the result is unchanged. For the networks this includes the
  community assignment under the fixed `seed`.
- **Author label.** A fixture in which one `author_id` carries two surnames across two records, for example `Smith` and
  `Adams`. Reversing the input rows gives identical bytes, and the label is `Adams`. With a range that keeps only the `Smith`
  record, the label is `Smith`. The first assertion fails on last-row-wins, because reversal changes the label.
- **Dimension distribution.** The guard for `multi_label: false` over the in-range subset is tested with an over-assigned record
  outside the range, which must not raise, and inside the range, which must raise.
- **Citation caveat.** With a range and a citation snapshot, the caption contains the age caveat; without a range it does not.

PR 5 adds a dashboard test that reads each of the four tab captions. With a range set, each says "applied"; none says
"does not apply". The same PR updates the hard-coded taxonomy caption at `viz/dashboard.py` line 551 (Decision 11).

Goldens: `numbers.json`, `numbers_keys.json`, the report reference and the figure caption golden must not change in any PR.
A changed golden stops that PR.

Governance: the Status line is in the form that `tests/unit/test_docs_governance.py::test_adr__on_main__is_not_still_proposed`
accepts, the same form ADR 0038 and ADR 0039 use (Question 3).

## Records touched

- New: this file.
- Appended, dated 2026-10-30, no older text edited: `docs/architecture/adr/0022-the-analysis-result-contract-and-its-provenance.md`
  and `docs/architecture/adr/0025-figures-render-what-the-engines-computed.md`.
- Edited: `mkdocs.yml`, one nav entry after the ADR 0039 entry.
- Not touched by this record: code, tests, goldens, `CHANGELOG.md` and `.claude/PROGRESS.md`. The five pull requests own them.

## Related decisions

- [ADR 0022](0022-the-analysis-result-contract-and-its-provenance.md): the `AnalysisResult` contract, `params` and the caption,
  provenance, the empty case, the no-clock rule, and the accessor boundary of Decision 9. Its Decision 1 wording on `run_ids`
  is excepted in Decision 2 above.
- [ADR 0025](0025-figures-render-what-the-engines-computed.md): figures render what the engines computed; the shared handle and
  the frozen filter state of Decision 7.
- [ADR 0023](0023-taxonomy-assignments-are-derived-and-overrides-replace-a-dimension.md): counting units and the sum-to-|C| guard
  that `dimension_distribution` runs over the range.
- [ADR 0039](0039-taxonomy-engine-behaviour-stamp.md): the `taxonomy_engine_behaviour` entry in `params`, which a range leaves
  in place, and the `methods.*` exemption from the unused-key check that Decision 13 relies on.
- [ADR 0038](0038-refresh-semantics-of-the-http-cache.md): the precedent for appended, dated notes on a frozen record, and for
  the Status wording used here.

## Questions put to the owner, and the answers

1. **Pull request count.** The brief said "4 PRs" but listed five steps. **Answer: five**, as in Decision 11: ADR and helper;
   geography and venues; taxonomy distribution; networks; dashboard wiring.
2. **Provenance `run_ids` and `criteria_versions`.** **Answer: stage-level**, to match `n`, including with a range set. This is
   an exception to ADR 0022 Decision 1's wording, recorded in Decision 2 and in ADR 0022's pointer note.
3. **Status and the governance test.** **Answer: the ADR 0038 and ADR 0039 form.** The Status reads `Accepted — 2026-10-30, on
   the merge of the pull request that adds this record, which the owner approves`, and names the branch on which it is
   `Proposed` until the merge. The owner's note is that the governance test forbids a `Proposed` ADR on `main`.
4. **Year-keyed engines.** **Answer: keep the dashboard post-filter.** `annual_counts`, `keyword_evolution`,
   `citations_by_year` and `dimension_evolution` do not gain `years` in this record (Decision 5).
5. **Share denominator.** **Answer: confirmed.** `country_counts` shares divide by `n_in_range` (Decision 5).
6. **Citation age caveat.** **Answer: in the caption.** The impact caption states "papers published in X-Y, citations as at
   <date>; older papers have had longer to accumulate citations" (Decision 3).
7. **Input rules.** **Answer: confirmed.** `(None, None)` normalises to `None`, and a start greater than an end raises
   `AnalysisError` (Decision 1).
8. **Not wired today.** **Answer: noted as a fact.** `citation_impact_by_country` and `venue_type_split` have no dashboard caller
   today, and PR 5 wires no view for them unless a view exists by then (Decision 7).
