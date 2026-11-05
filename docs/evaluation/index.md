# Peer Evaluation Kit

This section is the kit for one round of individual remote sessions. The project owner runs one session at a time,
with one peer, on a screen share. Each session lasts at most 75 minutes.

The round asks one question: can a researcher install prismabib on a clean Windows 11 computer, follow the guide, and
get a result they could check and trust?

**Author disclosure.** The facilitator wrote this tool. The facilitator is also the project owner, the person who will
decide the release, and the person who will read the results. These roles are held by one person. The mitigations are
process hygiene. They do not detect bias. Say this to every participant at the start, in these words:

> I wrote this tool. You will not hurt my feelings. Criticism is the most useful thing you can give me.

**Everything the participant says about the tool is welcome, and nothing the participant does is judged.** A confusing
message, a missing step, or a slow install is a result about the tool.

**Single unblinded coder.** The only coder is the facilitator, who wrote the tool. The session is not recorded (option
C), and no second person attends. Nobody checks the notes against anything. The results are the author's observations.
They are not independent coding, and no inter-rater figure exists. Every summary says so. See
[questionnaires](questionnaires.md), section 5.7.

## Session 1 gate

No session may run until every row of the gate in section 7 of [protocol](protocol.md) is complete. The gate includes
the ethics route (G2), the reporting scope (G9), and a rehearsal of the screen share (G10). The owner does not waive it.

## Pages in this section

- [Tasks](tasks.md): the timeline, the participant self-check, the facilitator pre-session check, and tasks T0 to T7.
- [Run card](run-card.md): one page for use during a session.
- [Protocol](protocol.md): the think-aloud script, hints, stop rules, checkpoints, screen share and key handover,
  recording, the gate, consent text, and data handling.
- [Questionnaires](questionnaires.md): background questions, SUS, custom items, the debrief, and the analysis plan.
- [Note sheet](note-sheet.md): the fill-in sheet, the stumble log, and the severity definitions.

## What we are testing

- Whether the install works on a clean Windows 11 computer, following the guide, within the install cap of 15 minutes.
- Whether a participant can get from a fresh clone to a flow count and to the methods sentence.
- Whether the participant can tell where a value comes from, and what they would want to know about it before using it.
- Whether error messages say what to do next.
- Whether the flow counts are the same after the store is deleted and rebuilt, on the same computer.
- Whether the OpenAlex-only path (mode `openalex`) works for a researcher without Scopus access.

## What we are not testing

- The participant's skill in bibliometrics, systematic reviews, or screening.
- The quality of the participant's research question.
- Whether a particular Scopus key has the COMPLETE view. That is decided by Elsevier and the participant's institution.
  The Scopus-only mode (`scopus`) is not part of this round.
- Whether `numbers.json` reproduces on a different computer. That is a separate check.
- macOS and Linux. Those participants are welcome. Their results are reported on their own line.
- Performance at scale, or use by more than one person at a time.

**What T4 measures.** T4 measures whether the participant can execute the screening keys and read the counts. It does not
measure whether the participant discovers the keys without help. The keys are in the view's help, and the guide points to
them.

## Who takes part

Three to six peers who are independent of the project. Each is a researcher or research staff member who runs, or may
run, literature searches.

**Excluded from recruitment:** supervisors, lab-mates, friends, and anyone with a reporting or assessment relationship
with the facilitator.

Each participant is asked at recruitment whether they have Scopus access. The answer assigns the path, and it is recorded
silently. In the session, the tool's own prompt is the first encounter with the wording of the question.

Participants complete the self-check in [tasks](tasks.md), sent three to five days ahead. The most important item is a
free OpenAlex key, created before the session.

## Session format and timeline

The participant drives on a shared working desktop. The facilitator watches and writes notes. The planned minutes total
75, and the hard end is minute 75.

| Minutes | Block | Budget |
| --- | --- | --- |
| 0 to 5 | Consent confirmation, share check, BQ1 to BQ3 asked, BQ4 recorded silently | 5 |
| 5 to 20 | T0: install (cap 15 minutes from `git clone`), doctor, throwaway init, sources. Hard cap 20. | 15 |
| 20 to 24 | T1: key handover and credentials | 4 |
| 24 to 30 | T2: project and search. T2 must start by minute 25. | 6 |
| 30 to 35 | T3: capture, build, flow | 5 |
| 35 to 43 | T4: screen ten records | 8 |
| 43 to 48 | T5: export and provenance. T5 must start by minute 50. | 5 |
| 48 to 51 | T6: methods sentence and the Mode openalex paragraph | 3 |
| 51 to 55 | T7: delete the store and rebuild | 4 |
| 55 to 65 | Questionnaires: SUS and custom items. Start by minute 60, whatever is unfinished. | 10 |
| 65 to 70 | Debrief: four questions and a one-minute member check | 5 |
| 70 to 75 | Slack | 5 |

The planned total is 75. The hard end is minute 75.

**Clock rules.**

- **Minute 25.** If T2 has not started, shorten T4 to five records. T4 is the rule's target because it is the longest
  task with no external dependency, and its cost scales with the number of records. Cutting it keeps every other task in
  the sequence.
- **Minute 50.** If T5 has not started, skip T6.
- **T7 outranks T6.** If only one can run, run T7.
- **Questionnaires start by minute 60,** whatever is unfinished.

The hint timer does not run while a command is executing. The overrun caps are in [protocol](protocol.md), section 3.

## Roles

- **Participant.** Works on their own computer and shares the working desktop. Follows the guide. Thinks aloud. Can stop at
  any time.
- **Facilitator.** Runs the session, reads the run card, applies the hint thresholds, stops tasks at their caps, and writes
  the notes alone. The facilitator never sees or records an API key.

There is no note-taker. The facilitator, author, owner, and release decider are one person. Severity is assigned during
the session, from the written definitions in the [note sheet](note-sheet.md). It is not re-rated afterwards, so that a
release date does not change the record.

## Facilitator-directed additions to the guide

The guide is the participant's only written instruction. The kit adds the steps below. Each is written into the scenario
wording of its task, so the participant reads it as an instruction.

| Addition | Where | Why |
| --- | --- | --- |
| Create a throwaway project with no `--sources` flag, so the tool asks the access question. | T0 | The guide's commands always pass `--sources`, so the question is otherwise never shown. |
| Run `sources` on the throwaway project. | T0 | To see the mode the answer gave. |
| Pause the share, copy the key on an unshared screen, close the account page, resume the share, paste into the hidden prompt, and copy a harmless word. | T1 | The share may follow the desktop (unverified), and the key must not be on screen. |
| Export the bundle. | T5 | The guide has this step later. T5 needs `records.csv`. |
| Open `records.csv`. | T5 | Provenance is read from the export. |
| Open `numbers.json` and find the methods key. The facilitator pastes the Mode openalex paragraph into the call chat. | T6 | The participant sees only these two items. |
| Delete `store\corpus.duckdb` only, then rebuild and print the flow. | T7 | The guide describes the store as disposable, but does not give the step as a test. |

The kit adds no step that changes what the tool does.

## Background questions

Block 1, before install, asks BQ1 to BQ3. BQ4 is the Scopus answer recorded at recruitment. It is not read aloud, and it
is not asked again. The wordings are in [questionnaires](questionnaires.md), section 1, and the run card.

## What the owner does with the results

- Adds the stumbles from each session to the stumble log within two working days.
- Files each blocker and major finding as an issue, with the template at
  `.github/ISSUE_TEMPLATE/evaluation_finding.md`. The issue carries no participant code and no date.
- Decides whether an open blocker stops the release.
- Reports aggregates as the author's observations, and not as independent coding. Findings that contradicted the author's
  expectations are listed separately. Per-person scores and anonymised quotes may be reported, under the consent text.
  The reporting scope is decided under gate G9.

## Terms

The kit uses the tool's own words, such as "sources mode", "records identified", and "unsure or not yet screened". It
defines none of them for the participant. A participant who does not know a term is a finding about the tool. The word
"stumble" is used only in facilitator documents and is never said to a participant.

## Relation to the guided scenario

- The participant's instructions are the guided scenario, [Getting Started](../getting-started.md). The page on the main
  branch is an older version. A rewritten guide is in review as PR #276.
- Each session runs against one tag or commit. The facilitator records it, and checks that the guide the participant opens
  is the same text.
- The kit links to the guide only as a page. It does not link to a section inside it, because section anchors change when
  the rewrite lands.
- Not every guide step is run in every task. The tasks list the steps they use.

## Fallback

A staged project folder, `baseball-pitch-staged`, is kept by the facilitator. It holds a capture and a store built from the
fixed search, a recorded actor `kit-staged`, and no key. It is sent to the participant only when the capture cannot run in
the session. Every task done on it is marked `assisted-state`. If the install failed, no fallback can run the tool. Record
the install failure as a blocker, and go to the questionnaires.
