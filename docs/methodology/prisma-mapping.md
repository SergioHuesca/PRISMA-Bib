# PRISMA Mapping

This page maps the PRISMA 2020 statement onto the code that implements it. Its purpose is
narrow and practical: **given a published flow diagram produced by this system, a reader
must be able to find the exact field and the exact function that produced every number in
it**, and re-derive that number themselves from the same project directory.

Everything described here lives in Layer 2 (`src/prismabib/prisma/`, built in Stage 4).
Layer 3 renders the diagram; it invents no numbers of its own.

## How to audit a published flow diagram

1. Note the criteria version and the commit the manuscript cites.
2. Check out that commit, rebuild Layer 1 from Layer 0, and call
   `prismabib.prisma.flow.compute_flow_counts(project)`.
3. Compare the returned `FlowCounts` field by field against the diagram, using the
   [box-by-box table](#the-prisma-2020-flow-diagram-box-by-box) below.
4. Call `FlowCounts.assert_consistent()`. It raises `ValidationError` naming the first
   equation that does not close — see [the four equations](#the-four-consistency-equations).

Every count is recomputed on every call. Nothing in `flow.py` or `engine.py` caches,
memoises, or persists a count, so step 2 is a re-derivation and not a lookup of a stored
answer.

## The formal sets

The methodology defines the corpus as a chain of set transformations. Each one is a single
function in `src/prismabib/prisma/engine.py`, and no other module in the codebase computes
record membership.

| Set | Definition | Function | Nature |
| --- | --- | --- | --- |
| `S_raw` | Every record captured from the query | `engine.raw_set(project)` | Every `record_id` in Layer 1's `records` table |
| `A` | `S_raw` filtered by year ∧ subject area ∧ document type | `engine.automated_set(project)` | **Deterministic** — a pure function of `criteria.yaml` and Layer 1 |
| `L` | `A` further filtered by language | `engine.language_set(project)` | **Deterministic**, same sense |
| `M_abs` | Records in `L` whose aggregated `title_abstract` decision is `include` | `engine.manual_abstract_set(project)` | Derived by folding the decision log |
| `M_full` (`M_ft`) | Records in `M_abs` whose aggregated `fulltext` decision is `include` | `engine.manual_fulltext_set(project)` | Derived by folding the decision log |
| `C` | The final corpus, `C = M_full` | `engine.corpus(project)` | Alias of `M_full`; a distinct name, not a distinct computation |

The same six sets are named by the `PrismaStage` enum in `src/prismabib/stage.py`
(`RAW`, `AUTOMATED`, `LANGUAGE`, `TITLE_ABSTRACT`, `FULLTEXT`, `INCLUDED`), which is what
`Corpus.records()` and `Corpus.keywords()` take as their `stage` parameter, so a Layer 3
query and a Layer 2 count always mean the same set by the same name.

### Predicates that define `A` and `L`

`A` and `L` are computed together by one private helper, `engine._compute_a_and_l`, from a
single Layer 1 read. A record is in `A` when all four predicates hold, and in `L` when the
language predicate holds as well:

| Predicate | `criteria.yaml` key | Function |
| --- | --- | --- |
| Publication year within the window, inclusive | `temporal.year_start`, `temporal.year_end` | `engine._passes_temporal` |
| Subject-area codes intersect | `subject_areas` | `engine._passes_subject_areas` |
| Document type matches | `doc_types.include` | `engine._doc_type_matches` |
| Conference venue is whitelisted | `doc_types.conference_whitelist` | `engine._passes_conference_whitelist` |
| Language matches | `languages` | `engine._passes_language` |

Because `L` is built by filtering `A` rather than by filtering `S_raw` independently,
`L ⊆ A` holds by construction and not by coincidence.

### Four filter conventions that change published numbers

These are judgement calls the implementation had to make where `criteria.yaml`'s schema is
silent. They are documented at each function, and repeated here because an auditor
comparing two reviews' numbers needs to know them:

- **An empty list means "no restriction on that dimension", not "match nothing".** A
  freshly initialised `criteria.yaml` has every list empty; under the opposite reading
  `automated_set()` would silently return the empty set before an operator had edited
  anything.
- **A record with no Layer 1 data on a dimension is never excluded on that dimension.**
  A `NULL` language, or a record with no `subject_areas` rows, passes. This matters
  concretely: `store/load.py` documents that the Scopus Search API `view=COMPLETE` responses
  this codebase captures carry no subject-area codes at all, so the strict reading would let
  a data-source limitation masquerade as a screening decision and empty the corpus.

    The corollary is bounded, though: applied to a corpus where *no* record carries the
    data, the same convention would turn the filter into a silent no-op — every record
    passing a restriction the diagram claims was applied. So
    `engine._refuse_unenforceable_subject_filter` raises `ConfigError` when `subject_areas`
    is non-empty and not one record in the corpus has subject-area data, which is every
    corpus captured from the Scopus Search API. See
    [Limitations](limitations.md#subject_areas-is-declared-but-not-enforceable).
- **Document types match on either the Scopus code or its description.** `records.doc_type`
  is populated with the description form (`"Conference Paper"`) whenever the captured entry
  carries one, while `criteria.yaml` is written in code form (`ar`, `cp`). The closed
  lookup table `engine._DOC_TYPE_CODE_TO_DESCRIPTION` bridges the two; matching is
  case-insensitive.
- **The conference whitelist is a case-insensitive substring test against the venue name**,
  applied only to records whose Layer 1 `venues.venue_type` is `"conference"`. Venue names
  routinely embed an acronym inside a longer title (`"Proceedings of CVPR 2024"`), so an
  exact match would essentially never fire, and a journal article is never excluded by a
  conference whitelist.

## The invariant chain

```
C ⊆ M_abs ⊆ (S_raw ∩ A ∩ L) ⊆ S_raw
```

Read left to right: the corpus is a subset of what survived title/abstract screening, which
is a subset of what the deterministic filters admitted, which is a subset of everything
captured. Since `L ⊆ A ⊆ S_raw` by construction, `S_raw ∩ A ∩ L` is just `L`; the
intersection is written out because it is the form the property test asserts, and writing it
that way keeps the claim true even if the filters were ever reordered.

Two structural facts make the chain hold for *any* event stream rather than for the streams
that happen to have been tried:

- `manual_abstract_set` filters `language_set`'s result, and `manual_fulltext_set` filters
  `manual_abstract_set`'s result. Membership is only ever narrowed, never widened, at each
  step. A decision logged for a record outside `L` cannot put that record into `M_abs`,
  because the fold is applied to the members of `L` and to nothing else.
- `corpus()` returns `manual_fulltext_set()` unchanged, so `C ⊆ M_abs` is not an
  independent claim.

`unsure` never resolves to inclusion at any stage: `manual_abstract_set` and
`manual_fulltext_set` admit only an aggregated `"include"`, so a record whose current
aggregated decision is `"unsure"` — or that has no decision logged yet — cannot appear in
`C`. BUILD_PLAN requires this to be asserted by Hypothesis property tests over generated
event streams containing `unsure` at any position, not by examples alone.

## Why `A` and `L` are computed, never logged

`A` and `L` are pure functions of `criteria.yaml` plus Layer 1. Neither reads the decision
log; `engine.py`'s `automated_set` and `language_set` call `_compute_a_and_l`, which touches
only the DuckDB store and a `Criteria` object, and recomputes from scratch on every call.
There is no cache, no memoisation, and no persisted copy.

The prohibition is enforced structurally, not by convention. A decision event's `stage`
field is validated against `events._LOGGABLE_STAGES`, which contains only
`PrismaStage.TITLE_ABSTRACT` and `PrismaStage.FULLTEXT`. An attempt to log a decision
against `raw`, `automated`, `language`, or `included` is rejected at construction time. It
is therefore not possible, through the public API or by hand-writing a well-formed
`decisions.jsonl` line, to record a human decision that widens or narrows `A` or `L`.

Three consequences matter for reproducibility:

1. **One source of truth per fact.** If the automated filter's membership were logged, the
   log and the criteria file could disagree, and nothing would say which one the published
   number came from. Because it is derived, the question cannot arise.
2. **Criteria amendments are free.** Widen the year range and `A`, `L`, and every count
   downstream of them recompute on the next call. No backfill, no migration, no re-screening
   of records whose logged human decisions are still applicable.
3. **The automated stage cannot drift with the reviewer.** Two people running
   `compute_flow_counts` on the same commit of the same project get identical
   `excluded_automated` and `excluded_language`, whatever either has screened, because those
   numbers do not depend on the log at all.

The corollary is that `A` and `L` are only as reproducible as `criteria.yaml` and Layer 0
are. That is the intended trade: both are versioned artefacts under git, and Layer 1 is
rebuildable from Layer 0 by one function.

## The PRISMA 2020 flow diagram, box by box

Every number in the diagram is one field of the frozen dataclass
`prismabib.prisma.flow.FlowCounts`, populated by `flow.compute_flow_counts(project)`.
"Producer" names the function that actually derives the number.

| PRISMA 2020 box | `FlowCounts` field | Producer | Derivation |
| --- | --- | --- | --- |
| Records identified from databases | `identified` | `flow._identified_count` | The sum, over the project's **distinct searches**, of each search's `total_results` — one term per distinct `runs.query`, taken from that query's earliest run (`(query, run_id) IN (SELECT query, MIN(run_id) FROM runs GROUP BY query)`), copied verbatim from that run's `RunManifest.total_results`. Never a row count. A run that re-queries the *same* search string to refresh citation counts joins an existing term and adds nothing. See [ADR 0013](../architecture/adr/0013-identified-sums-across-searches.md) |
| Records removed before screening — duplicate records removed | `duplicates_across_searches` | `flow._cross_run_duplicate_count` | `SUM(duplicates)` over Layer 1's `run_duplicates` table, which `build_store` writes during the load: captured entries that resolved to a `record_id` an earlier run **under a different query** had already loaded — identified by two searches, stored once, because `records.record_id` is a primary key. **Not** the normalised-DOI report — see [below](#reading-the-two-removed-before-screening-counts) |
| Records removed before screening — records removed for other reasons | `removed_other_reasons` | `flow._unloadable_count` | `COUNT(*)` over Layer 1's `malformed_entries` table — the Layer 0 entries `build_store` could not turn into a record ([ADR 0012](../architecture/adr/0012-persisting-skipped-layer0-entries.md)). Counted as *entries*, not records |
| Records removed before screening — marked ineligible by automation tools (year / subject / document type) | `excluded_automated` | `engine.raw_set`, `engine.automated_set` | `|S_raw| - |A|` |
| — intermediate, not a diagram box | `after_automated` | `engine.automated_set` | `|A|` |
| Records removed before screening — marked ineligible by automation tools (language) | `excluded_language` | `engine.automated_set`, `engine.language_set` | `|A| - |L|` |
| Records screened | `after_language` | `engine.language_set` | `|L|` — the unique records reaching title/abstract screening |
| Records excluded (title/abstract screening) | `excluded_title_abstract` | `engine._aggregate_record_decisions` at `PrismaStage.TITLE_ABSTRACT` | Records in `L` whose aggregated decision is `"exclude"` |
| *No PRISMA box* — screened but unresolved | `unsure_title_abstract` | `flow.compute_flow_counts` (partition remainder) | `after_language - excluded_title_abstract - retrieved_fulltext`: aggregated `"unsure"`, or nothing logged yet. See [ADR 0007](../architecture/adr/0007-flow-counts-unsure-fields.md) |
| Reports sought for retrieval / Reports assessed for eligibility | `retrieved_fulltext` | `engine.manual_abstract_set` | `|M_abs|` |
| Reports excluded, with reasons | `excluded_fulltext` (`dict[str, int]`) | `engine._aggregate_record_decisions` at `PrismaStage.FULLTEXT` | Records in `M_abs` aggregated to `"exclude"`, grouped by `reason_code` |
| *No PRISMA box* — assessed but unresolved | `unsure_fulltext` | `flow.compute_flow_counts` (partition remainder) | `retrieved_fulltext - sum(excluded_fulltext.values()) - included` |
| Studies included in review | `included` | `engine.corpus` | `|C| = |M_full|` |

### Reading the two removed-before-screening counts

`duplicates_across_searches` and `removed_other_reasons` are the bridge between what Scopus
said it had (`identified`, a server-reported total) and what Layer 1 actually holds
(`|S_raw|`). They are kept apart, on PRISMA's own two lines, because they mean opposite
things about a review:

- **A duplicate across searches is expected.** Two search strings aimed at one register
  overlap; a strategy where they did not would be the surprising one. The number describes
  the search strategy, not a fault.
- **A record removed for another reason is a defect in the capture.** It is a record Scopus
  counted, that the review spent quota on, and that no reviewer will ever screen because the
  entry could not be parsed. A non-zero value here should be read, cited, and — per
  [ADR 0012](../architecture/adr/0012-persisting-skipped-layer0-entries.md) — traced to the
  exact payload line through the `malformed_entries` table.

Three things `duplicates_across_searches` is **not**:

- It is not `StoreStats.duplicate_doi_groups` / `duplicate_records`. Those count *distinct*
  records that share a normalised DOI. Both of those records are in `records` and both are
  screened; nothing is removed. See [Limitations](limitations.md#no-cross-database-deduplication).
- It is not a deduplication step this system performs. The only collapse it describes is the
  one `records`' primary key already performed at load time. Dedup is still
  [reported, not applied](../architecture/data-model.md).
- It is not a count of every re-captured record. A record re-found by a **refresh of the same
  query** is not a duplicate here: `identified` counts each distinct query once, so that
  record was never added twice and must not be subtracted. Only a record an earlier run under
  a *different* query had already loaded is counted, which is why a single-search project's
  `duplicates_across_searches` is always zero.

Neither count is a remainder. `removed_other_reasons` is a row count over `malformed_entries`
and `duplicates_across_searches` is a sum over `run_duplicates`, both written by the loader
from Layer 0. Deriving either as `identified - |S_raw| - the other` would make equation 1 an
identity that cannot fail, which would defeat the only check that compares a run's manifest
against the corpus that run produced. `run_duplicates` also *cannot* be recomputed after the
load: `records.run_id` keeps only the first run that loaded a record, so nothing in Layer 1
afterwards knows how many searches found it.

`identified` sums one `total_results` per **distinct query string**, so a citation-refresh run
— the same query re-run later — contributes nothing to it, while a genuinely different search
string contributes its own total. `runs` keeps one row per run with its `query`, `started_at`
and `total_results`, so a review that must report identification per search reads the
breakdown straight out of Layer 1 and cites it.

### Reading `excluded_fulltext`

The keys are the `reason_code` values that appear in the decision log, which `log.py`
validates on append against `criteria.yaml`'s `manual_fulltext.exclude_reason_codes`. They
are the "Reason 1 / Reason 2 / Reason 3" slots of the PRISMA diagram, and there may be more
than three.

One key is not a criteria code: `"UNKNOWN"` (`flow._UNKNOWN_REASON_CODE`). It buckets an
aggregated exclusion whose attributed event carries no `reason_code`. `DecisionLog.append`
makes that impossible to create through the API, but a `decisions.jsonl` that conforms to
the event schema without having been written through `DecisionLog` could still reach the
counter. **A published diagram containing an `UNKNOWN` bucket should be treated as a
finding, not as a category** — it means some decision entered the log by a path that did not
enforce the reason-code rule.

Where two reviewers excluded the same record for different reasons, the reported
`reason_code` is the one from the exclude event with the greatest `(ts, event_id)` — the
most recently logged exclusion, using the same tie-break the fold itself uses. See
[ADR 0008](../architecture/adr/0008-multi-reviewer-adjudication.md).

### Boxes this system does not produce

Stated explicitly, because a mapping document that quietly omits a box invites a reader to
assume it is covered:

- **"Duplicate records removed" — only partly.** `duplicates_across_searches` counts records
  that arrived twice under the *same* `record_id` and collapsed onto one row, which is what
  happens where two search strings overlap. It does **not** cover duplicates that are two
  distinct records: Layer 1 *reports* those rather than removing them
  (`StoreStats.duplicate_doi_groups` and `duplicate_records` in `store/load.py`), and both
  members of a duplicate-DOI pair are retained as ordinary rows and both are screened. A
  review that removed such duplicates did so as a screening decision and must report that
  count from `StoreStats`, and say so.
- **"Records removed for other reasons" — narrowly.** `removed_other_reasons` counts exactly
  one thing: Layer 0 entries the loader could not turn into a record. There is no other
  pre-screening removal path — no manual removal, no out-of-scope purge, nothing an operator
  can add to it — so a review that removed records for any other reason did it outside
  prismabib and must report that in prose.
- **"Reports not retrieved."** Not modelled in Stage 4. `retrieved_fulltext` is `|M_abs|` —
  the records *advanced* to full-text screening — so it stands for both "sought for
  retrieval" and "assessed for eligibility", and the difference between those two boxes is
  zero by construction. Full-text acquisition and its failure modes are Stage 7
  (`fulltext/resolver.py`); until then, a review that failed to obtain some reports cannot
  express that in `FlowCounts` and must report it in prose.
- **"Registers."** This system searches Scopus and ScienceDirect only. The register column
  of the PRISMA diagram is empty.

## The four consistency equations

`FlowCounts.assert_consistent()` checks four accounting identities in order and raises
`prismabib.errors.ValidationError` on the first that fails, naming the equation verbatim,
both sides, and the signed difference:

```
1.  identified - duplicates_across_searches - removed_other_reasons
                      - excluded_automated  == after_automated
2.  after_automated   - excluded_language   == after_language
3.  after_language    == excluded_title_abstract + unsure_title_abstract + retrieved_fulltext
4.  retrieved_fulltext == sum(excluded_fulltext.values()) + unsure_fulltext + included
```

Equations 2, 3, and 4 hold by construction for anything `compute_flow_counts` returns:
`unsure_title_abstract` and `unsure_fulltext` are each computed as the remainder of their
partition rather than measured independently. They are not therefore pointless — they are
what catches a hand-assembled, mutated, or deserialised `FlowCounts` whose fields have
drifted.

**Equation 1 is a genuine cross-check and can legitimately fail.** `identified` is the sum of
the servers' own reported totals across the project's distinct searches; `after_automated`
descends from the records actually loaded into Layer 1. Everything between them is now named:
`duplicates_across_searches` and `removed_other_reasons` account for the two ways a record can
be identified and not be a row in `records`, and `excluded_automated` accounts for the
automated filter. `compute_flow_counts` deliberately does **not** call `assert_consistent()`
itself, so a disagreement is returned to a caller to inspect rather than raised from inside a
function whose job is to compute, not to judge. BUILD_PLAN requires CI to call
`assert_consistent()` on every project fixture; a reviewer publishing a diagram should call it
too, and investigate rather than paper over a failure of equation 1.

Note what equation 1 means arithmetically: `excluded_automated` is computed as
`|S_raw| - |A|`, so equation 1 closes exactly when
`identified - duplicates_across_searches - removed_other_reasons == |S_raw|`. If a review
reports a difference between "records identified" and "records loaded", that difference is
not an automation exclusion and must not be presented as one.

### When equation 1 does not close

This is the list to work through when a diagram will not add up. It is not closed — a cause
outside it means something the pipeline does not model, which is itself a finding — but every
cause observed so far is here:

| Symptom | Cause | What to do |
| --- | --- | --- |
| `identified` far exceeds `|S_raw|`, by roughly a page multiple | **The capture is incomplete.** Paging stopped early, or the API's result cap was hit, so Layer 0 holds fewer entries than the server said existed | Compare `manifest.pages_fetched` and the run's entry count against `total_results`. Re-running `prismabib search` resumes an unsealed run; a *sealed* short run is a fact about the capture and must be reported as one |
| `identified` exceeds `|S_raw|` by a small amount, and the store was built a while ago | **The store predates the last search.** `prismabib build` without `--rebuild` reuses the existing store and loads no run captured since | `uv run prismabib build <slug> --rebuild` |
| A stubborn residual on a project with more than one search string | **A search string that is not distinct.** `identified` sums one term per distinct `runs.query`, verbatim; two runs whose queries differ only in whitespace are two searches to this rule and one search to a reader | `SELECT run_id, query, total_results FROM runs ORDER BY run_id` and read the query strings side by side. Do not normalise them in the code — see the Constraints in [ADR 0013](../architecture/adr/0013-identified-sums-across-searches.md) |
| `identified` exceeds `|S_raw|` by exactly the number of entries the build reported skipping | **Entries the loader could not read**, not reflected in the counts | Check `removed_other_reasons` against `SELECT COUNT(*) FROM malformed_entries`; if they disagree, the store was not rebuilt after the entries were skipped |
| A multi-search project whose `duplicates_across_searches` is `0` | **The store predates this accounting.** `run_duplicates` is written *during* the load, so a store built by an earlier version — or reused without `--rebuild` — has no rows in it, and the overlap between the searches goes unsubtracted | `uv run prismabib build <slug> --rebuild`. The count cannot be recovered any other way: `records.run_id` keeps only the first run that loaded a record |

Two causes are **no longer** on this list, and a diagram from before this change may still
show them:

- **A second search string was ignored.** `identified` used to be the earliest run's
  `total_results` alone, so a project that ran a second search reported only the first one's
  total and failed equation 1 by everything the second search identified. It now sums across
  distinct searches ([ADR 0013](../architecture/adr/0013-identified-sums-across-searches.md)).
- **A collapsed duplicate or a skipped entry had nowhere to be reported.** Both now have a
  field.

## What moves a record between boxes

- **The fold key is `(stage, record_id, reviewer)`**, not `record_id` alone
  (`log.FoldKey`). A full-text decision never overwrites a title/abstract one, and one
  reviewer never overwrites another.
- **Later events supersede earlier ones** for the same key. `log.fold_events` keeps the
  event with the greatest `(ts, event_id)`; the ULID `event_id` is monotonic, so a tie
  inside one millisecond still resolves in append order. Permuting the file's lines yields
  the same membership — the fold does not depend on file order.
- **A reversal is a new event, never an edit.** `log.py` exposes no delete or rewrite path.
  The original decision remains readable in the log after being superseded.
- **Reviewers who disagree are adjudicated conservatively**: any `exclude` wins; otherwise
  any `unsure` wins; otherwise `include` only if every reviewer who logged a decision at that
  stage said so ([ADR 0008](../architecture/adr/0008-multi-reviewer-adjudication.md)). A
  reviewer who has not screened the record yet does not block it — enforcing double screening
  is a Stage 5 workflow concern, not an engine one.
- **The log is checksum-guarded.** `decisions.jsonl.sha256` is rewritten on every append,
  and a mismatch on load raises `LogError` — hand-editing a decision to change a published
  number is detected rather than silently honoured.

## Criteria amendments

`engine.replay(project, criteria_version=...)` recomputes `A` and `L` under a different
`criteria.yaml` version and reports, without writing anything:

| `ReplayResult` field | Meaning |
| --- | --- |
| `criteria_version` | The resolved criteria's `version` |
| `automated`, `language` | `A` and `L` recomputed under that criteria |
| `decisions_still_valid` | Records with an existing `title_abstract` decision that remain inside `L` |
| `newly_requires_screening` | Records inside `L` with no existing `title_abstract` decision |
| `no_longer_in_scope` | Records with an existing decision that now fall outside `L` — their events stay in the log untouched |

Historical versions are resolved from `criteria.yaml`'s **git history**
(`criteria.resolve_criteria`); there is no per-version archive directory. A version that was
never committed cannot be replayed, and `ConfigError` names it rather than silently falling
back to the current file. Every decision event carries the `criteria_version` in force when
it was made, which is what makes "which decisions were taken under 1.0.0?" a query rather
than an archaeology exercise.

## PRISMA 2020 checklist items this layer supports

This page covers the flow diagram and the selection process. It is not a complete checklist
mapping — most items are reporting obligations on the author, not artefacts a library can
produce.

| Item | Topic | Where it comes from |
| --- | --- | --- |
| 5 | Eligibility criteria | `criteria.yaml`, rendered by the predicates above; versioned in git |
| 8 | Selection process (how many reviewers, independence, tooling) | The decision log's `reviewer` field and [ADR 0008](../architecture/adr/0008-multi-reviewer-adjudication.md) |
| 16a | Study selection results, ideally as a flow diagram | `FlowCounts`, box by box, as tabulated above |
| 16b | Studies excluded at full text, with reasons | `FlowCounts.excluded_fulltext`, keyed by `reason_code` |

Inter-reviewer agreement statistics (κ), which item 8 invites where two reviewers screened
independently, are Stage 5 work and are not part of `FlowCounts`.

## Related pages

- [Architecture Overview](../architecture/overview.md) — the four-layer model
- [ADR 0002: Append-only decision log](../architecture/adr/0002-append-only-decision-log.md)
- [ADR 0007: FlowCounts unsure fields](../architecture/adr/0007-flow-counts-unsure-fields.md)
- [ADR 0008: Multi-reviewer adjudication](../architecture/adr/0008-multi-reviewer-adjudication.md)
- [ADR 0013: `identified` sums across distinct searches](../architecture/adr/0013-identified-sums-across-searches.md)
- [Amend Eligibility Criteria](../how-to/amend-eligibility-criteria.md) — the replay workflow
- [Testing](../testing.md) — why this module is mutation-tested

BUILD_PLAN §Stage 4 (lines 935–1059) is the frozen specification these functions implement.
