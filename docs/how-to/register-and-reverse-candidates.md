# Register and Reverse a Candidate

How to register a pasted candidate as a record, and how to reverse a registration you
made in error, from a notebook with the registration view. The view is a form over the
same writer the command line uses (`prismabib register`, `reverse` and `registrations`).
It adds no rule of its own: what it refuses, the command line refuses too, and nothing
is written in either case.

The rules behind every step are in [ADR 0029](../architecture/adr/0029-reviewer-registration-of-a-candidate-as-a-record.md)
(registration and reversal) and [ADR 0035](../architecture/adr/0035-gui-registration-of-candidates.md)
(this view).

## Before you start

- **A pasted candidate.** The view lists the candidates in `decisions/candidates.jsonl`
  ([ADR 0028](../architecture/adr/0028-field-level-fallback-cascade.md)). It does not paste
  them: a person pastes each candidate, as ADR 0028 Decision 5 requires.
- **A sealed OpenAlex response that contains the paper.** The view reads the sealed runs
  under `raw/openalex/` ([ADR 0031](../architecture/adr/0031-openalex-responses-are-layer0-runs.md)).
  Capturing one needs the network and stays its own step, `capture_openalex(project, ...)`;
  the view never fetches anything. A capture made before the `type`, `primary_location` and
  `primary_topic` fields were requested cannot back a registration. Recapture those
  identifiers with `refresh=True`.
- **A built Layer 1 store**, for two checks. Without `uv run prismabib build <slug>` having
  run once, the duplicate-of-Scopus check cannot run, and the reversal warning reads
  "impact unknown" (see [Reverse a registration](#reverse-a-registration)).

## Open the view

```python
from prismabib.capture.registration_ui import registration_view
from prismabib.project import Project

project = Project.open("my-review")
registration_view(project, reviewer="sh")
```

Run the cell in a notebook. `reviewer` is required. A blank name raises `ValidationError`
when the view is built. The name appears in the header, so you can see under whose name an
event will be written. It is a name you type, not a login: as with the command line's
`--reviewer`, two people can type the same name.

The view has three sections, in this order: **Register a candidate**, **Reverse a
registration** and **Active registrations**. Every event the view writes carries the name in
its header. A reversal is therefore recorded under the reviewer who reverses, which can differ
from the reviewer who registered; to reverse under another name, open the view with that name.

## Register a candidate

1. **Read the candidates table.** It shows title, year, DOI, who pasted the candidate and
   when, and a state: `unregistered`, or `registered (<record id>)`. Only unregistered
   candidates appear in the **Candidate** select, so a candidate with an active
   registration cannot be selected for Register.
2. **Pick the candidate** from **Candidate**. You never type or see its identifier.
3. **Pick the match rule** from **Match rule**. The view preselects a rule only when exactly
   one of the three rules has a satisfying response. Otherwise you choose.

   | Rule | When it applies (ADR 0029 section 1) |
   | --- | --- |
   | `doi` | the work's DOI equals the candidate's DOI |
   | `title_year` | the candidate has no DOI; the normalised title and the year are equal |
   | `near_confirmed` | same normalised title with a different year, or one title a whole-word prefix of the other; needs your confirmation |

4. **Pick the captured OpenAlex response** from **Captured OpenAlex response**. Each entry
   reads `<run>/response-NNNN (<api view>, HTTP <status>)`. The list holds only responses
   in which **exactly one** work satisfies the chosen rule. If there is only one, the view
   selects it for you.

   Responses that were not offered are counted, not hidden. The line under the select
   reads, for example, `2 response(s) offered; 1 with no work or several under this rule; 0
   unreadable.` A response is unreadable when the writer would refuse to read it: a status
   other than 200, an older field selection, or a checksum that does not match.

5. **Check the match evidence.** The view shows the rule and its definition, then a table of
   the candidate and the work side by side, row by row: title, year and DOI.
6. **Near matches only: confirm.** For `near_confirmed` the view names the near rule that
   fired, `same normalised title, different year` or `one title is a whole-word prefix of the
   other, same year`. A checkbox then appears: **I confirm this work is this candidate**.
   Register stays disabled until you tick it. Changing the candidate, the response or the
   rule clears the tick. The checkbox is never offered for `doi` or `title_year`.
7. **Read the confirmation text**, which is the last step before the write. It reads
   `Register <DOI>: <title> (<year>) under rule <rule>.`, with the near-match wording added
   when it applies.
8. **Click Register.** Success shows, in the alert region:

   > Registered registered:<event id> (event <event id>). It takes effect at the next build.

   The candidates table then shows the candidate as `registered (...)`, and it leaves the
   Candidate select. The Reverse section gains the new registration at once.

The view passes `source_level=4` on every call. Only level 4 (an OpenAlex work) is
registrable, and the view has no control for the level.

### What a refusal looks like

A refusal appears in the same alert region, which is announced as an alert. It begins with
`Refused:` and then carries the writer's own message, unchanged. Nothing is written: the
registrations file keeps the same bytes and the same checksum sidecar. The view then re-reads
the files, so what you see matches what is on disk.

The messages you are most likely to meet, with the placeholders the writer fills in:

- `Refused: candidate '<candidate id>' is unresolved under 'doi': 0 works in
  'openalex/<run>/response-NNNN.jsonl' satisfy it (exactly one is required)`. The response you
  picked no longer contains exactly one matching work, for example because it was replaced
  under the open form.
- `Refused: the paper is already a Scopus record (<Scopus record id>); a registration would
  duplicate it`. This check reads the Layer 1 store as last built.
- `Refused: candidate '<candidate id>' already has an active registration (registered:<event id>)`.
  The same candidate was registered elsewhere, for example from another tab or the command
  line, while this form was open.
- `Refused: the paper already has an active registration (registered:<event id>)`.
- `Refused: a near match needs the reviewer's confirmation`. The Register button is disabled
  in that state, so you should not normally see this. It is the writer's own check, and it
  still applies if the form is driven some other way.

The refusals are the writer's rules, not the view's. A refusal you disagree with is a change
to the rules, which needs an ADR, not a change to this page.

## Reverse a registration

1. **Pick a registration** from **Registration to reverse**. Each entry reads
   `registered:<event id> - <candidate title>`. The **Active registrations** table below
   shows the same registrations as text: record, candidate, rule, reviewer and time.
   Reversed registrations are not listed.
2. **Read the warning.** Selecting a registration shows its impact before you act:

   > Reversing registered:<event id> orphans 3 current decision(s); the paper is currently
   > included. The decisions stay in the log. Reverse?

   - *Orphans* counts the current screening decisions on that record: one per stage and
     reviewer, with superseded ones not counted. These are the decisions a reversal would
     leave pointing at a record outside the corpus. Reversal is still allowed, and the view
     does not forbid it.
   - *Currently included* says whether the paper is in the set that the flow diagram's
     **Included** count is taken from, as the store was last built.
   - If the impact cannot be read, for example because no Layer 1 store exists, the alert
     shows `Impact unknown: <the error>`, and the warning says the impact is unknown. The view
     never shows a guessed count. Reverse stays available in that case.

3. **Pick a reason** from **Reversal reason**. The list is closed, and it is the writer's own
   list, not a copy:

   - `wrong_doi`
   - `wrong_title`
   - `duplicate_of_scopus`
   - `not_eligible`
   - `other`

4. **Enter a note** in **Note (required when the reason is other)**. The note is required
   only for `other`. For `other`, Reverse stays disabled while the note is empty or only
   whitespace. For the other reasons a blank note is simply not recorded.
5. **Click Reverse.** The click after the warning is the one confirmation: there is no second
   dialog. Success shows:

   > Reversed <register event id> (event <event id>). It takes effect at the next build; the
   > decisions stay in decisions.jsonl.

   Reverse is disabled until a reason is picked and, for `other`, a note is entered. A refusal
   that gets past the disabled button reads `Refused: ...` and writes nothing. For example, the
   writer's own check on `other` with a blank note reads
   `Refused: reversal reason 'other' requires a non-empty note`.

The view has no edit and no delete. A reversal appends a `reverse` event to the registrations
file, and the register event stays in place. To correct a wrong DOI or title, reverse the
registration and then register the candidate again, as the **Correcting a registration** note
in the view says. A candidate that has been reversed is selectable again.

## After a change: the figures move at the next build

A successful Register or Reverse appends one event to `decisions/registrations.jsonl` and
rewrites its checksum sidecar. Nothing else changes: the raw captures and `decisions.jsonl` are
not touched, and no screening decision is created.

The figures that depend on registrations are derived in Layer 1, and they do not move on the
click:

- the `registered` count in `prismabib flow`,
- the `registered_records` table, and
- the orphaned decisions listed by `project_orphaned_decisions`.

A plain `uv run prismabib build <slug>` reuses an existing store and loads nothing new; its
output says so. To fold a registration or a reversal into Layer 1, rebuild:

```bash
uv run prismabib build my-review --rebuild
uv run prismabib flow my-review
```

The success messages say "takes effect at the next build". Until you rebuild, the flow diagram
still shows the numbers from before the change, as it does after a command-line registration.

## The same writer on the command line

The three commands, `prismabib register`, `prismabib reverse` and `prismabib registrations`,
call the same two writers. They take the same inputs the view derives for you, which are the
candidate identifier, the response path and the reason. They have no candidate table, no
impact warning and no confirmation text. Use them for scripts and continuous integration.

## Accessibility

The project checks the following, automatically. It is not a conformance claim.

- **Labelled controls.** Every input control has a label, which is its Panel `name`. A unit
  test checks that every control in both sections has a non-empty name that Panel did not
  generate.
- **Text, not colour.** Each outcome starts with a word: `Refused:` for a refusal, `Registered`
  or `Reversed` for a success. The impact warning, the near-match wording and the confirmation
  are text. No meaning is carried by colour alone.
- **Announced alerts.** The alert region is a `role="alert"` element in the rendered HTML. A
  test asserts the attribute on the saved document, for a refusal in each section.
- **Keyboard use.** The view uses native form controls only, with no keys bound of its own.
  Its controls are in reading order, so Tab and Shift+Tab move through them in that order.
  Space and Enter press a button or tick the checkbox, and the arrow keys move through a select.
  The keyboard behaviour is by construction, and no test drives it in a browser.
- **Tables as text.** The candidates table and the active registrations table are HTML tables
  with header cells, not images.

What this does not establish:

- **No manual screen-reader pass.** The project owner has decided that none is required for this
  view, so the automated checks above are the verification that exists.
- **No WCAG conformance claim.** This page does not say that the view meets WCAG 2.x at any
  level.

## Do not run this view in the same notebook as the screener

!!! warning "Keep the registration view and `screener()` in separate notebooks or browser tabs"

    `screener()` installs a listener for keystrokes on the whole page, not only on its own
    cell. It is active on any page that holds the screener, including a cell that holds
    `registration_view`. While a Register or Reverse button has focus, pressing `i`, `e`, `u`,
    `n`, `p` or `z` can then act as a screening key, and a stray `i` or `e` appends a screening
    decision to `decisions.jsonl`.

    Run the two views in separate notebooks, or in separate tabs under `panel serve`. Each
    served page has its own listener. The registration view binds no keys, so it does not
    need one.

## Related pages

- [Run a New Review](run-a-new-review.md), for the steps before registration.
- [Getting Started](../getting-started.md#what-exists-today), for the table of what each tool does.
- [ADR 0029: reviewer registration of a candidate as a record](../architecture/adr/0029-reviewer-registration-of-a-candidate-as-a-record.md),
  for the rules the writer enforces.
- [ADR 0033: FlowCounts registered count and orphaned decisions](../architecture/adr/0033-flowcounts-registered-count-and-orphaned-decisions.md),
  for the closed reason list and the orphan count.
- [ADR 0035: GUI for registering and reversing candidate records](../architecture/adr/0035-gui-registration-of-candidates.md),
  for the decisions behind this view.
