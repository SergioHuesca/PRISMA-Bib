# Amend Eligibility Criteria

How to change the inclusion/exclusion criteria after screening has begun.

## Delivered in Stage 5

This page will contain:

- **Criteria structure** — the YAML format of `projects/<slug>/criteria.yaml` defining inclusion and exclusion rules (Stage 5)
- **Editing criteria** — changing thresholds, adding language filters, modifying date ranges (Stage 5)
- **Rescreening** — re-running screening with amended criteria, with guidance on which records need review (Stage 5)
- **Replay semantics** — how the decision log is folded under different criteria versions (append a new criteria version, replay the fold) (Stage 5)
- **Audit trail** — how to see which criteria changes affected which records (git history of `criteria.yaml`) (Stage 5)
- **Common cases**:
  - "I want to exclude papers from 2020–2022" — update date range, which records change status
  - "I realized I should also exclude Book Chapters" — update doctype filter, replay
  - "Our subject area is broader than I thought" — add keyword term, decide which newly-eligible records to include (Stage 5)

Amending criteria does not require re-screening; it leverages the append-only decision log. The system tracks which records were screened under which criteria version and can report agreement/disagreement across versions.

See [Architecture Overview](../architecture/overview.md) for how criteria version is stored in the decision log, and [PRISMA Mapping](../methodology/prisma-mapping.md) for how flow counts are recomputed.
