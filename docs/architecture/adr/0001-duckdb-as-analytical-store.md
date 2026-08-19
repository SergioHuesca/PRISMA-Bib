# ADR 0001: DuckDB as Analytical Store

## Status

Accepted — Stage 1, 2026-08-18.

## Context

Layer 1 (the normalised store, BUILD_PLAN §2.2 line 105) must be fully reconstructible from Layer 0 (raw JSONL capture). The workload is analytical, not transactional:

- Group-by queries on affiliations (geography, institution-level metrics)
- Keyword explosions (term frequency, co-occurrence)
- Percentile and quantile queries on citations
- Statistical aggregations
- Exports to pandas/polars for external analysis

Two candidates were considered: SQLite and DuckDB.

### SQLite

**Disadvantages:**
- Row-oriented design; weak column-oriented optimisations
- Slow on large analytical group-bys
- No native Parquet I/O; CSV export requires serialisation and round-trip

### DuckDB

**Advantages:**
- Column-oriented, optimised for analytical workloads
- Native Parquet support (reads and writes natively)
- Direct pandas/polars interop (`.df()` or `.pl()` methods)
- Single-file embedded database, identical operational simplicity to SQLite
- No server required

## Decision

**DuckDB is the Layer 1 normalised store** (BUILD_PLAN §2.2 line 105).

The analytical workload makes column-orientation and Parquet support the deciding factors. Results hand directly to pandas/polars without serialisation friction. Staging code does not export to intermediate files; the same query object can feed both Plotly (interactive) and Matplotlib (static publication figures, BUILD_PLAN §Stage 9).

## Consequences

### 1. Layer 1 is a single `.duckdb` file

- At `<project>/store/corpus.duckdb`
- No database server, no connection strings
- File is backupable like any other data file
- Never committed to git; fully derived from Layer 0 (BUILD_PLAN §2.5 line 290)

### 2. Layer 1 is fully reconstructible

**Stage 3 requirement (BUILD_PLAN Stage 3 line 925):** "Running `build_store(project, rebuild=True)` on a fresh cache recovers the previous layer's content."

This is testable: given Layer 0, Layer 1 rebuild is deterministic.

### 3. Schema and views are SQL

- `src/prismabib/store/schema.sql` defines table structure
- `src/prismabib/store/views.sql` defines derived views (read-only aggregations over Layer 1 + Layer 2)
- Version-controlled; auditable

### 4. Exports do not cache results to disk

BUILD_PLAN §2.2 line 121 forbids caching analysis results to files (they could go stale). Layer 3 produces figures, tables, and captions as **functions** that take a Corpus handle and return the result object plus the dataframe that produced it, so every figure ships with its own provenance.

**Exception:** Aggregate tables and figures are released as GitHub Release assets (§2.5 line 293), not stored in the working tree. This keeps exports intentional and tagged.

### 5. Tests use in-memory DuckDB

```python
import duckdb

# Create temporary in-memory database
db = duckdb.connect(":memory:")

# Load schema and test data
db.sql("CREATE TABLE records (...)")
db.sql("INSERT INTO records VALUES (...)")

# Analytical query
result = db.sql("SELECT year, COUNT(*) FROM records GROUP BY year").df()
```

In-memory databases provide trivial test isolation: no file I/O, no cleanup.

## Constraints

- **OLTP (transactional) workloads are not a good fit.** Screening decisions and taxonomy overrides are appended to JSONL logs (Layer 2), not upserted into DuckDB (ADR 0002).
- **Rebuild is single-threaded.** Layer 1 rebuild from Layer 0 is human-triggered, not continuous. Serialisation prevents corruption from concurrent writer races.

## Related decisions

- **ADR 0002** (Append-Only Decision Log): Layer 2 events are separate from Layer 1, never mutations
- BUILD_PLAN §2.2 (Layer invariants)

## References

- [DuckDB Documentation](https://duckdb.org/)
- [DuckDB Python API](https://duckdb.org/docs/api/python/overview.html)
- BUILD_PLAN §2.2 lines 104–105 (Layer 1 invariants and DuckDB rationale)
- BUILD_PLAN §2.5 line 290 (Layer 1 is not versioned)
- BUILD_PLAN §Stage 3 line 925 (Layer 1 reconstructibility requirement)

---

This documents BUILD_PLAN §2.2 lines 104–105, which is not open for renegotiation. Changing it requires a new ADR that supersedes this one (§2.6).
