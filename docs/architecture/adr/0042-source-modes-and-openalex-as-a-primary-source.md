# ADR 0042: Source Modes, and OpenAlex as a Primary Source

## Status

Accepted — on the merge of the pull request that adds this record, which the owner approves. Until the merge, this record is `Proposed` on the branch `docs/adr-0042-source-modes`. Accepting it changes no code, test or golden: it fixes the design that the implementation pull requests build.

The first word under this heading is the one the repository's ADR governance test reads. `tests/unit/test_docs_governance.py` (`test_adr__on_main__is_not_still_proposed`) parses every ADR file and requires `Accepted`, `Superseded`, `Rejected` or `Deprecated`, so a literal `Proposed` would fail the suite on the branch. This record uses the form ADR 0041 used.

What is decided and what is open. The owner's answers of 2026-11-03 are Decisions and are not open. The owner's answers to the first draft are recorded in "Owner decisions". Where this record gives a recommendation under an "Owner decision needed" item, the recommendation stands as written unless the owner overrules it before the merge. Each such item is listed under "Open questions for the owner".

ADRs 0001 to 0041 and BUILD_PLAN.md are not edited. BUILD_PLAN.md is not published in this repository (ADR 0031, Context, cites `provenance.md`), so its clauses are cited as the ADRs quote them. Every amendment is stated in Decision 10. The frozen files therefore carry no note that points to this record. That is a gap against the convention that ADR 0041 followed, and Decision 10 says so.

## Context

### The owner's goal

Real, independent users, most of whom have no Scopus access, must be able to run a full review. Today they cannot. Every search is a Scopus-only search, and `Settings` cannot be built without a Scopus key.

### What the code does now

Verified by reading the worktree on 2026-11-03. No shell was available, so nothing was run.

- **Search is Scopus-only.** The `search` command (`src/prismabib/cli.py:381`) calls `capture_search` (`src/prismabib/capture/writer.py:347`), which builds a Scopus query from `project.toml` through `build_query_for_project` (`writer.py:392`, `src/prismabib/query.py:429`). The query language is Scopus field syntax (`TITLE-ABS-KEY`, `query.py:64`).
- **OpenAlex enriches or registers; it does not search.** The `cascade` and `openalex` commands are registered at `cli.py:104` and `cli.py:105`, and implemented at `capture/cascade_commands.py:320` and `:412`. `capture_openalex` (`capture/openalex.py:355`) takes DOIs and `(title, year)` pairs only. The OpenAlex client has no cursor paging: the word `cursor` does not occur in `src/prismabib/sources/openalex.py`.
- **The ADR 0041 implementation is on main, verified from code.** The commands above are registered. Manual entry and conflict resolution are registered at `cli.py:106` through `capture/manual_commands.py:36`, `:65`, `:90` and `:117`, and the panels exist in `capture/manual_ui.py`. The provenance columns and `records_all` are in `store/schema.sql:27-59` and `:233-265`. `.claude/PROGRESS.md` records the completion on 2026-11-03 as a second source.
- **Settings require a Scopus key.** `Settings.scopus_api_key` is a required `SecretStr` (`src/prismabib/config.py:110`), read from `.env` relative to the working directory (`config.py:105`). By reading the code, `capture_openalex` (`capture/openalex.py:406`) and `OpenAlexClient` (`sources/openalex.py:304`) build `Settings()` and so fail without `SCOPUS_API_KEY`. This was not reproduced.
- **`identified` is Scopus-only.** `_identified_count` sums `runs.total_results` (`src/prismabib/prisma/flow.py:297-340`), and `runs` has no source column (`src/prismabib/store/schema.sql:15-18`).
- **Scopus is named in fixed text.** The flow diagram labels the identification box "Records identified from Scopus" (`src/prismabib/report/flow_diagram.py:225`). The CLI text also names Scopus at `cli.py:93`, `cli.py:1056` and `cli.py:1118-1121`, and three tests pin the label (`tests/integration/report/test_flow_diagram.py:92` and `:204`, `tests/integration/prisma/test_registered_flow.py:325`).
- **`project.toml` has no source setting.** The default template holds `[project]` and `[query]` only (`src/prismabib/project.py:282-317`).
- **Readers of Layer 1 tables are not all view readers.** Several analyses read `records`, `record_authors`, `record_affiliations`, `record_keywords` and `citation_snapshots` directly, and some filter keywords by kind. Decision 6 lists them. ADR 0032's claim that readers "need no edit" holds only for the view itself.

### What OpenAlex provides

The vendor pages were read on 2026-11-03 through a summarising fetch, not verbatim. Each claim is marked for a live re-check in the Unverified section at the end.

- Cursor paging starts at `cursor=*`, and the next cursor is `meta.next_cursor`. The maximum `per-page` is 100. Page-based access stops at 10,000 results, and cursor paging goes beyond that. The rate-limit page says that without a key "only the first page works". Cursors may expire, which is a further assumption to check live.
- `search` covers title, abstract, full text and keywords. Operators are `AND`, `OR` and `NOT` in uppercase, double-quoted phrases, and a minus sign that does not exclude. Wildcards need `search.exact`. The whole request URL is limited to about 4 KB.
- A key is optional. It can be sent as an `api_key` parameter or as a bearer header, and the two forms are said to work identically. The client already uses the header (`sources/openalex.py:470`).
- Prices on the example-costs page: a single lookup is free, a list-and-filter call is $0.0001, and a search call is $0.001. A free key carries $1 of usage per day, and without a key the budget is $0.10 per day. The budget resets at midnight UTC.

## Owner decisions

### Recorded from the first draft (2026-11-03)

1. **Source mode per project.** A first-class question, "Do you have access to Scopus?", sets `[sources] mode` in `project.toml` to `openalex`, `scopus` or `scopus+openalex`. "No" enforces `openalex`: every Scopus-touching command refuses with an actionable message, no Scopus key is requested or read, and the cascade runs levels 4 and 5 only. `scopus` keeps v1.0.0 behaviour. `scopus+openalex` keeps today's cascade.
2. **Three places, one answer.** The question is asked by `prismabib init` (interactively, or with `--sources` for scripts), by `prismabib doctor`, and by the notebook and GUI views, which disable Scopus-only actions with a visible reason. `prismabib sources <slug> --set <mode>` changes the answer, and a change is an append-only event recording who, when, from and to. A "yes" is not verified silently. `doctor --check-scopus` and `credentials test scopus` are opt-in and spend one request.
3. **One guided credential step.** After a "yes", `prismabib credentials set scopus` prompts for the key with hidden input, an optional institutional token, an optional OpenAlex key and an optional Unpaywall email. It is non-interactive only through stdin or `--from-env`, never through argv. The GUI offers a "Connect Scopus" password panel that shows only "set" or "not set". `credentials status`, `credentials test scopus` and `credentials clear scopus` complete the set. Storage is the git-ignored `.env`, written atomically, changing only its own keys, and refused unless git confirms the file is ignored. The keyring alternative is recorded as considered and deferred. `Settings.scopus_api_key` becomes optional and is required only when a Scopus command runs. Switching to `openalex` does not delete stored credentials: the tool reports that they stay unused and offers `credentials clear`.
4. **OpenAlex as a primary source.** A search command captures OpenAlex works by query into sealed Layer 0 runs, with cursor paging, resume, an explicit quota, and no key in any stored body. It reuses `sources/openalex.py` and `capture/openalex.py` and follows ADR 0031.

### Decided in the second round (2026-11-03)

These four are recorded as Decisions and are not open.

- **D-1. Scripted `init` refuses without `--sources`.** A non-interactive `prismabib init` with no `--sources` flag refuses and writes nothing (Decision 2). Every existing call site is updated in PR1 (Decision 2 lists them).
- **D-2. Every analysis works for OpenAlex-origin records in v1.** Authors, affiliations, countries, co-authorship, keywords, citations, venues, trends and the taxonomy coder all take OpenAlex-origin records and produce real values, or fail with a named refusal. None may be silently empty or silently zero (Decision 6, and Verification 41 to 45).
- **D-3. Manual entry for `openalex:` ids is required in v1.** A reviewer can enter a value for an unavailable field of an OpenAlex-origin record, with the same labels and fold rules as for Scopus records (Decision 9).
- **D-4. A resumed run whose `meta.count` has changed is discarded and restarted.** The run is never sealed, the old run is kept on disk as an audit trail, and the command says "discard and restart" (Decision 4).

### Closed in the second round

- **OQ-9 (a `.env` outside a git repository)** is closed by the owner's rule: the writer refuses unless git confirms the file is ignored. Decision 8 states it as a Decision.
- **OQ-10, second half** is closed: `credentials set openalex` and `credentials set unpaywall` are in PR2 scope, not optional (Decision 8). The first half, whether an OpenAlex key is required for any search, is Q-D.
- **OQ-12 (supersession by an OpenAlex search)** is closed as required, not optional (Decision 9).

## Decision

### 1. The source mode of a project

**Storage.** A project's mode is the `mode` key of a `[sources]` table in `project.toml`:

```toml
[sources]
mode = "scopus+openalex"   # "scopus" | "scopus+openalex" | "openalex"
```

The allowed values are these three strings, exactly. Any other value is a `ConfigError` that names the file and the value. The project model gains a `source_mode` property beside `title` (`project.py:556`), read on every access as `title` is.

**The legacy default.** A `project.toml` without a `[sources]` table has the effective mode `scopus+openalex`. That is today's behaviour on main, and it is the only default that keeps existing projects byte-identical, because the cascade (`cli.py:104`, `cascade_commands.py:320`) already runs for them. Mode `scopus` would refuse the cascade for every existing project that has used it. The owner's wording "`scopus` = v1.0.0 behaviour" does not settle this for legacy projects, so this default is OQ-1.

**Byte identity for legacy projects.** A legacy project's `numbers.json`, methods text and flow diagram are unchanged by this record. One condition is the store. A legacy store must be rebuilt by the new code before it is read (the store stamp below). A store that has not been rebuilt is refused, never read as zero. The byte-identity tests (Verification 1 to 3) therefore run on a store rebuilt by the new code, and the goldens `tests/golden/report/__snapshots__/numbers_keys.json` and `reference_numbers.json` are the anchors.

**New projects (F3).** `Project.init` gains a keyword `source_mode: SourceMode | None = None`. The current signature (`project.py:438`) has no such argument. With `None`, which is what every existing caller gets, it writes no `[sources]` table and no event, and the project has the legacy default. Only `prismabib init` passes a mode, and it appends the first event in the same call, so no project with a table lacks its event. The grep finds 114 `Project.init(` occurrences in 39 files, across tests, source and docs. All of them keep the `None` default, so none of them starts refusing. `Project.init` never reads the Scopus key: `tests/integration/test_project.py:412-433` pins that, and it stays true. `Project.init` never rewrites an existing file, as today (`project.py:469-471`).

**Transitions.** A change is refused whenever a screening decision exists in `decisions/decisions.jsonl`. A change is also refused when any run directory of the blocked kind exists, sealed, unsealed or discarded. A discarded run still blocks, because it is kept on disk as an audit trail (Decision 4). Unsealed leftovers count because a partial run holds state that the other mode would misread. The table gives the pairs:

| From \ To | scopus | scopus+openalex | openalex |
|---|---|---|---|
| scopus | none | allowed | refused if any Scopus search run exists |
| scopus+openalex | allowed, unless an OpenAlex enrichment run or an active registration exists (Q-B) | none | refused if any Scopus search run exists |
| openalex | refused if any OpenAlex search run exists | refused if any OpenAlex search run exists | none |

A refused move names the blocking run or decision and says "create a new project", which is the owner's recommended route (OQ-15). Under Q-B, a move from `scopus+openalex` to `scopus` is refused when OpenAlex enrichment or a registration exists, because the methods of mode `scopus` would then be false.

**The event log.** Each change appends one event to `decisions/source_modes.jsonl`, through `AppendOnlyLog` with its checksum sidecar. That is the same class and pattern as `registrations.jsonl` (`src/prismabib/log.py:400`, `write_event` at `:520`). The `decisions/*.jsonl` allowlist of the project `.gitignore` already tracks the file (`project.py:346-348`). An event has this shape:

```json
{"event_id": "01HZ7M3QK2W9T4R8N6Y1C5B0XD", "schema_version": 1, "action": "set_mode", "ts": "2026-11-03T10:00:00.000Z", "project": "example-review", "actor": "sh", "from_mode": "scopus", "to_mode": "openalex", "note": null}
```

`action` is `set_mode` for every change, and `from_mode` is `null` for the first event that `init` writes. `actor` is a named person and is required, as `reviewer` is for registrations. Folds sort by `event_id`, and `ts` is UTC.

**Consistency.** The last event's `to_mode` must equal the mode in `project.toml`. A file that has a `[sources]` table and no event, or an event that disagrees with the file, refuses to run and names both values. The fix is `prismabib sources <slug> --set <mode>`, which records the change. A legacy project has neither a table nor an event, and its mode is the legacy default.

**The store stamp.** Every `build_store` run writes `store/build_stamp.json`, a sidecar beside the DuckDB file, with the `source_mode` and a `schema` number. The `schema` number is a constant, `STORE_SCHEMA`, that PR4a bumps when it adds the new tables. The stamp is written inside the same swap as the store, by `_install_store` (`store/load.py:3152`, with `os.replace` at `:3172`). It is gated on `installed` (`load.py:3366`), so a failed build cannot leave a stamp that does not match the store it sits beside. Two readers enforce it:

- `store/db.py:19` (`connect`), the gateway that most readers use, refuses a store whose stamp names another mode. The message says "run `build --rebuild`". The readers that open DuckDB directly are listed in Decision 6 and must take the same check. PR1 confirms the list.
- `build_store` without `--rebuild` (`store/load.py:3273-3286`) refuses the same way, so `build` cannot reuse a store built under another mode.

**A store with no stamp (F5a).** A store with no stamp was built before this record. Before PR4a it is read as mode `scopus+openalex`, schema 0, and the existing checks apply unchanged. Once PR4a adds the new tables to `TABLE_NAMES` (`store/checksums.py:75`), a store with no stamp is refused with "needs rebuild", through the existing stale-schema guard `_refuse_stale_schema` (`store/db.py:65-103`, called from `connect` at `:61`). That guard already refuses any store that is missing some of `TABLE_NAMES`, and this record relies on that mechanism. `_store_has_provenance` (`load.py:3127`) is the precedent for the same pattern. No reader treats a missing table as zero. Verification 27 to 29 test this.

Q-A recommends that these refusals are hard refusals and not warnings. Decision 1 is written that way.

### 2. The question, and the places that ask it

- **`prismabib init <slug>`** (`cli.py:225`, with `Project.init` at `cli.py:251`). Interactively it asks "Do you have access to Scopus?". On "no" the mode is `openalex`. On "yes" it asks a second question, "Also fill missing fields of Scopus records from OpenAlex?", and the answer selects `scopus+openalex` or `scopus` (OQ-2). A non-interactive run must pass `--sources {openalex,scopus,scopus+openalex}`. An interactive run is detected as `sys.stdin.isatty()`. Without `--sources`, and without a terminal, `init` refuses with a `ValidationError` (`errors.py:96`) and writes nothing (D-1). With `--sources` on an existing project whose mode differs, it refuses and points to `sources --set`. Re-running `init` on an existing project still never rewrites it (the docstring at `cli.py:238-239`).
- **The actor (F4).** The first `set_mode` event needs a named actor, so `init` also takes `--actor NAME`. When `--actor` is omitted, the actor is read from `git config user.name` in the working directory. If neither gives a non-blank name, `init` refuses and writes nothing. The interactive path asks for the name when neither is set.
- **`prismabib doctor <slug>`** (new, no network). It prints the mode, whether the mode is the legacy default, the key status that `credentials status` reports, and the next command. `--check-scopus` makes exactly one Scopus request, and it is refused in mode `openalex`.
- **Notebook and GUI views.** The Panel views read the mode and disable Scopus-only actions, with the reason shown beside each control. A project without a recorded choice shows the legacy mode and a button, "Record my choice", that writes the event.
- **`prismabib sources <slug> [--set MODE] [--actor NAME] [--note TEXT]`** (new). Without `--set` it prints the mode and the last event. With `--set` it validates the table in Decision 1, appends the event, and replaces only the `[sources]` table of `project.toml`, leaving every other byte in place.

The question never has a default answer. A default would be the silent choice the owner ruled out.

**Every `init` call site that PR1 updates.** The inventory was checked by grep. The reviewer's count of 55 hits in 25 files included `git init` calls, which this record does not touch. The real `prismabib init` call sites are these:

- `tests/integration/test_cli.py:122`, `:141`, `:153`, `:164`, `:169` and `:709`, which call `runner.invoke(app, ["init", ...])` and need `--sources` and `--actor`. `test_cli.py:92` only checks `--help` and needs no change.
- `README.md:45`, `examples/README.md:41`, `docs/getting-started.md:124` (and the table row at `:56`), `docs/index.md:105`, `docs/how-to/run-a-new-review.md:51`, `docs/how-to/run-the-demo.md:24` and `:179`, and `docs/how-to/build-a-query.md:469`. Each shows a command the reader runs in a terminal. Each gets `--sources` (and `--actor` where it runs non-interactively) so the example is correct when pasted into a script.
- `examples/worked_example.py:153` prints an instruction and runs nothing. It is updated for its text only.
- `docs/how-to/build-a-query.md:220` and `src/prismabib/config.py:45` name `prismabib init` in prose. They are checked for the new flag wording.
- No CI workflow under `.github/` and no file under `scripts/` runs `prismabib init`, so no CI job breaks.
- `CHANGELOG.md` is history and is not edited.

### 3. Enforcement matrix

The rows below are the Scopus- and OpenAlex-touching surfaces, found by grep over `src/prismabib/cli.py`, `capture/`, `sources/`, `fulltext/` and `screening/`, and by listing `notebooks/`. Each cell is **allowed**, **refused** (a `ConfigError`, exit status 1 from `_EXIT_KNOWN_ERROR`, `cli.py:80`), or **degraded** (allowed, with a named reduced scope).

| Surface (verified location) | Touches | scopus | scopus+openalex | openalex |
|---|---|---|---|---|
| `init` (`cli.py:225`) | none | allowed, asks | allowed, asks | allowed, asks |
| `search` (`cli.py:381`; `writer.py:347`) | Scopus Search COMPLETE | allowed | allowed | refused |
| `enrich` (`cli.py:719`; `enrich.py:662`) | Scopus Abstract Retrieval | allowed | allowed | refused |
| `cascade` (`cascade_commands.py:320`) | Scopus levels 2 and 3 with `--opt-in`; OpenAlex levels 4 and 5 | refused | allowed, levels 1 to 5 as today | degraded: candidates only, through the `openalex_lookup` capability (see below); `--opt-in` and `--scopus-budget` refused |
| `openalex` (`cascade_commands.py:412`) | OpenAlex lookup for Scopus records (capability `openalex_command`) | refused | allowed | refused, pointing to `openalex-search` |
| `openalex-search` (new; Decision 4) | OpenAlex works search (capability `openalex_search`) | refused | refused (OQ-3) | allowed |
| `register` (`cli.py:643`; `capture/registrations.py`) | reads a sealed OpenAlex body; makes no request | refused | allowed | allowed, with the duplicate check and supersession of Decision 9 |
| `registrations`, `reverse` (`cli.py:704`, `:682`) | none (read or reverse) | allowed | allowed | allowed |
| `candidates add` (`capture/candidate_commands.py:28`) | none (no request) | allowed | allowed | allowed |
| `manual-entry`, `manual-reverse` (`capture/manual_commands.py:36`, `:65`) | none (human entry) | allowed for `scopus:` records | allowed | allowed for `openalex:` records (Decision 9) |
| `conflict-resolve` (`manual_commands.py:90`) | none (events) | refused, no OpenAlex side | allowed | refused, no conflicts exist |
| `conflict-reverse` (`manual_commands.py:117`) | none | allowed | allowed | allowed |
| `build` (`cli.py:449`) | reads Layer 0 and writes the stamp | loads Scopus runs; loads the registrations and any OpenAlex enrichment run that exists; reports any OpenAlex search run as present but unused | loads Scopus runs, OpenAlex enrichment runs and registrations | loads OpenAlex search runs, OpenAlex enrichment runs and registrations; reports Scopus runs as present but unused |
| `flow`, `export`, `fill` (`cli.py:624`, `:927`, `:978`) | reads the store | allowed; refused on a stamp mismatch | allowed | allowed, with mode labels (Decision 6) |
| `fulltext` (`cli.py:798`) | Elsevier ScienceDirect and Unpaywall, through `FullTextSettings` (`config.py:67-92`, `fulltext/resolve.py:935-936`) | allowed | allowed | allowed, with no Scopus key; this is already true on main |
| `sources` (new) | none | allowed | allowed | allowed |
| `doctor` (new) | none, or one request with `--check-scopus` | allowed | allowed | `--check-scopus` refused |
| `credentials set`, `status`, `clear` (new) | `.env` only | allowed | allowed | allowed (credentials belong to the machine) |
| `credentials test scopus` (new) | one Scopus request | allowed | allowed | refused |
| GUI: registration view (`capture/registration_ui.py`) | register action | control disabled, reason shown | enabled | enabled |
| GUI: conflict panel (`capture/manual_ui.py:160`) | conflict resolution | disabled, reason shown | enabled | disabled, reason shown |
| GUI: manual form (`capture/manual_ui.py:66`) | manual entry | enabled | enabled | enabled |
| GUI: Connect Scopus panel (new) | `.env` only | enabled | enabled | enabled, labelled as unused in this mode |
| Notebooks `01_screen_title_abstract.ipynb`, `05_dashboard.ipynb` | read or screen | allowed | allowed | allowed |

**Capability per call site.** The library check is one capability per call site, not one rule per function. That keeps the cascade's own call from being refused by the check that refuses the public `openalex` command. `capture_openalex` takes a `caller` keyword, and the capability table reads it:

- `scopus_search` (`capture_search`, `writer.py:347`): modes `scopus` and `scopus+openalex`.
- `scopus_abstract` (`capture_abstracts`, `enrich.py:662`): modes `scopus` and `scopus+openalex`.
- `scopus_cascade` (`capture_scopus_cascade`, `cascade.py:246`): mode `scopus+openalex` only.
- `openalex_command` (`capture_openalex` with `caller="openalex_command"`, the public `openalex` command): mode `scopus+openalex` only. It enriches Scopus records, which mode `openalex` does not have.
- `openalex_lookup` (`capture_openalex` with `caller="cascade"`, which the cascade calls at `cascade_commands.py:379` and `:404`): modes `scopus+openalex` and `openalex`. The name `cascade` covers both the record targets and the candidates the cascade passes, since `_targets` (`:79-112`) serves both.
- `openalex_search` (`capture_openalex_search`): mode `openalex` only.

`capture_openalex` gains the `caller` keyword in PR1. It has no default, and the capability table refuses `None`, so no caller can omit it silently. What the cascade does in mode `openalex` follows from its code. `_targets` reads `records` (`cascade_commands.py:88`), and in mode `openalex` that table has no rows, so the record levels have no targets. Only candidate targets remain, and the cascade enriches them at level 4 by title. `--opt-in` and `--scopus-budget` are refused. The cascade's final `build_store(project, rebuild=True)` (`:407`) runs in every mode and is covered by the stamp (Decision 1).

**Library enforcement, generally.** The same check runs in the library, not only in the CLI, because notebooks and scripts call the capture functions directly. The check lives in a new module, `src/prismabib/source_mode.py`, which owns the capability table and the refusal text. A test reads the table back (Verification 4), so a command or function with no declared capability fails the suite.

**Refusal error and message.** A mode refusal is a `ConfigError` (`src/prismabib/errors.py:46`). A missing key is also a `ConfigError`, as it is today (`config.py:139-164`). The message pattern is:

> `<command>` is not available in sources mode `<mode>` (projects/<slug>/project.toml, [sources] mode). <why, in one sentence>. <the fix: a `sources --set` that is allowed, or "create a new project">.

### 4. OpenAlex search capture, in Layer 0

**Command.** `prismabib openalex-search <slug> --budget N [--refresh] [--dry-run] [--root]`, in a new module `src/prismabib/capture/openalex_search_commands.py`, registered by one line in `cli.py`.

- `--budget N` is the number of pages, one HTTP request each. It has no default, as `--scopus-budget` has none (ADR 0041 Decision 7). At list price a 100-page budget costs at most $0.10.
- There is no `--opt-in`. OpenAlex is the only source in this mode, so there is no fallback to opt into and no refusal to record. This departs from the word the owner used, and it is OQ-13.
- `--dry-run` prints the query key, the `select`, the page budget and the cost bound. It makes no request and writes nothing.

**Request.** One `GET` to `https://api.openalex.org/works` with `search=<the query>`, `per-page=100`, `cursor=*` for the first page, then the `meta.next_cursor` of each response. The `select` is the one the client already uses (`sources/openalex.py:56-59`), with `cited_by_count` added (Decision 5). The key goes in the `Authorization` header only (`sources/openalex.py:470`), never in `params`, the manifest or a body. A key is required for every search run (Q-D). If Q-D is not accepted, a keyless run is refused before the second page is requested, and the refusal names `OPENALEX_API_KEY` and the free account.

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
- `discarded` (bool), true when D-4 or a seal failure has ended the run (see below);
- `api_key_used` (bool), never the key;
- `requests`, one entry per HTTP request in order, each with `index`, `file`, `cursor_sent`, `sha256`, `encoding`, `raw_sha256` (when compact), `http_status`, `retrieved_at`, `result_count`, `meta_count` and `next_cursor`.

**Seal rules.** The run seals only when all four hold:

1. the last response has no `next_cursor` (`complete` is true);
2. `total_count` is not null, and `works_captured` equals `total_count`;
3. every page was a 200, and every `meta_count` equals `total_count`;
4. the run was never resumed with a changed `meta_count` (D-4).

**Discard and restart (D-4, F-minor 6).** A run that fails rule 2 or rule 3, or that is resumed with a changed count, is marked `discarded` in its `progress.json` and its manifest. It is never sealed. Its `progress.json` still holds the plan and the cursor, so a later call with the same plan would reopen it. It is therefore not resumed. The command reports "discard and restart" and starts a new run with a new `run_id` on the next call. The discarded run is kept on disk as an audit trail, and no loader reads it, because it is not sealed. Its pages are not counted and its works are not loaded. A discarded run still blocks a mode change (Decision 1). A cursor that the server rejects (an expired cursor, under the Unverified assumption) takes the same path. The command never patches a run in place. A missing count is never estimated, and this is what keeps `identified` from closing on an estimate (Decision 5).

**Resume, budget and reuse.**

- A budget stop writes `progress.json` with the next cursor and leaves the run unsealed and not discarded. The next call with the same plan (`query_key`, `select`, `per_page`) resumes from that cursor. It does not request a page already on disk (ADR 0031 Verification 7). If the first response of the resumed call carries a different `meta.count` from the stored `total_count`, the run is discarded and restarted (D-4). A discarded run is never resumed, so its cursor is never used again (Verification 26a).
- A sealed, complete run for the same `query_key` means the next call creates no run and makes no request. `--refresh` makes a new run. Its values update Layer 1 under ADR 0031 Decision 7, and it adds no membership (Decision 5).
- Unlike the cascade, a budget stop does not seal. A partial search is not a fact that identification can use.

**Sizes.** A keyed free account carries $1 a day, which is about 1,000 search pages, or 100,000 works at 100 per page. That covers a review-sized search in one day. Keyless access carries $0.10 a day, and the vendor statement limits it to one page per query, so it cannot capture a review. These figures come from the vendor pages and are unverified.

### 5. Layer 1: where OpenAlex records live, and how they are counted

**The discriminator is `origin`, and the physical table is new.** Each OpenAlex-primary work becomes a row of a new table, `openalex_records`. It carries the same 16 provenance columns as `records` (`schema.sql:31-38`), so the view can select them directly. `records_all` gains a third branch with `origin` `'openalex'`, and that branch selects the columns rather than deriving them by `CASE`, as the registered branch does (`schema.sql:245-264`). The branch's `run_id` column is `query_run_id`, the earliest sealed run that holds the work.

```sql
CREATE TABLE openalex_records (
  record_id TEXT PRIMARY KEY,         -- 'openalex:' || openalex_id
  openalex_id TEXT NOT NULL,
  doi TEXT,                           -- normalised; NULL when the work has none
  title TEXT NOT NULL,
  abstract TEXT,
  year INTEGER,                       -- nullable, as records.year is
  doc_type TEXT,                      -- raw OpenAlex type when unmapped (Decision 5)
  language TEXT,                      -- always NULL (ADR 0032 Decision 2)
  venue_type TEXT,                    -- closed set, or NULL when the source has no type
  venue_name TEXT,                    -- the source display name, stripped, or NULL
  payload_file TEXT NOT NULL,         -- openalex-search/<run>/response-NNNN.jsonl
  payload_line INTEGER NOT NULL,      -- the work's index within results[] of its response (F7)
  query_run_id TEXT NOT NULL,         -- the earliest sealed run that holds the work
  abstract_source TEXT NOT NULL DEFAULT 'unavailable',
  abstract_level INTEGER NOT NULL DEFAULT 0,
  abstract_outcome TEXT NOT NULL DEFAULT 'unavailable',
  abstract_payload_ref TEXT,
  -- the same four columns for authors, affiliations and keywords, with the
  -- closed sets of Decision 5 (manual is allowed for abstract, authors and
  -- affiliations; keywords take no manual value)
  retrieved_at TIMESTAMP NOT NULL
);
```

The four provenance columns per field are written as ADR 0041 Decision 3 defines them, and their CHECK constraints follow the same pattern with the openalex source sets: `openalex` at level 4 and `manual` at level 5 for abstract, authors and affiliations, and `openalex-topic` at level 4 for keywords. Manual values reach these columns through the fold of Decision 9.

**Payload references (F7).** A work inside a response is addressed by its file and its index within the response's `results` array, and not by the line, because the file holds one line (ADR 0031 Decision 2). So `payload_line` is the work's zero-based index in `results[]`. The payload reference of a work is `payload_file#<openalex_id>`, the same form that `registered_records` uses (`payload_file || '#' || openalex_id`, `schema.sql:245-264`). The index is kept in `payload_line`, and no new column is added, because `malformed_entries` already has a primary key of `(payload_file, payload_line)` (`schema.sql:97-99`) and the index keeps it unique per work. The record would otherwise collide when two works of one page are malformed, and the second loss would not be counted, which breaks equation 1. Decision 10 amendment l records that this departs from ADR 0031's "always 0" for search works. The enrichment bodies of ADR 0031 keep line 0.

Why a new table, and not one of the two that exist:

- **Not `records`.** `records` is written from Scopus payloads, its `run_id` names a Scopus search, and it is the table that Scopus-only checks read (`registrations.py:496`, and the build statistic at `store/load.py:3072-3078`). Mixing origins there would give every such reader an origin check. ADR 0032 rejected the same mixing for registrations.
- **Not `registered_records`.** Its rows are keyed `registered:<event_id>`, come from a reviewer's `register` event, and feed the `registered` count and the "other methods" box of the diagram (ADR 0029 §3). An OpenAlex search work has no reviewer event, and the search identifies it. Counting it as a registration would be false.

**Authors, affiliations and keywords reach their tables.** Several analyses read `record_authors`, `record_affiliations` and `record_keywords` directly (Decision 6, the readers table). So the loader writes rows for OpenAlex-origin records, as it does for Scopus records. The source of each row is the stored body:

- **Authors.** `record_authors` and `authors` rows. `authorships_to_fields` (`sources/openalex.py:171`) returns display names only, so PR4a adds a helper beside it that also returns each author's OpenAlex id. The author id is `openalex-author:<OpenAlex author id>` when the id is present, and `openalex-name:<normalised display name>` otherwise. The surname is the last whitespace-separated token, as the comparison rule of ADR 0041 Decision 2 reads a display name.
- **Affiliations.** `record_affiliations` and `affiliations` rows. The afid is `openalex-inst:<institution id>` when the id is present, and `openalex-name:<normalised name>` otherwise. The country is the alpha-3 code of the institution's `country_code` through `ALPHA2_TO_ALPHA3` (`countries.py:300`), the derivation that the loader already applies to OpenAlex institutions (`store/load.py:2114-2130`).
- **Keywords.** `keywords` and `record_keywords` rows of kind `openalex-topic` (ADR 0030), from `topics_to_keywords` (`sources/openalex.py:187`). No new kind is added. The readers that filter by kind are changed in PR4c (Decision 6, F1).
- **Citations.** `citation_snapshots` rows, one per OpenAlex-origin record, with `retrieved_at` the response's retrieval time and `cited_by_count` from the body. For that, the OpenAlex `select` gains `cited_by_count` (Decision 10, amendment d). Without a snapshot row, `bibliometrics/geography.py:245-249` fills the count with `0` (a left join with `fill_null(0)`), and that would be a silent zero.
- **Subject areas.** One `subject_areas` row for a mapped `primary_topic` subfield (ADR 0032's map). For a work with no mapped subfield, see the subject-area rule below.

**Identification.** A new table, `openalex_search_runs`, holds one row per sealed run: `run_id`, `query_key`, `query`, `total_count` and `works_captured`. It is a new table and not a column of `runs`, because the frozen `runs` table must not change, and ADR 0012 treats an added table as the smaller deviation (OQ-5). Duplicates are not stored here. They go into `run_duplicates` (below).

- `identified` in mode `openalex` is the sum of `total_count` over the distinct `query_key`s, each taken from its earliest sealed run. This is ADR 0013's rule, with OpenAlex's count in place of `total_results`. The flow reads it from `openalex_search_runs` in mode `openalex`, and from `runs` otherwise (`flow.py:297-340`).
- `duplicates_across_searches` is read from `run_duplicates` by `_cross_run_duplicate_count` (`flow.py:412-438`, called at `:557`), unchanged in form. The loader adds one `run_duplicates` row per sealed OpenAlex search run that has duplicates, keyed by `run_id`, with no source column (M4).
- **Who contributes (F7, rewritten from line 283).** Only the earliest sealed run per `query_key` contributes members and duplicates (line 278). A refresh of the same query adds no member and is not a duplicate. Within that earliest run, a second work with the same DOI is a duplicate. A work already loaded by a run of a different `query_key` is a duplicate. Runs are taken in `run_id` order.
- `removed_other_reasons` counts OpenAlex search works that cannot be loaded, such as a work with no title or no id. They are recorded in `malformed_entries`, with `payload_line` the work index (above) and reason `malformed_openalex_search_work`.

**Why the equation is a check.** `identified` is taken from the sealed manifests' `total_count`. `duplicates_across_searches` is measured by the loader and stored in `run_duplicates`. `removed_other_reasons` is measured from `malformed_entries`. `after_automated` comes from the loaded rows. Nothing defines one of these as the remainder of the others, so ADR 0033's equation 1 can fail when the manifests and the loader disagree. That is the defect the equation exists to catch, and `flow.py:412-428` warns against deriving a duplicate count as a remainder. Verification 50 includes a negative case that makes the loader disagree and expects the failure.

**Deduplication key (Q-C).** A work's key is its normalised DOI when it has one, and its OpenAlex id otherwise. Within the earliest run of a query, a second work with the same DOI is a duplicate, and it is counted in that run's `run_duplicates` row. This counts a DOI collision as a duplicate and not as an unreported pair, which is the PRISMA definition. The recommendation is that Q-C is accepted. If the owner rejects it, DOI collisions are reported in the build report only, and the methods must say that two records may share a DOI. That alternative understates the duplicates before screening, which is why it is not recommended.

**The unloadable count (M4, F-minor 5).** `_unloadable_count` (`flow.py:378-409`) has two clauses. The first counts distinct `malformed_entries` record ids that are absent from `records` (the membership test at `:400`). The second counts NULL-id rows whose reason is not `malformed_openalex_body` (`:405`). That exemption is a blacklist, so a NULL-id `malformed_openalex_search_work` is already counted today. The failing part is the membership test at `:400`. It uses `records`, so a malformed search work whose OpenAlex id was loaded by another run would be counted as lost, which overcounts. PR4b changes the test to `records_all`. A malformed search work whose id is loaded elsewhere is then not lost, which is the rule the function's own comment states for Scopus entries. Verification 51 pins that.

**The view.** `records_all` gains the third branch, which selects the 32 columns from `openalex_records`, with `origin` `'openalex'` and `run_id` set to `query_run_id`. The `CASE` form is used only for `registered_records`.

**Classification, reused.** The map of ADR 0032 (`openalex_map.py`, `MAP_VERSION` `"1"`) is used unchanged for three things: the subfield of `primary_topic` gives the one `subject_areas` row, `type` gives `doc_type`, and the source `type` gives `venue_type`. Three rules differ from ADR 0032 for these records, and each is an amendment in Decision 10:

- **An unmapped `type` is stored raw, not refused.** ADR 0032 Decision 4 refuses a registration whose type has no target, because a refused registration is a candidate that stays out of the corpus. An OpenAlex search work is identified by the search, and refusing it would drop it from the count and break the equation. Its raw type is stored in `doc_type`. The engine's `doc_types.include` rule then excludes it as it excludes any other type (OQ-6).
- **A missing source, or an unmapped source type, is stored as NULL and not refused (F9).** ADR 0032 §2 refuses a registration whose work has no `primary_location` source, or a source type outside its table. A blank `venue_name` is also refused there (ADR 0032 Decision 5). For an OpenAlex search work these inputs stay NULL in `venue_type` and `venue_name`, because the work is already identified. A NULL `venue_type` is not a conference, so the whitelist does not apply to it.
- **A work without a mapped subfield keeps no subject row, and is recorded as such.** Its `record_subject_area_coverage` row (`schema.sql:140-143`) has status `none_assigned`, the Layer 1 value that `load.py:429` names (the others are `assigned`, `not_found` and `not_entitled`). A mapped work has status `assigned`. The coverage row's primary key is `(record_id, run_id)`, so its `run_id` is `query_run_id` (F8). The no-data rule then lets the work pass the subject filter, as Scopus records do (`prisma/engine.py:336-364`). The methods count these works (OQ-7).

**The subject guard (mi8).** `_require_subject_data` raises only when `enrichment_attempted` is true (`prisma/engine.py:578`, `:676`), and it counts records without `subject_area_observed` (`:661`). In mode `openalex` no abstract run can exist, because `enrich` is refused in that mode (Decision 3), so `enrichment_attempted` is false and the guard is inert there. The coverage rows of the previous paragraph make every OpenAlex work observed, with `assigned` or `none_assigned`, so the guard would also not fire if it were reached. A test pins both facts (Verification 45).

Language is NULL, for the reason ADR 0032 Decision 2 gives. A NULL language passes the language filter, as Scopus records already do.

**The readers that are touched.** Decision 6 lists each reader with its file and line, what it does today, and the edit it needs.

**The subject-area asymmetry becomes total.** ADR 0032 Consequence 4 says that most Scopus records pass the subject filter, while a registration is filtered by its subject. In mode `openalex` almost every work carries a mapped subfield, so the subject filter becomes the main automated rule. The methods must say so, and `docs/methodology/limitations.md` gets a paragraph in PR7.

### 6. Readers, numbers, the flow diagram and the methods, by mode

**Readers.** The table lists every reader that the two origins touch. Its "Today" column is what the code does on main, and "Edit" is what this record requires. A reader with no edit is listed because it was checked.

| Reader (file:line) | Today | Edit required |
|---|---|---|
| `prisma/engine.py:210-214` and `:226-231` (`_fetch_record_attributes`) | `COALESCE(r.venue_name, '')` turns a NULL venue into `''` (`:212`). The registered-only check at `:227` does not fire for `openalex` | none. A missing source fails a venue whitelist token exactly as a Scopus record with no source does, and the methods count these works (F6) |
| `store/load.py:3782-3796` (`Corpus.venues`): keep clause at `:3789` | `r.origin = 'registered' OR v.venue_id IS NOT NULL` drops every OpenAlex row, because it has no `venues` row | `r.origin IN ('registered', 'openalex') OR v.venue_id IS NOT NULL`; venues grouped by `venue_name` |
| `store/load.py:3828` (`Corpus.affiliations`) | reads `record_affiliations` | none, once PR4a writes the rows |
| `store/load.py:3867` (`Corpus.authors`) | reads `record_authors` | none, once PR4a writes the rows |
| `store/load.py:3698-3705` (`Corpus.keywords`) | reads `record_keywords` by the `kind` the caller passes | none in the accessor. Its callers are in the rows below |
| `bibliometrics/keywords.py:82` and `:131` | defaults `kind="author"` | in a project whose mode is `openalex`, the kind is `openalex-topic`. PR4c changes the default by mode (F1) |
| `bibliometrics/network.py:419` and `:462` (`keyword_cooccurrence_network`) | defaults `kind="author"` and forwards it to `corpus.keywords` at `:462` | the same mode rule as `keywords.py`. Verification 41 builds the network from `openalex-topic` rows (F1) |
| `taxonomy/coder.py:377-378` | reads `corpus.keywords("author", stage)` and `corpus.keywords("index", stage)` literally | in mode `openalex` it reads `openalex-topic` in place of `author` and `index`, so the taxonomy coder sees the topics (F1) |
| `screening/ui.py:226-235` (`load_records`, keyword query at `:232`) | hard-codes `WHERE rk.kind = 'author'` | the kind is chosen by mode, as for `keywords.py` (F1) |
| `store/load.py:3735` (`Corpus.citations`) | reads `citation_snapshots` | none, once PR4a writes the snapshot rows |
| `bibliometrics/geography.py:245-249` | left join on citations, then `fill_null(0)` | none in code; the snapshot rows make the value real. This is the silent zero that the rows prevent |
| `bibliometrics/citations.py:178`, `:259` | inner join on snapshots | none, once snapshot rows exist |
| `bibliometrics/network.py` (co-authorship), `bibliometrics/trends.py` | read through `Corpus` accessors | none, once rows exist; checked by Verification 41 |
| `screening/ui.py:236-246` (authors and citations) | reads `record_authors` and `citation_snapshots` | none, once rows exist |
| `report/numbers.py:124-150` (`_cascade_numbers`) | reads `records` (`:143`) | PR4c: count `records` and `openalex_records`, never registrations (ADR 0041 Consequence 8). PR6a does not touch it (F-minor 4) |
| `capture/cascade_commands.py:79-112` (`_targets`) | opens DuckDB with `duckdb.connect` at `:84` and reads `records` at `:88` | route through `connect` (F5b). In mode `openalex` there are no record targets, which is correct and is stated in Decision 3 |
| `capture/cascade_commands.py:254-292` (`_field_report`) | opens DuckDB with `duckdb.connect` at `:262`. Builds `counts` with the keys `scopus` and `registered` (`:267`), and indexes `counts[origin]` (`:271`) over `records_all`, so an `openalex` row raises `KeyError` | route through `connect` (F5b). Add an `openalex` bucket and a third printed group, `per field, OpenAlex-primary records`. PR4c (F2) |
| `store/load.py:3072-3078` (duplicate-DOI statistic) | reads `records` | count DOI collisions across `records` and `openalex_records` (Q-C) |
| `prisma/flow.py:378-409` (`_unloadable_count`) | membership in `records` at `:400` | membership in `records_all` (Decision 5, F-minor 5) |
| `prisma/flow.py:412-438`, `:557` (`_cross_run_duplicate_count`) | sums `run_duplicates` | none; OpenAlex search runs add rows to `run_duplicates` |
| `capture/registrations.py:488-496` (`_scopus_duplicate`) | reads `records` (`:496`) | also check `openalex_records` by DOI and by normalised title and year (Decision 9) |
| `capture/manual.py:83-104` (`manual_target`) | accepts only `scopus:` ids (`:89`), reads `records` (`:94`) | accept `openalex:` ids and read `openalex_records` (Decision 9) |
| `report/flow_diagram.py:225` and `cli.py:93`, `:1056`, `:1118-1121` | Scopus wording | mode-aware wording (mi3, PR6a) |
| `store/db.py:19` (`connect`) and `store/db.py:65-103` (`_refuse_stale_schema`) | no stamp check; the stale-schema guard refuses a store missing some `TABLE_NAMES` | the stamp check (Decision 1); the new tables enter `TABLE_NAMES` in PR4a |
| `store/load.py:3152-3172` (`_install_store`) and `:3273-3286` (`build_store` without `--rebuild`) | the swap and the reuse path | write the stamp inside the swap (`_install_store`); refuse a stamp mismatch on reuse (Decision 1) |

**Numbers.** `numbers.json` keeps the same keys in every mode, and the values change. A key whose presence depends on the mode would read "absent" where it means "zero", which is the reason ADR 0033 gives for `flow.registered`.

- `flow.identified` is the mode's identification total. ADR 0033 §5 (line 198, "`identified` is still read only from `runs`") and ADR 0033 §1 (line 26, "read from `runs`") are amended for mode `openalex` (Decision 10, amendments a and g). A project has one identification source, because the transition table refuses to mix them.
- The other `flow.*` keys follow the same mode rules as `FlowCounts` (Decision 5).
- `corpus.*` and `venues.*` count `records_all`, so they include `openalex` rows. Venues are grouped by `venue_name`, as registrations are (ADR 0032 Decision 1).
- `methods.cascade.<field>.level1` to `level4` and `unavailable` count `records` and `openalex_records`, and never registrations. In mode `openalex` the level 1 to 3 counts are 0, the level 4 counts are the OpenAlex supplies, and `methods.cascade_conflicts_open` and `_resolved` are 0 because no Scopus side exists.
- `citations.*` count the snapshot rows, which now include OpenAlex-origin records. The existing key `citations.registered_unavailable` (`report/numbers.py:315`) still counts registrations only, and it is not reused for OpenAlex-origin records (OQ-8).

For a legacy project none of these values changes, because a legacy store has no `openalex` row, and the store is rebuilt by the new code before it is read (Decision 1).

**The flow diagram and CLI text (mi3).** The identification box is labelled "Records identified from OpenAlex (n = …)" in mode `openalex`, and it keeps today's label in both Scopus modes (`flow_diagram.py:225`). The renderer receives the mode from its caller. The CLI text at `cli.py:93` ("capture Scopus"), `cli.py:1056` ("records identified (Scopus total_results)") and `cli.py:1118-1121` (the equation-1 guidance) becomes mode-aware in the same way. The tests that pin the Scopus label (`tests/integration/report/test_flow_diagram.py:92` and `:204`, `tests/integration/prisma/test_registered_flow.py:325`) change in PR6a with their goldens. Each change is named there.

**The methods text.** Explicit modes carry one source sentence. Legacy projects do not, because adding a sentence would change their bytes (OQ-14). The sentence for mode `openalex` is produced by a pure function in `report/methods.py`, beside `cascade_sentence` (`methods.py:112`), and it reads the queries and run dates from the store. Its shape is:

> Records were identified by searching OpenAlex; no Scopus search was run. The search was `{query}`, run on {run dates}. OpenAlex reported {identified} works across {queries} distinct queries, of which {duplicates} were found by more than one query and counted once. {registered} records were registered by a reviewer from manual Google Scholar searches (ADR 0029). Metadata, including abstracts rebuilt from OpenAlex's inverted index and subject areas taken from OpenAlex's primary topic, is OpenAlex's own (see Limitations).

For mode `scopus` the sentence names the Scopus search and nothing else. It does not claim that no OpenAlex enrichment was run. Under Q-B a `scopus+openalex` project cannot move to `scopus` while OpenAlex enrichment or an active registration exists, so a store in mode `scopus` has no such rows. The sentence is therefore true of every store that can exist in that mode, and it is tested from a hand-built store (Verification 68, mi7). The existing cascade sentence (`cascade_sentence`, `methods.py:112`) is unchanged in both Scopus modes.

**Limitations text, in PR7.** `docs/methodology/limitations.md` line 14 ("The only search source is the Scopus Search API") and `docs/methodology/prisma-mapping.md` lines 74-85, 180-188, 266 and 312-333 are wrong for mode `openalex`, and they are rewritten in PR7. The lines in `prisma-mapping.md` that say "Scopus side of S_raw" or "Scopus record superseding it" are among them. `docs/methodology/metric-definitions.md` lines 148 and 272 also name Scopus values and must change, because node ids are now `openalex-author:` values (F-minor 9). New text uses "Scopus-only" with a hyphen, because the phrase test `test_public_text__denying_the_field_level_cascade__is_absent` (`tests/unit/test_docs_governance.py:151-160`) bans the unhyphenated phrase in `docs/` and `src/prismabib/`.

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

**Current state, and the blast radius.** `Settings` (`config.py:95-115`) reads `.env` from the working directory (`config.py:105`). `scopus_api_key` is required, so `Settings()` cannot be built without it. Nine call sites build `Settings()` by default: `capture/writer.py:434` (search), `capture/enrich.py:803` (abstracts), `capture/cascade.py:298` (Scopus levels), `capture/openalex.py:406` (OpenAlex capture), `sources/scopus.py:522` (the Scopus client), `sources/openalex.py:304` (the OpenAlex client), `sources/crossref.py:195`, `sources/unpaywall.py:112` and `sources/sciencedirect.py:106`. The Scopus client also sends the key in a header (`scopus.py:1011`). No production code constructs `CrossrefClient`, so its default at `crossref.py:195` is unreached in production. Tests reference `SCOPUS_API_KEY`, `scopus_api_key` or `Settings(` in 27 files, 91 occurrences, and `tests/unit/test_config.py` alone has five of them.

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

- The Scopus paths call it: the Scopus client constructor (`scopus.py:522`, with the registration at `:531`) and its header (`:1011`), `capture_search` (`writer.py:434`), `capture_abstracts` (`enrich.py:803`) and `capture_scopus_cascade` (`cascade.py:298-299`, where the token list must drop a `None`). The OpenAlex, Unpaywall and ScienceDirect clients do not require it. That removes the OpenAlex-only defect in Context.
- The `.env` path does not change. It is relative to the working directory, as it is for `FullTextSettings` (`config.py:86`) and `ProjectsRootSettings` (`config.py:59`), so one file serves all of them.

**Commands, and what PR2 covers.** PR2 adds `credentials set scopus`, `credentials set openalex` and `credentials set unpaywall`, as well as `status`, `test scopus` and `clear`. The owner's second-round decision is that the OpenAlex and Unpaywall setters are in scope, because a user who answers "no" must be able to add an OpenAlex key without a Scopus command (M9). Each setter writes only its own keys. `credentials set openalex` writes `OPENALEX_API_KEY`, and `credentials set unpaywall` writes `UNPAYWALL_EMAIL`. PR1 and PR2 both add to `cli.py`. PR2 depends on PR1, so the registration lines are added in order (F-minor 4).

**Writing `.env`.** The writer is a new module, `src/prismabib/credentials.py`. The owner's rule is that it refuses to write unless git confirms the file is ignored. This record states the checks in full.

1. The file the writer will write is named by its absolute path, and the check runs against that path, not against the working directory, so that a `--root` elsewhere is covered. Git is run in that file's directory with `git check-ignore -v -- <file>`. The `-v` output names the rule and its source file. The check passes only when the source is the repository's tracked `.gitignore` and the matching rule is one of the patterns `.env` or `*.env` (F-minor 8: the rule is matched by pattern, not by line number). A rule from `core.excludesFile`, `.git/info/exclude` or any other source refuses. Those sources are absent from a fresh clone, so a file that is ignored only through them would be committed by the next peer. The tracked-source requirement is the portable form of the check.
2. `git ls-files -- <file>` lists nothing. A tracked file is not reported as ignored by `check-ignore`, so the first check alone would not catch a tracked file. This is the documented git behaviour and is an implementation check to confirm (Unverified).
3. git is installed and the directory is a repository. Otherwise the command refuses and says why. This is the owner's rule for OQ-9, and it closes that question.

When all three hold, the writer reads the existing file, replaces only the lines of the keys it manages (`SCOPUS_API_KEY`, `SCOPUS_INSTTOKEN`, `OPENALEX_API_KEY`, `UNPAYWALL_EMAIL`), appends the missing ones, and keeps every other line and comment. The temporary file is named `tmp-<random>.env` in the same directory. The name ends in `.env`, so the existing `*.env` pattern covers it, and an interrupted write cannot leave an untracked secret under a name that no rule covers. The writer deletes the temporary file on any failure, flushes and `fsync`s it, and replaces the original with `os.replace`. On POSIX it sets mode `0600` before the replace. On NTFS that step is a no-op, and the repository's test environment is NTFS, so the mode is not a control on this machine. The writer rejects any value that contains a line break, and it quotes a value the way `python-dotenv` reads it (to confirm in PR2). A failed write leaves the old file in place.

**Source of each value (mi5).** Environment variables override `.env`, because pydantic-settings reads the process environment first. `credentials status` therefore reports, for each key, whether it is set in the process environment or in the file, and not only that it is set. A key set in the environment is unaffected by `credentials clear`, and `status` says so.

**The commands.**

```bash
prismabib credentials set scopus          # hidden prompts; optional values may be left blank
prismabib credentials set scopus --from-env
prismabib credentials set openalex
prismabib credentials set unpaywall
type <a file outside the repository> | prismabib credentials set scopus --stdin   # KEY=VALUE lines, never argv
prismabib credentials status               # set or not set, source (environment or file), file path, ignored yes or no
prismabib credentials test scopus          # one Scopus request; reports ok, 401, 403 or network
prismabib credentials clear scopus         # removes the Scopus keys from .env
```

No option accepts a key value. The `--stdin` example reads from a file that the user keeps outside the repository. A file inside the repository would be a second secret file, which the ignore rules cover only if its name ends in `.env`, so the example says where to keep it. `status` prints "set" or "not set" for each key and never a value. `test scopus` reports the HTTP status or the network error class, and nothing from the response that could contain the key. It is refused in mode `openalex` (Decision 3), because that mode does not use Scopus.

**Output and logs.** The key is registered for redaction when a client is built (`sources/scopus.py:114`, called at `:531-533`). The structlog processor that applies it is installed at `:143-144`. OpenAlex does the same (`sources/openalex.py:315`), and its httpx filter (`:64`, `:90`) removes the key from the stdlib logger. The credential commands never log a value, and `SecretStr` renders as asterisks in `repr`.

**The GUI panel (mi6).** "Connect Scopus" is a password-type panel in a new module. It shows "set" or "not set", writes through the same `credentials` writer, and never echoes the input back. After a successful write the panel clears the input widget's value. The panel does not persist its widget state into the notebook: the value must not reach the notebook's saved state, because a notebook is tracked in git. Verification 84 scans `notebooks/*.ipynb` for the sentinel as well as the CLI streams.

**Mode and credentials.** Credentials belong to the machine, and the mode belongs to the project. A key in `.env` is unused by a project in mode `openalex`. `credentials status` says so when it runs inside a project, and `credentials clear scopus` is offered on a mode-change refusal (Decision 1).

**Considered and deferred: the keyring.** Windows Credential Manager, through the `keyring` package, keeps the key out of a plain file. It is deferred for four reasons. It adds a dependency. It is not visible to the notebook path and to headless runs in the same way. Its entries belong to the user account, not to a working copy. And it cannot be checked for being git-ignored, which is the property the owner asked the `.env` writer to enforce.

### 9. Registrations, manual entry, conflicts and supersession in mode `openalex`

**Registration (ADR 0029).** A reviewer may register a candidate in mode `openalex`. Registration is refused when the paper is already a record, and the duplicate check tests two tables. `_scopus_duplicate` (`registrations.py:488-496`) reads `records` only today. It is extended to `openalex_records`, by DOI and by normalised title and year, so that the rule "no registration of a paper the corpus already holds" (ADR 0029 §5) holds for both origins.

**Supersession is required (M5, OQ-12 closed).** A later `openalex-search` run that finds the DOI, or the normalised title and year, of an active registration writes a `supersede` event, as ADR 0029 §6 does for a Scopus match. The build report lists the event. Without supersession, the work is held in `openalex_records` and in `registered_records` at once: it is screened twice, counted in `identified` and in `registered`, and equation 1 still closes arithmetically, so the defect is silent. The loader also reports any DOI or normalised title and year held in both tables, whether or not the event exists, so no pair is silent. This extends ADR 0029 §6, which names only a Scopus match (Decision 10, amendment e).

**Manual entry for `openalex:` ids (M7, D-3).** Manual entry accepts `openalex:` ids in mode `openalex`, for any field that is `unavailable` in `openalex_records`.

- The writer `manual_target` (`capture/manual.py:83-104`) accepts `openalex:` ids as well as `scopus:` ids (`:89` and the prefix constant at `:31`) and reads `openalex_records` for them (`:94` reads `records` today).
- The provenance columns that receive a value are the four columns per field that `openalex_records` already has (Decision 5). A manual value sets `<field>_source` `manual`, `<field>_level` 5, `<field>_outcome` `value` and `<field>_payload_ref` to the event id, exactly as for Scopus records (ADR 0041 Decision 4).
- The loader fold applies the single active `manual_entry` per `(record_id, field)` to `openalex_records`, under the same rules as ADR 0041 Decision 4 (`manual_superseded` when a field was supplied, `manual_orphaned` when no record exists). The authors and affiliations of a manual value become `record_authors` and `record_affiliations` rows, with `manual:<event_id>:<n>` ids, so the analyses of Decision 6 see them.
- The refusal text changes from "no scopus record" to "no scopus or openalex record". ADR 0041's event model and refusal list are amended in Decision 10 (amendment f).

**Conflicts (ADR 0041 Decision 2).** A conflict needs two values, one from Scopus and one from OpenAlex. In mode `openalex` there is no Scopus side, so there are no conflicts. `conflict-resolve` is refused, and the GUI panel is disabled with its reason shown. `conflict-reverse` stays allowed, because a reversal is never blocked.

**Keywords.** Keywords never conflict and take no manual value (ADR 0041 Decision 1b), in every mode.

### 10. What stays frozen, and what this record amends

**Stays as written.**

- ADRs 0001 to 0041, which are not edited, and BUILD_PLAN.md, which is not edited and is not in the repository.
- The v1.0.0 claim (ADR 0027). It is a claim about the Scopus search, and mode `scopus` is that search.
- ADR 0028's rule that Layer 1 is written only by the loader (Decision 3), and that every attempt is a Layer 0 payload.
- ADR 0031's layout rules, one response per file, the key rules (Decision 6), the reuse rule (Decision 7) and the outcome rules (Decision 4). The new run kind follows them, with the payload-line change in amendment l.
- ADR 0013's distinct-query rule, which `openalex_search_runs` reuses.
- ADR 0030's `openalex-topic` kind, ADR 0032's classification map (version 1, unchanged), ADR 0033's equations 1 to 5, and ADR 0041's provenance rules and merge rule.

**Amended by this record.** Each entry names the frozen text it changes. The frozen file gets no note, so each amendment binds only through this record's statement. The owner's convention for this record is that the record states its amendments. ADR 0041 appended notes to frozen files, and this record does not. That is a difference the owner should look at (OQ-18).

a. **ADR 0033, §1 (line 26, "read from `runs`"), and §5 (line 198, "`identified` is still read only from `runs`").** `identified` reads the project's one identification source: `runs` in the Scopus modes (unchanged), and `openalex_search_runs` in mode `openalex`. Equation 1 keeps its form.

b. **ADR 0028, Resolved item 3 (lines 219-220, "`identified` stays the Scopus `total_results`").** That sentence is true for the Scopus modes. In mode `openalex` the identified count is the OpenAlex total of Decision 5. ADR 0028 does not otherwise change, and the mode is not a fallback (amendment k).

c. **ADR 0031, Decision 1 ("OpenAlex identifies no records").** True of enrichment runs. It does not hold for `openalex-search` runs in mode `openalex`. The enrichment runs are unchanged.

d. **ADR 0031, the `select` (Context, and the `_SELECT` of `sources/openalex.py:56-59`).** The select gains `cited_by_count`, so that citation snapshots can be written (Decision 5). This follows the precedent of ADR 0032 deviation (vi).

e. **ADR 0029, §5 and §6.** The duplicate check and the supersession cover `openalex_records` (Decision 9). ADR 0029 §6 names only a Scopus match.

f. **ADR 0041, Decision 4: the event model's `record_id` row ("An existing `scopus:` record", line 332), its writer refusal 1, and Decision 5 with Consequence 8.** The event model and the writer accept `openalex:` ids in mode `openalex` (Decision 9). The cascade counts cover `records` and `openalex_records`, and never registrations.

g. **ADR 0033, §2 (line 112, "Every function named `identified` returns the Scopus value alone"), and ADR 0029, §3 (the same sentence).** Both are amended to say that the function returns the total of the project's single identification source (Decision 6).

h. **ADR 0032, Decision 1 (the view), Decision 4 (refusal of an unmapped input), §2 (the refusal of a work with no source, or an unmapped source type), and Decision 5 (the refusal of a blank `venue_name`).** `records_all` gains a third branch with origin `'openalex'`. For `openalex_records` only, an unmapped `type` is stored raw, and a missing source, an unmapped source type or a blank `venue_name` is stored as NULL, and the work is not refused (Decision 5, F9). Registrations keep every refusal.

i. **ADR 0018, the coverage table (`record_subject_area_coverage`).** The rows for OpenAlex-origin works are an addition that ADR 0018 does not describe. Their status values are `assigned` and `none_assigned`, the Layer 1 vocabulary of `store/load.py:429`, and their `run_id` is `query_run_id` (Decision 5, F8).

j. **BUILD_PLAN §1.2 and §2.6 (the metadata source, as quoted by ADR 0032).** A project in mode `openalex` takes its records from OpenAlex, which §1.2 does not name, so this is a deviation under §2.6 (line 301, as quoted in ADR 0031). Its basis is the same as ADR 0032's deviation (ii). S02-AC5 ("the only source of the identified number") is read as the same rule for the one identification source that a project has.

k. **ADR 0028, Resolved item 4 and the "never degrade" rule.** Mode `openalex` is not a fallback. It has no Scopus record to degrade, so the rule does not apply. The sentence in amendment b is the only ADR 0028 text that this record states as changed in meaning.

l. **ADR 0031, Decision 2 (the `PayloadRef` line is always 0).** A work inside a search response is addressed by its file and its index in `results[]`, and `payload_line` holds that index (Decision 5, F7). The enrichment bodies keep line 0.

m. **ADR 0029, §3 (S_raw), and ADR 0033, Context, equation 1.** ADR 0029 §3 defines S_raw as "the Scopus records loaded into Layer 1 `records`, together with the active registrations" (line 100). In this record S_raw is `records_all`, which holds Scopus rows, registrations and OpenAlex-origin rows. The equation is unchanged, and its operands read this set.

n. **BUILD_PLAN §1.2 and §2.6 (the metadata source), as in amendment j,** also cover the `cited_by_count` select of amendment d. No separate deviation is claimed.

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

**A store that is never stamped, and is read whatever mode it was built under.** Rejected. A store built under one mode, read under another, gives tables that are wrong without an error (Decision 1, store stamp).

**A new `payload_ref` column for the work index.** Considered for F7. Rejected: `payload_line` already sits in the `malformed_entries` primary key and in the record's payload reference, and a new column would change the schema of two tables for the same information.

## Consequences

1. A project has one identification source, and its mode says which. The methods of an OpenAlex review name OpenAlex and its queries, and say that no Scopus search was run.
2. Legacy projects are unchanged in bytes, once their store is rebuilt by the new code. A legacy store that is not rebuilt is refused with "needs rebuild", and is never read as zero. From PR4a this is the stale-schema guard's refusal (Decision 1).
3. A peer with no Scopus access can complete a review. The cost is an OpenAlex key for every search (Q-D, recommended), and the corpus is OpenAlex's, which differs from a Scopus corpus.
4. Scopus and OpenAlex searches are not comparable. A query written for one does not carry over, and the methods say so. The query recorded per run is the only record of what was searched.
5. A `ConfigError` names the blocked command, the mode and the fix, so no Scopus call is attempted and no fallback is silent.
6. The credential path is the only path that writes a secret, and it writes only to a file that a tracked `.gitignore` pattern covers. A peer outside a git repository cannot store a key with the writer, which is the owner's rule.
7. `Settings` no longer requires a Scopus key for the paths that do not use Scopus. That is a fix in its own right.
8. OpenAlex-origin records have citation snapshots, from the `cited_by_count` of their body (amendment d), so citation analyses are real and not zero.
9. The subject criterion filters most OpenAlex records, so it does more work than it does for Scopus (Decision 5).
10. The new tables, the view branch, the stamp and the new run kind each change a checksum or a golden. Each change is named in the pull request that makes it, as ADR 0041 requires.
11. A resumed run that changes its count is discarded and restarted, which costs quota. The owner accepted that cost (D-4).

## Verification the implementation must include

Each test is shown to fail on the defect it guards before the code that fixes it is merged, as ADR 0031 and ADR 0041 require, except where a test is marked as pinning behaviour. "Fails before" means the test is run against main, or against the stub that precedes the fix.

**Mode and legacy byte identity.**

1. `test_source_mode__absent_table__is_scopus_plus_openalex`. Fails before: no such property exists.
2. `test_numbers_json__legacy_project__bytes_unchanged`. A legacy fixture, rebuilt by the new code, has its `numbers.json` compared byte for byte with the file from before the change. It passes after the change, and it fails if any key is added. It is anchored by `tests/golden/report/__snapshots__/numbers_keys.json` and `reference_numbers.json`.
3. `test_methods__legacy_project__no_source_sentence`. Fails if the sentence is emitted for a legacy project.
4. `test_capability_table__every_cli_command__has_a_declared_capability`. Iterates the registered commands and fails on any command missing from the table in `source_mode.py`. A second test does the same for every capture function.
5. `test_source_mode__invalid_value__config_error` and `test_source_mode__hand_edit_without_event__config_error`.
6. `test_sources_set__openalex_to_scopus__refused_with_sealed_openalex_run`, and one test for each other cell of the transition table.
7. `test_sources_set__decisions_present__refused`.
8. `test_sources_set__appends_one_event_and_keeps_other_toml_bytes`.
9. `test_source_modes_log__checksum_tamper__log_error`, through `AppendOnlyLog`.
10. `test_sources_set__unsealed_run_blocks_transition` (mi2). A partial, unsealed run blocks the transition the same way a sealed run does.
10a. `test_sources_set__discarded_run_blocks_transition` (F-minor 6). A discarded run still blocks a mode change.
11. `test_sources_set__scopus_plus_openalex_to_scopus__refused_with_openalex_enrichment` (mi1, Q-B). Fails before the rule exists.

**Store stamp (M1, F5).**

27. `test_store__built_under_scopus__flow_refuses_after_sources_set_openalex`. The defect this guards is a flow that reads the wrong tables after a mode change.
28. `test_store__no_stamp__after_pr4a__refuses_needs_rebuild`. A store with no stamp, after the new tables enter `TABLE_NAMES`, is refused by the stale-schema guard, and is not read as zero (F5a).
29. `test_build__without_rebuild_refuses_stamp_mismatch`. `build_store` with no `--rebuild` refuses a store built under another mode (`load.py:3273-3286`).
29a. `test_store_stamp__written_inside_the_swap__never_without_its_store` (F5c). A build that fails after the swap, or before it, leaves no stamp that does not match the store beside it. Fails before the stamp is written inside `_install_store`.

**Enforcement, with no request before a refusal.**

12. `test_source_mode__openalex__search_refused_before_any_http`. Uses an `httpx.MockTransport` that fails the test on any call. Fails before: `search` has no mode check.
13. `test_source_mode__openalex__enrich_and_cascade_scopus_levels_refused`.
14. `test_library__capture_search__refused_in_openalex_mode`, which checks the same rule at the function boundary.
15. `test_capture_openalex__caller_none__refused` (defect: a caller that omits `caller` and so bypasses the capability).
16. `test_cascade__openalex_mode__candidates_only_and_no_record_targets`. Fails if the cascade's own call is refused, or if it reads records as targets (M6).
16a. `test_cascade__openalex_mode__field_report_does_not_raise` (F2). Fails before the `openalex` bucket exists: `counts[origin]` raises `KeyError` after every cascade in mode `openalex`.
17. `test_init__non_interactive_without_sources_flag__refuses_and_writes_nothing` (D-1).
17a. `test_init__without_actor_and_without_git_user_name__refuses_and_writes_nothing` (F4).
18. `test_init__sources_flag__writes_table_and_first_event`.
18a. `test_project_init__default_source_mode_none__writes_no_table_and_no_event` (F3). Pins that every existing caller keeps the legacy default.
19. `test_init__every_call_site__passes_sources` (M2). Runs the updated `tests/integration/test_cli.py` invocations, and the README and docs commands, through the same parser. Fails before the flag is added.
20. `test_doctor__default__makes_no_request`.

**No Scopus key in mode `openalex`.**

21. `test_openalex_search__without_scopus_key__succeeds`. Runs with `SCOPUS_API_KEY` unset. Fails before: `Settings()` requires the key.
22. `test_openalex_mode__scopus_client_never_constructed`. Monkeypatches the `ScopusClient` constructor to raise.

**OpenAlex search capture.**

23. `test_openalex_search__pages_by_cursor_until_next_cursor_is_null`. Uses a fake transport with three pages.
24. `test_openalex_search__budget_stop_leaves_run_unsealed_and_resumes_from_cursor`. Fails if a budget stop seals or marks the run discarded, or if a resume re-requests a page already on disk.
25. `test_openalex_search__seal_refused_when_works_captured_differs_from_total_count` (M8). The run is marked `discarded`, stays unsealed, and the command reports "discard and restart"; a new call starts a new run id, and the old run's files are unchanged.
26. `test_openalex_search__resume_with_changed_meta_count__discard_and_restart` (D-4, M8). Fails before the rule exists, because the resumed run would seal a corpus whose count moved.
26a. `test_openalex_search__discarded_run_is_never_resumed` (F-minor 6). A later call with the same plan starts a new run and never reuses the discarded cursor.
30. `test_openalex_search__expired_cursor__discard_and_restart` (M8, under the Unverified assumption).
31. `test_openalex_search__no_key__refused_before_first_request` (Q-D, recommended), and `test_openalex_search__keyless_second_page_refused_before_request` if Q-D is rejected.
32. `test_openalex_search__same_query_sealed__creates_no_run_and_no_request`.
33. `test_openalex_search__refresh_makes_new_run_and_adds_no_membership`.
34. `test_openalex_search__key_only_in_header_never_in_params_or_manifest`, in the style of ADR 0031 Verification 2.

**Query language.**

35. `test_openalex_query__scopus_field_code__refused`.
36. `test_openalex_query__leading_minus__refused_with_not_hint`.
37. `test_openalex_query__wildcard_fuzzy_parentheses__refused`.
38. `test_openalex_query__extra_key__refused`.
39. `test_openalex_query__over_2000_characters__refused`.
40. `test_openalex_query__renders_search_parameter_only`. It also checks that `build_query` output is unchanged for an existing `[query]` table.

**Analyses in mode `openalex` (D-2, M3, F1).**

41. `test_analyses__openalex_mode__each_populated_or_named_refusal`. On an OpenAlex-only fixture, each analysis (co-authorship network, affiliation and country counts, keyword network, citation statistics, venue statistics, trends and the taxonomy coder) either returns a non-empty result or raises a named refusal. None may return an empty result silently. The keyword network is built from `openalex-topic` rows and is non-empty (F1). Before PR4c, the keyword network and the coder return empty results for this fixture, so the test fails.
42. `test_citations__openalex_record__snapshot_row_written`. It also checks that the geography citation impact is not the silent zero of `geography.py:245-249`.
43. `test_engine__openalex_null_venue__is_not_raised_and_fails_whitelist_like_scopus` (F6). This test pins behaviour and is not a fail-first test. The registered-only check does not fire for `openalex`, and a NULL `venue_name` becomes `''` through the query at `engine.py:212`. The test asserts that the record fails a conference whitelist token exactly as a Scopus record with no source does, and does not raise.
44. `test_venues__openalex_rows_kept` (M3). Fails before the fix at `load.py:3789`.
45. `test_subject_coverage__openalex_no_primary_topic__status_none_assigned` (mi8, F8). It pins that the guard does not fire in mode `openalex`, and that the coverage status is the Layer 1 value `none_assigned`.

**Identity and Layer 1.**

46. `test_flow_counts__openalex_mode__identity_closes_with_two_overlapping_queries`. The fixture has two queries, A and B. Query A's sealed run holds works 1 to 3, and query B's holds works 2 to 4, so works 2 and 3 are shared. `identified` is the total of A plus the total of B, `duplicates_across_searches` is 2, and `after_automated` is 4 (F-minor 1).
47. `test_flow_counts__openalex_mode__duplicates_read_from_run_duplicates` (M4). Fails before the fix: the mode-openalex duplicates would read zero from `run_duplicates`, and `assert_consistent` would fail.
48. `test_flow_counts__same_query_refresh__not_a_duplicate`, which tests ADR 0013's condition.
49. `test_flow_counts__scopus_modes__values_unchanged`, against the reference fixture.
50. `test_flow_counts__equation_one__fails_when_loader_disagrees_with_manifest` (mi7). A negative test: the equation must be able to fail.
51. `test_unloadable__malformed_search_work_loaded_elsewhere__not_counted` (F-minor 5). A malformed search work whose OpenAlex id was loaded by another run is not counted as lost. Fails before the change to `records_all` at `flow.py:400`, which counts it. A NULL-id malformed search work is already counted today, and this test does not change that.
51a. `test_malformed__two_bad_works_in_one_page__both_counted` (F7). Two malformed works in one response both reach `malformed_entries` (with distinct work indexes) and both count in `removed_other_reasons`. Fails before the change, where the second row collides on `(payload_file, payload_line)`.
52. `test_unloadable__openalex_enrichment_body__not_counted`. The existing exemption still holds for enrichment bodies.
53. `test_dedup__doi_collision_within_earliest_run__counted_in_run_duplicates` (mi4, Q-C).
54. `test_records_all__openalex_branch__origin_openalex_and_32_columns`.
55. `test_openalex_records__unmapped_type__stored_raw_not_refused`.
56. `test_openalex_records__work_without_title__counted_as_removed_other_reasons`.
56a. `test_openalex_records__missing_source_or_unmapped_source_type__stored_null_not_refused` (F9).
57. `test_rebuild__determinism__openalex_records_checksums_identical`, in the style of ADR 0041's rebuild-determinism test.
58. `test_build__mode_openalex__scopus_runs_reported_unused_not_loaded`.

**Registrations, supersession, manual entry, conflicts.**

59. `test_registration__openalex_record_with_same_doi__refused` (M5). Fails before: the check reads `records` only (`registrations.py:496`).
60. `test_supersede__openalex_search_finds_active_registration_doi__writes_one_event` (M5). Fails before the rule exists.
61. `test_build__registration_and_openalex_record_same_doi__reported` (M5). The loader reports any pair held in both tables.
62. `test_manual_entry__openalex_id_unavailable_field__written_and_folded` (M7, D-3). Fails before: the writer refuses `openalex:` ids (`manual.py:89`).
63. `test_manual_entry__openalex_id_supplied_field__refused` (M7).
64. `test_conflict_resolve__openalex_mode__refused`.

**Mode labels, CLI text and the GUI.**

65. `test_flow_diagram__openalex_mode__label_names_openalex`, and `test_flow_diagram__scopus_modes__label_bytes_unchanged`.
66. `test_cli__flow_and_search_text__mode_aware` (mi3). Pins the CLI text at `cli.py:93`, `:1056` and `:1118-1121` for each mode.
67. `test_methods__openalex_mode__sentence_names_queries_and_run_dates`.
68. `test_methods__scopus_mode__sentence_from_hand_built_store` (mi7). The sentence names the Scopus search and nothing else, and is tested on a hand-built store in mode `scopus`. The state that Q-B makes unreachable is not tested.
69. `test_gui__scopus_actions__disabled_with_reason_in_openalex_mode`. Asserts the widget state, not the rendered text.
70. `test_gui_connect_scopus__value_cleared_after_write_and_not_persisted` (mi6).

**Credentials.**

71. `test_settings__scopus_key_optional__require_scopus_key_names_the_variable`. Fails before: the field is required.
72. `test_credentials_set__output_never_contains_the_key`. Captures stdout, stderr and the structlog output.
73. `test_credentials_set__no_option_accepts_a_value`.
74. `test_credentials_set_openalex__writes_only_its_key` and `test_credentials_set_unpaywall__writes_only_its_key` (M9).
75. `test_dotenv_writer__preserves_unrelated_lines_and_comments`.
76. `test_dotenv_writer__refuses_when_not_ignored`, `test_dotenv_writer__refuses_when_ignored_by_a_non_tracked_source` (mi5: `core.excludesFile` or `.git/info/exclude`), `test_dotenv_writer__refuses_when_tracked` and `test_dotenv_writer__refuses_outside_a_repository`.
77. `test_dotenv_writer__temp_file_ends_in_env_and_is_removed_on_failure` (mi5). Fails if an interrupted write leaves a name that no ignore pattern covers.
78. `test_dotenv_writer__failed_replace_leaves_old_file`. Fails if the write is not atomic.
79. `test_credentials_status__reports_source_environment_or_file` (mi5).
80. `test_credentials_test_scopus__one_request_and_no_key_in_output`. Covers a 200, a 401, a 403 and a network error.
81. `test_redaction__registered_scopus_key_never_in_structlog_output`.

**No secret in any output or artifact.** These tests run on a sentinel key placed in `.env` and in the environment, after a full run in each mode. They scan every produced file and every captured stream for the sentinel and for its percent-encoded form.

82. `test_no_secret__raw_decisions_exports_and_manifests_clean`. Covers `raw/`, `decisions/`, `exports/`, `project.toml`, `numbers.json`, the methods text, the `manifest.json` of both run kinds and the store stamp.
83. `test_no_secret__cli_stdout_and_stderr_clean`, for every credential and sources command.
84. `test_no_secret__notebooks_clean` (mi6). Scans `notebooks/*.ipynb` for the sentinel after the GUI panel has been used.

**Python in this record.** The one Python block in this record must pass `ruff format --check`. This is a manual check that the orchestrator runs before pushing. It is not a repository test.

**Numbering.** The numbers above are the ones this record uses. Tests with a letter suffix (for example 10a, 16a, 29a) were added in the second revision without renumbering the others.

## Implementation plan

Ten implementation pull requests follow this record (PR0). The count is above the 5 to 8 that the first draft aimed for, because the reviewer required the splits listed here. Each PR is stacked on the one it depends on, under ADR 0037.

| PR | Scope | Size | Depends on | Owns (hotspots) |
|---|---|---|---|---|
| PR0 | This record and its nav line | S | none | `docs/architecture/adr/0042-*.md`, `mkdocs.yml` |
| PR1 | Mode model: `[sources]`, the event log, the capability table and library checks (including `caller` on `capture_openalex`), `Project.init` with `source_mode` defaulting to `None` (F3), the `init` question, `--sources` and `--actor` (F4), every `init` call site listed in Decision 2, the `sources` command, `doctor` without a network call, the store stamp and the stamp checks, the legacy byte-identity tests | L | PR0 | `source_mode.py` (new), `project.py`, `cli.py`, `source_commands.py` (new), `store/db.py`, `store/load.py` (`build_store` path), `capture/openalex.py` (`caller`), `tests/integration/test_cli.py`, the docs and README call sites |
| PR2 | Credentials: optional `scopus_api_key`, `require_scopus_key`, the `.env` writer with the checks of Decision 8, `credentials set scopus`, `set openalex`, `set unpaywall`, `status`, `test scopus`, `clear`, redaction and output tests, the OpenAlex-only fix. Depends on PR1 for `cli.py` ordering (F-minor 4) | M | PR0, PR1 | `config.py`, `credentials.py` (new), `sources/scopus.py`, `capture/writer.py`, `capture/enrich.py`, `capture/cascade.py`, the client defaults in `sources/`, `tests/unit/test_config.py`, `cli.py` (one registration) |
| PR3 | OpenAlex search capture, Layer 0 only: the cursor client method, the manifest model, the budget, resume, the seal rules, discard and restart with the `discarded` marker, the key guard, `NON_RUN_DIRNAMES` | M | PR1, PR2 | `capture/openalex_search.py` (new), `capture/manifest.py`, `capture/layout.py`, `sources/openalex.py` (cursor method) |
| PR4a | Tables and loader: `openalex_records` with the 16 provenance columns and the payload-index rule (F7), `openalex_search_runs`, `STORE_SCHEMA` and the stamp written in `_install_store`, the new names in `TABLE_NAMES`, the third `records_all` branch, the authors, affiliations, keywords, citation and coverage rows (`none_assigned`, F8), the `select` change, the checksum golden for the new tables | L | PR3 | `store/schema.sql`, `store/load.py`, `store/checksums.py`, `store/db.py`, `sources/openalex.py` (helpers) |
| PR4b | Identification and flow: `identified` by mode, `run_duplicates` rows, the unloadable rule on `records_all` (`flow.py:400`), the DOI-collision rule (Q-C), the equation tests | M | PR4a | `prisma/flow.py`, `store/load.py` (duplicate statistic) |
| PR4c | Reader edits and manual entry: the keyword kind by mode (`keywords.py:82,131`, `network.py:419,462`, `coder.py:377-378`, `screening/ui.py:232`, F1), `cascade_commands.py:84` and `:262` routed through `connect` (F5b), the `_field_report` openalex bucket and third group (F2), `numbers.py:143` (the cascade counts, F-minor 4), the venue keep clause at `load.py:3789`, `registrations.py:496`, `manual.py` (the `openalex:` ids and the loader fold). `engine.py` needs no edit (F6) | L | PR4a | `bibliometrics/keywords.py`, `bibliometrics/network.py`, `taxonomy/coder.py`, `screening/ui.py`, `capture/cascade_commands.py`, `report/numbers.py`, `prisma/engine.py` (none), `store/load.py`, `capture/registrations.py`, `capture/manual.py` |
| PR5 | Query language and command: `[openalex_query]`, `build_openalex_query_for_project`, the `openalex-search` command, the sibling how-to page and its nav line | S | PR1, PR3 | `query.py`, `capture/openalex_search_commands.py` (new), `docs/how-to/` |
| PR6a | Reports text: the methods sentence, the flow label and CLI text by mode (mi3), the goldens and tests they change. The cascade counts are PR4c's (F-minor 4) | M | PR4b, PR4c | `report/methods.py`, `report/flow_diagram.py`, `cli.py`, `tests/integration/report/test_flow_diagram.py`, `tests/integration/prisma/test_registered_flow.py` |
| PR6b | GUI and supersession: the registration, conflict and manual panels, the Connect Scopus panel with its widget clearing, the supersession writer (M5) | M | PR4c, PR2 | `capture/manual_ui.py`, `capture/registration_ui.py`, `credentials_ui.py` (new), `capture/registrations.py` (supersede) |
| PR7 | Public text and demo: `limitations.md`, the `prisma-mapping.md` lines of Decision 6, `metric-definitions.md` lines 148 and 272 (F-minor 9), the content of the new how-to page, `run-the-demo.md`, the CHANGELOG entry | S | PR6a, PR6b | `docs/methodology/*`, `docs/how-to/run-the-demo.md`, `CHANGELOG.md` |

Failing tests come first in each PR. Golden changes are named in the PR that makes them, as ADR 0041 requires, and no golden is regenerated to make a test pass. A live capture of a real review is not part of this plan. Any capture of Scopus data stays under the sharing gate of ADR 0041 (Risk 8).

## Risks

1. **A Scopus query does not carry over.** OpenAlex's `search` ranks by relevance over four fields, and it does not implement `TITLE-ABS-KEY`. A peer who reuses a Scopus query gets a different corpus. Mitigation: the query is recorded per run and in the methods, and the how-to says so.
2. **Keyless truncation.** Without a key, a query returns one page, so a keyless run cannot seal and cannot count. Mitigation: Q-D makes a key mandatory for every search, and the guard of Decision 4 is the fallback. Both depend on the vendor statement, which is re-checked live.
3. **The corpus changes with OpenAlex.** The index grows and is corrected. Mitigation: the retrieval date, the total and the payload bytes are stored per run, and a reproduction works from the bytes (ADR 0028 Resolved item 7).
4. **Coverage is not Scopus coverage.** ADR 0028 measured on 2026-10-21 that OpenAlex covers 1,084 of the 1,119 DOIs of the Scopus corpus. That measures OpenAlex against Scopus, not OpenAlex's own recall, and no recall figure exists for this design. The methods must not claim one.
5. **Classification is approximate.** The ADR 0032 map has 13 qualifier rows, and its venue-type rows are judgement calls. Mitigation: the map version is recorded, and qualifier rows are flagged as ADR 0032 requires.
6. **Raw OpenAlex types in `doc_type`.** An unmapped type reaches the engine as a raw string. Mitigation: the criteria rule excludes it, the build report counts the types, and the methods show the counts.
7. **Credentials in a working copy.** A `.env` in the wrong folder, or a `.env` tracked by mistake, could leak a key. Mitigation: the writer's checks of Decision 8, the `*.env` pattern, and the output tests (Verification 72 to 84).
8. **The blast radius of `Settings`.** Nine defaults and 91 test occurrences change. Mitigation: PR2 is its own pull request, and the tests that assert the required key are rewritten there.
9. **A hand-edited mode.** A file that says `openalex` while the log says `scopus` is refused at the first read (Decision 1). The check depends on the log, so a deleted log is refused too, and the fix is to record the mode again.
10. **Two identification sources in one folder.** A `raw/` folder can hold Scopus and OpenAlex search runs after a transition. The transition table refuses a move while any run of the blocked kind exists, sealed, unsealed or discarded (Decision 1). The loader reports, rather than loads, any search run of the other kind, and the build report says so (Verification 58).
11. **Legacy projects after the change.** A legacy project never sees the new question, and its methods carry no source sentence. A legacy store that is not rebuilt is refused. Mitigation: `doctor` and the GUI say "legacy default" and offer to record the choice, and the refusal says "run build --rebuild".
12. **Reviewer workload and quota.** An OpenAlex search of 100,000 works is 1,000 pages. The budget is explicit, and the dry run shows the cost bound. A discarded run costs its quota (D-4). The source does not change the screening load, but a larger corpus is more to screen.
13. **Silent empties in the analyses.** The analyses read several tables that only the view covers, and some filter keywords by kind (Decision 6). A reader that returns an empty table for an OpenAlex corpus would look like a result. Mitigation: the rows are written by the loader, the readers are listed, and Verification 41 requires each analysis to be populated or to refuse.

## Open questions for the owner

Each item below is an Owner decision needed. Where a recommendation is given, the Decision above writes it in, and that stands unless the owner overrules it before the merge.

- **OQ-1. Legacy default.** `scopus+openalex` (recommended, and written into Decision 1), or `scopus`. The owner's wording "`scopus` = v1.0.0 behaviour" conflicts with byte identity for legacy projects that run the cascade.
- **OQ-2. The second question for a "yes".** Ask "Also fill missing fields of Scopus records from OpenAlex?" (recommended, with a default of yes), or drop the question and default to `scopus+openalex`.
- **OQ-3. Can `scopus+openalex` also run `openalex-search`?** Recommended no. Adding OpenAlex-discovered works to a Scopus corpus creates two identification sources and a second duplicate rule.
- **OQ-4. A separate `openalex-search` command, or a mode-dispatched `search`.** Recommended: separate (Decisions 3 and 4).
- **OQ-5. Identification table.** A new table `openalex_search_runs` (recommended), or a `source` column on `runs`.
- **OQ-6. Unmapped `type`.** Stored raw and excluded by the criteria (recommended, Decision 5), or refused as ADR 0032 Decision 4 does. Refusal drops identified works from the count.
- **OQ-7. A work with no mapped subfield.** Passes the subject filter under the no-data rule, with its coverage status recorded as `none_assigned` (recommended), or is held for review.
- **OQ-8. Citations for OpenAlex-origin records.** Snapshot rows from the body's `cited_by_count` (recommended, and written into Decision 5), with the existing key `citations.registered_unavailable` left for registrations only.
- **OQ-11. Mode `scopus` and the Scopus levels 2 and 3.** Mode `scopus` refuses the cascade entirely (recommended, and Decision 3). The alternative allows the Scopus levels only, with `--opt-in`.
- **OQ-13. "Opt-in" for `openalex-search`.** The owner's word is "opt-in". The recommendation is a required `--budget` and no `--opt-in`, because this mode has no fallback.
- **OQ-14. The methods sentence for legacy projects.** None (recommended, for byte identity). A sentence for every mode would change the legacy bytes.
- **OQ-15. A mode change after screening decisions: refuse, or warn.** Refused, with "create a new project" (recommended, as the owner suggested). The open point is only whether a change after screening decisions is a refusal or a warning. Decision 1 writes refusal.
- **OQ-16. Deduplication key.** The normalised DOI, with the OpenAlex id for a work with no DOI (written into Decision 5), and DOI collisions counted under Q-C.
- **OQ-17. A query preview command.** `prismabib query <slug>` would print the rendered query without a request. It is optional and is not in the plan.
- **OQ-18. Frozen files and pointer notes.** This record states its amendments and edits no frozen file. ADR 0041 appended dated notes to frozen files. The owner should decide whether this record should also append notes to ADR 0028, ADR 0029, ADR 0031 and ADR 0033, as ADR 0041 did, or whether the statement in this record is enough.

**Decided and not open:** D-1 to D-4, OQ-9 (closed), OQ-10 second half (closed), OQ-12 (closed).

**New open questions, with recommendations:**

- **Q-A. Hard refusal on a store built under another mode or schema.** Recommended yes: a reader refuses, and the message says "run build --rebuild". Decision 1 is written that way. It is an owner-confirm item because it changes how existing stores are read.
- **Q-B. Refuse `scopus+openalex` to `scopus` once OpenAlex enrichment or an active registration exists.** Recommended yes. Decision 1 is written that way, and Verification 11 tests it. Verification 68 now tests the mode-`scopus` sentence on a hand-built store, because the state Q-B makes unreachable is not exercised.
- **Q-C. Count DOI collisions as duplicates.** Recommended yes, with the DOI as part of the deduplication key (Decision 5). The alternative is reporting only, which understates the duplicates before screening.
- **Q-D. An OpenAlex key is mandatory for every `openalex-search`.** Recommended yes. The alternative is a keyless first page, with a refusal before the second page (Decision 4). The keyless first page cannot complete a review, so the choice is between a clear requirement and a guard that fails late.

## Unverified in this draft

Each item is a claim this record relies on that was not confirmed in the repository, or was confirmed only through a summarising fetch. The pull request that relies on it must check it first.

- **OpenAlex keyless paging.** "Without a key, only the first page works" comes from a summarising fetch of the rate-limit page. Re-check live before PR3.
- **Cursor expiry.** The record assumes that a stored cursor can expire while a run waits for quota. Re-check live. Decision 4 treats an expired cursor as a discard-and-restart, so the design does not depend on the answer, but the frequency does.
- **`meta.count` on search responses, and on resumed responses.** The paging page's sample includes `meta.count`, but the page does not say that every response carries it. The seal and resume rules refuse a run without it, which is the safe behaviour, but the claim must be checked live.
- **The `cited_by_count` field and the `institutions` ids and country codes in an authorship.** These are assumed from the OpenAlex work object. The select already carries `authorships` (`sources/openalex.py:56-59`), but the presence of the ids and country codes in the stored bodies is to be checked against a recorded response in PR4a.
- **Prices.** The example-costs and pricing pages were read on 2026-11-03. Re-check before PR5. A third-party summary gave a different credit model, and this record does not use it.
- **OpenAlex parentheses and wildcard handling.** Parentheses are refused in v1 because they were not verified. Wildcard support needs `search.exact`, which v1 does not use.
- **The OpenAlex data licence.** Not verified. The licence terms of OpenAlex's data are needed before any shared sample, and ADR 0041's sharing gate (Risk 8) is the model.
- **`git check-ignore -v` output and `git ls-files` behaviour.** The tracked-source check of Decision 8 relies on the documented git output format and on the behaviour of tracked files, not on a run in this session. PR2 confirms both on a fresh clone.
- **`python-dotenv` quoting.** The writer's quoting rule is to be confirmed in PR2.
- **`Settings()` in the Unpaywall and ScienceDirect clients.** The defaults were read by line (`sources/unpaywall.py:112`, `sources/sciencedirect.py:106`). Whether each client needs any Scopus value was not traced, and PR2 traces it.
- **Panel widget state.** The claim that a Panel password input can be cleared and kept out of notebook saved state is to be checked against the installed Panel version in PR6b.
- **Whether every reader uses `connect`.** Decision 6 lists the readers that were checked. PR1 must confirm that the list is complete.
- **BUILD_PLAN.md.** Not in this repository. Its clauses are cited as the ADRs quote them.
- **Nothing was run.** No shell was available. Line numbers were read from the worktree, and the citations are as read.

## Records touched

- New: this file, `docs/architecture/adr/0042-source-modes-and-openalex-as-a-primary-source.md`.
- Edited: `mkdocs.yml`, one nav line after the ADR 0041 entry (`mkdocs.yml:128`). No other governance registration is required. `tests/unit/test_docs_governance.py` reads the status word and the changelog only, and no ADR index exists outside `mkdocs.yml`.
- Not touched by this record: code, tests, goldens, `CHANGELOG.md`, `.claude/PROGRESS.md`, BUILD_PLAN.md, and ADRs 0001 to 0041. The implementing pull requests own them, as the implementation plan assigns.

## Related decisions

- [ADR 0002](0002-append-only-decision-log.md) and [ADR 0024](0024-one-implementation-of-append-only-durability.md): the append-only log that records mode changes.
- [ADR 0003](0003-human-only-screening.md): mode changes and credentials are human acts, and nothing in this record screens.
- [ADR 0007](0007-flow-counts-unsure-fields.md), [ADR 0013](0013-identified-sums-across-searches.md) and [ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md): the identification and identity rules that Decision 5 reuses and amends for mode `openalex`.
- [ADR 0012](0012-persisting-skipped-layer0-entries.md): the rule for adding a Layer 1 table, which `openalex_records` and `openalex_search_runs` follow.
- [ADR 0018](0018-abstract-runs-in-layer-1.md): the coverage table, to which Decision 5 adds rows.
- [ADR 0027](0027-what-v1-0-0-claims.md): the v1.0.0 claim, which mode `scopus` preserves.
- [ADR 0028](0028-field-level-fallback-cascade.md): the cascade and its no-degradation rule, which mode `openalex` does not break.
- [ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md): registration, which Decision 9 extends to OpenAlex records and to supersession.
- [ADR 0030](0030-openalex-topics-as-a-keyword-kind.md): the `openalex-topic` kind, which is reused.
- [ADR 0031](0031-openalex-responses-are-layer0-runs.md): the OpenAlex run rules, which Decision 4 follows for a new run kind, and whose line-0 rule Decision 5 amends for search works.
- [ADR 0032](0032-registered-records-table-and-classification-map.md): the view, the map and the refusal rules. Decision 5 amends some of them, for its own origin.
- [ADR 0037](0037-merge-train-and-native-stacked-prs.md): the stacking rule for the implementation pull requests.
- [ADR 0040](0040-year-range-in-analysis-engines.md) and [ADR 0041](0041-field-level-provenance-and-cascade-completion.md): the optional `methods.*` rule, and the cascade and provenance that mode `openalex` reuses.
