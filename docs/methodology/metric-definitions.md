# Metric Definitions

This page documents every formula, counting convention, and default parameter used in bibliometric analysis.

## Delivered in Stage 7

This page will contain:

- **Growth metrics** — absolute and CAGR (compound annual growth rate), with exact formulas and edge-case handling (Stage 7)
- **Citation metrics** — percentiles, h-index, m-index, with definitions of counting conventions (self-citations included/excluded, truncation at citing set) (Stage 7)
- **Geographic metrics** — publication count and citation strength by country/region, with mapping from author affiliation to ISO-3166 country codes (Stage 7)
- **Venue metrics** — top venues by publication count and citation strength, with definitions of "venue" (journal ISSN vs. conference abbreviation) (Stage 7)
- **Keyword metrics** — co-occurrence networks and frequency tables, with definitions of de-duplication and thresholding (Stage 7)
- **Network metrics** — co-authorship clustering, with definitions of edge weight (number of shared publications) and community detection (Stage 7)
- **Defaults** — all parameters (e.g., citation percentile thresholds, keyword frequency floor) and how to override them (Stage 7)

Every formula is accompanied by:
- The exact SQL/Python code that implements it
- A worked example on reference data
- Edge cases and how they are handled (records with zero keywords, authors with no affiliation, etc.)
- The docstring documenting the counting convention (§6 docstring standard)

See [Testing](../testing.md) for how metrics are validated (property tests on invariants, golden snapshots on tables, byte-identical reproducibility).
