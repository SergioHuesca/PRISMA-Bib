# prismabib

A reproducible PRISMA + bibliometric research lab: a Python library and Jupyter notebooks for executing the PRISMA-Bibliometric Hybrid Research Algorithm. Scopus acquisition, eligibility filtering, quantitative bibliometrics, and qualitative synthesis—with the central guarantee that **every number in every output is a query against a versioned store, never a literal**.

## The problem

Reference manuscripts often contain internally inconsistent numbers: dataset-usage counts in prose disagree with corresponding figures, and claims about methodology performance disagree with tables on the same page. This is not carelessness—it is the **predictable result** of numbers being computed once, copied into prose, and then drifting when the corpus is refreshed.

No amount of careful writing prevents this. The only solution is **architectural**: numbers must not be copyable. Every number that appears in any output must be a query against a versioned store, evaluated at render time.

## How it works

A four-layer pipeline ensures provenance and reproducibility:

### Layer 0: Raw capture (immutable)

Every HTTP response from Scopus and ScienceDirect is persisted verbatim as JSONL before any parsing. Each acquisition run writes a manifest recording:

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

### Layer 3: Analysis views (pure functions)

Deterministic transformations over Layers 1+2 producing:

- Interactive figures (Plotly) and camera-ready PDFs (Matplotlib)
- LaTeX tables for manuscripts
- SVG PRISMA flow diagrams
- A Panel dashboard for exploration (runs in-notebook or served standalone)
- Exports: CSV, Parquet, JSON with full provenance metadata

**Critical design:** No caching of results into files that could go stale. Figures are produced by functions that take a `Corpus` handle and return a figure object plus the dataframe that produced it. Every figure ships with its own data provenance.

## What is built (Stage 0)

The repository is live but nearly empty—the test harness and governance infrastructure are built, but the four-layer system itself is not yet implemented. Stage 0 exists to establish governance and CI before any source code is written.

Currently available:

- A fully governed GitHub repository with branch protection, CI/CD, secret scanning
- Python 3.11+ with `uv` for reproducible environments
- A test harness (socket ban, frozen clock, seeded ID factory, `tmp_project` fixture)
- Documentation skeleton with MkDocs Material theme
- Pre-commit hooks for linting, formatting, and secret detection

The architecture itself—Layers 0–3, Scopus acquisition, PRISMA logic, analysis views—is specified in [BUILD_PLAN.md](https://github.com/SergioHuesca/PRISMA-Bib/blob/main/BUILD_PLAN.md) and will be built in Stages 1–11.

## Getting started

**To clone and set up:**

```bash
gh repo clone SergioHuesca/PRISMA-Bib
cd PRISMA-Bib
uv sync
cp .env.example .env
# Edit .env with your Scopus credentials
```

**To run tests:**

```bash
pytest                          # All tests except live
pytest -m "unit or contract"    # Fast pre-commit subset
pytest -m live                  # Real Scopus (requires secrets)
```

**To build docs:**

```bash
mkdocs serve    # Live preview at http://localhost:8000
mkdocs build    # Static HTML in site/
```

## Documentation

- [Architecture Overview](architecture/overview.md) — the four-layer model in detail (Stage 1)
- [Architecture Decision Records](architecture/adr/) — design decisions and their justifications
- [Testing](testing.md) — test taxonomy, how to run each subset, snapshot management
- [Methodology](methodology/) — PRISMA mapping, metric definitions, limitations
- [How To](how-to/) — task-oriented guides for reviewers and maintainers
- [API Reference](reference/) — generated from source docstrings
- [Contributing](https://github.com/SergioHuesca/PRISMA-Bib/blob/main/CONTRIBUTING.md) — workflow, branch naming, commit conventions, Definition of Done

## Project status

- **Current stage:** 0 (v0.1.0) — Repository bootstrap
- **Next stage:** 1 (v0.2.0) — Domain model, project, five ADRs
- **Roadmap:** Stages 1–11 planned; see BUILD_PLAN.md §7 for full schedule and version tags

## Visibility

The repository is **public**. This differs from the original plan to keep it private until v1.0.0, but a GitHub free-tier account cannot enable branch protection and secret scanning on a private repository. The repository was made public before any data was tracked (history audited and verified empty).

See [ADR 0006](architecture/adr/0006-public-repository-and-single-owner-review.md) for the full context and governance implications.

## License

MIT. See [LICENSE](../LICENSE).

## Questions?

- **New contributor?** See [Contributing](https://github.com/SergioHuesca/PRISMA-Bib/blob/main/CONTRIBUTING.md)
- **Want to understand the architecture?** Start with [Architecture Overview](architecture/overview.md)
- **Need to know how to test?** See [Testing](testing.md)
- **Interested in methodology?** See [Methodology](methodology/)
