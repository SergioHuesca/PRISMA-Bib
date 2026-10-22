# ADR 0033: FlowCounts Gains a Registered Count, and Orphaned Decisions Are Reported

## Status

Proposed — 2026-10-22.

This record exists only on the branch `docs/adr-0032-0033-drafts`. The governance test
(`tests/unit/test_docs_governance.py`, `test_adr__on_main__is_not_still_proposed`) fails any ADR whose
Status reads `Proposed` on `main`, so this line must not reach `main` as written. The owner accepts
this record on merge by changing the line to `Accepted — <date>` in the pull request that enacts it,
after the open questions at the end are decided.

It depends on [ADR 0032](0032-registered-records-table-and-classification-map.md), which must be accepted
first, because the registered records it counts come from the table and view that ADR 0032 specifies.

Each change is a deviation from the frozen BUILD_PLAN `FlowCounts` (Stage 4, lines 976-993), and it is
a further amendment of the contract that [ADR 0007](0007-flow-counts-unsure-fields.md),
[ADR 0013](0013-identified-sums-across-searches.md) and [ADR 0016](0016-automated-exclusion-reasons.md) already
changed. It needs this record under §2.6 (BUILD_PLAN line 301).

## Context

[ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md) lets a reviewer register a resolved
candidate as a record. It fixes four things that this record must implement, and it says so in its §3
("the identity that ADR 0033 must implement"):

1. `identified` is unchanged. It is the sum of `total_results` over distinct searches (ADR 0013), read from
   `runs`, and it is never a row count.
2. `registered` is the number of active `register` events in `registrations.jsonl`. A registration is active
   when no `reverse` or `supersede` event names it.
3. Registered records are members of `S_raw`, and a registered record that fails the automated set is counted
   in `excluded_automated` and under its reason in `excluded_automated_by_reason`.
4. A decision that names a `record_id` outside `S_raw` is an **orphaned decision**. It is reported, it is not
   deleted, and it is not an arithmetic term.

**The current code has no such term.** `prisma/flow.py` defines `FlowCounts` at lines 56-119. Equation 1 is
checked in `_assert_flow_counts_consistent` (lines 160-161) as `identified - duplicates_across_searches -
removed_other_reasons - excluded_automated == after_automated`. `compute_flow_counts` (lines 438-544) reads
one snapshot. It reads `decisions.jsonl` only for records already in the sets it counts, so a decision for an
id outside those sets is never counted, and it is never reported either. Nothing in the flow output shows that
it exists.

**The published numbers must stay tied to the arithmetic.** ADR 0007 rejected folding unresolved records into
the exclusion counts, because that publishes a false number (its Alternatives 1 to 3). ADR 0029 has the same
concern for registrations: a count that is not in the identity cannot be audited against it.

**A reversal or supersession can move a published number.** A paper that a reviewer had included can leave
the corpus when its registration is reversed, and it can re-enter the queue when a Scopus match supersedes it.
Those are real changes to `included` and `unsure_title_abstract`, and the diagram has to show them.

## Decision

### 1. The `FlowCounts` field and the identity

`FlowCounts` gains one integer field, `registered`, placed after `removed_other_reasons`. Every other field
keeps its name, its meaning, and its place in the dataclass.

```python
identified: int
duplicates_across_searches: int
removed_other_reasons: int
registered: int                       # added: active registrations (ADR 0029 §3)
excluded_automated: int
```

Equation 1 is amended. It is the only equation whose form changes:

```text
identified + registered - duplicates_across_searches - removed_other_reasons - excluded_automated == after_automated
```

Equations 2 to 5 are unchanged, as ADR 0007 and ADR 0016 left them. The added `registered` term is the only
change to equation 1's form, and it is the same identity ADR 0029 §3 states.

**Worked check, with the ADR 0029 example.** `identified = 1,294`, one active registration, no duplicates, no
other removals, no automated exclusion: `1,294 + 1 - 0 - 0 - 0 = 1,295 = after_automated`. The identity closes.

**`assert_consistent` gains one precondition.** `registered` must be non-negative. It is checked with the other
counts before any equation, as ADR 0007 requires for every count (`_assert_flow_counts_consistent`).

**What `registered` counts, and where it comes from.** It is the number of active registrations in the same
snapshot as every other count. `compute_flow_counts` takes `registrations.jsonl` and its sidecar inside
`_capture_snapshot`, under the same connection, so it cannot be read at a different moment from `S_raw`. A
registration that is active when the snapshot is taken is in `records_all` (ADR 0032 Decision 1), so it is
also in `S_raw`. The same snapshot therefore supports both counts. A test asserts that the number of registered
ids in `S_raw` equals `registered`.

### 2. What is not an arithmetic term

Three counts are reported with the diagram but are not `FlowCounts` fields:

- **Resolved, unregistered candidates.** ADR 0029 §3 requires this count beside `registered`. It is computed
  from the candidate log, not from the record set, so it is not an operand of any identity. ADR 0007
  Alternative 3 rejected splitting the operands of an identity across two objects. This count is not an operand,
  so it is not that case.
- **Orphaned decisions.** Decision 3 defines them and their count. They are not operands either (ADR 0029 §3).
- **`numbers.json` keys.** `identified`, `registered`, and the resolved-unregistered count are separate keys,
  and no export adds `registered` into a key named `identified` (ADR 0029 §3). Every function named `identified`
  returns the Scopus value alone.

### 3. Orphaned decisions: reported, not deleted, not re-attached

**Definition.** An orphaned decision is a **current** decision, that is, the latest event for its
`(stage, record_id, reviewer)` key in `decisions.jsonl` (ADR 0002), whose `record_id` is not in `S_raw`
(`records_all`). Earlier events for the same key are history and are not counted.

**Reporting.** The count of orphaned decisions and the `event_id` of each is written:

- beside the flow diagram, as a count, and
- in the methods text, with each record's `record_id`, the stage, and the reason it left `S_raw`, which is
  either a reversal or a supersession, and
- in the build report, for the build that found it.

**No deletion and no re-attachment.** The decision stays in `decisions.jsonl`. It is never folded into
membership, and it is never moved to another record. A reviewer who wants the paper screened again makes a new
decision on the record that is now in `S_raw`.

**Why a count, and not a `FlowCounts` field.** It is not an operand of any identity, for the reason in §2. Adding
it to `FlowCounts` would give `assert_consistent` a term that no equation uses, which would be a check that
cannot fail.

**Today's behaviour.** Nothing reports an orphaned decision (Context). The implementation adds the listing as a
separate function over the same snapshot, and it does not change which decisions are counted.

### 4. Reversal and supersession

**Reversal (`reverse`, ADR 0029 §7).** The registration becomes inactive, so the record leaves `S_raw`. Its
current decisions become orphaned and are reported. `registered` drops by one. If the paper had reached
`included`, `included` drops by one, and that is shown in the diagram, not hidden. A correction to a wrong DOI or
title is a reversal followed by a new registration, which has a new `record_id` (ADR 0029 §4), so the old
decisions are never reused.

**Supersession (`supersede`, ADR 0029 §6).** The build step writes one `supersede` event when a Scopus record
matches an active registration by DOI, or by normalised title and year. The registration becomes inactive, its
current decisions become orphaned, and the build report lists the event. The Scopus record enters `S_raw` with no
decisions. In the diagram it therefore counts in `unsure_title_abstract` until a reviewer decides it again, and
the diagram shows that screening is in progress (ADR 0007 Consequence 3). If the superseded paper had been
`included`, `included` drops until the Scopus record is decided again. Decisions are not transferred (ADR 0029 §6).

**A worked sequence.** The ADR 0029 example, with the numbers in this record applied:

1. `registered = 1`, the paper is `included`, `included = 1`.
2. A second search finds the same DOI as a Scopus record. `identified` rises by that search's total (ADR 0013).
   The build writes `supersede`. `registered = 0`.
3. The reviewer's decision on the `registered:` record is orphaned and reported. The Scopus record is in
   `S_raw` with no decision, so `unsure_title_abstract` rises by one, and `included` falls by one.
4. The identity still closes by the same mechanism as for any second search (ADR 0013): `identified` and the
   Scopus side of `S_raw` move together, and `registered` falls by the one registration that was superseded. The
   implementation test in Verification 6 checks the closure on a fixture of this shape, and the number is not
   asserted by hand here.

### 5. What does not change

- `identified` is still read only from `runs`, and it is still the sum of `total_results` (ADR 0013).
- Equations 2 to 5 are unchanged.
- `unsure` never resolves to inclusion (BUILD_PLAN line 973). The `included` set is still `M_full`.
- Registration writes nothing to `runs`, and it does not change the search.

## Alternatives considered

**Add `orphaned_decisions` to `FlowCounts`.** Rejected. No equation would use it, so `assert_consistent` could never
fail on it. It would also make a count that a reviewer reads as arithmetic into one that is not.

**Fail the build when any decision is orphaned.** Rejected. A reversal is a legitimate reviewer act (ADR 0029 §7), and
a build that refuses after a legitimate reversal would stop the diagram for a reason that is already on the record.
Reporting it keeps the number visible and lets the review go on.

**Re-attach orphaned decisions to the superseding Scopus record.** Rejected. ADR 0029 §6 and ADR 0002 both say a
decision is a human statement about one record, and a supersession is a new record. Carrying a decision across
would make a Scopus record look screened when no one screened it.

**Delete orphaned decisions.** Rejected. ADR 0002 forbids editing or deleting an event, and the orphaned decision is
the audit trail of what the reviewer decided and when.

**Write `registered` only when it is non-zero.** Rejected. A key whose presence depends on the data reads as "absent"
when it is "zero". ADR 0028 Resolved item 6 rejects the same ambiguity for field values. See open question 1.

## Consequences

1. The published `FlowCounts` contract changes again. Any code, test, or golden snapshot that compares a whole
   `FlowCounts` gains the `registered` field (ADR 0007 Consequence 1).
2. **`numbers.json` changes bytes for every project, including v1.0.0.** A Scopus-only project has `registered = 0`,
   and its numbers are unchanged in value. Under open question 1 option (a) its file gains one key. ADR 0027
   Decision 3 claims byte-identity of `numbers.json` across two runners, not against the released bytes, but the
   reference golden `tests/golden/report/__snapshots__/reference_numbers.json` changes. That change is deliberate,
   and the implementation pull request must name it under BUILD_PLAN line 927.
3. The flow diagram gains a registered line in the other-methods box, and the resolved-unregistered and
   orphaned-decision counts beside it. The renderer (`report/flow_diagram.py`, which draws the "Records identified
   from Scopus" box at line 207) must show each of them, not omit them (ADR 0007 Consequence 3).
4. A reversal or supersession can lower `included` after a paper was reported in a draft. The build report lists
   each such event, so the change is traceable, and the methods must state it.
5. Orphaned decisions are visible in the methods text, so a reader can see that a reversal or supersession happened
   after screening began.
6. `AppendOnlyLog` gains `append_if` in ADR 0029 §8, and a reversal or supersession uses it. This record does not
   change that mechanism.

## Open questions for the owner

1. **Does `numbers.json` gain a `registered` key for a Scopus-only project?** Options: (a) always write
   `"registered": 0`, so the key's presence never depends on the data. This changes the bytes of `numbers.json` for every
   project, the values stay the same, and the reference golden is re-baselined. (b) Write the key only when at least
   one registration exists. This keeps the bytes of every released project, but the key's presence then means
   "there were registrations". **Recommendation: (a).** ADR 0028 Resolved item 6 says a blank must never be confused with
   a zero, and option (b) makes the same confusion in the key itself. The re-baseline is one reviewed change.
2. **The reason on a reversal.** ADR 0029 §2 makes `reason` mandatory but does not say what it may contain. Options:
   (a) free text, non-empty; (b) a fixed set of codes, such as `wrong_doi`, `wrong_title`, `duplicate_of_scopus`,
   `not_eligible`, and `other` with a free-text note, validated the way ADR 0002 validates `reason_code`. **Recommendation:
   (b).** The reasons are reported in the methods, and free text cannot be counted or checked.
3. **Form of the amendment to ADR 0007.** ADR 0007's own rule says a further change to the `FlowCounts` contract requires
   "a new ADR that supersedes this one". Options: (a) this record supersedes ADR 0007 in full and restates its text;
   (b) this record amends ADR 0007's equation 1 only, and ADR 0007 gets a dated pointer note in the implementation pull
   request, as ADR 0029 did for ADR 0024. **Recommendation: (a).** The rule's wording asks for it, and a reader who
   opens ADR 0007 alone then sees the current contract. The cost is one restated record.

## Verification the implementation must include

1. `test_flow_counts__registrations_absent__identity_unchanged`: with no registrations, every field equals its value
   before this change, except `registered = 0`.
2. `test_flow_counts__one_registration__identity_closes_with_registered_term`: the worked check in Decision 1, with the
   term included.
3. `test_flow_counts__negative_registered__assert_consistent_raises`: the precondition in Decision 1 fires.
4. `test_flow_counts__registered_ids_in_S_raw__equal_registered`: the snapshot-consistency check in Decision 1.
5. `test_orphaned_decisions__reversed_registration__reported_not_folded`: a current decision on a reversed registration
   is listed by `event_id`, is absent from every set, and is absent from `FlowCounts`.
6. `test_orphaned_decisions__superseded_registration__scopus_record_is_unsure`: after `supersede`, the Scopus record counts
   in `unsure_title_abstract`, and `included` falls if the paper had been included.
7. `test_flow_counts__fails_on_original_defect`: each test above is shown to fail against the current `FlowCounts`
   (no `registered` field), following the project rule that a test which covers a line proves nothing until it fails on
   the original defect.
8. The suite runs under `pytest -n auto` and in a single process, and the golden re-baseline is named in the pull
   request.

## Related decisions

- [ADR 0002](0002-append-only-decision-log.md): the append-only decision log. A decision is never edited or deleted, which
  is why an orphaned decision stays in the file.
- [ADR 0007](0007-flow-counts-unsure-fields.md): the `unsure` fields and the first amendment of equation 1. Open question 3
  concerns its supersession rule.
- [ADR 0013](0013-identified-sums-across-searches.md): `identified` sums `total_results` across searches, and a supersession
  can raise it.
- [ADR 0016](0016-automated-exclusion-reasons.md): `excluded_automated_by_reason` and equation 5, which registrations join.
- [ADR 0028](0028-field-level-fallback-cascade.md): Resolved item 6, the rule against blank values that Decision 1 applies to
  the `registered` key.
- [ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md): the registration, reversal and supersession events,
  and the identity this record implements.
- [ADR 0032](0032-registered-records-table-and-classification-map.md): the table and view that registered records come from.
