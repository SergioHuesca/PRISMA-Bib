# ADR 0018: Abstract Runs Load Into Layer 1, With Coverage Recorded Per Record

## Status

Accepted — 2026-09-01. **This is the third addition to the frozen Layer 1 schema**
(BUILD_PLAN §Stage 3, lines 847–879), after
[ADR 0012](0012-persisting-skipped-layer0-entries.md)'s `malformed_entries` and
[ADR 0013](0013-identified-sums-across-searches.md)'s `run_duplicates`. Two new tables,
`abstract_runs` and `record_subject_area_coverage`. No existing table, column, type or
primary key changes, and `subject_areas(record_id, area_code)` is untouched.

It completes [ADR 0011](0011-abstract-retrieval-for-subject-areas.md), whose work was
deliberately split into a Layer 0 PR and a Layer 1 PR; only the first shipped.
[ADR 0017](0017-subject-areas-match-by-asjc-grouping.md) fixed the comparison this data will
be fed into.

## Context

`prismabib enrich` has sealed Abstract Retrieval payloads into `raw/abstracts/<run_id>/`
since v0.8.0. Nothing reads them. `store/load.py` excludes that directory by name
(`capture.layout.NON_RUN_DIRNAMES`) and its module docstring says so, so `subject_areas`
loads zero rows and `engine._refuse_unenforceable_subject_filter` refuses any project that
declares a subject-area restriction.

ADR 0011 states the position in its own Consequence 1:

> No loader change, no engine change, no schema change, no fixture regeneration, no golden
> update. `subject_areas` still loads zero rows; the engine still refuses a declared subject
> filter with the same message.

That was correct and deliberate — the PR adding HTTP code changed no counts, so if a number
moved, exactly one change was a candidate. The consequence is that the feature has been
half-present for four releases: a researcher can spend a weekly Abstract Retrieval quota,
roughly one call per record, and observe nothing change.

### Why `subject_areas(record_id, area_code)` alone is not enough

The existing table can say *a record has these areas*. It cannot distinguish three states
that a filter which **removes records** must tell apart:

| state | what it means | what the filter should do |
| --- | --- | --- |
| Scopus assigned areas | we asked, we know them | filter on them |
| Scopus assigned none | we asked; this record genuinely has none | keep — no evidence to exclude on |
| we never asked | not enriched, or enrichment skipped it | keep, but the corpus is incomplete |

The middle and last rows are both "no rows in `subject_areas`", and they are different
claims about a published review. "We restricted to computer science and this paper carries
no subject classification at all" is a defensible inclusion; "we restricted to computer
science and never looked this paper up" is an incomplete method. A reviewer must be able to
tell which, and today's schema cannot answer.

Layer 0 already knows: `AbstractRunManifest.unavailable` records every record that will
contribute no codes, with an `AbstractUnavailableReason` of `not_found`, `not_entitled` or
`no_subject_areas`, and its docstrings are explicit that a `no_subject_areas` payload line
*is* written "so that a later reader knows the empty set was observed rather than assumed".
That distinction must survive the load into Layer 1, or §2.2's reconstructibility rule buys
nothing here: Layer 1 would be reconstructible but less informative than what it was
reconstructed from.

## Decision

**`raw/abstracts/` loads into Layer 1, and per-record coverage is recorded explicitly.**

```sql
CREATE TABLE abstract_runs (
  run_id TEXT PRIMARY KEY,
  started_at TIMESTAMP, finished_at TIMESTAMP,
  endpoint TEXT, view TEXT,
  records_requested INTEGER, records_fetched INTEGER,
  payload_sha256 TEXT, client_version TEXT, criteria_version TEXT
);

CREATE TABLE record_subject_area_coverage (
  record_id TEXT, run_id TEXT, status TEXT,
  PRIMARY KEY (record_id, run_id)
);
```

`status` is `assigned`, `none_assigned`, `not_found` or `not_entitled` — Layer 0's three
unavailability reasons plus the successful case. **A record with no row at all is the third
state: never asked.** Absence carries meaning here, which is unusual enough to be worth
stating: the alternative, a row per record per run for the whole corpus, multiplies the
table by the corpus size to record that nothing happened.

`abstract_runs` mirrors `runs` for provenance — an exported number that depends on subject
areas must be resolvable to the capture that produced them, and `payload_sha256` is the
citable proof of what Scopus returned on a date.

### `runs` gains no row

An abstract run identifies nothing. `runs` is the only sanctioned source of PRISMA "records
identified" (S02-AC5), and a row there would inflate `identified` for records that were
already counted by the search that found them. This is asserted by a test, not left to
care.

### The later run wins

Where two sealed runs both carry areas for one record, the later `run_id` supplies its
`subject_areas` rows — a re-enrichment observes Scopus as it is now, and a corpus whose
areas came from two dates is not one filter. Coverage rows are kept per run, since the PK
is `(record_id, run_id)`: the history of what was asked and when stays legible even though
only the newest answer is filtered on.

### No number moves in this repository

`tests/fixtures/projects/reference/criteria.yaml` declares `subject_areas: []` and the
reference fixture has no abstract runs, so every existing golden value is unchanged;
`reference_table_checksums.json` gains two empty-table digests and alters nothing.

This is worth stating because it is the opposite of what a schema addition that finally
switches on a filter would normally imply, and because §5 risk 11 makes a moved golden the
most dangerous thing this change could do. It is checked, not assumed: if any existing
golden value moves, the cause is a defect in the loader, not a fixture that needs
regenerating.

## Alternatives rejected

### 1. Load subject areas, skip the coverage table

Read the payloads into `subject_areas` and stop. Smaller, no new provenance table, and the
filter works.

*Rejected:* it discards the distinction the feature exists to support. "No rows" would mean
both "Scopus assigns none" and "never enriched", so a partially-enriched corpus filters as
if it were fully enriched — every un-enriched record kept by the "no data, so passes"
branch, silently, with the diagram reporting a subject-area exclusion that ran over an
unknown fraction of the corpus. That is BUILD_PLAN §1.4 with extra steps, and Layer 0
already holds the information needed to prevent it.

### 2. Add `enriched_at` / `coverage_status` columns to `records`

Put the per-record state on the record row instead of in a new table.

*Rejected:* `records` is BUILD_PLAN's own frozen table, and the previous two schema
additions both took the form of a *new* table precisely to leave the original ones alone.
A record can also be covered by more than one run, which a column on `records` cannot
represent without losing the earlier observation.

### 3. Read the payloads at screening time rather than loading them

Have the engine consult `raw/abstracts/` directly when `criteria.subject_areas` is
non-empty.

*Rejected:* it breaks the four-layer architecture's central rule — Layer 1 is the only
thing the engine reads, and it is reconstructible from Layer 0 by one function. It would
also re-parse hundreds of megabytes on every screening call, and put JSON-shape knowledge
into `prisma/`, where §3.7.6's strictest coverage and mutation gates apply to logic that has
nothing to do with PRISMA arithmetic.

## Consequences

### 1. `subject_areas` becomes usable for the first time

The order is **enrich → rebuild → amend `criteria.yaml`**;
`_refuse_unenforceable_subject_filter` raises if criteria declare subject areas before the
data exists. Enrichment must also precede screening, since it changes which records reach
the queue.

### 2. A previously enriched project needs no re-enrichment

The sealed payloads are read as they stand. Quota already spent is not spent again — which
matters, because this ADR is what makes that spend worth anything.

### 3. Partial coverage is visible rather than assumed

`record_subject_area_coverage` lets a reviewer ask "how much of the corpus did the
subject-area filter actually see?" — a question that could not previously be asked and whose
answer belongs in a limitations section.

### 4. `identified` is unaffected

Guaranteed by the "`runs` gains no row" rule and asserted by a test.

## Constraints

- `runs` never gains a row from an abstract run.
- Traversal is sorted by `run_id`, then `payload_files` in manifest order, then line order —
  the same discipline `store/load.py` already applies to search runs, because Layer 1 must
  rebuild byte-identically on another machine.
- A record present in an abstract run but absent from `records` is skipped and **counted**,
  never silently dropped (§5 risk 8's discipline).
- An unsealed abstract run is ignored entirely. A partial load is worse than none.
- The four `status` values are exactly Layer 0's three `AbstractUnavailableReason` values
  plus `assigned`. Adding a fifth means Layer 0 gained a reason, and both must change
  together.

## Related decisions

- [ADR 0011](0011-abstract-retrieval-for-subject-areas.md) — the Layer 0 half, and why
  abstract runs are nested rather than siblings
- [ADR 0017](0017-subject-areas-match-by-asjc-grouping.md) — the comparison this data feeds
- [ADR 0012](0012-persisting-skipped-layer0-entries.md) — the rule that a `schema.sql`
  change needs its own ADR
- [ADR 0013](0013-identified-sums-across-searches.md) — `identified`, and why `runs` is the
  only source for it
- [ADR 0016](0016-automated-exclusion-reasons.md) — how the resulting exclusion is reported

## References

- `src/prismabib/store/schema.sql` — `abstract_runs`, `record_subject_area_coverage`
- `src/prismabib/store/load.py` — the abstract-run traversal
- `src/prismabib/store/checksums.py` — `_TABLE_SORT_KEYS`
- `src/prismabib/capture/manifest.py` — `AbstractRunManifest`, `AbstractUnavailable`,
  `AbstractUnavailableReason`
- `src/prismabib/capture/layout.py` — `ABSTRACTS_DIRNAME`, `NON_RUN_DIRNAMES`, `is_sealed`
- [Issue #31](https://github.com/SergioHuesca/PRISMA-Bib/issues/31)

---

This ADR records that sealed abstract runs load into Layer 1, that per-record coverage is
recorded in `record_subject_area_coverage` with absence meaning "never asked", that
`abstract_runs` carries the capture's provenance, and that `runs` gains no row. Loading
subject areas without the coverage table, putting coverage on `records`, reading Layer 0 at
screening time, or letting an abstract run into `runs` requires a new ADR that supersedes
this one (§2.6).
