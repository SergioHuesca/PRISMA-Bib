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
   a silent choice of either side would hide the disagreement. Decision 2 states the mechanics. The owner's answer 3 (below)
   amends "never overrides" for one path: a reviewer's `conflict_resolution` event may pick the OpenAlex or the manual value.
3. **Provenance as columns on `records`.** Per field among abstract, authors, affiliations and keywords: source, level, outcome and
   payload reference. It is rebuilt from Layer 0 only. Decision 3 gives the names, the closed sets and the consequences. Rationale:
   the table form was the alternative, and four columns per field on one row avoid a join for every export and every methods
   sentence. The cost is a checksum change, which Decision 3 and Verification record.
4. **Manual entry (level 5).** An append-only Layer 2 `manual_entry` event, written from the Panel form after an "Open publisher
   page" button. It fills only fields still `unavailable`, may not override Scopus or OpenAlex values, and is labelled `manual`
   in exports and methods. Decision 4 gives the schema, the fold rule, the reversal and the GUI implications. The one exception to
   "may not override" is a reviewer's `conflict_resolution` with `chosen_side` `manual` (Decision 2, rule 4), which is a separate
   event and is the only way a manual value replaces a Scopus or OpenAlex value.
5. **Per-level count keys in `numbers.json`.** Per field and level: supplied counts, `unavailable`, `manual`, and a `conflicts`
   count, plus the two totals `conflicts_open` and `conflicts_resolved`. Decision 5 gives the exact names and why they are optional. Rationale: the methods can then say, with numbers, how much
   of the corpus each source contributed.

## Decision

### 1. The levels and what each one may supply

| Level | Source label | Request | Fields it may supply | Layer 0 or Layer 2 |
|---|---|---|---|---|
| 1 | `scopus-complete` | Search `COMPLETE` (acquisition, unchanged) | abstract, authors, affiliations, keywords | `raw/<run_id>/` (existing; see 1d for the abstract) |
| 2 | `scopus-standard` | Search `STANDARD`, batched by DOI | affiliations | `raw/scopus-cascade/<run_id>/` (Decision 6) |
| 3 | `scopus-meta` | Abstract `META`, one record per request, by `scopus_id` | affiliations | `raw/scopus-cascade/<run_id>/` |
| 4 | `openalex` (keywords: `openalex-topic`) | OpenAlex works (ADR 0031) | abstract, authors, affiliations, keywords (topics) | `raw/openalex/<run_id>/` (existing) |
| 5 | `manual` | Entered by a named reviewer | abstract, authors, affiliations | `decisions/manual_entries.jsonl` (Layer 2, Decision 4) |

**1a. Levels 2 and 3 supply `affiliations` only.** `CascadeResult` carries affiliations, a first author and identifiers. It does
not carry an abstract, a full author list or keywords. A first-author list stored as `authors` would be a partial field reported
as complete, which is the bias ADR 0028 Context names. So `authors` is supplied only by levels 1 and 4, and ADR 0028 Decision 1's
table is narrowed to that.

**1b. Keywords.** The `keywords` field is supplied by level 1 when the record has Scopus author or index keywords. Otherwise
level 4 supplies it when the record has OpenAlex topics, with source `openalex-topic`, the label ADR 0028 Decision 2 already
gives them. The topics are also written as `openalex-topic` links in `record_keywords`, as ADR 0030 specifies, whether or not
they fill the field. Keywords never conflict and take no manual entry, because no keyword kind exists for manual values.

**1c. Which records each level is asked about.**

- Level 2 runs over records whose affiliations are unsupplied after level 1.
- Level 3 runs over records whose affiliations are still unsupplied after level 2. It keys on `scopus_id` (ADR 0028 Resolved
  item 2), which every Scopus record carries in `record_id`.
- Level 4 runs over every record that has a DOI, and by title and year over records without one. It is not limited to blanks,
  because a conflict needs the OpenAlex value even where Scopus supplied the field. This is cheap: about 23 requests of 50 DOIs
  for 1,119 records.

**1d. Level-1 abstract input.** The abstract is not read from the Abstract Retrieval runs. The loader sets `records.abstract` from
the search entry's `dc:description` (`_record_from_entry` in `src/prismabib/store/load.py`), so the level-1 abstract is that
search payload's text. Its payload reference is the record's own `payload_file` and `payload_line`, the same address as
`source_payload_ref`. The sealed Abstract Retrieval runs under `raw/abstracts/` (`ABSTRACTS_DIRNAME`, table `abstract_runs`,
ADR 0018) feed subject areas only, and this record does not change that. Authors, affiliations and keywords at level 1 come from
`raw/<run_id>/` in the same way.

**1e. Levels 2 and 3 never set `abstract` or `keywords`.** Their results carry no abstract and no keywords (Decision 1a), so the
`abstract_*` and `keywords_*` columns are never written with a level 2 or 3 source. A level 2 or 3 affiliation is addressed by its
entry in the response, as described in Decision 3's list of reference forms. Each response is one JSONL line (ADR 0031 Decision 2
form), and a STANDARD response holds many DOIs, so the line number cannot address one record.

### 2. The merge rule, per record and per field

For each field, in this order:

1. The first Scopus level (1, then 2, then 3) that holds a value supplies the field. The automatic merge keeps that value, and no
   later automatic step replaces it.
2. Level 4 supplies the field only when no Scopus level did.
3. When a Scopus value and a level 4 value both exist and differ, a row is written to `provenance_conflicts` with both values and
   both payload references, with status `open`. Layer 1 keeps the Scopus value. The conflict is visible in the export and in the
   count, so the choice is not silent.
4. A reviewer may resolve an open conflict through a `conflict_resolution` event (below). Only that event can make the
   level 4 value, or a reviewer's manual value, replace a Scopus value. The owner decided this on 2026-11-02, and it amends the
   "never overrides" wording of the owner decision 2 above for this one path only.
5. Level 5 fills the field only when levels 1 to 4 left it unavailable.
6. A field with no value after level 5 is `unavailable`.

**Conflict resolution event.** Appended to `decisions/conflict_resolutions.jsonl`, a Layer 2 log beside `manual_entries.jsonl`,
through the same append-only machinery (ADR 0024). Fields: `event_id` (ULID), `schema_version` (1), `action`
(`conflict_resolution`), `ts` (UTC), `project`, `reviewer`, `record_id`, `field` (`abstract`, `authors` or `affiliations`; keywords
never conflict), `chosen_side` (`scopus`, `openalex` or `manual`), `value` (required only when `chosen_side` is `manual`, in the
shape of Decision 4's `manual_entry` values), `scopus_digest` and `openalex_digest` (64 lowercase hex characters each), and `note`
(optional).

- **Digests.** Each digest is the SHA-256 of the UTF-8 bytes of the stored JSON text of that side's value in `provenance_conflicts`
  (`scopus_value` or `openalex_value`), which is already the canonical serialisation of Decision 2. The writer computes both from
  the store, so the event records exactly what the reviewer saw.
- **Conflict key:** `(record_id, field)`. One conflict row exists per key.
- **Reversal:** a `reverse` action names the `conflict_resolution` event by `event_id`, with a reviewer and a reason from
  `wrong_side`, `wrong_value` or `other`, as registrations are reversed (ADR 0029 Decision 7). Nothing is deleted.
- **Fold rule (mirrors the manual-entry fold of Decision 4).** At most one active `conflict_resolution` per conflict key. An
  active one is one that no `reverse` names. If two are active for one key, the build fails with a `StoreError` that names both,
  and reversing one unblocks it. A reversal leaves the key `open` until a new resolution is written.
- **Orphaned (mirrors `manual_superseded`'s non-failing rule).** An active resolution whose `(record_id, field)` has no conflict row in
  the build is not applied. It is counted as `conflict_resolution_orphaned` in the build report and does not fail the build.
- **Stale (never silent).** At build time, an active resolution is applied only when the digest of each side it names matches the
  current stored value: `scopus_digest` for `scopus` and `openalex_digest` for `openalex`, and both for `manual`, since a manual value
  is chosen over both sides. If a digest differs, the resolution is not applied, the row stays `open`, and the build reports
  `conflict_resolution_stale` with the count. A stale resolution is never applied silently.
- **Effect on Layer 1, when applied:** the chosen side's value and its provenance (source, level, outcome, payload reference) replace
  the record's column values, and the `provenance_conflicts` row gains `status` `resolved`, `resolved_side` and
  `resolution_event_id`. `chosen_side` `scopus` changes no value and only marks the row resolved. `chosen_side` `openalex` takes the
  `openalex` value of the row. `chosen_side` `manual` takes the reviewer's value with source `manual`, level 5 and the payload
  reference `decisions/conflict_resolutions.jsonl#<event_id>`.
- **Writer refusals (PR6).** The writer refuses, and writes nothing, in these five cases: the key has no `open` conflict row in the
  store; another active resolution exists for the key; `chosen_side` is `manual` and `value` is blank; the field is not one of the
  three; the `chosen_side` is not one of the three values. The writer computes both digests from the store itself. A stale store
  therefore yields a resolution that the build marks stale, and the build, not the writer, makes that check.
- **Rebuilt tables:** for `authors`, the loader rewrites the record's `record_authors` rows from the chosen value, with `author_id`
  `resolved:<event_id>:<n>` (the position `n`, from 1), the surname as the comparison surname and `given_name` NULL. For `abstract`,
  `records.abstract` takes the chosen text. For `affiliations`, the rewrite depends on the side:
  - `chosen_side` `scopus`: no rewrite. The Scopus rows stay as they are.
  - `chosen_side` `openalex`: the rows are written with `afid` `resolved:<event_id>:<n>` and the institution name from the value.
    `country_iso3` is the ISO alpha-3 of the institution's `country_code` in the OpenAlex body named by `openalex_payload_ref`, mapped
    through `ALPHA2_TO_ALPHA3` (Decision 2, country form). An institution with no `country_code`, or an unmapped one, gets NULL. City is
    NULL. The loader matches each name to its institution in that body. This is a PR3 implementation check, and a test pins it.
  - `chosen_side` `manual`: each line of the value is one row. The minimal line shape is `Name` or `Name; City; Country`. `Country`
    is a country name or an alpha-3 code, mapped through `normalise_country`; a value it does not match gives `country_iso3` NULL.
    `City` and `Country` are NULL when the line has only a name. Ids are `resolved:<event_id>:<n>`.
  The consequence is that country-based analyses follow the chosen side: after an `openalex` resolution, a record's countries come
  from OpenAlex, and after a `scopus` resolution they come from Scopus.
- **Layer 0 is untouched:** the Scopus value stays in its Scopus payload, and the OpenAlex value stays in its sealed run.

**Comparison (fixed rule).** The normal form of a string is `unicodedata.normalize("NFKC", s)`, then `casefold()`, then runs of
whitespace collapsed to one space, then stripped. The Unicode database used is the one of the Python interpreter that runs the
build. The build report records `unicodedata.unidata_version`, and a test pins the expected version and fails if a different
one is used. Each field is compared as follows:

- **abstract:** the normal form of the whole text.
- **authors:** the sequence of surnames, in source order. A value of the form `Surname, Given` (manual entry) takes the text
  before the first comma. Any other value (an OpenAlex display name, `Given Surname`) takes its last whitespace-separated token.
  Scopus surnames are compared as stored. Particles such as "de la" are not split, so a name with a particle is compared by its last
  token and may disagree with a Scopus surname. Such a disagreement is a conflict, and it is shown, not hidden.
- **affiliations, when the Scopus side is level 1:** the set of normal-form institution names.
- **affiliations, when the Scopus side is level 2 or 3:** the set of ISO 3166-1 alpha-3 country codes only. Level 2 and 3
  affiliations carry city and country and no reliable institution name (ADR 0028 Consequence 4), so they are never compared by
  name. A conflict arises only when the two country sets differ.

**Country canonical form: ISO 3166-1 alpha-3.** This is the form `Affiliation.country` already stores. The existing code is
`normalise_country(raw: str) -> tuple[str, bool]` in `src/prismabib/countries.py`, which returns `(value, matched)`: the alpha-3
code and `True` on a hit, or the raw string unchanged and `False` on a miss. It looks the string up in the pinned `_COUNTRY_TABLE` (the ISO short names,
`_ALIASES`, and an identity entry for every alpha-3 code). Scopus country names therefore reach alpha-3 through that function, as
the model validator already does. OpenAlex supplies alpha-2 codes (`country_code`), and `normalise_country` does not map them. PR3
adds a pinned `ALPHA2_TO_ALPHA3` table beside `_COUNTRY_TABLE`, and a test checks that every target is a value of
`_ISO_SHORT_NAMES`. For comparison only, a value for which `normalise_country` returns `matched` as `False`, or an alpha-2 code missing from the table,
becomes the literal `unmapped`. The stored value keeps the raw string, as today. An `unmapped` member is removed before the sets are
compared, so it never causes a conflict. If one side is empty after removal, there is no conflict. Tests pin one unmapped name and
one unmapped alpha-2 code to this rule.

OpenAlex abstracts are rebuilt from an inverted index, so punctuation and hyphenation differences may appear. That makes conflicts
more frequent than real disagreement. Risk 4 covers it, and PR3 measures the rate before any count is published.

**New table.** `provenance_conflicts` (added to `store/schema.sql` and to `_TABLE_SORT_KEYS`, which the golden checksums use).
Every value column holds a JSON text, written with `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`.
Abstracts are a JSON string. Authors are a JSON array of the source-order strings. Affiliations are a JSON array of names, sorted
by their normal form and unique. A level 2 or 3 affiliation value is the JSON array of its country codes, sorted and unique.

```sql
CREATE TABLE provenance_conflicts (
  record_id TEXT, field TEXT,
  scopus_level INTEGER CHECK (scopus_level IN (1, 2, 3)),
  scopus_source TEXT,
  scopus_payload_ref TEXT, scopus_value TEXT,
  openalex_payload_ref TEXT, openalex_value TEXT,
  status TEXT NOT NULL CHECK (status IN ('open', 'resolved')),
  resolved_side TEXT CHECK (resolved_side IN ('scopus', 'openalex', 'manual')),
  resolution_event_id TEXT,
  PRIMARY KEY (record_id, field),
  CHECK ((scopus_level, scopus_source) IN ((1, 'scopus-complete'), (2, 'scopus-standard'), (3, 'scopus-meta'))),
  CHECK ((status = 'open' AND resolved_side IS NULL AND resolution_event_id IS NULL)
    OR (status = 'resolved' AND resolved_side IS NOT NULL AND resolution_event_id IS NOT NULL))
);
```

`status` is `open` until a reviewer resolves the conflict, and it is `resolved` once the fold applies a `conflict_resolution`.
The two CHECKs keep the status and the resolution columns consistent.

### 3. Provenance lives as columns on `records`, rebuilt from Layer 0

**Column names.** For each `<field>` in `abstract`, `authors`, `affiliations`, `keywords`, `records` gains four columns:

| Column | Type | Closed set, or content |
|---|---|---|
| `<field>_source` | TEXT, NOT NULL | The allowed set per field, below |
| `<field>_level` | INTEGER, NOT NULL | 0 to 5: the supplying level, fixed by the source (see the CHECK); 0 when the field is unavailable |
| `<field>_outcome` | TEXT, NOT NULL | `value` or `unavailable` |
| `<field>_payload_ref` | TEXT, nullable | The payload address, in the forms listed below. NULL when unavailable |

The payload reference forms, one per source. Levels 1 to 4 are relative to `raw/`. Level 5 is the one exception, and it is
project-relative:

- level 1: `<run_id>/page-NNNN.jsonl#<line>`, the record's `payload_file` and `payload_line`. The name `page-NNNN.jsonl` is the
  Scopus search page name that the writer uses (`capture/writer.py`) and that `RunManifest.payload_files` lists (`capture/manifest.py`).
  `<line>` is the record's 0-based line index in that page, as the module docstring of `store/load.py` describes.
- level 2 and 3: `scopus-cascade/<run_id>/response-NNNN.jsonl#<scopus_id>`. The `<id>` addresses an entry inside one response body,
  because a STANDARD response holds many DOIs and each response is one line. `<scopus_id>` is the bare numeric Scopus id, the
  part after `scopus:` in the record id. If an entry carries no Scopus id, `<id>` is `doi:<normalised DOI>` (`normalise_doi`). The
  writer emits the id from the entry it parsed, in body order, and it fails loudly when two entries of one response carry the same
  id, so an id is unique per record per response file. A META response holds one entry, addressed the same way.
- level 4: `openalex/<run_id>/response-NNNN.jsonl#<openalex_id>` (ADR 0031 Decision 2).
- level 5: **the exception.** `decisions/manual_entries.jsonl#<event_id>` is relative to the project directory, not to `raw/`.
  The file sits beside `decisions/registrations.jsonl`, the shared Layer 2 log location of ADR 0029 Decision 8. A value that a
  reviewer enters through a `conflict_resolution` with `chosen_side` `manual` has the same exception, as
  `decisions/conflict_resolutions.jsonl#<event_id>`. No other form is project-relative.

Allowed `<field>_source` values per field: `abstract`: `scopus-complete`, `openalex`, `manual`, `unavailable`.
`authors`: `scopus-complete`, `openalex`, `manual`, `unavailable`. `affiliations`: `scopus-complete`, `scopus-standard`,
`scopus-meta`, `openalex`, `manual`, `unavailable`. `keywords`: `scopus-complete`, `openalex-topic`, `unavailable`. Keywords take
no manual value.

So the new columns are `abstract_source`, `abstract_level`, `abstract_outcome`, `abstract_payload_ref`, and the same four for
`authors`, `affiliations` and `keywords`: 16 columns in all. The closed sets are enforced by CHECK constraints, in the form ADR
0030 Decision 3 uses for `record_keywords.kind`. The constraints for one field are shown for `abstract`; the other three follow
the same pattern with their own source sets. Each `CHECK` is one table-level constraint, and the schema file wraps them.

```sql
CHECK (
  (abstract_source, abstract_level) IN (
    ('scopus-complete', 1), ('openalex', 4), ('manual', 5), ('unavailable', 0)
  )
  AND ((abstract_outcome = 'unavailable' AND abstract_payload_ref IS NULL)
    OR (abstract_outcome = 'value' AND abstract_payload_ref IS NOT NULL
        AND abstract_source <> 'unavailable'))
)
```

The `value` and `unavailable` rows are tied together so that an unavailable field has level 0 and no payload reference, and a
value has level 1 to 5 and a reference. The keywords pair list is `('scopus-complete', 1)`, `('openalex-topic', 4)` and
`('unavailable', 0)`, and the same outcome clause applies.

**The literal `unavailable`.** It is written in `<field>_source` and `<field>_outcome` for every field with no value after level 5.
Exports and views never show a blank for these columns. An empty `<field>_payload_ref` is exported as the literal `unavailable`.

**Why the outcome set is two values here.** `<field>_outcome` records the final state of the field. The attempt outcomes `absent`,
`not_found` and `refused` are per-level facts. They are already stored per identifier in the Layer 0 manifests (ADR 0031
Decision 4) and are not repeated in Layer 1. The gap against ADR 0028 Decision 2, which asks for each earlier outcome in the
provenance map, is recorded as an amendment of that decision. The owner confirmed on 2026-11-02 that the trail stays in Layer 0
only (Questions put to the owner, item 1).

**Rebuild.** The loader computes all 16 columns, and the conflicts table, from Layer 0 and from the Layer 2 logs
`decisions/manual_entries.jsonl` and `decisions/conflict_resolutions.jsonl`. Nothing is
kept only in memory. A rebuild from the same runs and the same log must give identical checksums (Verification).

**Which sealed run wins.** An identifier can have outcomes in several sealed runs, of OpenAlex or of the cascade kind. For each
identifier and field, the value is taken from the latest sealed run, by run id, whose outcome for that field is `value`. If no
sealed run holds a value, the latest outcome is used, and the field is `unavailable`. This applies ADR 0031 Decision 7 to every
run kind in this record. A newer `absent` or `not_found` never replaces an older `value`.

**Registered records.** `records_all` (a view, not checksummed) gains the same 16 columns, in the same order as in `records`:
`abstract_source`, `abstract_level`, `abstract_outcome`, `abstract_payload_ref`, then the four `authors_*`, the four
`affiliations_*`, and the four `keywords_*`. The registered branch of the view selects them as literals, from its own row:

- `abstract_*`: `abstract_source` is `openalex` and `abstract_level` is 4 when `abstract` is not NULL, else `unavailable` and 0.
  `abstract_outcome` is `value` or `unavailable` to match. `abstract_payload_ref` is `payload_file || '#' || openalex_id` when
  the abstract is present, else NULL.
- `authors_*`: `authors_source` is `openalex` and `authors_level` is 4 when `registered_records.authors_json` holds at least one
  name, else `unavailable` and 0. `authors_payload_ref` is `payload_file || '#' || openalex_id` when present, else NULL.
  `authors_outcome` matches.
- `affiliations_*`: the same rule, from `affiliations_json`, with source `openalex`, level 4.
- `keywords_*`: the same rule, from `keywords_json`, with source `openalex-topic`, level 4.

The scopus branch of `records_all` selects the 16 columns from `records` in the same order.

**Registered values (owner answer 2).** `registered_records` (ADR 0032) gains three columns: `authors_json`, `affiliations_json`
and `keywords_json`, each a JSON array of text or NULL. They are derived at load time from the registration's own OpenAlex body
(the `payload_file`), by the functions the OpenAlex loader already uses: `authorships_to_fields` for the authors and the
affiliation names, and `topics_to_keywords` for the keywords (`src/prismabib/sources/openalex.py`). The derivation runs in
`_fold_registrations` beside `derive_registration` (`src/prismabib/capture/registrations.py`), which already reads that body. Authors
are OpenAlex display names in source order, as the comparison of Decision 2 reads them. A body that lacks a field gives an empty
array, which is stored as NULL and read as `unavailable`. The source is `openalex`, not a new `registration` label: the values come
from OpenAlex, and the reviewer's act is only the choice of the work. A later Scopus match supersedes the registration (ADR 0029
Decision 6), so the registered row is dropped and the Scopus record's own provenance applies.

**Consequences for the schema.** `records` is named in the frozen BUILD_PLAN clause that `schema.sql` reproduces, and a column
added there is a deviation. This record is the ADR for it, as ADR 0030 Decision 3 was for `record_keywords`. The new table
`provenance_conflicts` is an addition of the kind ADR 0012 and ADR 0013 made.

**Migration: none.** The store is a build artefact, rebuilt from Layer 0 (`store/db.py`). A store built before this change has no
provenance columns. `prismabib build --rebuild` produces the new columns, and the build report says that the previous store had none.

### 4. Manual entry (level 5)

**Event.** A new Layer 2 log, `decisions/manual_entries.jsonl`, beside `decisions/registrations.jsonl`. It uses the
append-only, checksum-guarded machinery of ADR 0024 (`prismabib.log.AppendOnlyLog`), and writes no other file.

| Field | Content |
|---|---|
| `event_id` | A ULID, as for registrations (ADR 0029 Decision 2) |
| `schema_version` | `1` |
| `action` | `manual_entry` |
| `ts` | UTC time of the write |
| `project` | The project slug |
| `reviewer` | The named reviewer (required) |
| `record_id` | An existing `scopus:` record id. A registered record is not eligible in this version. |
| `field` | `abstract`, `authors` or `affiliations` |
| `value` | Text. Authors: one per line, `Surname, Given`. Affiliations: one per line, `Name` or `Name; City; Country` (the shape of Decision 2, Rebuilt tables). Line endings are stored as `\n`. |
| `doi` | The DOI page opened, or NULL when the record has none |
| `note` | Optional |

**Reversal.** Consistent with registrations (ADR 0029 Decision 7). A `reverse` event names the `manual_entry` by `event_id`,
with a reviewer and a reason from a closed set: `wrong_value`, `wrong_field`, `duplicate_entry`, `other`. Nothing is deleted.
The reversed entry is not applied.

**Writer refusals.** `capture/manual.py` raises `ManualEntryRefusal` and writes nothing in these five cases, and only these:

1. the record id is not an existing `scopus:` record;
2. the field is not one of `abstract`, `authors`, `affiliations`;
3. the value is blank after stripping;
4. the field is not `unavailable` in the current Layer 1 store;
5. an active `manual_entry` already exists for that record and field.

Case 4 reads the store. A stale store can pass it, so the fold (below) applies the same rule again at build time.

**Fold rule (implemented in PR3, not PR6).** The loader runs levels 1 to 4 first. Then, for each `(record_id, field)` that is
still `unavailable`, it applies the single active `manual_entry`. An active entry for a field that levels 1 to 4 supplied is not
applied. It is counted in the build report as `manual_superseded` and is not a numbers key. An active entry whose `(record_id, field)`
names no record in the build is not applied either. It is counted as `manual_orphaned` and does not fail the build. Two active
entries for one `(record_id, field)` (the race that case 5 cannot prevent between two writers) fail the build with a `StoreError`
that names both. Reversing one unblocks it, as ADR 0029 does for registrations. The conflict-resolution fold (Decision 2) follows the
same rules for two active resolutions and for orphans. Its counters are `conflict_resolution_orphaned` (no conflict row for the key)
and `conflict_resolution_stale` (a digest mismatch, Decision 2). A stale resolution is never applied, so the build report names it.

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
| `methods.cascade.<field>.conflicts` | int | Records with a row in `provenance_conflicts` for that field, open or resolved (always 0 for keywords) |
| `methods.cascade_conflicts_open` | int | Conflict rows with `status` `open`, all fields together |
| `methods.cascade_conflicts_resolved` | int | Conflict rows with `status` `resolved`, all fields together |
| `methods.cascade_sentence` | str | Always present. The methods sentence below. |

`<field>` is `abstract`, `authors`, `affiliations` or `keywords`. That is 4 fields times 7 integer keys, which is 28 integer
keys, plus two integer totals and one string, 31 keys in all. Every key is always present, with zero where nothing happened, so
the key set does not depend on which levels ran.

**Counts follow the current columns, over Scopus records.** The level and manual counts are taken from the `records` columns after
resolution, and they cover Scopus records only. A registered record is not counted here. A record whose abstract was resolved to
the OpenAlex value counts under `level4`, not `level1`. `conflicts` equals `conflicts_open` plus
`conflicts_resolved` summed over the fields.

**Consuming sentence.** `methods.cascade_sentence` is built from the counts by a pure function in `report/methods.py`, for example:

> Abstracts were taken from Scopus COMPLETE for {level1} records, from OpenAlex for {level4} and entered manually for {manual};
> {unavailable} have no abstract. ...

with the same clause for each field, and the conflict count after each field's clause. The last sentence states the two totals:
"{conflicts_open} conflicts remain open and {conflicts_resolved} were resolved by a reviewer."

**Key counts.** Required keys stay at 41. Optional keys go from 3 to 34 (the 3 existing, plus the 31 new). Total keys go from 44
to 75. The count that `docs/how-to/run-the-demo.md` states must move to match, and so must the optional-key list in its paragraph
on `fill`. The sample output in that file that prints `numbers.json keys 33` is stale already and is corrected in the same pull
request. The 75 is the count after PR7 with the three existing optional keys still present. `registered_records` and the
provenance columns add no numbers key.

**Goldens that change.** `tests/golden/report/__snapshots__/numbers_keys.json` gains the 31 keys. `reference_numbers.json` gains
their values. The values are not zero in general, because level 1 counts what the reference corpus already holds. Each change is
named in the pull request that makes it.

### 6. Scopus Layer 0 for levels 2 and 3

**Run kind.** `raw/scopus-cascade/<run_id>/`, with `response-NNNN.jsonl` per HTTP response, a `progress.json` resumption sidecar
deleted on seal, and `manifest.json` as the seal. The name is added to `layout.NON_RUN_DIRNAMES`, as ADR 0031 Decision 8 does
for `openalex`. The run ids come from `layout.new_run_id()`, and every write goes through `guard_writable`.

**Manifest.** `CascadeRunManifest`, a new model in `capture/manifest.py`, not the frozen `RunManifest`. PR2 owns this model and its
writer. PR3 is stacked on PR2 and imports the model and the `NON_RUN_DIRNAMES` name from it. This section is the contract both
satisfy. Its top-level fields, all required unless marked:

- `run_id` (str), `started_at`, `finished_at` (UTC datetimes), `endpoint` (str, the base URL only), `client_version`,
  `criteria_version` (str).
- `fallback_opt_in` (bool). Required to be `true` for any request in this run kind, and a sealed run with `false` is refused.
- `api_key_used` (bool). Never the key.
- `budget_requests` (int, from `--scopus-budget`), `requests_made` (int), `stop_reason` (`complete`, `budget` or `quota`).
- `requests` (list), in request order. Each entry:
  - `index` (int), `file` (str, `response-NNNN.jsonl`, with `NNNN` the zero-padded index), `sha256` (str), `encoding`
    (`verbatim` or `compact`, as ADR 0031 Decision 2), `raw_sha256` (str or null, set when compact).
  - `view` (`STANDARD` or `META`), `identifiers` (list of str: DOIs for `STANDARD`, Scopus ids for `META`).
  - `http_status` (int), `retrieved_at` (UTC datetime), `quota_remaining` (int or null, from the header).
  - `outcomes` (list). One entry per identifier of the request, each with `identifier` (str), `outcome`, `http_status` (int),
    and `affiliations` (int, the count of affiliations returned, zero when none).
- `payload_files` (list of str), `payload_sha256` (str), the digest of the stored bytes in request order.

**Outcome set for a cascade identifier.** `value`: a 200 whose entry is present and has at least one affiliation. `absent`: a 200
whose entry is present with zero affiliations. This is a real answer, not a miss, and it triggers level 3. `not_found`: a 200
with no entry for the identifier, or a META 404. `refused`: a 401 or 403 in an opt-in run. A batch of identifiers that is not
present in the 200 body gets `not_found`, one outcome per identifier. `absent` is stored in the manifest, computed when the run is
written, as ADR 0031 Decision 4 stores its own outcomes.

**Level 3 targets.** Records whose affiliations are not `value` after level 2: `absent`, `not_found` and `refused` all qualify.

**Opt-in.** A run is created only with `--opt-in`. A 401 or 403 in such a run is recorded as `refused` for the affected identifiers,
and the run continues (ADR 0028 Resolved item 1, as amended below). Outside an opt-in run the existing `AuthError` and
`EntitlementError` behaviour is unchanged.

**Budget and quota.** `--scopus-budget N` caps the requests made in the run, and it is required with `--opt-in`. When the cap is
reached, the run stops, writes `stop_reason: budget`, and seals. A weekly-quota 429 (`QuotaExceededError`) stops the run the same
way, with `stop_reason: quota`. An identifier that was not attempted has no outcome, so it is not held, and the next run asks for
it (Decision 7 reuse rule). A `--dry-run` prints the planned requests per level before any call is made.

**Reuse.** An identifier is held when a sealed run of this kind holds a `value` or `not_found` for it. A `refused` outcome does not
hold it. This is the rule ADR 0031 Decision 7 gives.

**Loader (implemented in PR3, not PR2).** PR2 writes no `load.py` code. PR3 adds `_sealed_cascade_run_dirs` to `store/load.py`,
which lists the sealed children of `raw/scopus-cascade/`, as ADR 0031 Decision 8 does for OpenAlex. Attachment is by `scopus_id`
for `META` and by normalised DOI for `STANDARD`. The loader recomputes every outcome from the stored bytes and fails on disagreement,
as ADR 0031 Decision 4 does. A malformed body becomes a `malformed_entries` row (ADR 0012). A sealed run with `fallback_opt_in`
false fails the build with a `StoreError`.

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
- The docstrings that say the view "never degrades" are corrected in PR1, in `src/prismabib/sources/scopus.py`,
  `src/prismabib/capture/enrich.py`, `src/prismabib/capture/manifest.py` (line 157) and `src/prismabib/capture/writer.py` (lines 351
  and 381). PR1's phrase test searches `docs/` and `src/prismabib/` for the phrases and excludes `docs/architecture/adr/`, because
  frozen ADRs and this record quote them.
- `docs/how-to/register-and-reverse-candidates.md` says to call `capture_openalex` by hand. That sentence is replaced by the
  command in PR4.
- A dated pointer note is appended to **ADR 0031, Decision 9** in PR3. It says the loader computes abstract, authors and affiliations
  from the stored bodies from that pull request, and that it did not before. It edits no older text.
- A dated pointer note is appended to **ADR 0028**, in PR3. It points to this record for the amendments below. ADR 0028 is not
  edited in place.

**Amendments to ADR 0028 (stated here, applied by the notes above).**

- **Decision 1 (the level table).** Levels 2 and 3 supply `affiliations` only, not `authors` (Decision 1a). Level 1 supplies the
  abstract from the search payload (Decision 1d).
- **Decision 2 (the provenance map).** The map is held as the columns of Decision 3, which is its column form. The per-level
  attempt outcomes are not repeated in Layer 1. Keyword provenance keeps the `openalex-topic` label, as Decision 2 already states.
- **Resolved item 4 (opt-in).** The opt-in for a cascade run is recorded in `CascadeRunManifest.fallback_opt_in`, not in
  `RunManifest`. The frozen Scopus `RunManifest` has no such field, and this record does not add one.

### 11. Out of scope: the sealed runs already on disk

The sealed runs already under `projects/Baseball-CVPR/raw/` are not supported by this design. That covers the Scopus acquisition
run, the abstract runs, and the two OpenAlex runs (`20261023T013520Z-d69e4d0f` and `20261026T084313Z-ae7e7c35`). The loader and
manifest changes may assume that every OpenAlex and cascade run was produced by the new commands. No migration or compatibility code
is written for runs of the old shape. Nothing in this record deletes them. PR8 starts from scratch: every level is captured anew with
the new cascade command, and the store is rebuilt from those new runs.

## Alternatives considered

- **A child table `record_field_provenance`, one row per record and field.** Rejected for the reason in Decision 3. It is a join for
  every export and every methods sentence, and the owner chose columns.
- **Let OpenAlex override Scopus where they disagree, automatically.** Rejected. It changes Scopus data without a trace. A reviewer
  may still choose the OpenAlex value, but only through a `conflict_resolution` event (Decision 2, rule 4).
- **Silently keep Scopus in the conflict case, with no list.** Rejected. It is a silent choice, which the owner ruled out.
- **Resolve conflicts by editing the Scopus payload or deleting the Scopus value.** Rejected. Layer 0 is immutable, and the
  resolution is an event, so the Scopus value stays available to every reader.
- **Store the registered authors, affiliations and keywords as rows in `authors` and `affiliations`.** Rejected for registrations.
  Those tables are keyed to Scopus records, and a registered record is not one. The JSON columns on `registered_records` keep the
  values where the registration is stored.
- **Manual entry from the screening view.** Rejected. Entering a field is not a decision (ADR 0003), and the screening view's keys
  and list are out of scope (ADR 0035 Decision 9).
- **Plain `cascade.*` numbers keys.** Rejected for the reason in Decision 5.
- **Levels 2 and 3 supply `authors` from their first author.** Rejected (Decision 1a).

## Consequences

1. Every field in every export says which level supplied it, or says `unavailable`. A reader can tell an absent value from an
   unasked one.
2. The `records` checksum changes for every store, because each row now carries 16 more cells. `registered_records` changes too,
   by its three JSON columns (Decision 3). The reference table checksums change in PR3, and `reference_numbers.json` in PR7.
   Each changed file names the cause.
3. The store is rebuilt, not migrated (Decision 3). A store built before this change has no provenance, and the build report says so.
4. Conflicts make the methods more honest and make the record list longer. Open conflicts and resolved conflicts are reported
   separately, and both are numbers a manuscript may cite.
5. Levels 2 and 3 add Scopus requests. `--scopus-budget` is always given explicitly (no default value), and the dry run shows the
   cost before it is spent.
6. A manual value is a human step, so it is slow and it can be wrong. It is labelled, it is never the default, and a reversal
   removes it from the fold.
7. A reviewer's `conflict_resolution` is the only path by which an OpenAlex or manual value replaces a Scopus value (Decision 2,
   rule 4). Every such replacement is an event with a reviewer, a time and a note, and it is reversible.
8. Registered records now show real author, affiliation and keyword provenance, taken from their OpenAlex body, in `records_all`.
   The numbers keys of Decision 5 count Scopus records (the `records` table) only, so registrations add no number to them.

## Verification

Every test is shown to fail on the defect it guards, following the rule ADR 0031 Verification sets. Each pull request runs
`pytest -n auto` as CI does, and once single-process. It runs `mypy --strict` on `src/prismabib` at Python 3.11, and `ruff`.

- **Rebuild determinism.** `build_store(rebuild=True)` twice on the same runs and the same log gives identical table checksums,
  including `records` and `provenance_conflicts`. Tests run on Windows and Linux.
- **Merge rule.** A Scopus value is never replaced by OpenAlex by the automatic merge, and only a reviewer's `conflict_resolution`
  event may replace it (Decision 2, rule 4). OpenAlex fills a blank. A disagreement creates one conflict row
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
- **Conflict resolution (PR3 fold, PR6 writer).** Each test fails on the defect it names:
  - a `resolved` row takes the chosen side's value and provenance, and the Scopus payload file is byte-identical before and after
    the build;
  - a `chosen_side` `openalex` resolution changes the count from `level1` to `level4` for that field;
  - a `reverse` of the resolution makes the row `open` again and restores the Scopus value, and the previous active resolution, if
    any, becomes effective;
  - two active resolutions for one key fail the build with a `StoreError` that names both, as for manual entries;
  - a resolution for a key with no conflict row is not applied and is counted as `conflict_resolution_orphaned`, and the build
    does not fail;
  - a resolution whose recorded `scopus_digest` or `openalex_digest` differs from the stored value is not applied, the row stays
    `open`, and the build reports `conflict_resolution_stale`. This test fails if the stale resolution is applied;
  - the digests are the SHA-256 of the stored JSON text, so a value that changes by one character is detected;
  - the CHECK constraints refuse `status` `resolved` without `resolution_event_id`, and `open` with one;
  - the writer refuses a `manual` side without a value, a resolution for a key with no open conflict row, and a second active
    resolution for a key;
  - the affiliations rewrite follows the chosen side (Decision 2, Rebuilt tables): `openalex` gives `country_iso3` from the body, and
    `scopus` leaves the rows unchanged;
  - the CLI and the Panel view call the same writer (a parity test, as for manual entry).
- **Registered provenance (PR3).** A registration whose body has an authorship list gives `authors_source` `openalex`, level 4, a
  non-NULL reference, and `authors_outcome` `value`. A body with an empty list gives `unavailable` in all four columns. A later
  Scopus match removes the registered row, and the Scopus record's own columns apply.
- **Key counts (PR7).** `numbers_keys.json` has 75 keys: 41 required, 34 optional. The fixture test asserts these three counts
  separately, so a count that drifts fails on the number, not only on the set.

## Implementation plan

Three batches. A later batch starts after the earlier batch is merged. Pull requests inside one batch are developed in parallel
where the dependency column allows it.

| Batch | PR | Size | Depends on | Owns (hotspots) |
|---|---|---|---|---|
| 1 | PR0 (this record) | S | none | `docs/architecture/adr/0041-*.md`, `mkdocs.yml` |
| 1 | PR1 (docs) | S | none | public docs and the docstrings listed in PR1 |
| 2 | PR2 (Scopus levels 2 and 3, Layer 0 writer) | M | PR1 | `capture/cascade.py` (writer), `capture/manifest.py` (`CascadeRunManifest`), `capture/layout.py` (`NON_RUN_DIRNAMES`) |
| 2 | PR3 (provenance columns, conflicts, resolution fold, manual fold, registered values), stacked on PR2 | L | PR2 (its branch base), PR0 | `store/schema.sql`, `models.py`, `store/load.py`, `store/checksums.py`, `countries.py` (`ALPHA2_TO_ALPHA3`), `capture/registrations.py` (a new derivation helper only; `_fold_registrations` in `store/load.py` calls it), the Project path constants for `decisions/manual_entries.jsonl` and `decisions/conflict_resolutions.jsonl`, the checksum golden, the ADR 0031 and ADR 0028 pointer notes, the ADR 0032 pointer note |
| 2 | PR5 (Scholar-paste entry point: CLI and candidate store) | S | PR1 only; independent of PR2 and PR3 | `capture/candidates.py`, one registration line in `cli.py` |
| 3 | PR4 (cascade and OpenAlex commands; runs candidates through the levels) | M | PR2, PR3, PR5 | `capture/cascade_commands.py` (new), one registration line in `cli.py` |
| 3 | PR6 (manual entry and conflict resolution: writer, CLI, GUI) | M-L | PR3 (event models and folds) | `capture/manual.py`, `capture/manual_ui.py`, `capture/manual_commands.py` (new), `capture/conflicts.py` (new: the resolution writer), one registration line in `cli.py`. Does NOT touch `store/load.py` or `models.py`. |
| 3 | PR7 (exports, methods, numbers keys, goldens, run-the-demo) | M | PR3 only; tests use fixture rows that carry the `manual`, `resolved` and `unavailable` values | `report/`, `numbers_keys.json`, `reference_numbers.json`, `docs/how-to/run-the-demo.md` |
| 4 | PR8 (fresh real run, CHANGELOG; sharing gate, see below) | S | all of the above | `CHANGELOG.md` |

Size: S is under 150 changed lines, M under 600, and L is larger.

**Stacking PR2 and PR3.** PR3 is stacked on PR2 under ADR 0037: its branch is created from PR2's branch. The train merges PR2 first,
then PR3. PR2's head is frozen once PR3 starts, and PR3 may import PR2's `CascadeRunManifest` and `NON_RUN_DIRNAMES` constant.
The squash-merge of PR2 into `main` is clean for PR3, for this reason: PR2 is frozen, so its squash commit has the same tree as
its last head. When the train updates PR3 onto `main`, the only changed lines are the ones PR2 already carries in PR3's base, and
the merge of identical changes is clean. Nothing in PR2 changes after PR3 starts, so the update does not reorder history.

**Additive hotspot: `src/prismabib/cli.py`.** Each PR puts its commands in its own module, and adds one registration line to
`cli.py`. No PR edits an existing command. A conflict on the registration lines is resolved by the train's update-branch step, in
train position order. PR4 and PR6 are both in batch 3, and they are ordered PR4 then PR6, so PR6's registration line lands after
PR4's. PR5 registers the `candidates` group, PR4 registers `cascade` and `openalex`, and PR6 registers `manual-entry` and
`manual-reverse`.

**Hotspot ownership.** `src/prismabib/store/schema.sql`, `src/prismabib/models.py` and `src/prismabib/store/load.py` change only in
PR3. `src/prismabib/cli.py` is additive, as above. The goldens and `docs/how-to/run-the-demo.md` key counts change only in PR7,
except the one checksum golden that PR3 regenerates (Risk 2). PR4 sits in batch 3, after PR5 in batch 2, because `cascade` runs
the candidate enrichment that PR5 makes possible. PR7 does not depend on PR6: its fixtures carry `manual` and `unavailable` rows
directly.

**Batch 1.**

- **PR0 (this record).** The ADR and its nav entry. Docs only, no code. Accepted when the owner approves the merge.
- **PR1 (docs).** Rewrites `limitations.md` and corrects the "never degrades" wording in the files listed in Section 10 (`scopus.py`,
  `capture/enrich.py`, `capture/manifest.py` line 157, `capture/writer.py` lines 351 and 381). Acceptance: `mkdocs build --strict`
  passes, and the phrase test below passes. Failing test first: a docs test that searches `docs/` and `src/prismabib/` for "no
  OpenAlex", "refuses to degrade" and "never degrades", excluding `docs/architecture/adr/`, and fails on `main`.

**Batch 2.**

- **PR2 (Scopus levels 2 and 3, Layer 0 writer only).** `capture/cascade.py` (the writer), `CascadeRunManifest` in
  `capture/manifest.py`, the `scopus-cascade` entry in `NON_RUN_DIRNAMES` in `capture/layout.py`, and the opt-in, budget and quota
  handling. It writes no `load.py` code, and it writes the files of Decision 6 exactly. PR2's head is frozen when PR3 starts. Acceptance: a sealed run for a fake client that returns 200 (with
  and without affiliations), 401, and 404, a budget stop, and a quota stop, each with the manifest shape of Decision 6. The live batch
  form is verified and the result is recorded in the PR. Failing tests first: refusal without opt-in writes no seal; a run with
  `fallback_opt_in` false is refused at seal; `NON_RUN_DIRNAMES` contains the name; the key is in no written file.
- **PR3 (provenance columns, conflicts, resolution fold, manual fold, registered values). Size L.** `store/schema.sql` (16 columns
  with the CHECKs of Decision 3, `provenance_conflicts` with `status` and the resolution columns, and `registered_records` with
  `authors_json`, `affiliations_json` and `keywords_json`), `models.py` (the `ManualEntry` and `ConflictResolution` event models of
  Decisions 4 and 2, each bound to `AppendOnlyLog`, with stub readers), `store/load.py` (the merge of Decision 2, the conflict rows,
  the `conflict_resolution` fold and its effect on the columns and the `authors` and `affiliations` rows, the `_sealed_cascade_run_dirs`
  discovery and its outcome check, the manual fold of Decision 4, and the call to the registered-value helper in `_fold_registrations`),
  `capture/registrations.py` (a new derivation helper for the registered values, no writer change), `project.py` (the path constants
  and properties for `decisions/manual_entries.jsonl` and `decisions/conflict_resolutions.jsonl`; the conflict filename constant sits
  beside `REGISTRATIONS_FILENAME`), `countries.py` (`ALPHA2_TO_ALPHA3`), the `records_all`
  view, `store/checksums.py`, and four pointer notes: ADR 0031 Decision 9, ADR 0028, and ADR 0032 (its `registered_records` table gains
  three columns). It is stacked on PR2 and imports PR2's `CascadeRunManifest` and `NON_RUN_DIRNAMES` name, and its tests may use both.
  Acceptance: the merge tests, the no-blank test, the rebuild-determinism test, the manual and conflict fold tests, the registered
  provenance tests, and the checksum golden regenerated with its cause named in the PR. Failing tests first: the no-blank test and
  the never-overrides test, both failing on the current `load.py`.
- **PR5 (Scholar-paste entry point).** Independent of PR2 and PR3. Adds `prismabib candidates add <slug> --reviewer R [--file PATH]`
  as a registration in `cli.py`, its command module `capture/candidate_commands.py`, and the candidate store writes through the
  existing `parse_pasted_rows` and `CandidateLog.add`. It runs no HTTP request: enrichment is PR4's. Acceptance: a pasted row writes
  one candidate event and no `records` row. Failing tests first: a paste
  creates no record.

**Batch 3.**

- **PR4 (cascade and OpenAlex commands).** Depends on PR2, PR3 and PR5. Adds `prismabib cascade` and `prismabib openalex` in
  `capture/cascade_commands.py`, and runs candidates through the levels in the order of Decision 9: first level 4 by title and year
  to obtain a DOI, then level 2 by that DOI, then level 3 by the `scopus_id` that level 2 returned. It also adds the report tables,
  the exit codes, and the replacement sentence in `register-and-reverse-candidates.md`. Acceptance: the idempotence test, the
  dry-run test that makes no call, and the budget-required-with-opt-in test. Failing tests first: a second call makes no request.
- **PR6 (manual entry and conflict resolution: writer, CLI, GUI). Size M-L.** Depends on PR3 for the event models and the folds.
  Adds `capture/manual.py` (the manual writer, the five refusals, and its reverse writer), `capture/conflicts.py` (the
  `conflict_resolution` writer, its refusals and its reverse writer), `capture/manual_ui.py` (the manual form and the conflict review
  panel, which lists open conflicts with both values and the two `chosen_side` buttons), `capture/manual_commands.py` with the CLI
  commands `prismabib manual-entry`, `prismabib manual-reverse`, `prismabib conflict-resolve` and `prismabib conflict-reverse`, and
  one registration line in `cli.py`. The writers use the path properties and the event models that PR3 adds, so PR6 owns the writer
  code and PR3 owns the constant, the models and the folds. It does not touch `store/load.py`, `models.py` or `project.py`.
  Acceptance: the five manual refusals, the conflict writer refusals listed in Verification, the reversal, the GUI and CLI parity
  tests, and the disabled button for a DOI-less record. Failing tests first: a manual value for a supplied field is refused by the
  writer, and a `chosen_side` `manual` without a value is refused.
- **PR7 (exports, methods, numbers keys, goldens, run-the-demo). Size M.** Depends on PR3 only. Its tests use fixture rows that carry
  `manual`, `resolved` and `unavailable` values in the columns PR3 adds, so it does not wait for PR6. The export columns (the 16
  provenance columns of `records`, in the records export), and a new conflicts export, `provenance_conflicts.csv`, written next to the
  records export: one row per `provenance_conflicts` row, with `status`, `resolved_side` and `resolution_event_id`, so a resolved
  conflict is visible as resolved and an open one as open. `methods.cascade_sentence`, the 31 new keys (28 per-field integers, two conflict totals
  and the sentence), `numbers_keys.json` (75 keys: 41 required, 34 optional), `reference_numbers.json`,
  `docs/how-to/run-the-demo.md` (the key count, the optional list, and the stale `33`), and the `limitations.md` label text.
  Acceptance: the golden tests pass with the stated changes, and `fill` on the demo manuscript passes. Failing tests first: the
  key-set golden, which fails until the keys are added.
- **PR8 (fresh real run, CHANGELOG). Size S.** Every level is captured anew with the new commands on the real project, the store is
  rebuilt from those runs, and the numbers are reported in the CHANGELOG. The old runs are not used (Decision 11). The real project's
  data is not committed. **Sharing gate:** nothing from PR8 may be shared, and no Scopus payload may be committed, until the owner has
  confirmed the Elsevier terms (Questions put to the owner, item 4). This is a gate on the sharing step, not an implementation step:
  PR8 proceeds without sharing, and any shared sample waits for the confirmation.

## Risks

1. **Checksums.** The `records` checksum changes for every store (Consequence 2). The golden is regenerated only in PR3, and the
   change is explained there.
2. **Goldens.** The key set, the values and the reference checksums all change. PR7 owns the number goldens, and PR3 owns the checksum
   golden. A change without a named cause stops the pull request.
3. **Machine dependence.** NFKC normalisation and casefolding read the Unicode database of the Python interpreter. That database is
   fixed for a Python minor version, and it can differ between minor versions (for example 3.11 and 3.12). Two machines on different
   minor versions could give different conflict counts for the same store. Mitigation: the comparison is one documented function,
   tested against a fixed set of strings, and the build report records `unicodedata.unidata_version`. A test pins that version and
   fails when a different one is used. The project pins Python 3.11, so the pinned version is the one the test expects.
   Other sources of drift: path separators in `payload_ref` (always `/`), CRLF in manual text (stored as `\n`), dict order (every
   sort is explicit), and the clock (the loader reads none).
4. **Conflict noise.** OpenAlex abstracts come from an inverted index, so formatting differences look like disagreement. PR3 measures
   the conflict rate on the real data before any conflict count is cited. If it is high, the comparison rule is revisited in a new ADR.
5. **Rebuild determinism.** The latest-run rule (ADR 0031 Decision 7) and the manual fold must not depend on the order of file
   listing. Run ids sort by time, and the manual log and the conflict-resolution log are each read in file order. Both logs are Layer 2
inputs to the rebuild, beside each other in `decisions/`.
6. **Unverified batch form.** The multi-DOI `STANDARD` request is not live-verified. PR2 verifies it. If the batch form fails, level 2
   runs one DOI per request, which costs more quota.
7. **Quota.** An opt-in run can spend a weekly quota. The budget is required, and the dry run prints the cost first.
8. **Scopus data in shared outputs.** Level 1, 2 and 3 payloads and Scopus values are Scopus data. No shared sample and no committed
   file may contain them until the owner confirms the Elsevier terms (the sharing gate in PR8, item 4 below). `raw/` stays gitignored.
9. **Registered values.** Registered authors, affiliations and keywords come from the registration's OpenAlex body. A body that is
   sparse gives `unavailable`, which is correct but can look like a gap in the registered set. PR3's tests cover the empty and the
   populated case, and the methods say the registered values are OpenAlex values.
10. **Resolution side effects.** A `chosen_side` `openalex` resolution rewrites a record's author and affiliation rows with synthetic
    ids. Those ids are deterministic (`resolved:<event_id>:<n>`), so the rebuild reproduces them. The Scopus rows are not deleted from
    Layer 0, and a reversal restores the Scopus values. The rebuild-determinism test covers a resolution and its reversal.
11. **Critical path.** PR3 is the largest pull request (size L) and gates PR4, PR6 and PR7, so it sets the schedule. PR2's head freezes
    as soon as PR3 starts, so a defect found in PR2 after that point is fixed in PR3's base by a new PR2 head, not by editing the frozen
    one. Work that PR2 needs should therefore be finished before PR3 starts.
12. **Reviewer workload.** Conflicts must be resolved by a person. An open list that grows faster than it is resolved leaves the
    methods with open conflicts. The `conflicts_open` count makes that visible rather than hiding it.

## Questions put to the owner, and the answers

The owner answered on 2026-11-02. Each answer is applied in the sections named.

1. **Attempt trail.** Answer: **Layer 0 only**, as drafted. The attempt outcomes `absent`, `not_found` and `refused` stay in the
   Layer 0 manifests and are not a fifth column (Decision 3). This is confirmed, and it is the position ADR 0028 Decision 2 is
   amended to state (Section 10).
2. **Registered records.** Answer: **yes**, store authors, affiliations and keywords. Design (Decision 3, "Registered values"):
   `registered_records` gains `authors_json`, `affiliations_json` and `keywords_json`, JSON arrays or NULL. They are derived at load
   time from the registration's own OpenAlex body, with source `openalex` (keywords `openalex-topic`) and level 4, which is the
   faithful source. A body that lacks a field gives `unavailable`. A later Scopus match supersedes the registration (ADR 0029
   Decision 6), and the Scopus record's provenance then applies. ADR 0032 gets a dated pointer note in PR3.
3. **Conflict resolution.** Answer: **a reviewer resolves each conflict through a Layer 2 event**. Design (Decision 2): the
   `conflict_resolution` event in `decisions/conflict_resolutions.jsonl`, with the conflict key `(record_id, field)`, `chosen_side`
   (`scopus`, `openalex` or `manual` with a value), `reviewer`, `ts` and an optional `note`. It is reversible through a `reverse`
   event, as registrations are (ADR 0029 Decision 7), with the reasons `wrong_side`, `wrong_value` and `other`. The fold allows one
   active resolution per key, as the manual-entry fold does, and it applies a resolution only when its digests match. (The first
   draft of this answer said "latest non-reversed wins"; the owner's mirror rule of the manual-entry fold replaces that wording.)
   The effect on Layer 1 is the chosen side's value and provenance, and the conflict row gains
   `status` `resolved`. The Scopus value stays in Layer 0. The rule "never overrides" is amended for this one path only (Decision 2,
   rule 4). The model and fold are PR3's, and the writer, CLI and panel are PR6's. New keys: `methods.cascade_conflicts_open` and
   `methods.cascade_conflicts_resolved` (Decision 5). The counts are recomputed in Decision 5 and in Key counts.
4. **Sharing.** Answer: **allow if the terms permit**. This is conditional, and it is **not decided** that Scopus payloads may be
   shared. The owner must confirm the Elsevier terms before any shared sample includes Scopus level 1, 2 or 3 data. Until
   confirmed, shared samples and anything committed to git use no Scopus payloads, and `raw/` stays gitignored. This is a gate on
   PR8 and on the peer-evaluation step (the sharing of a sample to peers), not an implementation pull request. Risk 8 and PR8 carry it.
5. **Budget.** Answer: **always explicit**, as drafted. `--scopus-budget` has no default value and is given explicitly on every run
   that includes levels 2 and 3, that is, every run with `--opt-in`. This is confirmed (Decision 7).

## Records touched

- New: this file.
- Edited: `mkdocs.yml`, one nav entry after the ADR 0040 entry.
- Not touched by this record: code, tests, goldens, `CHANGELOG.md`, `.claude/PROGRESS.md`, BUILD_PLAN.md, and ADRs 0001 to 0040.
  The implementing pull requests own them, as Implementation plan assigns.
- Dated pointer notes that the implementing pull requests append, and do not edit older text in:
  - ADR 0031, Decision 9: PR3.
  - ADR 0028: PR3. It covers the amendments of Section 10.
  - ADR 0032, the `registered_records` table: PR3, for the three JSON columns.

## Related decisions

- [ADR 0002](0002-append-only-decision-log.md): the append-only pattern, applied to the manual log.
- [ADR 0003](0003-human-only-screening.md): why manual entry is not a screening decision.
- [ADR 0012](0012-persisting-skipped-layer0-entries.md) and [ADR 0013](0013-identified-sums-across-searches.md): the precedent for adding a
  table to the frozen schema.
- [ADR 0018](0018-abstract-runs-in-layer-1.md) and [ADR 0021](0021-entitlement-refusals-are-attributed-to-a-publisher.md): refusal
  attribution, which the refusal outcomes follow.
- [ADR 0022](0022-the-analysis-result-contract-and-its-provenance.md): analysis-level provenance. This record adds per-field provenance.
- [ADR 0024](0024-one-implementation-of-append-only-durability.md): the append-only log used for `decisions/manual_entries.jsonl` and `decisions/conflict_resolutions.jsonl`.
- [ADR 0028](0028-field-level-fallback-cascade.md): the cascade this record completes. It amends Decision 1 for the levels 2 and 3
  authors row, Decision 2 for the column form, and Resolved item 4 for where the opt-in is recorded (Section 10).
- [ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md): the reversal pattern and the event envelope.
- [ADR 0030](0030-openalex-topics-as-a-keyword-kind.md): the CHECK-constraint amendment form, and the topic kind.
- [ADR 0031](0031-openalex-responses-are-layer0-runs.md): the OpenAlex run kind that levels 4 uses. Decision 9 is made true by PR3.
- [ADR 0032](0032-registered-records-table-and-classification-map.md): `registered_records`, which gains three JSON columns (Decision 3).
  Its dated pointer note is a deliverable of PR3.
- [ADR 0035](0035-gui-registration-of-candidates.md): the GUI and CLI single-writer rule, and KEY_MAP.
- [ADR 0039](0039-taxonomy-engine-behaviour-stamp.md): the `methods.*` exemption that Decision 5 relies on.
- [ADR 0037](0037-merge-train-and-native-stacked-prs.md): the merge train and native stacked pull requests that the PR2 to PR3 stack
  and the additive `cli.py` hotspot rely on.
- [ADR 0040](0040-year-range-in-analysis-engines.md): the precedent for optional `methods.*` keys.
