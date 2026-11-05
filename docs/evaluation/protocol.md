# Protocol

This page is for the facilitator. It covers how a session runs, what the facilitator says, when help is
given, when a task stops, what is shared on screen, what is recorded, the gate that must be passed before
the first session, the consent text, and data handling.

The [run card](run-card.md) is the one-page version for use during a session. The tasks are in
[tasks](tasks.md). The forms and analysis are in [questionnaires](questionnaires.md). The note sheet is
in [note sheet](note-sheet.md).

## 1. The author, and what the facilitator says

The facilitator wrote the tool. This creates a bias toward a favourable reading of the tool and of the
guide. The rules below reduce that bias. They do not remove it. See section 9 and the analysis plan.

**Disclosure.** Say this at the start, after consent, in these words:

> I wrote this tool. You will not hurt my feelings. Criticism is the most useful thing you can give me.

**No defending, no explaining.** When the participant reports a problem, a confusion, or a wrong belief,
reply only: "Thank you, I will note that." Do not explain the tool, the guide, or the output during the
session. Do not correct a wrong belief during a task. Explanations are given only in the debrief, after
the questionnaires. Write down what the participant said, word for word.

**Think-aloud script.** Read this at the start of block 1, after the consent confirmation:

> Thank you for joining. I will ask you to work through the guide on your own computer, and I will watch
> your screen. Please think out loud. Tell me what you are looking for, what you expect a command to do,
> and what surprises you. I will not answer questions while you work. If I help you, I will say so.
>
> We are testing the tool and the guide. We are not testing you. I wrote the tool, so I would like you
> to say what you really think. If something is confusing, say so. That is welcome.
>
> You can stop at any time. You do not have to answer any question.

**Neutral probes during a task.** Use only these:

- "What are you looking for?"
- "What do you expect that to do?"
- "What does that line tell you?"
- "What would you try next?"

Do not say "good", "right", "that is it", "no, try again", or anything else that judges a step or answer.

**After each task,** say: "Thank you. Is there anything you want to say about that step?" Then write
down the answer.

## 2. Hints: thresholds and levels

Hints are time-based. "No progress" means no new output, no changed screen, and no new command for the
period in question. The facilitator waits. Struggling is data, and it is recorded as data.

| Time without progress | Action |
| --- | --- |
| 0 to 2 minutes | Nothing. Neutral probe only if the participant is silent. |
| 2 minutes | Level 1: a pointer, with no content. |
| 4 minutes | Level 2: point to the exact place on the screen or in the guide. No value or answer. |
| 6 minutes | Level 3: say the exact command or value, or type it. Level 3 is a blocker by definition. |

**Earlier help is allowed only for safety**, for example a key visible on the shared desktop, or a
command that would delete data the participant cannot get back. Log it as a safety hint.

**Logging.** Log every hint in the note sheet when it is given. Write the words used, exactly. A repeated
hint at the same level is logged each time. The highest level used for a task decides its outcome.

**Never give:**

- an answer to the access question, or a view on whether the participant has Scopus access;
- a choice of mode;
- an explanation of what a number means before the participant has answered;
- a judgement on a screening decision or a reason code;
- a key, or a place to put a key other than the prompt in the guide's command;
- a guess at the cause of a problem, before the participant has described it.

The facilitator types a command only at level 3. If the participant asks "is this right?", say "What do
you think?"

## 3. Stop rules and caps

**Per-task cap.** Each task stops at its target plus 3 minutes. The caps are in [tasks](tasks.md).
T0 is capped by the install limit of 15 minutes from the start of `git clone`, which comes first.

**Stop a task when:**

| Condition | Action | What is recorded |
| --- | --- | --- |
| The success criteria are met and the participant says they are done. | Move on. | Success, and the highest hint level. |
| The task reaches its cap. | Stop. Move on. | Stopped at cap, and the last step. |
| Level 3 was given and the step still fails. | Stop the task. Move on if the next task does not need it. | Failed, with the last error text verbatim. |
| The participant asks to stop the task. | Stop. Do not argue. | The request, and the reason if given. |
| A key, or the contents of `.env`, is visible or typed where it can be seen. | Stop the share now. Apply section 5. | An incident line, with no value. |
| A wrong number is on screen and the participant is about to use it, for example to cite it. | Say "Thank you, I will note that." Let the participant finish the step. | The screen text, verbatim, and the participant's belief. |

**Stop the session when:**

- the participant is distressed, or asks to end;
- the shared screen or the network fails for more than 10 minutes.

There is no separate install rule. The install cap of 15 minutes is the rule. If it is reached, T0 stops,
the failure is a blocker, and the session goes to the questionnaires. A stopped session still counts.

## 4. Clock checkpoints and fallback

The checkpoints and their consequences are in [index](index.md). In short:

- **Minute 25.** T2 must have started. If not, shorten T4 to five records.
- **Minute 50.** T5 must have started. If not, skip T6.
- **T7 outranks T6.** If only one can run, run T7.
- **Questionnaires always run.**

**Staged fallback.** If the capture cannot run in the session, the facilitator may send the staged project
folder `baseball-pitch`. Every task done on it is marked `assisted-state` in the note sheet. The fallback is
used for T3 onward only when the capture itself fails. It is not used to rescue an install.

### Facilitator checklist

**Before the session**

- [ ] The Session 1 gate in section 7 is complete.
- [ ] The consent email was sent, and the participant's emailed "yes" is saved outside the repository.
- [ ] The participant self-check was returned (see [tasks](tasks.md)).
- [ ] The tag or commit under test is recorded, and the guide the participant opens matches it.
- [ ] The staged fallback folder is ready and holds no key.
- [ ] The virtual desktops are set up as in section 5, and notifications are off.
- [ ] A timer is running, and the run card is open.

**During the session**

- [ ] Consent is confirmed at the start, with the time.
- [ ] The disclosure line and the think-aloud script are read.
- [ ] Each hint is logged at the time it is given.
- [ ] Each task has a start and a stop time.
- [ ] The two checkpoints are checked at minutes 25 and 50.
- [ ] Caps are applied as they happen.

**After the session**

- [ ] The note sheet is completed on the same day.
- [ ] The questionnaire answers are saved outside the repository, with the session code.
- [ ] No recording is made (option C). The full note sheet is written up within 30 minutes, as section 6 says.
- [ ] Blockers and majors go to the owner within two working days, as findings only.

## 5. Screen share and the incident rule

**Setup (before the session).**

- The participant uses a clean Windows virtual desktop, opened with Win+Ctrl+D. It holds only Windows
  Terminal, Notepad, one browser window, and a CSV viewer. Any app that shows a CSV as text is acceptable.
- The participant shares that desktop, not the whole screen.
- The openalex.org account page stays on another virtual desktop, which is not shared.
- Notifications are off, on both sides.
- Windows Terminal font size is 16 or larger.
- Clipboard history is cleared or turned off (Win+V), before the share starts.
- **Do not open `.env`.** Nothing edits `.env` by hand. `credentials status` is safe to show. It prints
  names and `set` or `not set`, and never a value.

**T1 key entry.** The participant copies the key on the other desktop and switches back to paste it into
the hidden prompt. The share is not stopped. The facilitator does not look away. The prompt shows nothing,
so nothing is on screen. If a key appears anywhere on the shared desktop, follow the incident rule.

**Paths in notes and screenshots.** A path can show the participant's Windows user name. Write paths as
`<projects root>\...` in the note sheet. Take no screenshot unless it is needed. A needed screenshot is
cropped to the window and checked for keys and user names before it is saved.

**Incident rule** (a key was seen or typed where others could see it).

1. Stop the share at once. Say: "Please stop sharing for a moment."
2. Tell the participant, without repeating the key: "A key may have been visible. Please revoke it on the
   provider's website and create a new one. Do not send it to anyone."
3. Write an incident line in the note sheet, with the time, the task, and the words "a key was visible,
   value not recorded". Do not write any character of the key.
4. Log it as a bug with severity blocker, with "security" added to the description.
5. The owner decides, with the facilitator, whether the session continues.

## 6. Recording: option C

**Chosen option: C.** No recording. No note-taker. The facilitator, who is the owner and the author, takes
written notes alone. This is the owner's decision.

**Option C in practice.**

- Nothing is recorded: no audio, no screen capture, no video, by the facilitator.
- The facilitator writes the notes alone. There is no note-taker, and no second person attends the session.
- No second coder is possible. The analysis is by one unblinded coder, the facilitator. See section 9.
- The notes are read as observations by the author, not as independent coding.

**Mitigations for the known weakness of option C.** The weakness is that the notes are the only record, and
they are written by the person whose tool is under test. The mitigations below apply to every session.

1. **Live shorthand only, during the session.** On the note sheet, write only: time, task code (T0 to T7),
   outcome letter (N = no hint, 1 = hint 1 only, 2 = hint 2, 3 = hint 3, F = failed), hint level, and at most
   one verbatim phrase per stumble. Do not write full sentences live.
2. **Full write-up within 30 minutes after the session,** while memory is fresh. Expand the shorthand into the
   full sheet on the same day. Mark what was expanded from memory.
3. **Verbatim quotes only if written live.** A quote written after the session is paraphrase, and is marked as
   such. Do not put it in quotation marks.
4. **No clean-up later.** Notes are not edited, tidied, or reworded after the write-up, except to expand
   shorthand. Any later change is dated and marked.
5. **Confidence field.** Each task row on the note sheet has a field "confidence in this record (high, medium,
   or low)". Record low confidence where the notes were expanded from memory, or where a stumble was not
   written live.

**Options considered and not chosen.** The owner did not choose these. They are recorded here with the reasons.

- **Option A, audio and screen recorded locally, deleted by a fixed date.** Not chosen. What it would have
  allowed: a recording to check the notes against, and a second coder. What it would have required: a deletion
  date, a recording file on the facilitator's computer, and recording wording in the consent text. The owner
  did not state a reason for not choosing it.
- **Option B, no recording, with a required note-taker.** Not chosen. What it would have allowed: a second
  written record of each session, from a person other than the author. What it would have required: a second
  person in every session, and a note-taker role in the consent text. The owner did not state a reason for not
  choosing it.

## 7. Session 1 gate

No session may run until every row is complete. The owner does not waive the gate.

| Gate row | Requirement | Status |
| --- | --- | --- |
| G1 | Every placeholder in the consent text (section 8) is filled. | `<to be filled before session 1>` |
| G2 | Either the facilitator holds the ethics determination or approval reference from their institution, and it is entered below, or the sessions are held only as a software usability evaluation, and the default sentence in section 8 stands. | `<approval reference, or default sentence stands>` |
| G3 | Recording option chosen. | Chosen option: C (no recording, facilitator written notes only, no note-taker). Decided by the owner. |
| G4 | Consent email sent, with the consent text. The participant's emailed "yes" is saved outside the repository. | `<to be filled before session 1>` |
| G5 | Tag or commit under test pinned, and the guide the participant opens matches it. | `<tag or commit under test>` |
| G6 | Staged fallback folder built and tested. It holds no key. | `<to be filled before session 1>` |
| G7 | Participant self-check returned. | `<to be filled before session 1>` |
| G8 | Retention period, storage location outside the repository, and deletion date for the code-to-person list set. | `<to be filled before session 1>` |

**Owner decisions that remain open:**

| Decision | Placeholder |
| --- | --- |
| Recording option (section 6) | Decided: C. Not recorded. Facilitator written notes only. |
| Retention period | `<retention period>` |
| Storage location | `<storage location, outside the repository>` |
| Withdrawal date | `<withdrawal date>` |
| Scopus variant, per participant | `<approved, or not>` |
| Connect Scopus panel | Not tested in this round. |
| Reporting beyond the release notes | `<what is reported, and where>` |
| Platforms included | Windows only, or also macOS and Linux, reported on their own line |

## 8. Consent text

Sent by email before the session. The participant replies with yes or no. The reply is kept outside the
repository. The consent is read again at the start of block 1, and the participant confirms it.

Placeholders are in code spans, so that a rendered page keeps them visible. The recording paragraph is fixed
to option C. The ethics paragraph has a default sentence, which may be replaced only as the paragraph says.
Do not send the text with any placeholder still in it.

> **Who is running this.** This session is run by `<facilitator name>`, who wrote the tool that is being
> tested. The facilitator is also the project owner and the person who decides the release. The data
> controller is `<data controller>`. Contact: `<contact>`.
>
> **Ethics.** `[ETHICS SENTENCE. The default is: "This session is a software usability evaluation run by the tool's author. It has not been reviewed by a research ethics committee." It may be replaced only by an approval reference from <institution>, filled in as: "This session was reviewed by <institution>, reference <approval reference>."]`
>
> **What the session is.** You are invited to take part in a remote session that tests a research software tool, prismabib, on your own computer. It takes up to 75 minutes, on a screen share through `<video-call provider>`, which processes the screen during the call. You will follow a written guide and complete a set of tasks. You will also answer a short background set of questions at the start, and a questionnaire and four debrief questions at the end.
>
> **What is not being tested.** You, your skill, or your research. Nothing you do will be judged. Anything you say about the tool is welcome, including criticism.
>
> **Recording.** The session is not recorded. Nobody else attends. The facilitator, `<facilitator name>`,
> takes written notes during the session. The facilitator also wrote the tool, which may affect how the notes
> read. The results are the facilitator's observations, not an independent record.
>
> **Incidental content.** Your screen may show your folder names, your user name in paths, and the search and results of the session. Please do not show any key. If a key appears, the facilitator will stop the share and tell you how to revoke it.
>
> **What is collected.** Notes of what you did and said about the tool, how long each task took, the problems you met, your answers to the background and questionnaire items, and facts about your computer (Windows build, Python minor version, prismabib version and commit).
>
> **What is not collected.** Your API keys, the contents of your `.env` file, the files in your projects folder, your name in the notes, and any screenshot unless checked.
>
> **Identifiers.** You are identified by a code, P01 to P06. A list linking your code to you is kept apart from the notes, until the withdrawal date below. On that date it is destroyed.
>
> **Reporting.** Results are reported in aggregate. Anonymised quotes, and per-person scores, may also be reported. Because the number of participants is small, a quote or a score may let a reader guess who said or scored it. You can ask for a specific quote not to be used, by writing to `<contact>` before `<withdrawal date>`.
>
> **Storage.** Notes are kept in `<storage location>`, outside the project repository, and are deleted after `<retention period>`.
>
> **No payment.** Taking part is not paid.
>
> **Your rights.** You can stop at any time, for any reason, without giving one. You do not have to answer any question. You can ask for your notes to be deleted until `<withdrawal date>` by writing to `<contact>`. `<GDPR rights statement, as confirmed by the data controller>`.
>
> Do you agree to take part, and to the notes being kept as described? Reply yes or no.

## 9. Data handling

**Collected.** Observations, the hint log, timings, quotes, questionnaire answers, the code list, the stumble
log, the environment facts above, and the consent email reply.

**Not collected.** Any API key, token, or content of `.env`. The contents of `projects\`, `raw\`, `store\`,
`fulltext\`, or `exports\`. Nothing is copied from the participant's computer. Names, email addresses, or the
participant's OpenAlex account details.

**Identifiers.** P01 to P06, in the order of sessions.

**Code-to-person list.** Kept apart from the notes, in `<storage location>`, until the withdrawal date.
On the withdrawal date it is destroyed, and the notes that remain are kept under the retention period. A
participant who withdraws before that date has their notes deleted, and the list entry removed.

**Where the notes live.** Outside the project repository, in the storage location. The repository is public.
Notes, questionnaires, and emails are never placed in the repository, a commit, or an issue.

**Consent reply.** The participant's emailed "yes" is saved outside the repository, with the session code.

**Retention.** Notes and questionnaires are kept until `<retention period>`, then deleted.

**No second coder.** Under the chosen option C, there is no recording and no second person, so no second
coder is possible. This is not recommended, it is not available.

**Limitation.** The analysis is by one unblinded coder, the facilitator, who is also the author. The results
are the author's observations. They are not independent coding. This limitation is stated in every report.

## 10. Right to stop

A participant can stop at any time, for any reason, and does not have to answer any question. A stop has no
other effect. A participant can ask for their notes to be deleted until the withdrawal date. The facilitator
says so at the start and again at the wrap-up.
