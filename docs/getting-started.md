# Getting Started

A quick walkthrough from cloning the repository to running your first dashboard.

## Delivered in Stage 11

This page will contain:

- **Prerequisites** — Python 3.11+, `uv`, Scopus API credentials (Stage 11)
- **Installation** — Clone, `uv sync`, configure `.env` (Stage 11)
- **Quick project setup** — `prismabib init` with minimal options (Stage 11)
- **Sample query** — a small Scopus search to verify credentials and data flow (Stage 11)
- **Running notebooks** — step-by-step through each notebook with expected output (Stage 11)
- **First dashboard** — accessing the Panel dashboard in `notebooks/05_dashboard.ipynb` (Stage 11)
- **Troubleshooting**:
  - "Got an API error" — diagnosing Scopus credential issues
  - "The dashboard is slow" — understanding why and when it's expected
  - "I want to use a different Scopus field" — where to modify the schema
  - "How do I export my results?" — using `notebooks/06_export_report.ipynb` (Stage 11)

The walkthrough assumes no prior experience with the system and takes about 30–60 minutes (most time spent waiting for Scopus queries).

For in-depth guides on specific tasks, see [How To](how-to/).

For the architecture and design decisions, see [Architecture](architecture/).

For understanding how to test and extend the system, see [Testing](testing.md).
