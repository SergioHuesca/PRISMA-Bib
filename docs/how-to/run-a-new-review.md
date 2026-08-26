# Run a New Review

Executing a review end to end: create the project, capture Scopus into Layer 0, build
Layer 1, screen, and read the PRISMA counts.

This page covers **steps 1–5**, which is everything that exists. Full-text acquisition,
taxonomy coding, bibliometrics, the dashboard and export are named at the end as not built;
see [Limitations](../methodology/limitations.md) for what that costs you in practice.

The running example uses the slug `my-review`. Substitute your own; nothing below depends
on the topic.

If you have never run this before, read [Getting Started](../getting-started.md) first —
in particular its statement of the Scopus `COMPLETE` entitlement requirement, which
decides whether any of this can work for you at all.

## Step 1: create the project

### 1a. Clone and set up

```bash
git clone https://github.com/SergioHuesca/PRISMA-Bib.git
cd PRISMA-Bib
uv sync
```

### 1b. Configure environment variables

Create a `.env` file (never committed) in the repository root:

```bash
SCOPUS_API_KEY=<your_api_key>
SCOPUS_INSTTOKEN=                # Leave empty unless your institution issued a separate token
ELSEVIER_SD_API_KEY=             # Read by Settings, but unused: no ScienceDirect client exists yet
PRISMABIB_PROJECTS_ROOT=./projects
```

**Important:** Do not paste your API key into `SCOPUS_INSTTOKEN`. That field is for a
separate institutional token issued for the same key. If you put your API key there,
Scopus returns `401 "Institution Token is not associated with API Key"`. See
[Provenance — troubleshooting](../architecture/provenance.md#troubleshooting-scopus_insttoken-trap).

`.env` must exist before the next step: the CLI resolves its projects root through
`Settings`, which requires `SCOPUS_API_KEY` to be present. The empty value copied from
`.env.example` satisfies that.

### 1c. Scaffold the project

```bash
uv run prismabib init my-review --title "My systematic review"
```

Do **not** create the directory and files by hand. `Project.init` — which is what this
command calls — writes the whole §2.3 skeleton, including a `criteria.yaml` you will
otherwise not have, and screening later fails without it:

```
projects/my-review/
├── project.toml          # [project] metadata + a scaffolded, empty [query] table
├── criteria.yaml         # a commented eligibility-criteria template
├── decisions/
│   └── decisions.jsonl   # empty; the append-only Layer 2 log
├── raw/                  # Layer 0 (never git-tracked)
├── store/                # Layer 1 (never git-tracked)
├── taxonomy/rules/
├── fulltext/             # (never git-tracked)
└── exports/
```

`init` takes `--title/-t` and `--root/-r` (a projects root overriding
`PRISMABIB_PROJECTS_ROOT` for this one command). It is idempotent: re-running recreates a
missing directory but never overwrites `project.toml`, `criteria.yaml`, or
`decisions.jsonl`, and reports `Reused` rather than `Created` when it finds an existing
project.

The same thing from Python, if you prefer:

```python
from prismabib.project import Project

project = Project.init("my-review", title="My systematic review")
```

`Project` is a dataclass of `(slug, root)`. Construct it through `Project.init(...)` for a
new project or `Project.open("<slug>")` for an existing one — never
`Project(some_path)`, which is not a valid call.

### 1d. Fill in `project.toml`

`init` scaffolds `[query]` empty. Fill it in:

```toml
[project]
slug = "my-review"
title = "My systematic review"
created = 2026-08-25
track_decisions = true

[query]
terms = [
  "urban heat island",
  "urban thermal environment",
]
compound_terms = [
  { all = ["heat exposure", "urban"] },
]
fields = ["TITLE-ABS-KEY"]
```

**`[project]`:**

- `slug` — machine-readable identifier, also the directory name
- `title` — human-readable title for the review
- `created` — the date you started (ISO 8601)
- `track_decisions` — whether `decisions.jsonl` is committed to git for auditability
  (recommended)

**`[query]`:**

- `terms` — simple terms, each wrapped in `TITLE-ABS-KEY("...")` and OR-ed together
- `compound_terms` — groups that must all co-occur: each entry is a mapping
  `{ all = [...] }`, AND-ed within the group and OR-ed with everything else. A bare string,
  or any key other than `all`, raises `ConfigError` rather than being coerced into
  something plausible-looking
- `fields` — the Scopus field code(s) applied to every term. With more than one, each term
  becomes a parenthesised OR across them

The query builder renders the above into exactly:

```
TITLE-ABS-KEY("urban heat island") OR TITLE-ABS-KEY("urban thermal environment") OR (TITLE-ABS-KEY("heat exposure") AND TITLE-ABS-KEY("urban"))
```

Quotes and backslashes inside a term are escaped, so a term cannot break out of its clause
and inject extra Boolean structure. An empty query (`terms` and `compound_terms` both
empty) raises `ValidationError` instead of running against the whole database.

There is no `[snapshot]` table. `retrieved_at` and the Scopus view are not configuration:
the view is always `COMPLETE`, and the retrieval time is recorded by the capture itself in
`manifest.json`, which is the only place it can be trustworthy.

### 1e. Fill in `criteria.yaml`

This file is the machine-readable half of your protocol; every automated filter comes from
it, and every decision event records its `version`. The template is commented. What decides
numbers:

```yaml
version: 0.1.0            # semantic; bump whenever you change anything below

temporal:                 # inclusive both ends, from the record's cover date
  year_start: 2015
  year_end: 2026

subject_areas: []         # must stay empty for a Scopus Search API corpus — see below

doc_types:
  include: [ar, cp]       # Scopus subtype codes; empty = no restriction
  conference_whitelist: []

languages: [English]      # Scopus's own language string, matched exactly

manual_abstract:
  exclude_reason_codes: [OFF_TOPIC, REVIEW_OR_SURVEY, NOT_PRIMARY_RESEARCH]
manual_fulltext:
  exclude_reason_codes: [NO_FULL_TEXT, WRONG_POPULATION, INSUFFICIENT_DATA]
```

- **An empty list is "no restriction on that dimension"**, not "match nothing".
- **`languages` matches exactly** (case-insensitively): `"English"`, not `"en"`. A record
  with no language recorded is kept.
- **`conference_whitelist` is a case-insensitive substring match**, applied only to records
  whose venue is a conference; a journal article is never excluded by it.
- **Unknown keys are refused**, with the closest valid key named — `language:` for
  `languages:` is caught rather than silently dropped.
- **An inverted year window is refused** rather than emptying your corpus.
- **`subject_areas` cannot be enforced** on a corpus captured from the Scopus Search API,
  because `view=COMPLETE` does not return subject-area codes. A non-empty list raises
  `ConfigError`. See
  [Limitations](../methodology/limitations.md#subject_areas-is-declared-but-not-enforceable).

The `exclude_reason_codes` lists are a closed vocabulary: an exclusion citing an
undeclared code is refused. Edit the starters to the reasons your review distinguishes.

Commit both files. Criteria history is resolved from git alone
([Amend Eligibility Criteria](amend-eligibility-criteria.md)), so an uncommitted version
cannot be replayed.

## Step 2: run the search and capture Layer 0

### 2a. Execute the search

```bash
uv run prismabib search my-review
```

Or, from a notebook or script:

```python
from prismabib.capture.writer import capture_search
from prismabib.project import Project

project = Project.open("my-review")
manifest = capture_search(project)

print(f"Run ID: {manifest.run_id}")
print(f"Total records: {manifest.total_results}")
print(f"Pages fetched: {manifest.pages_fetched}")
print(f"Payload SHA-256: {manifest.payload_sha256}")
```

`capture_search` also accepts an explicit `query=` string, which overrides the
`project.toml` `[query]` table for that run. Use it deliberately: the query is recorded in
the manifest either way, but a query that is not in `project.toml` is not in git.

The run:

1. Reads `project.toml` and builds the Boolean query
2. Connects to the Scopus Search API with `view=COMPLETE` — never falling back to
   `STANDARD`; a 403 raises `EntitlementError` and stops the run
3. Paginates with a cursor (from `cursor=*`), never `start`/`count`, which caps at 5,000
4. Writes each page to `projects/my-review/raw/<run_id>/page-0000.jsonl`,
   `page-0001.jsonl`, … with the response envelope alongside it in
   `page-0000.meta.json`, and saves a `cursor.json` sidecar after every page
5. On completion writes `manifest.json`, deletes the cursor sidecar, and seals the run

**If interrupted:** run the same command again (or call `capture_search(project)` again).
It finds the unsealed run directory whose recorded query, view and endpoint match, resumes
from the saved cursor, and completes the run. Pages already in Layer 0 are never
re-requested and cost no quota.

### 2b. What is written

```
projects/my-review/
├── project.toml
├── criteria.yaml
├── raw/
│   ├── 20260825T090000Z-3f9a2c11/     # one run directory
│   │   ├── page-0000.jsonl            # one Scopus entry per line (25 per page)
│   │   ├── page-0000.meta.json        # the response envelope, minus `entry`
│   │   ├── page-0001.jsonl
│   │   ├── ...
│   │   └── manifest.json              # sealed: query, view, total_results, SHA-256
│   └── _cache/                        # HTTP cache; never a run, never git-tracked
```

- **`raw/` is never tracked by git** (see
  [Architecture Overview](../architecture/overview.md#repository-structure)) — it is large
  and licensed; the manifest is what makes the run reproducible.
- **`manifest.json` is the source of truth for "records identified"**: `total_results` is
  copied from Scopus's own `opensearch:totalResults`, never derived from a count of rows or
  pages.
- **`payload_sha256`** hashes every page file concatenated in fetch order. A re-run with the
  HTTP cache warm reproduces it byte for byte.
- **The run is sealed.** Sealing is the presence of `manifest.json`, and every write path in
  `capture/writer.py` refuses a sealed directory (`SealedRunError`) — immutability enforced
  in code, not by convention. A later `search` starts a new run directory.
- **One page is two files.** `page-NNNN.jsonl` is true JSON Lines, one record per line, so a
  record's `payload_line` addresses *that record*; the envelope lives in the sibling
  `.meta.json`. Together they reconstruct the response exactly.

### 2c. Verify the run

```python
import json
from pathlib import Path

manifest_path = Path("projects/my-review/raw/20260825T090000Z-3f9a2c11/manifest.json")
print(json.dumps(json.loads(manifest_path.read_text(encoding="utf-8")), indent=2))
```

You should see:

- `query` — the Boolean string built from your `project.toml`
- `view` — `"COMPLETE"`
- `total_results` — the PRISMA "records identified" count
- `pages_fetched` — how many pages Scopus returned
- `payload_sha256` — the content hash for provenance verification
- `client_version` — the prismabib version that made the capture, derived from the git tag
- `criteria_version` — the `criteria.yaml` version in force at capture time

## Step 3: build Layer 1

```bash
uv run prismabib build my-review
```

Equivalently:

```python
from prismabib.store.load import build_store

stats = build_store(project, rebuild=True)
print(stats.records_loaded, stats.duplicate_doi_groups)
```

Only **sealed** run directories are loaded; `raw/_cache/` is skipped. The traversal order
is fixed (runs by `run_id`, then each run's `payload_files` in fetch order, then line
order), which is what makes two builds over identical Layer 0 input produce identical
per-table checksums.

- **Without `--rebuild`, an existing store is reused as-is** and any run captured since it
  was built is not loaded. The command says so. After a new `search`, use
  `uv run prismabib build my-review --rebuild`.
- **Duplicates are reported, never applied**: `duplicate_doi_groups` /
  `duplicate_records` count normalised-DOI collisions, and both rows stay in the store.
  Removing one is a screening decision.
- **The store is derived and disposable.** Delete `store/corpus.duckdb` and rebuild; nothing
  is lost. Layer 0 is the archive of record.
- Affiliation country strings that do not map to an ISO 3166-1 alpha-3 code are kept
  verbatim and listed in the output, never dropped.

## Step 4: screen

There is no screening UI yet (Stage 5). Decisions are recorded through `DecisionLog.append`:

```python
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus

project = Project.open("my-review")

# Everything that survived the automated filters — the title/abstract queue.
corpus = Corpus.open(project)
queue = corpus.records(PrismaStage.LANGUAGE).select("record_id", "year", "title")

log = DecisionLog(project)
log.append(
    stage=PrismaStage.TITLE_ABSTRACT,
    record_id="scopus:2-s2.0-85101234567",
    reviewer="sh",
    decision="exclude",  # "include" | "exclude" | "unsure"
    reason_code="REVIEW_OR_SURVEY",  # required for "exclude"; must be declared for the stage
    note="secondary study",
)
```

Full-text decisions are the same call with `stage=PrismaStage.FULLTEXT`, and are only
meaningful for records that passed title/abstract screening — `M_full` is computed by
filtering `M_abs`.

Each append is written in one `write(2)`, `fsync`ed, and covered by a rewritten
`decisions.jsonl.sha256` sidecar under an exclusive `flock`. Nothing is ever edited: a
reversal is a new event, and the fold key `(stage, record_id, reviewer)` decides which
event currently counts. `criteria_version` is stamped automatically from the current
`criteria.yaml`.

To change criteria mid-review, see
[Amend Eligibility Criteria](amend-eligibility-criteria.md) — `engine.replay()` tells you
what the amendment costs in screening labour *before* you commit to it.

## Step 5: read the PRISMA flow counts

```bash
uv run prismabib flow my-review
```

Or:

```python
from prismabib.prisma.flow import compute_flow_counts

counts = compute_flow_counts(project)
counts.assert_consistent()
```

Every number is recomputed from Layer 1 and `decisions.jsonl` on each call; nothing is
cached or stored. The CLI prints the counts and, if the diagram's arithmetic does not
close, a warning on stderr — it still prints the numbers and still exits `0`, because an
incomplete-but-valid capture is a state you need described rather than one that should look
like a crash. Do not publish a diagram whose warning you have not explained. See
[PRISMA Mapping](../methodology/prisma-mapping.md) for the box-by-box audit table and the
four consistency equations.

---

## What is not here yet

- **Full-text retrieval and extraction** — no ScienceDirect client, no PDF/XML pipeline.
  "Reports not retrieved" cannot be expressed in `FlowCounts` and must be reported in prose.
- **A screening UI** — Stage 5, with the keyboard-first queue and inter-reviewer agreement
  statistics.
- **Taxonomy coding** (`prismabib code`), **bibliometrics**, **the Panel dashboard**, and
  **export/reporting** (`prismabib export`). The two commands are deliberately absent
  rather than stubbed: an absent command fails with "No such command", which is honest.

See [Limitations](../methodology/limitations.md) for the consolidated list.

---

See [Architecture Overview](../architecture/overview.md) for the four-layer design,
[Provenance](../architecture/provenance.md) for how a number traces back to Scopus, and
[Getting Started](../getting-started.md) for the shorter walkthrough of the same path.
