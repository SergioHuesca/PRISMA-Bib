# Peer Evaluation Kit

This section is the kit for one round of individual remote sessions. The project owner runs one
session at a time, with one peer, on a screen share. Each session lasts at most 75 minutes.

The round asks one question: can a researcher install prismabib on a clean Windows 11 computer,
follow the guide, and get a result they could check and trust?

**Author disclosure.** The facilitator wrote this tool. The facilitator is also the project owner,
the person who will decide the release, and the person who will read the results. These roles are
held by one person. Mitigations are set out below and in [protocol](protocol.md). Say this to
every participant at the start, in these words:

> I wrote this tool. You will not hurt my feelings. Criticism is the most useful thing you can give
> me.

**Everything the participant says about the tool is welcome, and nothing the participant does is
judged.** A confusing message, a missing step, or a slow install is a result about the tool.

## Session 1 gate

No session may run until every row of the gate table in the protocol section "Session 1 gate" of
[protocol](protocol.md) is complete.
The short version: all placeholders filled, the recording option recorded as C, the ethics sentence
settled, the consent email sent, the tag pinned, and the staged fallback built and tested. The
owner does not waive the gate.

## Pages in this section

- [Tasks](tasks.md): requirements, the participant self-check, and tasks T0 to T7 in full.
- [Run card](run-card.md): one page for use during a session.
- [Protocol](protocol.md): think-aloud script, hint thresholds, stop rules, clock checkpoints,
  screen share, recording (option C), consent text, and data handling.
- [Questionnaires](questionnaires.md): background questions, SUS, custom items, the debrief, and the
  analysis plan.
- [Note sheet](note-sheet.md): the fill-in sheet, the stumble log, and the severity definitions.

## What we are testing

- Whether the install works on a clean Windows 11 computer, following the guide, within 15 minutes.
- Whether a participant can get from a fresh clone to a flow count and to the methods sentence.
- Whether the participant can tell where a value comes from, and what they would want to know about
  it before using it.
- Whether error messages say what to do next.
- Whether the flow counts are the same after the store is deleted and rebuilt, on the same computer.
- Whether the OpenAlex-only path (mode `openalex`) works for a researcher without Scopus access.

## What we are not testing

- The participant's skill in bibliometrics, systematic reviews, or screening.
- The quality of the participant's research question.
- Whether a particular Scopus key has the COMPLETE view. That is decided by Elsevier and the
  participant's institution. The Scopus-only mode (`scopus`) is not part of this round.
- Whether `numbers.json` reproduces on a different computer. That is a separate check.
- macOS and Linux. Those participants are welcome. Their results are reported on their own line.
- Performance at scale, or use by more than one person at a time.

**What T4 measures.** T4 measures whether the participant can execute the screening keys and read
the counts. It does not measure whether the participant discovers the keys without help. The keys
are shown in the view's help, and the guide points to them. A participant who never found the help
has still been measured on execution.

## Who takes part

Three to six peers who are independent of the project. Each is a researcher or research staff member
who runs, or may run, literature searches.

**Excluded from recruitment:** supervisors, lab-mates, friends, and anyone who has a reporting or
assessment relationship with the facilitator. The reason is the author bias described in
[protocol](protocol.md).

Each participant is asked at recruitment whether they have Scopus access. The answer assigns the
path. The answer is taken only for that purpose. In the session, the tool's own prompt is the first
encounter with the access question. See [tasks](tasks.md), T0.

Participants need the self-check in [tasks](tasks.md), sent three to five days ahead. The most
important item is a free OpenAlex key, created before the session.

## Session format and timeline

The participant drives on a shared virtual desktop. The facilitator watches and writes notes. The
planned minutes total 71. The hard end is minute 75.

| Block | What happens | Minutes | Planned clock |
| --- | --- | --- | --- |
| 1 | Consent confirmation, share check, and the four background questions. Consent was emailed beforehand. | 5 | 0 to 5 |
| 2 | T0: install (hard cap 15 minutes), doctor, throwaway init, sources check | 15 | 5 to 20 |
| 3 | T1: store the OpenAlex key | 4 | 20 to 24 |
| 4 | T2: create the project, write the search, size check | 6 | 24 to 30 |
| 5 | T3: capture, build, flow | 5 | 30 to 35 |
| 6 | T4: screen ten records | 8 | 35 to 43 |
| 7 | T5: export and provenance | 5 | 43 to 48 |
| 8 | T6: methods sentence and the Mode openalex paragraph | 3 | 48 to 51 |
| 9 | T7: delete the store, rebuild, compare | 4 | 51 to 55 |
| 10 | SUS and custom items | 6 | 55 to 61 |
| 11 | Debrief, four questions, then wrap-up | 5 | 61 to 66 |
| 12 | Slack, not assigned to a task | 5 | 66 to 71 |

**Clock checkpoints.**

- **Minute 25.** T2 must have started. Planned start is minute 24. If T2 has not started at minute 25,
  shorten T4 to five records.
- **Minute 50.** T5 must have started. Planned start is minute 43. If T5 has not started at minute 50,
  skip T6.
- **Priority.** T7 outranks T6. If there is time for only one, run T7.
- **Questionnaires always run,** even if a task was stopped or skipped.

**Per-task caps.** Each task stops at its target plus 3 minutes. T0 is capped by the install
limit of 15 minutes from the start of `git clone`, which comes first. Stop rules are in
[protocol](protocol.md).

## Roles

- **Participant.** Works on their own computer and shares one virtual desktop. Follows the guide.
  Thinks aloud. Can stop at any time.
- **Facilitator.** Runs the session, reads the run card, applies the hint thresholds, stops tasks at
  their caps, and writes the notes. The facilitator never sees or records an API key.
There is no note-taker. The session is not recorded (recording option C, decided by the owner). The
facilitator writes notes alone.

Facilitator, author, owner, and release decider are one person. This is a limitation, and the
session records it. Severity is assigned during the session, from the written definitions in the
[note sheet](note-sheet.md). It is not re-rated afterwards, so that a release date does not change
the record.

**Single unblinded coder.** The only coder is the facilitator, who wrote the tool. Nobody else
checks the notes against anything. The results are therefore reported as the author's observations.
They are not independent coding, and no inter-rater figure exists. Every summary says so. See
[questionnaires](questionnaires.md), section 5.7.

## Facilitator-directed additions to the guide

The guide is the participant's only written instruction. The kit adds the following steps. Each is
written into the scenario wording of its task, so the participant reads it as an instruction. Nothing
else is added.

| Addition | Where | Why |
| --- | --- | --- |
| Create a throwaway project with no `--sources` flag, so the tool asks the access question. | T0 | The guide's own commands always pass `--sources`, so the question is otherwise never shown. |
| Run `sources` on the throwaway project. | T0 | To see the mode the answer gave. |
| Export the bundle. | T5 | The guide has this step later; T5 needs `records.csv`. |
| Open `records.csv`. | T5 | Provenance is read from the export. |
| Open `numbers.json`, find the methods key, and read the Mode openalex paragraph. | T6 | The participant gets only these two items. |
| Delete `store\corpus.duckdb` only, then rebuild and print the flow. | T7 | The guide describes the store as disposable, but does not give the step as a test. |

The kit does not add any step that changes what the tool does.

## Background questions

Four questions are asked in block 1, before the install starts. Their wording and coding are in
[questionnaires](questionnaires.md). They are: role, review experience, terminal experience, and the
Scopus access answer recorded at recruitment. The Scopus answer is confirmed, not asked again.

## What the owner does with the results

- Adds the stumbles from each session to the stumble log within two working days.
- Files each blocker and major finding as an issue, with the template at
  `.github/ISSUE_TEMPLATE/evaluation_finding.md`. The issue carries no participant code and no date.
- Decides whether an open blocker stops the release.
- Reports aggregates, as the author's observations and not as independent coding. Per-person scores and
  anonymised quotes may be reported, under the consent text.
  See [questionnaires](questionnaires.md) for the analysis plan and the re-identification warning.

## Terms

The kit uses the tool's own words, such as "sources mode", "records identified", and "unsure or not
yet screened". It defines none of them for the participant. A participant who does not know a term
is a finding about the tool. The word "stumble" is used only in facilitator documents and never said
to a participant.

## Relation to the guided scenario

- The participant's instructions are the guided scenario, [Getting Started](../getting-started.md).
  The page on the main branch is an older version. A rewritten guide is in review as PR #276.
- Each session runs against one tag or commit. The facilitator records it, and checks that the
  guide the participant opens is the same text.
- The kit links to the guide only as a page. It does not link to a section inside it, because section
  anchors change when the rewrite lands.
- Not every guide step is run in every task. The tasks list the steps they use.

## Fallback

A pre-built project folder, `baseball-pitch`, is kept by the facilitator. It holds a capture and a
store built from the fixed query. It contains no key. The facilitator may send it to the participant
when the capture cannot run in the session, for example during an OpenAlex outage or an exhausted
daily allowance. Every task done on the fallback is marked `assisted-state` in the note sheet. If the
install failed, no fallback can run the tool. Record the install failure as a blocker and go to the
questionnaires.
