# ADR 0003: Human-Only Screening

## Status

Accepted — Stage 1, 2026-08-18.

## Context

Systematic reviews screen documents (title, abstract, full text) to determine inclusion or exclusion against predefined criteria. Alternative screening models include fully automated (ML/LLM predicts verdicts) and hybrid (model assists, human approves).

The central architectural commitment (BUILD_PLAN §1.4, lines 67–72) is: **every number that appears in any output is a query against a versioned store, never a literal.** This extends to screening: every decision that determines set membership must be traceable to a human reviewer and a criteria version.

## Decision

**Screening is human-only** (BUILD_PLAN §1.2 line 55).

No LLM, no active learning, no relevance ranking. Every inclusion/exclusion verdict is made by a human reviewer. Screening-stage specifics (BUILD_PLAN §Stage 4, lines 943–947):

- Only records in `S_raw ∩ A ∩ L` (after automated filtering on year, subject, language) reach manual screening
- Two stages: `title_abstract` and `fulltext`
- Decisions are recorded with `decision ∈ {include, exclude, unsure}` (lowercase)

## Consequences

### 1. Screening order must be stable and deterministic

Records are presented in an order seeded from the project slug (BUILD_PLAN §Stage 5, line 1069: `deterministic order seeded from the project slug`). This prevents:

- **Recency order-effects** (newer papers dominating early decisions)
- **Model-confidence ranking** (which would reintroduce relevance-bias into a human-only protocol)
- **Accidental bias** from presentation order

Consequence: Stage 5 test `test_queue__different_slug__ordering_differs` verifies the seed. Chronological ordering violates the unbiased-screening requirement.

### 2. UI ergonomics are load-bearing

Slow, cumbersome screening UIs become a project blocker. Stage 5 specifies ≥4 records/minute throughput (line 1092) measured on the reference fixture. This is the planning basis for human labour cost.

### 3. Deferred work: multi-reviewer agreement

Second-reviewer support and interrater agreement (Cohen's κ, Krippendorff's α) are deferred. BUILD_PLAN §8 (deferred work, line 1558): "Second reviewer with Cohen's κ / Krippendorff's α (schema already supports it — the `reviewer` field exists and the fold is per-reviewer; only the UI and reporting are missing)."

### 4. Future LLM-assisted pre-screening must preserve the human-decision schema

BUILD_PLAN §8 (line 1561) states:

> LLM-assisted pre-screening — deliberately excluded by ADR 0003. Any future adoption must preserve the human-decision event schema and record model provenance in a separate `source` field.

If post-v1.0 versions add LLM suggestions:

- Event schema must remain unchanged; only overrides change `source` field
- Human verdicts must never be determined by model confidence (order must remain seeded, not ranked)
- Audit trail must distinguish human from LLM (for reproducibility)

## Constraints

- **All screened records are human-read.** Filtering to "easy cases" for model pre-screening is not allowed; it introduces model-confidence bias into the corpus.
- **Labour cost is accepted.** Human screening is slower than ML-assisted screening. This is the trade-off for auditability.
- **Screening order is not human-chosen.** If a reviewer were to prioritise records by interest or relevance, it would reintroduce bias. The seed ensures order is fixed and reproducible.

## Related decisions

- **ADR 0002** (Append-Only Decision Log): decisions are events, not mutations
- **ADR 0004** (Panel for In-Notebook UI): UI must be optimised for screening speed and keyboard-first workflow
- BUILD_PLAN §1.2 (line 55: Screening automation decision)
- BUILD_PLAN §1.4 (lines 67–72: why this architecture: numbers must query, never be typed)

## References

- BUILD_PLAN §1.2 line 55 (Screening automation decision)
- BUILD_PLAN §1.4 lines 67–72 (the problem this architecture exists to solve)
- BUILD_PLAN §Stage 4 lines 943–947 (which records reach manual screening)
- BUILD_PLAN §Stage 5 line 1069 (stable, deterministic order seeded from project slug)
- BUILD_PLAN §8 line 1558 (deferred: multi-reviewer with agreement statistics)
- BUILD_PLAN §8 line 1561 (LLM-assisted pre-screening exclusion and future adoption rules)

---

This records BUILD_PLAN §1.2 line 55, which is not open for renegotiation. Changing it requires a new ADR that supersedes this one (§2.6).
