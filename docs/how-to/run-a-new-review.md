# Run a New Review

Step-by-step guide for executing a full PRISMA + bibliometric review from start to finish.

## Delivered in Stage 11

This page will contain:

- **Clone and setup** — Clone the repository, install dependencies, configure credentials (Stage 11)
- **Project initialization** — `prismabib init`: create a new project, define search terms and eligibility criteria (Stage 11)
- **Search and capture** — Run `notebooks/00_search.ipynb` to query Scopus, persist raw results (Layer 0) (Stage 11)
- **Title/abstract screening** — Run `notebooks/01_screen_title_abstract.ipynb` with the Panel UI to filter records (Stage 11)
- **Full-text eligibility** — Run `notebooks/02_fulltext_eligibility.ipynb` to apply detailed criteria and capture full text (Stage 11)
- **Bibliometric analysis** — Run `notebooks/03_bibliometrics.ipynb` to compute growth, citations, venues, keywords, networks (Stage 11)
- **Taxonomy coding** — Run `notebooks/04_taxonomy.ipynb` to code records using versioned rules and human overrides (Stage 11)
- **Dashboard exploration** — Run `notebooks/05_dashboard.ipynb` to explore and validate the corpus interactively (Stage 11)
- **Export and report** — Run `notebooks/06_export_report.ipynb` to generate figures, tables, and manuscript assets (Stage 11)
- **Publication** — Export includes PRISMA flow diagram, all tables/figures, and `manifest.json` for provenance tracing (Stage 11)

Each notebook includes:

- Expected inputs (project configuration, decision log state)
- Key parameters and how to override defaults
- Expected outputs and where they are stored
- Troubleshooting for common issues

The workflow is designed to be **reproducible**: re-running the notebooks on the same project produces byte-identical outputs (determinism is tested; see [Testing](../testing.md)).

See [Getting Started](../getting-started.md) for a faster walkthrough, and [Architecture Overview](../architecture/overview.md) for the conceptual model underlying the workflow.
