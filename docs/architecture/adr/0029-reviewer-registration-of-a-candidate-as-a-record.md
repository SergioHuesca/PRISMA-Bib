# ADR 0029: Reviewer Registration of a Candidate as a Record, Amending Stage 2

## Status

Proposed — 2026-10-21. Not in force. This record amends the wording of BUILD_PLAN Stage 2 acceptance criterion 5 (`S02-AC5`) and nothing else in the frozen BUILD_PLAN. Three further records carry the other changes. Moving the shared `AppendOnlyLog` below Stage 3, with an `append_if` method, amends [ADR 0024](0024-one-implementation-of-append-only-durability.md), and the owner has decided it. The Stage 3 schema change is ADR 0032 and the Stage 4 `FlowCounts` change is ADR 0033, both to be written, and neither is decided here. Each change is a deviation from the frozen BUILD_PLAN and needs its own record under §2.6. This ADR moves to Accepted only when the owner approves it on merge. No registration code is written before then, as [ADR 0028](0028-field-level-fallback-cascade.md) resolved item 3 requires.

## Context

[ADR 0028](0028-field-level-fallback-cascade.md) Decision 5 lets a person search Google Scholar by hand and paste title and year rows. Each row is a candidate, and a candidate is not a record. ADR 0028 records that the owner has chosen to allow a reviewer to register a candidate as a record, and that this needs its own ADR, because the Stage 2 rule about identified records is affected.

Without registration, a paper that a reviewer has found and resolved (a source work matched the candidate by DOI, by exact title and year, or by a near match the reviewer confirmed) can never be screened. The corpus omits it, and the flow diagram does not say so. This ADR is the smallest change that lets such a paper enter screening while keeping the Scopus search as the only source of the "records identified" number and keeping every registration traceable.

## The spec clauses this amends

BUILD_PLAN Stage 2, acceptance criterion 5 (`S02-AC5`), reads exactly:

> The reported `total_results` is recorded in the manifest and is the **only** source of the PRISMA "records identified" number.

Its test row reads exactly:

> `test_manifest__total_results__is_the_only_identified_count` | unit | `FlowCounts.identified` reads the manifest, not a row count. Claims `S02-AC5`.

**Amended wording of `S02-AC5`.** The criterion now reads:

> The reported `total_results` is recorded in the manifest and is the **only** source of the PRISMA "records identified" number. Records registered by a reviewer under ADR 0029 are counted on a separate line named "registered", and are never added to or reported as part of that number.

Its test row is amended to add: registering any number of candidates leaves `FlowCounts.identified` unchanged (V1 below).

**Why the amendment is written, not argued.** The original sentence stays literally true after registration, because `identified` still reads only the manifest. But the PRISMA diagram would then have a second source of records entering screening, and "the only source" could be read as a claim about the set of records. Stating the boundary in the criterion removes that reading. Status and body say the same thing.

BUILD_PLAN §2.6 reads exactly:

> Every non-obvious choice gets an ADR in `docs/architecture/adr/` using the standard template (Context / Decision / Status / Consequences). The five listed in §2.3 are written in Stage 1 and are mandatory. Any later deviation from §1.2 requires a new ADR that supersedes the relevant one.

§1.2 reads exactly: "These were settled during design and are **not open for renegotiation** during implementation. Changing one requires a new ADR (see §2.6)."

The project's `CLAUDE.md` states the rule as: "Contracts, schemas, and acceptance criteria are not renegotiable without a new ADR (§2.6)." The Stage 2 criterion is not in §1.2. This ADR therefore relies on the `CLAUDE.md` reading, and on [ADR 0013](0013-identified-sums-across-searches.md), which applied §2.6 to the Stage 4 `FlowCounts` contract for the same reason.

## Decision

**A candidate resolved at level 4 (OpenAlex), against a work that has a DOI, may be registered as a record, by a named reviewer, as an explicit Layer 2 event. `identified` is not changed. Registered records are counted on a separate line, in the "other methods" identification box of the PRISMA diagram.**

### 1. Who may register, and what counts as resolved

- Only a named reviewer registers a candidate, one event per candidate. Registration is never automatic. It is not a side effect of a search, an attachment or an enrichment run, and nothing registers in batch.
- **Only level 4 can be registered.** A candidate resolved at level 1, 2 or 3 is already a Scopus record: those levels read Scopus payloads, so the work is in the Scopus data. Such a candidate is refused as a duplicate of a Scopus record, and it attaches under ADR 0028 Decision 4 instead. Level 5 (manual entry) is never a source either, because a manual payload is a Layer 2 event (ADR 0028 Decision 3) whose values a person has typed.
- **The source work must have a DOI.** A work without a DOI is refused. Every registered record therefore carries a DOI.
- A candidate is **resolved** when exactly one level 4 work satisfies one of these match rules:
  - `doi`: the candidate's DOI equals the work's DOI;
  - `title_year`: the candidate has no DOI, the work's title and the candidate's title are equal under `normalise_title` (the function in `capture/candidates.py`), and the years are equal. That function's definition is the code's: punctuation becomes a space, and whitespace is collapsed, so `well-being` matches `well being` and does not match `wellbeing`;
  - `near_confirmed`: the work is a near match to the candidate under the two rules that `capture/candidates.py` already applies to its review list (`near_matches`): the same normalised title with a different year, or the same year with one normalised title a whole-word prefix of the other. The reviewer must confirm that this work is the candidate, and the confirmation is the registration call itself. A near match is never registered without it.
- Two or more works that satisfy a rule leave the candidate unresolved, under any rule. ADR 0028 does not state this condition. This ADR adds it, because a title and year can match two different papers, such as a conference paper and its preprint.
- Before the event is written, the reviewer sees the DOI, the title, the year and the match rule, and confirms them. The confirmation is a UI step; the event records the match rule.
- A reviewer may leave a resolved candidate unregistered. The number of resolved candidates that are not registered is reported beside `registered` (§3).

### 2. The registration is a Layer 2 event

- A registration is written to `projects/<slug>/decisions/registrations.jsonl`, with the checksum sidecar `registrations.jsonl.sha256`, through `AppendOnlyLog` (ADR 0024), as candidate paste events already are. It is a separate file, not `decisions.jsonl`, so the Stage 4 fold keyed on `(stage, record_id, reviewer)` is not changed. Like `decisions.jsonl`, the file and its sidecar are tracked in git, as BUILD_PLAN §2.5 recommends. The Layer 0 payloads they reference are not committed; see Consequence 9.
- Each event carries `event_id` (ULID), `schema_version` (1), `ts` (UTC, ending `Z`), `project`, and `action`. The actions are:
  - `register`, which creates a record. It carries `reviewer`, `record_id`, `candidate_id` (the id from ADR 0028 Decision 5), `match_rule` (`doi`, `title_year` or `near_confirmed`), `source_level` (always 4), `source_payload_ref`, `source_payload_sha256`, and `doi` (required);
  - `reverse`, which removes a record it names. It carries `reviewer`, `reverses` (the `event_id` of the `register` event) and a mandatory `reason`;
  - `supersede`, which removes a record it names, written only by the automatic step in §6. It carries `reverses` (the `event_id` of the `register` event), `scopus_record_id`, `match_rule` (`doi` or `title_year`), and `actor` (`system`).
- **Why `supersede` is a separate kind, not a second `register`.** A `register` event creates a record, and a `supersede` event removes one. A record is created by a human's choice, and it is removed by a reversal or by a Scopus match. Writing removal as a second `register` would mix the two meanings in one kind, and every reader of `register` would have to check which one it was. The two kinds are kept apart so that the count of `register` events is the count of records ever created, and the count of active registrations is that count less the removals.
- `source_payload_ref` is a Layer 0 `PayloadRef` (the type Stage 3 uses for `source_payload_ref`). Because only level 4 is accepted, it always names an OpenAlex payload in Layer 0. A manual payload is a Layer 2 event, not a Layer 0 payload, so it has no `PayloadRef` into Layer 0 and never reaches this field.
- The `registered_records` table that holds these records in Layer 1 is specified by ADR 0032. The loader derives it, as the only writer of Layer 1 (ADR 0028 Decision 3), by folding this file and reading the referenced payloads.

Worked example of a `register` event. Illustrative values, not from a real run:

```json
{"event_id": "01HZ7M3QK2W9T4R8N6Y1C5B0XD", "schema_version": 1,
 "ts": "2026-11-02T09:14:55.120Z", "project": "example-review", "action": "register",
 "record_id": "registered:01HZ7M3QK2W9T4R8N6Y1C5B0XD",
 "candidate_id": "3f9c0a7e5b2d41f8a6c0e9d7b1a24c60", "reviewer": "sh",
 "match_rule": "title_year", "source_level": 4,
 "source_payload_ref": {"file": "raw/run-0007/openalex-0001.jsonl", "line": 42},
 "source_payload_sha256": "9b1e...", "doi": "10.0000/example.2019.1"}
```

### 3. The sets and the counts

- `identified` is unchanged. It is Σ `total_results` over the distinct searches (ADR 0013), read only from `runs`, and never a row count.
- `registered` is the number of `register` events in `registrations.jsonl` that are **active**. A registration is active when no `reverse` or `supersede` event names it.
- **S_raw is amended.** S_raw is the Scopus records loaded into Layer 1 `records`, **together with** the active registrations. A registration is therefore a member of S_raw, and it enters the automated-exclusion step like any record.
- **Registrations count in `excluded_automated`.** A registered record that fails the automated set A is counted in `excluded_automated`, and under its reason in `excluded_automated_by_reason` (ADR 0016), exactly as a Scopus record would be. Nothing counts a registered record outside those two terms.
- The identity that ADR 0033 must implement, stated here so the two records cannot disagree on what it means:

  ```text
  identified + registered - duplicates_across_searches - removed_other_reasons - excluded_automated == after_automated
  ```

  The added `registered` term is the only change to the identity's form. The `registered` records are in S_raw and nowhere else, and the reason is in the two bullets above. ADR 0033 writes the equation.
- **PRISMA diagram.** `registered` is drawn as one line in the **other methods** identification box (PRISMA 2020 has a box for identification by other methods), labelled as a reviewer registration of candidates from a manual Google Scholar search. `identified` stays in the box for databases and registers, with its label unchanged. The two flow into the same box, whose number is `identified + registered - duplicates_across_searches - removed_other_reasons`, as the identity above gives it before `excluded_automated` is subtracted.
- `numbers.json` and the methods carry `identified`, `registered` and the count of resolved, unregistered candidates as separate keys. No export sums `registered` into a value named "identified". Every function named `identified` returns the Scopus value alone.
- **Orphaned decisions.** A decision in `decisions.jsonl` that names a `record_id` no longer in S_raw is an orphaned decision. It is never folded into membership. It is reported as a count beside the diagram and listed in the methods. It is not an arithmetic term.

### 4. The `record_id` scheme for registered records

- A registered record's `record_id` is `registered:<event_id>`, where `<event_id>` is the ULID of its `register` event. Example: `registered:01HZ7M3QK2W9T4R8N6Y1C5B0XD`.
- The `scopus:` prefix (BUILD_PLAN §3.2) is produced only by the Scopus loader, which rejects any id beginning `registered:`. The registration writer rejects any id not beginning `registered:`, and rejects a `register` event whose `record_id` is not exactly `"registered:" + event_id`. The two prefixes are the enforcement. That no `scopus:` id equals a `registered:` id follows from the prefixes, so it is a documentation invariant. No runtime check is added for it.
- An id is never reused. A registration after a reversal gets a new `event_id`, and so a new `record_id`.
- The id is not derived from the DOI or from `candidate_id`. A `candidate_id`-based id would repeat after a reversal and re-registration.

### 5. What registration cannot do, and where each limit is enforced

| Limit | Enforcement point | Mechanism |
|---|---|---|
| Only level 4 is registrable. Levels 1 to 3 are refused as duplicates of Scopus records. Level 5 is refused. | The registration writer, before any lock is taken; the event model on write and on load | Validation at the boundary. `source_level` must be 4. |
| The work must have a DOI | The registration writer, against the referenced payload | A pure check before the write. |
| Exactly one work satisfies a match rule | The registration writer, against the referenced payload | A pure check before the write. |
| A near match is registered only with the reviewer's confirmation in the same call | The registration writer | A `near_confirmed` event is written only from a call that carries the confirmation. The event model requires a non-empty `reviewer` for it. |
| No registration of a paper the corpus already holds (a `records` row, or an active registration, with the same DOI or the same normalised title and year) | The registration writer, against Layer 1 as last built (the `records` half), and against the active registrations under the lock (the registration half) | The `records` half reads Layer 1 outside the registration lock, so a concurrent build can add a matching Scopus record after the check. The window is closed by the §6 `supersede` at the next build (§8). The registration half is checked inside the lock. |
| No second active registration for one `candidate_id` | The registration writer, **inside** the exclusive lock of the registration log, through `append_if` (§8) | The check reads the log's confirmed events, and the append follows in the same locked section. |
| A `reverse` names only an active registration | The registration writer, inside the exclusive lock | As above. |
| A `supersede` names only an active registration | The automatic step in §6, inside the exclusive lock | As above. |
| No silent overwrite; no event edits or deletes another | `AppendOnlyLog` (ADR 0024: append-only, checksum, duplicate `event_id` refused) | There is no update operation. A wrong DOI or title is corrected by reversing and registering again, which gives a new `record_id`. |
| Reversal is allowed after screening decisions exist; the decisions are reported as orphaned | The registration writer, plus the Stage 4 fold (ADR 0033) | The fold does not raise on an orphaned decision; it reports it (§3). |
| No write to Layer 0 | No code path. V7 asserts that `raw/` is byte-identical after a registration. | |
| No write to `decisions.jsonl` and no screening decision created | The registration module does not import `DecisionLog`. A test asserts it. | |
| No write to the Scopus `records` table | The registration module writes only the registration log. | |
| A refused attempt writes nothing | The writer raises before any append. | A test asserts the log's bytes and checksum are unchanged. |

**`AppendOnlyLog.write_event` bypasses business rules.** Its documented contract is "Append an already-constructed, already-validated event." It checks schema, checksum and duplicate `event_id`, and nothing else. It does not know what a candidate, a DOI, or an active registration is. The registration path therefore never calls `write_event` directly, since that would skip every rule in the table above. The business check and the append run in one locked section through `append_if` (§8). A test asserts that no module other than the registration writer and the §6 step calls `write_event` on the registration log.

### 6. A later Scopus match supersedes the registration, automatically and visibly

- A build step, run before the loader folds the registration log, takes the exclusive lock on `registrations.jsonl` and checks each active registration against the Scopus records just loaded. A Scopus record matches when its DOI equals the registration's DOI, or its normalised title and year equal the registration's. Near matches are not used here.
- For each match, the step appends a `supersede` event (§2) naming the `register` event and the Scopus `record_id`. The registration then stops being active. The `register` event is not altered. Nothing is removed from the log.
- The build report lists every `supersede` event it wrote. It is not silent.
- The `supersede` event is the only Layer 2 event written by a machine rather than a reviewer. ADR 0028 Decision 3 describes Layer 2 as human-written, so this is a visible departure, and ADR 0028 Resolved item 3 carries a pointer to it.
- Decisions on the superseded `registered:` record become orphaned (§3) and are reported. They are not transferred to the Scopus record. The reviewer decides on the Scopus record again.

### 7. Reversal after screening, and what is reported

- A `reverse` event is allowed even when `decisions.jsonl` already holds decisions for the record. The record leaves S_raw, and its decisions become orphaned (§3).
- Orphaned decisions are reported with their count and their `event_id`s. They are never deleted and never silently re-attached.

### 8. Where the shared log lives, and the locked check

- The registration log's own checks and its append must run in one exclusive lock. `AppendOnlyLog` takes its own lock inside `write_event`, and the lock is not reentrant (`_refuse_reentrant_lock` in `prisma/log.py`). A caller therefore cannot take the lock, check, and then call `write_event`. The class gains `append_if`, which runs a caller's check on the confirmed registration events and appends the event within the same locked section. The append happens only when the check returns.

What the lock covers is exactly the registration log: the one-active-registration rule, the `reverse` and `supersede` target checks, and the active-registration half of the duplicate rule. It does **not** cover Layer 1. The duplicate check's `records` half reads Layer 1 as last built. A Layer 1 build that adds a Scopus record does not take the registration lock, so a concurrent write can pass the duplicate check against a record that is about to appear. The window is closed only at the next build, when §6 appends a `supersede` event for the match. Taking the registration lock across a whole build would close it, but would couple the loader to the registration log, and that is not decided here.
- **Decided:** `AppendOnlyLog` moves to a module below Stage 3, with no stage-specific imports. Its current location, `src/prismabib/prisma/log.py`, is in the Stage 4 package. The registration writer reads Layer 1, so it is at or after Stage 3. The candidate intake (`capture/candidates.py`, Stage 2) already imports `prisma.log`, which is a Stage 2 module importing a later stage, forbidden by BUILD_PLAN §0 rule 1. The move removes that import. The new module path is set at implementation, and this ADR does not fix a file name.
- **This amends [ADR 0024](0024-one-implementation-of-append-only-durability.md).** ADR 0024 describes `AppendOnlyLog` as the one implementation shared by the decision log and the override log. The move keeps one implementation, changes its module location, and adds one method. It is not a second implementation. ADR 0024 carries a dated pointer note in the same pull request.

### 9. What this does not change

- Acquisition still requires `view=COMPLETE`, and a refused run stays refused and unsealed (ADR 0028 Decision 7, BUILD_PLAN Stage 2 criterion 4). Registration is not a search. It writes no row to `runs`, so `identified` is unaffected.
- Scopus remains the only source of identified records. The Scopus search still defines the Scopus set.
- A candidate that is not registered is still not a record. ADR 0028 Decision 5's first sentence holds for it.
- ADR 0028 Decision 4's closing sentence, "so the set of records is still fixed by the Scopus search", is amended for registrations only. Attachment still never creates a record. Only a human `register` event does.
- The v1.0.0 claim (ADR 0027) is unchanged. No v1.0.0 project has a registration event, so its numbers are unchanged.

## Worked example, end to end

Illustrative values, not from a real run. Suppose a review has `identified = 1,294` from one Scopus search.

1. A reviewer pastes the candidate "Example Paper", 2019, into the candidate log. Its `candidate_id` is `3f9c0a7e…` (ADR 0028 Decision 5).
2. The OpenAlex lookup (level 4) returns exactly one work with that normalised title and year, with DOI `10.0000/example.2019.1`. The payload is `raw/run-0007/openalex-0001.jsonl`, line 42, with checksum `9b1e…`.
3. The registration writer checks the level (4, allowed), the DOI (present), the single match under `title_year`, and that no `records` row and no active registration holds that DOI or that title and year. All pass.
4. Under the exclusive lock, `append_if` confirms that no active registration names `3f9c0a7e…`, then appends the `register` event above. `registered` becomes 1. `identified` stays 1,294.
5. The paper passes the automated set, so it reaches title and abstract screening. A reviewer records an `exclude` decision on `registered:01HZ7M3QK2W9T4R8N6Y1C5B0XD`.
6. Months later, a second Scopus search returns the same DOI as `scopus:2-s2.0-…`. Its `total_results` is recorded under its own query string, and `identified` rises by that search's total (ADR 0013). The build step appends a `supersede` event, and `registered` falls back to 0. The exclusion decision on the `registered:` record becomes orphaned. The build report lists both facts. The reviewer must decide the Scopus record again.

## Alternatives considered

**Keep candidates outside the record set (ADR 0028 as written).** This is the fallback if the owner declines this ADR. ADR 0028 Decision 5 then holds as written. It is not chosen because a paper that has been found and resolved has no path into screening, and the flow diagram cannot say that the search missed it.

**Register into a separate Layer 1 table that the application writes directly.** Rejected. Layer 1 is written only by the loader (ADR 0028 Decision 3) and must be rebuildable from Layers 0 and 2 (BUILD_PLAN §2.2). A table written by the application has no event history and cannot be rebuilt.

**Count registrations inside `identified`, by summing them into `total_results` or writing a synthetic run.** Rejected. It breaks `S02-AC5`, and `identified` would stop being Scopus's number. ADR 0013 rejected this kind of plausible wrong number.

**Write the registration as a Layer 0 payload.** Rejected. Layer 0 holds verbatim HTTP responses (BUILD_PLAN §2.2). A human choice is not a response, and a synthetic response would also create a run with a `total_results` that could be confused with a search.

**Register into `decisions.jsonl`.** Rejected. The Stage 4 fold is keyed on `(stage, record_id, reviewer)`, and a new kind of event there would have to be skipped by every reader. A separate file leaves the frozen fold intact.

**Derive the `record_id` from the DOI, or from `candidate_id`.** Rejected, for the reasons in §4.

**Register every resolved candidate automatically.** Rejected. The corpus would then depend on which rows a person pasted, and on a match nobody reviewed. Each record is registered by a named person, one event at a time.

**Keep `write_event` as the only write and check rules before calling it.** Rejected. The check would run outside the lock, so two concurrent registrations could both pass the one-active-registration rule. §8 keeps the check and the append in one locked section.

## Consequences

1. **Existing projects are unchanged.** A project with no registration events has `registered = 0`, no `supersede` events, and no orphaned decisions. Its numbers, its existing table checksums on the reference fixture, and `numbers.json` are unchanged.
2. **The Stage 4 `FlowCounts` contract changes again**, in ADR 0033 (to be written). The change adds `registered` and the identity in §3. It is a further deviation, after ADRs 0007, 0013 and 0016.
3. **Layer 1 gains a table and a view**, specified by ADR 0032 (to be written), as ADR 0012 and ADR 0013 require for schema additions.
4. **`AppendOnlyLog` moves and gains `append_if`** (§8). This amends ADR 0024, which carries a pointer note in this pull request.
5. **Layer 2 carries one machine-written event kind**, `supersede` (§6). The loader and every reader must accept it. The methods must say that some Layer 2 events were written by the build.
6. **Selection risk.** The reviewer chooses which resolved candidates to register. Each registration names its reviewer and time. The number of resolved candidates not registered is reported beside `registered`. The methods state the query each paste was made with.
7. **Stage 7 citation metrics.** Registered records have no Scopus citation snapshot. Their `cited_by_count` is reported as `unavailable`, counted explicitly in every citation denominator, split by origin, and never imputed. This applies ADR 0028 Consequence 6 and Resolved item 6.
8. **Methods and exports name the second population.** The corpus is no longer only the Scopus search's results. The methods state each registration's match rule, reviewer, and the fact that the paper came from a manual Google Scholar search, which has no API.
9. **Reproducibility needs Layer 2 as well as Layer 0.** A corpus with registrations reproduces only if `registrations.jsonl`, its sidecar and the referenced Layer 0 payloads are kept. BUILD_PLAN §2.5 says `decisions.jsonl` is optional to track and recommends tracking it, because it is the human labour. It does not mention `registrations.jsonl`. The owner has decided that the registration log and its sidecar are tracked in git, as §2.5 recommends for `decisions.jsonl`. The referenced Layer 0 payloads are **not** committed to git. They are Scopus or OpenAlex data, and §2.5 forbids committing Layer 0 raw JSONL. They stay in the owner's local Layer 0 archive (`projects/<slug>/raw/`, which is gitignored). The archive that accompanies the Stage 11 numbers is therefore the git-tracked registration log and sidecar, plus the local Layer 0 archive, and it is not a git commit. A clean clone reproduces a corpus with registrations only where the local Layer 0 archive is also present.

## Verification the implementation will need

- **V1.** `FlowCounts.identified` still reads only `runs`. `test_manifest__total_results__is_the_only_identified_count` is kept. A new test shows that registering any number of candidates leaves `identified` unchanged.
- **V2.** `registered` counts active registrations only. Unit cases: none; one `register`; `register` then `reverse` (0); `register`, `reverse`, `register` (1, with a new `record_id`); `register` then `supersede` (0).
- **V3.** Level validation. A `source_level` other than 4 is refused on write and on load. A level 1, 2 or 3 resolution is refused as a Scopus duplicate, and a level 5 resolution is refused.
- **V4.** One test for each refusal in §1 and §5: no match; two matches; a work without a DOI; a near match without the reviewer's confirmation; a near match that is not one of the two near rules; a duplicate of a Scopus record; a duplicate of an active registration; a second active registration for one `candidate_id`; a reversal of an unknown or already-reversed registration; a bad prefix; a `record_id` that is not `"registered:" + event_id`. Each asserts that the log's bytes and checksum are unchanged.
- **V5.** Concurrency. Two registrations for one `candidate_id`, started together, produce exactly one `register` event. This runs against the locked `append_if`, not against `write_event`.
- **V6.** `write_event` is called on the registration log only by the registration writer and the §6 step (a test asserts it), and `DecisionLog` is not imported by the registration module.
- **V7.** Layer 0 immutability. `raw/` is byte-identical before and after a registration. This extends `test_capture__raw_dir__is_never_reopened_for_write`.
- **V8.** A hand edit of `registrations.jsonl` raises `LogError` on the next load.
- **V9.** Prefix enforcement: the Scopus loader rejects `registered:`, and the registration writer rejects `scopus:`.
- **V10.** Supersession. A Scopus record matching an active registration by DOI or exact title and year yields one `supersede` event, listed in the build report. A rebuild gives the same result. The superseded registration's decisions are reported as orphaned.
- **V11.** Reversal after decisions. A reversal of a registration with decisions succeeds, and the decisions are reported as orphaned, not folded.
- **V12.** Machine independence. `ts` is UTC with a `Z` suffix. Folds sort by `event_id`. No result depends on locale or timezone. The suite runs under `pytest -n auto` as well as single-process, and the Stage 11 clean-clone check on a second machine covers a corpus without registrations only. A corpus with registrations reproduces only where the local Layer 0 archive is present (Consequence 9), so the clean-clone check does not cover it.
- **V13.** `normalise_title` is pinned by a test that asserts the code's definition: punctuation becomes a space, and whitespace is collapsed. So `well-being` matches `well being`, and does not match `wellbeing`. ADR 0028 Decision 4 words this differently. The code's definition governs. A dated pointer note in ADR 0028 records the difference, and its text is not edited.
- **V14.** Rebuild. Deleting Layer 1 and rebuilding from Layers 0 and 2 gives byte-identical table checksums, including the rows that ADR 0032 defines.

## Split records

- **ADR 0032 (to be written):** the Stage 3 Layer 1 table for registered records, and the view that unites them with Scopus records. This ADR fixes what the table must carry for registered records: `record_id`, `origin`, `doi` (required, since §1 requires a DOI), `title`, `year`, and the following three fields, which are a requirement on ADR 0032 and not an assumption made here:
  - `doc_type`, `language`, and the subject-area input. Criterion A is year, subject area and document type, and criterion L is language (BUILD_PLAN Stage 4). Both are computed from the stored fields, so a registered record without them cannot be classified and must not enter S_raw. ADR 0032 must specify how each is obtained for a registered record.
  - Subject area is the open point. OpenAlex topics are not ASJC subject areas, and ADR 0028 Decision 2 keeps them separate. ADR 0017 matches subject areas by ASJC grouping. ADR 0032 must say where a registered record's subject area comes from (owner list).
- **ADR 0033 (to be written):** the Stage 4 `FlowCounts` change, namely the `registered` field and the identity in §3, and the fold's handling of orphaned decisions.

## Questions for the owner before acceptance

1. Confirm the reading of BUILD_PLAN §2.6 in "Context", and that this ADR, ADR 0032 and ADR 0033 are the right split for the Stage 2, Stage 3 and Stage 4 changes.
2. Confirm the amended wording of `S02-AC5`.
3. Confirm that "near match" means exactly the two rules `capture/candidates.py` applies to its review list, and nothing wider.
4. Where does a registered record's subject area come from, given that OpenAlex supplies topics and not ASJC codes? This is for ADR 0032 to settle, and the owner must choose the source.

## Related decisions

- [ADR 0002](0002-append-only-decision-log.md): the append-only pattern, applied here to a human-written registration.
- [ADR 0003](0003-human-only-screening.md): human-only screening. Registration is also a human act, and it is never automatic.
- [ADR 0007](0007-flow-counts-unsure-fields.md), [ADR 0013](0013-identified-sums-across-searches.md) and [ADR 0016](0016-automated-exclusion-reasons.md): earlier changes to the `FlowCounts` contract. ADR 0033 is a further one.
- [ADR 0012](0012-persisting-skipped-layer0-entries.md): the rule that a Layer 1 schema addition needs its own ADR. ADR 0032 applies it.
- [ADR 0017](0017-subject-areas-match-by-asjc-grouping.md): subject areas match by ASJC grouping, which bears on the subject-area source for registered records.
- [ADR 0024](0024-one-implementation-of-append-only-durability.md): the shared `AppendOnlyLog`, which §8 moves below Stage 3 and extends with `append_if`. This ADR amends it.
- [ADR 0027](0027-what-v1-0-0-claims.md): what v1.0.0 claims, which this ADR does not change.
- [ADR 0028](0028-field-level-fallback-cascade.md): the candidate, attachment and provenance rules. This ADR builds on them and amends its Decision 4 closing sentence for registrations. A pointer is recorded under its Resolved item 3.
