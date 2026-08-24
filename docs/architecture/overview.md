# Architecture Overview

The prismabib system is built as a four-layer pipeline that ensures every number in published output traces back to a versioned, queryable source.

## The four layers

```
  Scopus Search API      ScienceDirect API       Manual PDF drop
          |                      |                      |
          +----------------------+----------------------+
                                 v
  LAYER 0 · RAW CAPTURE        immutable JSONL + hashed run manifest
                                 |
                                 v
  LAYER 1 · NORMALISED STORE   DuckDB, fully rebuildable from Layer 0
                                 |
                    +------------+------------+
                    v                         v
  LAYER 2 · DECISION LOG          LAYER 2 · TAXONOMY CODING
  append-only PRISMA events       versioned rules + override events
                    |                         |
                    +------------+------------+
                                 v
  LAYER 3 · ANALYSIS VIEWS     figures, tables, Panel dashboard, exports
```

### Layer 0 — Raw capture (immutable)

Every HTTP response from Scopus and ScienceDirect is persisted verbatim as JSONL before any parsing or transformation. Each acquisition run produces a manifest recording:

- **Query and operators** — the exact Boolean search expression
- **Endpoint** — which API (Search, Retrieve, ScienceDirect Full Text)
- **View** — the entitlement level (STANDARD vs COMPLETE on Scopus)
- **Timestamp** — when the query executed
- **Result count** — how many records were returned
- **SHA-256 hash** — of the concatenated payload bytes

**Key property:** Nothing downstream may write to Layer 0. It is immutable and append-only.

**Why this matters:** A claim like **"as of 2026-01-15 Scopus returned 1,771 records"** remains provable after Scopus has drifted. You can re-run the query today and compare manifests; if the hash matches, Scopus data has not changed since the original run; if it differs, you can see exactly how.

### Layer 1 — Normalised store (derived, disposable)

A single DuckDB file containing a relational schema derived from Layer 0. It includes:

- **Records** — papers, reports, reviews (denormalized from Scopus's inconsistent field structure)
- **Authors and affiliations** — resolved and deduplicated
- **Venues** — journal/conference metadata with ISSNs
- **Keywords** — author and Scopus index keywords
- **Citations** — if captured (via ScienceDirect API for entitled full text)

**Key property:** Layer 1 must be reconstructible from Layer 0 by running one function. If something has been written to it that should have been an event in Layer 2, the architecture is broken.

**Why this matters:** Layer 1 is disposable. If the schema needs revision, you rebuild it from Layer 0. No data ever drifts between layers—rebuilding Layer 1 is deterministic and reproducible.

### Layer 2 — Decision log (append-only)

Two append-only event streams:

**Decision log (PRISMA screening):**
Every screening decision is an immutable event:

```
(event_id, timestamp, project, stage, record_id, reviewer, decision,
 reason_code, note, criteria_version, schema_version)
```

Decisions are never edited—if a reviewer changes their mind, a new event is appended. Consequences:

- The **PRISMA flow diagram** is a pure function of the log (folded at query time)
- **No count is ever typed**—flow counts are derived from the log's contents
- **Auditable history** — which reviewer decided what, and when
- **Replay-able** — screening can be redone under amended criteria without losing prior human work
- **Multi-reviewer ready** — adding a second reviewer requires no schema migration; agreement statistics become a query over the log

**Taxonomy coding:**
Versioned rule files (YAML) plus override events. As methodology understanding evolves, rules change; the taxonomy history is the audit trail.

**Key property:** Append-only. Current state is derived from the log; you never mutate the log.

### Layer 3 — Analysis views (pure functions)

Deterministic queries and transformations over Layers 1+2, producing:

- **Figures** — interactive (Plotly) and camera-ready (Matplotlib)
- **Tables** — formatted as LaTeX for manuscript inclusion
- **Flow diagrams** — SVG PRISMA flow diagrams (derived from the decision log)
- **Dashboard** — an interactive Panel widget for exploration
- **Exports** — CSV, Parquet, JSON with full provenance metadata

**Key property:** No caching of results into files. Figures are produced by functions that take a `Corpus` handle and return:
1. A figure object (Plotly Figure, Matplotlib Figure, or SVG string)
2. The dataframe that produced it

Every figure ships with its own data provenance—you can see exactly which records were included and how they were counted.

## Why this architecture?

This design solves the central problem: **numbers that drift between a manuscript and a corpus.**

A traditional workflow:

1. Run a query → get 1,771 records
2. Compute flow counts, filter by language → now 1,550 records
3. Copy "1,550" into the manuscript
4. Six months later, Scopus updates and now returns 1,780 records
5. Rebuild the database → now 1,562 records
6. Update the manuscript manually... or forget to
7. **Result: the published paper claims 1,550 but the current database has 1,562**

The prismabib approach:

1. Layer 0 captures every query result, versioned by manifest hash
2. Layer 1 normalizes consistently (always the same transformation)
3. Layer 2 records screening decisions as immutable events
4. Layer 3 queries derive every number at render time

The manuscript never contains a literal count. It contains a reference: "The database at commit SHA `abc123` with criteria version 2.1 contains 1,550 records after language filtering." If Scopus changes, a new run produces new numbers; the old numbers remain traceable to the old manifest hash.

## Design invariants

- **Immutability of sources:** Layer 0 is append-only; once written, a raw API response never changes
- **Deterministic derivation:** Layer 1 is reproducible; identical Layer 0 always produces identical Layer 1
- **Append-only decisions:** Layer 2 events are never edited; disagreement is recorded as a new event
- **Stateless queries:** Layer 3 functions take inputs (corpus, criteria) and produce outputs; no hidden state
- **No cross-layer dependencies:** Layers do not import each other's internals; they communicate through data only

## Repository structure

```
src/prismabib/
├── sources/          # HTTP to Scopus/ScienceDirect (§3.1 Sources)
├── capture/          # Layer 0 writer and manifest (§3.2 Capture)
├── store/            # Layer 1 DuckDB and normalisation (§3.3 Store)
├── prisma/           # Layer 2 decision log and PRISMA engine (§3.4 PRISMA)
├── screening/        # UI for human screening (§3.5 Screening)
├── fulltext/         # Full-text PDF/XML extraction (§3.6 Full text)
├── taxonomy/         # Layer 2 taxonomy coder (§3.7 Taxonomy)
├── bibliometrics/    # Layer 3 quantitative analysis (§3.8 Bibliometrics)
├── viz/              # Layer 3 figures and dashboard (§3.9 Visualisation)
├── report/           # Layer 3 export and manuscript assets (§3.10 Reporting)
├── countries.py      # ISO-3166 country normalisation (Stage 1 addition; see next section)
└── ...
```

### Additions to BUILD_PLAN §2.3 repository layout

**`src/prismabib/stage.py`** (Stage 3, PRISMA flow stages)

The `PrismaStage` enum, naming the six named record sets of the PRISMA 2020 flow: `RAW` (unfiltered), `AUTOMATED` (deterministic year/subject/doc-type filter), `LANGUAGE` (deterministic language filter), `TITLE_ABSTRACT` (human-screened), `FULLTEXT` (human-screened), and `INCLUDED` (final corpus). Used as the `stage` parameter of `Corpus.records()` and `Corpus.keywords()` (BUILD_PLAN lines 895–896), and later the Stage 5 `screening_queue()` contract.

**Rationale:** `PrismaStage` is conceptually a Stage 4 artefact (the PRISMA engine is its sole producer), but Stage 3's frozen `Corpus` contract already needs it as a parameter type, and BUILD_PLAN §0 rule 1 forbids Stage 3 from importing the not-yet-built `prisma/` package. A standalone leaf module with zero dependencies lets both `prismabib.store` (Stage 3) and `prismabib.prisma` (Stage 4) depend on it downward without either depending on the other. See [data-model.md](data-model.md#prismastage-before-stage-4-exists) for full rationale.

**`src/prismabib/query.py`** (Stage 2, query builder)

The Scopus Boolean query builder (BUILD_PLAN line 775 names the contract `prismabib.query`). Renders a project's `project.toml` `[query]` table into the Boolean string the Scopus API expects. Implements the frozen acceptance test contract exactly: simple terms are OR-ed, compound terms (AND groups) are parenthesised, fields are applied to every term, and injection-style input (backslash and quote escaping) cannot break the query.

**Rationale:** BUILD_PLAN line 775 explicitly names the module as `prismabib.query`, a top-level module; §2.3's repository layout (lines 131–143) did not list it, so this addition reconciles the contract with the layout.

**`src/prismabib/countries.py`** (Stage 1, added during domain model)

A checked-in ISO 3166-1 alpha-3 country normalisation table with ~250 aliases (e.g., "South Korea" → "KOR", "Viet Nam" → "VNM").

**Rationale:** BUILD_PLAN §2.4 specifies a closed technology stack (no new dependencies). Scopus affiliation country strings are free-text and require normalisation for geographic analysis. Rather than adding a dependency or hand-coding the table, this module captures the mapping and keeps it out of the domain model (BUILD_PLAN §Stage 1 line 689: country is normalised at the Affiliation level). The module is also importable by Stage 3 analysis without depending on acquisition code.

## Technology choices

- **Python 3.11+** — `tomllib`, `ExceptionGroup`, `Self` typing
- **DuckDB** — Analytical SQL, Parquet native, embedded, zero-ops
- **Pydantic v2** — Validate at the boundary (Scopus fields are structurally inconsistent)
- **httpx + tenacity** — Async-capable HTTP with exponential backoff and jitter
- **polars + pandas** — Polars for heavy joins; pandas for interop with Panel/Plotly
- **Panel (HoloViz)** — Widgets run inline in notebooks *and* serve standalone (unlike Streamlit)
- **Plotly + Matplotlib** — Same dataframe feeds both; never re-derive numbers per backend
- **MkDocs Material** — Documentation with Material theme, `mkdocstrings` for API docs

See BUILD_PLAN.md §2.4 for the full technology stack and rationale.

## Next: Architecture Decision Records

Five core decisions (ADRs 0001–0005) are made in Stage 1:

1. **DuckDB as analytical store** — Why DuckDB over SQLite, PostgreSQL, or Snowflake
2. **Append-only decision log** — Why events, not mutable records
3. **Human-only screening** — Why no LLM, active learning, or relevance ranking
4. **Panel for in-notebook UI** — Why Panel instead of Streamlit or Jupyter widgets
5. **Rules-plus-override taxonomy** — Why versioned rule files plus event overrides

Plus ADR 0006 (Stage 0): **Public repository and single-owner review** — governance on a free-tier GitHub account.

See the [Architecture Decision Records](adr/) section for details.
