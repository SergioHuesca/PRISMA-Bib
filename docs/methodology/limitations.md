# Limitations

What prismabib cannot do today, in one place, so that a researcher deciding whether to
adopt it can decide with the facts.

This is a statement of current state, not an apology. Some entries are stages not yet
built; others are properties of the data source that will not change. Each says which it
is, and what to do instead.

Everything here describes **v0.5.0**: Layers 0, 1 and 2 are built; Layer 3 is not.

## Coverage: Scopus only

**Current state, and deliberate.** The only implemented source is the Scopus Search API.
There is no Web of Science, no OpenAlex, no Crossref, no PubMed, no Dimensions, and no
manual-import path for records exported from another database.

Consequences for your review:

- Your search is a single-database search and must be reported as one. PRISMA's "records
  identified from databases" is a single number here, from a single register — a sum over
  however many *search strings* you ran, all of them against Scopus
  ([ADR 0013](../architecture/adr/0013-identified-sums-across-searches.md)). Several searches
  are not several sources, and the per-search breakdown lives in Layer 1's `runs` table, not
  in the diagram.
- Fields Scopus does not index are invisible to the whole pipeline. There is no second
  source to fill a gap with.
- The "Registers" column of the PRISMA 2020 diagram is empty.

If your protocol requires multi-database coverage — most methodological guidance does —
you will need to run the other databases separately and merge outside prismabib. Which
brings up the next entry.

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
  same DOI indexed twice), not cross-database ones. Merging a Scopus corpus with a Web of
  Science corpus is not something this tool can do at all.

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

## `subject_areas` requires enrichment, and enrichment must be complete

**The Search API cannot supply this data.** The Scopus Search API's `view=COMPLETE`
response does not carry subject-area codes, so a corpus captured by `prismabib search`
alone has none. `prismabib enrich` fetches them from the **Abstract Retrieval API** — a
separate entitlement and a separate weekly quota, one call per record — and those sealed
runs load into Layer 1 ([ADR 0018](../architecture/adr/0018-abstract-runs-in-layer-1.md)).

**Enrichment must be complete, and it must precede screening.** A record that was never
looked up carries no subject areas, so it passes the filter for exactly the same reason a
record Scopus genuinely classified as nothing does. If enrichment covered only part of the
corpus — an exhausted quota, an interrupted run, a `--budget` cap — the diagram would report
one "excluded by subject area" figure computed over an unknown fraction of the review.
prismabib refuses that state rather than reporting it: `record_subject_area_coverage` records
what was asked, and the engine raises, naming how many records remain. Enrichment is
resumable and does not re-spend quota on records already fetched. Records Scopus cannot
resolve (404) or refuses (403) do not block screening — they were asked, and the answer is
recorded.

The engine's general convention is that a record with no data on a dimension is never
excluded on that dimension. Applied to a corpus where *no* record has the data, that would
silently turn the whole filter into a no-op: every record passes, the automated-exclusion
count omits a restriction you believe you applied, and the published diagram claims a filter
that never ran. The numbers look entirely plausible, which is what makes it dangerous — so
a project that declares `subject_areas` without the data to evaluate it is refused.

So a non-empty `subject_areas` against a corpus with no subject-area data **raises
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

**State the mechanism plainly, because it determines whose corpus you actually read.**
ScienceDirect answers for Elsevier journals only — Pattern Recognition, Neurocomputing,
Knowledge-Based Systems, and similar Elsevier venues are, in principle, retrievable by
resolver 1 alone. IEEE, Springer, MDPI, and CVF/AAAI proceedings are not: resolver 1 can
never succeed for them, and they depend entirely on resolver 2 (whether an OA copy happens
to exist) or resolver 3 (a reviewer's own access). The three resolvers therefore do not
sample the underlying corpus uniformly by publisher, and **the set of records with resolved
full text is not a representative subset of `M_abs`** — it is systematically weighted
toward Elsevier, and secondarily toward however open-access-friendly each remaining
publisher happens to be. Any statistic computed only over resolved full text (a
methods-section content analysis, a per-venue eligibility rate derived by reading the PDF)
inherits that skew silently unless it is checked against the coverage report first.

Two hard rules exist specifically to keep that skew from becoming invisible bias rather
than a stated, measured one:

- **A ScienceDirect refusal (HTTP 403) is recorded as an entitlement gap, `entitled=false`,
  and the chain moves on** — it is never conflated with "this paper does not exist" (which
  is `entitled=NULL`, e.g. a genuine HTTP 404 or no OA location found). Collapsing the two
  would make an institution's subscription gaps look like a property of the literature.
- **No record is ever marked `INACCESSIBLE` automatically.** Exhausting all three resolvers
  is a fact about this run, not a verdict; only a human, during full-text screening and
  after confirming no institutional route exists, may log that decision
  (enforced by a static check over the whole codebase, not merely by convention).

**Figures for your project.** Run `prismabib fulltext <slug>`, then
`prismabib build <slug> --rebuild` to fold the run into the store, then `prismabib export
<slug>` and read `tables/fulltext_coverage_by_resolver.{csv,md,tex}` and
`tables/fulltext_coverage_by_publisher.{csv,md,tex}` — no numbers are asserted here, since
they depend entirely on your corpus's publisher mix and your institution's entitlements.
Report them alongside any full-text-derived finding, the same way a database's own coverage
is reported under "No cross-database deduplication" above.

## No screening interface

**Not built yet** (Stage 5). Screening decisions are recorded by calling
`DecisionLog.append` in Python — see
[Getting Started, step 8](../getting-started.md#8-record-your-first-screening-decision).

The guarantees around a decision are already real: append-only, `fsync`ed per write,
`flock`-serialised, checksum-guarded against hand-editing, folded by
`(stage, record_id, reviewer)` so a reversal is a new event and no reviewer overwrites
another. What is missing is the ergonomics — a keyboard-first queue, progress tracking, and
inter-reviewer agreement statistics (κ). Screening several thousand titles through a Python
API is possible and unpleasant.

Multi-reviewer adjudication is already implemented conservatively (any `exclude` wins, then
any `unsure`, `include` only if unanimous among those who logged a decision), but nothing
*enforces* double screening; that is a workflow concern the UI will own.

## No analysis layer

**Not built yet** (Layer 3, Stages 6–10). None of this exists:

- bibliometrics — growth curves, citation percentiles, h-index, geographic and venue
  analysis, keyword co-occurrence and co-authorship networks;
- the taxonomy coder (`prismabib code`) and its versioned rule files;
- figures, LaTeX tables, and the SVG PRISMA flow diagram;
- the Panel dashboard;
- exports (`prismabib export`) — CSV/Parquet/JSON with provenance metadata.

`prismabib code` and `prismabib export` are **absent from the CLI**, not stubbed. An absent
command fails with "No such command", which is honest; a stub that accepts its arguments and
does nothing is indistinguishable from a working one in a shell script or a methods section.

What you *can* do today is query Layer 1 directly through `Corpus` (`records`, `keywords`,
`citations`), which returns polars DataFrames for any `PrismaStage`, and compute your own
analysis from there. Numbers derived that way are your responsibility, not the store's.

## Data-source properties that will not change

These are Scopus's, not prismabib's, and they will still be true when every stage above is
built.

- **`COMPLETE` entitlement is required.** It is granted to subscribing institutions, not to
  personal or free API keys, and prismabib refuses to degrade to `STANDARD` (which omits
  author keywords, the full author block, and abstracts). Without the entitlement, this tool
  cannot run your review.
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
