# ADR 0036: Errata to ADR 0035, and the Registration-Timing Wording of ADR 0032 and ADR 0033

## Status

Accepted — 2026-10-26, on the merge of the pull request that adds this record, which the owner approves. Until the
merge, this record was `Proposed` on the branch `docs/adr-0036-errata-0035`. Accepting it changes no code, no test and
no number (Decision 9).

ADRs 0001 to 0035 are frozen, so none of [ADR 0032](0032-registered-records-table-and-classification-map.md),
[ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md) or [ADR 0035](0035-gui-registration-of-candidates.md)
is edited. Each keeps its text as written. This record is the place a reader is sent to for the correct statement.

## Context

Several statements in accepted records do not match the code, the tests or the user documents. Each correction below
quotes the old text with its section or line, states what is true, and names the authoritative document. Every
statement was checked against the repository on the branch before it was written. Where a check could not be run, the
correction says so.

### Correction 1: a registration takes effect at `build --rebuild`, not at the next build

**Old text (a).** [ADR 0035](0035-gui-registration-of-candidates.md) §7, the fourth bullet (line 186-187):

> The `registered_records` table updates at the next build (ADR 0032), as with the CLI, and the success message says so.

**Old text (b).** ADR 0035 §11, the reversal integration test (line 264):

> Layer 1 changes only at build (section 7, consequence 5), so

**Old text (c).** ADR 0035 Consequence 5 (line 308-309):

> A registration is not in effect until the next build; the success message says so.

**Old text (d).** [ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md) §1, "What `registered` counts, and
where it comes from" (line 96-98):

> `compute_flow_counts` reads `registrations.jsonl` and its sidecar inside `_capture_snapshot`, on the same connection,
> so the registration count and `S_raw` cannot be read at different moments.

**What is true.** A plain `prismabib build <slug>` reuses an existing store and loads nothing. The CLI prints that it
loaded nothing and points at `--rebuild`. A registration or a reversal reaches Layer 1 (the `registered_records` table,
`FlowCounts.registered`, and the orphaned-decision listing) only after `prismabib build <slug> --rebuild`. Until then the
flow diagram shows the numbers from before the change. The registration log and its checksum sidecar change at once,
when the event is appended.

The `registered` count is read from Layer 1, not from `registrations.jsonl`. `_registered_count` in
`src/prismabib/prisma/flow.py` (lines 346-359) runs `SELECT count(*) FROM registered_records`, on the same connection
as `_capture_snapshot` and before it. No code in `src/prismabib/prisma/engine.py` reads `registrations.jsonl`. So the
count and `S_raw` are both read from the store as last built. They are consistent with each other, and both lag the
log until a rebuild.

No ADR 0032 or ADR 0033 sentence says "at build" in those words. ADR 0032 says that the loader builds
`registered_records` and that it is rebuildable (Decision 1, lines 79-80). That is accurate, and it is not corrected.
The mistaken statement is the source named in (d).

The success messages say the same thing, and the GUI is the only interface that says it. `register` and `reverse` in
`src/prismabib/capture/registration_ui.py` print "It takes effect at the next `prismabib build <slug> --rebuild`" (lines
468-471 and 635-638). The CLI `register` and `reverse` commands print only `registered <record id> (event <event id>)`
and `reversed <event id> (event <event id>)` (`src/prismabib/cli.py`, lines 661 and 683). So ADR 0035's "This is the
CLI's behaviour too" (Consequence 5) is true for timing, and it is not true for the message.

**Authority.** The `--rebuild` option help and the reuse message in `src/prismabib/cli.py` (`build`, lines 434-443 and
591-596). The how-to,
[`docs/how-to/register-and-reverse-candidates.md`](../../how-to/register-and-reverse-candidates.md), "After a change: the
figures move at the next rebuild" (lines 170-192). `docs/methodology/prisma-mapping.md`, the table of symptoms (line 348), which says
that `build` without `--rebuild` loads no run captured since.

### Correction 2: the level 1 to 3 refusal is not reachable through the view

**Old text.** ADR 0035 §11, the refusal test list (line 260):

> `test_view__refusals__surface_the_writer_message`: levels 1 to 3, no DOI, two matching works, duplicate of a Scopus
> record, second active registration for the candidate.

**What is true.** The view has no level control and passes `source_level=4` on every call
(`registration_ui.py` line 462; ADR 0035 §5.4). A level 1 to 3 refusal can therefore not be produced through the view.
The view tests cover the other four refusals, under the names `test_view__refusal_no_doi__…`,
`test_view__refusal_two_matching_works__…`, `test_view__refusal_duplicate_of_scopus_record__…` and
`test_view__second_registration_same_candidate__refused` (`tests/integration/capture/test_registration_view.py`). The
file's own docstring (lines 3-5) says the same.

The writer refuses levels 1 to 3 as a Scopus duplicate, with the message `level N resolves against a Scopus payload`
(`src/prismabib/capture/registrations.py`, lines 503-507). That is tested at writer level in
`tests/integration/capture/test_registrations.py`, as `test_register_candidate__level_one_to_three__is_refused_as_a_scopus_duplicate`
(lines 321-330). That file is the integration file, not the unit file. `tests/unit/capture/test_registrations.py` (lines
213-219) checks only that a stored register event with `source_level` 1, 2, 3 or 5 fails to load. It does not call the
writer.

**Authority.** `src/prismabib/capture/registrations.py` (the refusal) and `tests/integration/capture/test_registrations.py`
(the writer-level test).

### Correction 3: the confirmations are inline text, not a modal step

**Old text (a).** ADR 0035 §3 (line 100):

> A last confirmation step shows DOI, title, year and rule (ADR 0029 section 1) before the call is made.

**Old text (b).** ADR 0035 §4 (line 111-113):

> reads a confirmation that states how many current decisions on that record a reversal would orphan [...] the text
> warns, needs one confirmation and does not forbid.

**What is true.** Neither confirmation is a modal dialog or a second click. Register shows its confirmation text in the
pane beside the button (`confirm_pane`, `registration_ui.py` lines 427-429, built by `register_confirmation_text` at
lines 199-205). The click on Register is the act. Reverse shows the impact warning in the pane beside the button, built
by `reversal_confirmation_text` (lines 254-262), and the click on Reverse is the one confirmation. The owner's decision
of 2026-10-26 (ADR 0035, "Decisions taken by the owner", item 1) asks for exactly that: "needs one confirmation, no
second one".

**Authority.** [`docs/how-to/register-and-reverse-candidates.md`](../../how-to/register-and-reverse-candidates.md), steps
7 and 8 of the Register section (lines 82-85), and the Reverse section, step 5 (lines 154-155): "The click after the
warning is the one confirmation: there is no second dialog."

### Correction 4: the writer's near-match refusal is not reachable from the GUI

**Old text.** ADR 0035 §11, the near-match integration test (line 258-259):

> `test_view__register_near_without_confirmation__refused_and_log_unchanged`: bytes and sidecar equal. *Mutation:* the
> view always passes `confirmed_near=True`.

**What is true.** The refusal `needs the reviewer's confirmation` cannot be reached by a click on Register. The button is
disabled until the reviewer ticks the box (`RegistrationForm._sync_controls`, `registration_ui.py` lines 437-441). The
view test clears the tick and calls `form.register()` directly. That is the case the mutation targets, a view that
passes `confirmed_near=True` regardless of the box. The writer's check is tested at
`tests/integration/capture/test_registrations.py` lines 387-394, and it still applies if the view is driven some other
way. The how-to already says of this refusal: "you should not normally see this" (its refusal list, lines 114-116).

**Authority.** `src/prismabib/capture/registrations.py` (the writer's check) and `registration_ui.py` (the disabled button).

### Correction 5: the alert is a Panel HTML pane with `role="alert"`, not an Alert widget

**Old text.** ADR 0035 §10, the accessibility bullet (line 224-225):

> The alert region is announced (an ARIA `role="alert"` through a Panel `HTML` pane, or an `Alert` widget, whichever the
> pinned Panel version renders with that role; the PR checks the rendered HTML).

**What is true.** The record leaves the choice open, and the implementation made it. The alert is a `pn.pane.HTML` pane
(`registration_ui.py` line 318). It carries `role="alert"` and a `data-attempt` counter (`_alert_html`, lines 268-274).
The counter makes two identical refusals two different objects, so the pane re-renders and a screen reader announces
the second one too. The test `test_view__identical_refusals__render_distinct_alert_objects` checks this. The choice of
the HTML pane follows from a comment in `tests/integration/capture/test_registration_view.py` (lines 255-256), which says
that Panel's `Alert` widget renders no `role` attribute in the pinned version. The pinned version is Panel 1.9.4
(`uv.lock`). This record takes that comment as the source. I did not render Panel to confirm the `Alert` claim in this
session.

**Authority.** `src/prismabib/capture/registration_ui.py` (lines 268-274 and 318) and the rendered-HTML tests in
`tests/integration/capture/test_registration_view.py` (lines 246-258 and 731).

### Correction 6: the CLI paragraph in the how-to

**Old text.** ADR 0035 Consequence 6 (line 310):

> The how-to for the CLI gains a GUI section in the last PR.

**What is true.** No CLI how-to existed. A search of `docs/` for `prismabib register` and `prismabib reverse` found only
[`docs/how-to/register-and-reverse-candidates.md`](../../how-to/register-and-reverse-candidates.md). The how-to was created
by ADR 0035 PR 5 (`CHANGELOG.md`, Unreleased). It has a short section for the command line, "The same writer on the
command line" (lines 194-199). That section says the commands take the same inputs, and that they have no candidate
table, no impact warning and no confirmation text. Nothing needs to change.

**Authority.** `docs/how-to/register-and-reverse-candidates.md`, lines 194-199.

### Correction 7: the OpenAlex `select` citation in ADR 0032

**Old text.** [ADR 0032](0032-registered-records-table-and-classification-map.md) Context (line 64-65):

> **The current OpenAlex select does not carry those fields.** `sources/openalex.py` line 52 sets `_SELECT = "id,doi,title,publication_year,abstract_inverted_index,authorships,topics"`.

**What is true.** The constant is at lines 56-59 of `src/prismabib/sources/openalex.py`, and it carries the three fields
the owner approved: `type`, `primary_location` and `primary_topic`. The sentence was true on 2026-10-22. It is not true
now, and the line number is stale. [ADR 0034](0034-errata-to-adr-0031-and-0033.md) (Consequences 4) named this as stale and
left it uncorrected. This record corrects it.

**Authority.** The `_SELECT` constant in `src/prismabib/sources/openalex.py` (lines 56-59).

### Correction 8: readings that the writer and the view settled

These are not contradictions. Each is a choice the code made where an accepted record left room. This record writes the
choice down as the implemented behaviour, so no reader takes it for an open question. No code changes.

- **Preselection of a rule.** The view preselects a rule only when exactly one of the three rules has at least one
  offered response (`RegistrationForm._on_candidate`, `registration_ui.py` lines 385-391). That is the reading of
  ADR 0035 §3, "It preselects a rule only when exactly one rule applies" (line 89). The pure helper `preselected_rule`
  (lines 163-166) is exported, but no code in `src/` calls it, so the form does not use it.
- **The response is never chosen for the reviewer.** The payload is not auto-selected. The choice is cleared on any
  change of candidate or rule (`_on_change`, lines 377-380). A reload keeps the choice only while the response is still
  offered (lines 405-408). ADR 0035 Alternatives rejects auto-selection: "Registration is a reviewer's act" (line 281-282).
- **Only unregistered candidates are selectable.** A candidate with an active registration is shown in the table but is
  not an option in the Candidate select (`CandidateRowView.selectable`, lines 77-80, and the options filter at lines
  367-372). ADR 0035 §2 (line 73) says it already.
- **The reversal impact is read when the registration is selected.** `reversal_impact` is called in `_on_select`
  (`registration_ui.py` lines 592-608). A reload does not recompute it. A decision made after the selection is therefore
  not in the figure until the reviewer selects the registration again. ADR 0035 §4 says the view "computes nothing itself"
  (line 144), and the figure can be stale in this way.
- **No injected clock or identifier.** ADR 0035 §11 says that "the view's handlers may take injected `now` and `new_id`"
  (line 253-254). They do not. The view calls `register_candidate` and `reverse_registration` with no `now` and no
  `new_id` (`registration_ui.py` lines 455-464 and 625-631), so the writer's defaults apply (`registrations.py` lines
  478-479). Parity with the CLI is therefore equality modulo `event_id`, `ts` and `record_id`, as the parity test shows
  (`tests/integration/capture/test_registration_view.py`, `_events` at lines 73-76). ADR 0035 §5.5 (lines 166-171) already
  states the parity rule.

**Not recorded here.** The PR history of the GUI (one PR, after stacked PRs were superseded) is not stated in this record.
It could not be checked without git or network, and no accepted record states it.

## Decision

1. **Correction 1 (ADR 0035 §7, §11 and Consequence 5; ADR 0033 §1).** A registration or a reversal reaches Layer 1, and so
   `FlowCounts.registered` and the orphaned-decision listing, only after `prismabib build <slug> --rebuild`. The `registered`
   count is read from `registered_records`, not from `registrations.jsonl`. The success messages of the GUI say this. The
   CLI's messages do not repeat it. The authority is the `--rebuild` text in `cli.py` and the how-to.
2. **Correction 2 (ADR 0035 §11).** The writer refuses levels 1 to 3, and a view test cannot reach that refusal. The writer
   test is `tests/integration/capture/test_registrations.py`, not the unit file.
3. **Correction 3 (ADR 0035 §3, §4 and owner decision 1).** The two confirmations are the text beside the button and the
   single click. No modal, no second click.
4. **Correction 4 (ADR 0035 §11).** The near-match refusal is the writer's, and the disabled button keeps the GUI from
   reaching it. The view test drives the refusal by calling the form directly.
5. **Correction 5 (ADR 0035 §10).** The alert is a `pn.pane.HTML` carrying `role="alert"` and a `data-attempt` counter.
   The reason is the `Alert` widget's missing role, as the test comment states.
6. **Correction 6 (ADR 0035 Consequence 6).** The how-to has a CLI section. No separate CLI how-to was needed or existed.
7. **Correction 7 (ADR 0032 Context).** `_SELECT` is at `src/prismabib/sources/openalex.py` lines 56-59, and it carries the
   three registration fields.
8. **Correction 8 (implemented readings).** The preselection rule, the choice clearing, the selectable candidates, the
   impact read at selection, and the absence of injected `now` and `new_id` are recorded as the implemented behaviour.
9. **No behaviour changes.** Nothing in `src/`, `tests/`, the schema, `numbers.json`, the goldens or the flow diagram
   changes. The how-to already states the correct behaviour and is not edited. ADR 0032, ADR 0033 and ADR 0035 are not
   edited, and their text stays as the record of what was accepted.

## Consequences

1. Readers of ADR 0032, ADR 0033 and ADR 0035 are pointed to this record by its number. The dated pointers are here, not
   in the frozen records.
2. `mkdocs.yml` gains a navigation entry for this record, after the entry for ADR 0035, as the ADR 0034 entry does.
3. No CHANGELOG bullet is added. The Unreleased section has no entry for ADR 0034, which is the errata record before this
   one, so the in-repository evidence is that an ADR-only errata record did not add one. The `git log -p -S` check was not
   run, because no shell was available.
4. **Not corrected here.** Two statements that are not corrected by this record were seen during verification.
   (a) [ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md) §3 says that the reason for an orphan is
   "a reversal or a supersession" where one applies. `docs/methodology/prisma-mapping.md` (lines 322-323) says that the
   reason is always "record not in S_raw", because nothing records which event removed the record. The code was not read
   for this point, so it is not decided here. (b) The test names in [ADR 0035](0035-gui-registration-of-candidates.md)
   §11 differ from the implemented names, and the view refusal test is split into four. These are naming differences and
   no rule changes.

## Alternatives considered

**Edit ADR 0032, ADR 0033 and ADR 0035 in place.** Rejected. ADRs 0001 to 0035 are frozen. An erratum record keeps the
original text and the correction side by side, so a reader sees what was accepted and what is true.

**Add a dated pointer note under each affected sentence, as ADR 0032 and ADR 0033 did.** Rejected. Eight corrections
across three records would mean nine or more notes, each needing the others to check. One record that quotes each
sentence is easier to verify.

## Related decisions

- [ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md): the registration and reversal rules the view
  presents and does not change.
- [ADR 0032](0032-registered-records-table-and-classification-map.md): the table and view that a registration reaches at
  build, and the select citation corrected in Correction 7.
- [ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md): the `registered` count corrected in Correction 1.
- [ADR 0034](0034-errata-to-adr-0031-and-0033.md): the earlier errata record, which named Correction 7 and left it open.
- [ADR 0035](0035-gui-registration-of-candidates.md): the view whose wording and tests are corrected here.
- [`docs/how-to/register-and-reverse-candidates.md`](../../how-to/register-and-reverse-candidates.md): the authoritative
  user statement of the rebuild timing and the confirmations.
- [`docs/methodology/prisma-mapping.md`](../../methodology/prisma-mapping.md): the authoritative statement of the
  reversal rule.
