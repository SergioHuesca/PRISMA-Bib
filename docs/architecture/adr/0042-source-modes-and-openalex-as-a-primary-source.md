# ADR 0042: Source Modes, and OpenAlex as a Primary Source

## Status

Accepted — on the merge of the pull request that adds this record, which the owner approves. Until the merge, this record is `Proposed` on the branch `docs/adr-0042-source-modes`. Accepting it changes no code, test or golden: it fixes the design that the implementation pull requests build.

The first word under this heading is the one the repository's ADR governance test reads. `tests/unit/test_docs_governance.py` (`test_adr__on_main__is_not_still_proposed`) parses every ADR file and requires `Accepted`, `Superseded`, `Rejected` or `Deprecated`, so a literal `Proposed` would fail the suite on the branch. This record uses the form ADR 0041 used.

ADRs 0001 to 0041 are frozen and are not edited by this record. BUILD_PLAN.md is not edited. It is not published in this repository (ADR 0031, Context, cites `provenance.md`), so the frozen clauses are cited here as the ADRs quote them. Every amendment this record makes is stated in Decision 10, and no frozen file receives a note.

## Context

### The owner's goal

Real, independent users, most of whom have no Scopus access, must be able to run a full review. Today they cannot. Every search is a Scopus search, and `Settings` cannot be built without a Scopus key.

### What the code does now

Verified by reading the worktree at main `11ac2a6` on 2026-11-03. No shell was available, so nothing was run.

- **Search is Scopus only.** The `search` command (`src/prismabib/cli.py:381`) calls `capture_search` (`src/prismabib/capture/writer.py:347`), which builds a Scopus query from `project.toml` through `build_query_for_project` (`writer.py:392`, `src/prismabib/query.py:429`). The query language is Scopus field syntax (`TITLE-ABS-KEY`, `query.py:64`).
- **OpenAlex enriches or registers; it does not search.** The `cascade` and `openalex` commands are registered at `cli.py:104` and `cli.py:105`, and implemented at `capture/cascade_commands.py:320` and `:412`. `capture_openalex` (`capture/openalex.py:355`) takes DOIs and `(title, year)` pairs only. The OpenAlex client has no cursor paging: the word `cursor` does not occur in `src/prismabib/sources/openalex.py`.
- **Settings require a Scopus key.** `Settings.scopus_api_key` is a required `SecretStr` (`src/prismabib/config.py:110`). It is read from `.env` relative to the working directory (`config.py:105`). By reading the code, `capture_openalex` (`capture/openalex.py:406`) and `OpenAlexClient` (`sources/openalex.py:304`) build `Settings()` and so fail without `SCOPUS_API_KEY`. This was not reproduced.
- **`identified` is Scopus only.** `_identified_count` sums `runs.total_results` (`src/prismabib/prisma/flow.py:297-340`), and `runs` has no source column (`src/prismabib/store/schema.sql:15-18`).
- **Scopus is named in fixed text.** The flow diagram labels the identification box "Records identified from Scopus" (`src/prismabib/report/flow_diagram.py:225`). The methods text names the source of each cascade field (`src/prismabib/report/methods.py:87-108`).
- **`project.toml` has no source setting.** The default template holds `[project]` and `[query]` only (`src/prismabib/project.py:282-317`).
- **The ADR 0041 cascade is complete on main.** The cascade, the provenance columns, manual entry and conflict resolution are implemented. `records_all` already has 32 columns (`schema.sql:233-265`). `.claude/PROGRESS.md` records the completion on 2026-11-03.
- **Layer 1 readers already use the view.** Eleven files under `src/prismabib` (including `schema.sql`) mention `records_all`, so a further origin in the view reaches them without edits, as ADR 0032 intended.

### What OpenAlex provides

The vendor pages were read on 2026-11-03 through a summarising fetch, not verbatim. Each claim below is marked for a live re-check in the Unverified section at the end.

- Cursor paging starts at `cursor=*`, and the next cursor is `meta.next_cursor`. The maximum `per-page` is 100. Page-based access stops at 10,000 results, and cursor paging goes beyond that. The rate-limit page says that without a key "only the first page works".
- `search` covers title, abstract, full text and keywords. Operators are `AND`, `OR` and `NOT` in uppercase, double-quoted phrases, and a minus sign that does not exclude. Wildcards need `search.exact`. The whole request URL is limited to about 4 KB.
- A key is optional. It can be sent as an `api_key` parameter or as a bearer header, and the two forms are said to work identically. The client already uses the header (`sources/openalex.py:470`).
- Prices on the example-costs page: a single lookup is free, a list-and-filter call is $0.0001, and a search call is $0.001. A free key carries $1 of usage per day, and without a key the budget is $0.10 per day. The budget resets at midnight UTC.

## Owner decisions (recorded as given, 2026-11-03)

1. **Source mode per project.** A first-class question, "Do you have access to Scopus?", sets `[sources] mode` in `project.toml` to `openalex`, `scopus` or `scopus+openalex`. "No" enforces `openalex`: every Scopus-touching command refuses with an actionable message, no Scopus key is requested or read, and the cascade runs levels 4 and 5 only. `scopus` keeps v1.0.0 behaviour. `scopus+openalex` keeps today's cascade.
2. **Three places, one answer.** The question is asked by `prismabib init` (interactively, or with `--sources` for scripts; a non-interactive run without the flag refuses to guess), by `prismabib doctor`, and by the notebook and GUI views, which disable Scopus-only actions with a visible reason. `prismabib sources <slug> --set <mode>` changes the answer, and a change is an append-only event recording who, when, from and to. A "yes" is not verified silently. `doctor --check-scopus` and `credentials test scopus` are opt-in and spend one request.
3. **One guided credential step.** After a "yes", `prismabib credentials set scopus` prompts for the key with hidden input, an optional institutional token, an optional OpenAlex key and an optional Unpaywall email. It is non-interactive only through stdin or `--from-env`, never through argv. The GUI offers a "Connect Scopus" password panel that shows only "set" or "not set". `credentials status`, `credentials test scopus` and `credentials clear scopus` complete the set. Storage is the git-ignored `.env`, written atomically, changing only its own keys, and refused unless git confirms the file is ignored. The keyring alternative is recorded as considered and deferred. `Settings.scopus_api_key` becomes optional and is required only when a Scopus command runs. Switching to `openalex` does not delete stored credentials: the tool reports that they stay unused and offers `credentials clear`.
4. **OpenAlex as a primary source.** A search command captures OpenAlex works by query into sealed Layer 0 runs, with cursor paging, resume, an explicit quota, and no key in any stored body. It reuses `sources/openalex.py` and `capture/openalex.py` and follows ADR 0031.

## Decision

### 1. The source mode of a project

**Storage.** A project's mode is the `mode` key of a `[sources]` table in `project.toml`:

```toml
[sources]
mode = "scopus+openalex"   # "scopus" | "scopus+openalex" | "openalex"
```

The allowed values are these three strings, exactly. Any other value is a `ConfigError` that names the file and the value. The project model gains a `source_mode` property beside `title` (`project.py:556`), read on every access as `title` is.

**The legacy default.** A `project.toml` without a `[sources]` table has the effective mode `scopus+openalex`. That is today's behaviour on main, and it is the only default that keeps existing projects byte-identical, because the cascade (`cli.py:104`, `cascade_commands.py:320`) already runs for them. Mode `scopus` would refuse the cascade for every existing project that has used it. The owner's wording "`scopus` = v1.0.0 behaviour" does not settle this for legacy projects, so this default is Owner decision OQ-1.

**Byte identity for legacy projects.** A legacy project's `numbers.json`, methods text and flow diagram are unchanged by this record. The keys and values in `numbers.json` are the same as today, and the methods carry no mode sentence (Decision 6). Verification 1 to 3 pin this against the existing goldens `tests/golden/report/__snapshots__/numbers_keys.json` and `reference_numbers.json`.

**New projects.** `Project.init` writes the `[sources]` table when it creates `project.toml` (`project.py:469-471`). It never rewrites an existing file, as today.

**Transitions.** A change is refused whenever a screening decision exists in `decisions/decisions.jsonl`. Beyond that, the pair decides:

| From \ To | scopus | scopus+openalex | openalex |
|---|---|---|---|
| scopus | none | allowed | refused if a sealed Scopus search run exists |
| scopus+openalex | allowed | none | refused if a sealed Scopus search run exists |
| openalex | refused if a sealed OpenAlex search run exists | refused if a sealed OpenAlex search run exists | none |

Read the table this way. A move to `openalex` requires that no Scopus search run is sealed. A move away from `openalex` requires that no OpenAlex search run is sealed. A refused move names the blocking run or decision and says "create a new project", which is the owner's recommended route (OQ-15).

**The event log.** Each change appends one event to `decisions/source_modes.jsonl`, through `AppendOnlyLog` with its checksum sidecar. That is the same class and pattern as `registrations.jsonl` (`src/prismabib/log.py:400`, `write_event` at `:520`). The `decisions/*.jsonl` allowlist of the project `.gitignore` already tracks the file (`project.py:346-348`). An event has this shape:

```json
{"event_id": "01HZ7M3QK2W9T4R8N6Y1C5B0XD", "schema_version": 1, "action": "set_mode", "ts": "2026-11-03T10:00:00.000Z", "project": "example-review", "actor": "sh", "from_mode": "scopus", "to_mode": "openalex", "note": null}
```

`action` is `set_mode` for every change, and `from_mode` is `null` for the first event that `init` writes. `actor` is a named person and is required, as `reviewer` is for registrations. Folds sort by `event_id`, and `ts` is UTC.

**Consistency.** The last event's `to_mode` must equal the mode in `project.toml`. A file that has a `[sources]` table and no event, or an event that disagrees with the file, refuses to run and names both values. The fix is `prismabib sources <slug> --set <mode>`, which records the change. A legacy project has neither a table nor an event, and its mode is the legacy default. A hand edit to the table of a legacy project is therefore detected at the first command that reads the mode.

### 2. The question, and the three places that ask it

- **`prismabib init <slug>`** (`cli.py:225`). Interactively it asks "Do you have access to Scopus?". On "no" the mode is `openalex`. On "yes" it asks a second question, "Also fill missing fields of Scopus records from OpenAlex?", and the answer selects `scopus+openalex` or `scopus` (OQ-2). Without a terminal and without `--sources {openalex,scopus,scopus+openalex}` it refuses with a `ValidationError` and writes nothing. With `--sources` on an existing project whose mode differs, it refuses and points to `sources --set`. Re-running `init` on an existing project still never rewrites it (the docstring at `cli.py:238-239`).
- **`prismabib doctor <slug>`** (new, no network). It prints the mode, whether the mode is the legacy default, the key and file status that `credentials status` reports, and the next command. `--check-scopus` makes exactly one Scopus request, and it is refused in mode `openalex`.
- **Notebook and GUI views.** The Panel views read the mode and disable Scopus-only actions, with the reason shown beside each control. A project without a recorded choice shows the legacy mode and a button, "Record my choice", that writes the event.
- **`prismabib sources <slug> [--set MODE] [--actor NAME] [--note TEXT]`** (new). Without `--set` it prints the mode and the last event. With `--set` it validates the table in Decision 1, appends the event, and replaces only the `[sources]` table of `project.toml`, leaving every other byte in place.

The question never has a default answer. A default would be the silent choice the owner ruled out.

### 3. Enforcement matrix

The rows below are the Scopus- and OpenAlex-touching surfaces. They were found by grep over `src/prismabib/cli.py`, `capture/`, `sources/`, `fulltext/` and `screening/`, and by listing `notebooks/`. Each cell is **allowed**, **refused** (a `ConfigError`, exit status 1 from `_EXIT_KNOWN_ERROR`, `cli.py:80`), or **degraded** (allowed, with a named reduced scope).

| Surface (verified location) | Touches | scopus | scopus+openalex | openalex |
|---|---|---|---|---|
| `init` (`cli.py:225`) | none | allowed, asks | allowed, asks | allowed, asks |
| `search` (`cli.py:381`; `writer.py:347`) | Scopus Search COMPLETE | allowed | allowed | refused |
| `enrich` (`cli.py:719`; `enrich.py:662`) | Scopus Abstract Retrieval | allowed | allowed | refused |
| `cascade` (`cascade_commands.py:320`) | Scopus levels 2 and 3, OpenAlex levels 4 and 5 | refused | allowed (levels 1 to 5, as today) | degraded: candidate enrichment (level 4 by title) and manual entry only; `--opt-in` and `--scopus-budget` refused |
| `openalex` (`cascade_commands.py:412`; `capture/openalex.py:355`) | OpenAlex DOI and title lookup for Scopus records | refused | allowed | refused, pointing to `openalex-search` |
| `openalex-search` (new; Decision 4) | OpenAlex works search | refused | refused (OQ-3) | allowed |
| `register` (`cli.py:643`; `capture/registrations.py`) | OpenAlex body of a candidate | refused | allowed | allowed, with the duplicate check extended (Decision 9) |
| `registrations`, `reverse` (`cli.py:704`, `:682`) | none (read or reverse) | allowed | allowed | allowed |
| `candidates add` (`capture/candidate_commands.py:28`) | none (no request) | allowed | allowed | allowed |
| `manual-entry`, `manual-reverse` (`capture/manual_commands.py:36`, `:65`) | none (human entry) | allowed for `scopus:` records | allowed | allowed for `openalex:` records (Decision 9) |
| `conflict-resolve` (`manual_commands.py:90`) | none (events) | refused, no OpenAlex side | allowed | refused, no conflicts exist |
| `conflict-reverse` (`manual_commands.py:117`) | none | allowed | allowed | allowed |
| `build` (`cli.py:449`) | reads Layer 0 | loads Scopus runs; reports any OpenAlex search run as present but unused | loads Scopus runs and OpenAlex enrichment runs | loads OpenAlex search runs; reports Scopus runs as present but unused |
| `flow`, `export`, `fill` (`cli.py:624`, `:927`, `:978`) | reads the store | allowed | allowed | allowed, with mode labels (Decision 6) |
| `fulltext` (`cli.py:798`) | Elsevier ScienceDirect and Unpaywall, through `FullTextSettings` (`fulltext/resolve.py:935-936`) | allowed | allowed | allowed, with no Scopus key |
| `sources` (new) | none | allowed | allowed | allowed |
| `doctor` (new) | none, or one request with `--check-scopus` | allowed | allowed | `--check-scopus` refused |
| `credentials set`, `status`, `clear` (new) | `.env` only | allowed | allowed | allowed (credentials belong to the machine) |
| `credentials test scopus` (new) | one Scopus request | allowed | allowed | refused |
| GUI: registration view (`capture/registration_ui.py`) | register action | control disabled, reason shown | enabled | enabled |
| GUI: conflict panel (`capture/manual_ui.py:160`) | conflict resolution | disabled, reason shown | enabled | disabled, reason shown |
| GUI: manual form (`capture/manual_ui.py:66`) | manual entry | enabled | enabled | enabled |
| GUI: Connect Scopus panel (new) | `.env` only | enabled | enabled | enabled, labelled as unused in this mode |
| Notebooks `01_screen_title_abstract.ipynb`, `05_dashboard.ipynb` | read or screen | allowed | allowed | allowed |

**Library enforcement.** The same check runs in the library, not only in the CLI, because notebooks and scripts call the capture functions directly. The functions are `capture_search` (`writer.py:347`), `capture_abstracts` (`enrich.py:662`), `capture_scopus_cascade` (`capture/cascade.py:246`), `capture_openalex` (`capture/openalex.py:355`) and the new `capture_openalex_search`. Each calls one function in a new module, `src/prismabib/source_mode.py`, which owns the capability table and the refusal text. A test reads the table back (Verification 4), so a command or function with no declared capability fails the suite.

**Refusal error and message.** A mode refusal is a `ConfigError` (`src/prismabib/errors.py:46`). A missing key is also a `ConfigError`, as it is today (`config.py:139-164`). The message pattern is:

> `<command>` is not available in sources mode `<mode>` (projects/<slug>/project.toml, [sources] mode). <why, in one sentence>. <the fix: a `sources --set` that is allowed, or "create a new project">.

### 4. OpenAlex search capture, in Layer 0

**Command.** `prismabib openalex-search <slug> --budget N [--refresh] [--dry-run] [--root]`, in a new module `src/prismabib/capture/openalex_search_commands.py`, registered by one line in `cli.py`.

- `--budget N` is the number of pages, one HTTP request each. It has no default, as `--scopus-budget` has none (ADR 0041 Decision 7). At list price a 100-page budget costs at most $0.10.
- There is no `--opt-in`. OpenAlex is the only source in this mode, so there is no fallback to opt into and no refusal to record. This departs from the word the owner used, and it is OQ-13.
- `--dry-run` prints the query key, the `select`, the page budget and the cost bound. It makes no request and writes nothing.

**Request.** One `GET` to `https://api.openalex.org/works` with `search=<the query>`, `per-page=100`, `cursor=*` for the first page, then the `meta.next_cursor` of each response. The `select` is the one the client already uses (`sources/openalex.py:56-59`). The key goes in the `Authorization` header only (`sources/openalex.py:470`), never in `params`, the manifest or a body.

**Layer 0 layout.** This is a new run kind, separate from the acquisition runs of ADR 0031, because its identity is the query and not a set of identifiers:

```text
raw/openalex-search/<run_id>/
├── response-0000.jsonl   # one HTTP response per file, one line, ADR 0031 Decision 2 encoding
├── response-0001.jsonl
├── progress.json         # saved cursor and plan; deleted on seal; never hashed
└── manifest.json         # the seal: OpenAlexSearchRunManifest
```

The name `openalex-search` is added to `layout.NON_RUN_DIRNAMES` (`capture/layout.py:83-84`), as ADR 0031 Decision 8 does for `openalex`. The run id comes from `layout.new_run_id()`, and every write goes through `guard_writable`.

**Manifest.** `OpenAlexSearchRunManifest` is a new model in `capture/manifest.py`:

- `run_id`, `started_at`, `finished_at` (UTC), `endpoint` (the base URL only), `client_version`, `criteria_version`;
- `query` (the query as stored, Decision 7) and `query_key` (the query with its whitespace collapsed and stripped, which is the reuse key);
- `select` and `per_page` (100);
- `total_count`, the `meta.count` of the first response, or `null`;
- `works_captured`, the number of works in all responses;
- `complete` (bool), true only when the last response's `next_cursor` is null;
- `api_key_used` (bool), never the key;
- `requests`, one entry per HTTP request in order, each with `index`, `file`, `cursor_sent`, `sha256`, `encoding`, `raw_sha256` (when compact), `http_status`, `retrieved_at`, `result_count` and `next_cursor`.

**Seal rules.** The run seals only when all three hold:

1. the last response has no `next_cursor` (`complete` is true);
2. `total_count` is not null, and `works_captured` equals `total_count`;
3. every page was a 200.

A run that fails rule 2 raises `UpstreamError` (`errors.py:80`) and stays unsealed. A missing count is never estimated. This rule is what lets `identified` close (Decision 5).

**Keyless capture.** Without a key, a request for a second page is refused before it is sent. The refusal is a `ConfigError` that names `OPENALEX_API_KEY` and the free account. This follows the vendor statement that a keyless call returns only the first page. Without the guard, a keyless run would seal a truncated corpus, which is the bias ADR 0028 forbids. The guard depends on the vendor statement, which is an implementation check to confirm live (Unverified).

**Resume, budget and reuse.**

- A budget stop writes `progress.json` with the next cursor and leaves the run unsealed. The next call with the same plan (`query_key`, `select`, `per_page`) resumes from that cursor and does not request a page already on disk (ADR 0031 Verification 7).
- A sealed, complete run for the same `query_key` means the next call creates no run and makes no request. `--refresh` makes a new run. Its values update Layer 1 under ADR 0031 Decision 7, and it adds no membership (Decision 5).
- Unlike the cascade, a budget stop does not seal. A partial search is not a fact that identification can use.

**Sizes.** A keyed free account carries $1 a day, which is about 1,000 search pages, or 100,000 works at 100 per page. That covers a review-sized search in one day. Keyless access carries $0.10 a day and is limited to one page per query, so it cannot capture a review. These figures come from the vendor pages and are unverified.

### 5. Layer 1: where OpenAlex records live, and how they are counted

**The discriminator is `origin`, and the physical table is new.** Each OpenAlex-primary work becomes a row of a new table, `openalex_records`. `records_all` gains a third branch with `origin` `'openalex'`. Readers that already use the view (see the list below) see these records without any edit.

```sql
CREATE TABLE openalex_records (
  record_id TEXT PRIMARY KEY,         -- 'openalex:' || openalex_id
  openalex_id TEXT NOT NULL,
  doi TEXT,                           -- nullable: a work may have no DOI
  title TEXT NOT NULL,
  abstract TEXT,
  year INTEGER,                       -- nullable, as records.year is
  doc_type TEXT,                      -- the rule for unmapped types is in this Decision
  language TEXT,                      -- always NULL (ADR 0032 Decision 2)
  venue_type TEXT,                    -- closed set, or NULL when the source has no type
  venue_name TEXT,                    -- the source display name, stripped
  payload_file TEXT NOT NULL,         -- openalex-search/<run>/response-NNNN.jsonl
  payload_line INTEGER NOT NULL,      -- always 0 (ADR 0031 Decision 2)
  query_run_id TEXT NOT NULL,         -- the earliest sealed run that holds the work
  authors_json TEXT,
  affiliations_json TEXT,
  keywords_json TEXT,
  retrieved_at TIMESTAMP NOT NULL
);
```

Why a new table, and not one of the two that exist:

- **Not `records`.** `records` is written from Scopus payloads, its `run_id` names a Scopus search, and it is the table that Scopus-only checks read (`_scopus_duplicate`, `registrations.py:496`, and the build statistics). Mixing origins there would give every such reader an origin check. ADR 0032 rejected the same mixing for registrations.
- **Not `registered_records`.** Its rows are keyed `registered:<event_id>`, come from a reviewer's `register` event, and feed the `registered` count and the "other methods" box of the diagram (ADR 0029 §3). An OpenAlex search work has no reviewer event, and the search identifies it. Counting it as a registration would be false.

The new table follows the rule of ADR 0041 Decision 3, "Registered values". `authors_json`, `affiliations_json` and `keywords_json` are JSON arrays derived at load time from the stored body, by `authorships_to_fields` (`sources/openalex.py:171`) and `topics_to_keywords` (`:187`). The abstract is `rebuild_abstract` of the inverted index (`:158`). The source labels and levels are those of the registered branch: `openalex` at level 4 for abstract, authors and affiliations, and `openalex-topic` at level 4 for keywords. Keywords still feed `record_keywords` with kind `openalex-topic` (ADR 0030), so no new kind is added.

**The view.** `records_all` gains a third `UNION ALL` branch. Its 32 columns are those of the registered branch, with `origin` `'openalex'`, and the provenance expressions read from `openalex_records`. The payload reference is `payload_file || '#' || openalex_id`, the form ADR 0041 Decision 3 uses for OpenAlex.

**Identification.** A new table, `openalex_search_runs`, holds one row per sealed run: `run_id`, `query_key`, `query`, `total_count`, `works_captured` and `duplicates`. It is a new table and not a column of `runs`, because the frozen `runs` table must not change, and ADR 0012 treats an added table as the smaller deviation (OQ-5).

- `identified` in mode `openalex` is the sum of `total_count` over the distinct `query_key`s, each taken from its earliest sealed run. This is ADR 0013's rule, with OpenAlex's count in place of `total_results`.
- `duplicates_across_searches` in mode `openalex` counts the works that a run found under one `query_key` and that an earlier run had already loaded under a different `query_key`. Runs are taken in `run_id` order. A refresh of the same query is not a duplicate, which is ADR 0013's different-query condition. The count is stored in `openalex_search_runs.duplicates`.
- Membership comes only from the earliest sealed run per `query_key`, which is the run that `identified` counts. A refresh updates values under ADR 0031 Decision 7 and adds no member.
- `removed_other_reasons` counts OpenAlex works that cannot be loaded, such as a work with no title or no id. They are recorded in `malformed_entries` under ADR 0012.

**Why equation 1 closes.** For each query, the earliest sealed run holds exactly `total_count` works (seal rule 2). Each work is loaded as new, or counted as a duplicate of an earlier query, or counted as malformed. So `identified − duplicates_across_searches − removed_other_reasons` equals the number of loaded works, and ADR 0033's equation 1 holds, with `registered` and `excluded_automated` as before. Verification 31 tests this on a fixture with two overlapping queries.

**Deduplication key.** The key is the OpenAlex id, which is exact. A DOI shared by two different OpenAlex ids is not merged. The build report counts such collisions and lists them. They are not a `FlowCounts` term (OQ-16).

**Classification, reused.** The map of ADR 0032 (`openalex_map.py`, `MAP_VERSION` `"1"`) is used unchanged for three things: the subfield of `primary_topic` gives the one `subject_areas` row, `type` gives `doc_type`, and the source `type` gives `venue_type`. Two rules differ from ADR 0032 for these records, and both are amendments listed in Decision 10:

- **An unmapped `type` is stored raw, not refused.** ADR 0032 Decision 4 refuses a registration whose type has no target, because a refused registration is a candidate that stays out of the corpus. An OpenAlex search work is identified by the search, and refusing it would drop it from the count and break equation 1. Its raw type is stored in `doc_type`. The engine's `doc_types.include` rule then excludes it as it excludes any other type (OQ-6).
- **A work without a mapped subfield keeps no subject row.** It then passes the subject filter under the no-data rule that Scopus records already use (`prisma/engine.py`; ADR 0032, Context, cites the module docstring at lines 72 to 84). The methods count these works (OQ-7).

Language is NULL, for the reason ADR 0032 Decision 2 gives. A NULL language passes the language filter, as Scopus records already do.

**Readers.** Every reader of `records_all` gains the new origin, as ADR 0032 Decision 1 intended, and needs no edit. The readers that read `records` directly stay Scopus-only, and each is given a decision in PR4. They are `_scopus_duplicate` (`registrations.py:496`), the build statistics in `store/load.py`, and the unloadable count at `prisma/flow.py:394-404`. Each decision is one of two: it stays as it is because it reads a Scopus fact, or it moves to `records_all`.

**The subject-area asymmetry becomes total.** ADR 0032 Consequence 4 says that most Scopus records pass the subject filter, while a registration is filtered by its subject. In mode `openalex` almost every work carries a mapped subfield, so the subject filter becomes the main automated rule. The methods must say so, and `docs/methodology/limitations.md` gets a paragraph in PR7.

### 6. Numbers, the flow diagram and the methods, by mode

**`numbers.json` keeps the same keys in every mode.** A key whose presence depends on the mode would read "absent" where it means "zero", which is the reason ADR 0033 gives for `flow.registered`. Only values change.

- `flow.identified` is the mode's identification total. ADR 0033 §2 says that every function named `identified` returns the Scopus value alone. That sentence is amended to say it returns the total of the project's one identification source. A project has one, because the transition table forbids mixing Scopus and OpenAlex search.
- The other `flow.*` keys follow the same mode rules as `FlowCounts` (Decision 5).
- `corpus.*` and `venues.*` count `records_all`, so they include `openalex` rows. Venues are grouped by `venue_name`, as registrations are (ADR 0032 Decision 1).
- `methods.cascade.<field>.level1` to `level4` and `unavailable` count `records` and `openalex_records`. They do not count registrations, which keeps ADR 0041 Consequence 8. In mode `openalex` the level 1 to 3 counts are 0, the level 4 counts are the OpenAlex supplies, and `methods.cascade_conflicts_open` and `_resolved` are 0 because no Scopus side exists.
- `citations.*`: an OpenAlex-origin record has no citation snapshot, because no citation count is captured. Whether it is counted under the existing key `citations.registered_unavailable` (`report/numbers.py:315`), or only in methods text, is OQ-8. The recommended course is methods text only.

For a legacy project none of these values changes, because no `openalex` row exists in it.

**The flow diagram.** The identification box is labelled "Records identified from OpenAlex (n = …)" in mode `openalex`, and it keeps today's label in both Scopus modes (`flow_diagram.py:225`). The renderer receives the mode from its caller. The legacy bytes are unchanged, because the legacy mode is a Scopus mode.

**The methods text.** Explicit modes carry one source sentence. Legacy projects do not, because adding a sentence would change their bytes (OQ-14). The sentence for mode `openalex` is produced by a pure function in `report/methods.py`, beside `cascade_sentence` (`methods.py:112`), and it reads the queries and run dates from the store. Its shape is:

> Records were identified by searching OpenAlex; no Scopus search was run. The search was `{query}`, run on {run dates}. OpenAlex reported {identified} works across {queries} distinct queries, of which {duplicates} were found by more than one query and counted once. {registered} records were registered by a reviewer from manual Google Scholar searches (ADR 0029). Metadata, including abstracts rebuilt from OpenAlex's inverted index and subject areas taken from OpenAlex's primary topic, is OpenAlex's own (see Limitations).

For mode `scopus` the sentence names the Scopus search and states that no OpenAlex enrichment was run. The existing cascade sentence (`cascade_sentence`, `methods.py:112`) is unchanged in both Scopus modes.

**Limitations text, in PR7.** `docs/methodology/limitations.md` line 14 ("The only search source is the Scopus Search API") and `docs/methodology/prisma-mapping.md` line 266 ("This system searches Scopus and ScienceDirect only") are wrong for mode `openalex`, and they are rewritten in PR7. The phrase test `test_public_text__denying_the_field_level_cascade__is_absent` (`tests/unit/test_docs_governance.py:160`) still applies.

### 7. The OpenAlex query language

**What a user writes.** The `[openalex_query]` table of `project.toml` holds one key, `search`. Its value is a string in OpenAlex's `search` syntax, passed as the `search` parameter, which covers title, abstract, full text and keywords (vendor page, unverified live). v1 accepts only the forms in the table below, so the example shows one of them:

```toml
[openalex_query]
search = '"carbon capture" AND "policy"'
```

**What v1 accepts and refuses.**

| Input | v1 | Reason |
|---|---|---|
| Words, with an implicit AND | accepted | vendor: unseparated words are treated as AND |
| Uppercase `AND`, `OR`, `NOT`; double-quoted phrases | accepted | vendor, operators section |
| Parentheses | refused | not verified against the vendor page |
| A leading `-` | refused, with a hint to write `NOT` | a minus sign does not exclude in OpenAlex |
| `*` or `?` wildcards | refused | wildcards need the exact search, which v1 does not use |
| `~` (fuzzy or proximity) | refused | not used in v1 |
| Scopus field codes such as `TITLE-ABS-KEY(` or `AUTHKEY(` | refused, with a hint to write the phrase | they are Scopus syntax |
| Any key other than `search` (for example `filter`, `search.title`, `sort`, `sample`) | refused, since the table is `extra="forbid"` | scope; there is no year or type filter in v1 (see below) |
| An empty string | refused | nothing to search for |
| More than 2,000 characters | refused | the vendor limit is about 4 KB for the whole URL, and the margin is kept for the other parameters |

**Why there is no year or type filter.** Year and document type are applied after identification by `criteria.yaml`, as they are for Scopus. A filter at search time would move an exclusion from the criteria into the query, where the flow diagram cannot count it. The Scopus query guide makes the same point (`docs/how-to/build-a-query.md`, lines 11 to 22).

**Where the query is built.** `build_query` (`src/prismabib/query.py:375`) stays the Scopus renderer and is not changed. A new function, `build_openalex_query_for_project(project)`, is added beside `build_query_for_project` (`query.py:429`). It reads `[openalex_query]` with the same pattern, which is the file read, the table check and the `extra="forbid"` model, validates the rules above, and returns the string. The string is then sent unchanged. The two renderers never share a table, so a Scopus query cannot be read as an OpenAlex one.

**Documentation.** `docs/how-to/build-a-query.md`, titled "Build a Scopus Query", stays about Scopus and gains one pointer. A sibling page, `docs/how-to/build-an-openalex-query.md`, is added in PR7 with its nav entry in `mkdocs.yml`. Its TOML examples must parse, because `tests/unit/test_docs_examples.py` checks every how-to page.

**There is no `prismabib build_query` command.** No such command exists in `cli.py`. The query is built inside `search` (`writer.py:392`). A preview command, `prismabib query <slug>`, would print the rendered string without any request. It is optional and is not in the PR list (OQ-17).

### 8. Credentials

**Current state, and the blast radius.** `Settings` (`config.py:95-115`) reads `.env` from the working directory (`config.py:105`). `scopus_api_key` is required, so `Settings()` cannot be built without it. Nine call sites build `Settings()` by default: `capture/writer.py:434` (search), `capture/enrich.py:803` (abstracts), `capture/cascade.py:298` (Scopus levels), `capture/openalex.py:406` (OpenAlex capture), `sources/scopus.py:522` (the Scopus client), `sources/openalex.py:304` (the OpenAlex client), `sources/crossref.py:195`, `sources/unpaywall.py:112` and `sources/sciencedirect.py:106`. The Scopus client also sends the key in a header (`scopus.py:1011`). Tests reference `SCOPUS_API_KEY`, `scopus_api_key` or `Settings(` in 27 files, 91 occurrences, and `tests/unit/test_config.py` alone has five of them.

**The change.**

- `Settings.scopus_api_key` becomes `SecretStr | None = None` (`config.py:110`). The other optional keys stay as they are.
- One function is the only place that requires the key:

```python
def require_scopus_key(settings: Settings) -> SecretStr:
    """Return the Scopus key, or fail before any request is made."""
    if settings.scopus_api_key is None:
        raise ConfigError("SCOPUS_API_KEY is not set; run `prismabib credentials set scopus`.")
    return settings.scopus_api_key
```

- The Scopus paths call it: the Scopus client constructor (`scopus.py:522`, with the registration at `:531`) and its header (`:1011`), `capture_search` (`writer.py:434`), `capture_abstracts` (`enrich.py:803`) and `capture_scopus_cascade` (`cascade.py:298-299`, where the token list must drop a `None`). The OpenAlex, Crossref, Unpaywall and ScienceDirect clients do not require it. That removes the OpenAlex-only defect described in Context, and the mode check runs before any of these calls.
- The `.env` path does not change. It is relative to the working directory, as it is for `FullTextSettings` (`config.py:86`) and `ProjectsRootSettings` (`config.py:59`), so one file serves all of them.

**Writing `.env`.** The writer is a new module, `src/prismabib/credentials.py`. It refuses to write unless all three hold:

1. `git check-ignore -q -- .env` exits 0 in the working directory. The repository ignores `.env` and `*.env` (`.gitignore:2` and `:6`).
2. `git ls-files -- .env` lists nothing. A tracked file is not reported as ignored by `check-ignore`, so the first check alone would not catch a tracked file. The documented git behaviour is the basis, and it is an implementation check to confirm (Unverified).
3. git is installed and the directory is a repository. Otherwise the command refuses and says why (OQ-9).

When all three hold, the writer reads the existing file, replaces only the lines of the keys it manages (`SCOPUS_API_KEY`, `SCOPUS_INSTTOKEN`, `OPENALEX_API_KEY`, `UNPAYWALL_EMAIL`), appends the missing ones, and keeps every other line and comment. It writes a temporary file in the same directory, flushes and `fsync`s it, and replaces the original with `os.replace`. It rejects any value that contains a line break, and it quotes a value the way `python-dotenv` reads it (to confirm in PR2). A failed write leaves the old file in place.

**The commands.**

```bash
prismabib credentials set scopus          # hidden prompts; optional values may be left blank
prismabib credentials set scopus --from-env
type secrets.env | prismabib credentials set scopus --stdin   # KEY=VALUE lines, never argv
prismabib credentials status               # set or not set, file path, ignored yes or no
prismabib credentials test scopus          # one Scopus request; reports ok, 401, 403 or network
prismabib credentials clear scopus         # removes the Scopus keys from .env
```

No option accepts a key value. `status` prints "set" or "not set" for each key and never a value. `test scopus` reports the HTTP status or the network error class, and nothing from the response that could contain the key. It is refused in mode `openalex` (Decision 3), because that mode does not use Scopus.

**Output and logs.** The key is registered for redaction when a client is built (`sources/scopus.py:114`, called at `:531-533`). The structlog processor that applies it is installed at `:143-144`. OpenAlex does the same (`sources/openalex.py:315`), and its httpx filter (`:64`, `:90`) removes the key from the stdlib logger. The credential commands never log a value, and `SecretStr` renders as asterisks in `repr`.

**The GUI panel.** "Connect Scopus" is a password-type panel in a new module. It shows "set" or "not set", writes through the same `credentials` writer, and never echoes the input back.

**Mode and credentials.** Credentials belong to the machine, and the mode belongs to the project. A key in `.env` is unused by a project in mode `openalex`. `credentials status` says so when it runs inside a project, and `credentials clear scopus` is offered on a mode-change refusal (Decision 1).

**Considered and deferred: the keyring.** Windows Credential Manager, through the `keyring` package, keeps the key out of a plain file. It is deferred for four reasons. It adds a dependency. It is not visible to the notebook path and to headless runs in the same way. Its entries belong to the user account, not to a working copy. And it cannot be checked for being git-ignored, which is the property the owner asked the `.env` writer to enforce.

### 9. Registrations, manual entry and conflicts in mode `openalex`

**Registration (ADR 0029).** A reviewer may register a candidate in mode `openalex`. Registration is refused when the paper is already a record, and the duplicate check now has to test two tables. `_scopus_duplicate` (`registrations.py:488-496`) reads `records` only today. It is extended to `openalex_records`, by DOI and by normalised title and year, so that the rule "no registration of a paper the corpus already holds" (ADR 0029 §5) holds for both origins. Supersession follows the same logic: a later `openalex-search` run that finds an active registration's DOI writes a `supersede` event, as ADR 0029 §6 does for a Scopus match. This extends ADR 0029 §6, and it is OQ-12.

**Manual entry (ADR 0041 Decision 4).** The writer's `manual_target` (`capture/manual.py:83-104`) accepts only ids that begin with `SCOPUS_PREFIX` (`manual.py:31`). It is extended to accept `openalex:` ids too, when the field is `unavailable` in `openalex_records`. The refusal text changes from "no scopus record" to "no scopus or openalex record". ADR 0041's refusal list is amended in Decision 10. The labels, level 5 and the fold rules are unchanged.

**Conflicts (ADR 0041 Decision 2).** A conflict needs two values, one from Scopus and one from OpenAlex. In mode `openalex` there is no Scopus side, so there are no conflicts. `conflict-resolve` is refused, and the GUI panel is disabled with its reason shown. `conflict-reverse` stays allowed, because a reversal is never blocked.

**Keywords.** Keywords never conflict and take no manual value (ADR 0041 Decision 1b), in every mode.

### 10. What stays frozen, and what this record amends

**Stays as written.**

- ADRs 0001 to 0041, which are not edited. BUILD_PLAN.md is not edited and is not in the repository.
- The v1.0.0 claim (ADR 0027). It is a claim about the Scopus search, and mode `scopus` is that search.
- ADR 0028's rule that Layer 1 is written only by the loader (Decision 3), and that every attempt is a Layer 0 payload.
- ADR 0031's layout rules, one response per file, the key rules (Decision 6), the reuse rule (Decision 7) and the outcome rules (Decision 4). The new run kind follows them.
- ADR 0013's distinct-query rule, which `openalex_search_runs` reuses.
- ADR 0030's `openalex-topic` kind, ADR 0032's classification map (version 1, unchanged), ADR 0033's equations 1 to 5, and ADR 0041's provenance rules and merge rule.

**Amended by this record.** Each entry names the frozen text it changes. No frozen file receives a note.

a. **ADR 0013 and ADR 0033, Decision 1 and §2 (the `identified` source).** `identified` reads the project's one identification source: `runs` in the Scopus modes (unchanged), and `openalex_search_runs` in mode `openalex`. Equation 1 keeps its form. The ADR 0033 sentence "`identified` is read only from `runs`" is amended for mode `openalex` only.

b. **ADR 0031, Decision 1 ("OpenAlex identifies no records").** This is true of enrichment runs. It does not hold for `openalex-search` runs in mode `openalex`, which do identify records. The enrichment runs are unchanged.

c. **ADR 0032, Decision 1 (the view).** `records_all` gains a third branch, with origin `'openalex'`.

d. **ADR 0032, Decision 4 (refusal of an unmapped input).** For `openalex_records` only, an unmapped `type` is stored raw and the work is not refused (Decision 5). Registrations keep the refusal.

e. **ADR 0029, §5 and §6 (duplicates and supersession).** The duplicate check and the supersession cover `openalex_records` (Decision 9). ADR 0029 §6 names only a Scopus match.

f. **ADR 0041, Decision 4 (manual entry, writer refusal 1).** Accepts `openalex:` ids in mode `openalex` (Decision 9). **ADR 0041, Decision 5 and Consequence 8.** The cascade counts cover `records` and `openalex_records`, and never registrations.

g. **ADR 0033 §2 (the `identified` function).** Returns the total of the project's single identification source (Decision 6).

h. **BUILD_PLAN §1.2 and §2.6 (the metadata source, as quoted by ADR 0032).** A project in mode `openalex` takes its records from OpenAlex, which §1.2 does not name, so this is a deviation under §2.6 (line 301, as quoted in ADR 0031). Its basis is the same as ADR 0032's deviation (ii). S02-AC5 ("the only source of the identified number") is read as the same rule for the one identification source that a project has, and ADR 0029's wording is the model.

i. **ADR 0028, Resolved item 4 and the "never degrade" rule.** Mode `openalex` is not a fallback. It has no Scopus record to degrade, so the rule does not apply. No ADR 0028 text changes.

No frozen file receives a dated note. The owner's rule for this record is that this record states its amendments. ADR 0041 is the precedent for notes that the implementation pull requests append, and this record does not follow it.

## Alternatives considered

**OpenAlex only as `registered_records`.** Rejected. Registration is a human event for one candidate at a time (ADR 0029 §1, "one event per candidate"). Using it for a search would put thousands of search results into the diagram's "other methods" line, and label them as manual registrations.

**Infer the mode from key presence.** Rejected. A key in `.env` is a machine fact, not a methodology choice. A key may be present but unentitled, and a project would change meaning when a variable was set. The owner asked for a choice that is never silent.

**Keyring for the key (deferred).** See Decision 8.

**Mode per run, not per project.** Rejected. The identification, the identity of equation 1 and the methods all describe one corpus. A corpus built from two sources, with a different mode for each run, has no single identity and an ambiguous methods text. The project is the unit that a review's methods describe.

**Legacy default `scopus`.** Rejected for legacy projects, because it would refuse the cascade that they run today and so break byte identity. Open as OQ-1.

**Mode-dispatched `search`.** Not chosen for v1. One command whose cost and budget depend on the mode would make the explicit-budget rule (ADR 0041 Decision 7) harder to read. A separate `openalex-search` keeps `search` exactly as it is. Open as OQ-4.

**OpenAlex works in `records`.** Rejected (Decision 5).

**A `source` column on `runs`.** Considered. It changes the columns of a frozen table, which ADR 0012 treats as the larger deviation. Open as OQ-5, with a new table recommended.

**Automatic fallback from Scopus to OpenAlex for OpenAlex-only users.** Rejected. A corpus that silently switches source is the degradation ADR 0028 forbids. The mode is chosen, recorded and shown.

**One `[query]` table for both syntaxes.** Rejected. Scopus field codes and OpenAlex's search string cannot be read as one another. A shared table would let a Scopus query be sent to OpenAlex, or the reverse, without a visible error.

## Consequences

1. A project has one identification source, and its mode says which. The methods of an OpenAlex review name OpenAlex and its queries, and say that no Scopus search was run.
2. Legacy projects are unchanged in bytes. New projects are asked the question once, and the answer is stored and recorded.
3. A peer with no Scopus access can complete a review. The cost is an OpenAlex key for any search beyond one page (OQ-10), and the corpus is OpenAlex's, which differs from a Scopus corpus.
4. Scopus and OpenAlex searches are not comparable. A query written for one does not carry over, and the methods say so. The query recorded per run is the only record of what was searched.
5. A `ConfigError` names the blocked command, the mode and the fix, so no Scopus call is attempted and no fallback is silent.
6. The credential path is the only path that writes a secret, and it writes only to a file that git ignores. A peer outside a git repository cannot store a key with the writer (OQ-9).
7. `Settings` no longer requires a Scopus key for the paths that do not use Scopus. That is a fix in its own right.
8. Citation metrics for OpenAlex records are not captured. A review in mode `openalex` reports them as unavailable (OQ-8).
9. The subject criterion filters most OpenAlex records, so it does more work than it does for Scopus (Decision 5).
10. The new tables, the view branch and the new run kind each change a checksum or a golden. Each change is named in the pull request that makes it, as ADR 0041 requires.

## Verification the implementation must include

Each test is shown to fail on the defect it guards before the code that fixes it is merged, as ADR 0031 and ADR 0041 require. "Fails before" means the test is run against main, or against the stub that precedes the fix.

**Mode and legacy byte identity.**

1. `test_source_mode__absent_table__is_scopus_plus_openalex`. Fails before: no such property exists.
2. `test_numbers_json__legacy_project__bytes_unchanged`. A legacy fixture's `numbers.json` is compared byte for byte with the file from before the change. It passes after the change, and it fails if any key is added. It is anchored by `tests/golden/report/__snapshots__/numbers_keys.json` and `reference_numbers.json`.
3. `test_methods__legacy_project__no_source_sentence`. Fails if the sentence is emitted for a legacy project.
4. `test_capability_table__every_cli_command__has_a_declared_capability`. Iterates the registered commands and fails on any command missing from the table in `source_mode.py`.
5. `test_source_mode__invalid_value__config_error` and `test_source_mode__hand_edit_without_event__config_error`.
6. `test_sources_set__openalex_to_scopus__refused_with_sealed_openalex_run`, and one test for each other cell of the transition table.
7. `test_sources_set__decisions_present__refused`.
8. `test_sources_set__appends_one_event_and_keeps_other_toml_bytes`.
9. `test_source_modes_log__checksum_tamper__log_error`, through `AppendOnlyLog`.

**Enforcement, with no request before a refusal.**

10. `test_source_mode__openalex__search_refused_before_any_http`. Uses an `httpx.MockTransport` that fails the test on any call. Fails before: `search` has no mode check.
11. `test_source_mode__openalex__enrich_and_cascade_scopus_levels_refused`.
12. `test_library__capture_search__refused_in_openalex_mode`, which checks the same rule at the function boundary.
13. `test_init__non_interactive_without_sources_flag__refuses_and_writes_nothing`.
14. `test_init__sources_flag__writes_table_and_first_event`.
15. `test_doctor__default__makes_no_request`.

**No Scopus key in mode `openalex`.**

16. `test_openalex_search__without_scopus_key__succeeds`. Runs with `SCOPUS_API_KEY` unset. Fails before: `Settings()` requires the key.
17. `test_openalex_mode__scopus_client_never_constructed`. Monkeypatches the `ScopusClient` constructor to raise.

**OpenAlex search capture.**

18. `test_openalex_search__pages_by_cursor_until_next_cursor_is_null`. Uses a fake transport with three pages.
19. `test_openalex_search__budget_stop_leaves_run_unsealed_and_resumes_from_cursor`. Fails if a budget stop seals, or if a resume re-requests a page already on disk.
20. `test_openalex_search__seal_refused_when_works_captured_differs_from_total_count`.
21. `test_openalex_search__keyless_second_page_refused_before_request`.
22. `test_openalex_search__same_query_sealed__creates_no_run_and_no_request`.
23. `test_openalex_search__refresh_makes_new_run_and_adds_no_membership`.
24. `test_openalex_search__key_only_in_header_never_in_params_or_manifest`, in the style of ADR 0031 Verification 2.

**Query language.**

25. `test_openalex_query__scopus_field_code__refused`.
26. `test_openalex_query__leading_minus__refused_with_not_hint`.
27. `test_openalex_query__wildcard_fuzzy_parentheses__refused`.
28. `test_openalex_query__extra_key__refused`.
29. `test_openalex_query__over_2000_characters__refused`.
30. `test_openalex_query__renders_search_parameter_only`. It also checks that `build_query` output is unchanged for an existing `[query]` table.

**Identity and Layer 1.**

31. `test_flow_counts__openalex_mode__identity_closes_with_two_overlapping_queries`. The fixture is the one described in Decision 5.
32. `test_flow_counts__openalex_mode__duplicate_across_queries_counted_once`.
33. `test_flow_counts__same_query_refresh__not_a_duplicate`, which tests ADR 0013's condition.
34. `test_flow_counts__scopus_modes__values_unchanged`, against the reference fixture.
35. `test_records_all__openalex_branch__origin_openalex_and_32_columns`.
36. `test_openalex_records__unmapped_type__stored_raw_not_refused`.
37. `test_openalex_records__work_without_title__counted_as_removed_other_reasons`.
38. `test_rebuild__determinism__openalex_records_checksums_identical`, in the style of ADR 0041's rebuild-determinism test.
38a. `test_build__mode_openalex__scopus_runs_reported_unused_not_loaded`.

**Registrations, manual entry and conflicts.**

39. `test_registration__openalex_record_with_same_doi__refused`. Fails before: the check reads `records` only (`registrations.py:496`).
40. `test_supersede__openalex_search_finds_active_registration_doi__writes_one_event`. Only if OQ-12 is accepted.
41. `test_manual_entry__openalex_record_unavailable_field__accepted`, and `test_manual_entry__openalex_record_supplied_field__refused`.
42. `test_conflict_resolve__openalex_mode__refused`.

**Mode labels and the GUI.**

43. `test_flow_diagram__openalex_mode__label_names_openalex`, and `test_flow_diagram__scopus_modes__label_bytes_unchanged`.
44. `test_methods__openalex_mode__sentence_names_queries_and_run_dates`.
45. `test_gui__scopus_actions__disabled_with_reason_in_openalex_mode`. Asserts the widget state, not the rendered text.

**Credentials.**

46. `test_settings__scopus_key_optional__require_scopus_key_names_the_variable`. Fails before: the field is required.
47. `test_credentials_set__output_never_contains_the_key`. Captures stdout, stderr and the structlog output.
48. `test_credentials_set__no_option_accepts_a_value`.
49. `test_dotenv_writer__preserves_unrelated_lines_and_comments`.
50. `test_dotenv_writer__refuses_when_not_ignored`, `test_dotenv_writer__refuses_when_tracked` and `test_dotenv_writer__refuses_outside_a_repository`.
51. `test_dotenv_writer__failed_replace_leaves_old_file`. Fails if the write is not atomic.
52. `test_credentials_test_scopus__one_request_and_no_key_in_output`. Covers a 200, a 401, a 403 and a network error.
53. `test_redaction__registered_scopus_key_never_in_structlog_output`.

**No secret in any output or artifact.** These tests run on a sentinel key placed in `.env` and in the environment, after a full run in each mode. They scan every produced file and every captured stream for the sentinel and for its percent-encoded form.

54. `test_no_secret__raw_decisions_exports_and_manifests_clean`. Covers `raw/`, `decisions/`, `exports/`, `project.toml`, `numbers.json`, the methods text and the `manifest.json` of both run kinds.
55. `test_no_secret__cli_stdout_and_stderr_clean`, for every credential and sources command.

**Python in this record.** The one Python block in this record must pass `ruff format --check`. This is a manual check that the orchestrator runs before pushing. It is not a repository test.

## Implementation plan

Seven implementation pull requests follow this record (PR0). Each is stacked where a dependency exists, under ADR 0037.

| PR | Scope | Size | Depends on | Owns (hotspots) |
|---|---|---|---|---|
| PR0 | This record and its nav line | S | none | `docs/architecture/adr/0042-*.md`, `mkdocs.yml` |
| PR1 | Mode model: `[sources]`, the event log, the capability table and library checks, the `init` question and flag, the `sources` command, `doctor` without a network call, the legacy byte-identity tests | M | PR0 | `source_mode.py` (new), `project.py`, `cli.py` (one line each), `source_commands.py` (new) |
| PR2 | Credentials: optional `scopus_api_key`, `require_scopus_key`, the `.env` writer, the `credentials` commands, redaction and output tests, and the OpenAlex-only fix | M | PR0 | `config.py`, `credentials.py` (new), `sources/scopus.py`, `capture/writer.py`, `capture/enrich.py`, `capture/cascade.py`, the client defaults in `sources/`, `tests/unit/test_config.py` |
| PR3 | OpenAlex search capture, Layer 0 only: the cursor client method, the manifest model, the budget, resume, the seal rules, the keyless guard, `NON_RUN_DIRNAMES` | M | PR1 (the mode check) | `capture/openalex_search.py` (new), `capture/manifest.py`, `capture/layout.py`, `sources/openalex.py` (cursor method) |
| PR4 | Layer 1: `openalex_records`, `openalex_search_runs`, the third `records_all` branch, identification, duplicates, the duplicate checks, the checksum golden for the new tables | L | PR3 | `store/schema.sql`, `store/load.py`, `store/checksums.py`, `prisma/flow.py`, `capture/registrations.py` (the duplicate check only) |
| PR5 | Query language and command: `[openalex_query]`, `build_openalex_query_for_project`, the `openalex-search` command, the sibling how-to page and its nav line | S | PR1, PR3 | `query.py`, `capture/openalex_search_commands.py` (new), `docs/how-to/` |
| PR6 | Reports and GUI: the flow label by mode, the source sentence, the cascade counts over the new origin, the Panel actions, the credentials panel, supersession, manual entry for `openalex:` ids | M to L | PR4, PR2 | `report/methods.py`, `report/numbers.py`, `report/flow_diagram.py`, `capture/manual.py`, `capture/manual_ui.py`, `capture/registration_ui.py`, `credentials_ui.py` (new) |
| PR7 | Public text and demo: `limitations.md`, `prisma-mapping.md`, the content of the new how-to page, `run-the-demo.md`, the CHANGELOG entry | S | PR6 | `docs/methodology/*`, `docs/how-to/run-the-demo.md`, `CHANGELOG.md` |

Failing tests come first in each PR. Golden changes are named in the PR that makes them, as ADR 0041 requires, and no golden is regenerated to make a test pass. A live capture of a real review is not part of this plan. Any capture of Scopus data stays under the sharing gate of ADR 0041 (Risk 8).

## Risks

1. **A Scopus query does not carry over.** OpenAlex's `search` ranks by relevance over four fields, and it does not implement `TITLE-ABS-KEY`. A peer who reuses a Scopus query gets a different corpus. Mitigation: the query is recorded per run and in the methods, and the how-to says so.
2. **Keyless truncation.** Without a key, a query returns one page, so a keyless run cannot seal and cannot count. Mitigation: the guard in Decision 4. This depends on the vendor statement, which is re-checked live.
3. **The corpus changes with OpenAlex.** The index grows and is corrected. Mitigation: the retrieval date, the total and the payload bytes are stored per run, and a reproduction works from the bytes (ADR 0028 Resolved item 7).
4. **Coverage is not Scopus coverage.** ADR 0028 measured on 2026-10-21 that OpenAlex covers 1,084 of the 1,119 DOIs of the Scopus corpus. That measures OpenAlex against Scopus, not OpenAlex's own recall, and no recall figure exists for this design. The methods must not claim one.
5. **Classification is approximate.** The ADR 0032 map has 13 qualifier rows, and its venue-type rows are judgement calls. Mitigation: the map version is recorded, and qualifier rows are flagged as ADR 0032 requires.
6. **Raw OpenAlex types in `doc_type`.** An unmapped type reaches the engine as a raw string. Mitigation: the criteria rule excludes it, the build report counts the types, and the methods show the counts.
7. **Credentials in a working copy.** A `.env` in the wrong folder, or a `.env` tracked by mistake, could leak a key. Mitigation: the writer's three checks, the `*.env` rule, and the output tests (Verification 46 to 55).
8. **The blast radius of `Settings`.** Nine defaults and 91 test occurrences change. Mitigation: PR2 is its own pull request, and the tests that assert the required key are rewritten there.
9. **A hand-edited mode.** A file that says `openalex` while the log says `scopus` is refused at the first read (Decision 1). The check depends on the log, so a deleted log is refused too, and the fix is to record the mode again.
10. **Two identification sources in one folder.** A `raw/` folder can hold Scopus and OpenAlex search runs after a transition. The loader reports the unused kind rather than loading it, and the build report says so. Verification 38a tests this.
11. **Legacy projects after the change.** A legacy project never sees the new question, and its methods carry no source sentence. A reader cannot tell from the text which mode the project was, until the choice is recorded. Mitigation: `doctor` and the GUI say "legacy default" and offer to record the choice.
12. **Reviewer workload and quota.** An OpenAlex search of 100,000 works is 1,000 pages. The budget is explicit, and the dry run shows the cost bound. The source does not change the screening load, but a larger corpus is more to screen.

## Open questions for the owner

Each item below is an Owner decision needed. Where a recommendation is given, Decision writes it in, and that stands until the owner changes it.

- **OQ-1. Legacy default.** `scopus+openalex` (recommended, and written into Decision 1), or `scopus`. The owner's wording "`scopus` = v1.0.0 behaviour" conflicts with byte identity for legacy projects that run the cascade.
- **OQ-2. The second question for a "yes".** Ask "Also fill missing fields of Scopus records from OpenAlex?" (recommended, with a default of yes), or drop the question and default to `scopus+openalex`.
- **OQ-3. Can `scopus+openalex` also run `openalex-search`?** Recommended no. Adding OpenAlex-discovered works to a Scopus corpus creates two identification sources and a second duplicate rule.
- **OQ-4. A separate `openalex-search` command, or a mode-dispatched `search`.** Recommended: separate (Decisions 3 and 4).
- **OQ-5. Identification table.** A new table `openalex_search_runs` (recommended), or a `source` column on `runs`.
- **OQ-6. Unmapped `type`.** Stored raw and excluded by the criteria (recommended, Decision 5), or refused as ADR 0032 Decision 4 does. Refusal drops identified works from the count.
- **OQ-7. A work with no mapped subfield.** Passes the subject filter under the no-data rule (recommended), or is held for review.
- **OQ-8. Citations for OpenAlex-origin records.** Methods text only (recommended), reuse of `citations.registered_unavailable` (a naming problem), or a new key (which changes the key set for every mode, and the legacy bytes unless it is conditional).
- **OQ-9. A `.env` outside a git repository.** Refused, with the environment variables as the fallback (recommended). The alternative is a per-user configuration folder outside any repository, which needs a change to the path `Settings` reads.
- **OQ-10. An OpenAlex key.** Required for any search beyond one page, with refusal rather than truncation (recommended). Also whether to add `credentials set openalex` as a subcommand (recommended).
- **OQ-11. Mode `scopus` and the Scopus levels 2 and 3.** Mode `scopus` refuses the cascade entirely (recommended, and Decision 3). The alternative allows the Scopus levels only, with `--opt-in`.
- **OQ-12. Supersession by an OpenAlex search.** An OpenAlex work matching an active registration's DOI supersedes it (recommended). This extends ADR 0029 §6.
- **OQ-13. "Opt-in" for `openalex-search`.** The owner's word is "opt-in". The recommendation is a required `--budget` and no `--opt-in`, because this mode has no fallback.
- **OQ-14. The methods sentence for legacy projects.** None (recommended, for byte identity). A sentence for every mode would change the legacy bytes.
- **OQ-15. A mode change after screening decisions.** Refused, with "create a new project" (recommended, as the owner suggested). Confirmation requested.
- **OQ-16. Deduplication key.** The OpenAlex id, with DOI collisions reported (recommended), or the DOI.
- **OQ-17. A query preview command.** `prismabib query <slug>` would print the rendered query without a request. It is optional and is not in the plan.

## Unverified in this draft

Each item is a claim this record relies on that was not confirmed in the repository, or was confirmed only through a summarising fetch. The pull request that relies on it must check it first.

- **OpenAlex keyless paging.** "Without a key, only the first page works" comes from a summarising fetch of the rate-limit page. Re-check live before PR3.
- **`meta.count` on search responses.** The paging page's sample includes `meta.count`, but the page does not say that every response carries it. The seal rule refuses a run without it, which is the safe behaviour, but the claim must be checked live.
- **Prices.** The example-costs and pricing pages were read on 2026-11-03. Re-check before PR5. A third-party summary gave a different credit model, and this record does not use it.
- **OpenAlex parentheses and wildcard handling.** Parentheses are refused in v1 because they were not verified. Wildcard support needs `search.exact`, which v1 does not use.
- **The OpenAlex data licence.** Not verified. The licence terms of OpenAlex's data are needed before any shared sample, and ADR 0041's sharing gate (Risk 8) is the model.
- **`git check-ignore` with tracked files.** The second check in Decision 8 relies on git's documented behaviour, not on a run in this session.
- **`python-dotenv` quoting.** The writer's quoting rule is to be confirmed in PR2.
- **`Settings()` defaults in the Crossref, Unpaywall and ScienceDirect clients.** The defaults were read by line (`sources/crossref.py:195`, `sources/unpaywall.py:112`, `sources/sciencedirect.py:106`). Whether each client needs any Scopus value was not traced, and PR2 traces it.
- **BUILD_PLAN.md.** Not in this repository. Its clauses are cited as the ADRs quote them.
- **The `select` for search.** It is assumed to match the client's current `select` (`sources/openalex.py:56-59`), which includes the fields added on 2026-10-22.
- **Nothing was run.** No shell was available. Line numbers were read from the worktree on 2026-11-03, and the citations are as read.

## Records touched

- New: this file, `docs/architecture/adr/0042-source-modes-and-openalex-as-a-primary-source.md`.
- Edited: `mkdocs.yml`, one nav line after the ADR 0041 entry (`mkdocs.yml:128`). No other governance registration is required. `tests/unit/test_docs_governance.py` reads the status word and the changelog only, and no ADR index exists outside `mkdocs.yml`.
- Not touched by this record: code, tests, goldens, `CHANGELOG.md`, `.claude/PROGRESS.md`, BUILD_PLAN.md, and ADRs 0001 to 0041. The implementing pull requests own them, as the implementation plan assigns.

## Related decisions

- [ADR 0002](0002-append-only-decision-log.md) and [ADR 0024](0024-one-implementation-of-append-only-durability.md): the append-only log that records mode changes.
- [ADR 0003](0003-human-only-screening.md): mode changes and credentials are human acts, and nothing in this record screens.
- [ADR 0007](0007-flow-counts-unsure-fields.md), [ADR 0013](0013-identified-sums-across-searches.md) and [ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md): the identification and identity rules that Decision 5 reuses.
- [ADR 0012](0012-persisting-skipped-layer0-entries.md): the rule for adding a Layer 1 table, which `openalex_records` and `openalex_search_runs` follow.
- [ADR 0027](0027-what-v1-0-0-claims.md): the v1.0.0 claim, which mode `scopus` preserves.
- [ADR 0028](0028-field-level-fallback-cascade.md): the cascade and its no-degradation rule, which mode `openalex` does not break.
- [ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md): registration, which Decision 9 extends to OpenAlex records.
- [ADR 0030](0030-openalex-topics-as-a-keyword-kind.md): the `openalex-topic` kind, which is reused.
- [ADR 0031](0031-openalex-responses-are-layer0-runs.md): the OpenAlex run rules, which Decision 4 follows for a new run kind.
- [ADR 0032](0032-registered-records-table-and-classification-map.md): the view, the map and the refusal rule. Decision 5 amends one rule of it, for its own origin.
- [ADR 0037](0037-merge-train-and-native-stacked-prs.md): the stacking rule for the implementation pull requests.
- [ADR 0040](0040-year-range-in-analysis-engines.md) and [ADR 0041](0041-field-level-provenance-and-cascade-completion.md): the optional `methods.*` rule, and the cascade that mode `openalex` reuses.
