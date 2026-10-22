# ADR 0033: FlowCounts Gains a Registered Count, and Orphaned Decisions Are Reported

## Status

Proposed — 2026-10-22.

This record exists only on the branch `docs/adr-0032-0033-drafts`. The governance test (`tests/unit/test_docs_governance.py`, `test_adr__on_main__is_not_still_proposed`)
scans the ADR directory of whichever checkout runs it, and fails any ADR whose Status reads `Proposed`
there. On this branch, this record and ADR 0032 fail that test by design.

**Acceptance is decided by a dedicated reviewer, not by this draft.** The owner has asked for that review. This record
stays `Proposed` through this revision. When the review passes, the Status line is changed to `Accepted — <date>` in
PR #150 itself, not in a separate pull request, so `main` never holds `Proposed`.

This record depends on [ADR 0032](0032-registered-records-table-and-classification-map.md), which must be accepted
first, because the registered records it counts come from the table and view that ADR 0032 specifies.

The change to the `FlowCounts` contract is a Stage 4 deviation. Its basis is the project's ADR rule, BUILD_PLAN §2.6
(line 301), as ADR 0029 (Context, "the spec clauses") applies it to Stage 2 and Stage 3. The contract is BUILD_PLAN lines
976-993 as amended by ADR 0007, ADR 0013 and ADR 0016.

**This record supersedes [ADR 0007](0007-flow-counts-unsure-fields.md) in full.** ADR 0007's Decision says that
further amending the `FlowCounts` contract requires a new ADR that supersedes it (ADR 0007, closing paragraph).
The restated contract is in Decision 1. The merge pull request changes ADR 0007's Status line to `Superseded by ADR 0033`.
This draft does not edit ADR 0007.

## Context

[ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md) lets a reviewer register a resolved candidate as a
record. Its §3 states the identity this record must implement:

1. `identified` is unchanged. It is the sum of `total_results` over distinct searches (ADR 0013), read from `runs`, never a
   row count.
2. `registered` is the number of active `register` events in `registrations.jsonl`. A registration is active when no
   `reverse` or `supersede` event names it.
3. Registered records are members of `S_raw`. A registered record that fails the automated set is counted in
   `excluded_automated`, and under its reason in `excluded_automated_by_reason`.
4. A decision that names a `record_id` outside `S_raw` is an orphaned decision. It is reported, not deleted, and it is not an
   arithmetic term.

**The current code has no such term.** `prisma/flow.py` defines `FlowCounts` at lines 56-119. Equation 1 is stated in the
`_assert_flow_counts_consistent` docstring (lines 160-161), and the code that checks it is at lines 242-249. `compute_flow_counts`
(lines 438-544) reads one snapshot. It reads `decisions.jsonl` only for records already in the sets it counts, so a decision for
an id outside those sets is never counted, and it is never reported either.

**The published numbers must stay tied to the arithmetic.** ADR 0007 rejected folding unresolved records into exclusion counts,
because that publishes a false number (its Alternatives 1 to 3). A count that is outside the identity cannot be audited
against it.

**A reversal or supersession can move a published number.** A paper a reviewer had included can leave the corpus when its
registration is reversed. It can re-enter the queue when a Scopus match supersedes it. Both change `included` and
`unsure_title_abstract`, and the diagram has to show them.

## Decision

### 1. The restated contract, and the registered field

`FlowCounts` is restated in full, so that one record carries the current contract. It gains one field, `registered`, placed after
`removed_other_reasons`. All other fields are those of ADR 0007, ADR 0013 and ADR 0016.

```python
@dataclass(frozen=True)
class FlowCounts:
    identified: int                       # sum of runs.total_results over distinct searches (ADR 0013)
    duplicates_across_searches: int       # ADR 0013
    removed_other_reasons: int            # unreadable capture entries (ADR 0012)
    registered: int                       # active registrations (ADR 0029 §3); new here
    excluded_automated: int               # |S_raw| - |S_raw ∩ A|
    excluded_automated_by_reason: Mapping[str, int]   # ADR 0016; sums to excluded_automated
    after_automated: int                  # |A|
    excluded_language: int                # |A| - |L|
    after_language: int                   # |L|
    excluded_title_abstract: int          # exclude decisions in L (ADR 0007)
    unsure_title_abstract: int            # ADR 0007
    retrieved_fulltext: int               # |M_abs|
    excluded_fulltext: dict[str, int]     # by reason_code
    unsure_fulltext: int                  # ADR 0007
    included: int                         # |C| = |M_full|
    def assert_consistent(self) -> None: ...
```

The five equations checked by `assert_consistent`:

```text
1. identified + registered - duplicates_across_searches - removed_other_reasons - excluded_automated == after_automated
2. after_automated - excluded_language == after_language
3. after_language == excluded_title_abstract + unsure_title_abstract + retrieved_fulltext
4. retrieved_fulltext == sum(excluded_fulltext.values()) + unsure_fulltext + included
5. sum(excluded_automated_by_reason.values()) == excluded_automated
```

Equation 1 is the only one whose form changes. Its added `registered` term is the one ADR 0029 §3 states. Equations 2 to 5 are
ADR 0007's and ADR 0016's, unchanged. `unsure_*` remain partition remainders (ADR 0007), so a finished review has both at zero.

**Worked check, with the ADR 0029 example.** `identified = 1,294`, one active registration, no duplicates, no other removals, no
automated exclusion: `1,294 + 1 - 0 - 0 - 0 = 1,295 = after_automated`.

**`assert_consistent` gains one precondition.** `registered` must be non-negative, checked with the other counts before any equation,
as `_assert_flow_counts_consistent` already does for every count.

**What `registered` counts, and where it comes from.** It is the number of active registrations in the same snapshot as every other
count. `compute_flow_counts` reads `registrations.jsonl` and its sidecar inside `_capture_snapshot`, on the same connection, so the
registration count and `S_raw` cannot be read at different moments. A registration active at that moment is in `records_all`
(ADR 0032 Decision 1), so it is in `S_raw`. A test asserts that the registered ids in `S_raw` number `registered`. `S_raw` is `raw_set` (`prisma/engine.py`, lines 726-744), which
reads `records` today. ADR 0032 Decision 1 changes it to read `records_all`. Without that change, no registration is in `S_raw`, and this
count would be zero by construction.

### 2. What is not an arithmetic term

Three counts are reported with the diagram but are not `FlowCounts` fields:

- **Resolved, unregistered candidates.** ADR 0029 §3 requires this count beside `registered`. It is computed from the candidate log,
  not from the record set, so it is not an operand of any identity. ADR 0007's Alternative 3 rejected splitting an identity's
  operands across two objects. This count is not an operand, so that objection does not apply.
- **Orphaned decisions.** Decision 3 defines them and their count. They are not operands either (ADR 0029 §3).
- **`numbers.json` keys.** `identified`, `registered` and the resolved-unregistered count are separate keys. No export puts
  `registered` into a key named `identified` (ADR 0029 §3). Every function named `identified` returns the Scopus value alone.

**`numbers.json` always carries `registered`.** A Scopus-only project writes `"registered": 0`. This is an owner decision. The key
is never omitted, because a key whose presence depends on the data reads "absent" where it means "zero". ADR 0028 Resolved item 6 rejects
the same confusion for field values, and the Alternatives below reject the data-dependent key.

The change to `numbers.json` bytes is listed under Consequences. The values of every existing key are unchanged for a project
with no registrations.

**Registrations in the corpus scalars (owner decision A).** `corpus.size`, `corpus.year_min` and `corpus.year_max` count Scopus
and registered records together, and the venue tables count registrations under their `venue_name` (ADR 0032 Decision 1). Each
registration is a record under ADR 0029, so this is the consequence of that status. The methods state the registered count beside
those numbers, in one sentence: "N records, of which M are registrations from a manual Google Scholar search (ADR 0029)". The `registered`
key in `numbers.json` is the same M, so the number is written once as data and once in the text.

### 3. Orphaned decisions: reported, not deleted, not re-attached

**Definition.** A **current** decision is the latest event for its `(stage, record_id, reviewer)` key, which is the fold rule of BUILD_PLAN
Stage 4 (lines 970-972: append-only, later events supersede earlier ones by `ts`, ties broken by `event_id`). An orphaned decision is a
current decision whose `record_id` is not in `S_raw` (`records_all`). Earlier events for the same key are history, and they are not
counted.

**Reporting.** The count of orphaned decisions, and the `event_id` of each, is written:

- beside the flow diagram, as a count;
- in the methods text, with each record's `record_id`, the stage, and the reason it left `S_raw` (a reversal or a supersession);
- in the build report, for the build that found it.

**No deletion and no re-attachment.** The decision stays in `decisions.jsonl`. It is never folded into membership and never moved to
another record. A reviewer who wants the paper screened again makes a new decision on the record now in `S_raw`.

**Why a count and not a `FlowCounts` field.** It is not an operand of any identity (§2). A field in `FlowCounts` that no equation uses
would make a check that cannot fail.

**Today's behaviour.** Nothing reports an orphaned decision (Context). The implementation adds the listing as a separate function over the
same snapshot, and it does not change which decisions are counted.

### 4. Reversal and supersession

**Reversal (`reverse`, ADR 0029 §7).** `reason` is one of a closed set, validated on write (owner decision): `wrong_doi`,
`wrong_title`, `duplicate_of_scopus`, `not_eligible`, or `other`. The writer refuses any other value, and `other` requires a
non-empty note. The registration becomes inactive, so the record leaves `S_raw`. Its current decisions become
orphaned and are reported. `registered` drops by one. If the paper had reached `included`, `included` drops by one, and the diagram shows
that. A correction to a wrong DOI or title is a reversal followed by a new registration, which has a new `record_id` (ADR 0029 §4), so
the old decisions are never reused.

**Supersession (`supersede`, ADR 0029 §6).** The build step writes one `supersede` event when a Scopus record matches an active
registration by DOI, or by normalised title and year. The registration becomes inactive, its current decisions become orphaned, and
the build report lists the event. The Scopus record enters `S_raw` with no decisions, so it counts in `unsure_title_abstract` until a
reviewer decides it again. The diagram then shows that screening is in progress (ADR 0007 Consequence 3). If the superseded paper had been
`included`, `included` drops until the Scopus record is decided again. Decisions are not transferred (ADR 0029 §6).

**A worked sequence**, using the ADR 0029 example:

1. `registered = 1`, the paper is `included`, `included = 1`.
2. A second search finds the same DOI as a Scopus record. `identified` rises by that search's total (ADR 0013). The build writes
   `supersede`, and `registered = 0`.
3. The reviewer's decision on the `registered:` record is orphaned and reported. The Scopus record is in `S_raw` with no decision, so
   `unsure_title_abstract` rises by one, and `included` falls by one.
4. The identity still closes by the mechanism of any second search (ADR 0013): `identified` and the Scopus side of `S_raw` move together,
   and `registered` falls by the one superseded registration. Verification 6 checks the closure on a fixture of this shape, and this
   record does not assert the numbers by hand.

### 5. What does not change

- `identified` is still read only from `runs`, and it is still the sum of `total_results` (ADR 0013).
- Equations 2 to 5 are unchanged.
- `unsure` never resolves to inclusion (BUILD_PLAN line 973). `included` is still `M_full`.
- Registration writes nothing to `runs`, and it does not change the search.

## Alternatives considered

**Add `orphaned_decisions` to `FlowCounts`.** Rejected. No equation would use it, so `assert_consistent` could never fail on it.

**Fail the build when any decision is orphaned.** Rejected. A reversal is a legitimate reviewer act (ADR 0029 §7). A build that refuses
after it would stop the diagram for a reason already on the record. Reporting keeps the number visible and lets the review go on.

**Re-attach orphaned decisions to the superseding Scopus record.** Rejected. ADR 0029 §6 says a decision is a human statement about one
record, and a supersession creates a new one. Carrying a decision across would make a Scopus record look screened when no one screened it.

**Delete orphaned decisions.** Rejected. ADR 0002 keeps the decision log append-only, and the orphaned decision is the audit trail of what the
reviewer decided and when.

**Write `registered` only when it is non-zero.** Rejected by owner decision (§2). A key whose presence depends on the data is ambiguous. The
values stay the same whichever way it is written, so the cost is a re-baselined golden file, which is named below.

**Amend ADR 0007's equation 1 only, with a pointer note.** Rejected by owner decision. ADR 0007's closing paragraph requires a superseding
record, so the restated contract is the one that the code and its tests read.

## Consequences

1. **ADR 0007 is superseded in full.** The merge pull request changes its Status line to `Superseded by ADR 0033`. Nothing else in ADR 0007
   changes, and its text stays as the record of the earlier contract.
2. **`numbers.json` changes bytes for every project, including v1.0.0, and its values do not change.** A Scopus-only project's
   `numbers.json` gains the key `"registered": 0`. The reference golden `tests/golden/report/__snapshots__/reference_numbers.json` changes,
   and so does a new golden `tests/golden/report/__snapshots__/numbers_keys.json`, which pins the sorted key list of `numbers.json`. Both
   changes are deliberate. The implementation pull request names them under BUILD_PLAN line 927. ADR 0027 Decision 3 claims byte-identity of
   `numbers.json` across two runners, so the claim applies to the new bytes, and the pull request states the re-baseline.
3. The flow diagram gains a registered line in the other-methods box, and the resolved-unregistered and orphaned-decision counts beside it.
   The renderer (`report/flow_diagram.py`, which draws the "Records identified from Scopus" box at line 207) must show each of them, not
   omit them (ADR 0007 Consequence 3).
4. A reversal or supersession can lower `included` after a paper was reported in a draft. The build report lists each such event, and the
   methods must state it.
5. Orphaned decisions appear in the methods text, so a reader can see that a reversal or supersession happened after screening began.
6. `AppendOnlyLog` gains `append_if` in ADR 0029 §8, and a reversal or supersession uses it. This record does not change that mechanism.

## Open questions for the owner

None. The owner has decided the reason-code set (Decision 4), the `corpus.*` counts (Decision 2), and the `numbers.json` key
(§2). The acceptance of this record is decided by the dedicated review named in Status.

## Verification the implementation must include

1. `test_flow_counts__registrations_absent__identity_unchanged`: with no registrations, every field equals its value before this change,
   except `registered = 0`.
2. `test_flow_counts__one_registration__identity_closes_with_registered_term`: the worked check in Decision 1, with the term included.
3. `test_flow_counts__negative_registered__assert_consistent_raises`: the precondition in Decision 1 fires.
4. `test_flow_counts__registered_ids_in_S_raw__equal_registered`: the snapshot-consistency check in Decision 1. `test_raw_set__registered_id__is_in_S_raw` asserts that `raw_set` returns the registered id, so the automated filters see it.
5. `test_orphaned_decisions__reversed_registration__reported_not_folded`: a current decision on a reversed registration is listed by
   `event_id`, is absent from every set, and is absent from `FlowCounts`.
6. `test_orphaned_decisions__superseded_registration__scopus_record_is_unsure`: after `supersede`, the Scopus record counts in
   `unsure_title_abstract`, and `included` falls if the paper had been included.
7. `test_numbers_json__registered_key__always_present`: a Scopus-only project writes `"registered": 0`, and `numbers_keys.json` lists it.
8. Each test above is shown to fail against the current `FlowCounts` (no `registered` field), following the project rule that a test which
   covers a line proves nothing until it fails on the original defect.
9. `test_reversal__reason_outside_closed_set__is_refused`: a `reverse` event with a reason outside the codes in Decision 4 is refused
   on write, and `other` without a note is refused. The log's bytes and checksum are unchanged.
10. The suite runs under `pytest -n auto` and in a single process, and the golden re-baseline is named in the pull request.

## Related decisions

- [ADR 0002](0002-append-only-decision-log.md): the append-only decision log, which keeps an orphaned decision in the file.
- [ADR 0007](0007-flow-counts-unsure-fields.md): the `unsure` fields. This record supersedes it in full (Status).
- [ADR 0013](0013-identified-sums-across-searches.md): `identified` sums `total_results` across searches, and a supersession can raise it.
- [ADR 0016](0016-automated-exclusion-reasons.md): `excluded_automated_by_reason` and equation 5, which registrations join.
- [ADR 0028](0028-field-level-fallback-cascade.md): Resolved item 6, the rule against blank values, which the `registered` key applies.
- [ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md): the registration, reversal and supersession events, and the identity
  this record implements.
- [ADR 0032](0032-registered-records-table-and-classification-map.md): the table and view that registered records come from.
