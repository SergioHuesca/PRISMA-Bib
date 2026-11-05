# Note sheet

This page is a template. Copy it for each session, to a folder outside the repository. Fill in the copy, not this page.

The word "stumble" is for facilitator documents only. It is never said to a participant.

**Recording: option C.** The session is not recorded, and there is no note-taker. The facilitator writes the notes alone.
The notes are the only record, and they are written by the author of the tool. The discipline below limits that weakness.
It does not detect bias. See section 6 of [protocol](protocol.md).

**Rules for every filled sheet:**

- No participant names. The session code only: P01 to P06.
- No API key, no character of a key, and no content of a `.env` file.
- Paths are written as `<projects root>\...`. A Windows user name in a path identifies the participant.
- Filled sheets are stored outside the repository, in the storage location set in the consent text. They are not
  committed, not pasted into an issue, and not attached to one.
- An issue carries only the finding text, not the sheet. See the issue template.

## Note-taking discipline

1. **Live shorthand.** Write only: time, task code (T0 to T7), outcome letter (N, 1, 2, 3, F), hint level, and at most one
   verbatim phrase per stumble.
2. **Verbatim only in these fields, one phrase each.** Everything else is shorthand.
   - T0: the expectation of the access question.
   - T3: the meaning of the number next to "records identified".
   - T4: the meaning of "unsure or not yet screened".
   - T5: the answer.
   - T6: the answer.
   - T7: the explanation of the match.
   - The four debrief answers.
3. **Hint log.** Log task, time and level. Write words only if they are off-script, that is, not one of the scripted
   phrases in [tasks](tasks.md) or the run card.
4. **Write-up within 30 minutes** after the session. Expand the shorthand into the full sheet the same day. Mark anything
   expanded from memory.
5. **No clean-up later.** Do not edit, tidy, or reword the sheet after the write-up. The only permitted later change is
   expanding shorthand. Any later change is dated.
6. **Confidence.** Each task row has a confidence field. Use "low" where the notes were expanded from memory, or where a
   stumble was not written live.

## Session header

| Field | Entry |
| --- | --- |
| Session code (P01 to P06) | |
| Date | |
| Facilitator (initials) | |
| Recording | Option C. Not recorded. Facilitator written notes only. No note-taker. |
| Consent confirmed by email reply, and at block 1 (time) | |
| BQ1 role | |
| BQ2 review experience | |
| BQ3 terminal experience | |
| BQ4 recruitment answer (recorded silently, not read aloud) | |
| Tag or commit under test | |
| Output of `prismabib --version` | |
| Platform (Windows build, or the other system) | |
| Python minor version | |
| Path taken (openalex or combined) | |
| Staged fallback used (yes, from which task, or no) | |
| Session start and end | |
| Total time | |
| Stopped early (yes and why, or no) | |
| Write-up completed (time, within 30 minutes of the end) | |

## Per-task fill-in

| Task | Start | Stop | Outcome (N, 1, 2, 3, or F) | assisted-state (yes or no) | Confidence (high, medium, low) | Verbatim field or shorthand | Stumble ids |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T0 Install, doctor, access question | | | | | | | |
| T1 Credentials and key handover | | | | | | | |
| T2 Project and search | | | | | | | |
| T3 Capture, build, flow | | | | | | | |
| T4 Screening | | | | | | | |
| T5 Export and provenance | | | | | | | |
| T6 Methods sentence | | | | | | | |
| T7 Delete and rebuild | | | | | | | |

**Outcome.** The highest hint level used decides it. "N" (no hint) is the headline. "F" means the task reached its cap, or
was stopped for any other reason. The stop reason goes in the shorthand column.

**assisted-state.** Mark "yes" for any task done on the staged fallback folder, `baseball-pitch-staged`. Those tasks are
reported in their own row.

## Hint log

| No. | Task | Time | Level (1, 2, or 3) | Safety hint (yes or no) | Words, only if off-script |
| --- | --- | --- | --- | --- | --- |
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |

## T7 match check

The participant keeps the first flow output in Notepad. The facilitator writes:

| Field | Entry |
| --- | --- |
| Match (yes or no) | |
| If no: the differing lines only, as printed by the first run | |
| If no: the differing lines only, as printed by the second run | |
| Stop-first sequence needed (yes or no), and the delete error if there was one | |

## Screenshots

| Screenshot | Task | What it shows (tool output only) | Checked for keys and user names (yes or no) | Kept, cropped (yes or no) |
| --- | --- | --- | --- | --- |
| | | | | |

A screenshot that fails the check is deleted at once, and the failure is noted in the stumble log.

## Stumble log

A stumble is anything that slowed the participant down, sent them the wrong way, or stopped them. Number them in order
within the session: S01, S02, and so on.

| id | task | what happened | category | severity | evidence |
| --- | --- | --- | --- | --- | --- |
| S01 | | | bug, usability, docs, or out-of-scope | blocker, major, or minor | |

Evidence is the exact text of the message, the command typed, the exit code, the line in the guide or the file, or a
checked screenshot. Do not write a key, a `.env` line, or a user name.

### Categories

| Category | Use it when |
| --- | --- |
| bug | prismabib does something other than its documentation or its own message says it does. |
| usability | prismabib does what it should, but the participant cannot use it easily. |
| docs | The guide or another page is wrong, unclear, missing a step, or out of date. |
| out-of-scope | The cause is something prismabib cannot change. Examples: the participant's Scopus entitlement, OpenAlex's quota or counts, the participant's network, or a Windows policy. Still log it. |

### Severity

Severity is assigned during the session, from these definitions, when the stumble happens. It is not re-rated afterwards.

| Severity | Definition |
| --- | --- |
| blocker | The participant cannot continue without facilitator help beyond hint level 2. Or the participant sees a wrong or misleading number. |
| major | A significant detour, or a wrong belief about the tool that the participant keeps after the task. |
| minor | Friction only. The participant gets past it without help. |

**Overlap rule.** Two questions decide between blocker and major:

- If the tool's own wording caused the wrong belief, the severity is **blocker**.
- If the participant ignored wording that was visible on screen, the severity is **major**.

If neither applies, use the definitions above.

**Examples, one per level. These are illustrations, not findings.**

- EXAMPLE, delete. Blocker: a count on screen differs from the count the same command printed earlier, and no message
  explains it.
- EXAMPLE, delete. Major: the participant read a line of the output as the number of records screened, and the wording on
  the same line said otherwise. The participant ignored visible wording.
- EXAMPLE, delete. Minor: the participant had to scroll up to find the command they had just typed.

**Rules that also apply.**

- A stumble that needed a level 3 hint is at least a blocker.
- A wrong number is a blocker, even if the participant spotted it. Record what was on screen and what the participant said.
- A key or a `.env` line visible where others could see it is a bug with severity blocker, with "security" added to the
  description. Follow section 5 of [protocol](protocol.md).
- An out-of-scope stumble keeps its severity. It is not dropped.
- A docs gap for the screening server is recorded if the stop-first sequence in T7 was needed. See [tasks](tasks.md), T7.
- A finding that contradicts the author's expectations is marked, so that it appears in the list required by
  [questionnaires](questionnaires.md), section 5.6.

## Stumble log: EXAMPLE, delete

This row shows the format. It is not a finding.

| id | task | what happened | category | severity | evidence |
| --- | --- | --- | --- | --- | --- |
| EXAMPLE, delete S00 | T2 | The participant read a count from a line that was not the stop line. | usability | minor | Paraphrase, marked as paraphrase. |

## Session end

| Field | Entry |
| --- | --- |
| Questionnaire done (time) | |
| Debrief done, including the one-minute member check (time) | |
| Write-up completed (time) | |
| Findings sent to the owner (date) | |
| Code-to-person list and emailed consent reply: destroyed on withdrawal date (date, or not yet) | |
