# ADR 0032: Layer 1 Table and View for Registered Records, and the OpenAlex Classification Map

## Status

Proposed — 2026-10-22.

This record exists only on the branch `docs/adr-0032-0033-drafts`. The governance test
(`tests/unit/test_docs_governance.py`, `test_adr__on_main__is_not_still_proposed`) fails any ADR whose
Status reads `Proposed` on `main`, so this line must not reach `main` as written. The owner accepts
this record on merge by changing the line to `Accepted — <date>` in the pull request that enacts it,
after the open questions at the end are decided.

Nothing here is decided by the owner yet except what [ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md)
already records. Each change is a deviation from the frozen BUILD_PLAN (Stage 3, lines 845-879) and
needs this record under §2.6 (BUILD_PLAN line 301). It is the precondition that ADR 0029 names for
registration code.

## Context

[ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md) lets a reviewer register a
candidate that has been resolved at level 4 (OpenAlex) as a record. Its "Split records" section states
what this record must specify, and its Decision 2 names the `register` event fields that depend on it:
`subject_map_version` and `subject_map_sha256`. Three inputs must be derivable for a registered record
before it can enter `S_raw`: `doc_type`, `language`, and a subject area. ADR 0029 says that a registered
record without them "cannot be classified and must not enter S_raw".

**The frozen Stage 3 schema has no place for registered records.** BUILD_PLAN lines 853-873 define
`records` (keyed on `record_id`, filled from Scopus payloads), `subject_areas(record_id, area_code)`, and
`runs`. Registered records do not come from a Scopus payload and have no `runs` row (ADR 0029 §9). A new
Layer 1 table is a schema addition, and [ADR 0012](0012-persisting-skipped-layer0-entries.md) requires its
own ADR. Earlier additions (ADRs 0012, 0013, 0018, 0019) followed that rule.

**The engine reads Layer 1 directly.** `prisma/engine.py` takes `S_raw` from `records`, and its automated
filters read `doc_type` and `language` from that row and subject areas from `subject_areas`. For a
registered record to be classified by the same filters, it has to reach those readers through the same
columns or through a view that presents both populations with the same shape.

**Scopus values are not OpenAlex values.**

- `doc_type` on a Scopus record is the Scopus subtype code (`ar`, `cp`, `re`, ...). The loader prefers
  `subtypeDescription` and falls back to `subtype` (`store/load.py`, `_doc_type_from_entry`, line 727).
  `doc_types.include` in `criteria.yaml` is written in that code form.
- `language` on a Scopus record is the language name (for example `English`). `_passes_language` compares
  it case-insensitively against `criteria.yaml`'s `languages` (`prisma/engine.py`, line 357).
- `subject_areas.area_code` holds Scopus's four-digit ASJC `@code` (`2202`). It is matched at the
  four-letter grouping through `asjc.area_abbrev` (`asjc.py`, line 74; [ADR 0017](0017-subject-areas-match-by-asjc-grouping.md)).
- OpenAlex gives `type` (for example `article`, `preprint`, `dataset`, `paratext`), `language` as an
  ISO 639-1 code that OpenAlex detects from the title and abstract and may leave unset, and up to three
  `topics`, each with a `score`. The first topic is `primary_topic`. The second and third scores are
  often near 0. OpenAlex topics are not ASJC codes, and ADR 0028 Decision 2 keeps them separate.

**The current OpenAlex select does not carry the fields this needs.** `sources/openalex.py` line 52 sets
`_SELECT = "id,doi,title,publication_year,abstract_inverted_index,authorships,topics"`. It has no `type`,
no `language`, and no `primary_location`, which is where OpenAlex's source type (journal or conference)
sits. [ADR 0031](0031-openalex-responses-are-layer0-runs.md) Decision 1 fixes this list for stored bodies.
Open question 3 covers changing it.

**The no-data rule does not carry over to registrations.** Scopus records with no `subject_areas` rows,
or a `NULL` language, are never excluded on that dimension (`prisma/engine.py`, module docstring, lines
72-84). Scopus's Search `COMPLETE` view carries no subject codes at all (`store/load.py` module docstring),
so most Scopus records have none. ADR 0029 does not allow the same latitude for registrations, so this
record does not extend the rule to them.

## Decision

### 1. The Layer 1 table and the view

Registered records are stored in one new table. It holds only **active** registrations, which are the
result of folding `registrations.jsonl` (ADR 0029 §3). The loader builds it, as the only Layer 1 writer
(ADR 0028 Decision 3), and it is rebuildable from Layers 0 and 2 (ADR 0029 V14).

```sql
CREATE TABLE registered_records (
  record_id TEXT PRIMARY KEY,         -- 'registered:' || event_id (ADR 0029 §4)
  event_id TEXT NOT NULL,             -- the active register event
  doi TEXT NOT NULL,                  -- required (ADR 0029 §1)
  title TEXT NOT NULL,
  year INTEGER NOT NULL,
  doc_type TEXT NOT NULL,             -- Scopus subtype code, derived per Decision 2
  language TEXT NOT NULL,             -- Scopus language name, derived per Decision 2
  openalex_id TEXT NOT NULL,          -- the work's OpenAlex id (ADR 0031 Decision 2)
  payload_file TEXT NOT NULL,         -- PayloadRef file, as in ADR 0031 Decision 2
  payload_line INTEGER NOT NULL,      -- always 0 for an OpenAlex work
  match_rule TEXT NOT NULL,           -- doi, title_year or near_confirmed (ADR 0029 §1)
  reviewer TEXT NOT NULL,
  registered_ts TIMESTAMP NOT NULL    -- UTC, from the register event's ts
);
```

The classification map version and checksum are **not** repeated in this table. They live in the
`register` event, and the table does not duplicate them (a copy could go stale against the event that
names it, the same reason ADR 0017 gave for not adding `area_abbrev`). The subject areas of a registered
record go into the existing `subject_areas` table, with `area_code` as a four-digit ASJC code, the same
form as a Scopus record. No change to `subject_areas` is needed.

The view that unites the two populations is the one reader of `S_raw` for registrations:

```sql
CREATE VIEW records_all AS
  SELECT record_id, 'scopus' AS origin, doi, title, year, doc_type, language FROM records
  UNION ALL
  SELECT record_id, 'registered' AS origin, doi, title, year, doc_type, language FROM registered_records;
```

Every reader of `S_raw` moves to `records_all`: the engine's raw set, `compute_flow_counts`, and
`Corpus.records`. Readers of Scopus-only facts keep reading their tables. `runs`, `run_duplicates`, and
`citation_snapshots` are unchanged. A registered record has no citation snapshot. Its `cited_by_count` is
reported as `unavailable` (ADR 0029 Consequence 7).

The table is named `registered_records`, and the view `records_all`. These are naming choices, not owner
decisions, and the owner may rename them in review.

### 2. How each input is derived

All three are derived by the loader from the stored OpenAlex body that the `register` event references
(`source_payload_ref`). Nothing is typed in by the reviewer, and nothing is taken from a client return value
(ADR 0031 Decision 9).

| Input | Source field in the stored body | Derivation | Unmapped or absent |
|---|---|---|---|
| `doc_type` | `type`, and `primary_location.source.type` to tell a conference from a journal | Fixed table in the classification map (Decision 3) | Registration refused |
| `language` | `language` (ISO 639-1) | Fixed table from ISO 639-1 codes to the language names that `criteria.yaml` uses | Registration refused |
| subject area | `primary_topic`, a topic id and its `subfield` | Fixed table from OpenAlex subfield to four-digit ASJC code (Decision 3); open question 2 decides whether other topics count | Registration refused |

**The refusal is at registration, not at the engine.** ADR 0029 §5 names the registration writer as the
enforcement point for every refusal, so it checks all three before the append. The loader also refuses a
`register` event whose referenced body cannot yield all three, so a hand-written or mis-built event cannot
enter `S_raw`. In both places the refusal names the input and its value, for example
`language 'zh' is not in classification map version 1`.

A registered record never has a `NULL` `doc_type`, `language`, or subject area in `records_all`. The engine's
no-data rule therefore does not apply to registered records, which is the point of ADR 0029's "must not
enter S_raw".

**Language is a detector's guess.** OpenAlex derives `language` from the title and abstract, and it may
be wrong for a short or mixed-language text. The methods must say so for each registered record, and the
`register` event's payload reference lets a reader check the value.

### 3. The classification map: a fixed, versioned table, checked

The classification map is one Python module, `src/prismabib/openalex_map.py`, in the style of
`asjc.py`. It holds three tables: `TYPE_TO_DOC_TYPE`, `ISO639_TO_LANGUAGE`, and
`SUBFIELD_TO_ASJC`. It has two constants:

- `MAP_VERSION`, a string. Any change to any table bumps it.
- `MAP_SHA256`, the SHA-256 of the canonical JSON of the three tables:
  `json.dumps(tables, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, encoded as UTF-8.

ADR 0029 calls this file "the OpenAlex-topic-to-ASJC mapping table". This record widens the file to three
tables. The field names `subject_map_version` and `subject_map_sha256` keep their ADR 0029 meaning: they
name the whole file that gave the record its subject area.

**Versions are kept, never replaced.** Every version the code has ever shipped stays in the module. A
`register` event names the version it was derived under, so a rebuild reproduces the same subject areas
after a later table change. A registration naming an unknown version, or a checksum that does not match
the named version, raises `LogError` on load (ADR 0029 V8 and V9 style).

**The table body is not in this draft.** The rows are the owner's decision (open question 1). The
Stage 3 schema, the version rule, and the checks below are fixed here. The table body must be attached
to this record before it is accepted, because ADR 0029 says ADR 0032 "must include the mapping itself".

**Checks, all in the test suite:**

1. `MAP_SHA256` equals the recomputed digest of the three tables. A table edit without a version bump fails.
2. Every ASJC value in `SUBFIELD_TO_ASJC` passes `asjc.area_abbrev(code)[1]`, so it is a code the matcher
   knows (ADR 0017 Constraint, "what validates must be what matches").
3. Every OpenAlex subfield id in `SUBFIELD_TO_ASJC`, and every `type` and `language` value in the table,
   appears in a **recorded** OpenAlex response fixture. A table checked only against itself proves nothing
   (ADR 0017, Constraints, last bullet).
4. Every `type` and `language` value in those fixtures is either mapped or is a refusal case. This exercises
   the refusal path against real data.

### 4. Records without a mapping

A registration whose required input cannot be derived is **refused**. The candidate stays resolved and
unregistered, and it is counted in the "resolved, not registered" line that ADR 0029 §3 requires. No
`register` event is written, and no record is created. A Scopus record is never refused for this reason,
and this record changes nothing for Scopus records.

### 5. What does not change

- `criteria.yaml`, the criteria validator, and `asjc.area_abbrev` are unchanged. A registered record's
  subject codes match exactly as Scopus codes do.
- `_passes_subject_areas` and `_passes_language` keep their no-data rule for Scopus records. Registered
  records never reach it with no data.
- The frozen `records` table, `subject_areas`, and `runs` keep their columns.

## Alternatives considered

**Store registered records in `records` with a null `run_id`.** Rejected. `records.payload_file` and
`payload_line` are Scopus-shaped, and every reader of `records` would need an origin check. A separate
table with a view keeps the Scopus path unchanged.

**Store the classification map version and checksum on the `registered_records` row.** Rejected. ADR 0029
puts them on the `register` event. A second copy in Layer 1 would be derived data that can drift from its
source (ADR 0017's reason for not adding `area_abbrev`).

**Use the OpenAlex type and language strings as they are, with no table.** Rejected. `language` would be
`"en"` for registrations and `"English"` for Scopus records, so `_passes_language` would exclude every
registration against an `English` criteria list. A mismatch that silently excludes one population is the
failure this project is built to prevent (BUILD_PLAN §1.4 and the Stage 4 test rules).

**Derive subject areas at load time from the current table, without versions.** Rejected. A table edit
would silently move records between subject areas, and an old published number could not be reproduced.
Named versions make the derivation a fact recorded in the event.

**Map OpenAlex topic names by string match against ASJC category names.** Rejected. A name match is a
guess with no recorded source, and ADR 0017 requires the mapping to be checked against Scopus's own pairs.

## Consequences

1. The schema gains one table and one view. This is a further Layer 1 addition under ADR 0012, and the
   golden table-checksum snapshot gains a key for `registered_records`. That is a deliberate change, and the
   implementation pull request must name it under BUILD_PLAN line 927.
2. A Scopus-only store builds an empty `registered_records` table. Its row checksums for every existing table
   are unchanged.
3. **The subject-area filter becomes asymmetric.** Most Scopus records carry no subject rows, and the engine
   keeps them. A registered record carries subject rows and is filtered by them. In a store with
   registrations, a criteria `subject_areas` list therefore excludes registrations that map elsewhere and
   keeps every Scopus record without subject data. The methods must state this, and the subject-area limitation
   in `docs/methodology/limitations.md` must name it.
4. Registration throughput depends on the table's coverage, which is not known until the table exists.
   Papers whose primary topic is unmapped cannot be registered until the table covers them. The count of
   refused candidates is reported, not hidden (Decision 4).
5. The OpenAlex select change in open question 3 would widen what each stored body holds. Layer 0 is
   append-only, so a body captured before the change keeps its old fields, and the loader must refuse a
   registration that references such a body.
6. A later classification map version changes only new registrations. Old registrations keep the version they
   name, and the code keeps every old table. That is the cost of reproducibility, and the code grows with each version.

## Open questions for the owner

1. **The table body, and its granularity.** Options: (a) OpenAlex subfield to ASJC code, roughly 250 rows,
   written by the owner and checked by Decision 3; (b) OpenAlex topic to ASJC code, roughly 4,500 rows, with
   more error surface; (c) no table yet, so every registration is refused until one exists. **Recommendation:
   (a).** Subfields are the level at which the ASJC grouping and the topic hierarchy can be matched without
   guessing, and the table stays reviewable by one person.
2. **Which topic feeds the subject area.** Options: (a) `primary_topic` only; (b) every topic in the three-entry
   `topics` list that has a mapped code, unioned; (c) topics above a score threshold, which adds a new number
   with no source. **Recommendation: (a).** OpenAlex's own documentation says the second and third scores are
   often near 0, so unioning them adds noise. Option (a) also makes the refusal rule of Decision 2 simple to
   state.
3. **Fields added to the OpenAlex select.** Registration needs `type`, `language`, `primary_location` (for the
   source type that separates a conference paper from an article), and `primary_topic`. Options: (a) amend the
   select in [ADR 0031](0031-openalex-responses-are-layer0-runs.md) Decision 1, with a dated note; (b) keep the
   select and make no registrations; (c) fetch the extra fields in a second request per work, which spends
   quota that [ADR 0011](0011-abstract-retrieval-for-subject-areas.md) rejected. **Recommendation: (a),** provided
   the repository holds no sealed OpenAlex run that the change would make inconsistent. ADR 0030 states that no
   OpenAlex capture writer existed at its acceptance, and the owner should confirm that nothing was captured since.

## Related decisions

- [ADR 0007](0007-flow-counts-unsure-fields.md): the `FlowCounts` change that ADR 0033 extends.
- [ADR 0011](0011-abstract-retrieval-for-subject-areas.md): the Layer 0 run and the rejected quota path.
- [ADR 0012](0012-persisting-skipped-layer0-entries.md): the rule that a Layer 1 schema addition needs its own ADR.
- [ADR 0017](0017-subject-areas-match-by-asjc-grouping.md): subject areas match by ASJC grouping, and the
  mapping is checked against recorded pairs.
- [ADR 0028](0028-field-level-fallback-cascade.md): OpenAlex topics stay separate from ASJC; the Layer 1 writer rule.
- [ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md): the registration that this record
  makes possible. It names this record as a precondition.
- [ADR 0031](0031-openalex-responses-are-layer0-runs.md): the stored OpenAlex body and the `PayloadRef` form.
- [ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md): the Stage 4 counts that read `records_all`.
