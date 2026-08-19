# ADR 0005: Rules-Plus-Override Taxonomy

## Status

Accepted — Stage 1, 2026-08-18.

## Context

Keywords attached to records must be taxonomically coded (categorised into research dimensions and categories). Dimensions are declared per project; categories are values within each dimension.

An example (BUILD_PLAN §Stage 8, lines 1254–1266):

```yaml
dimensions:
  - id: learning_paradigm
    multi_label: false
    categories: [unsupervised, weakly_supervised, self_supervised, semi_supervised, fully_supervised, foundation_zero_shot]
  - id: architecture
    multi_label: true
    categories: [cnn, autoencoder, gan, rnn_lstm, transformer, gnn, diffusion, vlm_foundation]
```

Assignments must declare the **unit of count** (BUILD_PLAN §Stage 8, lines 1269–1278):

```python
class CountingUnit(StrEnum):
    PAPERS = "papers"  # each record contributes at most 1 per category
    ASSIGNMENTS = "assignments"  # multi_label sums may exceed N
    KEYWORD_MENTIONS = "mentions"  # raw term frequency; NOT a paper distribution
```

This is mandatory. The source manuscript's taxonomy figure sums to substantially more than the corpus size because it counts keyword mentions without declaring it, creating the exact failure mode (§1.4, line 72) this architecture exists to prevent.

## Decision

**Taxonomy uses versioned rules (data, not code) plus human override events** (BUILD_PLAN §1.2 line 58).

**Rules (versioned YAML files, `projects/<slug>/taxonomy/rules/<dimension>.yaml`):**

Rules use a regex DSL (BUILD_PLAN §Stage 8, lines 1281–1295):

```yaml
version: 1.3.0
dimension: architecture
counting_unit: papers
categories:
  - id: transformer
    any:
      - {field: author_keywords, pattern: '\b(transformer|vision transformer|vit|self[- ]attention)\b'}
      - {field: title,           pattern: '\btransformer\b'}
      - {field: abstract,        pattern: '\b(transformer|multi[- ]head attention)\b'}
    none:
      - {field: abstract, pattern: '\btransformer (?:oil|winding|substation)\b'}
    confidence: 0.9
```

**Field specifications:**
- `version`: semantic version (mandatory)
- `dimension`: which dimension these rules apply to (must exist in the project schema)
- `counting_unit`: one of `{papers, assignments, mentions}` (mandatory, per §Stage 8 line 1285)
- `categories`: list of category definitions, each with `any` clauses (OR), `none` clauses (negative lookaside), and `confidence` score
- Regex must be valid; malformed regex fails at rule load time, not at match time (§Stage 8 line 1307: `test_rules__malformed_regex__raises_at_load_not_at_match`)

**Overrides (append-only JSONL, `projects/<slug>/taxonomy/taxonomy_overrides.jsonl`):**

Separate from the decision log. A human assignment always beats a rule assignment (Stage 8 line 1299):

```json
{
  "record_id": "scopus:2-s2.0-85101234567",
  "dimension": "architecture",
  "category": "transformer",
  "reviewer": "alice",
  "reason": "Explicitly discusses transformer models",
  "ts": "2026-01-18T14:22:07.412Z"
}
```

**Review queue (Stage 8 lines 1301–1304):**

After running the coder, Stage 8's review queue surfaces records in priority order:

1. **Uncoded**: no rule fired for a required dimension
2. **Conflicting**: multiple categories fired in a `multi_label: false` dimension
3. **Audit sample**: random 10% of confidently rule-coded records (seeded, reproducible)

The audit sample is **the only way to estimate rule precision** (Stage 8 line 1304: "Report the audit agreement rate in the output").

## Consequences

### 1. Rule output is derived, not logged

The coder emits `taxonomy_assignments(record_id, dimension, category, rule_id, rule_version, confidence, source)` where `source ∈ {rule, human}` (Stage 8 line 1297). These assignments are derived from the rule file run over the corpus. They are not appended to a log (that would create two sources of truth, violating §2.2 line 105).

### 2. Overrides are append-only events, separate from the decision log

Overrides go into `taxonomy_overrides.jsonl`, not `decisions.jsonl` (Stage 8 line 1299). This keeps the decision log (which has structure `decision ∈ {include, exclude, unsure}` per Stage 4 line 963) uncontaminated by taxonomy metadata.

### 3. Human assignment always wins

Fold precedence is: `source` (human > rule), then recency within source. This is what makes re-coding cheap: bump the rule file version, re-run the coder, and human overrides survive untouched (Stage 8 line 1309: `test_override__survives_rule_version_bump_and_recode`).

### 4. Counting unit is mandatory and enforced

Every rule file declares `counting_unit`. When `multi_label: false` and `counting_unit: papers`, Stage 8 asserts that assignments sum to exactly `|C|` (the corpus size); this assertion fails loudly on deliberately over-assigned fixtures (Stage 8 line 1311: `test_counting_unit__over_assigned_fixture__assertion_fails_loudly`). This is the test that would have caught the source manuscript's taxonomy-figure inconsistency.

### 5. Audit sample is seeded and reproducible

The audit sample is a deterministic 10% of records, seeded from the project to make it reproducible (Stage 8 line 1334: `test_review_queue__audit_sample__is_10pct_and_reproducible_from_seed`). This allows the protocol to record "in project X, the audit sample agreed with 92% of rule assignments" as an auditable fact.

### 6. Coding is deterministic and idempotent

Given the same rule file and corpus, running the coder produces the same assignments (Stage 8 line 1321: `test_coder__same_rules_and_corpus__is_deterministic`). Re-running produces no duplicates (Stage 8 line 1322: `test_coder__run_twice__is_idempotent`).

## Constraints

- **Rules are configuration, not code.** Changes to rules are methodology amendments (tracked in git as part of the `projects-config/` methodology audit trail per §2.5 line 287).
- **Regex matching is required.** The DSL uses regex, not substring matching, to support negative lookaside and word boundaries (e.g., `\btransformer\b` does not match "transformers").
- **No bulk overrides in v1.0.** Appending one override per record is verbose; Stage 8 deferred work (§8) may add SQL-like bulk overrides (e.g., "for all records where...").

## Design principles for Stage 8 implementation

- Rules are data: test with `(keywords, expected_categories)` tuples, not hand-written assertions on regex
- Overrides are auditable: each override records reviewer, timestamp, and reason
- Audit sample is the ground truth for rule precision: do not add unit tests asserting "this regex matches X" (that tests the regex library, not the system)
- Counting units are explicit in every figure caption (Stage 8 line 1278: "must state its unit in the auto-generated caption")

## Related decisions

- **ADR 0002** (Append-Only Decision Log): taxonomy overrides are separate events
- BUILD_PLAN §1.2 line 58 (Taxonomy: versioned rules + override)
- BUILD_PLAN §Stage 8 (lines 1247–1340: complete taxonomy engine spec, DSL, review queue, tests)

## References

- BUILD_PLAN §1.2 line 58 (Taxonomy decision)
- BUILD_PLAN §Stage 8 lines 1247–1340 (complete taxonomy spec)
- BUILD_PLAN §Stage 8 lines 1251–1278 (dimension schema and counting unit)
- BUILD_PLAN §Stage 8 lines 1281–1295 (rule DSL with regex)
- BUILD_PLAN §Stage 8 lines 1297–1299 (coder and override separation)
- BUILD_PLAN §Stage 8 lines 1301–1304 (review queue and audit sample)
- BUILD_PLAN §8 line 1554 (deferred work is a closed list; do not extend it)

---

This records BUILD_PLAN §1.2 line 58, which is not open for renegotiation. Changing it requires a new ADR that supersedes this one (§2.6).
