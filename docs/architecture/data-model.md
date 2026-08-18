# Data Model

The data model defines the structure of records, metadata, and events flowing through the system.

## Delivered in Stage 1 and Stage 3

This page will contain:

- **Entity definitions** — Pydantic models for Record, Author, Affiliation, Venue, Keyword, Citation (Stage 1)
- **Record normalisation** — how Scopus's inconsistent field structure (scalar vs. list `afid`, optional `authkeywords`) is validated and normalised (Stage 1)
- **Schema and keys** — DuckDB table structure, primary/foreign keys, indexes (Stage 1)
- **Normalisation rules** — deterministic transformations for country codes, language codes, author name parsing (Stage 1)
- **Event schema** — structure of the decision log and taxonomy override events (Stage 1)
- **Extensions in Stage 3** — additions when full-text extraction is implemented (ScienceDirect API response handling, text extraction metadata)

For now, see [Architecture Overview](overview.md) for the conceptual model, and [BUILD_PLAN.md](https://github.com/SergioHuesca/PRISMA-Bib/blob/main/BUILD_PLAN.md) §4.1 (Stage 1) and §4.3 (Stage 3) for the detailed specifications.

The model is frozen by Stage 1 and never redesigned; extensions in later stages are additive only (new fields, new event types), never retroactive schema changes.
