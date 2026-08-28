# ADR 0012: A Skipped Layer 0 Entry Is a Row in Layer 1, Not an In-Memory Tally

## Status

Accepted — 2026-08-28. **This is a deviation from the frozen BUILD_PLAN schema** (§Stage 3,
lines 847–879), which `src/prismabib/store/schema.sql` reproduces character-for-character
and whose own header forbids adding, renaming, or retyping anything in it without amending
the spec first. §2.6 requires an ADR; this is it. It adds one table, `malformed_entries`,
and changes no existing table, column, type, count, or checksum.

## Context

`build_store` used to abort the entire load when any Layer 0 entry lacked `dc:title` or
`prism:coverDate` — fields Scopus always sends. A real capture returned 1,945 records with
exactly one missing `dc:title`, which made the other 1,944 unloadable. There was no way
forward: Layer 0 is immutable (§2.2), so the bad line cannot be edited, and re-capturing
means querying a Scopus index that has drifted, producing a different corpus at the cost of
another weekly quota.

Skipping that one entry and continuing is right. Skipping it *quietly* is the failure
BUILD_PLAN §1.4 names — "a plausible wrong number in a published paper" — because a corpus
that is short by a record and reports itself complete is exactly that, and it is
undetectable downstream: every count, table and figure is internally consistent, just
computed over fewer records than were captured.

So the reporting is not a nicety attached to the skip. It is the entire justification for
skipping rather than aborting, and it has to survive every path an operator uses.

It did not. The skipped references were accumulated in memory during the load and returned
in `StoreStats.malformed_entries_skipped`, with no column behind them:

```text
build_store(p, rebuild=True)  ->  records_loaded=119, malformed_entries_skipped=('<run>/page-0000.jsonl:2',)
build_store(p)                ->  records_loaded=119, malformed_entries_skipped=()
```

The second is what `prismabib build <slug>` runs — the default, with no `--rebuild`. And
`()` does not read as "this call did not load anything so it cannot say"; it reads as
"nothing was skipped". The store on disk was built with a skip and every later reader of it
was told otherwise.

`StoreStats`'s own class docstring had already stated the invariant this broke: every field
is "a row count in the freshly (re)built Layer 1 store … never an in-memory tally kept
alongside the load, so a caller reading `StoreStats` and a caller running the same query
against `connect` always agree." The new field was the only exception, and the exception is
what made the default path lie.

## Decision

**Persist one row per unloadable Layer 0 entry in a new Layer 1 table,
`malformed_entries`, and derive `StoreStats.malformed_entries_skipped` from it by query
like every other field.**

```sql
CREATE TABLE malformed_entries (
  run_id TEXT, payload_file TEXT, payload_line INTEGER, record_id TEXT, reason TEXT,
  PRIMARY KEY (payload_file, payload_line)
);
```

A skipped entry is a fact *derived from Layer 0* — the same derivation, from the same
bytes, by the same function — so by §2.2 it belongs in Layer 1, alongside the records that
did load. It is reconstructible: `build_store(rebuild=True)` on the same `raw/` produces
the same rows, which is precisely the property that makes it safe to put there.

### A new table, not a wider `runs`

`runs` was the cheaper-looking option: one `malformed_count` column, or a JSON list, on a
table that already exists per run.

A new table is the *smaller* deviation from the frozen spec, and this is the load-bearing
argument for it. Every existing table keeps its exact declared shape, so
`test_schema__sql_file__matches_live_duckdb_introspection`'s transcription of the
BUILD_PLAN schema is unchanged line for line, `runs`' checksum is unchanged, and any reader
holding the BUILD_PLAN next to `schema.sql` finds the spec's tables exactly as specified
plus one clearly-marked addition. Widening `runs` would edit a row shape the spec fixes and
that S02-AC5 depends on, and would move a committed golden checksum — the one artefact §5
risk 11 says must never move without a stated reason.

It is also the wrong grain. A count on `runs` answers "how many", and the operator's next
question is always *which line*: Layer 0 is immutable, so the only remedy is to go and read
the entry, and the reference has to survive to make that possible. A JSON list in a column
would be a table hiding in a string — unqueryable, unjoinable, and unsortable, therefore
also not stably checksummable.

### `reason` is a closed vocabulary, never the exception message

Two values today: `missing_eid` (no `eid`, so no record id to key on) and `invalid_field`
(`dc:title` or `prism:coverDate` absent or unparseable).

The `ValidationError` message would be more informative and is *not* stored, because it
embeds the entry's **absolute path** (`Scopus entry at /home/…/page-0000.jsonl:2 is missing
'dc:title'`). An absolute path inside a checksummed table makes S03-AC1's byte-stable
checksums depend on where the repository is checked out: green on every local run, red the
moment Stage 11's criterion — a clean clone on a *different machine* reproducing
`numbers.json` — is actually exercised. That class of defect has already been found three
times in this project. The full message, path and all, goes to the log, where machine
dependence is harmless.

`payload_file` uses the same run-relative `"<run_id>/<page>"` form as `records.payload_file`
for the same reason.

### `missing_eid` is in the same table

An entry with no `eid` had its own warning and sat outside the reporting entirely: deleting
an `eid` gave `records_loaded=119, malformed_entries_skipped=()` — a record dropped, and
the field saying none were. It is the adjacent branch of the same loop with the same
consequence for the corpus, and it is now reported through the same channel, distinguished
by `reason`.

### Entries, not records

`malformed_entries` counts *entries*; `records_loaded` counts *distinct records*. These
differ, and the difference is not a rounding error: a re-capture of a paper an earlier run
already loaded can be skipped here while the record stays in the store. One entry named,
zero records lost. The field's docstring and the CLI line both say so, because a reader who
subtracts one from the other gets a wrong number in the direction that looks like
diligence.

### A skipped entry's citation snapshot is kept when its record exists

The count is present, parseable, and independent of the field that failed. Two sealed runs,
same record — January well-formed with `citedby-count` 5, February missing `dc:title` with
99 — used to yield a citation trend reading "5 as of January, nothing since" for a record
that is in the store and whose February count was captured and then thrown away with the
title.

It is kept only when some run did load the record, and the decision is deferred to the end
of the load rather than made where the entry is skipped, because runs are walked in sorted
`run_id` order and the malformed capture may sort first. The condition is not decorative:
`schema.sql` declares **no foreign keys at all**, so a `citation_snapshots` row for a record
no run loaded would be rejected by neither DuckDB nor any existing test — it would simply
vanish from every join while still counting towards `citation_snapshots_loaded`. A test
asserts zero orphans in both directions.

### Past a threshold, refuse rather than report

Skipping is right for *an* entry and wrong for a capture. With `dc:title` stripped from all
120 reference entries, `build_store` returned normally with `records_loaded=0` and
`prismabib build --rebuild` exited `0` printing `records 0` and a next-step hint.
`_guard_against_unloadable_capture` now raises `StoreError` when every entry was skipped, or
when more than 5% were and at least 10 were.

5% is two orders of magnitude above the only rate ever observed in a real capture (1 in
1,945, 0.05%), so a healthy capture does not approach it, while a wrong parser, a truncated
download, or a capture of the wrong response shape fails on a large fraction rather than a
handful. The floor of 10 skips exists because a bare ratio would refuse a 15-entry pilot
capture with one bad record — the very case skipping was introduced to survive.

A refused build leaves **no** store. `connect` creates the file before anything is loaded,
and a store that exists is a store the next `prismabib build` (without `--rebuild`) reuses
and reports as a clean load, which is how a broken capture would become a plausible wrong
number by a second route.

### The line between skip and abort is `prismabib.errors.ValidationError`

The `except` catches prismabib's `ValidationError`, deliberately not pydantic's. The only
two raisers of the former are `_title_from_entry` and `_cover_date_from_entry`: hand-written
checks that Scopus sent a field Scopus always sends. That is a defect in the captured bytes,
confined to one entry.

A pydantic failure constructing `Record` is a different thing. Every value handed to
`Record(...)` has already been read, coerced, and defaulted by the `_*_from_entry` helpers,
so pydantic rejecting one means prismabib built a record its own model forbids — a loader or
model defect, not a bad entry, and not confined to one entry. Skipping those would silently
shrink every corpus the defect touched. Skip what Layer 0 got wrong; abort on what prismabib
got wrong. The line is now named in a comment at the `except` rather than left as an
accident of which validator happens to fire first.

## Alternatives rejected

### 1. Keep the in-memory tally and document that it only covers the rebuild path

Change nothing in the schema; state in `StoreStats.malformed_entries_skipped`'s docstring
that the field is populated only when `rebuilt` is `True`, and that on the reuse path an
empty tuple means "not computed". No ADR, no new table, no golden-snapshot change, and the
existing field keeps working on the path where it does work.

Rejected because it makes correctness depend on a reader noticing a caveat, for a field
whose entire purpose is to be noticed. `rebuilt` and `malformed_entries_skipped` are two
fields on the same object; nothing forces a reader to consult the first before believing the
second, and `()` is not a null — a caller writing `if stats.malformed_entries_skipped:` gets
"clean" with no indication anything was withheld. `prismabib build <slug>` takes that path by
default, so the caveat would apply to the most common invocation.

It also leaves the store itself amnesiac. The skipped references would exist only in the
return value of the process that happened to do the loading, and in a log line. Six months
later the person checking a published number has the repository, `raw/`, and
`corpus.duckdb` — and no way to ask "did anything fail to load?" without a full rebuild.
Layer 0 is immutable precisely so that question stays answerable; answering it only in
transient memory is the opposite arrangement.

Finally, it would keep `StoreStats`'s stated invariant false. The class promises that a
caller reading it and a caller querying the store always agree, and that promise is worth
more than one field's convenience — it is what lets every other number on the object be
trusted without checking.

### 2. Write the skipped entries to a sidecar file next to the store

`store/malformed.jsonl`, or a key in a `store/build-report.json`, written by `build_store`
alongside `corpus.duckdb`. It survives the process, it is human-readable without a SQL
client, it can hold the full error message, and it needs no schema change at all — so no
deviation, no ADR, and no golden-snapshot churn.

Rejected, and it is the alternative that most deserved consideration, because the "no
schema change" saving is smaller than it looks and the costs are real. It creates a second
artefact that must be kept in step with the store by convention alone: `build_store` writes
both, but `_delete_stale_store` deletes only one, a rebuild that fails partway leaves a
stale sidecar describing a store that no longer exists, and copying a project directory
without it silently loses the information. Layer 1 exists so that "the store" is one file
whose deletion and rebuild loses nothing (S03-AC3); a companion file that must not be
deleted is a hole in that.

It is also outside the checksum. `table_checksums` covers the store's tables, and a golden
snapshot pins them; a sidecar would be the one build output whose content could change
without any committed checksum moving. "The same Layer 0 now skips a different set of
entries" is a change to what the corpus contains and must be visible where every other such
change is visible.

And it would not be queryable next to the data. "Which of these records came from a run
that also had failures?" is a join in the table version and a hand-rolled file parse in the
sidecar version.

### 3. Abort the load, as before, and require the operator to fix Layer 0

The strictest option, and the status quo before this branch: any entry that cannot be parsed
fails the whole build, loudly, with the payload reference in the message. Nothing is ever
silently skipped because nothing is ever skipped. No new table, no threshold, no ratio to
justify, and no risk of a short corpus at all.

Rejected because it is unimplementable against an immutable Layer 0. There is no legitimate
"fix": editing the captured JSONL destroys the provenance §2.2 exists to guarantee and
invalidates `payload_sha256`, and re-capturing returns a different corpus from a drifted
index at the cost of another weekly quota. The operator's only real options were to falsify
the archive or throw away 1,944 good records — which is what actually happened, and is a
worse outcome for reproducibility than a store that loads 1,944 records and says so.

It also misidentifies whose defect it is. One malformed entry is Scopus's, and the tool's
job is to record it accurately, not to refuse to work until an upstream vendor's historical
response changes. The guard preserves what was genuinely right about this option — a
capture that is broken *as a capture* still aborts — while declining to punish a corpus for
one bad line.

## Consequences

### 1. The golden snapshot gains a table checksum and nothing else moves

`reference_table_checksums.json` gains exactly one key, `malformed_entries`, whose value is
the SHA-256 of the empty byte string (the reference fixture skips nothing, so the table is
empty — the same digest `subject_areas` already carries). Every existing checksum and every
existing row count is byte-identical. That is stated here and in the PR because §5 risk 11's
whole concern is a golden file that moves without a reason anyone checked.

### 2. `TABLE_NAMES` grows, and so does everything derived from it

`_TABLE_SORT_KEYS` gains `malformed_entries: (payload_file, payload_line)`, so `TABLE_NAMES`,
`table_checksums`'s default, and `_reset_schema`'s reversed drop all pick the table up with
no further change. The table is checksummed like every other one on purpose: a rebuild that
skips a different set of entries than the committed snapshot records is a change to what the
corpus contains, and must surface as a moved checksum rather than only in a log.

### 3. `build_store` can now raise `StoreError` where it used to return

Callers that treated a return as "the store is built" are unaffected — that is still true —
but a caller that treated *no exception* as "Layer 0 was fine" now gets an exception on a
broken capture. `prismabib build` exits 1 with the message on stderr instead of exiting 0
with `records 0`. The documented `Raises: ValidationError` clause on `build_store` is gone
because that behaviour is gone; it was still in the docstring, and in
`docs/architecture/data-model.md`, after the code stopped doing it.

### 4. Two log events changed shape

`store.load.malformed_entry_skipped` now carries `reason` (the persisted code) as well as
`detail` (the full message). The end-of-build `store.load.malformed_entries_skipped` summary
carries at most 20 references plus `truncated`, and `store.build_store.complete` carries
`malformed_entries_skipped_count` in place of the tuple. At 1,945 skips the previous shape
was 1,946 lines, the last one enormous.

### 5. A skipped entry's citation snapshot may now be kept

`citation_snapshots_loaded` can therefore exceed what the previous implementation would have
produced for the same Layer 0 — only in the specific case of a re-captured record whose
later entry is malformed. No fixture in the repository contains one, so no committed number
moves; a real corpus with a re-capture may see one more snapshot row than it would have, and
that row is a real observation that was previously discarded.

## Constraints

- **No existing table changes.** Not a column, not a type, not a primary key. A further
  addition to `schema.sql` needs its own ADR under the same rule.
- **Nothing machine-dependent is ever persisted.** No absolute path, no locale-dependent
  text, no wall clock. `reason` stays a closed vocabulary; extending it means adding a code,
  never storing a message.
- **`malformed_entries` is derived, never authored.** Only `build_store` writes it, from
  `raw/`, like every other Layer 1 table.
- **The guard's thresholds are constants with a stated basis** (`_MAX_SKIPPED_ENTRY_RATIO`,
  `_MIN_SKIPS_FOR_RATIO_GUARD`). Changing either changes which captures are refused and
  belongs in a PR that says so.
- **A refused build leaves no store**, never a half-loaded one.

## Related decisions

- **ADR 0001** (DuckDB as Analytical Store): Layer 1 is rebuilt from Layer 0 by one
  function — the property that makes a persisted skip reconstructible rather than authored
- **ADR 0011** (Abstract Retrieval for Subject Areas): the precedent for adding to Layer 0's
  and Layer 1's shape without editing what the BUILD_PLAN froze

## References

- BUILD_PLAN §1.4 (a plausible wrong number in a published paper), §2.2 (Layer 0
  immutability; Layer 1 reconstructible from Layer 0), §2.6 (ADR required for a deviation),
  §Stage 3 lines 847–879 (the frozen schema) and line 927 (golden checksums are recomputed
  only when semantics deliberately change, and the PR must say which), S03-AC1 (byte-stable
  table checksums), S03-AC3 (deleting the store and rebuilding loses nothing), §5 risk 11
  (never regenerate a golden snapshot to make a test pass)
- `src/prismabib/store/schema.sql` — the `malformed_entries` DDL and why `reason` is a code
- `src/prismabib/store/load.py` — `_load_run`'s two skip branches and the comment naming the
  skip/abort line, `_resolve_pending_snapshots`, `_guard_against_unloadable_capture`,
  `_stats_from_connection`
- `src/prismabib/store/checksums.py` — `_TABLE_SORT_KEYS`
- `tests/integration/store/test_load.py` — the reuse-path test, both pinned warnings, the
  guard's two rules and its permissive cases, the orphan-snapshot assertions, and the test
  that no absolute path reaches the table
- `tests/golden/store/__snapshots__/reference_table_checksums.json` — one key added, none
  changed

---

This ADR records that an unloadable Layer 0 entry is persisted in Layer 1. Removing the
`malformed_entries` table, moving its contents to a sidecar file or a column on `runs`,
storing the exception message in it, or dropping the unloadable-capture guard requires a new
ADR that supersedes this one (§2.6).
