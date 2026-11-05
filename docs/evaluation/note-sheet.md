# Note sheet

This page is a template. Copy it for each session, to a folder outside the repository. Fill in the copy,
not this page.

The word "stumble" is for facilitator documents only. It is never said to a participant.

**Recording: option C.** The session is not recorded. There is no note-taker. The facilitator writes the
notes alone. The notes are therefore the only record, and they are written by the author of the tool. The
discipline below is there to limit that weakness. Read section 6 of [protocol](protocol.md) for the reasons.

**Rules for every filled sheet:**

- No participant names. The session code only: P01 to P06.
- No API key, no character of a key, and no content of a `.env` file.
- Paths are written as `<projects root>\...`. A Windows user name in a path identifies the participant.
- Filled sheets are stored outside the repository, in the storage location set in the consent text. They
  are not committed, not pasted into an issue, and not attached to one.
- An issue carries only the finding text, not the sheet. See the issue template.

## Note-taking discipline

1. **Live, write shorthand only.** During the session, write only: time, task code (T0 to T7), outcome letter
   (N = no hint, 1 = hint 1 only, 2 = hint 2, 3 = hint 3, F = failed), hint level, and at most one verbatim
   phrase per stumble. No full sentences live.
2. **Write-up within 30 minutes after the session,** while memory is fresh. Expand the shorthand into the full
   sheet the same day. Mark anything expanded from memory as "from memory".
3. **Verbatim quotes only if written live.** A quote written after the session is paraphrase. Mark it as
   paraphrase, and do not put it in quotation marks.
4. **No clean-up later.** Do not edit, tidy, or reword the sheet after the write-up. The only permitted later
   change is expanding shorthand. Any later change is dated.
5. **Confidence.** Each task row has a confidence field. Use "low" where the notes were expanded from memory, or
   where a stumble was not written live.

## Session header

| Field | Entry |
| --- | --- |
| Session code (P01 to P06) | |
| Date | |
| Facilitator (initials) | |
| Recording | Option C. Not recorded. Facilitator written notes only. No note-taker. |
| Consent confirmed by email reply, and at block 1 (time) | |
| Tag or commit under test | |
| Output of `prismabib --version` | |
| Platform (Windows build, or the other system) | |
| Python minor version | |
| Path taken (openalex or combined) | |
| Background answers BQ1 to BQ4 | |
| Session start and end | |
| Total time | |
| Stopped early (yes and why, or no) | |
| Write-up completed (time, within 30 minutes of the end) | |

## Per-task fill-in

| Task | Start | Stop | Outcome (N, 1, 2, 3, or F) | assisted-state (yes or no) | Confidence (high, medium, low) | Quotes (verbatim if live, else paraphrase, marked) | Stumble ids |
| --- | --- | --- | --- | --- | --- | --- | --- |
| T0 Install, doctor, access question | | | | | | | |
| T1 Credentials | | | | | | | |
| T2 Project and search | | | | | | | |
| T3 Capture, build, flow | | | | | | | |
| T4 Screening | | | | | | | |
| T5 Export and provenance | | | | | | | |
| T6 Methods sentence | | | | | | | |
| T7 Delete and rebuild | | | | | | | |

**Outcome.** The highest hint level used decides it. "N" (no hint) is the headline. "F" means the task reached
its cap, or was stopped for any other reason. The stop reason goes in the quotes column.

**assisted-state.** Mark "yes" for any task done on the staged fallback folder. Those tasks are reported in
their own row, not in the main table.

## Hint log

Write each hint when it is given. Write the words used, exactly, if the words are written live.

| No. | Task | Time | Level (1, 2, or 3) | Safety hint (yes or no) | Words used |
| --- | --- | --- | --- | --- | --- |
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |

## Stumble log

A stumble is anything that slowed the participant down, sent them the wrong way, or stopped them. Number
them in order within the session: S01, S02, and so on.

| id | task | what happened | category | severity | evidence |
| --- | --- | --- | --- | --- | --- |
| S01 | | | bug, usability, docs, or out-of-scope | blocker, major, or minor | |

Evidence is the exact text of the message, the command typed, the exit code, the line in the guide or the
file, or a screenshot checked as section 5 of [protocol](protocol.md) says. Do not write a key, a `.env`
line, or a user name.

### Categories

| Category | Use it when |
| --- | --- |
| bug | prismabib does something other than its documentation or its own message says it does. |
| usability | prismabib does what it should, but the participant cannot use it easily. |
| docs | The guide or another page is wrong, unclear, missing a step, or out of date. |
| out-of-scope | The cause is something prismabib cannot change. Examples: the participant's Scopus entitlement, OpenAlex's quota or counts, the participant's network, or a Windows policy. Still log it. |

### Severity

Severity is assigned during the session, from these definitions, when the stumble happens. It is not re-rated
afterwards. This protects the record from a release date.

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

- EXAMPLE, delete. Blocker: a count on screen differs from the count the same command printed earlier, and
  no message explains it.
- EXAMPLE, delete. Major: the participant read a line of the output as the number of records screened, and
  the wording on the same line said otherwise. The participant ignored visible wording.
- EXAMPLE, delete. Minor: the participant had to scroll up to find the command they had just typed.

**Rules that also apply.**

- A stumble that needed a level 3 hint is at least a blocker.
- A wrong number is a blocker, even if the participant spotted it. Record what was on screen and what the
  participant said.
- A key or a `.env` line visible where others could see it is a bug with severity blocker, with "security"
  added to the description. Follow section 5 of [protocol](protocol.md).
- An out-of-scope stumble keeps its severity. It is not dropped.
- A docs gap for the panel server is recorded if the participant did not stop the screening server before the
  delete in T7, and was not told to by the guide. See [tasks](tasks.md), T7.

## Stumble log: EXAMPLE, delete

This row shows the format. It is not a finding.

| id | task | what happened | category | severity | evidence |
| --- | --- | --- | --- | --- | --- |
| EXAMPLE, delete S00 | T2 | The participant read a count from a line that was not the stop line. | usability | minor | Paraphrase of the participant's question, marked as paraphrase. |

## Session end

| Field | Entry |
| --- | --- |
| Questionnaire done (time) | |
| Debrief done (time) | |
| Write-up completed (time) | |
| Findings sent to the owner (date) | |
| Code-to-person list status (kept apart, or destroyed on withdrawal date) | |
