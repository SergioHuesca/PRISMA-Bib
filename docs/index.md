# prismabib

A reproducible PRISMA + bibliometric research lab: a Python library and Jupyter notebooks for executing the PRISMA-Bibliometric Hybrid Research Algorithm. Scopus acquisition, eligibility filtering, quantitative bibliometrics, and qualitative synthesis—with the central guarantee that **every number in every output is a query against a versioned store, never a literal**.

## The problem

Reference manuscripts often contain internally inconsistent numbers: dataset-usage counts in prose disagree with corresponding figures, and claims about methodology performance disagree with tables on the same page. This is not carelessness—it is the **predictable result** of numbers being computed once, copied into prose, and then drifting when the corpus is refreshed.

No amount of careful writing prevents this. The only solution is **architectural**: numbers must not be copyable. Every number that appears in any output must be a query against a versioned store, evaluated at render time.

## How it works

A four-layer pipeline ensures provenance and reproducibility. Layers 0, 1 and 2 are built;
Layer 3 is described here as designed and is **not implemented yet** — see
[what is built today](#what-is-built-today-v050).

### Layer 0: Raw capture (immutable)

Every HTTP response is persisted verbatim as JSONL before any parsing. Today that means the Scopus Search API; ScienceDirect arrives with full-text acquisition, which is not built. Each acquisition run writes a manifest recording:

- The query string and Boolean operators
- The API endpoint and view (e.g., COMPLETE or STANDARD)
- The timestamp and entitlement context
- The result count
- A SHA-256 hash of the concatenated payloads

Nothing downstream can write to Layer 0. This layer is what allows the claim **"as of 2026-01-15 Scopus returned 1,771 records"** to remain provable after Scopus has drifted. You can re-run the query today and compare the manifest hashes.

### Layer 1: Normalised store (derived, disposable)

A DuckDB single file (fully rebuildable from Layer 0 by running one function). Chosen over SQLite because:

- The workload is **analytical**: group-bys over affiliations, keyword explosions, citation percentiles
- It reads and writes Parquet natively
- It hands results to pandas/polars with no serialisation ceremony
- It needs no server

**The key invariant:** Layer 1 must be reconstructible from Layer 0 by running one function. If something has been written to it that should have been an event in Layer 2, the architecture is broken.

### Layer 2: Decision log (append-only)

Screening decisions and taxonomy overrides are **events**, never mutations. Every decision record contains:

```
(event_id, timestamp, project, stage, record_id, reviewer, decision, reason_code, note, criteria_version, schema_version)
```

Current set membership is **derived** by folding the log. Consequences:

- The PRISMA flow diagram is a pure function of the log. No count is ever typed.
- A reviewer changing their mind appends a new event; the original remains auditable.
- Adding a second reviewer later requires no migration—agreement statistics become a query.
- Screening can be replayed under amended criteria without redoing human work that still applies.

### Layer 3: Analysis views (pure functions) — not built yet

Deterministic transformations over Layers 1+2, designed to produce:

- Interactive figures (Plotly) and camera-ready PDFs (Matplotlib)
- LaTeX tables for manuscripts
- SVG PRISMA flow diagrams
- A Panel dashboard for exploration (runs in-notebook or served standalone)
- Exports: CSV, Parquet, JSON with full provenance metadata

**Critical design:** No caching of results into files that could go stale. Figures are produced by functions that take a `Corpus` handle and return a figure object plus the dataframe that produced it. Every figure ships with its own data provenance.

## What is built today (v0.5.0)

**Layers 0, 1 and 2 are complete and tested. Layer 3 is not built.** Stages 0–4 have
shipped, tagged through `v0.5.0`.

What works:

- **Scopus acquisition** into an immutable, sealed Layer 0 archive: cursor pagination, a
  header-aware rate limiter, an HTTP cache, and a resumable cursor so an interrupted run
  costs no quota to continue. `view=COMPLETE` is mandatory and never degrades.
- **The Layer 1 DuckDB store**, rebuilt from Layer 0 by one function (`build_store`), with
  deterministic per-table checksums that do not depend on the DuckDB version or on the
  machine's timezone.
- **The PRISMA engine**: the formal sets `S_raw`/`A`/`L`/`M_abs`/`M_full`/`C`, an
  append-only decision log that is `fsync`ed per write, `flock`-serialised and
  checksum-guarded, `FlowCounts` with a consistency guard, and `replay()` under amended
  criteria.
- **A command-line wrapper**: `prismabib init | search | build | flow`, plus `--root/-r`
  and `--version`.

What does not exist yet: the screening UI, full-text retrieval, bibliometrics, the taxonomy
engine, the Panel dashboard, and export/reporting. `prismabib code` and `prismabib export`
are named in the build plan and are **deliberately absent** rather than stubbed — a
subcommand that accepts its arguments and then does nothing real is indistinguishable from
a working one at the point where it matters.

Screening therefore has no user interface today: decisions are recorded through
`DecisionLog.append` in Python. See [Limitations](methodology/limitations.md) for the full,
plainly stated list before adopting this for a real review.

## Getting started

```bash
git clone https://github.com/SergioHuesca/PRISMA-Bib.git
cd PRISMA-Bib
uv sync
cp .env.example .env                                    # then put your Scopus key in it

uv run prismabib init my-review --title "My review"     # then edit the two files it names
uv run prismabib search my-review                       # spends Scopus quota; resumable
uv run prismabib build my-review
uv run prismabib flow my-review
```

**prismabib needs a Scopus API key entitled to `view=COMPLETE`**, which is granted to
subscribing institutions rather than to personal keys. If your institution has no Scopus
subscription, this tool cannot run your review today — better to know that now.
[Getting Started](getting-started.md) states the access requirements first and walks the
whole path from clone to a first screening decision.

`uv sync` installs the library and the CLI. The test and documentation toolchains are
optional extras, so working on the project itself needs `uv sync --all-extras` — which is
what CI runs.

**To run tests:**

```bash
uv run pytest                          # All tests except live
uv run pytest -m "unit or contract"    # Fast pre-commit subset
uv run pytest -m live                  # Real Scopus and real GitHub; mutates the repository
```

**To build docs:**

```bash
uv run mkdocs serve             # Live preview at http://localhost:8000
uv run mkdocs build --strict    # Static HTML in site/; the CI gate
```

## Documentation

- [Getting Started](getting-started.md) — clone to your first screening decision
- [Architecture Overview](architecture/overview.md) — the four-layer model in detail
- [Architecture Decision Records](architecture/adr/) — design decisions and their justifications
- [Testing](testing.md) — test taxonomy, how to run each subset, snapshot management
- [Methodology](methodology/) — PRISMA mapping, metric definitions, limitations
- [How To](how-to/) — task-oriented guides for reviewers and maintainers
- [API Reference](reference/) — generated from source docstrings
- [Contributing](https://github.com/SergioHuesca/PRISMA-Bib/blob/main/CONTRIBUTING.md) — workflow, branch naming, commit conventions, Definition of Done

## Project status

- **Released:** v0.5.0 — Stage 4, the PRISMA engine and the Layer 2 decision log. Layers 0,
  1 and 2 are complete; see [what is built today](#what-is-built-today-v050).
- **Next:** Stage 5 — the keyboard-first screening UI (ADR 0004), which is what replaces
  calling `DecisionLog.append` by hand, plus inter-reviewer agreement statistics.
- **Then:** full-text acquisition, the taxonomy coder, bibliometrics, the dashboard, and
  export/reporting — Layer 3, none of it built.
- **Per-release detail:** [CHANGELOG.md](https://github.com/SergioHuesca/PRISMA-Bib/blob/main/CHANGELOG.md),
  which records what each stage added, changed, and fixed.

The frozen build specification (`BUILD_PLAN.md`) is the project's internal contract and is
not published in this repository; the documentation cites its section numbers so that a
claim here can be traced to the clause it implements.

## Visibility

The repository is **public**. This differs from the original plan to keep it private until v1.0.0, but a GitHub free-tier account cannot enable branch protection and secret scanning on a private repository. The repository was made public before any data was tracked (history audited and verified empty).

See [ADR 0006](architecture/adr/0006-public-repository-and-single-owner-review.md) for the full context and governance implications.

## License

MIT. See [LICENSE](../LICENSE).

## Questions?

- **Want to run a review?** Start with [Getting Started](getting-started.md), which states the Scopus access requirements before anything else
- **Deciding whether to adopt this?** Read [Limitations](methodology/limitations.md) first
- **New contributor?** See [Contributing](https://github.com/SergioHuesca/PRISMA-Bib/blob/main/CONTRIBUTING.md)
- **Want to understand the architecture?** Start with [Architecture Overview](architecture/overview.md)
- **Need to know how to test?** See [Testing](testing.md)
- **Interested in methodology?** See [Methodology](methodology/)
