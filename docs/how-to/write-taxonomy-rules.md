# Write Taxonomy Rules

How to define and update the taxonomy (method classification rules) for coding records.

## Delivered in Stage 8

This page will contain:

- **Taxonomy structure** — the YAML format of `projects/<slug>/taxonomy/rules/*.yaml` (Stage 8)
- **Rule format** — how to define matching patterns (keyword matching, regex, Boolean combinations) (Stage 8)
- **Hierarchy** — how taxonomy rules can be nested (e.g., methods → machine learning → deep learning) (Stage 8)
- **Versioning** — how taxonomy versions are tracked in the decision log (Stage 8)
- **Coding workflow** — running `notebooks/04_taxonomy.ipynb` to apply rules and override conflicts (Stage 8)
- **Overrides** — appending manual coding decisions to the decision log when rules disagree (Stage 8)
- **Audit trail** — reviewing which records got which codes and why (Stage 8)
- **Common patterns**:
  - "Add a new method category for X" — add a new rule file, test it on the corpus
  - "The regex is too broad; it's catching unrelated papers" — refine the pattern
  - "Thirty records are miscoded; should I rewrite the rule?" — create override events and track them separately (Stage 8)

Taxonomy rules are **data, not code**. They are versioned in git (tracked separately per §2.5) and the changelog documents when rules change. Current codes are derived by folding the rule versions and override events through the corpus.

See [Architecture Overview](../architecture/overview.md) for Layer 2 taxonomy structure, and ADR 0005 (when published) for the rules-plus-override design.
