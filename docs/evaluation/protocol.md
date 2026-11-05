# Protocol

This page is for the facilitator. It covers how a session runs, what the facilitator says, when help is given,
when a task stops, what is shared on screen, how the key is handed over, what is recorded, the gate that must be
passed before the first session, the consent text, and data handling.

The [run card](run-card.md) is the one-page version for use during a session. The tasks are in
[tasks](tasks.md). The forms and analysis are in [questionnaires](questionnaires.md). The note sheet is in
[note sheet](note-sheet.md).

## 1. The author, and what the facilitator says

The facilitator wrote the tool. This creates a bias toward a favourable reading of the tool and the guide. The
rules below are process hygiene. They reduce the chance that the author's expectations shape the notes. They do
not detect bias, and they do not remove it. See section 6.

**Disclosure.** Say this at the start, after consent, in these words:

> I wrote this tool. You will not hurt my feelings. Criticism is the most useful thing you can give me.

**No defending, no explaining.** When the participant reports a problem, a confusion, or a wrong belief, reply
only: "Thank you, I will note that." Do not explain the tool, the guide, or the output during the session. Do not
correct a wrong belief during a task. Explanations are given only in the debrief, after the questionnaires.

**Think-aloud script.** Read this at the start of block 1, after the consent confirmation:

> Thank you for joining. I will ask you to work through the guide on your own computer, and I will watch your
> screen. Please think out loud. Tell me what you are looking for, what you expect a command to do, and what
> surprises you. I will not answer questions while you work. If I help you, I will say so.
>
> We are testing the tool and the guide. We are not testing you. I wrote the tool, so I would like you to say
> what you really think. If something is confusing, say so. That is welcome.
>
> You can stop at any time. You do not have to answer any question.

**Neutral probes during a task.** Use only these four:

- "What are you looking for?"
- "What do you expect that to do?"
- "What does that line tell you?"
- "What would you try next?"

Do not say "good", "right", "that is it", "no, try again", or anything that judges a step or an answer.

**After each task,** say: "Thank you. Is there anything you want to say about that step?" Then write the answer
down, following section 6.

## 2. Hints: timer, thresholds, and levels

The hint timer measures time without progress. Progress means new command output, a changed screen, or a new
command typed. The timer does not run while a command is executing (install, capture, build, export, rebuild).
When the command ends, the timer starts again from zero. Struggling is data, and it is recorded as data.

| Time without progress | Level | What the facilitator does |
| --- | --- | --- |
| 2 minutes | 1 | A pointer with no content. Per-task phrase in the run card. |
| 4 minutes | 2 | Point to the exact place on screen or in the guide. No value, no answer. |
| 6 minutes | 3 | Say the exact command or value, or type it. Level 3 is a blocker by definition. |

**Earlier help is allowed only for safety**, for example a key visible on the shared desktop, or a command that
would delete data the participant cannot get back. Log it as a safety hint.

**Logging.** Log each hint by three things only: task, time, and level. Write the words used only if they were
off-script, that is, not one of the scripted phrases in [tasks](tasks.md) or the run card. The highest level used
for a task decides its outcome.

**Never give:**

- an answer to the access question, or a view on whether the participant has Scopus access;
- a choice of mode;
- an explanation of what a number means before the participant has answered;
- a judgement on a screening decision or a reason code;
- a key, or a place to put a key other than the prompt in the guide's command;
- a guess at the cause of a problem, before the participant has described it.

The facilitator types a command only at level 3. If the participant asks "is this right?", say "What do you
think?"

## 3. Stop rules and caps

**Per-task cap.** Each task stops at its target plus 3 minutes. The caps are in [tasks](tasks.md). T0 has a hard
cap of 20 minutes, and inside it the install has a cap of 15 minutes from the start of `git clone`. The install
cap comes first.

**Stop a task when:**

| Condition | Action | What is recorded |
| --- | --- | --- |
| The success criteria are met and the participant says they are done. | Move on. | Outcome, and the highest hint level. |
| The task reaches its cap. | Stop. Move on. | "Stopped at cap", and the last step. |
| Level 3 was given and the step still fails. | Stop the task. Move on if the next task does not need it. | Failed, with the last error text verbatim. |
| The participant asks to stop the task. | Stop. Do not argue. | The request, and the reason if given. |
| A key, or the contents of `.env`, is visible or typed where it can be seen. | Stop the share now. Apply section 5. | An incident line, with no value. |
| A wrong number is on screen and the participant is about to use it, for example to cite it. | Say "Thank you, I will note that." Let the participant finish the step. | The screen text, verbatim, and the participant's belief. |

**Stop the session when:** the participant is distressed, or asks to end; or the shared screen or the network
fails for more than 10 minutes.

If the install cap is reached, T0 stops, the failure is a blocker, and the session goes to the questionnaires. A
stopped session still counts.

## 4. Clock checkpoints, fallback, and checklists

The clock is in [index](index.md). The rules that act on it:

- **Minute 25.** T2 must have started. If not, shorten T4 to five records. T4 is the rule's target because it is
  the longest task with no external dependency, and its cost scales with the number of records. Cutting it keeps
  every task in the sequence.
- **Minute 50.** T5 must have started. If not, skip T6.
- **T7 outranks T6.** If only one can run, run T7.
- **Questionnaires start by minute 60, whatever is unfinished.**

**Staged fallback.** If the capture cannot run in the session, the facilitator may use the staged project folder
`baseball-pitch-staged`. It is a separate folder, so the participant's own `baseball-pitch` is never overwritten.
Every task done on it is marked `assisted-state` in the note sheet. The fallback is used for T3 onward only when the
capture itself fails. It is not used to rescue an install. The staged folder's `project.toml` records a generic
actor, `kit-staged`, and no real name. Section 7 gate G6 covers the check.

**Facilitator checklist, before the session**

- [ ] Gate G1 to G10 in section 7 is complete.
- [ ] The consent email was sent. The participant's emailed reply is saved outside the repository.
- [ ] The participant self-check was returned (see [tasks](tasks.md)).
- [ ] The tag or commit under test is recorded, and the guide the participant opens matches it.
- [ ] The staged fallback folder is ready, checked for keys, and carries the generic actor.
- [ ] The virtual desktops are set up as in section 5, and notifications are off.
- [ ] Clipboard history confirmed off, on the participant's self-check reply.
- [ ] A timer is running, and the run card is open.
- [ ] The facilitator's expectations for each task are written down, dated, before the session. These are the
      expectations that section 6 and questionnaires section 5.6 compare findings against.

**During the session**

- [ ] Consent is confirmed at the start, with the time.
- [ ] The disclosure line and the think-aloud script are read.
- [ ] Each hint is logged with task, time and level.
- [ ] Each task has a start and a stop time.
- [ ] The checkpoints are checked at minutes 25 and 50, and the questionnaire start at minute 60.
- [ ] Caps are applied as they happen.

**After the session**

- [ ] The full note sheet is written up within 30 minutes (section 6).
- [ ] The questionnaire answers are saved outside the repository, with the session code.
- [ ] The one-minute member check is done in the debrief, and noted.
- [ ] Findings go to the owner within two working days, as finding text only.

## 5. Screen share, the key handover, screenshots, and the incident rule

**What the share shows.** The share may follow the desktop. This is unverified in this kit. A desktop switch may change
what is shared. Window
thumbnails in Win+Tab and Alt+Tab can show a window on another desktop. Because of this, during a share the
participant does not use Win+Tab or Alt+Tab, and nothing that is not meant for the call is open on the shared
desktop.

**Test before the first session.** The rehearsal (gate G10) tests, on the chosen call platform, what the share
shows during a desktop switch and during Alt+Tab. **This behaviour is unverified in this kit.** The result is
recorded in gate G10. If the share shows the other desktop, use the sequence below without exception.

**Setup (before the session).**

- The participant works on a clean Windows virtual desktop, opened with Win+Ctrl+D. It holds only Windows
  Terminal, Notepad, one browser window, and a CSV viewer. Any app that shows a CSV as text is acceptable.
- The participant shares the working desktop, not the whole screen.
- Notifications are off, on both sides.
- Clipboard history is off, confirmed before the session. This is mandatory. Never open Win+V while sharing.
- Windows Terminal font size is 16 or larger.
- **Do not open `.env`.** Nothing edits `.env` by hand. `credentials status` is safe to show. It prints names and
  `set` or `not set`, and never a value.

**The key handover (T1).** Fixed sequence. The facilitator does not look at the key page at any time.

1. The participant **pauses or stops the share**.
2. The participant creates or copies the key on their own screen, which is not shared. This may be a separate
   desktop or a window the call is not showing.
3. The participant **closes the account page** after copying.
4. The participant **resumes the share** on the working desktop.
5. The participant pastes the key into the hidden prompt and presses Enter. Nothing appears, so nothing shows.
6. The participant **copies a harmless word** from the terminal, for example selects the word ok and copies it. Win+V is
   not opened while sharing. If clipboard history could not be turned off, the participant clears it while the share is
   paused, before step 4.

The key is pasted into the hidden prompt and nowhere else. It is never pasted into Notepad, a chat, or any other window to
check it.

If a key appears on the shared desktop at any point, the incident rule applies.

**Screenshots of tool output.** Allowed for tool output only: errors and the flow output. The participant takes the
screenshot, and sends it to the facilitator by the call chat or by email. Before it is kept, the facilitator
checks it for keys and user names. A screenshot that fails the check is deleted at once, and the failure is noted.
A kept screenshot is cropped to the output. Screenshots are never taken of the account page, `.env`, or the
desktop.

**Paths in notes and screenshots.** A path can show the participant's Windows user name. Write paths as
`<projects root>\...` in the note sheet.

**Incident rule** (a key was seen, typed, or sent where others could see it).

1. Stop the share at once. Say: "Please stop sharing for a moment."
2. Tell the participant, without repeating the key: "A key may have been visible. Please revoke it on the
   provider's website and create a new one. Do not send it to anyone."
3. Write an incident line in the note sheet, with the time, the task, and the words "a key was visible, value not
   recorded". Do not write any character of the key.
4. Log it as a bug with severity blocker, with "security" added to the description.
5. The owner decides, with the facilitator, whether the session continues.

## 6. Recording: option C, and what the mitigations do

**Chosen option: C.** No recording. No note-taker. The facilitator, who is the owner and the author, takes written
notes alone. This is the owner's decision.

**What option C means.**

- Nothing is recorded: no audio, no screen capture, no video, by the facilitator. The video-call provider
  processes the live share as the provider does for any call. That is the provider's own processing, not a
  recording by the facilitator.
- No second person attends. There is no note-taker.
- No second coder is possible. The analysis is by one unblinded coder, the facilitator.
- The notes are the observations of the author. They are not independent coding.

**The weakness.** The notes are the only record, and they are written by the person whose tool is under test. The
mitigations below are process hygiene. They make the notes tighter and dated. They do not detect bias, and nothing
in this kit can. A reader must not take the notes as proof that bias was absent.

**Mitigations.**

1. **Live shorthand only.** During the session, write only: time, task code (T0 to T7), outcome letter (N = no
   hint, 1 = hint 1 only, 2 = hint 2, 3 = hint 3, F = failed), hint level, and at most one verbatim phrase per
   stumble.
2. **Write-up within 30 minutes** after the session, while memory is fresh. Expand the shorthand into the full
   sheet the same day. Mark what was expanded from memory.
3. **No clean-up later.** The sheet is not edited, tidied, or reworded after the write-up, except to expand
   shorthand. Any later change is dated.
4. **Confidence.** Each task row carries a confidence field: high, medium, or low. Use low where the notes were
   expanded from memory, or where a stumble was not written live.
5. **Member check.** In the debrief, the facilitator reads back the stumbles noted so far, and asks what is
   missing or wrong. The answer is noted as the participant's correction.
6. **Contradicting findings are listed.** Every finding that contradicted the author's expectations is listed
   separately in the report, whatever its severity.

**Verbatim-allowed fields.** Verbatim wording is written only in these fields, as one phrase each. Everything else
is shorthand.

- T0: the participant's expectation of the access question.
- T3: the participant's meaning of the number next to "records identified".
- T4: the participant's meaning of "unsure or not yet screened".
- T5: the participant's answer.
- T6: the participant's answer.
- T7: the participant's explanation of the match.
- The four debrief answers.

**Hint log.** Log task, time and level only. Words are written only when off-script.

**Options considered and not chosen.** The owner did not choose these. They are recorded here with what they would
have allowed and required.

- **Option A, audio and screen recorded locally, deleted by a fixed date.** Not chosen. It would have allowed a
  recording to check the notes against, and a second coder. It would have required a deletion date, a recording
  file on the facilitator's computer, and recording wording in the consent text. The owner did not state a reason.
- **Option B, no recording, with a required note-taker.** Not chosen. It would have allowed a second written record
  of each session, from a person other than the author. It would have required a second person in every session,
  and a note-taker role in the consent text. The owner did not state a reason.

## 7. Session 1 gate

No session may run until every row is complete. The owner does not waive the gate.

| Gate row | Requirement | Status |
| --- | --- | --- |
| G1 | Every placeholder in the consent text (section 8) is filled. | `<to be filled before session 1>` |
| G2 | Ethics. If thesis or paper use is possible, the default not-reviewed sentence in section 8 may stand only with a written institutional determination that the work is not subject to ethics review, or an approval reference. Otherwise reporting is limited to release notes and issues. | `<determination or approval reference, or the reporting limit applies>` |
| G3 | Recording option chosen. | Chosen option: C (no recording, facilitator written notes only, no note-taker). Decided by the owner. |
| G4 | Consent emailed, with the consent text. The participant's emailed reply is saved outside the repository. | `<to be filled before session 1>` |
| G5 | Tag or commit under test pinned, and the guide the participant opens matches it. | `<tag or commit under test>` |
| G6 | Staged fallback folder `baseball-pitch-staged` built and tested. It holds no key, and its project.toml records the generic actor `kit-staged`, not a real name. | `<to be filled before session 1>` |
| G7 | Participant self-check returned. | `<to be filled before session 1>` |
| G8 | Retention period, storage location outside the repository, withdrawal date, and the destruction rule for the code-to-person list and the emailed reply. | `<to be filled before session 1>` |
| G9 | Reporting scope decided: release notes and issues only, or the facilitator's thesis or papers as well. | `<release notes and issues only, or thesis and papers>` |
| G10 | Rehearsal done on the chosen call platform: what the share shows during a desktop switch and during Alt+Tab, and whether deleting `store\corpus.duckdb` succeeds while the screening server is running on the facilitator's machine. Results recorded here. | `<rehearsal result, to be filled before session 1>` |

**Other open owner decisions.**

| Decision | Placeholder |
| --- | --- |
| Retention period | `<retention period>` |
| Storage location | `<storage location, outside the repository>` |
| Withdrawal date | `<withdrawal date>` |
| Scopus variant, per participant | `<approved, or not>` |
| Connect Scopus panel | Not tested in this round. |
| Platforms included | Windows only, or also macOS and Linux, reported on their own line |

## 8. Consent text

Sent by email before the session. The participant replies with yes or no. The reply is saved outside the repository.
The consent is read again at the start of block 1, and the participant confirms it.

Placeholders are in code spans, so a rendered page keeps them visible. Do not send the text with any placeholder
still in it.

> **Who is running this.** This session is run by `<facilitator name>`, who wrote the tool that is being tested.
> The facilitator is also the project owner and the person who decides the release. The data controller is
> `<data controller>`. Contact: `<contact>`.
>
> **Ethics.** `[ETHICS SENTENCE. The default is: "This session is a software usability evaluation run by the tool's author. It has not been reviewed by a research ethics committee." It may be replaced only by an approval reference, filled in as: "This session was reviewed by <institution>, reference <approval reference>."]`
>
> **What the session is.** You are invited to take part in a remote session that tests a research software tool, prismabib, on your own computer. It takes up to 75 minutes, on a screen share through `<video-call provider>`. The provider processes the live share as it does for any call. You will follow a written guide and complete a set of tasks. You will answer four short background questions at the start, a questionnaire, and four debrief questions at the end.
>
> **What is not being tested.** You, your skill, or your research. Nothing you do will be judged. Anything you say about the tool is welcome, including criticism.
>
> **Recording.** The session is not recorded. Nobody else attends. The facilitator, `<facilitator name>`, takes written notes during the session. The facilitator also wrote the tool, which may affect how the notes read. The results are the facilitator's observations, not an independent record.
>
> **Screenshots.** You may send the facilitator a screenshot of tool output, such as an error or the flow output, by call chat or email. The facilitator checks it for keys and user names, and keeps it only if it passes, cropped to the output. Please never send a screenshot of your account page or a `.env` file.
>
> **What is collected.** Notes of what you did and said about the tool, how long each task took, the problems you met, your answers to the background and questionnaire items, facts about your computer (Windows build, Python minor version, prismabib version and commit), any screenshot that passes the check, the email address your consent reply was sent from, and [if a form is used; otherwise delete this clause: the answers you give in it, which are held by `<form provider>`].
>
> **What is not collected.** Your API keys, the contents of your `.env` file, the files in your projects folder, your name in the notes.
>
> **Identifiers.** You are identified by a code, P01 to P06. A list linking your code to you, and your emailed consent reply, are kept apart from the notes until the withdrawal date below. On that date both are destroyed.
>
> **Reporting.** Results are reported in aggregate. Anonymised quotes and per-person scores may also be reported. Because the number of participants is small, a quote or a score may let a reader guess who said or scored it. Findings that contradicted the facilitator's expectations are reported too. You can ask for a specific quote not to be used by writing to `<contact>` before `<withdrawal date>`. Results will appear in `<the facilitator's thesis, papers, and release notes, as applicable per gate G9>`.
>
> **Storage.** Notes are kept in `<storage location>`, outside the project repository, and are deleted after `<retention period>`.
>
> **No payment.** Taking part is not paid.
>
> **Your rights.** You can stop at any time, for any reason, without giving one. You do not have to answer any question. You can ask for your notes to be deleted until `<withdrawal date>` by writing to `<contact>`. `<GDPR rights statement, as confirmed by the data controller>`.
>
> Do you agree to take part, and to the notes being kept as described? Reply yes or no.

## 9. Data handling

**Collected.** Observations, the hint log, timings, quotes in the verbatim-allowed fields, questionnaire answers, the
code list, the stumble log, the environment facts in section 8, the emailed consent reply (which holds the
participant's email address), any screenshot that passed the check, and any form answers held by the form provider.

**Not collected.** Any API key, token, or content of `.env`. The contents of `projects\`, `raw\`, `store\`,
`fulltext\`, or `exports\`. Nothing is copied from the participant's computer, except a screenshot that passed the
check. Names, or the participant's OpenAlex account details.

**Identifiers.** P01 to P06, in the order of sessions.

**Code-to-person list and emailed reply.** Kept apart from the notes, in `<storage location>`, until the withdrawal
date. On the withdrawal date the code-to-person list and the emailed consent reply are destroyed. The notes that
remain are kept under the retention period. A participant who withdraws before that date has their notes deleted,
and their list entry and emailed reply destroyed at the same time.

**Where the notes live.** Outside the project repository, in the storage location. The repository is public. Notes,
questionnaires, emails, and screenshots are never placed in the repository, a commit, or an issue.

**Retention.** Notes and questionnaires are kept until `<retention period>`, then deleted.

**Consent record after destruction.** Destroying the emailed reply on the withdrawal date also destroys the only written
record of consent. The trade-off is accepted, and the facilitator keeps a code-only line in the notes instead:
`consent: yes, <date>`, with no email address and no name. This line stays with the notes under the retention period.

**No second coder.** No second coder is possible under option C. The session is not recorded and no second person
attends.

**Limitation.** The analysis is by one unblinded coder, the facilitator, who is also the author. The results are the
author's observations. They are not independent coding. This limitation is stated in every report.

## 10. Right to stop

A participant can stop at any time, for any reason, and does not have to answer any question. A stop has no other
effect. A participant can ask for their notes to be deleted until the withdrawal date. The facilitator says so at
the start and again at the wrap-up.
