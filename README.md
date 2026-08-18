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

## Quick start

```bash
gh repo clone SergioHuesca/PRISMA-Bib
cd PRISMA-Bib
uv sync
cp .env.example .env
# Edit .env with your Scopus credentials
prismabib init
```

Then open the notebooks in `notebooks/` in Jupyter and follow the numbered sequence.

## Project status

**Stage 0 (v0.1.0)** — Repository bootstrap. What exists now:

- Fully governed GitHub repository with branch protection, CI/CD, secret scanning, and GitHub Pages enabled
- Python 3.11+, `uv` for reproducible environments
- Test harness with socket ban, frozen clock, and seeded ID factory
- Documentation skeleton with Material theme
- Pre-commit hooks for linting, formatting, and secret detection

Stages 1–11 are planned. See [BUILD_PLAN.md](https://github.com/SergioHuesca/PRISMA-Bib/blob/main/BUILD_PLAN.md) §7 for the full roadmap and version schedule.

## Visibility

The repository is **public**. This differs from the original plan (BUILD_PLAN.md §3.6.1) to keep it private until v1.0.0, but a GitHub free-tier account cannot enable branch protection *and* rulesets *and* secret scanning on a private repository simultaneously. Those protections were chosen over GitHub Pro. The history was audited before going public and verified empty—one root commit, no data ever tracked.

## Repository and package naming

The repository is `PRISMA-Bib` on GitHub, but the Python package is `prismabib` (no hyphen). If you see import statements like `import prismabib` or `from prismabib.store import db`, that is correct—they are not mistakes.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for branch naming, commit conventions, the Definition of Done, and the data licensing rules.

## Documentation

- [Getting Started](docs/getting-started.md) — from clone to first dashboard (Stage 11)
- [Architecture](docs/architecture/overview.md) — the four-layer model and design decisions
- [Testing](docs/testing.md) — test taxonomy, how to run each subset, snapshot management
- [Methodology](docs/methodology/) — PRISMA mapping, metric definitions, limitations
- [How To](docs/how-to/) — task-oriented guides for reviewers and maintainers
- [API Reference](docs/reference/) — generated from source docstrings

## License

MIT. See [LICENSE](LICENSE).
