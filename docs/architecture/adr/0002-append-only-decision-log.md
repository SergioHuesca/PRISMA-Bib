# ADR 0002: Append-Only Decision Log

## Status

Accepted — Stage 1, 2026-08-18.

## Context

Human screening decisions (verdicts on inclusion/exclusion at each stage) must be persisted as an audit trail. These decisions are later folded to compute set membership in the PRISMA flow diagram. The decision log is the contract between Layer 1 (normalised store) and the flow-counting engine (Stage 4 `prisma/`). Layer 1 is disposable; the decision log is not.

## Decision

Screening decisions are stored as append-only events in `projects/<slug>/decisions/decisions.jsonl`, one JSON object per line. Later events supersede earlier ones for the same `(stage, record_id, reviewer)` tuple (BUILD_PLAN §Stage 4, lines 970–972).

**Event schema (BUILD_PLAN Stage 4, lines 954–967):**

```json
{
  "event_id": "01HV7CZPN4B7G7T9KN9J1B9K1C",
  "schema_version": 1,
  "ts": "2026-01-18T14:22:07.412Z",
  "project": "vad-2026",
  "stage": "title_abstract",
  "record_id": "scopus:2-s2.0-85101234567",
  "reviewer": "kp",
  "decision": "exclude",
  "reason_code": "REVIEW_OR_SURVEY",
  "note": "",
  "criteria_version": "1.0.0"
}
```

**Field specifications:**
- `event_id`: ULID string, monotonically ordered (ties broken by timestamp)
- `schema_version`: INTEGER (not a version string); unknown versions fail loudly (Stage 4 line 1031)
- `stage`: one of `{title_abstract, fulltext}` (Stage 4 lines 943–947)
- `decision`: lowercase—`include`, `exclude`, or `unsure`. `unsure` never resolves to inclusion (Stage 4 line 973)
- `reviewer`: person code or ID
- `reason_code`: mandatory for `exclude`, must exist in `criteria.yaml` for that stage (Stage 4 line 974)

**Rules enforced by `log.py` (BUILD_PLAN Stage 4, lines 970–974):**

- Append-only. Opened `"a"`, fsynced per write. Checksum sidecar `decisions.jsonl.sha256` detects tampering (line 971).
- Later events for the same `(stage, record_id, reviewer)` supersede earlier ones. Fold uses `(ts, event_id)` order.
- Forward compatibility: unknown `schema_version` raises `LogError`.
- Mandatory reason codes validated against `criteria.yaml` for the stage.

## Consequences

### 1. Fold key is per-reviewer per-stage

Current set membership is derived with key `(stage, record_id, reviewer)`. This enables:

- **Per-stage separation**: a fulltext decision does not overwrite a title_abstract decision
- **Per-reviewer state**: each reviewer has independent history; one reviewer's change does not affect another's queue (Stage 5 line 1102: `test_queue__decided_by_other_reviewer__still_appears_for_this_reviewer`)
- **Second reviewer without migration**: adding a second reviewer appends events with different `reviewer` value; schema unchanged

### 2. Schema version is integer; unknown versions fail loudly

The event format can evolve. Unknown `schema_version` must raise `LogError` rather than silently ignoring (Stage 4 line 1031: `test_log__unknown_schema_version__raises_not_silently_ignored`).

### 3. Criteria amendments retain valid decisions

When criteria change (e.g., year range widens), `engine.replay(criteria_version=...)` recomputes membership under different criteria. **Existing valid human decisions are RETAINED** (Stage 4 line 1049: `test_replay__widened_year_range__reports_new_records_needing_screening`). Only genuinely new records enter the queue.

### 4. Tampering is detected via checksum sidecar

The append-only invariant is enforced by `decisions.jsonl.sha256`. Checksum mismatch on load raises `LogError` (Stage 4 line 971). This is testable (Stage 4 line 1026: `test_log__hand_edited_file__raises_log_error_on_load`).

### 5. Event ordering is by timestamp + tie-break

The fold uses `(ts, event_id)` order, not file line order. Permuting lines yields the same membership (Stage 4 line 1014: `test_sets__event_order_permuted_by_timestamp__yields_same_membership`).

### 6. Concurrent appends are safe (but tested)

Independent handles (one per reviewer, test/prod) may append to the same file. Line-atomicity is required to prevent interleaved writes (Stage 4 line 1032: `test_log__concurrent_appends_from_two_handles__no_interleaved_line`).

## Constraints

- **Not a replacement for Layer 1.** The log records only decisions, not the corpus. Layer 1 is reconstructed from Layer 0 independently (BUILD_PLAN §2.2 line 105).
- **Versioning is explicit.** `schema_version` changes only in major version bumps; it is rare.

## Related decisions

- **ADR 0001** (DuckDB as Analytical Store): Layer 1 never mutates; events are separate
- **ADR 0003** (Human-Only Screening): decisions made only by humans

## References

- BUILD_PLAN §Stage 4 (lines 935–1059): complete PRISMA engine and event-log spec
- BUILD_PLAN §Stage 4 lines 970–974 (rules enforced by log.py)
- BUILD_PLAN §Stage 4 lines 954–967 (event schema)
- BUILD_PLAN §Stage 4 line 972 (fold key and superseding)
- BUILD_PLAN §Stage 4 line 995 (criteria amendment)

---

This records BUILD_PLAN §2.2 lines 107–118, which is not open for renegotiation. Changing it requires a new ADR that supersedes this one (§2.6).
