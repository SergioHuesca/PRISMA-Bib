# Limitations

What prismabib cannot do today, in one place, so that a researcher deciding whether to
adopt it can decide with the facts.

This is a statement of current state, not an apology. Some entries are stages not yet
built; others are properties of the data source that will not change. Each says which it
is, and what to do instead.

Everything here describes **v1.1.0**: Layers 0 to 3 are built; the one CLI command still absent is `prismabib code` (see below).

## Coverage: one source per project, chosen by its source mode

**Current state, and deliberate.** Each project has one search source, recorded as its source
mode in `project.toml` (`[sources] mode`,
[ADR 0042](../architecture/adr/0042-source-modes-and-openalex-as-a-primary-source.md)):

- `scopus`: the Scopus Search API. The field-level cascade is refused in this mode.
- `scopus+openalex`: the same Scopus search. OpenAlex fills missing fields of Scopus records,
  and Scopus `STANDARD` and `META` may do so only with `--opt-in`.
- `openalex`: an OpenAlex works search, through `prismabib openalex-search`. No Scopus search
  runs, and no Scopus key is read.

A project with no `[sources]` table, which is every project created before the mode was recorded,
has mode `scopus+openalex`. `prismabib sources <slug> --set` changes the mode. A change is
refused when a screening decision exists, when a search run of the other kind exists, and, from
`scopus+openalex` to `scopus`, when OpenAlex enrichment or an active registration exists. Each
change is recorded in `decisions/source_modes.jsonl`.

There is no Web of Science, Crossref, PubMed or Dimensions search, and no manual-import path
for records exported from another database. In the Scopus modes the design lets other sources
fill individual *fields* of a Scopus record, never add records to the search, and a value would
never come from a second source without a recorded label. That field-level cascade is built
([ADR 0028](../architecture/adr/0028-field-level-fallback-cascade.md),
[ADR 0041](../architecture/adr/0041-field-level-provenance-and-cascade-completion.md)) and is
off until you ask for it. `prismabib cascade <slug>` fills missing fields from OpenAlex, and
from Scopus `STANDARD` and `META` only with `--opt-in` and an explicit `--scopus-budget`;
`prismabib openalex <slug>` runs the OpenAlex step alone. Every value carries its source label.
Neither command runs in mode `scopus`.

**Mode `openalex`: the records and their metadata are OpenAlex's.** Each record is a work that an
`openalex-search` query returned, and its values are the ones OpenAlex gives:

- **Abstracts** are rebuilt from OpenAlex's inverted index, so the text is the one OpenAlex
  reconstructs, not the publisher's. A work with no index has no abstract.
- **Subject areas** come from the work's primary topic only, through a fixed table that maps its
  subfield to a subject code. A work whose primary topic has no mapped subfield has no subject
  area, and its coverage is recorded as `none_assigned`.
- **Keywords** are OpenAlex topics, of the kind `openalex-topic`. They are not author keywords,
  and this mode has no Scopus index terms.
- **Authors and institutions** carry OpenAlex ids (`openalex-author:`, `openalex-inst:`), and a
  name key where OpenAlex gives no id.

**A country named by an authorship is a deliberate departure from institution-only geography.**
In the Scopus modes a record's countries come from its affiliations. In mode `openalex`, a country
that an authorship names, but none of its institutions carries, becomes an affiliation
`openalex-country:XX`, so the geography analyses count it. A code that does not map to a country is
skipped, and it is logged once per code per build. This can add a country that the work's own
institutions do not name.

**The subject criterion does real work in this mode.** A non-empty `subject_areas` list needs
subject data. In the Scopus modes that data comes only from `prismabib enrich` (below). In mode
`openalex` it comes with each work's primary topic, so the criterion can remove works without any
enrichment. A work without a subject area passes that criterion, as a Scopus record with no
subject data does.

**Works with no source, and works with no year.** A work with no source (no venue name) has no
venue type, so the conference whitelist does not apply to it, and it passes that test, as a
Scopus record with no source does. A work with no publication year fails the year test and is
counted under `year` in the automated exclusions.

**OpenAlex's counts can move, and a run whose count moves is discarded.** `identified` in this
mode is the `meta.count` that OpenAlex reports for each distinct query. A run whose count changes
while it is paged, or between one call and the next, is discarded. A discarded run stays on disk
and is never loaded, and the next call starts a new run and spends the quota again.

**The daily budget.** Checked on 2026-11-05 against OpenAlex's help page: a free OpenAlex key
carries $1 of usage a day, which prismabib takes as about 1,000 search pages, or 100,000 works at
100 a page. The allowance resets at midnight UTC. OpenAlex may change it. prismabib requires a key
for every search and refuses to start one without it, before its first request.

**Duplicates are matched by DOI as well as by id.** A work is a duplicate when its OpenAlex id or
its normalised DOI is already held, whether by an earlier query or by an earlier copy in the same
run. The second copy is not loaded, and it is counted under `duplicates_across_searches`, so it is
removed before screening. In the Scopus modes two records with one DOI are both kept and both
screened.

**A capture of unloadable works is refused at build.** A search work with no id or no title is
recorded in `malformed_entries`. The build refuses to write a store when no OpenAlex work could be
loaded from the sealed search runs, or when at least 10 works were skipped and more than 5% of the
works examined were skipped. These are the same thresholds as the Scopus guard. A malformed copy of
a work that is already loaded is a duplicate, not a skip, and a refresh of a query is not counted
again. The refusal says that no store was written and that the capture should be repeated. Below
those thresholds the unreadable works are removed before screening and counted under
`other reasons (unreadable capture entries)` in the flow. Check that line before you cite a count.

**A registration of a paper that an OpenAlex search also finds is superseded at build.** A
registration is refused when its paper is already an OpenAlex record, but that check reads the
store as it was last built, so a registration made before the next build is not refused. At
build, a registration whose DOI, or normalised title and year, an OpenAlex work holds is not
loaded. The OpenAlex work is screened in its place, and the build names the pair
(`registered_openalex_overlap`) and writes a `supersede` event to the registration log after the
store is installed. The event is written once: the superseded registration is no longer active,
so a later build writes nothing for it. Decisions logged on the registration are orphaned, as for
a Scopus supersession, and the OpenAlex work starts with no decisions
([ADR 0042](../architecture/adr/0042-source-modes-and-openalex-as-a-primary-source.md), Decision 9).

**Query syntax.** The OpenAlex query refuses characters that could change the search without an
error, such as curly quotes, full-width and half-width characters (including half-width katakana),
and zero-width joiners. See [Build an OpenAlex Query](../how-to/build-an-openalex-query.md).

Consequences for your review in the Scopus modes:

- Your search is a single-database search and must be reported as one. PRISMA's "records
  identified from databases" is a single number here, from a single register — a sum over
  however many *search strings* you ran, all of them against Scopus
  ([ADR 0013](../architecture/adr/0013-identified-sums-across-searches.md)). Several searches
  are not several sources, and the per-search breakdown lives in Layer 1's `runs` table, not
  in the diagram. Records a reviewer registers from a manual Google Scholar search
  ([ADR 0029](../architecture/adr/0029-reviewer-registration-of-a-candidate-as-a-record.md))
  are counted separately as `registered` ("other methods"), never added into `identified`;
  `identified` stays the Scopus total alone in the Scopus modes, and OpenAlex's total for the
  distinct queries in mode `openalex`
  ([ADR 0033](../architecture/adr/0033-flowcounts-registered-count-and-orphaned-decisions.md)).
- Fields Scopus refuses stay missing until you run the cascade (`prismabib cascade`, above),
  which fills them from a lower level with a source label on each value; a field no level can
  fill stays `unavailable`. Registered records take their classification from their OpenAlex work, below.
  Every exported record already carries a per-field source label for abstract, authors,
  affiliations and keywords (`exports/records.csv`, columns `<field>_source`, `_level`,
  `_outcome`, `_payload_ref`; a reviewer-entered value is labelled `manual`), and
  `exports/provenance_conflicts.csv` lists every Scopus/OpenAlex disagreement, open or resolved
  ([ADR 0041](../architecture/adr/0041-field-level-provenance-and-cascade-completion.md)).
- The "Registers" column of the PRISMA 2020 diagram is empty.

If your protocol requires multi-database coverage — most methodological guidance does —
you will need to run the other databases separately and merge outside prismabib. Which
brings up the next entry.

## Registered records: a second, manual population

**Current state, and deliberate.** A reviewer may register a paper found by a manual Google
Scholar search as a record
([ADR 0029](../architecture/adr/0029-reviewer-registration-of-a-candidate-as-a-record.md),
[ADR 0032](../architecture/adr/0032-registered-records-table-and-classification-map.md)).
These records differ from Scopus records, and a review that registers any must say so:

- **Selection risk.** The reviewer chooses which resolved candidates to register. The records
  therefore depend on what a person pasted and resolved, not only on the Scopus query.
- **OpenAlex-derived classification.** A registered record's `doc_type`, venue type and
  subject area come from its OpenAlex work through a fixed, versioned map (OpenAlex type and
  source type to the engine's values; OpenAlex subfield to ASJC code). Thirteen subfield rows
  are approximate (`qualifier` rows), and a record carrying one is flagged in the methods.
  Only the primary topic is read. A work the map does not cover is refused at registration.
- **No Scopus citation counts.** A registered record has no citation snapshot. Its citation
  count is reported as unavailable (`citations.registered_unavailable`), never imputed, and it
  is absent from every citation statistic.
- **Authors, affiliations and keywords.** A registration keeps the authors, affiliations and
  keywords of its OpenAlex work as stored values. The record view (`records_all`) and
  `exports/records.csv` label the authors and affiliations `openalex` and the keywords
  `openalex-topic` (`store/schema.sql:308-319`). They are not loaded into the author, affiliation or keyword tables
  (`store/load.py:3212-3235`), so a registered paper adds no country, keyword or co-authorship
  node to any analysis, and the screening view, which reads those tables, shows it no keywords or
  authors.
- **Language filter.** A registration's `language` is NULL and passes the language filter.
  In the two stores ADR 0032 measured, every stored Scopus record also has `language` NULL, so
  the filter excluded nothing there; that is true of those stores only. A Scopus record with a
  language set can still be excluded by it.
- **Asymmetric subject-area filter.** Most Scopus records carry no subject rows and pass the
  subject-area filter; a registered record always carries one and is filtered by it.
- **Conference whitelist.** Scopus records with no `venue_id` pass the whitelist; a
  registration is judged on its OpenAlex source name.
- **Counted in the set of records.** Registrations count in `corpus.size`, the year bounds and the
  venue tables; the methods sentence reports the registered count beside them.
- **Reproducibility needs more than Layer 0.** A set of records with registrations reproduces only
  with `registrations.jsonl`, its sidecar (both tracked in git) and the referenced Layer 0
  OpenAlex payloads, which are not committed and stay in the local archive.

## No cross-database deduplication

**Current state.** prismabib **reports** duplicates and never removes them.
`build_store` computes `StoreStats.duplicate_doi_groups` and `duplicate_records` from a
normalised-DOI collision query, and both members of a duplicate pair remain in the store as
ordinary rows. Deduplication is treated as a screening decision, not a load-time one.

Two things follow:

- **`FlowCounts.duplicates_across_searches` does not cover this case.** That field counts
  records that arrived twice under the *same* `record_id` and collapsed onto one row, which
  is what two overlapping search strings produce. A DOI collision between two *distinct*
  records is not removed, not collapsed, and not counted there. A review that removed such
  duplicates did so as a screening decision and must report that count from `StoreStats` and
  say where it came from.
- Because there is only one source, the duplicates you will see are Scopus-internal (the
  same DOI indexed twice) in the Scopus modes, and OpenAlex-internal in mode `openalex`, not
  cross-database ones. Merging Scopus records with Web of
  Science records is not something this tool can do at all.

Records that arrive twice under the *same* `record_id` — the ordinary case when a review runs
more than one search string — collapse into one row, because `records.record_id` is the
table's primary key. Where the two runs used **different queries**, that collapse is now
counted and reported as `FlowCounts.duplicates_across_searches`, on PRISMA's "duplicate
records removed" line; it used to be one of the things that could break the flow diagram's
first consistency equation with no field to explain it. A record re-found by a *refresh of the
same query* is deliberately not counted there — it was never identified twice, because
`identified` counts each distinct query once. See
[ADR 0013](../architecture/adr/0013-identified-sums-across-searches.md).

A richer `(normalised_title, first_author_surname, year)` key exists as
`prismabib.models.dedup_key` and is unit-tested, but the loader does not apply it; only the
DOI collision is reported.

## `subject_areas` requires enrichment, and enrichment must be complete {#subject-areas}

This section describes the Scopus modes. In mode `openalex` the subject areas come from each
work's primary topic, as described under Coverage.

**The Search API cannot supply this data.** The Scopus Search API's `view=COMPLETE`
response does not carry subject-area codes, so a set of records captured by `prismabib search`
alone has none. `prismabib enrich` fetches them from the **Abstract Retrieval API** — a
separate entitlement and a separate weekly quota, one call per record — and those sealed
runs load into Layer 1 ([ADR 0018](../architecture/adr/0018-abstract-runs-in-layer-1.md)).

**Enrichment must be complete, and it must precede screening.** A record that was never
looked up carries no subject areas, so it passes the filter for exactly the same reason a
record Scopus genuinely classified as nothing does. If enrichment covered only part of the
set of records — an exhausted quota, an interrupted run, a `--budget` cap — the diagram would report
one "excluded by subject area" figure computed over an unknown fraction of the review.
prismabib refuses that state rather than reporting it: `record_subject_area_coverage` records
what was asked, and the engine raises, naming how many records remain. Enrichment is
resumable and does not re-spend quota on records already fetched. `--budget` counts only calls
that reach the network: a resumed run replays its own cached responses for free, and a budget
that is not a multiple of 100 advances the run only by the whole batches it covers. Records
Scopus cannot resolve (404) or refuses (403) do not block screening — they were asked, and the
answer is recorded.

The engine's general convention is that a record with no data on a dimension is never
excluded on that dimension. Applied to a set of records where *no* record has the data, that would
silently turn the whole filter into a no-op: every record passes, the automated-exclusion
count omits a restriction you believe you applied, and the published diagram claims a filter
that never ran. The numbers look entirely plausible, which is what makes it dangerous — so
a project that declares `subject_areas` without the data to evaluate it is refused.

So a non-empty `subject_areas` against a set of records with no subject-area data **raises
`ConfigError`** rather than quietly doing nothing. Your options:

1. **Set `subject_areas: []` and apply the restriction during title/abstract screening**,
   where it becomes a logged human decision with a reason code. This is the honest route,
   and it is what the reason-code vocabulary is for.
2. **Move the restriction into the query**, so Scopus applies it server-side at search time
   with a `SUBJAREA(...)` clause. Note two costs: it narrows what is *identified*, so those
   records never appear in your automated-exclusion count; and the `[query]` table in
   `project.toml` can only render `FIELD("term")` clauses, so a bare `SUBJAREA(...)` cannot
   be expressed there today — you would have to pass the full query string to
   `capture_search(project, query=...)` yourself, which puts your real search outside the
   file git versions. Weigh that against option 1.

The schema supports subject areas, and the `subject_areas` table loads normally if a
captured entry ever carries a `subject-area` array. It is the API response that is missing
them, not the model.

## Full-text retrieval exists, and its coverage is structurally biased by publisher

**Current state.** `prismabib fulltext` runs a three-step resolver chain, first hit wins,
for every record in `M_abs` (the set advanced to full-text screening):

1. **ScienceDirect Article Retrieval** — entitled Elsevier content only, fetched as
   structured XML.
2. **Unpaywall** — a DOI-keyed lookup for a legitimate open-access copy, wherever one
   happens to be hosted (an institutional repository, a preprint server, the publisher's
   own site under an OA licence).
3. **A manual drop** — `projects/<slug>/fulltext/manual/<record_id>.pdf`, with `:` replaced
   by `_` in the filename (so `scopus:2-s2.0-85100000201` is dropped as
   `scopus_2-s2.0-85100000201.pdf`; a colon cannot appear in a Windows filename), for
   whatever a
   reviewer's own institutional access can obtain outside prismabib.

Every attempt, hit or miss, is sealed into a Layer 0 run under
`projects/<slug>/fulltext/runs/` — `prismabib fulltext` is a **capture**, exactly like
`prismabib search`/`prismabib enrich`, so a resolution run costs no Elsevier or Unpaywall
quota twice and is never lost by deleting and rebuilding `corpus.duckdb` (ADR 0019
Decision 0). Running `prismabib build <slug> --rebuild` afterward folds that run into Layer
1's `fulltext_assets` table — resolver name and a three-valued `entitled` flag
(`true`/`false`/`NULL`; see
[ADR 0019](../architecture/adr/0019-fulltext-resolution-and-coverage.md)) — and extracts
section text into `fulltext_sections`. `prismabib.fulltext.coverage` renders that table into
a coverage-by-resolver and a coverage-by-publisher report, which `prismabib export` writes
alongside every other table.

**Quota and resume.** A `QuotaExceededError` from any resolver stops the run: `prismabib
fulltext` exits `1` with the error and its message, and the run stays unsealed with its
progress saved. The next `prismabib fulltext` call resumes it, and records already attempted
are not re-paid for. Any other error fails only the record it occurred on. A download over
100 MiB is refused as an `UpstreamError`, which the resolvers treat as a failed candidate.

**State the mechanism plainly, because it determines whose records you actually read.**
ScienceDirect answers for Elsevier journals only — Pattern Recognition, Neurocomputing,
Knowledge-Based Systems, and similar Elsevier venues are, in principle, retrievable by
resolver 1 alone. IEEE, Springer, MDPI, and CVF/AAAI proceedings are not: resolver 1 can
never succeed for them, and they depend entirely on resolver 2 (whether an OA copy happens
to exist) or resolver 3 (a reviewer's own access). The three resolvers therefore do not
sample the underlying set of records uniformly by publisher, and **the set of records with resolved
full text is not a representative subset of `M_abs`** — it is systematically weighted
toward Elsevier, and secondarily toward however open-access-friendly each remaining
publisher happens to be. Any statistic computed only over resolved full text (a
methods-section content analysis, a per-venue eligibility rate derived by reading the PDF)
inherits that skew silently unless it is checked against the coverage report first.

Two hard rules exist specifically to keep that skew from becoming invisible bias rather
than a stated, measured one:

- **A refusal (HTTP 403) is recorded as an entitlement gap, `entitled=false`, and the chain
  moves on** — it is never conflated with "this paper does not exist" (which is
  `entitled=NULL`, e.g. a genuine HTTP 404 or no OA location found). Collapsing the two
  would make an institution's subscription gaps look like a property of the literature.

  The gap is attributed to a **publisher**, not merely to a resolver. ScienceDirect serves
  only Elsevier's content, so an unentitled Elsevier key refuses every DOI it is handed,
  including papers ScienceDirect never held. Counting an IEEE paper's ScienceDirect 403 as
  an entitlement gap would state "we were refused this IEEE paper" when IEEE was never
  asked, so a refusal counts against a publisher only when the refusing resolver could have
  served that publisher; otherwise it is recorded `NULL`. Where the publisher cannot be
  identified from the DOI at all, the refusal is `NULL` too — under-reporting a gap is an
  omission, over-reporting one is a false statement.
- **No record is ever marked `INACCESSIBLE` automatically.** Exhausting the resolver chain
  is a fact about this run, not a verdict; only a human, during full-text screening and
  after confirming no institutional route exists, may log that decision
  (enforced by a static check over the whole codebase, not merely by convention).

**Figures for your project.** Run `prismabib fulltext <slug>`, then
`prismabib build <slug> --rebuild` to fold the run into the store, then `prismabib export
<slug>` and read `tables/fulltext_coverage_by_resolver.{csv,md,tex}` and
`tables/fulltext_coverage_by_publisher.{csv,md,tex}` — no numbers are asserted here, since
they depend entirely on the publisher mix of your records and your institution's entitlements.
Report them alongside any full-text-derived finding, the same way a database's own coverage
is reported under "No cross-database deduplication" above.

## Screening interface

**Built** (Stage 5): a Panel view, `prismabib.screening.ui.screener()`, for use in a
notebook (it needs the `viz` extra). Decisions can also be recorded by calling
`DecisionLog.append` in Python — see
[Getting Started, the Scopus path](../getting-started.md#record-your-first-screening-decision).

The guarantees around a decision are already real: append-only, `fsync`ed per write,
`flock`-serialised, checksum-guarded against hand-editing, folded by
`(stage, record_id, reviewer)` so a reversal is a new event and no reviewer overwrites
another. What is still missing is inter-reviewer agreement statistics (κ); the view already has a
keyboard-first queue and progress tracking. Screening several thousand titles through a
Python API is possible and unpleasant.

Multi-reviewer adjudication is already implemented conservatively (any `exclude` wins, then
any `unsure`, `include` only if unanimous among those who logged a decision), but nothing
*enforces* double screening; that is a workflow concern the UI will own.

## A venue count is a count of venue *name strings*

`venues.total`, and the grouping behind the top-venues table, count **distinct normalised
venue names**. Normalisation is deliberately conservative — casefold, collapse whitespace,
strip a leading `The`, strip a trailing parenthetical *only when it is purely a year*, unify
`&` and `and` — because a rule that merges on similarity can invent a venue that published
papers it did not, which is worse than reporting one venue twice.

The consequence, measured on the reference set of records: **a recurring conference appears once per
edition.**

```
Proceedings 2017 IEEE Winter Conference on Applications of Computer Vision Workshops WACVW 2017
Proceedings 2020 IEEE Winter Conference on Applications of Computer Vision Workshops WACVW 2020
Proceedings 2024 IEEE Winter Conference on Applications of Computer Vision Workshops WACVW 2024
```

Three names, one workshop. Fifteen such groups span 21 of the 769 names in that set of records, and no
normalisation rule fires on a single one of them — the difference is a *year*, not
formatting. So `venues.total = 769` should be read as "769 distinct venue name strings", not
"769 venues". Folding editions would give 746, a 3% change that leaves the top-ten table
byte-identical.

**Do not silently re-fold this yourself before quoting the number.** Whether WACV 2020 and
WACV 2024 are one venue is a methodological choice: for "which venues does this literature
appear in?" they are, and for "where was this specific paper published?" they are not. State
which you mean. A declared normalisation mode, with the unit of count named in the figure
caption, is the planned resolution; until it exists, the honest citation is the name-string
count with this paragraph beside it.

The same caution applies to venue **type**: where Scopus indexes one venue under more than
one `prism:aggregationType`, the type is reported as `mixed` rather than resolved to one.

## Taxonomy, dashboard and export layer

**Bibliometrics now exist** (Stage 7): annual counts and CAGR, geographic counting with a
declared `full`/`fractional` method, venue and venue-type analysis, citation statistics
including h-index, keyword frequency and evolution, and keyword co-occurrence and
co-authorship networks with VOSviewer export. Every one returns an `AnalysisResult` carrying
the parameters that produced it and a generated caption, so a figure cannot drift from its
own number.

Still absent from the CLI (Layer 3):

- the taxonomy coder command, `prismabib code`. The coder and its rule files exist in
  `src/prismabib/taxonomy/`.

`prismabib code` is **absent from the CLI**, not stubbed. An absent command fails with "No
such command", which is honest; a stub that accepts its arguments and does nothing is
indistinguishable from a working one in a shell script or a methods section.

The full bibliometric analyses are library-only for now: no CLI command emits them, so they are
reached from a notebook through `prismabib.bibliometrics` over a `Corpus` handle. `prismabib export`
writes only selected scalars to `numbers.json` (flow counts, record and citation summary
statistics, and the top venues by record count), not those analyses.
Numbers you derive by querying Layer 1 yourself, rather than through those functions, carry
no provenance and no caption — and are your responsibility, not the store's.

## Data-source properties that will not change

These are Scopus's, not prismabib's, and they will still be true when every stage above is
built.

- **`COMPLETE` entitlement is required.** It is granted to subscribing institutions, not to
  personal or free API keys, and the search itself never silently drops to `STANDARD` (which
  omits author keywords, the full author block, and abstracts). Without the entitlement the
  search is refused. `prismabib cascade` fills the missing fields afterwards, from OpenAlex
  and, with `--opt-in`, from Scopus `STANDARD` and `META`; nothing fills them silently.
- **Index drift.** Scopus revises records retroactively. Re-running the same query months
  later can return different results. Layer 0 manifests are the mitigation: the query, the
  view, the result count and a payload hash are sealed at capture time, so a past claim
  stays provable and a change is visible as a hash difference rather than a silent one.
- **A weekly quota** bounds how much you can capture. Captures are resumable and never
  re-fetch a page Layer 0 already holds.
- **Index keywords are absent.** `view=COMPLETE` carries author keywords only; Scopus's
  indexed terms live in the Abstract Retrieval API, which is not called. `record_keywords`
  therefore never contains a row of kind `"index"`.
- **The language field is author- and publisher-supplied**, and is matched exactly against
  Scopus's own string. A record with no language recorded is kept rather than excluded, so a
  language restriction under-excludes rather than over-excludes.
- **Author identity is Scopus's `authid`**, used as-is. There is no disambiguation across
  name variants or across the same person's multiple Scopus profiles. An entry that carries
  only `dc:creator` (a display-name string, no id) produces **no** `authors` or
  `record_authors` row at all — the loader never invents an id — so authorship counts
  under-report for those entries.
- **Citation counts are point-in-time and lag.** They are stored as
  `citation_snapshots(record_id, retrieved_at)` with `retrieved_at` taken from the capturing
  run's start time, never the load-time clock. Counts for recent publications are
  systematically incomplete.
- **Affiliation countries are free text.** They normalise to ISO 3166-1 alpha-3 through a
  checked-in table; an unmapped string is kept verbatim, logged, and listed by
  `prismabib build`, never dropped — so a geographic total still equals the record count.

## What is not a limitation

Stated so that the list above is not read as a general disclaimer. These hold today, are
tested, and are the reason the tool exists:

- Every number in every output is derived at query time from Layer 1 and the decision log.
  No count is stored, cached, or typed.
- Layer 1 is reconstructible from Layer 0 by one function, deterministically: identical
  Layer 0 input yields identical per-table checksums, independent of the DuckDB version and
  of the machine's timezone.
- Layer 0 run directories are immutable once sealed, enforced in code.
- The decision log cannot be edited without detection, and an interrupted append is
  diagnosed distinctly from tampering.
- A human decision can never widen an automated set: `A` and `L` are pure functions of
  `criteria.yaml` and Layer 1, and the event schema makes a decision against those stages
  unrepresentable.

## Related pages

- [Getting Started](../getting-started.md) — the access requirements, stated before you
  invest time
- [PRISMA Mapping](prisma-mapping.md) — the box-by-box audit table, including
  [the boxes this system does not produce](prisma-mapping.md#boxes-this-system-does-not-produce)
- [Run a New Review](../how-to/run-a-new-review.md) — the workflow as it exists
- [Architecture Overview](../architecture/overview.md) — the four-layer model
