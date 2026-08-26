# ADR 0008: Multi-Reviewer Adjudication Is Conservative

## Status

Accepted — Stage 4, 2026-08-24. A methodological decision made by the implementation, not
specified by BUILD_PLAN; recorded here because it changes published numbers.

## Context

The decision log's fold key is `(stage, record_id, reviewer)` (ADR 0002, BUILD_PLAN §Stage 4
line 972). One reviewer's decisions never overwrite another's — that is the property that
lets a second reviewer join with no schema migration.

It also means that after folding, a single record at a single stage can carry several
different current decisions, one per reviewer. Set membership needs one. BUILD_PLAN's Stage 4
table specifies the fold and the set definitions but says nothing about how disagreement
between reviewers resolves; the nearest thing to guidance is line 973 (`unsure` never
resolves to inclusion) and the deferred note that agreement statistics arrive later.

Something had to be chosen, because `manual_abstract_set` and `manual_fulltext_set` cannot
return a set without it. The choice is not an implementation detail: it decides which records
appear in `included` in a published flow diagram.

## Decision

`engine._aggregate_record_decisions(fold, stage)` reduces every reviewer's *current* decision
for a record at one stage to a single aggregate, using the most conservative rule available
from the data alone:

1. **Any `exclude` wins.** If at least one reviewer's current decision is `"exclude"`, the
   aggregate is `"exclude"`, regardless of what anyone else said or when.
2. **Otherwise, any `unsure` wins.** If no reviewer excluded and at least one is `"unsure"`,
   the aggregate is `"unsure"` — the record stays in the queue rather than advancing
   (BUILD_PLAN line 973).
3. **Otherwise the aggregate is `"include"`** — reached only when every reviewer who has
   logged a decision at that stage said `"include"`.

A record with no logged decision at that stage is absent from the mapping entirely; callers
look it up against the shared `engine._PENDING` sentinel (`decision=None`), which is neither
included nor excluded and lands in ADR 0007's unresolved counts.

**Reason-code attribution.** When several reviewers excluded the same record for different
reasons, the reported `reason_code` is that of the exclude event with the greatest
`(ts, event_id)` — the most recently logged exclusion. This reuses the exact tie-break
`log.fold_events` already applies, so one ordering rule decides both "which of a reviewer's
decisions is current" and "which of several reviewers' reasons is reported". It is the
`reason_code` that appears as a key of `FlowCounts.excluded_fulltext`.

**The rule is a function of the set of decisions, not of their order.** Rules 1–3 test for
the presence of a value, so permuting the log, or the order in which reviewers screened,
cannot change the aggregate. Only the reason-code attribution is order-sensitive, and it uses
a total, deterministic order.

### Precisely what "unanimous" means here

Unanimity is over the reviewers who have **actually logged a decision** at that stage. A
reviewer who has not yet screened the record does not block it: if reviewer A logs `include`
and reviewer B never logs anything, the aggregate is `"include"` and the record advances.

This is worth stating plainly because it is the one place the rule is not conservative. The
engine cannot be conservative here — it has no roster of who is *supposed* to screen, so
"waiting for B" and "B is not a reviewer on this project" are indistinguishable to it, and
treating a single-reviewer project's every decision as unresolved would be wrong far more
often than right. **Enforcing that both reviewers actually screen every record is a screening
workflow concern (Stage 5), not an engine concern.** A double-screened review that reports
`included` before the second reviewer finished is reporting one reviewer's judgement, and
nothing in Layer 2 will say so.

## Alternatives rejected

### 1. Majority vote

With three or more reviewers, take the modal decision; with two, some tie-break.

Rejected on methodology, not on mechanics. A systematic review's inclusion criteria are meant
to be applied, not polled: two reviewers out-voting a third who identified a concrete
disqualifying feature does not make the paper eligible, it means the disagreement needs
adjudication by a human. Majority vote also silently *includes* records that a reviewer
doubted — the exact outcome this project cannot afford, since a wrongly included record
propagates into every downstream count, figure, and claim. And it degenerates on the common
case: with two reviewers, every disagreement is a tie, so the tie-break rule becomes the real
policy while looking like an edge case.

### 2. Last-write-wins across reviewers

Extend the fold's `(ts, event_id)` ordering across reviewers as well as within one, so the
most recently logged decision for a record wins outright.

Rejected: it makes membership depend on screening *scheduling*. Whether a paper is in the
corpus would turn on which reviewer happened to open their laptop last — a clock artefact, not
a judgement — and re-running the same two reviewers' identical decisions in the opposite order
would produce a different corpus. It also silently discards a recorded exclusion without
recording that it was overridden, which is exactly the kind of invisible mutation the
append-only log exists to prevent.

### 3. Raise on disagreement

Refuse to compute set membership while any record has conflicting current decisions.

Rejected: disagreement is the normal, expected state of a double-screened review in progress,
and it is not an error. `compute_flow_counts` would raise for most of a review's life, taking
down every progress report and dashboard with it. The conservative aggregate keeps the numbers
computable *and* keeps the disputed record out of the corpus, which is the same protection
without the outage.

## Consequences

### 1. A record is never silently included over a reviewer's objection

The rule's guarantee is one-directional and worth naming: nothing in Layer 2 can put a record
into `C` while any reviewer's current decision is `exclude` or `unsure`. False exclusions are
recoverable — a reviewer appends a reversal event and the record returns. A false inclusion is
not recoverable in the same way, because nobody is looking for it: it is already in the corpus,
in the counts, and in the figures.

### 2. Disagreement is visible in the counts, not hidden

An unresolved disagreement aggregates to `"unsure"` and therefore lands in
`unsure_title_abstract` or `unsure_fulltext` (ADR 0007), where a non-zero value tells a
reviewer that screening is not finished. It does not inflate the exclusion counts and it does
not quietly disappear.

### 3. Adjudication is by appending, not by editing

To resolve a disagreement, the dissenting reviewer appends a new event superseding their own
earlier decision; both events remain readable. There is no "adjudicator overrides reviewer"
event type, and no reviewer can edit another's decision. A project needing a formal third-party
adjudicator can log that person as an additional `reviewer` — but note that under rule 1 an
adjudicator cannot *overturn* another reviewer's standing exclusion; that reviewer must
append the reversal themselves. This is a deliberate limit on who can change whose record, and
a candidate for a future ADR if formal adjudication is ever required.

### 4. Stage 5 adds agreement statistics on top, and changes nothing here

Inter-reviewer agreement (Cohen's κ, and per-stage disagreement lists) is a **query over the
log**, computed from the same per-reviewer fold this rule consumes. It reports how often
reviewers agreed; it does not decide membership, and adding it will not change any number this
ADR governs. That separation is the point: the agreement statistic stays an honest measurement
precisely because it has no vote in the outcome it measures.

## Constraints

- **The rule is not configurable.** There is no `criteria.yaml` key selecting an adjudication
  policy. A per-project policy would make two reviews' `included` counts incomparable while
  looking identical in the diagram; changing the rule requires a new ADR superseding this one.
- **Reviewer identity is a free-text string.** The engine trusts `reviewer` as an opaque
  identifier and cannot detect one person logging under two names, or two people sharing one.
- **The aggregate is per stage.** A record excluded at `fulltext` is not retrospectively
  excluded at `title_abstract`; the two stages fold and aggregate independently.

## Related decisions

- **ADR 0002** (Append-Only Decision Log): the `(stage, record_id, reviewer)` fold key that
  makes multi-reviewer disagreement representable in the first place
- **ADR 0003** (Human-Only Screening): every decision being adjudicated here is a human's;
  §3 of that ADR defers agreement statistics to a later stage
- **ADR 0007** (`FlowCounts` Unresolved Fields): where an aggregated `"unsure"` is reported

## References

- BUILD_PLAN §Stage 4 line 972 (fold key and supersession)
- BUILD_PLAN §Stage 4 line 973 (`unsure` never resolves to inclusion)
- BUILD_PLAN §5 risk 4 (support a second reviewer and κ later at zero migration cost)
- `src/prismabib/prisma/engine.py` — `_aggregate_record_decisions`, `_RecordDecision`,
  `_PENDING`, `manual_abstract_set`, `manual_fulltext_set`
- [PRISMA Mapping](../../methodology/prisma-mapping.md) — where the aggregate feeds the flow
  counts

---

This is a methodological decision not present in BUILD_PLAN. Changing the adjudication rule
changes published numbers and requires a new ADR that supersedes this one (§2.6).
