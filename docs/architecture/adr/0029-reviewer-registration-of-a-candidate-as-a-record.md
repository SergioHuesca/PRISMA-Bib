# ADR 0029: Reviewer Registration of a Candidate as a Record, Amending Stage 2

## Status

Proposed — 2026-10-21. Not in force. Amends the BUILD_PLAN Stage 2 criterion that makes `total_results`
the only source of the "records identified" number, and, as a consequence, the Stage 3 schema and the
Stage 4 `FlowCounts` contract. Each is a deviation from the frozen BUILD_PLAN and needs this record under
§2.6. Moves to Accepted only when the owner approves it on merge. No registration code is written before
that, as [ADR 0028](0028-field-level-fallback-cascade.md) resolved item 3 requires.

## Context

[ADR 0028](0028-field-level-fallback-cascade.md) Decision 5 lets a person search Google Scholar by hand and
paste title and year rows. Each row is a candidate, and a candidate is not a record. ADR 0028 also records
that the owner has chosen that a reviewer may register a candidate as a record, and that doing so needs its
own ADR, because the Stage 2 rule about identified records is affected.

Without registration, a paper the reviewer has found and resolved (its DOI or its exact title and year
matched a source work) can never be screened. The corpus then omits it, and nothing in the flow diagram
says so. This ADR is the smallest change that lets such a paper enter screening, while keeping the Scopus
search as the only source of the "identified" number and keeping every registration traceable.

## The spec clauses this amends

BUILD_PLAN Stage 2, acceptance criterion 5 (`S02-AC5`), reads exactly:

> The reported `total_results` is recorded in the manifest and is the **only** source of the PRISMA "records identified" number.

Its test row reads exactly:

> `test_manifest__total_results__is_the_only_identified_count` | unit | `FlowCounts.identified` reads the manifest, not a row count. Claims `S02-AC5`.

BUILD_PLAN §2.6 reads exactly:

> Every non-obvious choice gets an ADR in `docs/architecture/adr/` using the standard template (Context / Decision / Status / Consequences). The five listed in §2.3 are written in Stage 1 and are mandatory. Any later deviation from §1.2 requires a new ADR that supersedes the relevant one.

**Reading of §2.6.** The sentence above, taken literally, reaches only deviations from §1.2, and the Stage 2
criterion is not in §1.2. This ADR relies on two other sources. The project's `CLAUDE.md` states the rule as
"Contracts, schemas, and acceptance criteria are not renegotiable without a new ADR (§2.6)." And
[ADR 0013](0013-identified-sums-across-searches.md) applied §2.6 to the Stage 4 `FlowCounts` contract, which
is also outside §1.2. The owner should confirm this reading (question 1 below).

This ADR also touches the Stage 3 schema (one table and one view) and the Stage 4 `FlowCounts` contract
(one field, and equation 1). ADR 0012 and ADR 0013 require any addition to `schema.sql` to have its own ADR,
so those additions are recorded here, not left implicit.

## Decision

**A resolved candidate may be registered as a record, by a named reviewer, as an explicit Layer 2 event.
`identified` is not changed. The registered records are counted on a separate line.**

### 1. Who may register, and what counts as resolved

- Only a named reviewer may register a candidate. Registration is never automatic: it is not a side effect of
  a search, an attachment, or an enrichment run, and it is never done in a batch without one event per candidate.
- A candidate is **resolved** when a source work, retrieved at a level of [ADR 0028](0028-field-level-fallback-cascade.md)
  (level 4, OpenAlex, or level 5, a manual payload), satisfies exactly one of:
  - (a) the candidate's DOI equals the work's DOI; or
  - (b) the candidate has no DOI, and the work's title and the candidate's title are equal after `normalise_title`
    (the function in `capture/candidates.py`), and the years are equal.
- Exactly one work in the source response may satisfy the rule. Two or more matches leave the candidate unresolved.
  ADR 0028 does not state this condition; this ADR adds it, because a title and year can match two different papers
  (a conference paper and its preprint, for example).
- A title match that is not exact (ADR 0028 Decision 4's review list) is **not** resolved, even when a person has
  confirmed it. Question 4 asks the owner to confirm this.
- Before the event is written, the reviewer confirms the DOI and title that will be registered. The UI shows them
  and the match rule.

### 2. The registration is a Layer 2 event, and nothing writes Layer 0

- A registration is written to `projects/<slug>/decisions/registrations.jsonl`, with a checksum sidecar
  `registrations.jsonl.sha256`, through the shared `AppendOnlyLog` ([ADR 0024](0024-one-implementation-of-append-only-durability.md)),
  as the candidate paste events already are. It is a separate file, not `decisions.jsonl`, so the Stage 4 fold keyed
  on `(stage, record_id, reviewer)` is not changed.
- Each event carries `event_id` (ULID), `schema_version` (1), `ts` (UTC, ending `Z`), `project`, `action`
  (`register` or `reverse`), and `reviewer`. A `register` event also carries `record_id`, `candidate_id` (the
  candidate's id from ADR 0028 Decision 5), `match_rule` (`doi` or `title_year`), `source_level` (4 or 5),
  `source_payload_ref` (the same `PayloadRef` type Stage 3 uses), `source_payload_sha256`, and `doi` (nullable).
  A `reverse` event carries `reverses` (the `event_id` of the `register` event) and a mandatory `reason`.

```json
{
  "event_id": "01HZ7M3QK2W9T4R8N6Y1C5B0XD",
  "schema_version": 1,
  "ts": "2026-11-02T09:14:55.120Z",
  "project": "baseball-cvpr",
  "action": "register",
  "record_id": "registered:01HZ7M3QK2W9T4R8N6Y1C5B0XD",
  "candidate_id": "3f9c0a7e...",
  "reviewer": "sh",
  "match_rule": "title_year",
  "source_level": 4,
  "source_payload_ref": {"file": "raw/run-0007/openalex-0001.jsonl", "line": 42},
  "source_payload_sha256": "9b1e...",
  "doi": null
}
```

Illustrative values, not from a real run:

- The `registered_records` table in Layer 1 (see Consequences) is derived by the loader, the only writer of Layer 1
  ([ADR 0028](0028-field-level-fallback-cascade.md) Decision 3), by folding this file and reading the referenced
  payloads. Bibliographic fields come from the referenced payload, with the per-field provenance rules of ADR 0028.
- Nothing in the registration path opens `raw/` for writing. It reads the referenced payload and nothing else.
  It never writes `decisions.jsonl`, never creates a screening decision, and never inserts into the Scopus `records` table.

### 3. The count: `identified` is unchanged, and `registered` is a separate line

The rule, stated exactly:

```text
identified   = Σ over each distinct query string in `runs` of that query's earliest run's `total_results`
               (unchanged: ADR 0013; read only from `runs`; never a row count)
registered   = the number of `register` events in registrations.jsonl whose record_id is not
               the target of any `reverse` event
```

`FlowCounts` gains one field, `registered: int`. Equation 1 of `FlowCounts.assert_consistent()` becomes:

```text
1.  identified
      + registered
      - duplicates_across_searches
      - removed_other_reasons
      - excluded_automated            == after_automated
```

Equations 2 to 5 are unchanged. The non-negativity precondition is extended to `registered`. The set entering
screening is the Scopus records loaded into `records`, plus the active registrations. A registered record is never
counted in `duplicates_across_searches`, because §1 and §5 prevent it from duplicating a record already present.

For the PRISMA 2020 diagram (generated from `FlowCounts`, BUILD_PLAN Stage 10):

- `identified` is drawn in the identification box for databases and registers, with its label unchanged.
- `registered` is drawn as its own line in the identification box for other methods, labelled as a reviewer
  registration of candidates from a manual Google Scholar search.
- Both feed one box, whose number is `identified + registered - duplicates_across_searches - removed_other_reasons`.
- The number of resolved candidates not registered is reported beside `registered` as a plain count. It is not in
  equation 1 (Consequences 4).
- `numbers.json` and the methods carry `identified` and `registered` as separate keys. No export sums them under the
  name "identified", and every function named `identified` returns the Scopus value alone.

### 4. The `record_id` scheme for registered records

- A registered record's `record_id` is `registered:<event_id>`, where `<event_id>` is the ULID of its `register`
  event. Example: `registered:01HZ7M3QK2W9T4R8N6Y1C5B0XD`.
- The prefix `scopus:` (BUILD_PLAN §3.2) is produced only by the Scopus loader. The Scopus loader rejects any id that
  starts with `registered:`. The registration writer rejects any id that does not start with `registered:`, and rejects
  a `register` event whose `record_id` is not exactly `"registered:" + event_id`.
- An id is never reused. A registration after a reversal gets a new `event_id` and so a new `record_id`.
- The build checks that the `record_id` values in `records` and `registered_records` are disjoint.
- The id is not derived from the DOI or from `candidate_id`. A DOI is absent for a title-and-year match, and a
  `candidate_id`-based id would be reused after a reversal and re-registration, which §5 forbids.

### 5. What registration cannot do

- **No registration from Scholar without a resolved match.** A candidate with no match under §1, or with a
  non-exact title match, stays unresolved, and the registration is refused.
- **No registration of a paper the corpus already holds.** A candidate whose DOI, or whose normalised title and
  year, equals an existing record (Scopus or an active registration) is attached under ADR 0028 Decision 4 and is
  not registered.
- **No second active registration for one candidate.** A `candidate_id` with an active registration cannot be registered again.
- **No silent overwrite.** No event edits or deletes another event, and no field of an active registration can change.
  A wrong DOI or title is corrected by reversing the registration and registering again, which gives a new `record_id`.
- **Append-only, reversible only by a new event.** A `reverse` event names the registration it reverses and gives a
  reason. A reversal of an unknown or already-reversed registration is refused. The original events stay readable.
- **No reversal after screening has started.** A reversal is refused once any event in `decisions.jsonl` references
  the `record_id`. The alternative is in question 6.
- **No write outside Layer 2.** Registration never writes Layer 0, never writes a screening decision, and never
  writes the Scopus `records` table.
- **A refused attempt writes nothing.** The refusal is shown to the reviewer and leaves the log's bytes and checksum unchanged.

### 6. A later Scopus search returns the same work

If a Layer 1 build finds a `records` row whose DOI, or whose normalised title and year, equals an active registered
record, the build raises `ValidationError`, naming both ids, and writes no store. The reviewer resolves it by appending
a `reverse` event (question 7 asks whether to make this automatic instead). Silent deduplication is refused, because it
would move a record from `registered` to `duplicates_across_searches` without anyone deciding that.

### 7. What this does not change

- Acquisition still requires `view=COMPLETE`, and a refused run stays refused and unsealed (ADR 0028 Decision 7, BUILD_PLAN
  Stage 2 criterion 4). Registration is not a search. It writes no row to `runs`, so `identified` is unaffected.
- Scopus remains the only source of identified records. The Scopus search still defines the Scopus set.
- A candidate that is not registered is still not a record. ADR 0028 Decision 5's first sentence holds for it.
- ADR 0028 Decision 4's closing sentence, "so the set of records is still fixed by the Scopus search", is amended for
  registrations only. Attachment still never creates a record. Only a human `register` event does.
- The v1.0.0 claim (ADR 0027) is unchanged. No v1.0.0 project has a registration event, so its numbers are unchanged.

## Alternatives considered

**Keep candidates outside the record set (ADR 0028 as written).** This is the fallback if the owner declines this ADR,
and ADR 0028 Decision 5 then holds as written. It is not chosen because a paper that has been found and resolved has no
path into screening, and the flow diagram cannot say that the search missed it.

**Register into a separate Layer 1 table that the application writes directly.** Rejected. Layer 1 is written only by the
loader (ADR 0028 Decision 3) and must be rebuildable from Layers 0 and 2 (BUILD_PLAN §2.2). A table written by the
application has no event history and cannot be rebuilt.

**Count registrations inside `identified`, by summing them into `total_results` or writing a synthetic run.** Rejected. It
breaks `S02-AC5`, and `identified` would stop being Scopus's number. ADR 0013 rejected this kind of plausible wrong number.

**Write the registration as a Layer 0 payload.** Rejected. Layer 0 holds verbatim HTTP responses (BUILD_PLAN §2.2). A
human choice is not a response, and a synthetic response would also create a run with a `total_results` that could be
confused with a search.

**Register into `decisions.jsonl`.** Rejected. The Stage 4 fold is keyed on `(stage, record_id, reviewer)`, and a new kind of
event there would have to be skipped by every reader. A separate file leaves the frozen fold intact.

**Derive the `record_id` from the DOI, or from `candidate_id`.** Rejected, for the reasons in §4.

**Register every resolved candidate automatically.** Rejected. The corpus would then depend on which rows a person pasted
and on a match nobody reviewed. Each record is registered by a named person, one event at a time.

## Consequences

1. **Existing projects are unchanged.** A project with no registration events has `registered = 0`, so every number,
   every existing table checksum on the reference fixture, and `numbers.json` are unchanged. The Scopus-only golden
   results do not move.
2. **`FlowCounts` changes again.** This is a further change to the frozen contract, after ADRs 0007, 0013 and 0016. It adds
   one field and changes equation 1. The frozen BUILD_PLAN text is not edited; this ADR is the recorded deviation.
3. **Layer 1 gains one table and one view**, as ADR 0012 and ADR 0013 require for schema additions:

   ```sql
   CREATE TABLE registered_records (
     record_id TEXT PRIMARY KEY,            -- 'registered:' || register event_id
     registration_event_id TEXT NOT NULL,
     reviewer TEXT NOT NULL,
     registered_at TIMESTAMP NOT NULL,
     candidate_id TEXT NOT NULL,
     match_rule TEXT NOT NULL,              -- 'doi' | 'title_year'
     source_level INTEGER NOT NULL,         -- 4 or 5 (ADR 0028)
     source_payload_sha256 TEXT NOT NULL,
     doi TEXT, title TEXT NOT NULL, year INTEGER NOT NULL
   );
   CREATE VIEW corpus_records AS
     SELECT record_id, 'scopus' AS origin, doi, title, year FROM records
     UNION ALL
     SELECT record_id, 'registered' AS origin, doi, title, year FROM registered_records;
   ```

   Stage 7 and Stage 10 read the corpus through `corpus_records`, so every record carries its `origin`. The golden checksum
   snapshot gains the new table's key, and no existing table, column, type or primary key changes.
4. **Selection bias is a new risk.** The reviewer chooses which resolved candidates to register. Mitigations: each
   registration names its reviewer and time; the count of resolved candidates not registered is reported beside
   `registered`; and the methods state the query each paste was made with. Question 3 asks whether selective registration is
   allowed at all.
5. **Stage 7 citation metrics.** Registered records have no Scopus citation snapshot. Their `cited_by_count` is reported as
   `unavailable`, counted explicitly in every citation denominator, split by `origin`, and never imputed. This applies
   ADR 0028 Consequence 6 and Resolved item 6.
6. **Methods and exports must name the second population.** The corpus is no longer only the Scopus search's results. The
   methods state the registrations, their sources and their reviewer, and they say that the registered papers came from a
   manual Google Scholar search, which has no API.
7. **Reproducibility needs more than Layer 0.** A corpus with registrations reproduces only if `registrations.jsonl`, the
   sidecar, and the referenced payloads are archived with the Layer 0 archive. Layer 2 is not in git (BUILD_PLAN §2.5). The
   Stage 11 manifest must record them (question 8).
8. **ADR 0028 Resolved item 3 expected `identified` to change.** This ADR does not change it. The count moves to a new line,
   and ADR 0028's text is not edited.

## Verification the implementation will need

- **V1.** `FlowCounts.identified` still reads only `runs` (`test_manifest__total_results__is_the_only_identified_count`
  is kept), and a new test shows that registering any number of candidates leaves `identified` unchanged.
- **V2.** `registered` counts active registrations only. Unit cases: none; one `register`; `register` then `reverse`
  (0); `register`, `reverse`, `register` (1, with a new `record_id`).
- **V3.** Equation 1 with `registered`: a Hypothesis property over random streams of `register` and `reverse` events closes
  `assert_consistent()`. A corrupted `registered` fails and names equation 1. Mutation testing (mutmut) runs on the new
  equation, following the ADR 0007 and ADR 0013 precedent.
- **V4.** One test for each refusal in §1 and §5 (no match, non-exact title, two matches, duplicate of a Scopus record,
  second active registration, reversal of an unknown or reversed event, reversal after a decision, a bad prefix, a
  `record_id` not equal to `"registered:" + event_id`). Each asserts that the log's bytes and checksum are unchanged.
- **V5.** A hand edit of `registrations.jsonl` raises `LogError` on the next load.
- **V6.** Prefix separation: the Scopus loader rejects `registered:`; the registration writer rejects `scopus:`; the build
  rejects a shared `record_id` between `records` and `registered_records`.
- **V7.** Layer 0 immutability: `raw/` is byte-identical before and after a registration. This extends
  `test_capture__raw_dir__is_never_reopened_for_write`.
- **V8.** Rebuild: deleting Layer 1 and rebuilding from Layers 0 and 2 gives byte-identical table checksums, including
  `registered_records` and the `corpus_records` view's rows.
- **V9.** Reference fixture (Scopus-only): every existing table checksum and `numbers.json` are identical. Only the new
  table's key is added to the golden snapshot.
- **V10.** The flow diagram test extracts the integer set from the SVG and compares it with `FlowCounts`, which now includes
  `registered`. `numbers.json` carries `identified` and `registered` as separate keys.
- **V11.** A Stage 7 citation metric over a corpus containing registered records reports `unavailable` for them, with the
  denominator split by `origin`, and imputes nothing.
- **V12.** Machine independence: `ts` is UTC with a `Z` suffix, folds sort by `event_id`, and no result depends on locale or
  timezone. The suite runs under `pytest -n auto` as well as single-process, and the Stage 11 clean-clone check on a
  second machine covers a corpus with registrations.
- **V13.** `normalise_title` is pinned by a test. ADR 0028 Decision 4 says "punctuation and whitespace removed", but the
  implementation replaces punctuation with a space, so `well-being` matches `well being` and does not match `wellbeing`.
  The implementation keeps the code's definition, and question 9 covers the wording.

## Questions for the owner before acceptance

1. **§2.6 scope.** Confirm that the Stage 2 criterion and the Stage 4 contract are covered by §2.6 as the project's
   `CLAUDE.md` states it, rather than by the narrower §1.2 wording quoted above.
2. **Identified stays Scopus-only.** Confirm that `registered` is a separate line, which narrows ADR 0028 Resolved item 3's
   expectation. Confirm the PRISMA box: "other methods" (recommended) or "databases and registers".
3. **Selective registration.** May a reviewer register some resolved candidates from one paste and leave others? The
   recommendation is yes, with a reason on each registration and the unregistered count reported. The alternative is
   all-or-nothing per paste.
4. **Exact titles only.** Confirm that a non-exact title match is never registrable, even after a person has confirmed it.
5. **DOI required?** Should a registration require a DOI on the resolved work? Title-and-year registration is allowed by the
   task brief. Requiring a DOI would make deduplication stronger and reproducibility simpler, at the cost of registering fewer papers.
6. **Reversal after screening.** Refuse (recommended), or allow it and report the orphaned decisions?
7. **Scopus later indexes the same work.** Fail the build until a reversal is appended (recommended), or supersede
   automatically?
8. **Archive.** Should `registrations.jsonl`, its sidecar, and the referenced payloads be part of the Stage 11 archive for `numbers.json`?
9. **Wording.** Accept the code's `normalise_title` and amend ADR 0028 Decision 4's wording, or change the code to match the wording?
10. **Where the writer lives.** The registration writer reads Layer 1 for the duplicate check, so it cannot live in Stage 2
    `capture/` (BUILD_PLAN §0, rule 1: no stage imports from a later stage). Confirm it belongs in the Stage 4 `prisma/` layer.

## Related decisions

- [ADR 0002](0002-append-only-decision-log.md): the append-only pattern, applied here to a human-written registration.
- [ADR 0003](0003-human-only-screening.md): human-only screening. Registration is also a human act, and it is never automatic.
- [ADR 0007](0007-flow-counts-unsure-fields.md), [ADR 0013](0013-identified-sums-across-searches.md) and
  [ADR 0016](0016-automated-exclusion-reasons.md): earlier changes to the `FlowCounts` contract. This ADR is a further one.
- [ADR 0012](0012-persisting-skipped-layer0-entries.md): the rule that a Layer 1 schema addition needs its own ADR.
- [ADR 0024](0024-one-implementation-of-append-only-durability.md): the shared `AppendOnlyLog` that the registration log uses.
- [ADR 0027](0027-what-v1-0-0-claims.md): what v1.0.0 claims, which this ADR does not change.
- [ADR 0028](0028-field-level-fallback-cascade.md): the candidate, attachment and provenance rules that this ADR builds on and
  amends in its registration clause.
