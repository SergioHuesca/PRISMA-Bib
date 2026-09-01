# ADR 0016: Automated Exclusions Are Attributed to a Reason by Precedence

## Status

Accepted — 2026-09-01. **This is the third deviation from the frozen `FlowCounts` contract**
(BUILD_PLAN §Stage 4, lines 978–991), after
[ADR 0007](0007-flow-counts-unsure-fields.md) and
[ADR 0013](0013-identified-sums-across-searches.md). It adds one mapping field and one
equation to `FlowCounts.assert_consistent()`. Every field and every equation those two ADRs
and BUILD_PLAN declared is unchanged: this ADR partitions an existing number, it does not
alter one. No Layer 0, Layer 1 or Layer 2 change is involved — the breakdown is computed
from records the engine was already filtering.

## Context

`FlowCounts.excluded_automated` was a single integer: how many records the automated criteria
removed before human screening. The PRISMA diagram and `numbers.json` reported that one
figure under the label "excluded by year/subject/doc-type".

**PRISMA 2020 asks for exclusions to be reported with reasons.** A reviewer reading
"excluded by year/subject/doc-type: 754" cannot tell whether the subject-area restriction
did any work, and neither can the author. On this project's own corpus the answer turned out
to be that it did none — 673 of the 754 were the year window and 81 were the document type,
and the subject-area filter removed nothing at all. That is a materially different
methodological claim from the one the combined figure implies, and the combined figure cannot
distinguish them.

It is also the shape of failure BUILD_PLAN §1.4 names. A subject-area restriction declared in
`criteria.yaml`, reported in the manuscript as part of an automated screening step, that in
fact excluded zero records, is a plausible wrong number in a published paper — and until now
nothing in the pipeline could surface it, because the number that would have revealed it was
never computed.

### The counts have to sum, and naively they do not

A record can fail several criteria at once. A 2003 medical case report in an unlisted venue
fails the year window, the subject areas, the document type and the conference whitelist. If
each failing criterion counted it, the four reasons would sum to more than
`excluded_automated`, and the diagram would report an exclusion total that disagrees with its
own breakdown. On the reference fixture the naive sum is 26 for 10 excluded records.

A breakdown that does not sum to its total is worse than no breakdown: it invites the reader
to add the lines up, and the sum is wrong.

## Decision

**Each excluded record is attributed to exactly one reason: the first criterion it fails, in
a fixed order.**

```
year -> subject_area -> doc_type -> venue
```

`FlowCounts` gains `excluded_automated_by_reason: Mapping[str, int]`, and
`assert_consistent()` gains equation 5:

```
sum(excluded_automated_by_reason.values()) == excluded_automated
```

Under precedence, "excluded by subject area" means *passed the year test and failed the
subject-area test*. That is a narrower claim than "failed the subject-area test", and the
diagram's ordering is what makes it readable — which is why the order is part of the
contract and not an implementation detail.

### The order is stated once, in one table

The obvious implementation is a constant naming the order plus an `if`/`elif` chain applying
it. Those are two statements of the same fact, and they can diverge: reorder the constant
alone and the diagram reports reasons in an order that no longer means "first criterion
failed", while every count still sums correctly and every test stays green. `engine.py`
therefore holds a single `_AUTOMATED_PREDICATES` table of `(reason, predicate)` pairs; the
loop iterates it and `AUTOMATED_EXCLUSION_PRECEDENCE` is derived from it.

### Every reason key is always present

A reason that excluded nothing reports `0`; it does not vanish from the mapping. A key that
appeared only when non-zero would make "we did not filter on subject area" and "we filtered
on subject area and it excluded nothing" indistinguishable in the figure and in
`numbers.json` — and those are exactly the two claims this ADR exists to separate. The fixed
key set also keeps `numbers.json`'s golden key set stable across projects.

### No record changes sets

The precedence order is the order the predicates were already applied in, so `A` and `L` are
byte-for-byte what they were. Every existing golden value is unchanged; the goldens gained
four keys and altered none. This ADR files already-excluded records under a reason; it
excludes nothing new.

## Alternatives rejected

### 1. Count each failing criterion separately (membership, not precedence)

Report, for each criterion, how many records fail it — regardless of what else they fail.
This answers a genuine question ("how much would dropping the subject-area restriction change
the corpus?") more directly than precedence does, and it is order-independent, so there is no
ordering convention to explain or to get wrong.

*Rejected:* the four numbers then do not sum to `excluded_automated`, and a PRISMA flow
diagram is an arithmetic document — its boxes are read as a partition. Publishing four
numbers under an exclusion total they exceed is precisely the "plausible wrong number"
failure. The membership question is real but it is a sensitivity analysis, not a flow-diagram
box, and it can be answered separately without claiming to partition anything.

### 2. Report only the reasons that excluded something

Emit a key per reason with a non-zero count, as `excluded_fulltext` already does for reason
codes.

*Rejected:* `excluded_fulltext`'s codes are an open vocabulary the researcher defines, where
absence honestly means "no reviewer used this code". The automated reasons are a closed set
of four criteria that either ran or did not, and absence would conflate "not filtered" with
"filtered, excluded nothing" — the exact ambiguity that motivated this ADR. It would also
make `numbers.json`'s key set vary by project, breaking the golden key-set test's purpose.

### 3. Attribute a record to every reason it fails, and report the total separately

Keep `excluded_automated` as the authoritative total, and present the per-reason counts
explicitly labelled as overlapping rather than as a partition.

*Rejected:* it depends entirely on a caveat surviving into the published figure. Diagram
boxes get transcribed into manuscripts, slides and tables without their footnotes, and a
reader who adds the lines is not being careless — they are reading a flow diagram the way
flow diagrams are meant to be read. A structure that is only correct when accompanied by
prose is the wrong structure.

## Consequences

### 1. Published numbers gain detail; none of them change

`excluded_automated`, `after_automated` and every downstream count are unchanged on every
project. The diagram, `numbers.json` and `prismabib flow` gain four lines each.

### 2. The reference fixture's 24 automated exclusions are all `venue`

Derived independently, by a SQL pass over the reference store that re-implements precedence
rather than calling `engine.py`, and cross-checked against the pinned
`REFERENCE_EXCLUDED_AUTOMATED = 24` (§5 risk 11: never read a golden off the code it tests).
The two goldens gained exactly four keys apiece and no existing value was touched.

### 3. `docs/assets/prisma-flow-example.svg` was regenerated

The figure changed, so the checked-in example is a deliberate regeneration, not a test being
made to pass. Its numbers were re-read by hand afterwards: 120 identified, 24 excluded (all
`venue`), 96 after automated filters, 5 included.

### 4. The precedence order is now part of the contract

Changing it silently re-files records under different reasons without changing any total, so
`assert_consistent()` cannot detect it. The order is pinned by an integration test whose
fixture has *pairwise distinct* per-reason counts (1/2/3/4), so a permutation is a diff;
with one record per reason every permutation yields the same four numbers and the test that
exists to pin the order could not see it change. Both a permuted order and a
count-every-failure implementation were injected and confirmed to fail it.

## Constraints

- The per-reason counts must always sum to `excluded_automated` (equation 5). A reason added
  to the precedence table must be applied inside the same single-attribution loop.
- Every key in `AUTOMATED_EXCLUSION_PRECEDENCE` appears in the mapping on every project,
  including one where nothing was excluded at all.
- `_AUTOMATED_PREDICATES` stays the only statement of the order.
- A test pinning the order must use pairwise-distinct per-reason counts.

## Related decisions

- [ADR 0007](0007-flow-counts-unsure-fields.md) — the first `FlowCounts` deviation
- [ADR 0013](0013-identified-sums-across-searches.md) — the second, and equation 1
- [ADR 0011](0011-abstract-retrieval-for-subject-areas.md) — why `subject_area` can be a
  criterion that excludes nothing, and what makes it excluded nothing here
- [ADR 0015](0015-stage-order-and-stage-10-scope.md) — Stage 10's reporting scope

## References

- `src/prismabib/prisma/engine.py` — `_AUTOMATED_PREDICATES`,
  `AUTOMATED_EXCLUSION_PRECEDENCE`, `_compute_a_and_l`, `_Layer1View.excluded_by_reason`
- `src/prismabib/prisma/flow.py` — `FlowCounts.excluded_automated_by_reason`, equation 5
- `src/prismabib/report/numbers.py` — `flow.excluded_automated.<reason>` keys
- `src/prismabib/report/flow_diagram.py` — `_automated_reason_lines`
- `src/prismabib/cli.py` — `_print_flow`'s automated block
- `tests/integration/prisma/test_flow.py` —
  `test_flow_counts__record_failing_several_criteria__is_charged_to_the_first_one`
- `tests/integration/report/test_flow_diagram.py` — `REASON_TO_LABEL`, `DISTINCT_COUNTS`
- [PRISMA 2020 statement](https://doi.org/10.1136/bmj.n71) — reporting exclusions with
  reasons

---

This ADR records that automated exclusions are attributed to exactly one reason by
precedence, that the order is `year -> subject_area -> doc_type -> venue` and is stated once
in `_AUTOMATED_PREDICATES`, that every reason key is always present, and that the counts sum
to `excluded_automated` under equation 5. Counting each failing criterion separately, omitting
zero-valued reasons, changing the precedence order, or removing equation 5 requires a new ADR
that supersedes this one (§2.6).
