# ADR 0013: `identified` Sums `total_results` Across Distinct Searches

## Status

Accepted — 2026-08-27. **This is the second deviation from the frozen `FlowCounts`
contract** (BUILD_PLAN §Stage 4, lines 978–991); [ADR 0007](0007-flow-counts-unsure-fields.md)
was the first. Like that one it was approved by the project owner before implementation, and
like that one it is recorded here because §2.6 requires an ADR for any deviation from a
frozen contract. It adds two integer fields and rewrites equation 1 of
`FlowCounts.assert_consistent()`. Equations 2, 3 and 4, and every field either BUILD_PLAN or
ADR 0007 declared, are unchanged.

It is also **the second addition to the frozen Layer 1 schema** (§Stage 3, lines 847–879),
after [ADR 0012](0012-persisting-skipped-layer0-entries.md)'s `malformed_entries`: one new
table, `run_duplicates`. ADR 0012's constraints require a further addition to `schema.sql` to
have its own ADR under the same rule; this is that ADR. As with 0012, no existing table,
column, type or primary key changes.

## Context

S02-AC5 makes `runs.total_results` — copied verbatim from a sealed run's
`RunManifest.total_results`, itself Scopus's own `opensearch:totalResults` — the only
sanctioned source of the PRISMA "records identified" count. It is never a row count, and
nothing in this ADR changes that.

What it did *not* settle is which run's `total_results`, once a project has more than one.
`flow._identified_count` answered with the earliest:

```sql
SELECT total_results FROM runs ORDER BY run_id LIMIT 1
```

### The earliest-run rule was not wrong when it was written

It was written against the only multi-run scenario the codebase modelled, and against that
scenario it is exactly right. `store/load.py` documents it under **"Re-captured records"**:
the same Scopus paper "can legitimately appear in more than one sealed run (e.g. the same
query re-run later to refresh citation counts)", and a later run's re-capture contributes
nothing but a `citation_snapshots` row, because `records.record_id` is a primary key and the
record's row is populated once, from the run that first saw it.

A refresh therefore identifies nothing. Summing `total_results` across every run would add
that refresh's total to `identified` while adding no record to the store — inflating the
published identification count of any project that had ever refreshed itself, in the
direction that looks like a larger search. The earliest-run rule made that impossible, and
BUILD_PLAN's own field comment ("`manifest.total_results`" — singular) reads naturally as
pointing at the one manifest that represents the search. With one query per project, the
earliest run *is* the search and every later run is a refresh.

The rule's defect is not its arithmetic. It is that "later run" and "refresh" were treated
as the same thing.

### A second search string is categorically different from a refresh

A refresh re-observes a population that has already been counted. A second search string
counts a population that has not been counted anywhere: different query, different
`total_results`, records that no earlier manifest ever described. PRISMA counts records
identified *per search*, and there is no reading of the statement under which a review that
ran two searches identified only the records the first one returned.

Nothing prevents an operator from doing this, and nothing warned them. `prismabib search`
starts a fresh run directory for whatever query `project.toml` holds at the time; Layer 0
seals it like any other run; `build_store` loads it like any other run. The tool supports
running a second search string in every layer except the one that counts the result.

### What a real corpus did

Two sealed runs, two different search strings:

```text
20260826T064957Z-63236ef3  total_results=651   baseball AND (computer vision | video)
20260826T194140Z-24ece745  total_results=1294  baseball AND (pose estimation | graph ...)
```

The store held **1,864** records. `identified` reported **651** — the first search's total —
against a corpus of which 1,213 records had no representation in the identification count at
all. Because `excluded_automated` is computed as `|S_raw| - |A|` from Layer 1's own rows, it
came out at 754, larger than the whole reported identification, and equation 1 failed:

```text
FlowCounts is inconsistent: 'identified - excluded_automated == after_automated' does not
hold: -103 != 1110 (off by -1213)
```

ADR 0007's non-negativity precondition does not catch this, and should not: every individual
field is a legitimate non-negative count. The incoherence exists only in the relationship
between them, which is precisely what equation 1 is for. It worked — it just had no field to
be satisfied by.

The failure was also **permanent, and its remedy unworkable**. `cli._warn_if_inconsistent`
printed, on every single invocation of `prismabib flow`:

> Do not publish this diagram until the discrepancy is explained. The usual cause is a Layer
> 0 capture that is incomplete, or a store built before the last `prismabib search` finished
> — re-run `prismabib build <slug> --rebuild`.

Both stated causes were false here and the remedy could not work: the store already
contained both runs, and rebuilding reproduced the same numbers. ADR 0007 argued that "a
check that fires constantly is a check people stop reading"; this is that failure mode with
an instruction attached that wastes the operator's time before it fails.

### Summing alone does not close the diagram

`sum(total_results)` for this corpus is 1,945, and the store holds 1,864 records. The
81-record difference is not an error, and it is not one thing:

- **80 entries collapsed onto a record another search had already loaded.** Two searches over
  one register overlap by construction; `records.record_id` is the primary key, so the second
  entry updates nothing and adds no row. The paper was identified twice and screened once.
- **1 entry could not be loaded at all** — the missing `dc:title` that
  [ADR 0012](0012-persisting-skipped-layer0-entries.md) was written for, now one row in
  `malformed_entries`.

Both are PRISMA 2020 "records removed before screening" lines: *duplicate records removed*
and *records removed for other reasons*. Neither had a `FlowCounts` field, so neither could
appear in equation 1, so summing without them would replace an equation that failed by
-1,213 with an equation that failed by 81 — a smaller wrong number, arrived at by removing
the reason anyone would notice it.

## Decision

**`identified` is the sum of `total_results` over the project's distinct searches, and
`FlowCounts` gains the two "removed before screening" terms that make equation 1 close.**

```python
identified: int
duplicates_across_searches: int  # added
removed_other_reasons: int  # added
excluded_automated: int
...
```

The identification rule, stated exactly:

```text
identified = Σ  over each distinct query string in `runs`
             of that query's earliest run's `total_results`
```

Equation 1 of `FlowCounts.assert_consistent()` becomes:

```text
1.  identified
      - duplicates_across_searches
      - removed_other_reasons
      - excluded_automated            == after_automated
2.  after_automated - excluded_language == after_language
3.  after_language     == excluded_title_abstract + unsure_title_abstract + retrieved_fulltext
4.  retrieved_fulltext == sum(excluded_fulltext.values()) + unsure_fulltext + included
```

Verified against the corpus above:

```text
identified (sum over distinct searches)  1,945
  − duplicates across searches               80
  − removed for other reasons                 1
  = records loaded into Layer 1 |S_raw|   1,864
  − excluded by automation                  754
  = to title/abstract  |A|                1,110
```

### Distinct queries, not runs

The sum groups on the query string recorded in `runs.query` and takes one `total_results` per
group — the earliest run's, by `run_id`. This is the whole reason the change is safe: a
refresh re-runs the *same* query, lands in the same group, and contributes nothing. The
earliest-run rule survives intact **within** each search; what changes is that a project is
no longer assumed to have exactly one.

The grouping key is the query string verbatim, never normalised (see Constraints). Where a
refresh legitimately returns a different `total_results` from the original run — the Scopus
index drifts — the *earliest* run's total is the one that counts, because that is the number
the review's identification actually happened under, and because "identified" must not move
when someone refreshes citation counts.

### `total_results` is still never a row count

Every term summed is a server-reported total read back from `runs`. No branch of this
computation counts rows in `records`, and S02-AC5 is untouched. What the two new fields
supply is the *bridge* between that server-reported total and the rows Layer 1 actually
holds — a bridge that previously existed only as an unexplained residual in a failing
equation.

### Why two fields and not one

A single "records removed before screening" field would close equation 1 with the same
arithmetic and one fewer deviation. It is rejected because the two terms are different
facts, with different causes, different remedies, and different meanings for the published
diagram — and PRISMA 2020 prints them on separate lines for exactly that reason.

A duplicate across searches is **expected**. Two searches over one register that never
overlapped would be suspicious; 80 out of 1,945 is the ordinary shape of a two-string search
strategy, and a reviewer reporting it is describing their strategy, not a fault.

An entry that could not be loaded is **a defect in the capture**, of the class BUILD_PLAN
§1.4 exists to make visible: a record that Scopus counted, that the review paid quota for,
and that no reviewer will ever screen because prismabib could not parse it. ADR 0012 built a
whole Layer 1 table so that this fact survives to a reader six months later; folding it into
a bucket labelled "duplicates" would hide it again at the last step, in the one artefact that
gets published.

Summed together they are also un-auditable. A reader who sees one number cannot tell a review
whose searches overlapped heavily from one whose capture partly failed, and those warrant
opposite responses.

### Where each term comes from

Both are read out of Layer 1 by their own function, on the same connection every other count
in `compute_flow_counts` uses: `flow._unloadable_count` and `flow._cross_run_duplicate_count`.

`removed_other_reasons` is `COUNT(*)` over the `malformed_entries` rows ADR 0012 persists —
the dependency that makes this ADR possible at all. Before 0012 the skipped entries were an
in-memory tally that the default `prismabib build` path reported as empty, so a term derived
from it would have been correct only on the `--rebuild` path and silently zero everywhere
else. The `FlowCounts` field is honest because the table behind it is.

`duplicates_across_searches` is `SUM(duplicates)` over a new Layer 1 table, `run_duplicates`,
written by the loader. It counts entries that resolved to a `record_id` an earlier run **under
a different query** had already loaded — identified by two searches, stored once. It is *not*
the normalised-DOI collision report: `StoreStats.duplicate_doi_groups`/`duplicate_records`
count two distinct records that share a DOI, both of which remain in the store as ordinary
rows and both of which are screened. Nothing here removes a record from `records`; duplicates
are still **reported, not applied** (BUILD_PLAN modelling note 4). The only collapse this
field describes is the one the primary key already performed at load time, which until now was
invisible to every count downstream.

### `run_duplicates`: measured during the load, in a table, not derived afterwards

```sql
CREATE TABLE run_duplicates (
  run_id TEXT PRIMARY KEY, duplicates INTEGER
);
```

Three things force this shape.

**It cannot be recomputed after the load.** `records.run_id` keeps only the run that *first*
loaded a record, so once `build_store` returns, Layer 1 no longer knows how many runs — or how
many searches — a given paper appeared in. The loader is the only thing ever in a position to
count it, and BUILD_PLAN §2.2 puts a fact derived from Layer 0 by that same function in Layer
1, where a rebuild reproduces it exactly.

**It is measured, never a remainder.** `identified - |S_raw| - removed_other_reasons` would
give the same number today and would be free. It is refused because it would make equation 1
an identity that cannot fail: any disagreement between a run's manifest and the corpus that
run produced would be silently absorbed into the "duplicates" term and published as a
duplicate count. That is precisely the defect BUILD_PLAN line 993 says this guard exists to
catch. Measured independently, the equation can disagree — and a disagreement then means
something real.

**Only a *different* search's re-find counts.** The loader compares the record's first-seen
query against the current run's, and increments only when they differ. This is what keeps the
two halves of the decision consistent: `identified` counts each distinct query once, so a
refresh's re-found records were never added and must not be subtracted. Counting them would
break equation 1 in the opposite direction, by exactly the size of the overlap.

A new table rather than a column on `runs`, for ADR 0012's reason: adding a table is the
smaller deviation from a frozen schema than altering one, and it leaves `runs`' declared shape
and committed checksum untouched. Rows are inserted in sorted `run_id` order, because
insertion order would otherwise depend on which run was walked first and the table is
checksummed like every other one.

## Alternatives rejected

### 1. One search per project — require the operator to split the two searches into two projects

Keep `_identified_count` exactly as it is, add no field, deviate from nothing, and state in
the how-to that a project models one search. Two search strings become two projects, each
with its own Layer 0, `criteria.yaml`, decision log and flow diagram. This has a genuine
methodological argument behind it: PRISMA reports identification per source, so one
identification number per project is the tidier mapping, and each project stays a
self-contained reproducible unit.

Rejected on three counts, in increasing order of severity.

It is unenforced. Nothing in `project.toml`, `capture/writer.py`, or `build_store` refuses a
second query — `prismabib search` starts a new run directory for whatever query the project
currently declares, and the loader folds it in. The rule would exist only in prose, against a
tool that does the opposite thing cheerfully and silently. A constraint that only the
documentation knows about is the kind of constraint that produces exactly the corpus above.

It is not retroactively available. Layer 0 is immutable and sealed; the operator cannot
un-run the second search, and no operation in this codebase moves a sealed run between
projects. The remedy offered to someone already holding this corpus would be to re-capture
into a second project at the cost of another weekly quota, against a Scopus index that has
since drifted — the same unimplementable "just fix Layer 0" that ADR 0012 rejected.

Most importantly, it splits one review into two. The two searches share a protocol, an
eligibility set, and a screening effort; the records overlap by 80. Under two projects those
80 papers are screened twice, in two decision logs, with no object anywhere that represents
the review's actual corpus and no place to report the overlap — each project's diagram would
claim a complete review of a fragment. The screening unit is the review, not the search
string, and the data model already agrees: one `criteria.yaml`, one `decisions.jsonl`, one
`corpus.duckdb` per project, all of which this alternative would have to duplicate to
describe one review.

### 2. Sum all runs unconditionally

`SELECT COALESCE(SUM(total_results), 0) FROM runs`. Simpler than the accepted rule by a
`GROUP BY`: no query-string comparison, no "earliest within a group" tie-break, no
assumption about what a repeated query means. It fixes the reported corpus exactly as well as
the accepted rule does, because those two runs carry two different query strings.

Rejected because it reintroduces the defect the earliest-run rule was written to prevent, and
does so silently. Refreshing citations on the 651-record search once would add 651 to
`identified` and zero records to the store: `identified` becomes 1,302 for a corpus of 651,
equation 1 fails by 651, and the only way to make it close again is to not refresh — an
instruction nobody can follow, since Layer 0 is immutable and the refresh run is sealed.

The cost is worse in the case where it does *not* fail loudly. A refresh whose overlap
happens to mask the discrepancy, or a diagram published without calling
`assert_consistent()`, yields a plausible larger identification number — "1,302 records
identified" for a search that identified 651. That is BUILD_PLAN §1.4's "plausible wrong
number in a published paper", reached by a route the project has already reasoned its way out
of once. Citation refresh is not a corner case either: it is a documented, encouraged
workflow with its own how-to and its own Stage 10 outputs.

### 3. Report `identified` per search rather than as one number

Make `identified` a `dict[str, int]` keyed on the query string, the way `excluded_fulltext`
is keyed on `reason_code`, and let the renderer draw one identification line per search.
This is the alternative with the strongest claim on the standard: PRISMA 2020's identification
box is explicitly per-source, and a breakdown is strictly more information than a total, from
which the total is recoverable by summing.

Rejected, and it deserved the most argument.

PRISMA's breakdown is per *database or register*, not per search string. Every one of these
searches hits Scopus. A diagram showing two Scopus lines invites precisely the misreading the
[Limitations](../../methodology/limitations.md) page exists to prevent — two lines look like
two databases, and this review has one. The honest per-source statement for this system is a
single Scopus line and an empty register column.

The breakdown also cannot be made to close. 80 records were identified by both searches and
loaded once; there is no non-arbitrary rule assigning them to one search or the other, so a
per-search identification column would either overstate the union or need an attribution rule
invented for the diagram — a number that depends on a convention no reader can see. The union
is the only quantity that is a fact about the review rather than about the presentation.

And the information is not lost by choosing one number. `runs` keeps one row per run with its
`run_id`, `query`, `started_at` and `total_results`; a review that must report per search
reads it straight out of Layer 1 and cites it, exactly as it must already do for
`duplicate_doi_groups`. The decision is "one number in `FlowCounts`, with the breakdown
queryable in Layer 1", not "one number instead of the breakdown".

## Consequences

### 1. Published numbers change for any multi-search project

This is the point of the change and it must be said plainly. For a project with more than one
distinct search string, `identified` rises from the first search's `total_results` to the sum
over all of them, and two new counts appear between it and `excluded_automated`. Every figure,
table or manuscript sentence carrying the old number is stale and must be regenerated, citing
the criteria version and commit as usual. No other field moves: `excluded_automated`,
`after_automated` and everything downstream are derived from Layer 1's rows and never depended
on `identified`.

### 2. A single-search project's numbers are unchanged

A sum over one distinct query is that query's `total_results` — the same integer the
earliest-run rule returned, including when the project has refreshed citations any number of
times. No committed number derived from a single-search fixture moves.

`duplicates_across_searches` is necessarily **zero** for such a project — a record can only be
a duplicate here if an earlier run under a *different* query loaded it, and there is no other
query. A single search whose paging returned the same paper twice is not counted either, and
must not be: the server's `total_results` counted that paper once, so subtracting it would
break equation 1 rather than close it.

`removed_other_reasons` can be non-zero — a capture with a malformed entry has
`removed_other_reasons = 1`, and the reference capture in ADR 0012 is exactly that. In that
case the new field makes an equation 1 that previously failed close for a stated reason. It
does not move a number that was previously right.

### 3. `FlowCounts` is now two fields further from BUILD_PLAN's literal shape

Thirteen fields where BUILD_PLAN froze nine: ADR 0007's two plus this ADR's two. The
deviations are cumulative and each is recorded. Any code, test, fixture or golden snapshot
that compares a whole `FlowCounts` instance carries all four, and a Layer 3 renderer that
draws the identification box must draw the two removal lines or it will draw a diagram that
does not add up.

### 4. Equation 1 stays a genuine cross-check, and becomes a sharper one

It still compares a server-reported total against rows actually in Layer 1, and it can still
legitimately fail — `compute_flow_counts` deliberately does not call `assert_consistent()`
itself, so the disagreement is returned rather than raised. What changes is what a failure
*means*. It previously conflated an incomplete capture, a collapsed duplicate, a skipped
entry and a multi-search project into one unexplained residual. Two of those four now have a
named field, and a third is not a failure at all any more, so a remaining failure points where
it should: the capture is incomplete (paging stopped early, or the API's result cap was hit),
or the store was built before the last search finished.

### 5. The permanent warning is gone, and the remaining warning's remedy is reachable

`prismabib flow` on a multi-search project stops printing "do not publish this diagram" on
every call with a `--rebuild` remedy that cannot help. This matters beyond ergonomics: the
warning is the project's last line of defence against publishing a diagram that does not add
up, and a warning that fires unconditionally trains its reader to ignore it.

### 6. "Records removed before screening" is now partly modelled, and the mapping says which part

`docs/methodology/prisma-mapping.md` previously listed "Records removed for other reasons" as
*not modelled* and "Duplicate records removed" as having no field. Both statements change,
and neither becomes a blanket claim: what is counted is entries that collapsed onto a
`record_id` a *differently queried* run had already loaded, and entries that could not be
loaded at all. Cross-database
deduplication, and the normalised-DOI collisions `StoreStats` reports, are still outside
`FlowCounts` and still the reviewer's own to report.

### 7. `TABLE_NAMES` grows again, and the golden checksum snapshot gains one key

`checksums._TABLE_SORT_KEYS` gains `run_duplicates: (run_id,)`, so `TABLE_NAMES`,
`table_checksums`'s default and `_reset_schema`'s reversed drop pick the table up with no
further change, exactly as `malformed_entries` did under ADR 0012. The committed
`reference_table_checksums.json` gains one key and no existing key moves: the reference
fixture is a single-search capture, so the table is empty and its digest is the SHA-256 of the
empty byte string. Stating that here is the point of §5 risk 11 — a golden file that moves
needs a reason someone checked.

The table is checksummed like every other one deliberately. A rebuild that finds a different
number of cross-search duplicates than the committed snapshot records is a change in what the
corpus contains, and must surface as a moved checksum rather than only as a different number
in a diagram.

## Constraints

- **`total_results` remains the only source of an identification number** (S02-AC5). Every
  term in the sum is read back from `runs`; no branch counts rows.
- **Distinctness is on `runs.query` verbatim.** Never normalised, case-folded, or fuzzily
  matched. Two searches differing by whitespace count as two searches, which is the
  conservative direction: a normalisation that silently merged them would delete a search
  from the identification count, and any such rule would itself need versioning to stay
  reproducible.
- **Within one search, the earliest run wins.** A refresh never changes `identified`, even
  when Scopus reports a different total months later.
- **Abstract Retrieval runs are still not rows in `runs`** ([ADR 0011](0011-abstract-retrieval-for-subject-areas.md)),
  and `AbstractRunManifest` still has no `total_results`, so enrichment cannot enter the sum.
- **The two new fields are removals, never screening decisions.** They are derived from Layer
  1, they never touch `decisions.jsonl`, and no record they describe is ever attributed a
  `reason_code`.
- **`records` is unchanged.** Duplicates are still reported, not applied; nothing is deleted
  from the store to make a count come out.
- **No existing table changes.** `run_duplicates` is an addition; not a column, type or
  primary key moves anywhere else, and a further addition to `schema.sql` needs its own ADR
  under ADR 0012's rule.
- **`run_duplicates` is derived, never authored.** Only `build_store` writes it, from `raw/`,
  like every other Layer 1 table, and its rows are inserted in sorted `run_id` order so the
  checksum does not depend on traversal accidents.
- **`duplicates_across_searches` is measured, never a remainder.** Deriving it from
  `identified - |S_raw| - removed_other_reasons` would make equation 1 unfailable, and is the
  one implementation of this field that must not be adopted.
- **Equations 2, 3 and 4 are unchanged**, as are ADR 0007's two `unsure` fields and their
  partition-remainder derivation.

## Related decisions

- **[ADR 0007](0007-flow-counts-unsure-fields.md)** (FlowCounts Records Unresolved Screening
  Decisions): the first deviation from the frozen `FlowCounts`, by the same route and for a
  structurally identical reason — a real population of records with no field to be reported
  into, and an accounting identity that therefore could not close. This ADR leaves its two
  fields and its equations 3 and 4 exactly as it left them; the two deviations are cumulative
  and independent.
- **[ADR 0012](0012-persisting-skipped-layer0-entries.md)** (A Skipped Layer 0 Entry Is a Row
  in Layer 1): supplies `removed_other_reasons`. Its `malformed_entries` table is why that
  term can be a queryable fact about the store rather than a tally only the rebuilding call
  ever saw — and its worked example, 1,945 records with one missing `dc:title`, is the same
  capture that produced the arithmetic above.
- **[ADR 0011](0011-abstract-retrieval-for-subject-areas.md)** (Subject Areas Come From a
  Separate Abstract Retrieval Run): established that a Layer 0 run which identifies nothing
  gets no `runs` row and no `total_results`, so it cannot reach this sum.

## References

- BUILD_PLAN §Stage 4 lines 976–993 (the frozen `FlowCounts` and `assert_consistent()`), §2.6
  (an ADR is required for a deviation from a frozen contract), §1.4 (a plausible wrong number
  in a published paper), §2.2 (Layer 0 immutability; Layer 1 reconstructible from Layer 0),
  S02-AC5 (`total_results` as the sole identification count), modelling note 4 (duplicates are
  reported, not applied)
- `src/prismabib/prisma/flow.py` — `FlowCounts`, `FlowCounts.assert_consistent`,
  `_identified_count` (the distinct-query sum), `_unloadable_count`,
  `_cross_run_duplicate_count`, `compute_flow_counts`
- `src/prismabib/store/load.py` — "Re-captured records" and "Citation snapshots" in the module
  docstring (why a refresh identifies nothing), the duplicate branch of `_load_run` and its
  `first_seen_query` comparison, `_insert_rows` for `run_duplicates`,
  `StoreStats.duplicate_doi_groups` / `duplicate_records` (the DOI report this ADR does *not*
  use), `StoreStats.malformed_entries_skipped`
- `src/prismabib/store/schema.sql` — the `run_duplicates` DDL, `runs`, `records`' `record_id`
  primary key, `malformed_entries`
- `src/prismabib/store/checksums.py` — `_TABLE_SORT_KEYS`
- `src/prismabib/cli.py` — `_print_flow`'s "Removed before screening" block,
  `_warn_if_inconsistent` (the warning that fired on every call)
- `tests/golden/store/__snapshots__/reference_table_checksums.json` — one key added
  (`run_duplicates`, the empty-table digest), none changed
- [PRISMA Mapping](../../methodology/prisma-mapping.md) — the box-by-box audit table, updated
  with both new fields and the revised equation 1
- [Limitations](../../methodology/limitations.md) — single register, and what deduplication
  this system does and does not perform
- [PRISMA 2020 statement](https://doi.org/10.1136/bmj.n71) — the flow diagram's "records
  removed before screening" box and its three lines

---

This ADR records that `identified` is a sum over distinct searches, that `FlowCounts` carries
the two "removed before screening" terms that make equation 1 close, and that the duplicate
term is measured during the load into the `run_duplicates` table. Returning to a single run's
`total_results`, summing all runs without grouping by query, folding the two removal terms
into one field, removing either of them from `assert_consistent()`'s equation 1, deriving
`duplicates_across_searches` as a remainder, or moving `run_duplicates` into a column on
`runs` requires a new ADR that supersedes this one (§2.6).
