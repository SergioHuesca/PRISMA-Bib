# ADR 0007: FlowCounts Records Unresolved Screening Decisions

## Status

Accepted — Stage 4, 2026-08-24. Deviation from the frozen `FlowCounts` contract, explicitly
approved by the project owner before implementation (§2.6 requires an ADR for any deviation
from a frozen contract).

## Context

BUILD_PLAN specifies two things that cannot both be satisfied by the dataclass as frozen.

**First**, a screening decision has three possible values (§Stage 4, line 973):

> `decision ∈ {include, exclude, unsure}`. `unsure` never resolves to inclusion; it keeps
> the record in the queue and **is reported separately**.

**Second**, the frozen `FlowCounts` shape (§Stage 4, lines 978–991) has nowhere to report it
into:

```python
@dataclass(frozen=True)
class FlowCounts:
    identified: int
    excluded_automated: int
    after_automated: int
    excluded_language: int
    after_language: int
    excluded_title_abstract: int
    retrieved_fulltext: int
    excluded_fulltext: dict[str, int]
    included: int
    def assert_consistent(self) -> None: ...
```

At the title/abstract stage, this partitions `after_language` into exactly two buckets:
excluded, and advanced to full text. A record that a reviewer marked `unsure` is in neither.
Nor is a record nobody has looked at yet — which is the state of most of the corpus for most
of a review's life. The same hole exists one stage further along, between
`retrieved_fulltext`, `excluded_fulltext`, and `included`.

The consequence is not cosmetic. `assert_consistent()` exists to prove the arithmetic
closes; BUILD_PLAN calls it "the guard that prevents the class of error the source manuscript
exhibits" (line 993). A partition with an unaccounted-for third bucket cannot close, so the
guard would either be silently wrong or would fire on every project that is not finished —
and a check that fires constantly is a check people stop reading.

## Decision

`FlowCounts` gains two integer fields, and `assert_consistent()`'s equations account for
them:

```python
    excluded_title_abstract: int
    unsure_title_abstract: int      # added
    retrieved_fulltext: int
    excluded_fulltext: dict[str, int]
    unsure_fulltext: int            # added
    included: int
```

```
identified         - excluded_automated == after_automated
after_automated    - excluded_language  == after_language
after_language     == excluded_title_abstract + unsure_title_abstract + retrieved_fulltext
retrieved_fulltext == sum(excluded_fulltext.values()) + unsure_fulltext + included
```

Every other field, and equations 1 and 2, are exactly as BUILD_PLAN froze them.

**What the two fields count.** Each is the remainder of its partition, computed in
`flow.compute_flow_counts` as `after_language - excluded_title_abstract -
retrieved_fulltext` and `retrieved_fulltext - sum(excluded_fulltext.values()) - included`.
That remainder contains two populations that are indistinguishable in a flow diagram and
identical in their effect on it:

- a record whose aggregated decision at that stage is `"unsure"`, and
- a record with **no decision logged at that stage yet** — screening is not finished.

Both mean "screened-eligible, not yet resolved". Neither can reach the corpus:
`manual_abstract_set` and `manual_fulltext_set` admit only an aggregated `"include"`, so
BUILD_PLAN line 973's "`unsure` never resolves to inclusion" is preserved unchanged.

**A finished review has both fields at zero.** That is the check a reviewer should run
before publishing: non-zero `unsure_title_abstract` or `unsure_fulltext` in a diagram means
the screening it depicts was still in progress.

## Alternatives rejected

### 1. Fold `unsure` into the exclusion counts

Add unresolved records to `excluded_title_abstract` and `excluded_fulltext`. The equations
close with no schema change, and BUILD_PLAN's dataclass stays frozen.

Rejected: it publishes a false number. A diagram claiming "412 records excluded at
title/abstract" when 60 of them were flagged for a second look, or never looked at, overstates
the exclusions by 60 and understates the review's incompleteness to zero. It also destroys
the distinction the log was built to preserve — every excluded record is supposed to carry a
`reason_code` validated against `criteria.yaml`, and an `unsure` record has none, so the
folded reasons would have to be invented or bucketed under `UNKNOWN`. This is precisely the
"plausible wrong number in a published paper" failure mode the whole architecture exists to
prevent, arrived at through a schema shortcut.

### 2. Drop unresolved records from the counts

Count only decided records: subtract the unresolved ones from `after_language` and
`retrieved_fulltext` before partitioning.

Rejected: it breaks the equations upward rather than sideways. `after_language` is `|L|` by
definition, and equation 2 (`after_automated - excluded_language == after_language`) is
frozen — quietly redefining `after_language` as "records decided so far" makes equation 2
fail for every in-progress project, and makes "records screened" in the published diagram
mean something other than what PRISMA means by it. The information about how large the
screened set actually was would be gone from the output entirely.

### 3. Report unresolved counts somewhere other than `FlowCounts`

Keep the dataclass frozen and return the unresolved counts from a separate function or
report object.

Rejected: `assert_consistent()` still could not close, because the numbers it checks would
live in two objects that nothing forces to be computed from the same fold. The guard's whole
value is that it is a single self-contained arithmetic proof over one immutable snapshot.
Splitting the operands across two objects converts an invariant into a convention.

## Consequences

### 1. `FlowCounts` is no longer BUILD_PLAN's literal shape

Any code, test, or fixture that compares a whole `FlowCounts` instance must include the two
new fields; golden snapshots of the dataclass carry them. This ADR is the record that the
difference is deliberate, dated, and approved — not drift.

### 2. `assert_consistent()` becomes a closed accounting identity

Equations 3 and 4 now hold by construction for anything `compute_flow_counts` returns,
because the two new fields are computed as partition remainders. They remain load-bearing for
a `FlowCounts` assembled by hand, mutated, deserialised, or produced by a future alternative
constructor — which is exactly the population that mutation testing attacks.

### 3. Renderers must decide what to show

A Layer 3 flow-diagram renderer (Stage 9) has three honest options for a non-zero unresolved
count: draw it as its own box, refuse to render, or render with a visible "screening in
progress" marker. Silently omitting it is not one of them, since the diagram would then not
add up. This ADR does not choose between the three; it requires that the renderer choose
deliberately.

### 4. The two populations inside the field are not distinguished

`unsure_title_abstract` does not separate "a reviewer said `unsure`" from "nobody has
screened this yet". Both are unresolved and both are non-includable, so the flow diagram
treats them identically. A caller that needs the split — a screening-progress display, for
instance — must query the fold directly rather than read `FlowCounts`; Stage 5's queue does
exactly that.

## Constraints

- **The three-value decision enum is unchanged.** This ADR adds reporting, not a fourth
  decision value.
- **`unsure` still never resolves to inclusion.** Nothing here weakens BUILD_PLAN line 973;
  the corpus `C` is untouched by this change.
- **Equations 1 and 2 are unchanged.** The deviation is confined to the title/abstract and
  full-text partitions.

## Related decisions

- **ADR 0002** (Append-Only Decision Log): the event schema whose `unsure` value this ADR
  gives a home in the counts
- **ADR 0008** (Multi-Reviewer Adjudication): how several reviewers' decisions on one record
  become the single aggregated decision these counts partition on — including the rule that
  an unresolved disagreement aggregates to `unsure`

## References

- BUILD_PLAN §Stage 4 line 973 (`unsure` "is reported separately")
- BUILD_PLAN §Stage 4 lines 976–993 (the frozen `FlowCounts` and `assert_consistent()`)
- BUILD_PLAN §2.6 (a deviation from a frozen contract requires an ADR)
- `src/prismabib/prisma/flow.py` — `FlowCounts`, `FlowCounts.assert_consistent`,
  `compute_flow_counts`
- [PRISMA Mapping](../../methodology/prisma-mapping.md) — the box-by-box audit table

---

This ADR deviates from BUILD_PLAN §Stage 4 lines 978–991, with the owner's explicit
approval. Reverting or further amending the `FlowCounts` contract requires a new ADR that
supersedes this one (§2.6).
