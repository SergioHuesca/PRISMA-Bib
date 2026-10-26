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
handler without a binding.

**A dashboard also exists.** `src/prismabib/viz/dashboard.py` is the Stage 9 Panel dashboard, `dashboard(project, *, corpus=None)`,
with the same notebook-or-`panel serve` contract as `screener()`. It has tabs (Overview, PRISMA, Trends and others) built on a
read-only `Corpus` and on `compute_flow_counts`. It reads Layer 1 and the logs and writes nothing.

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
| A. A tab inside `Screener` | One window | `Screener` is the blind, keyboard-first, per-record view with bias controls (ADR 0004; the `ui.py` module docstring). Registration is a different task on a different object (a candidate, not a queue item). `Screener` is built over a `ScreeningQueue`, which registration does not have. Its page-global `keydown` listener (`keymap_javascript`) would also sit on a page whose buttons write irreversible events (section 9). |
| B. A tab in the existing `viz/dashboard.py` dashboard | The PRISMA tab already shows `registered`, so a reviewer sees the effect beside the action; one place for the project | The dashboard is read-only by construction: it is built on a read-only `Corpus` and renders results. A write tab would break that property, and every dashboard test and review would need to re-establish it. Registration writes events that feed Layer 1 at the next build, so the dashboard's numbers would not change when the reviewer acts (consequence 5), which is confusing beside a write control. It also loads a whole corpus to show a form that needs only the candidate log and Layer 0 manifests. |
| C. A separate Panel view with its own factory | Same hosting as `screener()` and `dashboard()` (notebook or `panel serve`); no coupling to the queue or the read-only corpus; the read-only and write surfaces stay separate | Two things to open. |

**Recommendation: C.** The deciding reason is the read/write split: `dashboard()` is the read-only reporting surface and `screener()` is the
decision-writing surface, and registration is a third kind of write (Layer 1-feeding events) that belongs to neither. Module `src/prismabib/capture/registration_ui.py`, beside `registrations.py`, which it wraps. It is not under
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
- **One shared matching function.** The matching in `_satisfying(candidate, rule: str, works)` (`registrations.py` line 421) is
  private today and has exactly one call site, in `register_candidate` (line 521). PR 1 renames it to the public `matching_works`
  and adds it to `__all__`; `mypy --strict` then checks every call site. Before the rename, PR 1 greps `src` and `tests` for
  `_satisfying` (today only the definition and that call site match). The view calls the same function, so what the form shows as a
  match and what the writer accepts are one function. Listing responses only reads Layer 0 (manifests, and bodies through
  `read_response`).

### 3. Match rules and the near-match confirmation

After a response is chosen, the view shows the single satisfying work for each rule (`doi`, `title_year`, `near_confirmed`), in that
order. It preselects a rule only when exactly one rule applies. For the chosen work it shows side by side the candidate's title, year
and DOI, the work's title, year and DOI, and the rule by name with its one-line definition from ADR 0029 section 1.

For `near_confirmed` the view states which near rule fired (same normalised title with a different year, or one title a whole-word
prefix of the other) and shows the two titles and years. `near_matches` (`capture/candidates.py` line 468) returns only
`(title, year)` pairs and does not say which rule fired, and `matching_works` returns works, not reasons. PR 1 therefore also adds one
small pure function, `near_rule(candidate, title, year)`, returning `"same_title_other_year"`, `"title_prefix"` or `None`, and rewrites
`near_matches` as a filter over it so the two cannot disagree. A test asserts that `near_matches` and `near_rule` agree on a table of
cases. Register is enabled only after the reviewer ticks "I confirm this work is
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
- reads a confirmation that states how many current decisions on that record a reversal would orphan (ADR 0033 Decision 3) and
  whether the paper is currently included. Reversal stays allowed after decisions exist (ADR 0029 section 5); the text warns, needs one
  confirmation and does not forbid.

**The reversal-impact helper.** The existing `orphaned_decisions` and `project_orphaned_decisions` (`prisma/orphans.py`) are
retrospective: they list decisions whose record is already outside `S_raw`, so before the reversal they return nothing for this
record. The view needs a prospective figure, so this record adds one named read-only function in `prisma/`, in a new module
`prisma/impact.py`:

```python
@dataclass(frozen=True)
class ReversalImpact:
    record_id: str
    currently_included: bool  # as defined below
    decisions_orphaned: int  # current decisions that a reversal would orphan


def reversal_impact(project: Project, record_id: str) -> ReversalImpact: ...
```

- `decisions_orphaned` is the number of entries in `DecisionLog(project).fold()` whose `record_id` is the argument: the current
  decision per `(stage, record_id, reviewer)`, which is exactly what `orphaned_decisions` would list once the record left `S_raw`.
- `currently_included` is true when the record is in `M_full`, the set whose size is `FlowCounts.included` (`included =
  len(snapshot.manual_fulltext)`, `prisma/flow.py` line 580), computed under the same fold and aggregation `compute_flow_counts` uses.
  The source is `manual_fulltext_set(project) -> frozenset[str]` in `prisma/engine.py` (line 1025), which reads the same snapshot as
  `compute_flow_counts`; `currently_included` is `record_id in manual_fulltext_set(project)`. `reversal_impact` reads the decision fold
  and that set as two reads, which is harmless: it is a warning figure, and neither read writes. A test asserts that the number of `currently_included` records equals `FlowCounts.included` on a fixture, so the helper cannot
  drift from the diagram. An `unsure` or excluded record is not included.
- It only reads: it folds `decisions.jsonl` without appending, opens Layer 1 read-only, and creates no file (like
  `project_orphaned_decisions`, a project without `decisions.jsonl` yields zero and creates nothing).
- If the helper raises `StoreError` (no Layer 1 store) or `ConfigError` (unreadable criteria), the view shows the error in the
  persistent alert (section 6) and offers no impact figure. It never shows a guessed or zero count. Whether the reversal button stays
  enabled then is the reviewer's choice: the writer does not need the store, and the message says the impact is unknown.
- The view shows these two figures from the helper and computes nothing itself.

On confirm the view calls `reverse_registration`. Correcting a wrong DOI or title is the existing reversal followed by a new
registration, and the view says so beside the reason list. There is no edit action and no delete.

### 5. One code path: how the GUI cannot diverge from the CLI

1. The view calls `register_candidate` and `reverse_registration` and no other writer. It never constructs `RegisterEvent`,
   `ReverseEvent` or `RegistrationRecord`, never touches `AppendOnlyLog`, and never calls `write_event` or `append_if`.
2. The CLI commands and the view pass the same keyword arguments. Neither adds a rule the other lacks. A rule that matters belongs in
   the writer, as today.
3. A test enforces the boundary structurally: parsing `registration_ui.py` with `ast`, the only names it may import from
   `prismabib.capture.registrations` that can write are the two functions, and the only name it may import from `prismabib.prisma` that
   touches the decision log is `reversal_impact`. It imports neither `DecisionLog` nor `AppendOnlyLog`. `reversal_impact` is read-only
   (section 4), and its module is the only new place that reads `DecisionLog`.
   - The existing check `test_registrations_module__source__never_imports_decisionlog_or_calls_write_event`
     (`tests/unit/capture/test_registrations.py`) reads the raw source of `registrations.py` alone (`registrations.__file__`) and
     asserts the substring `DecisionLog` is absent. It stays valid and unchanged, because `registrations.py` imports nothing from
     `prisma/impact.py`. It does not cover `registration_ui.py`; the `ast` test above does, with the one allowance.
4. The view passes `source_level=4`, which is the writer's default and the only registrable level (ADR 0029 section 1; the sealed OpenAlex
   response is a level-4 source, ADR 0031). The view has no control for the level.
5. A parity test drives the same accepted and refused scenarios through the CLI (`CliRunner`) and through the view's handler
   functions on two identical copies of a project. The CLI commands pass no `now` or `new_id`, and the writer binds its defaults
   (`now=_utc_now`, `new_id=_default_ids`) at definition time, so injection through the CLI is not possible and the test does not
   rely on it. It reads each event back from the log, as `tests/integration/capture/test_registrations_cli.py` does, and asserts the
   events are equal after setting `event_id`, `ts` and `record_id` (which is `registered:` plus `event_id`) aside, and that for a
   `reverse` event `reverses` names the corresponding register event of its own project. Refused scenarios assert equal refusal
   text and unchanged bytes and sidecar.

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

- Registration is not a screening action. `KEY_MAP` is the screening view's single source of truth for what each key does
  (`ui.py` module docstring), and registration actions do not belong in it. The listener already ignores key presses whose target is an
  `input`, `textarea`, `select` or content-editable element, so typing a note is safe, but it is page-global and still acts on a
  keypress when focus is on a button or the page body. Any registration binding would be a single key that writes an irreversible event
  from those positions.
- Registration is rare and screening is frequent. A single-key irreversible write buys nothing here, and a stray keypress that
  registers a record is a worse failure than a slow form.
- The registration view binds no keys. In a notebook, though, `screener()` installs a document-level capture listener
  (`window.__prismabib_screener_keydown`, `keymap_javascript`) that stays active on the whole page, including a cell that holds
  `registration_view`. With focus on a Register or Reverse button, pressing `i`, `e`, `u`, `n`, `p` or `z` could fire a screener action.
  **Residual risk:** a notebook that runs both views is exposed to this. Run them in separate notebooks or browser tabs (under
  `panel serve`, each is its own page and the listener is not shared). The how-to says so (PR 5). This record does not change the
  screener's listener.

The registration view uses native form controls only (Tab, Shift+Tab, Space, Enter, arrows in a select), which need no listener. The
`KEY_MAP` dead-key test, `test_keymap__every_binding__maps_to_a_handler`, is therefore untouched. A new test, `test_keymap__registration_actions__not_bound`, asserts that no `KEY_MAP` action
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
- `test_view__imports_only_the_two_writers_and_reversal_impact` (section 5.3). *Mutation:* import `registration_log` or `DecisionLog` in the view.
- `test_near_rule__agrees_with_near_matches` (section 3). *Mutation:* change one branch of `near_rule` only.
- `test_reversal_impact__counts_current_decisions_and_included`: `decisions_orphaned` equals the number of current decisions on the
  record (an earlier superseded event is not counted); `currently_included` is true for a record in `M_full` and false for an `unsure`
  or excluded one; the count of included records equals `FlowCounts.included`; the call appends nothing and creates no file.
  *Mutation:* count every event instead of the folded ones.
- `test_keymap__registration_actions__not_bound` (section 9).
- `test_widgets__all_labelled`.

Integration (real project fixture under `tmp_path`, no network; the view's handlers may take injected `now` and `new_id`, the CLI
cannot, so parity is by equality modulo `event_id`, `ts` and `record_id`, section 5.5):

- `test_view__register_doi_match__appends_event_equal_to_cli`: the parity test (section 5.5). *Mutation:* the view always passes
  `match_rule="doi"`.
- `test_view__register_near_without_confirmation__refused_and_log_unchanged`: bytes and sidecar equal. *Mutation:* the view always passes
  `confirmed_near=True`.
- `test_view__refusals__surface_the_writer_message`: levels 1 to 3, no DOI, two matching works, duplicate of a Scopus record, second
  active registration for the candidate. Each shows the exact refusal text and writes nothing.
- `test_view__reverse_other_without_note__refused_and_log_unchanged` and `test_view__reverse_each_code__appends_reverse_event`.
- `test_view__reverse_after_decision__orphan_count_shown_then_reversed`: the confirmation, before the reversal, names the count from
  `reversal_impact` and says whether the paper is included; after it the decision stays in `decisions.jsonl`. Layer 1 changes only at build (section 7, consequence 5), so
  the test rebuilds the store (`build_store`) between the reversal and the remaining assertions: that the helper's count equals what
  `project_orphaned_decisions` then lists, and that `compute_flow_counts` shows `registered` one lower.
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
2. `matching_works` becomes a public name in `capture/registrations.py` (a rename of a private helper and its one call site), and
   `near_matches` is rewritten as a filter over the new `near_rule`. These are the only changes to existing code outside new files, and
   both preserve behaviour. The new files are `capture/registration_ui.py` and `prisma/impact.py`.
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

1. **Shared matching.** Grep `src` and `tests` for `_satisfying`; rename it to the public `matching_works` and export it; add
   `near_rule` and rewrite `near_matches` over it; add `test_matching_works__same_result_as_writer` and
   `test_near_rule__agrees_with_near_matches`. No behaviour change; the existing CLI tests pass untouched.
2. **Pure view models and the impact helper.** `prisma/impact.py` (`reversal_impact`, over the existing `manual_fulltext_set`) and
   `capture/registration_ui.py`: candidate rows, response options (sealed runs, exactly-one filter), match table, near-confirmation
   state, reason options, note predicate, confirmation text. Unit tests with mutation checks. No Panel yet.
3. **Register form.** The Panel widgets and `registration_view` for Register, calling `register_candidate` with `source_level=4`;
   refusal surfacing; the integration and parity tests; the `ast` import-boundary test; the `KEY_MAP` guard test.
4. **Reverse and list.** The Reverse and Active registrations sections, the confirmation fed by `reversal_impact`, and their tests.
5. **Docs.** How-to section, accessibility checklist, `docs/getting-started.md` row, the separate-notebooks warning (section 9), and
   `.claude/PROGRESS.md`. This record is already Accepted at the merge that adds it, so no later status note is needed.

## Verification the implementation must include

The tests in Decision 11, each shown to fail on its named mutation, run under `pytest -n auto` and single-process, with `mypy --strict`
clean on `src/prismabib`. The existing registration tests (`tests/unit/capture/test_registrations.py`, which holds the `DecisionLog` source check, and
`tests/integration/capture/test_registrations.py`) pass unchanged, and `test_keymap__every_binding__maps_to_a_handler` is untouched.

## Related decisions

- [ADR 0004](0004-panel-for-in-notebook-ui.md): Panel, and the notebook-or-serve factory shape.
- [ADR 0024](0024-one-implementation-of-append-only-durability.md): the single append-only implementation the writer uses.
- [ADR 0028](0028-field-level-fallback-cascade.md): candidates and the cascade levels.
- [ADR 0029](0029-reviewer-registration-of-a-candidate-as-a-record.md): the rules the view presents and does not change.
- [ADR 0031](0031-openalex-responses-are-layer0-runs.md): the sealed responses the reviewer picks from.
- [ADR 0032](0032-registered-records-table-and-classification-map.md): the derived table updated at build.
- [ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md): the closed reversal reasons and orphaned decisions.
