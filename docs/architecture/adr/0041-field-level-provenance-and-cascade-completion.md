# ADR 0041: Field-Level Provenance, Manual Entry, and the Cascade Command

## Status

Accepted — 2026-11-02, on the merge of the pull request that adds this record, which the owner approves. Until the merge, this record is `Proposed` on the branch `docs/adr-0041-cascade-provenance`. Accepting it changes no code, test or golden: it fixes the design that the implementation pull requests build.

ADRs 0001 to 0040 are frozen and are not edited by this record. The implementing pull requests append dated pointer notes to
ADR 0028 and ADR 0031 (Decision 10). BUILD_PLAN.md is not edited.

## Context

ADR 0028 (Accepted) names a four-level enrichment cascade for fields that Scopus refuses, and asks for per-field provenance,
a manual level, and per-level counts. A read-only audit of `main` (09d56e2) found that the cascade is not connected end to end:

- **Provenance does not exist.** `Record` (`src/prismabib/models.py`) carries only `source_payload_ref`. `records`
  (`src/prismabib/store/schema.sql`) has no provenance column, and no table records which level supplied a field.
- **OpenAlex fills nothing but keywords.** `_load_openalex_run` (`src/prismabib/store/load.py`) attaches topics only. ADR 0031
  Decision 9 says the loader also derives abstract, authors and affiliations from the stored bodies. It does not, so ADR 0031
  Decision 9 is currently unfulfilled.
- **Levels 2 and 3 persist nothing.** `search_standard_by_dois` and `abstract_meta` (`src/prismabib/sources/scopus.py`) return
  `CascadeResult` in memory. Nothing writes them to `raw/`.
- **Manual entry, the cascade command and an OpenAlex command do not exist.** The `cli.py` commands are `build`, `register`,
  `reverse`, `registrations`, `enrich`, `fill` and others, and none of them runs the cascade or captures OpenAlex.
- **`RunManifest` has no `fallback_opt_in`.** The field exists only on `OpenAlexRunManifest` (`src/prismabib/capture/manifest.py`).
  The frozen Scopus `RunManifest` is unchanged by this record.
- **Public text is stale.** `docs/methodology/limitations.md` says "Scopus only ... no OpenAlex" and "prismabib refuses to degrade
  to STANDARD". ADR 0028 Resolved item 4 requires that text to change in the same pull request as the behaviour.

**Abandoned data.** The sealed runs already under `projects/Baseball-CVPR/raw/`, including the two OpenAlex runs, are not
supported by this design (Decision 11). No migration or compatibility code is written for them.

## Owner decisions

The owner took these decisions on 2026-11-02. They are recorded here as given, with the rationale this record adds.

1. **Scope A+B+C.** Per-field provenance (A), manual entry at level 5 (B), and an end-to-end cascade command with per-level counts
   (C). Rationale: ADR 0028 Consequence 1 is not met by any one of them alone, and a smaller scope would leave the methods unable
   to say where a field came from.
2. **Merge rule.** The first level that returns a value wins per field. A lower level (OpenAlex) may fill a field that a Scopus
   record lacks, and never overrides a value Scopus supplied. When both have a value and they disagree, the pair goes to a
   conflicts review list, with both values recorded. Rationale: a silent overwrite would change Scopus data without a trace, and
   a silent choice of either side would hide the disagreement. Decision 2 states the mechanics.
3. **Provenance as columns on `records`.** Per field among abstract, authors, affiliations and keywords: source, level, outcome and
   payload reference. It is rebuilt from Layer 0 only. Decision 3 gives the names, the closed sets and the consequences. Rationale:
   the table form was the alternative, and four columns per field on one row avoid a join for every export and every methods
   sentence. The cost is a checksum change, which Decision 3 and Verification record.
4. **Manual entry (level 5).** An append-only Layer 2 `manual_entry` event, written from the Panel form after an "Open publisher
   page" button. It fills only fields still `unavailable`, may not override Scopus or OpenAlex values, and is labelled `manual`
   in exports and methods. Decision 4 gives the schema, the fold rule, the reversal and the GUI implications.
5. **Per-level count keys in `numbers.json`.** Per field and level: supplied counts, `unavailable`, `manual`, and a `conflicts`
   count. Decision 5 gives the exact names and why they are optional. Rationale: the methods can then say, with numbers, how much
   of the corpus each source contributed.

## Decision

### 1. The levels and what each one may supply

| Level | Source label | Request | Fields it may supply | Layer 0 or Layer 2 |
|---|---|---|---|---|
| 1 | `scopus-complete` | Search `COMPLETE` / Abstract `FULL` (acquisition, unchanged) | abstract, authors, affiliations, keywords | `raw/<run_id>/` (existing) |
| 2 | `scopus-standard` | Search `STANDARD`, batched by DOI | affiliations | `raw/scopus-cascade/<run_id>/` (Decision 6) |
| 3 | `scopus-meta` | Abstract `META`, one record per request, by `scopus_id` | affiliations | `raw/scopus-cascade/<run_id>/` |
| 4 | `openalex` | OpenAlex works (ADR 0031) | abstract, authors, affiliations, keywords (topics) | `raw/openalex/<run_id>/` (existing) |
| 5 | `manual` | Entered by a named reviewer | abstract, authors, affiliations | `manual_entries.jsonl` (Layer 2, Decision 4) |

**1a. Levels 2 and 3 supply `affiliations` only.** `CascadeResult` carries affiliations, a first author and identifiers. It does
not carry an abstract, a full author list or keywords. A first-author list stored as `authors` would be a partial field reported
as complete, which is the bias ADR 0028 Context names. So `authors` is supplied only by levels 1 and 4, and ADR 0028 Decision 1's
table is narrowed to that.

**1b. Keywords.** The `keywords` field is supplied by level 1 when the record has Scopus author or index keywords. Otherwise
level 4 supplies it when the record has OpenAlex topics, with source `openalex`. The topics are also written as
`openalex-topic` links in `record_keywords`, as ADR 0030 specifies, whether or not they fill the field. Keywords never conflict
and take no manual entry, because no keyword kind exists for manual values.

**1c. Which records each level is asked about.**

- Level 2 runs over records whose affiliations are unsupplied after level 1.
- Level 3 runs over records whose affiliations are still unsupplied after level 2. It keys on `scopus_id` (ADR 0028 Resolved
  item 2), which every Scopus record carries in `record_id`.
- Level 4 runs over every record that has a DOI, and by title and year over records without one. It is not limited to blanks,
  because a conflict needs the OpenAlex value even where Scopus supplied the field. This is cheap: about 23 requests of 50 DOIs
  for 1,119 records.

### 2. The merge rule, per record and per field

For each field, in this order:

1. The first Scopus level (1, then 2, then 3) that holds a value supplies the field. Its value is kept, and no later level replaces it.
2. Level 4 supplies the field only when no Scopus level did.
3. When a Scopus value and a level 4 value both exist and differ, a row is written to `provenance_conflicts` with both values and
   both payload references. Layer 1 keeps the Scopus value, as rule 1 requires. The conflict is visible in the export and in the
   count, so the choice is not silent.
4. Level 5 fills the field only when levels 1 to 4 left it unavailable.
5. A field with no value after level 5 is `unavailable`.

**Comparison.** Two values differ when their normalised forms differ. The normal form of an abstract or an affiliation name is
the text casefolded with runs of whitespace collapsed. An author list is compared by its surname sequence in the same normal form.
OpenAlex abstracts are rebuilt from an inverted index, so punctuation and hyphenation differences may appear. That makes conflicts
more frequent than real disagreement. Risk 4 covers it, and PR3 measures the rate before any count is published.

**New table.** `provenance_conflicts` (added to `store/schema.sql` and to `_TABLE_SORT_KEYS`, which the golden checksums use):

```sql
CREATE TABLE provenance_conflicts (
  record_id TEXT, field TEXT,
  scopus_source TEXT, scopus_payload_ref TEXT, scopus_value TEXT,
  openalex_payload_ref TEXT, openalex_value TEXT,
  PRIMARY KEY (record_id, field)
);
```

It is read-only in this record. Resolving a conflict is out of scope (Open questions, item 3).

### 3. Provenance lives as columns on `records`, rebuilt from Layer 0

**Column names.** For each `<field>` in `abstract`, `authors`, `affiliations`, `keywords`, `records` gains four columns:

| Column | Type | Closed set, or content |
|---|---|---|
| `<field>_source` | TEXT, NOT NULL | `scopus-complete`, `scopus-standard`, `scopus-meta`, `openalex`, `manual`, or `unavailable` |
| `<field>_level` | INTEGER, NOT NULL | 1 to 5, the supplying level; 0 when the field is unavailable |
| `<field>_outcome` | TEXT, NOT NULL | `value` or `unavailable` |
| `<field>_payload_ref` | TEXT, nullable | The run-relative payload path, with `#<openalex_id>` for OpenAlex, or the manual `event_id`; NULL when unavailable |

So the new columns are `abstract_source`, `abstract_level`, `abstract_outcome`, `abstract_payload_ref`, and the same four for
`authors`, `affiliations` and `keywords`: 16 columns in all. Each carries a CHECK constraint on its closed set, in the form ADR
0030 Decision 3 uses for `record_keywords.kind`:

```sql
abstract_source TEXT NOT NULL CHECK (
  abstract_source IN ('scopus-complete', 'scopus-standard', 'scopus-meta', 'openalex', 'manual', 'unavailable')
),
```

**The literal `unavailable`.** It is written in `<field>_source` and `<field>_outcome` for every field with no value after level 5.
Exports and views never show a blank for these columns. An empty `<field>_payload_ref` is exported as the literal `unavailable`.

**Why the outcome set is two values here.** `<field>_outcome` records the final state of the field. The attempt outcomes `absent`,
`not_found` and `refused` are per-level facts. They are already stored per identifier in the Layer 0 manifests (ADR 0031
Decision 4) and are not repeated in Layer 1. The gap against ADR 0028 Decision 2, which asks for each earlier outcome in the
provenance map, is recorded as an amendment of that decision. Open questions, item 1, asks whether the owner wants the trail as a
fifth column.

**Rebuild.** The loader computes all 16 columns, and the conflicts table, from Layer 0 and from the Layer 2 manual log. Nothing is
kept only in memory. A rebuild from the same runs and the same log must give identical checksums (Verification).

**Registered records.** `records_all` (a view, not checksummed) gains the same 16 columns. For a registered record, `abstract`
takes source `openalex`, level 4, when its abstract is present, else `unavailable`. The other three fields are `unavailable`,
because `registered_records` (ADR 0032) stores no authors, affiliations or keywords. Open questions, item 2.

**Consequences for the schema.** `records` is named in the frozen BUILD_PLAN clause that `schema.sql` reproduces, and a column
added there is a deviation. This record is the ADR for it, as ADR 0030 Decision 3 was for `record_keywords`. The new table
`provenance_conflicts` is an addition of the kind ADR 0012 and ADR 0013 made.

**Migration: none.** The store is a build artefact, rebuilt from Layer 0 (`store/db.py`). A store built before this change has no
provenance columns. `prismabib build --rebuild` produces the new columns, and the build report says that the previous store had none.

### 4. Manual entry (level 5)

**Event.** A new Layer 2 log, `manual_entries.jsonl`, in the project directory beside `registrations.jsonl`. It uses the
append-only, checksum-guarded machinery of ADR 0024 (`prismabib.log.AppendOnlyLog`), and writes no other file.

| Field | Content |
|---|---|
| `event_id` | Generated as for registrations (ADR 0029 Decision 2) |
| `schema_version` | `1` |
| `action` | `manual_entry` |
| `ts` | UTC time of the write |
| `project` | The project slug |
| `reviewer` | The named reviewer (required) |
| `record_id` | An existing `scopus:` record id. A registered record is not eligible in this version. |
| `field` | `abstract`, `authors` or `affiliations` |
| `value` | Text. Authors: one per line, `Surname, Given`. Affiliations: one per line, the name. Line endings are stored as `\n`. |
| `doi` | The DOI page opened, or NULL when the record has none |
| `note` | Optional |

**Reversal.** Consistent with registrations (ADR 0029 Decision 7). A `reverse` event names the `manual_entry` by `event_id`,
with a reviewer and a reason from a closed set: `wrong_value`, `wrong_field`, `duplicate_entry`, `other`. Nothing is deleted.
The reversed entry is not applied.

**Writer refusals.** `capture/manual.py` refuses a write when the record does not exist, when the field is not one of the three,
when the value is blank after stripping, when the field is not `unavailable` in the current Layer 1 store, or when an active
`manual_entry` already exists for that record and field. The first refusal comes from the writer's check of the store. A
second refusal is raised by the fold, if a later build finds one.

**Fold rule.** The loader runs levels 1 to 4 first. Then, for each `(record_id, field)` that is still `unavailable`, it applies the
single active `manual_entry`. An active entry for a field that levels 1 to 4 supplied is not applied. It is counted in the build
report as `manual_superseded` and is not a numbers key. Two active entries for one `(record_id, field)` fail the build with a
`StoreError` that names both, and reversing one unblocks it, as ADR 0029 does for registrations.

**Labelling.** The source is `manual`, the level is 5, and the payload reference is the `event_id`. Authors and affiliations
entered by hand get ids derived from the event: `manual:<event_id>:<n>`, with `n` the position in the value. Those ids are
deterministic, so a rebuild reproduces them.

**GUI.** A new Panel view, `capture/manual_ui.py`, mounted as a tab beside the registration view (ADR 0035). It lists the record's
unavailable fields, and has one text area per field. An "Open publisher page" button opens `https://doi.org/<doi>` in the default
browser through `webbrowser`. The button is disabled, with a line of text, when the record has no DOI. "Save" calls the writer in
`capture/manual.py`, and the view does not write the log itself. A CLI pair, `prismabib manual-entry` and `prismabib manual-reverse`,
calls the same writer, so the one-code-path rule of ADR 0035 holds.

**KEY_MAP.** Unchanged. Manual entry is not a screening action, and it is not a decision (ADR 0003). The screening view does not
gain the form.

### 5. The numbers keys

The new keys are all optional, and all live under the `methods.` prefix. Reason: `fill` exempts only `methods.*` from its
unused-key check (`src/prismabib/report/fill.py`). A plain `cascade.*` key would make every existing manuscript fail `fill`, as
ADR 0040 Decision 12 records for the venue snippet. The alternative is to widen that exemption, which is a change to ADR 0039's
mechanism and is not taken here.

| Key pattern | Type | Meaning |
|---|---|---|
| `methods.cascade.<field>.level1` ... `level4` | int | Records whose field was supplied by that level |
| `methods.cascade.<field>.manual` | int | Records whose field was supplied by level 5 |
| `methods.cascade.<field>.unavailable` | int | Records whose field is unavailable after level 5 |
| `methods.cascade.<field>.conflicts` | int | Records in `provenance_conflicts` for that field (always 0 for keywords) |
| `methods.cascade_sentence` | str | Always present. The methods sentence below. |

`<field>` is `abstract`, `authors`, `affiliations` or `keywords`. That is 4 fields times 7 integer keys, which is 28 integer
keys, plus the one string, 29 keys in all. Every key is always present, with zero where nothing happened, so the key set does not
depend on which levels ran.

**Consuming sentence.** `methods.cascade_sentence` is built from the counts by a pure function in `report/methods.py`, for example:

> Abstracts were taken from Scopus COMPLETE for {level1} records, from OpenAlex for {level4} and entered manually for {manual};
> {unavailable} have no abstract. ...

with the same clause for each field, and the conflict count after each field's clause.

**Key counts.** Required keys stay at 41. Optional keys go from 3 to 32. Total keys go from 44 to 73. The count that
`docs/how-to/run-the-demo.md` states must move to match, and so must the optional-key list in its paragraph on `fill`. The sample
output in that file that prints `numbers.json keys 33` is stale already and is corrected in the same pull request.

**Goldens that change.** `tests/golden/report/__snapshots__/numbers_keys.json` gains the 29 keys. `reference_numbers.json` gains
their values. The values are not zero in general, because level 1 counts what the reference corpus already holds. Each change is
named in the pull request that makes it.

### 6. Scopus Layer 0 for levels 2 and 3

**Run kind.** `raw/scopus-cascade/<run_id>/`, with `response-NNNN.jsonl` per HTTP response, a `progress.json` resumption sidecar
deleted on seal, and `manifest.json` as the seal. The name is added to `layout.NON_RUN_DIRNAMES`, as ADR 0031 Decision 8 does
for `openalex`. The run ids come from `layout.new_run_id()`, and every write goes through `guard_writable`.

**Manifest.** `CascadeRunManifest`, a new model in `capture/manifest.py`, not the frozen `RunManifest`. Its fields:

- `run_id`, `started_at`, `finished_at`, `endpoint` (the base URL only), `client_version`, `criteria_version`.
- `fallback_opt_in` (bool). An opt-in is required to write any request in this run kind, and a sealed run with `false` is refused.
- `api_key_used` (bool). Never the key.
- `budget_requests` (int, from `--scopus-budget`), `requests_made` (int), and `stop_reason`: `complete`, `budget` or `quota`.
- `requests`: one entry per HTTP request, with `index`, `file`, `sha256`, `view` (`STANDARD` or `META`), `identifiers` (DOIs for
  `STANDARD`, `scopus_id`s for `META`), `http_status`, `retrieved_at`, `quota_remaining` (from the header, or null), and `outcomes`.
- Each outcome: `identifier`, `outcome` (`value`, `not_found` or `refused`), `http_status`, and `affiliations` (the count).
- `payload_files`, `payload_sha256`.

**Opt-in.** A run is created only with `--opt-in`. A 401 or 403 in such a run is recorded as `refused` for the affected identifiers,
and the run continues (ADR 0028 Resolved item 1). Outside an opt-in run the existing `AuthError` and `EntitlementError` behaviour is
unchanged.

**Budget and quota.** `--scopus-budget N` caps the requests made in the run, and it is required with `--opt-in`. When the cap is
reached, the run stops, writes `stop_reason: budget`, and seals. A weekly-quota 429 (`QuotaExceededError`) stops the run the same
way, with `stop_reason: quota`. An identifier that was not attempted has no outcome, so it is not held, and the next run asks for
it (Decision 7 reuse rule). A `--dry-run` prints the planned requests per level before any call is made.

**Reuse.** An identifier is held when a sealed run of this kind holds a `value` or `not_found` for it. A `refused` outcome does not
hold it. This is the rule ADR 0031 Decision 7 gives.

**Loader.** `store/load.py` gains `_sealed_cascade_run_dirs`, which lists the sealed children of `raw/scopus-cascade/`, as ADR 0031
Decision 8 does for OpenAlex. Attachment is by `scopus_id` for `META` and by normalised DOI for `STANDARD`. The loader recomputes
every outcome from the stored bytes and fails on disagreement, as ADR 0031 Decision 4 does. A malformed body becomes a
`malformed_entries` row (ADR 0012).

**Unverified batch form.** `search_standard_by_dois` documents that the multi-DOI `OR` form is not live-verified. PR2 verifies it
against the live API before any run relies on it (Risk 6).

### 7. The `prismabib cascade <slug>` command

| Option | Meaning |
|---|---|
| `--opt-in` | Enables levels 2 and 3, and records refusals in them |
| `--scopus-budget N` | The request cap for levels 2 and 3. Required with `--opt-in`. Without it, levels 2 and 3 do not run. |
| `--refresh` | Passed to level 4 only (ADR 0031 Decision 7) |
| `--dry-run` | Prints the identifiers and planned requests per level. No HTTP call, no write. |

**Order.** Level 1 is not touched (acquisition is unchanged). Then level 2, level 3, level 4, and level 4 for unresolved candidates
(Decision 9). Each level writes its Layer 0 run through its own writer. Then the command runs `build_store(project, rebuild=True)`,
so the counts it prints come from the store, and it prints the per-level report.

**Report.** Two tables. The first has one row per level, with the source, requests used, identifiers attempted, `value`,
`refused`, `not_found` and the stop reason. The second has one row per field, with the level 1 to 4 counts, manual, unavailable and
conflicts. These are the same numbers as the keys in Decision 5.

**Exit codes.** `0` for `complete`, `budget` and `quota`, since those stops are recorded and reported. Non-zero for a refusal
outside an opt-in run (`AuthError` or `EntitlementError`), and for a `StoreError` from the build. A refusal that aborts the command
seals nothing for the run that refused, as ADR 0031 Decision 5 requires.

**Idempotence.** A second run with nothing new to ask creates no run and makes no request for level 4 (ADR 0031 Decision 7). For
levels 2 and 3, it asks only for identifiers without a held outcome.

### 8. The OpenAlex capture command

`prismabib openalex <slug>`, with `--opt-in`, `--refresh` and `--dry-run`, calls the existing `capture_openalex` in
`src/prismabib/capture/openalex.py`. Its identifiers are every record in the raw stage, by DOI, or by title and year where there
is no DOI. Its inputs and its reuse rule are the ones ADR 0031 Decision 7 gives. Its output is a count per field and per outcome
(`value`, `absent`, `not_found`, `refused`) for the run it made. It creates no run when nothing is left to request. `cascade`
calls the same function for level 4, so there is one path.

### 9. Candidate entry from a Scholar paste

**Entry point.** `prismabib candidates add <slug> --reviewer R [--file PATH]` reads pasted rows from the file or from standard input,
then calls `parse_pasted_rows` and `CandidateLog.add`, both in `capture/candidates.py`. A Panel box for the same paste is a later
change, and this record does not require it.

**DOI enrichment.** A Scholar paste carries title and year only. Enrichment goes in this order:

1. Level 4 title search by normalised title and year (ADR 0028 Decision 4). A match supplies the DOI.
2. Level 2 by the DOI from step 1, then level 3 by the `scopus_id` that level 2 returned.

A candidate with no match at level 4 stays unresolved (ADR 0028 Decision 5).

**Where the results go.** Candidate results are Layer 0 payloads that the registration view shows as evidence. A candidate is not
a record, so it gets no row in `records` and no provenance columns. Only a registration (ADR 0029) creates a record, and that
record's provenance follows Decision 3 from its own OpenAlex body.

**Who runs it.** `prismabib cascade` runs candidate enrichment after the record levels, as one more step in the same command. Its
counts go on a separate line of the report, and they are not numbers keys.

### 10. Stale public text, and dated notes on frozen records

**In the implementing pull requests, not in this one:**

- `docs/methodology/limitations.md` (the "Scopus only" and "no OpenAlex" text, and the "refuses to degrade" text) is rewritten to
  describe the opt-in cascade and the labels. This is the public-text change ADR 0028 Resolved item 4 requires, and it lands in
  PR1.
- The docstrings that say the view "never degrades" (`src/prismabib/sources/scopus.py` and `src/prismabib/enrich.py`) are corrected
  in PR1.
- `docs/how-to/register-and-reverse-candidates.md` says to call `capture_openalex` by hand. That sentence is replaced by the
  command in PR4.
- A dated pointer note is appended to **ADR 0031, Decision 9** in PR3. It says the loader computes abstract, authors and affiliations
  from the stored bodies from that pull request, and that it did not before. It edits no older text.
- A dated pointer note is appended to **ADR 0028**, in PR3. It points to this record for the level 2 and 3 authors row (Decision
  1a), the column form of Decision 2 (Decision 3), and the title match, which is unchanged. ADR 0028 is not edited in place.

### 11. Out of scope: the sealed runs already on disk

The sealed runs already under `projects/Baseball-CVPR/raw/` are not supported by this design. That covers the Scopus acquisition
run, the abstract runs, and the two OpenAlex runs (`20261023T013520Z-d69e4d0f` and `20261026T084313Z-ae7e7c35`). The loader and
manifest changes may assume that every OpenAlex and cascade run was produced by the new commands. No migration or compatibility code
is written for runs of the old shape. Nothing in this record deletes them. PR8 starts from scratch: every level is captured anew with
the new cascade command, and the store is rebuilt from those new runs.

## Alternatives considered

- **A child table `record_field_provenance`, one row per record and field.** Rejected for the reason in Decision 3. It is a join for
  every export and every methods sentence, and the owner chose columns.
- **Let OpenAlex override Scopus where they disagree.** Rejected. It changes Scopus data without a trace, and rule 2 forbids it.
- **Silently keep Scopus in the conflict case, with no list.** Rejected. It is a silent choice, which the owner ruled out.
- **Manual entry from the screening view.** Rejected. Entering a field is not a decision (ADR 0003), and the screening view's keys
  and list are out of scope (ADR 0035 Decision 9).
- **Plain `cascade.*` numbers keys.** Rejected for the reason in Decision 5.
- **Levels 2 and 3 supply `authors` from their first author.** Rejected (Decision 1a).

## Consequences

1. Every field in every export says which level supplied it, or says `unavailable`. A reader can tell an absent value from an
   unasked one.
2. The `records` checksum changes for every store, because each row now carries 16 more cells. The reference table checksums and
   `reference_numbers.json` change in PR3 and PR7 respectively, and each changed file names the cause.
3. The store is rebuilt, not migrated (Decision 3). A store built before this change has no provenance, and the build report says so.
4. Conflicts make the methods more honest and make the record list longer. The count is a number a manuscript may cite.
5. Levels 2 and 3 add Scopus requests. The budget is set by the owner each time, and the dry run shows the cost before it is spent.
6. A manual value is a human step, so it is slow and it can be wrong. It is labelled, it is never the default, and a reversal
   removes it from the fold.

## Verification

Every test is shown to fail on the defect it guards, following the rule ADR 0031 Verification sets. Each pull request runs
`pytest -n auto` as CI does, and once single-process. It runs `mypy --strict` on `src/prismabib` at Python 3.11, and `ruff`.

- **Rebuild determinism.** `build_store(rebuild=True)` twice on the same runs and the same log gives identical table checksums,
  including `records` and `provenance_conflicts`. Tests run on Windows and Linux.
- **Merge rule.** A Scopus value is never replaced by OpenAlex. OpenAlex fills a blank. A disagreement creates one conflict row
  with both values. A manual entry fills only an `unavailable` field.
- **No blanks.** For every record, every provenance column is non-null, and every `*_outcome` and `*_source` is in its closed set.
- **Closed sets.** A value outside a closed set is refused by the CHECK constraint.
- **Golden changes.** `reference_table_checksums.json` and `reference_numbers.json` change only in the pull request that names
  the cause. Neither is regenerated to make a test pass.
- **Opt-in and refusal.** A cascade run without `--opt-in` makes no level 2 or 3 request and writes nothing sealed. A sealed
  run with `fallback_opt_in` false is refused.
- **Budget and quota.** A run stopped by the budget seals with `stop_reason: budget`, and the unattempted identifiers are asked for
  again by the next run.
- **Idempotence.** A second `openalex` or `cascade` call with nothing new creates no run and makes no request.
- **Key confinement.** The Scopus key, when set, appears in no file under `raw/scopus-cascade/` or in any log.
- **Manual.** Writes are refused for supplied fields and for blank values. Reversal removes the entry from the fold. The CLI and
  the Panel view call the same writer (a parity test in the style of ADR 0035).
- **Keyword field.** No conflict and no manual entry for keywords.

## Implementation plan

Three batches. Each batch's pull requests are independent within the batch, and a later batch starts after the previous batch is
merged. The hotspot files have one owner each: `src/prismabib/store/schema.sql`, `src/prismabib/models.py` and
`src/prismabib/store/load.py` change only in PR3. The goldens and `docs/how-to/run-the-demo.md` key counts change only in PR7.

**Batch 1.**

- **PR0 (this record).** The ADR and its nav entry. Docs only, no code. Accepted when the owner approves the merge.
- **PR1 (docs).** Rewrites `limitations.md`, and the "never degrades" docstrings in `scopus.py` and `enrich.py`. Acceptance:
  `mkdocs build --strict` passes, and no public page says "no OpenAlex" or "refuses to degrade". Failing test first: a docs test
  that asserts those phrases are absent from `docs/` and from the two docstrings, which fails on `main`.

**Batch 2.**

- **PR2 (Scopus levels 2 and 3, Layer 0).** `capture/cascade.py` (the writer), `CascadeRunManifest`, `NON_RUN_DIRNAMES`,
  `_sealed_cascade_run_dirs`, and the opt-in and budget handling. Acceptance: a sealed run for a fake client that returns 200, 401 and
  404, a budget stop, and a quota stop. The loader recognises the runs and computes outcomes from the bytes. The live batch form is
  verified and the result is recorded in the PR. Failing tests first: refusal without opt-in writes no seal; a sealed run with
  `fallback_opt_in` false is refused; a Scopus scan finds only Scopus runs; the key is in no stored file.
- **PR3 (provenance columns, conflicts, loader merge).** `schema.sql` (16 columns and `provenance_conflicts`), `models.py`, `load.py`
  (the merge, the conflict rows, the Layer 2 manual fold, though the manual writer lands in PR6), the `records_all` view, the checksum
  list, and the two dated pointer notes (ADR 0031 Decision 9 and ADR 0028). Acceptance: the merge rule tests, the no-blank test, the
  rebuild-determinism test, and the golden checksum change, named in the PR. Failing tests first: the no-blank test and the
  never-overrides test, both failing on the current `load.py`.
- **PR5 (candidate paste entry).** `prismabib candidates add`, the enrichment order of Decision 9, and the registration view's
  evidence line. Acceptance: a pasted title with no match creates no record and no `records` row. A candidate with a DOI from
  level 4 reaches level 2. Failing tests first: a candidate title-only request goes to level 4 only, and no record is created.

**Batch 3.**

- **PR4 (cascade and OpenAlex commands).** `prismabib cascade`, `prismabib openalex`, the report tables, the exit codes, and the
  replacement sentence in `register-and-reverse-candidates.md`. Acceptance: the idempotence test, the dry-run test that makes no
  call, and the budget-required-with-opt-in test. Failing tests first: a second call makes no request.
- **PR6 (manual entry).** `manual_entries.jsonl`, `capture/manual.py`, `capture/manual_ui.py`, `prismabib manual-entry` and
  `prismabib manual-reverse`, and the fold in `load.py` (whose only change in this PR is the call into the manual log). Acceptance:
  the refusals, the reversal, the GUI and CLI parity test, and the disabled button for a DOI-less record. Failing tests first: a
  manual value for a supplied field is refused; a reversed entry is not applied.
- **PR7 (exports, methods, numbers keys, goldens, run-the-demo).** The export columns, `methods.cascade_sentence`, the 29 keys,
  `numbers_keys.json`, `reference_numbers.json`, `docs/how-to/run-the-demo.md` (the 44 to 73 key count, the optional list, and the
  stale `33`), and the `limitations.md` label text. Acceptance: the golden tests pass with the stated changes, and `fill` on the demo
  manuscript passes. Failing tests first: the key-set golden, which fails until the keys are added.
- **PR8 (fresh real run, CHANGELOG).** Every level is captured anew with the new commands on the real project, the store is rebuilt
  from those runs, and the numbers are reported in the CHANGELOG. The old runs are not used (Decision 11). The real project's data
  is not committed.

## Risks

1. **Checksums.** The `records` checksum changes for every store (Consequence 2). The golden is regenerated only in PR3, and the
   change is explained there.
2. **Goldens.** The key set, the values and the reference checksums all change. PR7 owns the number goldens, and PR3 owns the checksum
   golden. A change without a named cause stops the pull request.
3. **Machine dependence.** Casefolding and Unicode normalisation depend on the Unicode data of the Python build, and on the Python 3.11
   patch release. Two machines may compare two abstracts differently, which would give different conflict counts. Mitigation: pin the
   comparison to one documented function, test it against a fixed set of strings, and report the Python version in the build report.
   Other sources of drift: path separators in `payload_ref` (always `/`), CRLF in manual text (stored as `\n`), dict order (every
   sort is explicit), and the clock (the loader reads none).
4. **Conflict noise.** OpenAlex abstracts come from an inverted index, so formatting differences look like disagreement. PR3 measures
   the conflict rate on the real data before any conflict count is cited. If it is high, the comparison rule is revisited in a new ADR.
5. **Rebuild determinism.** The latest-run rule (ADR 0031 Decision 7) and the manual fold must not depend on the order of file
   listing. Run ids sort by time, and the manual log is read in file order.
6. **Unverified batch form.** The multi-DOI `STANDARD` request is not live-verified. PR2 verifies it. If the batch form fails, level 2
   runs one DOI per request, which costs more quota.
7. **Quota.** An opt-in run can spend a weekly quota. The budget is required, and the dry run prints the cost first.
8. **Scopus data in shared outputs.** Level 2 and 3 payloads are Scopus data. Whether a shared sample may contain them is an open
   question (item 4).

## Open questions for the owner

1. **Attempt trail.** Should `absent`, `not_found` and `refused` per field and level be kept in Layer 1, as a fifth column per field
   (ADR 0028 Decision 2), or left in Layer 0 only, as this draft does?
2. **Registered records.** Should `registered_records` store authors, affiliations and keywords, which needs a change to ADR 0032's
   table? Until then, they are `unavailable` for registered records.
3. **Conflict resolution.** Is the conflicts list read-only, as here, or should a reviewer resolve each conflict by a Layer 2 event?
4. **Sharing.** May a shared sample contain level 2 and 3 Scopus payloads, or the Scopus abstracts from level 1, under the Scopus
   terms? This is the sharing decision the plan lists as item 7.
5. **Budget defaults.** Is there a standing `--scopus-budget` the owner wants as a default, or is it always given explicitly? The draft
   requires it explicitly.

## Records touched

- New: this file.
- Edited: `mkdocs.yml`, one nav entry after the ADR 0040 entry.
- Not touched by this record: code, tests, goldens, `CHANGELOG.md`, `.claude/PROGRESS.md`, and BUILD_PLAN.md. The implementing pull
  requests own them, as Implementation plan assigns.

## Related decisions

- [ADR 0002](0002-append-only-decision-log.md): the append-only pattern, applied to the manual log.
- [ADR 0003](0003-human-only-screening.md): why manual entry is not a screening decision.
- [ADR 0012](0012-persisting-skipped-layer0-entries.md) and [ADR 0013](0013-identified-sums-across-searches.md): the precedent for adding a
  table to the frozen schema.
- [ADR 0018](0018-abstract-runs-in-layer-1.md) and [ADR 0021](0021-entitlement-refusals-are-attributed-to-a-publisher.md): refusal
  attribution, which the refusal outcomes follow.
- [ADR 0022](0022-the-analysis-result-contract-and-its-provenance.md): analysis-level provenance. This record adds per-field provenance.
- [ADR 0024](0024-one-implementation-of-append-only-durability.md): the append-only log used for `manual_entries.jsonl`.
- [ADR 0028](0028-field-level-fallback-cascade.md): the cascade this record completes. It amends Decision 1 for the levels 2 and 3
  authors row, and Decision 2 for the column form.
- [ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md): the reversal pattern and the event envelope.
- [ADR 0030](0030-openalex-topics-as-a-keyword-kind.md): the CHECK-constraint amendment form, and the topic kind.
- [ADR 0031](0031-openalex-responses-are-layer0-runs.md): the OpenAlex run kind that levels 4 uses. Decision 9 is made true by PR3.
- [ADR 0032](0032-registered-records-table-and-classification-map.md): `registered_records`, whose columns are the subject of open question 2.
- [ADR 0035](0035-gui-registration-of-candidates.md): the GUI and CLI single-writer rule, and KEY_MAP.
- [ADR 0039](0039-taxonomy-engine-behaviour-stamp.md): the `methods.*` exemption that Decision 5 relies on.
- [ADR 0040](0040-year-range-in-analysis-engines.md): the precedent for optional `methods.*` keys.
