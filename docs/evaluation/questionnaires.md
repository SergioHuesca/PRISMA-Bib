# Questionnaires

The participant answers the background questions before the install starts, in block 1. The SUS and
custom items come after the tasks, in block 10. The debrief comes in block 11, with four questions.
Answers are stored outside the repository, under the session code.

Answers are coded by the facilitator. They are never typed into an issue or a commit.

## 0. Delivery

Use one method per session, and do not mix them.

- **Form link.** The facilitator sends a form link with no name field. The form holds the SUS items and
  the custom items. The participant fills it in during block 10. The response is stored with the session
  code the facilitator writes on it.
- **Read aloud.** The facilitator reads each item in order and writes the answer.

## 1. Background questions (block 1, before install)

The facilitator asks these in order. The wording is fixed. Each answer is written as given.

| ID | Question | Answers |
| --- | --- | --- |
| BQ1 | What is your role? | Free text. |
| BQ2 | How many literature reviews or systematic reviews have you worked on? | None. One or two. Three or more. |
| BQ3 | How comfortable are you with a terminal or PowerShell? | Not at all. Occasionally. Regularly. |
| BQ4 | Confirm the Scopus access answer you gave when you were recruited. | Yes. No. Not sure. |

BQ4 is a confirmation, not a question. The facilitator does not ask about entitlement again, and does not
explain the question. The answer assigns the path, as set out in [tasks](tasks.md).

## 2. System Usability Scale

Use the ten standard items, in this order, with the wording unchanged. "This system" means prismabib as the
participant used it in this session.

The scale runs from 1, Strongly disagree, to 5, Strongly agree.

| No. | Item | Type |
| --- | --- | --- |
| 1 | I think that I would like to use this system frequently. | Odd |
| 2 | I found the system unnecessarily complex. | Even |
| 3 | I thought the system was easy to use. | Odd |
| 4 | I think that I would need the support of a technical person to be able to use this system. | Even |
| 5 | I found the various functions in this system were well integrated. | Odd |
| 6 | I thought there was too much inconsistency in this system. | Even |
| 7 | I would imagine that most people would learn to use this system very quickly. | Odd |
| 8 | I found the system very cumbersome to use. | Even |
| 9 | I felt very confident using the system. | Odd |
| 10 | I needed to learn a lot of things before I could get going with this system. | Even |

**Note on item 8.** "Cumbersome" is an uncommon word for some participants. If they ask what it means,
say: "Use your own understanding." Do not define it. Record that the question was asked.

**Scoring.** Let x be the response to an item, from 1 to 5.

- Odd item: contribution is x minus 1, from 0 to 4.
- Even item: contribution is 5 minus x, from 0 to 4.
- Add the ten contributions. The total is 0 to 40.
- Multiply by 2.5. The SUS score is 0 to 100.

**Worked example.** Responses 4, 2, 5, 1, 4, 2, 5, 2, 4, 1 for items 1 to 10.

| Item | Response | Type | Contribution |
| --- | --- | --- | --- |
| 1 | 4 | Odd | 4 minus 1 = 3 |
| 2 | 2 | Even | 5 minus 2 = 3 |
| 3 | 5 | Odd | 5 minus 1 = 4 |
| 4 | 1 | Even | 5 minus 1 = 4 |
| 5 | 4 | Odd | 4 minus 1 = 3 |
| 6 | 2 | Even | 5 minus 2 = 3 |
| 7 | 5 | Odd | 5 minus 1 = 4 |
| 8 | 2 | Even | 5 minus 2 = 3 |
| 9 | 4 | Odd | 4 minus 1 = 3 |
| 10 | 1 | Even | 5 minus 1 = 4 |
| | | Total | 34 |

The SUS score is 34 times 2.5, which is 85.

**Rules.**

- If an item is blank, record the gap and do not score that participant. Tell the owner.
- A SUS score is not a percentage. A value near 68 is often quoted as an average across many studies. Use it
  only as a loose reference.
- Cite the scale as Brooke (1996) in any report. The reference is Brooke, J. (1996). SUS: A "quick and dirty"
  usability scale. In P. W. Jordan, B. Thomas, B. A. Weerdmeester, and I. L. McClelland (Eds.), *Usability
  Evaluation in Industry* (pp. 189-194). Taylor and Francis.

## 3. Custom items

These items are the kit's own. They are not a validated instrument. Report them descriptively.

The agree items run from 1, Strongly disagree, to 5, Strongly agree. Each may be answered "Not applicable"
where the item says so. "Not applicable" is coded separately, and is not a zero.

| ID | Item | Scale | Open question |
| --- | --- | --- | --- |
| C1 | The question "Do you have access to Scopus?" was clear. | 1 to 5 agree | What, if anything, about the question was unclear? |
| C2 | I could tell where each value in the results came from. | 1 to 5 agree | Describe how you would find out where a value came from. |
| R1 | I could not tell where the numbers came from. (Reversed) | 1 to 5 agree | None. |
| C3 | I am confident I could reproduce these numbers from the same files. | 1 to 5 agree | Describe how you would reproduce them, or what would stop you. |
| C4 | When something went wrong, the message I saw told me what to do next. | 1 to 5 agree, or Not applicable | Quote a message that was unclear, if there was one. |
| C5 | I would trust these counts enough to report them in a manuscript. | 1 to 5 agree | What, if anything, would you need to see first? |

**Scoring R1.** R1 is reversed before analysis: the score is 6 minus the response. Report it with the others.

**Workload.** Two single items. Each is a number from 1 to 7. Neither has an open question.

| ID | Item | Scale |
| --- | --- | --- |
| W1 | How mentally demanding was the session? | 1 = very low, 7 = very high |
| W2 | How much time pressure did you feel during the session? | 1 = none, 7 = very high |

## 4. Debrief (block 11, four questions)

The debrief starts after the questionnaires. Explanations are given here, and only here, if the participant
asks. Ask the four questions in this order, aloud. Write each answer as given. Do not comment on answers.

1. What did you expect the tool to do at the start, and what happened?
2. Which number on the flow output was least clear to you?
3. Was there a point where you felt less sure about the results? Describe it.
4. Is there anything you would want to know about the results before using them in a review?

The facilitator also checks, once, that nothing on screen should have been shown. This check is not a debrief
question. If the answer is yes, follow the incident rule in [protocol](protocol.md).

## 5. Analysis plan

This plan describes the sessions. It does not test a hypothesis.

**5.1 Session record.** One row per session: session code, date, tag or commit, platform, path, recording
(option C, not recorded), total time, and any stop.

**5.2 Background and SUS.**

- Report BQ1 to BQ4 per participant.
- Score SUS per participant, using section 2.
- With fewer than five participants, list each individual value, and do not give a median.
- With five or more, give the median and each individual value.

| Session | BQ2 | BQ3 | BQ4 | SUS | Items blank |
| --- | --- | --- | --- | --- | --- |
| P01 | | | | | |
| P02 | | | | | |
| P03 | | | | | |
| P04 | | | | | |
| P05 | | | | | |
| P06 | | | | | |

**5.3 Task outcome table.** Each participant has one outcome per task. The highest hint level used decides it.
A task that reached its cap is "Failed", whatever the reason. The headline column is "No hint".

| Task | No hint | Hint 1 only | Hint 2 | Hint 3 | Failed |
| --- | --- | --- | --- | --- | --- |
| T0 Install, doctor, access question | | | | | |
| T1 Credentials | | | | | |
| T2 Project and search | | | | | |
| T3 Capture, build, flow | | | | | |
| T4 Screening | | | | | |
| T5 Export and provenance | | | | | |
| T6 Methods sentence | | | | | |
| T7 Delete and rebuild | | | | | |

Tasks with an "assisted-state" mark are reported in their own row, and not added to the rows above.

**5.4 Custom items, workload, and debrief.** Give each item's values per participant, with R1 reversed.
With five or more participants, add the median. With fewer, list values only. Group open answers by theme,
with the session code. Quote an answer in full only where it describes a stumble.

**5.5 Stumble log.** Build a table of counts, with category as rows and severity as columns. List every
blocker and every major by id and task. Count minor ones without listing them.

A stumble met by several participants is counted once, and recorded as "seen by n of N".

**5.6 Reporting.** Each summary states n, the tag or commit, the dates, the platform mix, the path mix, and
the recording option (C, not recorded). Do not compute p-values or confidence intervals. Describe the results. Do not generalise.

Per-person scores and anonymised quotes may be reported, as the consent text says. With three to six
participants, a reader may be able to identify a person from a score or a quote. Report them with that
warning.

**5.7 Single unblinded coder (limitation).** The only coder is the facilitator, who is also the author of the
tool. The analysis is by that one unblinded coder. No second coder is possible: the session is not recorded
and no second person attends. Every report states this. Results are reported as the author's observations, not
as independent coding. No inter-rater agreement figure is reported, because none exists. Read every count and
every quote with that in mind.

**5.7a Confidence per task.** Each task row on the note sheet carries a confidence field: high, medium, or low.
Report the counts of low-confidence rows next to the task table. A low-confidence row is still reported, and
is marked as low confidence.

**5.8 Release.** The owner decides what a finding means for the release. This plan only counts. A finding is
not a decision.
