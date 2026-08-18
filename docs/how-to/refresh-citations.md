# Refresh Citations

How to update citation counts and re-compute citation-based metrics when new data arrives.

## Delivered in Stage 10

This page will contain:

- **Citation acquisition** — fetching updated citation counts from Scopus (requires Scopus COMPLETE view entitlement) (Stage 10)
- **Incremental refresh** — updating citation data without re-screening records (append new Layer 0, merge into Layer 1) (Stage 10)
- **Citation metrics** — which bibliometric metrics depend on citation data (citation percentiles, h-index, citations per year) and what changes when citations are refreshed (Stage 10)
- **Figures and tables that change** — which Layer 3 outputs are regenerated after citation refresh (Stage 10)
- **Versioning and traceability** — how updated figures are tagged with the new Layer 0 manifest hash (Stage 10)
- **Workflow** — running `notebooks/03_bibliometrics.ipynb` and `notebooks/06_export_report.ipynb` with refreshed data (Stage 10)

Citation refresh is designed to be **deterministic and traceable**: the same input (corpus records + citation counts from manifest hash M1) always produces identical output. If you refresh citations three months later (now at manifest hash M2), the output includes both M1 and M2 in its provenance metadata.

See [Provenance](../architecture/provenance.md) for how to trace figures back to Layer 0 manifests, and [Metric Definitions](../methodology/metric-definitions.md) for how citations are counted.
