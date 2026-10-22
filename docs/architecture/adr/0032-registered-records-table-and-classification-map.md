# ADR 0032: Layer 1 Table and View for Registered Records, and the OpenAlex Classification Map

## Status

Proposed — 2026-10-22.

This record exists only on the branch `docs/adr-0032-0033-drafts`. The governance test (`tests/unit/test_docs_governance.py`, `test_adr__on_main__is_not_still_proposed`)
scans the ADR directory of whichever checkout runs it, and fails any ADR whose Status reads `Proposed`
there. On this branch, this record and ADR 0033 fail that test by design.

**Acceptance is decided by a dedicated reviewer, not by this draft.** The owner has asked for that review. This
record stays `Proposed` through this revision. When the review passes, the Status line is changed to
`Accepted — <date>` in PR #150 itself, not in a separate pull request, so `main` never holds `Proposed`.

This PR also adds a dated supersession note to ADR 0007 and a dated note to ADR 0027, as ADR 0033 requires. Neither body is changed.
The deviations are listed under Consequences. Each deviation cites the project rule in `CLAUDE.md`, which says:
"Contracts, schemas, and acceptance criteria are not renegotiable without a new ADR (§2.6)." Item (ii) alone also falls
under BUILD_PLAN line 301, because the metadata source at BUILD_PLAN line 54 is a §1.2 item.

This record is the precondition that ADR 0029 names for registration code. The owner's answers to the
earlier draft are stated here as decisions, not as open questions.

## Context

[ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md) lets a reviewer register a candidate
resolved at level 4 (OpenAlex) as a record. Its "Split records" section states what this record must
specify, and its Decision 2 names the `register` event fields that depend on it, `subject_map_version`
and `subject_map_sha256`. ADR 0029 (Split records) names three inputs that a registered record needs before it can enter
`S_raw`: `doc_type`, `language`, and a subject area. ADR 0029 says a registered record without them "cannot be classified
and must not enter S_raw". This record keeps `doc_type`, `venue_type` and the subject area, and drops `language` (Decision 2).
That is a change to ADR 0029's requirement, listed as deviation (viii).

**The frozen Stage 3 schema has no place for registered records.** BUILD_PLAN lines 853-873 define
`records` (filled from Scopus payloads), `venues`, `subject_areas(record_id, area_code)` and `runs`. A
registered record has no Scopus payload and no `runs` row (ADR 0029 §9). A new Layer 1 table is a schema
addition, and [ADR 0012](0012-persisting-skipped-layer0-entries.md) requires its own ADR.

**The engine reads Layer 1 directly.** `prisma/engine.py` builds its attributes in `_fetch_record_attributes`
(defined at line 193, query at lines 210-214) from `records`, a `LEFT JOIN venues`, and `subject_areas`. `_passes_conference_whitelist`
(lines 297-320) keys on `venue_type == "conference"`, and a missing venue reads as `""`.

Two more readers take the record set from `records`, and they must move too. `raw_set` (`prisma/engine.py`, lines 726-744)
runs `SELECT record_id FROM records`, and that is the `S_raw` universe from which `automated_set` and `language_set` are
filtered. Without a change, registrations never reach the automated filters. `load_records` (`screening/ui.py`, lines
195-227) reads `records` joined to `venues`. Without a change, a reviewer cannot load a registered id, so no decision can
be recorded for it. `Corpus.records` (`store/load.py`, lines 2508-2537) runs `SELECT * FROM records`.

**Scopus values and OpenAlex values differ.**

- `doc_type` on a Scopus record is the human-readable `subtypeDescription`, falling back to the code
  (`store/load.py`, `_doc_type_from_entry`, lines 727-742). The engine compares it with `criteria.yaml`'s
  codes through `_DOC_TYPE_CODE_TO_DESCRIPTION` (`prisma/engine.py`, lines 255-268, and `_doc_type_matches`,
  lines 271-294). The stored values are therefore descriptions such as `article` and `conference paper`.
- `venue_type` on a Scopus record comes from `prism:aggregationType` through a closed set of
  `journal`, `conference`, `book` and `other` (`store/load.py`, lines 194 and 216-220).
- `language` on a Scopus record is a language name such as `English`. `_passes_language` compares it
  case-insensitively with `criteria.yaml` (`prisma/engine.py`, lines 357-377). In the two stores the owner measured,
  every stored Scopus record has `language` NULL (1,293 of 1,293 and 1,864 of 1,864), so the language filter has never
  excluded anything in them, and no language table can be checked against stored values.
- `subject_areas.area_code` holds a four-digit ASJC code (`2202`), matched at grouping level through
  `asjc.area_abbrev` (`asjc.py`, line 74; [ADR 0017](0017-subject-areas-match-by-asjc-grouping.md)).
- OpenAlex gives `type` (25 values, verified against the OpenAlex Work types page), `language` as an ISO 639-1
  code that is not used by this record (Decision 2), `primary_location.source.type`
  (seven values, verified against the OpenAlex Sources page), and `topics`. The OpenAlex Work object page
  states that `primary_topic` is the first entry of `topics`, that `topics` holds up to three topics, and
  that each carries `subfield`, `field` and `domain`. It also states that the second and third scores are
  "often near 0" (help.openalex.org, Work object, fetched 2026-10-22). OpenAlex topics are not ASJC codes: the two taxonomies differ, so the
  subfield-to-ASJC map of §3 is the only bridge between them.

**The current OpenAlex select does not carry those fields.** `sources/openalex.py` line 52 sets
`_SELECT = "id,doi,title,publication_year,abstract_inverted_index,authorships,topics"`. It has no `type`,
`primary_location` or `primary_topic`. [ADR 0031](0031-openalex-responses-are-layer0-runs.md)
Context lists that select at line 44 of the current ADR 0031 file (it was line 42 before the dated note of 2026-10-22 was added). The owner has approved adding `type`, `primary_location` and `primary_topic`, but not
`language` (ADR 0031 note of 2026-10-22).

**Scopus's no-data rule does not carry over.** A Scopus record with no `subject_areas` rows, or a `NULL`
language, is never excluded on that dimension (`prisma/engine.py`, module docstring, lines 72-84). ADR 0029
does not allow that latitude for registrations, so this record does not extend it.

## Decision

### 1. The Layer 1 table and the view

Registered records are stored in one new table holding only **active** registrations, the result of
folding `registrations.jsonl` (ADR 0029 §3). The loader builds it, as the only Layer 1 writer (ADR 0028
Decision 3), and it is rebuildable from Layers 0 and 2 (ADR 0029 V14).

```sql
CREATE TABLE registered_records (
  record_id TEXT PRIMARY KEY,         -- 'registered:' || event_id (ADR 0029 §4)
  event_id TEXT NOT NULL,             -- the active register event
  doi TEXT NOT NULL,                  -- required (ADR 0029 §1)
  title TEXT NOT NULL,
  abstract TEXT,                      -- nullable: OpenAlex may have no abstract (as Scopus may not)
  year INTEGER NOT NULL,
  doc_type TEXT NOT NULL,             -- description form, derived per Decision 2
  language TEXT,                      -- always NULL in this version (Decision 2)
  venue_type TEXT NOT NULL,           -- closed set journal, conference, book, other (Decision 2)
  venue_name TEXT NOT NULL,           -- the OpenAlex source display name, stored stripped (§2, §5)
  openalex_id TEXT NOT NULL,          -- the work's OpenAlex id
  payload_file TEXT NOT NULL,         -- PayloadRef file (ADR 0031 Decision 2)
  payload_line INTEGER NOT NULL,      -- always 0 (§6)
  match_rule TEXT NOT NULL,           -- doi, title_year or near_confirmed (ADR 0029 §1)
  reviewer TEXT NOT NULL,
  registered_ts TIMESTAMP NOT NULL    -- UTC, from the register event's ts
);
```

A registered record has no `venues` row. Its venue type and name sit in this table, because `venues` is
keyed on Scopus venue ids and is written from Scopus payloads. The classification map version and checksum
are not repeated here. They stay on the `register` event (ADR 0029 §2), so a copy cannot drift from its
source (the reason ADR 0017 gave for not adding `area_abbrev`). A registered record's subject areas go into
the existing `subject_areas` table as four-digit ASJC codes. No change to that table's columns is needed.

The view unites the two populations, and it is the one reader of `S_raw`:

```sql
CREATE VIEW records_all AS
  SELECT r.record_id, 'scopus' AS origin, r.run_id, r.doi, r.title, r.abstract, r.year,
         r.cover_date, r.doc_type, r.language, r.venue_id, r.open_access,
         r.payload_file, r.payload_line,
         v.venue_type, v.name AS venue_name
    FROM records r LEFT JOIN venues v ON v.venue_id = r.venue_id
  UNION ALL
  SELECT record_id, 'registered', NULL, doi, title, abstract, year,
         NULL, doc_type, language, NULL, NULL,
         payload_file, payload_line, venue_type, venue_name
    FROM registered_records;
```

The view has 16 columns: the 13 columns of `records` (BUILD_PLAN lines 853-857), plus `origin`, `venue_type` and
`venue_name`. The Scopus half has no `COALESCE`, so a Scopus record with no venue keeps a NULL venue name and type, as
`screening/ui.py` line 224 (`v.name`) reads them today. `_fetch_record_attributes` keeps its own `COALESCE` (`prisma/engine.py`
lines 211-212). An earlier draft put `COALESCE` in the view, which would have changed the screening reader's value from NULL to
`''` for Scopus rows; that draft is withdrawn. For Scopus rows, `screening/ui.py` reads the same NULL it reads today, and every other column is the
`records` value, with `origin` added.

**The readers that move to `records_all`**, each with what it does for a registration:

- `prisma/engine.py` `raw_set` (lines 726-744), the `S_raw` universe. `automated_set` and `language_set` then filter
  registrations too. A registration that fails the automated set is counted in `excluded_automated` (ADR 0033).
- `prisma/engine.py` `_fetch_record_attributes` (defined at line 193; query at lines 210-214). Registrations take the conference whitelist from their
  own `venue_type` and `venue_name` (Decision 5).
- `screening/ui.py` `load_records` (lines 195-227). Its `venues` join is replaced by the view's `venue_name`, so a reviewer
  can load a registered id and record a decision on it.
- `store/load.py` `Corpus.records` (lines 2508-2537). For `PrismaStage.RAW` it runs `SELECT * FROM records_all`. For the other
  stages it filters `records_all` by the engine's record ids.
- `store/load.py` `Corpus.venues` (lines 2646-2655). Its inner join `records r JOIN venues v` drops every registration, because a
  registration has no `venues` row. It reads `records_all` instead, and a registration is grouped by its `venue_name`. `Corpus.venues` (`store/load.py` lines 2646-2648) selects `venue_id`, `name`,
  `issn`, `eissn`, `venue_type` and `abbreviation`. A registration has NULL for `venue_id`, `issn`, `eissn` and `abbreviation`,
  and its `name` and `venue_type` come from `venue_name` and the registration's type. Owner
  decision A: registrations count in the venue tables.
- `report/numbers.py` `corpus scalars` (lines 100-103). `corpus.size`, `corpus.year_min` and `corpus.year_max` are computed from
  `records` today. They read `records_all`, so they count registrations. Owner decision A.
- `fulltext/run.py` (lines 201-205). It reads each target's DOI from `records`. A registration is a target once it is included at
  title and abstract screening, and today it would be given no DOI, so it could not be resolved. It reads `records_all` instead.
- `fulltext/coverage.py` (lines 129-136). Its inner join `fulltext_assets a JOIN records r` drops registrations from the coverage
  counts. It reads `records_all` instead, so a registration's attempts are counted.
- `scripts/fulltext_missing.py` (lines 74-80). It builds the lookup `meta[record_id]` from `records` and then indexes it for every
  sought id, so a registration raises `KeyError` today. It reads `records_all` instead.
- `scripts/fetch_assist.py` (lines 315-327). It reads `records` for the batch and silently drops any id missing from it. It reads
  `records_all` instead.

**The readers that do not move.** Each reads a fact that a registration does not have, and each stays on its table:

- `prisma/flow.py` `_unloadable_count` (defined at line 334; its SQL is at lines 362-374). It counts `malformed_entries` whose `record_id` is absent from `records`.
  Malformed entries come from Layer 0 payloads that failed to parse, and a registration has none (a registration is refused
  unless its OpenAlex body parses). The check is benign for registrations, and it is unchanged.
- `store/load.py` build statistics (lines 2084-2096). `records_loaded` counts the Scopus rows the loader wrote, which is what
  the build did. Registrations are loaded by the registration fold, which is a separate count, so `records_loaded` stays
  Scopus-only, and the methods report the registered count separately.
- `runs`, `run_duplicates`, `citation_snapshots`, `malformed_entries` and the authors, affiliations and keyword tables are
  Scopus facts and are unchanged.

**What `Corpus.records` returns for a registration.** A registered row has the same 16 columns. `run_id`, `cover_date`,
`venue_id` and `open_access` are NULL, because a registration has no run, no cover date, no `venues` row and no open-access flag.
`abstract` is the OpenAlex abstract when the work has one, and NULL when it does not. `payload_file` and `payload_line` name the
OpenAlex response file (§6), so `payload_line` is 0. `origin` is `registered`. A caller that joins on `venue_id` gets NULL
for a registration, and this is part of deviation (iii). Registered records have no citation snapshot, and their `cited_by_count`
is reported as `unavailable` (ADR 0029 Consequence 7). The names `registered_records` and `records_all` are naming choices,
and are fixed by this record (decided 2026-10-22).

**Readers of the venue, citation and consumer numbers.** Each reader below is named, with what a registration does in it.

- `taxonomy/analysis.py` (lines 101, 168, `corpus.records`). The taxonomy analysis counts coded records. A registration is coded
  from its title and abstract, as a Scopus record is, and it is counted in the taxonomy totals.
- `viz/dashboard.py` line 459 (Corpus browser, `corpus.records`). A registration appears in the browser, with `origin` shown.
- `viz/dashboard.py` line 256 (`Corpus.citations()` for the snapshot selector). A registration has no snapshot, so it is absent
  from the snapshot options. No other effect.
- `screening/ui.py` lines 242-245 (`cited_by` from `citation_snapshots`). A registration has no `cited_by` value, so the screening
  view shows no citation line for it. Its absence is shown as absence, not as zero.
- `store/load.py` `Corpus.citations` (defined at line 2577). It returns snapshot rows only, so a registration is absent from it.
  ADR 0029 Consequence 7 requires the registration to be counted as `unavailable` in every citation denominator. That is the
  `citations.registered_unavailable` key named below.
- `store/load.py` lines 2065-2069 (duplicate-DOI statistic, `StoreStats`). It reads `records` only, and it stays Scopus-only.
  A registration never duplicates a Scopus DOI, because ADR 0029 §6 supersedes the registration when one appears. This is a
  build statistic, and it is unchanged.

- `report/numbers.py` `_venue_numbers` (`venues.total` and `venues.top<i>`, through `Corpus.venues`). Counted, per owner decision A.
  The `venues.*` keys change for a project with registrations.
- `report/numbers.py` `_citation_numbers` (`citations.*`, through `bibliometrics/citations.py` `citation_statistics`). A registration
  has no `citation_snapshots` row, and the citation statistics inner-join snapshots, so a registration is excluded from the
  citation figures. ADR 0029 Consequence 7 requires it to be counted as `unavailable` in every citation denominator. That count
  is a new key, `citations.registered_unavailable`, which this record names as required of the implementation PR. It is not
  yet in the code, and `numbers_keys.json` lists it when it is.
- `bibliometrics/keywords.py` (lines 103, 159). A registration has no `record_keywords` row, so it contributes no terms. Any count
  of records in a keyword denominator includes it.
- `bibliometrics/geography.py` (lines 147, 202). A registration has no `record_affiliations` row, so it contributes no country.
  The methods must say so for the registrations that are counted.
- `bibliometrics/trends.py` (lines 91, 191). Registrations are counted in their year bins, as Scopus records are.
- `bibliometrics/venues.py` (lines 162-163, 193-194) and `taxonomy/coder.py` (line 376). Venues take the `Corpus.venues` change.
  The coder applies its rules to title and abstract, so a registration is coded from its OpenAlex abstract when it has one.
- `bibliometrics/network.py` (lines 444, 497). A registration has no keyword rows, so it adds no node or edge.
- Tests that depend on the change. The implementation PR must update these, and they are named here so that none is missed:
  - `tests/integration/store/test_corpus_bibliometrics_accessors.py:62` counts `records JOIN venues`. After `Corpus.venues` reads
    `records_all`, the expected count must include registrations, so this test changes.
  - `tests/integration/report/test_bundle_consistency.py:119` picks the top venue from `records JOIN venues`. The reference fixture
    has no registrations, so its value does not change. The implementation PR moves it to `records_all`, so that the test and
    `Corpus.venues` use one definition.
  - `tests/unit/bibliometrics/test_trends.py:385` and `tests/e2e/test_reference_project.py:65-66` read `Corpus.records`. On the
    reference fixture they keep their values, but any that assert the 13-column schema change with the 16-column view.

- `bibliometrics/base.py` line 314 (`build_provenance`, `corpus_size=records.height`, over `corpus.records`). The provenance
  line of each figure reports `n` from the records frame, so it counts registrations, as owner decision A decides. A figure's
  `n` for a project with registrations is therefore the Scopus count plus the registered count.
- The writer, `store/load.py` line 1955 (`_insert_rows(connection, "records", acc.records)`). It is unchanged by this design. The
  loader still writes Scopus rows only to `records`. Registrations reach Layer 1 through the registration fold into
  `registered_records`. The fold is part of the loader (`store/load.py`), which is the only writer of Layer 1 (ADR 0028, Decision 3), so no deviation from ADR 0028 is needed.

**Full text for registrations.** Registrations are targets of the full-text stage on the same terms as Scopus records, because
they are members of `M_abs` once included at title and abstract. Their DOI comes from `records_all` (above). Their assets are
attempted and counted in coverage, and the resolvers see the registration's DOI, as they see a Scopus record's.

### 2. How each input is derived

The loader derives the inputs from the stored OpenAlex body that the `register` event references. No reviewer types
them, and no client return value is read (ADR 0031 Decision 9). Each value must come from the fixed tables in Decision 3.
A value that a table does not cover is a refusal (§4).

| Input | Stored body field | Rule | Stored value |
|---|---|---|---|
| `doc_type` | `type` | `TYPE_TO_DOC_TYPE` (Decision 3b) | description form, as the engine compares it, for example `article` or `conference paper` |
| `venue_type` | `primary_location.source.type` | `SOURCE_TYPE_TO_VENUE_TYPE` (Decision 3c) | `journal`, `conference`, `book` or `other` |
| `venue_name` | `primary_location.source.display_name` | stored after `strip()`; refused if empty after stripping (Decision 5) | string |
| subject area | `primary_topic.subfield.id` | `SUBFIELD_TO_ASJC` (Decision 3a) | four-digit ASJC code, one row in `subject_areas` |

The subject area takes **`primary_topic` only** (owner decision, 2026-10-22). The second and third topics are not
read.

**Language is not derived (owner decision, 2026-10-22).** `language` is not a registration input and is not in `registered_records`
with a value. The stored column is always NULL in this version. Its reason is the measured fact in Context: every stored Scopus
record has `language` NULL, so no language table could be checked against stored data. A NULL language passes the language
filter by the existing no-data rule (`_passes_language`, `prisma/engine.py` lines 372-375), the same as every Scopus record in the
two stores. ADR 0029's Split records bullet (line 231) names language as an input, and this is deviation (viii).

**Refusal is at registration.** ADR 0029 §5 names the registration writer as the enforcement point for every
refusal, so the writer checks every input in the table above before the append. This refusal is not in code today. It is required of the
registration PR, which is later than this record, and the tests that prove it are named in the venue_name rule (Decision 5). The loader also refuses a `register` event
whose referenced body cannot yield them, so a mis-built event cannot enter `S_raw`. The refusal names the
input and its value, for example `type 'preprint' is not in classification map version 1`.

### 3. The classification map: one fixed, versioned file, checked

The classification map is one Python module, `src/prismabib/openalex_map.py`, in the style of `asjc.py`. It
holds three tables, (a) to (c) below. Two constants name it:

- `MAP_VERSION`, a string. The version of this draft is `"1"`, dated 2026-10-22. Any change to any table
  bumps it.
- `MAP_SHA256`, the SHA-256 of the canonical JSON of the three tables:
  `json.dumps(tables, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`, encoded as UTF-8. The
  digest is computed when the module is written, and it is pinned in the same commit. It is not computed in
  this draft.

ADR 0029 Owner decision 4 (line 250) and the register bullet (line 62) describe the mapping as "a fixed, versioned
mapping table from OpenAlex topics to ASJC codes". **The owner decided the granularity on 2026-10-22: the map is from
OpenAlex subfield to ASJC code, not from topic.** This record changes that wording in ADR 0029 Owner decision 4
and in line 62. A dated pointer note is added under Owner decision 4 in ADR 0029, and its body is unchanged. The change is
listed under Consequences as deviation (i). The field names
`subject_map_version` and `subject_map_sha256` keep their ADR 0029 meaning. They name the whole file that gave
the record its subject area, and the file now holds three tables.

**Versions are kept, never replaced.** Every version the code has shipped stays in the module. A `register`
event names the version it was derived under, so a rebuild reproduces the same codes after a later change. A
registration naming an unknown version, or a checksum that does not match the named version, raises `LogError`
on load.

**(a) Subfield to ASJC: version 1, 252 rows.** Source: the OpenAlex subfields list (`api.openalex.org/subfields`,
two pages, `meta.count` 252, fetched 2026-10-22). ASJC names: the Scopus ASJC code list (elsevier.support,
"What is the complete list of ASJC subject areas in Scopus", fetched 2026-10-22). Every OpenAlex subfield id
equals an ASJC code in that list. Match status is one of:

- `exact`: the OpenAlex subfield name equals the ASJC name.
- `qualifier`: the ASJC name differs only by a parenthetical qualifier or by letter case. The ASJC name is
  given in the row.

Rows are in OpenAlex API order. Result: **252 rows; 239 exact; 13 qualifier; 0 unmapped; 0 unverified.** Separately, and not part of that count, the path `primary_location.source.type` used by criterion (c) is
unverified. It is gated by the required test `test_registration__source_type_path__matches_recorded_works_fixture`, which must
pass before the registration PR merges. It is not an open question. The
status is my comparison of two transcribed name lists. It is not a second independent source, and the owner
accepted the 13 qualifier rows as qualified, on 2026-10-22. Each is marked `qualifier` and its mapping is approximate.
A registered record that carries one is flagged in the methods. The owner may tighten these rows in a later ADR.

| OpenAlex subfield id | OpenAlex subfield | ASJC code | Match |
|---|---|---|---|
| 3106 | Nuclear and High Energy Physics | 3106 | exact |
| 1110 | Plant Science | 1110 | exact |
| 3312 | Sociology and Political Science | 3312 | exact |
| 3320 | Political Science and International Relations | 3320 | exact |
| 3304 | Education | 3304 | exact |
| 1202 | History | 1202 | exact |
| 1312 | Molecular Biology | 1312 | exact |
| 1208 | Literature and Literary Theory | 1208 | exact |
| 2208 | Electrical and Electronic Engineering | 2208 | exact |
| 2202 | Aerospace Engineering | 2202 | exact |
| 2746 | Surgery | 2746 | exact |
| 2002 | Economics and Econometrics | 2002 | exact |
| 2210 | Mechanical Engineering | 2210 | exact |
| 1311 | Genetics | 1311 | exact |
| 1203 | Language and Linguistics | 1203 | exact |
| 3308 | Law | 3308 | exact |
| 2739 | Public Health, Environmental and Occupational Health | 2739 | exact |
| 1710 | Information Systems | 1710 | exact |
| 1702 | Artificial Intelligence | 1702 | exact |
| 2505 | Materials Chemistry | 2505 | exact |
| 1105 | Ecology, Evolution, Behavior and Systematics | 1105 | exact |
| 2303 | Ecology | 2303 | exact |
| 2713 | Epidemiology | 2713 | exact |
| 1213 | Visual Arts and Performing Arts | 1213 | exact |
| 2740 | Pulmonary and Respiratory Medicine | 2740 | exact |
| 2306 | Global and Planetary Change | 2306 | exact |
| 1211 | Philosophy | 1211 | exact |
| 3103 | Astronomy and Astrophysics | 3103 | exact |
| 2204 | Biomedical Engineering | 2204 | exact |
| 2205 | Civil and Structural Engineering | 2205 | exact |
| 3316 | Cultural Studies | 3316 | exact |
| 1408 | Strategy and Management | 1408 | exact |
| 1204 | Archeology | 1204 | qualifier: ASJC "Archeology (arts and humanities)" |
| 1605 | Organic Chemistry | 1605 | exact |
| 3600 | General Health Professions | 3600 | exact |
| 3314 | Anthropology | 3314 | exact |
| 3107 | Atomic and Molecular Physics, and Optics | 3107 | exact |
| 1402 | Accounting | 1402 | exact |
| 3203 | Clinical Psychology | 3203 | exact |
| 3207 | Social Psychology | 3207 | exact |
| 1212 | Religious studies | 1212 | qualifier: ASJC "Religious Studies" (case) |
| 1210 | Music | 1210 | exact |
| 2705 | Cardiology and Cardiovascular Medicine | 2705 | exact |
| 1705 | Computer Networks and Communications | 1705 | exact |
| 2737 | Physiology | 2737 | qualifier: ASJC "Physiology (medical)" |
| 2207 | Control and Systems Engineering | 2207 | exact |
| 1207 | History and Philosophy of Science | 1207 | exact |
| 1106 | Food Science | 1106 | exact |
| 1707 | Computer Vision and Pattern Recognition | 1707 | exact |
| 1209 | Museology | 1209 | exact |
| 2730 | Oncology | 2730 | exact |
| 2741 | Radiology, Nuclear Medicine and Imaging | 2741 | exact |
| 2725 | Infectious Diseases | 2725 | exact |
| 2805 | Cognitive Neuroscience | 2805 | exact |
| 3204 | Developmental and Educational Psychology | 3204 | exact |
| 3205 | Experimental and Cognitive Psychology | 3205 | exact |
| 1910 | Oceanography | 1910 | exact |
| 1604 | Inorganic Chemistry | 1604 | exact |
| 2211 | Mechanics of Materials | 2211 | exact |
| 2206 | Computational Mechanics | 2206 | exact |
| 1902 | Atmospheric Science | 1902 | exact |
| 3311 | Safety Research | 3311 | exact |
| 2215 | Building and Construction | 2215 | exact |
| 2738 | Psychiatry and Mental health | 2738 | qualifier: ASJC "Psychiatry and Mental Health" (case) |
| 2003 | Finance | 2003 | exact |
| 2308 | Management, Monitoring, Policy and Law | 2308 | exact |
| 1908 | Geophysics | 1908 | exact |
| 1407 | Organizational Behavior and Human Resource Management | 1407 | exact |
| 1102 | Agronomy and Crop Science | 1102 | exact |
| 1803 | Management Science and Operations Research | 1803 | exact |
| 1111 | Soil Science | 1111 | exact |
| 3305 | Geography, Planning and Development | 3305 | exact |
| 2302 | Ecological Modeling | 2302 | exact |
| 2712 | Endocrinology, Diabetes and Metabolism | 2712 | exact |
| 2736 | Pharmacology | 2736 | qualifier: ASJC "Pharmacology (medical)" |
| 3317 | Demography | 3317 | exact |
| 2312 | Water Science and Technology | 2312 | exact |
| 3315 | Communication | 3315 | exact |
| 1406 | Marketing | 1406 | exact |
| 2403 | Immunology | 2403 | exact |
| 2734 | Pathology and Forensic Medicine | 2734 | exact |
| 2000 | General Economics, Econometrics and Finance | 2000 | exact |
| 3318 | Gender Studies | 3318 | exact |
| 2105 | Renewable Energy, Sustainability and the Environment | 2105 | exact |
| 1205 | Classics | 1205 | exact |
| 1404 | Management Information Systems | 1404 | exact |
| 1109 | Insect Science | 1109 | exact |
| 1206 | Conservation | 1206 | exact |
| 2604 | Applied Mathematics | 2604 | exact |
| 1703 | Computational Theory and Mathematics | 1703 | exact |
| 2212 | Ocean Engineering | 2212 | exact |
| 2216 | Architecture | 2216 | exact |
| 1911 | Paleontology | 1911 | exact |
| 2728 | Neurology | 2728 | qualifier: ASJC "Neurology (clinical)" |
| 2504 | Electronic, Optical and Magnetic Materials | 2504 | exact |
| 1100 | General Agricultural and Biological Sciences | 1100 | exact |
| 2307 | Health, Toxicology and Mutagenesis | 2307 | exact |
| 2745 | Rheumatology | 2745 | exact |
| 1802 | Information Systems and Management | 1802 | exact |
| 2613 | Statistics and Probability | 2613 | exact |
| 2209 | Industrial and Manufacturing Engineering | 2209 | exact |
| 2720 | Hematology | 2720 | exact |
| 3309 | Library and Information Sciences | 3309 | exact |
| 2213 | Safety, Risk, Reliability and Quality | 2213 | exact |
| 1307 | Cell Biology | 1307 | exact |
| 2305 | Environmental Engineering | 2305 | exact |
| 1607 | Spectroscopy | 1607 | exact |
| 2916 | Nutrition and Dietetics | 2916 | exact |
| 2735 | Pediatrics, Perinatology and Child Health | 2735 | exact |
| 2309 | Nature and Landscape Conservation | 2309 | exact |
| 2743 | Reproductive Medicine | 2743 | exact |
| 2804 | Cellular and Molecular Neuroscience | 2804 | exact |
| 3322 | Urban Studies | 3322 | exact |
| 1306 | Cancer Research | 1306 | exact |
| 2608 | Geometry and Topology | 2608 | exact |
| 1405 | Management of Technology and Innovation | 1405 | exact |
| 2310 | Pollution | 2310 | exact |
| 2507 | Polymers and Plastics | 2507 | exact |
| 2707 | Complementary and alternative medicine | 2707 | qualifier: ASJC "Complementary and Alternative Medicine" (case) |
| 2729 | Obstetrics and Gynecology | 2729 | exact |
| 1904 | Earth-Surface Processes | 1904 | exact |
| 2731 | Ophthalmology | 2731 | exact |
| 3109 | Statistical and Nonlinear Physics | 3109 | exact |
| 2214 | Media Technology | 2214 | exact |
| 3313 | Transportation | 3313 | exact |
| 3104 | Condensed Matter Physics | 3104 | exact |
| 3306 | Health | 3306 | qualifier: ASJC "Health (social science)" |
| 3321 | Public Administration | 3321 | exact |
| 2610 | Mathematical Physics | 2610 | exact |
| 2727 | Nephrology | 2727 | exact |
| 1602 | Analytical Chemistry | 1602 | exact |
| 2203 | Automotive Engineering | 2203 | exact |
| 2732 | Orthopedics and Sports Medicine | 2732 | exact |
| 3310 | Linguistics and Language | 3310 | exact |
| 3605 | Health Information Management | 3605 | exact |
| 1103 | Animal Science and Zoology | 1103 | exact |
| 2502 | Biomaterials | 2502 | exact |
| 1104 | Aquatic Science | 1104 | exact |
| 3004 | Pharmacology | 3004 | exact |
| 3612 | Physical Therapy, Sports Therapy and Rehabilitation | 3612 | exact |
| 1906 | Geochemistry and Petrology | 1906 | exact |
| 3609 | Occupational Therapy | 3609 | exact |
| 1907 | Geology | 1907 | exact |
| 1804 | Statistics, Probability and Uncertainty | 1804 | exact |
| 2721 | Hepatology | 2721 | exact |
| 3404 | Small Animals | 3404 | exact |
| 2708 | Dermatology | 2708 | exact |
| 2711 | Emergency Medicine | 2711 | exact |
| 2716 | Genetics | 2716 | qualifier: ASJC "Genetics (clinical)" |
| 1711 | Signal Processing | 1711 | exact |
| 2304 | Environmental Chemistry | 2304 | exact |
| 2311 | Waste Management and Disposal | 2311 | exact |
| 3108 | Radiation | 3108 | exact |
| 3505 | Orthodontics | 3505 | exact |
| 1708 | Hardware and Architecture | 1708 | exact |
| 2405 | Parasitology | 2405 | exact |
| 3506 | Periodontics | 3506 | exact |
| 1606 | Physical and Theoretical Chemistry | 1606 | exact |
| 1709 | Human-Computer Interaction | 1709 | exact |
| 2748 | Urology | 2748 | exact |
| 3604 | Emergency Medical Services | 3604 | exact |
| 2715 | Gastroenterology | 2715 | exact |
| 1706 | Computer Science Applications | 1706 | exact |
| 3504 | Oral Surgery | 3504 | exact |
| 1712 | Software | 1712 | exact |
| 2703 | Anesthesiology and Pain Medicine | 2703 | exact |
| 2808 | Neurology | 2808 | exact |
| 2404 | Microbiology | 2404 | exact |
| 1912 | Space and Planetary Science | 1912 | exact |
| 1507 | Fluid Flow and Transfer Processes | 1507 | exact |
| 2742 | Rehabilitation | 2742 | exact |
| 3616 | Speech and Hearing | 3616 | exact |
| 3307 | Human Factors and Ergonomics | 3307 | exact |
| 2733 | Otorhinolaryngology | 2733 | exact |
| 3005 | Toxicology | 3005 | exact |
| 3303 | Development | 3303 | exact |
| 1304 | Biophysics | 1304 | exact |
| 3003 | Pharmaceutical Science | 3003 | exact |
| 3202 | Applied Psychology | 3202 | exact |
| 1704 | Computer Graphics and Computer-Aided Design | 1704 | exact |
| 3300 | General Social Sciences | 3300 | exact |
| 1305 | Biotechnology | 1305 | exact |
| 1310 | Endocrinology | 1310 | exact |
| 2611 | Modeling and Simulation | 2611 | exact |
| 2503 | Ceramics and Composites | 2503 | exact |
| 2704 | Biochemistry | 2704 | qualifier: ASJC "Biochemistry (medical)" |
| 1313 | Molecular Medicine | 1313 | exact |
| 1200 | General Arts and Humanities | 1200 | exact |
| 2602 | Algebra and Number Theory | 2602 | exact |
| 3105 | Instrumentation | 3105 | exact |
| 2723 | Immunology and Allergy | 2723 | exact |
| 2922 | Research and Theory | 2922 | exact |
| 3611 | Pharmacy | 3611 | exact |
| 2508 | Surfaces, Coatings and Films | 2508 | exact |
| 2807 | Endocrine and Autonomic Systems | 2807 | exact |
| 3614 | Radiological and Ultrasound Technology | 3614 | exact |
| 2809 | Sensory Systems | 2809 | exact |
| 2706 | Critical Care and Intensive Care Medicine | 2706 | exact |
| 2607 | Discrete Mathematics and Combinatorics | 2607 | exact |
| 2406 | Virology | 2406 | exact |
| 1107 | Forestry | 1107 | exact |
| 2614 | Theoretical Computer Science | 2614 | exact |
| 1503 | Catalysis | 1503 | exact |
| 1303 | Biochemistry | 1303 | exact |
| 1308 | Clinical Biochemistry | 1308 | exact |
| 2612 | Numerical Analysis | 2612 | exact |
| 2717 | Geriatrics and Gerontology | 2717 | exact |
| 1800 | General Decision Sciences | 1800 | exact |
| 2724 | Internal Medicine | 2724 | exact |
| 2910 | Issues, ethics and legal aspects | 2910 | qualifier: ASJC "Issues, Ethics and Legal Aspects" (case) |
| 1314 | Physiology | 1314 | exact |
| 2747 | Transplantation | 2747 | exact |
| 3603 | Complementary and Manual Therapy | 3603 | exact |
| 2100 | General Energy | 2100 | exact |
| 2702 | Anatomy | 2702 | exact |
| 2102 | Energy Engineering and Power Technology | 2102 | exact |
| 3200 | General Psychology | 3200 | exact |
| 1409 | Tourism, Leisure and Hospitality Management | 1409 | exact |
| 3206 | Neuropsychology and Physiological Psychology | 3206 | exact |
| 3402 | Equine | 3402 | exact |
| 2714 | Family Practice | 2714 | exact |
| 3302 | Archeology | 3302 | exact |
| 2718 | Health Informatics | 2718 | exact |
| 2806 | Developmental Neuroscience | 2806 | exact |
| 1603 | Electrochemistry | 1603 | exact |
| 2402 | Applied Microbiology and Biotechnology | 2402 | exact |
| 2500 | General Materials Science | 2500 | exact |
| 1315 | Structural Biology | 1315 | exact |
| 2506 | Metals and Alloys | 2506 | exact |
| 2802 | Behavioral Neuroscience | 2802 | exact |
| 1309 | Developmental Biology | 1309 | exact |
| 1504 | Chemical Health and Safety | 1504 | exact |
| 1410 | Industrial relations | 1410 | qualifier: ASJC "Industrial Relations" (case) |
| 3319 | Life-span and Life-course Studies | 3319 | exact |
| 1502 | Bioengineering | 1502 | exact |
| 1403 | Business and International Management | 1403 | exact |
| 1508 | Process Chemistry and Technology | 1508 | exact |
| 1302 | Aging | 1302 | exact |
| 2103 | Fuel Technology | 2103 | exact |
| 2726 | Microbiology | 2726 | qualifier: ASJC "Microbiology (medical)" |
| 1108 | Horticulture | 1108 | exact |
| 2911 | Leadership and Management | 2911 | exact |
| 3607 | Medical Laboratory Technology | 3607 | exact |
| 1506 | Filtration and Separation | 1506 | exact |
| 2803 | Biological Psychiatry | 2803 | exact |
| 3102 | Acoustics and Ultrasonics | 3102 | exact |
| 2605 | Computational Mathematics | 2605 | exact |
| 2200 | General Engineering | 2200 | exact |
| 3500 | General Dentistry | 3500 | exact |
| 3608 | Medical Terminology | 3608 | exact |
| 2104 | Nuclear Energy and Engineering | 2104 | exact |
| 3002 | Drug Discovery | 3002 | exact |

**Unmapped rule.** Every subfield in the table has a code. A subfield that is missing from a future version of
this table is a refusal, not a guess: the registration is refused with the id named, and no record is created.

**(b) Work type to `doc_type`: version 1, 25 rows.** The OpenAlex Work types page lists 25 values, and every one has a row. Eight are mapped and 17 are refused. The refused row lists them. Source: the OpenAlex Work types page, fetched 2026-10-22. The
targets are the description strings the engine compares with (`_DOC_TYPE_CODE_TO_DESCRIPTION`,
`prisma/engine.py` lines 255-268). A type with no target is refused.

| OpenAlex `type` | `doc_type` | Note |
|---|---|---|
| `article` | `article` | Owner decision, 2026-10-22: a conference-hosted article is typed `article`. Its conference status is carried by `venue_type`, so it matches `doc_types.include` like any article, and the conference rule still applies through `venue_type`. |
| `review` | `review` | |
| `editorial` | `editorial` | |
| `letter` | `letter` | |
| `erratum` | `erratum` | |
| `book` | `book` | |
| `book-chapter` | `book chapter` | |
| `conference-paper` | `conference paper` | |
| `book-review`, `conference-abstract`, `data-paper`, `dataset`, `dissertation`, `libguides`, `other`, `paratext`, `peer-review`, `preprint`, `reference-entry`, `report`, `retraction`, `software`, `software-paper`, `standard`, `supplementary-materials` | (none) | Refused. Each has no Scopus description in the engine's table. |

**(c) Source type to `venue_type`: version 1, 7 rows.** Source: the OpenAlex Sources page, fetched 2026-10-22. The
targets are the closed `venue_type` set (`store/load.py` line 194). All seven rows are judgement calls chosen by this record's author, and the table marks each one.
Four of them collapse several OpenAlex source types into one closed value: `ebook platform` and `book series` into `book`,
and `repository` and `metadata` into `other`. The owner accepts all seven judgement rows with this ADR, dated 2026-10-22.
They are tightenable in a later ADR. The engine reads only `conference`, so no row here changes a count today except the
`conference` row.

| OpenAlex source `type` | `venue_type` | Basis |
|---|---|---|
| `journal` | `journal` | judgement, direct, tightenable |
| `conference` | `conference` | judgement, direct, tightenable |
| `ebook platform` | `book` | judgement, tightenable |
| `book series` | `book` | judgement, tightenable |
| `repository` | `other` | judgement, tightenable |
| `metadata` | `other` | judgement, tightenable |
| `other` | `other` | judgement, direct, tightenable |

A `primary_location` with no source, or a source type outside this table, is refused. The Sources page confirms
`type` only on the source object, not the path `primary_location.source.type` on a work. The registration PR must include
a required test, which does not exist yet: `test_registration__source_type_path__matches_recorded_works_fixture`. This path is
unverified, and the test is its gate: it must pass before the registration PR merges. The fixture source is as follows. The
registration PR captures a recorded OpenAlex work response from the live API, with the API key redacted, and saves it under
`tests/fixtures/openalex/`. It is not hand-written, unlike the existing client cassettes that ADR 0030 (line 186) describes.
Its licence is checked before the commit. This record does not verify OpenAlex's licence terms. The fixture does not exist yet.
If the path is absent in the fixture, the registration is refused, not guessed.

**Language: no table.** Language is not in the map (Decision 2).

**Checks, all in the test suite:**

1. `MAP_SHA256` equals the recomputed digest of the three tables. A table edit without a version bump fails.
2. Every ASJC value in `SUBFIELD_TO_ASJC` passes `asjc.area_abbrev(code)[1]`, so the matcher knows it (ADR 0017,
   Constraints).
3. Every subfield id, `type` and source `type` that the map contains appears in a **recorded** OpenAlex response fixture.
   A table checked only against itself proves nothing (ADR 0017, Constraints).
4. Every `type` and source `type` value in those fixtures is either mapped or is a tested refusal.
5. The 252 rows of table (a) match the OpenAlex subfield count in `meta.count`. A row count that differs fails.

### 4. Records without a mapping

A registration whose required input cannot be derived is **refused**. The candidate stays resolved and
unregistered, and it is counted in the "resolved, not registered" line that ADR 0029 §3 requires. No
`register` event is written and no record is created. Scopus records are never refused for this reason, and this
record changes nothing for them.

### 5. The venue and the conference whitelist

`criteria.yaml`'s `doc_types.conference_whitelist` applies only to records whose `venue_type` is `conference`
(`_passes_conference_whitelist`, `prisma/engine.py` lines 297-320). A registered record's venue type is
therefore always one of the four closed values, read from its own row. It is never the empty string. A record
whose venue type is empty is never a registration, because the source type is required and refused when it is
missing. A registration with `venue_type = conference` is tested against the whitelist exactly as a Scopus
conference record is.

The engine reads `records_all` (Decision 1), so this holds without a `venues` row.

**`venue_name` for a registration** is the OpenAlex `primary_location.source.display_name`, and it is NOT NULL. A conference
registration is matched against the whitelist exactly as a Scopus conference is: a casefold substring match on `venue_name`
(`_passes_conference_whitelist`, `prisma/engine.py` lines 315-320). A `venue_name` that is missing, or empty after `strip()`, is to be **refused** for every venue type. The refusal point is the
registration writer, which does not exist yet. It is part of the later registration PR (ADR 0029 §5), so no code enforces this
rule today. The loader stores the stripped name, so the refusal check and the stored value use the same string. The engine does not strip
names (`prisma/engine.py` lines 319-320), so without the load-time strip a whitespace-only
name would otherwise fail every token and exclude a registered conference paper silently. The implementation PR must include two
tests, which do not exist yet: `test_registration__whitespace_venue_name__is_refused` and `test_registration__missing_venue_name__is_refused`.
Both assert that the event log's bytes and checksum are unchanged after the refusal. The refusal, not a silent pass or exclusion, is the rule, because an empty name would otherwise fail every
token and exclude a registered conference paper with no record of why.

Scopus records with a `NULL` `venue_id` keep today's behaviour, which passes the whitelist. This record does not
change that, and the limitation should be stated in `docs/methodology/limitations.md`.

### 6. What does not change

- `criteria.yaml`, the criteria validator and `asjc.area_abbrev` are unchanged. Registered subject codes match the
  way Scopus codes do.
- `_passes_subject_areas` and `_passes_language` keep their no-data rule for Scopus records. A registered record has
  its required inputs (`doc_type`, `venue_type`, `venue_name`, subject area) from the refusal rule of §2, so the subject-area
  filter never sees it with missing data. Its `language` is NULL, and `_passes_language` passes it (Consequence 7).
- The frozen `records`, `venues`, `subject_areas` and `runs` tables keep their columns.
- Payload references: a registered record's `payload_line` is `0`. ADR 0029's worked example (`line` 42 in
  `raw/run-0007/openalex-0001.jsonl`, line 91) is **superseded** by ADR 0031 Decision 2, under which each HTTP response
  is one file, `response-NNNN.jsonl`, holding one line. The example is kept in ADR 0029 unchanged.

## Alternatives considered

**Store registered records in `records` with a null `run_id`.** Rejected. `records` is shaped by Scopus payloads, and every
reader would need an origin check. A separate table and a view keep the Scopus path unchanged.

**Create a `venues` row for each registration.** Rejected. `venues` is keyed by Scopus venue ids and written from Scopus
payloads. Two sources would write one table, which the loader's rules do not allow.

**Put the map version and checksum on the `registered_records` row.** Rejected. ADR 0029 puts them on the `register` event.
A copy would be derived data that can drift.

**Derive `language` from OpenAlex's ISO 639-1 code through a map.** Rejected by owner decision, 2026-10-22. Every stored Scopus record has
`language` NULL (1,293 of 1,293 and 1,864 of 1,864, Context), so the Scopus spelling of each language cannot be checked against
any stored value. A map of about 37 unverified rows would be a plausible wrong table. The field stays NULL instead, and the existing
no-data rule applies to it.

**Map from topic (about 4,500 values) rather than subfield.** Rejected by owner decision, 2026-10-22. The subfield level gives 252
reviewable rows, and every topic belongs to one subfield.

**Derive subject areas at load time from the current table, without versions.** Rejected. A table edit would move records
between subject areas, and an old published number could not be reproduced.

## Consequences

1. The schema gains one table and one view. The registration PR touches two files that are not part of this record:
   `src/prismabib/store/schema.sql`, which gains the DDL for `registered_records` and the view `records_all` (the live-introspection
   test, BUILD_PLAN line 924, checks it against the database); and `src/prismabib/store/checksums.py`, whose `_TABLE_SORT_KEYS`
   (lines 50-68) gains `"registered_records": ("record_id",)`, so the table's checksum is computed in key order (ADR 0029 V14).
   A view has no checksum entry, so `records_all` is not checksummed. This is a Layer 1 addition under ADR 0012. The golden table-checksum
   snapshot gains a key for `registered_records`, which is a deliberate change that the implementation pull request must
   name under BUILD_PLAN line 927.
2. A Scopus-only store builds an empty `registered_records`. The row checksums of existing tables are unchanged.
3. **The engine changes its input.** `_fetch_record_attributes` reads `records_all` instead of `records` joined to
   `venues`. Scopus rows are read the same way, so their values are unchanged.
4. **The subject-area filter becomes asymmetric once registrations exist.** Most Scopus records carry no subject rows and
   pass the filter. A registered record carries subject rows and is filtered by them. The methods and
   `docs/methodology/limitations.md` must say so.
5. Registration throughput depends on the tables' coverage, which is not known until the map is checked against real
   registrations. Refused candidates are counted (§4), not hidden.
6. Registered records carry no author, affiliation or keyword rows in this version, so the screening view shows the title, the
   abstract and the venue only. This is decided for this version (2026-10-22). Adding authors or keywords for registrations
   is a later change, under its own ADR.
7. A registration's `language` is NULL, so `_passes_language` passes it. Every Scopus record in the two measured stores already
   behaves this way, so the language filter has no effect on either population in this version. The methods must say so.
8. **Counts (owner decision A, 2026-10-22).** Registrations count in `corpus.size`, `corpus.year_min` and `corpus.year_max` (`numbers.json`),
   and in the venue tables (`Corpus.venues`). The methods report the registered count next to those numbers, in one sentence:
   "N records, of which M are registrations from a manual Google Scholar search (ADR 0029)". The same number is the `flow.registered`
   key that ADR 0033 adds to `numbers.json`, so the count appears once in the data and once in the text.

**Deviations from the frozen contract.** Each item cites the project rule quoted in Status: "Contracts, schemas, and acceptance
criteria are not renegotiable without a new ADR (§2.6)." Item (ii) also cites BUILD_PLAN line 301. Each is named here, and each
needs the owner's acceptance under this record:

- **(i) Stage 2 and Stage 3 granularity.** ADR 0029 Owner decision 4 (line 250) and the register bullet (line 62) say "OpenAlex
  topics to ASJC codes". This record changes that to subfield to ASJC, by owner decision. A dated pointer note is added under Owner decision 4 in ADR 0029, and its body is unchanged. Basis: CLAUDE.md, "Contracts, schemas, and acceptance criteria are not renegotiable without a new ADR (§2.6)."
- **(ii) BUILD_PLAN §1.2, metadata source (line 54).** §1.2 fixes the metadata source as "Scopus API (institutional key)".
  Registered records bring OpenAlex-derived metadata (`doc_type`, `venue_type`, subject area, title and DOI) into Layer 1,
  next to Scopus records. Under BUILD_PLAN line 301, this is a deviation from §1.2 that needs this record. Basis: BUILD_PLAN line 301 (§1.2).
- **(iii) BUILD_PLAN `Corpus.records` (lines 893-895), and the readers of the record set.** The signature is unchanged, but
  `records(stage)` changes what it returns. It now includes registered records in `S_raw` and in each later stage, and its rows
  have 16 columns, not 13 (Decision 1). `raw_set`, `_fetch_record_attributes`, `load_records`, `Corpus.venues`, the `numbers.py` corpus
  scalars, `fulltext/run.py`, `fulltext/coverage.py`, `scripts/fulltext_missing.py` and `scripts/fetch_assist.py` change with
  it, as Decision 1 lists. Two of these are latent defects, not defects today, because no registration exists yet: `fulltext/run.py` would give
  a registration no DOI, and `scripts/fulltext_missing.py` would raise `KeyError` on one. Every caller that assumed a Scopus-only set or the 13 columns needs review. Basis: CLAUDE.md, "Contracts, schemas, and acceptance criteria are not renegotiable without a new ADR (§2.6)."
- **(iv) BUILD_PLAN Stage 3 schema (lines 853-873).** The new table `registered_records` and the view `records_all` are
  additions to a frozen schema (ADR 0012 rule). Basis: CLAUDE.md, "Contracts, schemas, and acceptance criteria are not renegotiable without a new ADR (§2.6)."
- **(v) `subject_areas` provenance.** The schema of `subject_areas` is unchanged, but its rows now hold OpenAlex-derived
  four-digit ASJC codes as well as Scopus ones. The criteria matching is unchanged, but the source of a row is no longer
  always Scopus. Basis: CLAUDE.md, "Contracts, schemas, and acceptance criteria are not renegotiable without a new ADR (§2.6)."
- **(vi) ADR 0031 select.** Adding `type`, `primary_location` and `primary_topic` changes the OpenAlex select
  that ADR 0031 Context (line 44) names. `language` is not added (deviation viii). The owner approved the change, and ADR 0031 has a dated note. Basis: CLAUDE.md, "Contracts, schemas, and acceptance criteria are not renegotiable without a new ADR (§2.6)." Bodies captured
  earlier lack the fields, and the loader refuses a registration that references one.
- **(vii) ADR 0029 example.** The `payload_line` example in ADR 0029 (line 91) is superseded by ADR 0031 Decision 2, as
  §6 above states. Basis: CLAUDE.md, "Contracts, schemas, and acceptance criteria are not renegotiable without a new ADR (§2.6)."
- **(viii) ADR 0029 Split records, the `language` input (line 231).** ADR 0029 names `language` as a registration input. This
  record drops it, by owner decision (Decision 2), because every stored Scopus record has `language` NULL. A registration's
  `language` is NULL, and the existing no-data rule passes it. Basis: CLAUDE.md, "Contracts, schemas, and acceptance criteria are not renegotiable without a new ADR (§2.6)."

## Open questions for the owner

None. The owner decided the open question of a conference-hosted article on 2026-10-22 (§3, row (b)). The acceptance of
this record is decided by the dedicated review named in Status.

## Related decisions

- [ADR 0007](0007-flow-counts-unsure-fields.md): the `FlowCounts` contract that ADR 0033 supersedes in full.
- [ADR 0011](0011-abstract-retrieval-for-subject-areas.md): the Layer 0 run precedent, and the quota path it rejected.
- [ADR 0012](0012-persisting-skipped-layer0-entries.md): the rule that a Layer 1 schema addition needs its own ADR.
- [ADR 0017](0017-subject-areas-match-by-asjc-grouping.md): subject areas match by ASJC grouping, and the mapping is checked
  against recorded pairs.
- [ADR 0028](0028-field-level-fallback-cascade.md): the loader is the only Layer 1 writer (Decision 3). The separation of OpenAlex topics from ASJC is stated in Context of this record, not in ADR 0028.
- [ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md): the registration this record makes possible. Its
  Owner decision 4 (line 250) and line 62 are changed by this record (deviation i).
- [ADR 0031](0031-openalex-responses-are-layer0-runs.md): the stored OpenAlex body, the `PayloadRef` form, and the select
  amendment (deviation vi).
- [ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md): the Stage 4 counts that read `records_all`.
