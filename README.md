# prismabib

A reproducible PRISMA + bibliometric research lab. Python library + Jupyter notebooks for executing the PRISMA-Bibliometric Hybrid Research Algorithm: Scopus acquisition, eligibility filtering, quantitative bibliometrics, and qualitative synthesis.

## The problem

Reference manuscripts often contain internally inconsistent numbers: dataset-usage counts in prose disagree with corresponding figures, and claims about methodology performance disagree with tables on the same page. This is not carelessness—it is the predictable result of numbers being computed once, copied into prose, and then drifting when the corpus is refreshed.

**This project's central architectural commitment: every number that appears in any output is a query against a versioned store, never a literal.** If a stage's design lets a human type a count into a document, that stage is wrong.

## What this is

A four-layer pipeline:

- **Layer 0 — Raw capture** (immutable): Every HTTP response persisted verbatim as JSONL + a manifest recording query, endpoint, timestamp, and SHA-256 of payloads. Allows claims like "as of 2026-01-15 Scopus returned 1,771 records" to remain provable after Scopus has drifted.

- **Layer 1 — Normalised store** (derived): DuckDB single file, fully rebuildable from Layer 0 by running one function.

- **Layer 2 — Decision log** (append-only): Screening decisions and taxonomy overrides as events, never mutations. Enables auditable reviewer histories and replaying screening under amended criteria.

- **Layer 3 — Analysis views** (pure functions): Deterministic transformations producing figures, tables, a Panel dashboard, and exports. Every figure ships with the dataframe that produced it.

## Before you start: which source you need

A review runs on one source, chosen per project. A Scopus review needs a Scopus API key with
`COMPLETE` view entitlement. That entitlement is granted to subscribing institutions, not to
personal or free API keys, and prismabib will not silently fall back to a lesser view.
`STANDARD` omits author keywords and full affiliation data, which would quietly bias the keyword
network and the geography analysis. A wrong number is worse than a refusal. Running from your
institution's network, or asking your university library for a Scopus *institutional token*
(`SCOPUS_INSTTOKEN`), is often enough. Get a key at [dev.elsevier.com](https://dev.elsevier.com).

Without Scopus access, a review runs on OpenAlex with a free key (source mode `openalex`). Its
records are OpenAlex's, not Scopus's. [Getting Started](docs/getting-started.md) opens with that
access question and walks the OpenAlex-only path from clone to a first screening decision.

## Quick start

Two paths, depending on whether you have Scopus access. [Getting Started](docs/getting-started.md)
opens with that question and covers each path in full.

Clone with git, not a zip: `credentials set` needs a git repository. Install with the extras
that the screening view and the notebook need:

```bash
git clone https://github.com/SergioHuesca/PRISMA-Bib.git
cd PRISMA-Bib
uv sync --extra viz --extra notebook
```

OpenAlex only (no Scopus key; mode `openalex`):

```bash
uv run prismabib init my-review --sources openalex --title "My systematic review" --actor "<your name>"
uv run prismabib credentials set openalex      # a free OpenAlex key; the prompt is hidden
# edit [openalex_query] search in projects/my-review/project.toml, and the year window in criteria.yaml
uv run prismabib openalex-search my-review --budget 5
uv run prismabib build my-review --rebuild
uv run prismabib flow my-review
```

Scopus (mode `scopus`):

```bash
uv run prismabib init my-scopus-review --sources scopus --title "My systematic review"
uv run prismabib credentials set scopus        # a Scopus key entitled to COMPLETE; the prompt is hidden
# edit [query] in projects/my-scopus-review/project.toml, and criteria.yaml
uv run prismabib search my-scopus-review       # spends Scopus quota; resumable if interrupted
uv run prismabib build my-scopus-review
uv run prismabib flow my-scopus-review
```

Screening runs in `notebooks/01_screen_title_abstract.ipynb`, with the slug set in
`PRISMABIB_NOTEBOOK_SLUG`. `DecisionLog.append` in Python is the scripted alternative. The
walkthrough for each path is in [Getting Started](docs/getting-started.md).

## Project status

**v1.1.0 (2026-11-05) — feature-complete against its build plan since v1.0.0; adds a Scopus-free source mode ([ADR 0042](docs/architecture/adr/0042-source-modes-and-openalex-as-a-primary-source.md)).** What works today:

- **Scopus acquisition** into an immutable, sealed Layer 0 archive, with a resumable
  cursor, a per-run HTTP cache that only resumes an interrupted run, and rate limiting.
  A re-run issues new requests (it spends quota); the same response bodies reproduce a
  byte-identical payload hash.
- **Layer 1 DuckDB store**, rebuildable from Layer 0 by one function call, with
  deterministic per-table checksums that do not depend on the DuckDB version.
- **The PRISMA engine**: the formal sets `S_raw`/`A`/`L`/`M_abs`/`M_full`/`C`, an
  append-only decision log with fsync-per-write and tamper detection, `FlowCounts` with a
  consistency guard, and replay under amended criteria.
- **A CLI**: `prismabib init | search | build | flow | enrich | fulltext | export | fill`. `register | reverse | registrations` are released in v1.1.0 (see [CHANGELOG.md](CHANGELOG.md)). Also in v1.1.0:
  - `sources <slug> [--set MODE]`: Show the project's source mode, or change it with --set (an append-only event).
  - `doctor [slug]`: Check the environment (Python, git, projects root, .env), then the project's mode and keys.
  - `credentials`: Store, inspect, test and remove API keys in the git-ignored .env (ADR 0042).
  - `openalex-search <slug> --budget N`: Capture OpenAlex works for the [openalex_query] search of a project, into a sealed Layer 0 run.
  - `cascade <slug>`: Fill missing fields: Scopus STANDARD, Scopus META, then OpenAlex.
  - `openalex <slug>`: Capture OpenAlex works for records and candidates with a field still missing.

The screening view, full-text retrieval, bibliometrics, the taxonomy engine,
dashboards, and export/reporting are also built. What v1.1.0 does and does not claim is stated in
[CHANGELOG.md](CHANGELOG.md). See
[docs/methodology/limitations.md](docs/methodology/limitations.md) for what that means in
practice before you adopt this for a real review, and
[BUILD_PLAN.md](https://github.com/SergioHuesca/PRISMA-Bib/blob/main/BUILD_PLAN.md) §7 for
the roadmap.

## Visibility

The repository is **public**. This differs from the original plan (BUILD_PLAN.md §3.6.1) to keep it private until v1.0.0, but a GitHub free-tier account cannot enable branch protection *and* rulesets *and* secret scanning on a private repository simultaneously. Those protections were chosen over GitHub Pro. The history was audited before going public and verified empty—one root commit, no data ever tracked.

## Repository and package naming

The repository is `PRISMA-Bib` on GitHub, but the Python package is `prismabib` (no hyphen). If you see import statements like `import prismabib` or `from prismabib.store import db`, that is correct—they are not mistakes.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for branch naming, commit conventions, the Definition of Done, and the data licensing rules.

## Documentation

- [Getting Started](docs/getting-started.md) — clone to your first screening decision
- [Architecture](docs/architecture/overview.md) — the four-layer model and design decisions
- [Testing](docs/testing.md) — test taxonomy, how to run each subset, snapshot management
- [Methodology](docs/methodology/) — PRISMA mapping, metric definitions, limitations
- [How To](docs/how-to/) — task-oriented guides for reviewers and maintainers
- [API Reference](docs/reference/) — generated from source docstrings

## License

MIT. See [LICENSE](LICENSE).
