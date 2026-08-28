# Getting Started

From a clone of this repository to a PRISMA 2020 flow count you can defend, on your own
topic and your own Scopus key.

At the end of this page you will have:

- a sealed, immutable Layer 0 archive of exactly what Scopus returned for your query,
- a Layer 1 DuckDB store rebuilt from it by one command,
- one screening decision recorded as an append-only, checksum-guarded event, and
- the PRISMA flow counts derived from all three, with nothing typed by hand.

Budget an hour, most of it spent waiting for Scopus.

## Before you invest any time: Scopus `COMPLETE` entitlement

!!! warning "prismabib requires a Scopus API key entitled to the COMPLETE view, and many researchers do not have one"

    `ScopusClient.search` always requests `view=COMPLETE` and **never** falls back to
    `STANDARD`. A 403 raises `EntitlementError` and the run stops. That is deliberate:
    `STANDARD` omits `authkeywords`, the full `author` block and `dc:description`, so a
    corpus built from it would be missing author keywords, affiliations and abstracts
    while looking complete — quietly biasing every keyword and geography analysis built on
    it afterwards.

    The `COMPLETE` entitlement is granted to **subscribing institutions**, not to personal
    or free keys. In practice:

    1. Running from your institution's network is often enough.
    2. Off campus, ask your university library for a Scopus **institutional token** and set
       `SCOPUS_INSTTOKEN` alongside `SCOPUS_API_KEY`. This is a routine request; librarians
       handle it regularly.
    3. If your institution has no Scopus subscription, prismabib cannot run your review
       today. Nothing later in this page changes that.

    Get a key at [dev.elsevier.com](https://dev.elsevier.com). There is no way to discover
    the entitlement problem except by attempting a capture, which is why it is stated here
    first.

Two more access facts worth knowing before you start:

- **Scopus enforces a weekly quota.** A large capture spends it. `prismabib search` is
  resumable and never re-fetches a page Layer 0 already holds, so an interruption costs
  nothing, but a repeatedly restarted *fresh* run does.
- **Only Scopus is implemented.** No Web of Science, no OpenAlex, no Crossref, and no
  full-text retrieval. See [Limitations](methodology/limitations.md) before adopting this
  for a real review.

## What exists today

Layers 0, 1 and 2 are built and tested. Concretely:

| You can | Command or API |
| --- | --- |
| Scaffold a project | `prismabib init <slug>` |
| Capture Scopus into Layer 0 | `prismabib search <slug>` |
| Build the Layer 1 store | `prismabib build <slug>` |
| Print PRISMA 2020 flow counts | `prismabib flow <slug>` |
| Record a screening decision | `DecisionLog.append(...)` in Python |
| Replay under amended criteria | `engine.replay(project, criteria_version=...)` |

There is **no screening UI yet** (Stage 5), so step 8 below records decisions through the
Python API. `prismabib code` and `prismabib export` do not exist — deliberately, rather
than as stubs that accept arguments and do nothing.

## 1. Clone and install

```bash
git clone https://github.com/SergioHuesca/PRISMA-Bib.git
cd PRISMA-Bib
uv sync
```

`uv sync` installs the package and its console script. Check it:

```bash
uv run prismabib --version
```

The version is derived from the git tag, never typed into `pyproject.toml` — a clone at
`v0.5.0` prints `prismabib 0.5.0`, a clone a few commits past it prints something like
`prismabib 0.5.0.post1.dev2+g696a64c4e`, and a tarball with no `.git` prints
`prismabib 0+unknown`. That string is stamped into every capture manifest as
`client_version`, so it says what it actually is rather than a plausible release number.

Run every command below from the repository root, with `uv run` in front of it.

## 2. Create `.env`

```bash
cp .env.example .env
```

Then edit it:

```bash
SCOPUS_API_KEY=<your key>
SCOPUS_INSTTOKEN=          # only if your library issued one; leave empty otherwise
ELSEVIER_SD_API_KEY=       # read, but nothing uses it yet — no ScienceDirect client exists
PRISMABIB_PROJECTS_ROOT=./projects
```

!!! danger "Do not paste your API key into SCOPUS_INSTTOKEN"

    That field is for a separate institutional token issued *for that same key*. Scopus
    reports the mismatch as `401 "Institution Token is not associated with API Key"`. If
    you have no token, leave the variable empty rather than filling it with a placeholder.
    See [Provenance — troubleshooting](architecture/provenance.md#troubleshooting-scopus_insttoken-trap).

`.env` is gitignored and must stay that way. Every secret is typed `SecretStr`, so it
cannot leak through `repr`, `str`, `model_dump`, a traceback, or a log line — the Scopus
client also registers the key with a structlog processor that scrubs it from every event.

`init` itself needs no credential — it resolves its projects root through
`ProjectsRootSettings`, which declares no required secret — so you can lay a project out
before your key arrives. `.env` is needed from `prismabib search` onward, which is the
first step that talks to Scopus.

## 3. `prismabib init`

```bash
uv run prismabib init my-review --title "My systematic review"
```

Options: `--title/-t` (written into `project.toml`), and `--root/-r` to override
`PRISMABIB_PROJECTS_ROOT` for this one command.

This creates the project skeleton and prints the two files you must edit next:

```
projects/my-review/
├── project.toml          # [project] metadata + the [query] table you fill in
├── criteria.yaml         # your eligibility criteria
├── decisions/
│   └── decisions.jsonl   # empty; the append-only Layer 2 log
├── raw/                  # Layer 0 archive (never git-tracked)
├── store/                # Layer 1 DuckDB (never git-tracked)
├── taxonomy/rules/
├── fulltext/             # (never git-tracked)
└── exports/
```

`init` is idempotent and safe to re-run: it recreates any missing directory but never
overwrites `project.toml`, `criteria.yaml`, or `decisions.jsonl`. Those hold hand-written
methodology and human labour. When it finds an existing project it says `Reused` rather
than `Created`, so it never points you at a template you filled in months ago.

Commit `project.toml` and `criteria.yaml`. Criteria history is resolved from git alone, so
an uncommitted amendment is invisible to replay later.

## 4. Edit `project.toml` — the search itself

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

Substitute your own concepts; the shape is what matters.

- `terms` — each rendered as `FIELD("term")` and OR-ed together.
- `compound_terms` — a **list** of `{ all = [...] }` groups, each becoming a parenthesised
  AND group that is itself OR-ed with everything else. A bare string, a single group
  written without its surrounding `[ ]`, or any key other than `all`, is refused rather
  than coerced into something plausible-looking. A longer list is often easier to read as
  repeated `[[query.compound_terms]]` headers, which mean the same thing.
- Keys this table does not define are **refused, not ignored** — `compound_term` without
  its `s` would otherwise drop a whole AND group and run a narrower search than the file
  describes.
- `fields` — applied to every term. With more than one field, each term becomes a
  parenthesised OR across them.

The example above renders exactly:

```
TITLE-ABS-KEY("urban heat island") OR TITLE-ABS-KEY("urban thermal environment") OR (TITLE-ABS-KEY("heat exposure") AND TITLE-ABS-KEY("urban"))
```

Quotes and backslashes in a term are escaped, so a term cannot break out of its clause and
inject Boolean structure. An empty `[query]` table is refused: prismabib will not silently
run your key against the whole of Scopus.

## 5. Edit `criteria.yaml` — who is eligible

`init` writes a commented template. The parts that decide numbers:

```yaml
version: 0.1.0            # semantic; bump it whenever you change anything below

temporal:                 # inclusive on both ends, from the record's cover date
  year_start: 2015
  year_end: 2026

subject_areas: []         # leave empty — see the warning below

doc_types:
  include: [ar, cp]       # Scopus subtype codes; empty = no document-type restriction
  conference_whitelist: []

languages: [English]      # empty = no language restriction

manual_abstract:
  exclude_reason_codes: [OFF_TOPIC, REVIEW_OR_SURVEY, NOT_PRIMARY_RESEARCH]
manual_fulltext:
  exclude_reason_codes: [NO_FULL_TEXT, WRONG_POPULATION, INSUFFICIENT_DATA]
```

Five rules that change published counts, all enforced in code:

- **An empty list means "no restriction on that dimension"**, not "match nothing".
  Emptying `doc_types.include` accepts every type; it does not exclude everything.
- **`languages` matches Scopus's own language string exactly** (case-insensitively):
  `"English"`, not `"en"` or `"eng"`. A record with no language recorded is kept.
- **`conference_whitelist` is a case-insensitive *substring* match on the venue name, and
  applies only to records whose venue is a conference.** A journal article is never
  excluded by it. A short token like `"AI"` matches almost any venue name; prefer a
  distinctive fragment such as `"Computer Vision and Pattern Recognition"`.
- **Unknown keys are refused, not ignored.** Writing `language:` instead of `languages:`
  raises `ConfigError` naming the key, the block it appeared in, and the closest valid
  alternative. A silently dropped key would be an eligibility rule that silently did not
  apply, and the resulting corpus looks entirely plausible.
- **An inverted year window is refused.** `year_start: 2026` with `year_end: 2015` would
  match no record at all and report that automation excluded your entire corpus.

!!! warning "subject_areas cannot be enforced on a Scopus Search API corpus"

    The Search API's `view=COMPLETE` response does not carry subject-area codes, so no
    record in a corpus captured by prismabib has any. Rather than let the filter quietly
    become a no-op — a PRISMA diagram claiming a restriction that never ran — a non-empty
    `subject_areas` against a corpus with no subject-area data raises `ConfigError`.

    Leave it `[]` and record the restriction in your protocol, applying it during
    title/abstract screening where it becomes a logged decision with a reason code. See
    [Limitations](methodology/limitations.md#subject_areas-is-declared-but-not-enforceable)
    for the alternative and its cost.

The reason codes are a **closed vocabulary**: an exclusion citing a code not listed for
that stage is refused, which is what keeps the diagram's exclusion breakdown
pre-registered rather than invented mid-screening. The template ships PRISMA-conventional
starters — edit them to the reasons your review actually distinguishes.

## 6. `prismabib search` — capture Layer 0

```bash
uv run prismabib search my-review
```

This spends Scopus quota. Progress goes to stderr, one line per page written; the sealed
run's manifest goes to stdout:

```
Run 20260825T090000Z-3f9a2c11 sealed at projects/my-review/raw/20260825T090000Z-3f9a2c11
  query          TITLE-ABS-KEY("urban heat island") OR ...
  view           COMPLETE
  total_results  1,771  (the PRISMA 'identified' count)
  pages          71
  payload_sha256 9f2c...
  client_version 0.5.0
  criteria       0.1.0

Next: prismabib build my-review
```

- **Interrupting is safe.** Every page is written as it arrives and a cursor is saved after
  each one. Ctrl-C exits `130` and prints how to resume; re-running the same command
  continues from the last page written, at no quota cost for what is already on disk.
- **A finished run is sealed and immutable.** Sealing is the presence of `manifest.json`;
  every write path refuses a sealed directory in code, not by convention. Running `search`
  again after a completed run starts a *new* run directory rather than amending the old one.
- **`total_results` comes from Scopus's own `opensearch:totalResults`**, never from a count
  of rows or pages. It is this search's contribution to the PRISMA "records identified"
  number: one search string, one term. A project that runs a second, different search string
  adds that search's total too; re-running the *same* string later adds nothing.
- **`raw/` is never git-tracked.** It is large and licensed; the manifest is what makes the
  run reproducible.

## 7. `prismabib build` — derive Layer 1

```bash
uv run prismabib build my-review
```

It prints per-table row counts, the duplicate-DOI report, and any affiliation country
string that did not map to an ISO 3166-1 alpha-3 code (kept verbatim, never dropped).

Two things to know:

- **Without `--rebuild`, an existing store is reused as-is** and any Layer 0 run captured
  since it was built is *not* loaded. The output says so explicitly. After a new `search`,
  run `uv run prismabib build my-review --rebuild`.
- **Duplicates are reported, never applied.** Both members of a duplicate-DOI pair stay in
  the store as ordinary rows: removing one is a screening decision, not a load-time one.

The store is derived and disposable. Deleting `projects/my-review/store/corpus.duckdb` and
rebuilding loses nothing — Layer 0 is the archive of record.

## 8. Record your first screening decision

There is no screening UI yet, so decisions go through `DecisionLog.append`. Run this from
the repository root:

```python
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus

project = Project.open("my-review")

# The screening queue: everything that survived the automated filters.
corpus = Corpus.open(project)
queue = corpus.records(PrismaStage.LANGUAGE).select("record_id", "year", "title")
print(queue.head(10))

log = DecisionLog(project)
log.append(
    stage=PrismaStage.TITLE_ABSTRACT,
    record_id="scopus:2-s2.0-85101234567",  # one from the queue above
    reviewer="sh",
    decision="exclude",  # "include" | "exclude" | "unsure"
    reason_code="REVIEW_OR_SURVEY",  # required for "exclude"; must be declared
    note="secondary study",
)
```

What that append guarantees:

- The event is written in one `write(2)`, `fsync`ed, and covered by a rewritten
  `decisions.jsonl.sha256` sidecar, under an exclusive `flock`. Hand-editing the log is
  detected on the next read; an *interrupted* append is diagnosed distinctly from tampering.
- `criteria_version` is stamped automatically from the project's current `criteria.yaml`.
  Pass it explicitly only when backfilling a decision genuinely made under a superseded
  version.
- `reason_code` is mandatory for `exclude` and must be one of that stage's declared codes.
- Nothing is ever edited or deleted. Changing your mind means appending a new event; the
  fold key is `(stage, record_id, reviewer)`, so the later event supersedes the earlier one
  for that key and nobody else's decision is touched.
- `"unsure"` never resolves to inclusion. It keeps the record in the queue.

## 9. `prismabib flow` — the PRISMA 2020 counts

```bash
uv run prismabib flow my-review
```

```
PRISMA 2020 flow -- project 'my-review'

Identification
  records identified (Scopus total_results)         1,771

Removed before screening
  duplicates across searches                            0
  other reasons (unreadable capture entries)            0

Screening -- automated, from criteria.yaml
  excluded by year / subject area / doc type         -412
  remaining                                         1,359
  excluded by language                                -37
  remaining, to title/abstract screening            1,322

Screening -- title/abstract, from logged human decisions
  excluded                                             -1
  unsure or not yet screened                        1,321
      (unsure never resolves to inclusion; it stays in the queue)
  sought for full-text retrieval                        0

Eligibility -- full text, from logged human decisions
  excluded                                              0
      (no full-text exclusion reason codes logged)
  unsure or not yet screened                            0
      (unsure never resolves to inclusion; it stays in the queue)

Included
  studies in the final corpus                           0
```

That is the state after exactly one logged exclusion: 1,322 records reached title/abstract
screening, one is excluded, and the other 1,321 are unscreened — which the report calls
"unsure or not yet screened" rather than quietly counting them as anything else.

Every number is recomputed from Layer 1 and `decisions.jsonl` on each run — nothing is
cached, so the report always describes screening as it stands right now.

That transcript is a project with **one** search string, which is why both "removed before
screening" lines are zero — they are printed even then, so that a reader of a published
diagram can see the line was considered rather than omitted. Run a second search string —
edit `[query]`, `search` again, `build --rebuild` — and they come alive: "records identified"
becomes the sum of `total_results` over the project's *distinct* searches rather than the
first search's total, "duplicates across searches" counts the papers both searches returned
(identified twice, stored once), and "other reasons" counts entries the loader could not read
at all. Re-running the *same* search string to refresh citation counts is not a second search:
it changes neither "records identified" nor the duplicate count. See
[ADR 0013](architecture/adr/0013-identified-sums-across-searches.md).

If the counts do not close into a consistent diagram, the command prints a warning on
stderr, still prints the numbers, and still exits `0`. That is not a crash: the usual cause
is a capture that is incomplete or a store built before the last `search` finished. Do not
publish a diagram whose warning you have not explained. See
[PRISMA Mapping — the four consistency equations](methodology/prisma-mapping.md#the-four-consistency-equations)
and [when equation 1 does not close](methodology/prisma-mapping.md#when-equation-1-does-not-close),
which lists every cause observed so far and what to do about each.

## When something goes wrong

Every failure prismabib raises on purpose is printed with its class name and its full
message, and exits `1`. The messages are written to be acted on; nothing re-wraps or
truncates them.

| Error | What it means | What to do |
| --- | --- | --- |
| `EntitlementError` | HTTP 403: your key is valid, but not entitled to `view=COMPLETE` | Run from your institution's network, or get an institutional token. prismabib will not degrade to `STANDARD` |
| `AuthError` | HTTP 401: the key itself was rejected | Check `SCOPUS_API_KEY`; check you did not paste the key into `SCOPUS_INSTTOKEN` |
| `QuotaExceededError` | The weekly Scopus quota is exhausted | Wait for the reset. Everything already captured is in Layer 0 and resumable |
| `ConfigError: … SCOPUS_API_KEY` | No `.env` and no environment variable | `cp .env.example .env` |
| `ConfigError` naming a `criteria.yaml` key | An unknown key — usually a typo | The message names the closest valid key and lists what is valid in that block |
| `ConfigError` about `subject_areas` | A restriction that no record in the corpus can be evaluated against | Set `subject_areas: []`; see [Limitations](methodology/limitations.md) |
| `StoreError: No Layer 1 store at …` | Layer 1 has not been built | `uv run prismabib build <slug>` |
| `LogError` about a `reason_code` | The code is not declared for that stage in `criteria.yaml` | Add it to the vocabulary (and bump `version`), or use a declared code |
| `ValidationError: query has no terms` | `[query].terms` and `compound_terms` are both empty | Fill in the query before running `search` |

Anything that is *not* one of these prints a traceback. That is intentional: an unexpected
exception is a bug, and a bug that prints one polite line is a bug nobody can report.

## Next

- [Run a New Review](how-to/run-a-new-review.md) — the same path in more detail, including
  what Layer 0 looks like on disk and how to verify a run.
- [Amend Eligibility Criteria](how-to/amend-eligibility-criteria.md) — changing
  `criteria.yaml` after screening has begun, and costing the change before you commit to it.
- [Limitations](methodology/limitations.md) — what this tool cannot do today, in one place.
- [PRISMA Mapping](methodology/prisma-mapping.md) — how to audit every number in a
  published flow diagram.
- [Architecture Overview](architecture/overview.md) — the four-layer model.
- [Testing](testing.md) — the test taxonomy and how to run each subset.
