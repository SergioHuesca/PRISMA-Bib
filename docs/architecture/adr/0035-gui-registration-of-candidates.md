# ADR 0035: A GUI for Registering and Reversing Candidate Records

## Status

Accepted — 2026-10-26, on the merge of the pull request that adds this record, which the owner approves. Until the merge, this record was `Proposed` on the branch `docs/adr-0035-gui-registration`. This record adds no code. It decides how a graphical view will sit on the
registration writer that already exists, so that the implementation PRs (Follow-up implementation outline) have nothing left to decide.

It builds on [ADR 0028](0028-field-level-fallback-cascade.md) (candidates), [ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md)
(registration, reversal), [ADR 0031](0031-openalex-responses-are-layer0-runs.md) (sealed OpenAlex responses),
[ADR 0032](0032-registered-records-table-and-classification-map.md) (the derived table) and
[ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md) (the closed reversal reasons, orphaned decisions). It changes
none of their contracts and makes no deviation from the frozen BUILD_PLAN. ADRs 0001 to 0034 are not edited. It follows
[ADR 0034](0034-errata-to-adr-0031-and-0033.md), which corrects two statements in ADR 0031 and ADR 0033.

## Context

**Registration works only from the command line.** Sprint 3 (B1) shipped three commands in `src/prismabib/cli.py`:

- `prismabib register <slug> <candidate_id> --reviewer R --payload openalex/<run>/response-NNNN.jsonl [--match-rule doi|title_year|near_confirmed] [--confirm-near]`
- `prismabib reverse <slug> <event_id> --reviewer R --reason <code> [--note ...]`
- `prismabib registrations <slug>` (read-only list of active registrations)

They call `register_candidate` and `reverse_registration` in `src/prismabib/capture/registrations.py`. Those two functions are the
only writers. Every ADR 0029 section 5 refusal is raised there as `RegistrationRefusal`, and the CLI prints the message, exits
non-zero and writes nothing (`_reporting_errors`).

**A reviewer must handle two opaque strings to use it.** `candidate_id` is a content hash (ADR 0028 Decision 5). The payload is a
path under `raw/` that the reviewer must find by hand. ADR 0029 section 1 says the reviewer "sees the DOI, the title, the year and
the match rule, and confirms them. The confirmation is a UI step". The CLI offers no such view: `--confirm-near` is a flag that can
be typed without looking at the work. The requirement is met in letter by the flag and not in purpose.

**The reviewer already works in a Panel view.** `screener(project, stage=, reviewer=, blind=)` in `src/prismabib/screening/ui.py`
is a notebook cell that `panel serve` also serves (ADR 0004). `Screener` takes its behaviour from `KEY_MAP`, and
`test_keymap__every_binding__maps_to_a_handler` (`tests/integration/screening/test_ui.py`) fails on a binding without a handler, or a
handler without a binding. There is no dashboard module under `src/prismabib`; "dashboard" in the docs means the notebook flow.

**The rules a GUI must not weaken**, all already in code:

- Only level 4 is registrable, with a DOI, and exactly one work must satisfy the rule (ADR 0029 section 1).
- `near_confirmed` needs the reviewer's confirmation in the same call (`confirmed_near`).
- `reason` is one of `REVERSAL_REASONS`; `other` needs a non-empty `note` (ADR 0033 Decision 4).
- The registration module does not import `DecisionLog` and never writes Layer 0 or the Scopus `records` table (ADR 0029 section 5).
- Registration and reversal are `append_if` appends under the log's exclusive lock (ADR 0029 section 8, ADR 0024).

## Decision

**Build a separate Panel view, `registration_view(project, *, reviewer)`, next to `screener()`. It is a thin form over the existing
writer functions, which it calls and does not re-implement. It has no global key bindings, and `KEY_MAP` does not change.**

### 1. Where it lives

| Option | For | Against |
|---|---|---|
| A. A tab inside `Screener` | One window | `Screener` is the blind, keyboard-first, per-record view with bias controls (ADR 0004; the `ui.py` module docstring). Registration is a different task on a different object (a candidate, not a queue item). The document-level `keydown` listener (`keymap_javascript`) would fire while the reviewer types a note. `Screener` is built over a `ScreeningQueue`, which registration does not have. |
| B. A new dashboard application | Could also show flow counts | None exists. It would be a new deployment surface for a three-form task. |
| C. A separate Panel view with its own factory | Same hosting as `screener()` (notebook or `panel serve`); no coupling to the queue | Two things to open. |

**Recommendation: C.** Module `src/prismabib/capture/registration_ui.py`, beside `registrations.py`, which it wraps. It is not under
`screening/`, because registration is not screening and creates no decision. The factory follows `screener()`:
`registration_view(project: Project, *, reviewer: str) -> pn.viewable.Viewable`, ending in `.servable()`. It has three sections:
Register, Reverse, Active registrations. As in `ui.py`, everything assertable (the candidate rows, the match table, the reason options,
the confirmation text) is a pure function over plain data, and the widgets are built from those.

### 2. How a reviewer picks a candidate and a captured payload

- **Candidate.** A table from `CandidateLog.view()` (the call the writer uses), showing title, year, effective DOI, the pasting
  reviewer, the paste time, and the state: unregistered, or registered (with its `record_id`). The reviewer selects a row;
  `candidate_id` is read from the row and never typed. A candidate with an active registration is shown and cannot be selected for Register.
- **Payload.** A list of the sealed OpenAlex responses under `raw/openalex/<run>/` (sealed runs only, by `is_sealed`), each labelled
  `<run>/response-NNNN` with its request's `api_view` and `http_status`. For the selected candidate the view evaluates each
  response with the writer's own matching, and offers only the responses where **exactly one** work satisfies a rule. It also shows a count of
  responses with none or several, so "nothing found" is visible and explained and not silent. The string handed to the writer is the
  same `openalex/<run>/response-NNNN.jsonl` the CLI takes.
- **One shared matching function.** The matching in `_satisfying` (`registrations.py`) is private today. The first PR makes it a public
  pure function, `matching_works(candidate, rule, works)`, and `register_candidate` calls that name. The view calls the same function,
  so what the form shows as a match and what the writer accepts are one function. Listing responses only reads Layer 0 (manifests, and
  bodies through `read_response`).

### 3. Match rules and the near-match confirmation

After a response is chosen, the view shows the single satisfying work for each rule (`doi`, `title_year`, `near_confirmed`), in that
order. It preselects a rule only when exactly one rule applies. For the chosen work it shows side by side the candidate's title, year
and DOI, the work's title, year and DOI, and the rule by name with its one-line definition from ADR 0029 section 1.

For `near_confirmed` the view states which near rule fired (same normalised title with a different year, or one title a whole-word
prefix of the other) and shows the two titles and years. Register is enabled only after the reviewer ticks "I confirm this work is
this candidate". That checkbox is the only source of `confirmed_near=True`. It is cleared whenever the selection changes, and it is not
shown for `doi` and `title_year`. The writer refuses `near_confirmed` without it, so the GUI is a convenience and the rule stays in the
writer. A last confirmation step shows DOI, title, year and rule (ADR 0029 section 1) before the call is made.

### 4. The reversal flow

The Reverse section lists `active_registrations(load_registration_events(...))` (as the `registrations` command does): record id,
DOI, registering reviewer, time. The reviewer selects one, then:

- picks a **reason** from a single-select whose options are `REVERSAL_REASONS`, imported and never copied, so a new code reaches both
  interfaces together;
- enters a **note**, a text area that is marked required when the reason is `other` (the button stays disabled while it is empty or
  blank) and is optional otherwise;
- reads a confirmation that states how many current decisions on that record will become orphaned (ADR 0033 Decision 3) and whether the
  paper is currently included, using the existing orphan-listing code read-only. Reversal stays allowed after decisions exist
  (ADR 0029 section 5); the text warns and does not forbid.

On confirm the view calls `reverse_registration`. Correcting a wrong DOI or title is the existing reversal followed by a new
registration, and the view says so beside the reason list. There is no edit action and no delete.

### 5. One code path: how the GUI cannot diverge from the CLI

1. The view calls `register_candidate` and `reverse_registration` and no other writer. It never constructs `RegisterEvent`,
   `ReverseEvent` or `RegistrationRecord`, never touches `AppendOnlyLog`, and never calls `write_event` or `append_if`.
2. The CLI commands and the view pass the same keyword arguments. Neither adds a rule the other lacks. A rule that matters belongs in
   the writer, as today.
3. A test enforces the boundary structurally: parsing `registration_ui.py` with `ast`, the only write-capable names it imports from
   `prismabib.capture.registrations` are the two functions, and it imports neither `DecisionLog` nor `AppendOnlyLog`. (The ADR 0029
   no-`DecisionLog` test is extended to the new module.)
4. A parity test drives the same accepted and refused scenarios through the CLI (`CliRunner`) and through the view's handler
   functions, and asserts equal `registrations.jsonl` content. The writer takes injected `now` and `new_id`, so the whole event is equal, not only part of it.

### 6. Refusals and errors

`RegistrationRefusal` carries the user-facing reason. The view catches `PrismabibError` as `_reporting_errors` does and shows
`str(exc)` in a persistent alert region that stays until the next action. An exception that is not a `PrismabibError` propagates, as in
the CLI. After any refusal the view re-reads the log, so the display matches the file, and a test asserts the file's bytes and its
sidecar are unchanged (the ADR 0029 "a refused attempt writes nothing" row). On success the view shows the new `record_id` and
`event_id`, as the CLI prints them, and refreshes its lists. The view does not retry, queue or update optimistically.

### 7. Layer 0, append-only, and the other guarantees

- No write to Layer 0. The view reads sealed runs. A test asserts `raw/` is byte-identical before and after, through the view.
- No write to `decisions.jsonl` or to the `records` table, and no screening decision is created.
- Registration events are append-only. The view has no edit or delete control, and a reversal appends a `reverse` event.
- The view does not run the loader or a build. The `registered_records` table updates at the next build (ADR 0032), as with the CLI,
  and the success message says so.
- No network. The view reads captured responses only; capturing an OpenAlex response stays the existing step (ADR 0031).

### 8. Reviewer identity

`reviewer` is a required factory argument, as in `screener()`. A blank value raises `ValidationError` at construction. It is passed to
every writer call, and the writer's own check stays the enforcement. The view does not authenticate: like the CLI's `--reviewer` and
the screener, identity is a name the reviewer asserts, not a login. The view shows the name in its header so a reviewer sees under whose
name an event will be written. A reversal records the reversing reviewer, who may differ from the one who registered.

### 9. Keyboard map (`KEY_MAP`) impact

**None, by decision.** `KEY_MAP` keeps `i e u n p z ?` and the reason digits. Reasons:

- The `KEY_MAP` listener is document-level and capturing. Registration has free-text input (the note), so a global single-letter key
  would act while the reviewer types.
- Registration is rare and screening is frequent. A single-key irreversible write buys nothing here, and a stray keypress that
  registers a record is a worse failure than a slow form.
- The two views are separate pages, so no key collides.

The registration view uses native form controls only (Tab, Shift+Tab, Space, Enter, arrows in a select), which need no listener. The
`KEY_MAP` dead-key test is therefore untouched. A new test, `test_keymap__registration_actions__not_bound`, asserts that no `KEY_MAP` action
name starts with `register`, `reverse` or `registration`, so a later PR cannot add one by accident. Shortcuts, if wanted later, need a new
ADR and their own dead-key test over their own map.

### 10. Accessibility

- Every control has a visible text label and a programmatic one (Panel `name`). Colour never carries meaning alone: a refusal begins
  with the word "Refused" and success with "Registered".
- The alert region is announced (an ARIA `role="alert"` through a Panel `HTML` pane, or an `Alert` widget, whichever the pinned Panel
  version renders with that role; the PR checks the rendered HTML). The confirmation step is text, not a tooltip.
- Controls follow reading order, so Tab order is correct without `tabindex` values. Every action is a button reachable by keyboard.
- Tables expose their data as text (`<run>/response-NNNN` and counts), not as an image or colour chip.
- Scope of the claim: a unit test checks that each widget has a non-empty `name`. The alert uses `role="alert"`, asserted on the
  rendered HTML. The owner decided that no manual screen-reader pass is required. This record does not claim WCAG conformance.

### 11. Test strategy

Each test below is written first and shown to fail against the named mutation, which the PR states (the project rule that a covering test
proves nothing until it fails on the original defect).

Unit (pure functions, no Panel server):

- `test_matching_works__same_result_as_writer`: a table of (candidate, rule, works) cases agrees with what `register_candidate` accepts
  or refuses. *Mutation:* `title_match` ignores the year.
- `test_response_options__only_exactly_one_match_listed`: zero, one and two satisfying works. *Mutation:* `== 1` becomes `>= 1`.
- `test_reason_options__equal_REVERSAL_REASONS`. *Mutation:* add a code in the view only.
- `test_note_required__only_for_other`: the can-submit predicate per code, with empty and whitespace notes. *Mutation:* drop `.strip()`.
- `test_near_confirmation__cleared_on_selection_change` and `test_near_confirmation__not_offered_for_doi`.
- `test_view__imports_only_the_two_writers` (section 5.3). *Mutation:* import `registration_log` in the view.
- `test_keymap__registration_actions__not_bound` (section 9).
- `test_widgets__all_labelled`.

Integration (real project fixture under `tmp_path`, injected `now` and `new_id`, no network):

- `test_view__register_doi_match__appends_event_equal_to_cli`: the parity test (section 5.4). *Mutation:* the view always passes
  `match_rule="doi"`.
- `test_view__register_near_without_confirmation__refused_and_log_unchanged`: bytes and sidecar equal. *Mutation:* the view always passes
  `confirmed_near=True`.
- `test_view__refusals__surface_the_writer_message`: levels 1 to 3, no DOI, two matching works, duplicate of a Scopus record, second
  active registration for the candidate. Each shows the exact refusal text and writes nothing.
- `test_view__reverse_other_without_note__refused_and_log_unchanged` and `test_view__reverse_each_code__appends_reverse_event`.
- `test_view__reverse_after_decision__orphan_count_shown_then_reversed`: the confirmation names the count, the decision stays in
  `decisions.jsonl`, and `compute_flow_counts` shows `registered` one lower.
- `test_view__register_and_reverse__raw_and_decisions_byte_identical`.
- `test_view__second_registration_same_candidate__refused`.

All run single-process and under `pytest -n auto`. Panel is exercised headlessly through handler functions, not a browser, in line
with the `ui.py` rule against assertion-free render tests.

## Alternatives considered

- **Extend `Screener` with a registration tab (option A).** Rejected, for the keyboard reasons in section 9 and because it mixes a blind
  per-record queue with a candidate workflow.
- **Keep the CLI only.** Rejected. It leaves ADR 0029's "reviewer sees DOI, title, year and rule and confirms" unmet in practice and
  makes the near-match confirmation a flag typed blind.
- **A GUI-specific writer that takes a candidate object instead of a payload path.** Rejected. It is the second code path this record
  exists to prevent.
- **Auto-select the matching payload.** Rejected. Registration is a reviewer's act (ADR 0029 section 1); the view narrows the choice and
  the reviewer still makes it.
- **Global shortcuts for registration.** Rejected for now (section 9).
- **Run the loader after a registration so the table updates at once.** Rejected. The loader is the only Layer 1 writer (ADR 0028
  Decision 3) and runs at build; tying it to a button moves a slow multi-file write behind a form.

## Non-goals

- No automated access to Google Scholar, by any means. Candidates are still pasted by a person (ADR 0028 Decision 5); the view does not
  scrape, fetch or open it.
- No new record fields, no new event kinds, no change to `RegisterEvent`, `ReverseEvent` or `SupersedeEvent`, no schema change.
- No change to the writer's rules, the closed reversal reasons, the `FlowCounts` contract or `numbers.json`.
- No batch registration and no edit in place (ADR 0029 section 1).
- No candidate paste and no OpenAlex capture inside this view; both stay in their existing steps.
- No authentication or per-user permissions (section 8).
- No `supersede` control; only the build step writes that event (ADR 0029 section 6).
- No keyboard shortcuts (section 9).

## Consequences

1. A reviewer can register and reverse without reading a hash or a path, and sees the evidence ADR 0029 requires before the write.
2. `matching_works` becomes a public name in `capture/registrations.py` (a rename of a private helper and one call site). It is the only
   change to existing code outside new files, and it preserves behaviour.
3. Panel stays the only UI technology (ADR 0004). The new module falls under the `ui.py` rule on assertion-free render tests.
4. The CLI stays supported as the scripting and CI path. The parity test covers both interfaces, so a later change to the writer is
   tested through both.
5. A registration is not in effect until the next build; the success message says so. A reviewer who reads the flow diagram straight
   after registering sees the old numbers. This is the CLI's behaviour too.
6. `docs/methodology/prisma-mapping.md` describes registration by its rules and needs no change. The how-to for the CLI gains a GUI section in the last PR.
7. Identity is a typed name, so two people can register under one name. That is existing behaviour and is not made worse here.

## Decisions taken by the owner

Decided on 2026-10-26, before acceptance:

1. Reversal in the GUI of a paper that is currently `included` shows a warning with the orphaned-decision count and needs one
   confirmation, no second one. Reversal is an allowed reviewer act (ADR 0033).
2. The module lives in `capture/registration_ui.py`, next to the writer.
3. Accessibility is checked by the labelled-widget unit test and the rendered `role="alert"` assertion. No manual screen-reader
   pass is required.

## Follow-up implementation outline

Small PRs, each green on its own, in this order:

1. **Shared matching.** Rename `_satisfying` to the public `matching_works`; add `test_matching_works__same_result_as_writer`. No behaviour
   change; the existing CLI tests pass untouched.
2. **Pure view models.** `capture/registration_ui.py`: candidate rows, response options (sealed runs, exactly-one filter), match table,
   near-confirmation state, reason options, note predicate, confirmation text. Unit tests with mutation checks. No Panel yet.
3. **Register form.** The Panel widgets and `registration_view` for Register, calling `register_candidate`; refusal surfacing; the
   integration and parity tests; the `ast` import-boundary test; the `KEY_MAP` guard test.
4. **Reverse and list.** The Reverse and Active registrations sections, the orphan-count confirmation, and their tests.
5. **Docs.** How-to section, accessibility checklist, `docs/getting-started.md` row, a dated note under this record when it becomes
   Accepted, and `.claude/PROGRESS.md`.

## Verification the implementation must include

The tests in Decision 11, each shown to fail on its named mutation, run under `pytest -n auto` and single-process, with `mypy --strict`
clean on `src/prismabib`. The existing registration tests (`tests/unit/capture/test_registrations.py`,
`tests/integration/capture/test_registrations.py`) pass unchanged, and `test_keymap__every_binding__maps_to_a_handler` is untouched.

## Related decisions

- [ADR 0004](0004-panel-for-in-notebook-ui.md): Panel, and the notebook-or-serve factory shape.
- [ADR 0024](0024-one-implementation-of-append-only-durability.md): the single append-only implementation the writer uses.
- [ADR 0028](0028-field-level-fallback-cascade.md): candidates and the cascade levels.
- [ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md): the rules the view presents and does not change.
- [ADR 0031](0031-openalex-responses-are-layer0-runs.md): the sealed responses the reviewer picks from.
- [ADR 0032](0032-registered-records-table-and-classification-map.md): the derived table updated at build.
- [ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md): the closed reversal reasons and orphaned decisions.
