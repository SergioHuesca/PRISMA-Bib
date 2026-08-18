# Provenance

This page explains how any number in prismabib output traces back to a versioned source.

## Delivered in Stage 2

This page will contain:

- **Manifest structure** — the §2.3 Layer 0 manifest format recording query, endpoint, timestamp, result count, and SHA-256 of payloads (Stage 2)
- **Hash verification** — how to verify that a recorded Layer 0 archive matches its manifest (proving the data has not been altered since capture) (Stage 2)
- **Traceability from figure to record** — given a datapoint in a Plotly figure or table, how to trace it back through Layer 3 → Layer 2 → Layer 1 → Layer 0 (Stage 2)
- **Audit trail examples** — worked examples: "This count of 1,771 records traces to Scopus manifest SHA `abc123` on 2026-01-15" (Stage 2)
- **Reproducibility** — re-running the query exactly reproduces the byte-identical DuckDB and Layer 3 outputs (determinism proof) (Stage 2)

The system is built with provenance as a first-class concern—every Layer 3 output includes metadata recording which Layer 0 manifest(s) and Layer 2 events were used to produce it. This metadata is embedded in exported files (`manifest.json` in exported bundles) and referenced in [Methodology — Validation](../methodology/) for external audit.

See [Architecture Overview](overview.md) for the conceptual model, and [BUILD_PLAN.md](https://github.com/SergioHuesca/PRISMA-Bib/blob/main/BUILD_PLAN.md) §3.2 for Layer 0 specification.
