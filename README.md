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

## Before you start: Scopus access

**prismabib needs a Scopus API key with `COMPLETE` view entitlement.** That entitlement
is granted to subscribing institutions, not to personal or free API keys, and prismabib
will not silently fall back to a lesser view — `STANDARD` omits author keywords and full
affiliation data, which would quietly bias the keyword network and the geography analysis.
A wrong number is worse than a refusal.

In practice this means running from your institution's network, or asking your university
library for a Scopus *institutional token* and setting `SCOPUS_INSTTOKEN`. If your
institution has no Scopus subscription, this tool cannot run your review today. Better to
know that now than after an hour of setup.

Get a key at [dev.elsevier.com](https://dev.elsevier.com).

## Quick start

```bash
gh repo clone SergioHuesca/PRISMA-Bib
cd PRISMA-Bib
uv sync

prismabib init my-review --title "My systematic review"
```

`init` creates `projects/my-review/` and tells you which two files to edit. It needs no
Scopus key — nothing before `search` touches the network — so you can lay a project out
while you are still waiting for access. Fill in:

- **`project.toml`** — the `[query]` table: the Boolean search itself.
- **`criteria.yaml`** — your eligibility criteria: year window, document types, languages,
  and the exclusion reason codes your review will use. It ships with a PRISMA-conventional
  starter vocabulary to edit.

Then:

```bash
cp .env.example .env          # put your Scopus key in it -- needed from here on
prismabib search my-review    # spends Scopus quota; resumable if interrupted
prismabib build my-review     # derive the Layer 1 store from the raw capture
prismabib flow my-review      # print the PRISMA 2020 flow counts
```

Screening decisions currently go through `DecisionLog.append` in Python; the keyboard-first
screening UI is the next release. See [Getting Started](docs/getting-started.md) for the
full walkthrough.

## Project status

**v0.5.0 — Layers 0, 1 and 2 are complete.** What works today:

- **Scopus acquisition** into an immutable, sealed Layer 0 archive, with a resumable
  cursor, an HTTP cache, and rate limiting. Re-running with a warm cache reproduces a
  byte-identical payload hash.
- **Layer 1 DuckDB store**, rebuildable from Layer 0 by one function call, with
  deterministic per-table checksums that do not depend on the DuckDB version.
- **The PRISMA engine**: the formal sets `S_raw`/`A`/`L`/`M_abs`/`M_full`/`C`, an
  append-only decision log with fsync-per-write and tamper detection, `FlowCounts` with a
  consistency guard, and replay under amended criteria.
- **A CLI**: `prismabib init | search | build | flow`.

Not built yet: the screening UI, full-text retrieval, bibliometrics, the taxonomy engine,
dashboards, and export/reporting. See
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
