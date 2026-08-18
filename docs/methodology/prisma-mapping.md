# PRISMA Mapping

This page documents how the system's layer structure maps to PRISMA 2020 checklist items and reporting standards.

## Delivered in Stage 4

This page will contain:

- **PRISMA 2020 checklist** — each item (P1–P27) mapped to the prismabib layer and function that produces it (Stage 4)
- **Set expressions** — formal set notation for each stage of filtering (Identification, Eligibility Phase 1, Eligibility Phase 2, final Included set) (Stage 4)
- **Flow diagram** — the PRISMA flow diagram is a pure function of the decision log, generated automatically at Layer 3 (Stage 4)
- **Reporting template** — a Markdown template for manuscripts using prismabib, showing how to cite layer versions and manifest hashes (Stage 4)

The key property: every count in PRISMA output (number of records identified, number screened, number excluded, number included) is derived from the decision log by applying Boolean set operations. No count is typed or copied.

See [Architecture Overview](../architecture/overview.md) for the layer structure, and BUILD_PLAN.md §1 for the problem this addresses.
