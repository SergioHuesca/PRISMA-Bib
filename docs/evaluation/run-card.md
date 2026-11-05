# Run card

Read at the start, after consent: "I wrote this tool. You will not hurt my feelings. Criticism is the most useful
thing you can give me."

One page for use during a session.

**Recording: option C.** Not recorded. No note-taker. Write live shorthand only: time, task code, outcome letter
(N, 1, 2, 3, F), hint level, and at most one verbatim phrase per stumble. Write up the full note sheet within
30 minutes after the session. Do not clean up the notes later, except to expand shorthand. The scenario texts below are copied from [tasks](tasks.md). Read them as
written. Details, success criteria and records are in [tasks](tasks.md). Rules are in [protocol](protocol.md).

## Clock (hard end at minute 75)

| Minute | Block | Check |
| --- | --- | --- |
| 0 to 5 | Consent confirmed, share check, background BQ1 to BQ4 (before install) | Virtual desktop, font 16 or larger, notifications off |
| 5 to 20 | T0 install, doctor, throwaway init, sources. Install cap 15 minutes from `git clone`. | |
| 20 to 24 | T1 credentials, cap 7 | |
| 24 to 30 | T2 project and search, cap 9. **Checkpoint: T2 must have started by minute 25, or shorten T4 to five records.** | |
| 30 to 35 | T3 capture, build, flow, cap 8 | |
| 35 to 43 | T4 screen ten records, cap 11 | |
| 43 to 48 | T5 export and provenance, cap 8. **Checkpoint: T5 must have started by minute 50, or skip T6.** | |
| 48 to 51 | T6 methods sentence and Mode openalex paragraph, cap 6. Skipped if T7 needs the time. | |
| 51 to 55 | T7 delete and rebuild, cap 7. Outranks T6. | |
| 55 to 61 | SUS and custom items. Always runs. | |
| 61 to 66 | Debrief, four questions | |
| 66 to 71 | Slack | |

## Scenario texts (verbatim)

**T0.** Install the tool by following the guide, from the start of the Install section through the doctor
step. After doctor, run this command. Leave out any `--sources` flag. The tool will ask you a question.
Answer it.

`uv run prismabib init access-check --title "Access check" --actor "<your name>"`

Then run this command.

`uv run prismabib sources access-check`

Think aloud as you go.

After T0, ask once: "What did you expect that question to mean?"

**T1.** Store your OpenAlex key with the guide's credentials step. Copy the key from your openalex.org account
page on the other desktop. Paste it at the prompt and press Enter. Nothing appears while you paste, and that is
expected. Then check the stored status.

**T2.** Create a project for this search: "baseball" AND "pose estimation" AND "pitcher". Put the search in the
project file. Set the year window in criteria.yaml as the guide says. Then check how many works the search finds
before you capture anything.

**T3.** Capture the search, build the store, and print the flow counts. Then tell me what the number next to
"records identified" means, and where the number came from.

**T4.** Open the screening view for this project and screen about ten records. Press ? to see the keys. Include
some records, exclude some with a reason, mark one unsure, move back and forward, and undo one decision. Then
print the flow counts again. Tell me what unsure or not yet screened means to you.

**T5.** Export the review bundle. Open records.csv in the exports folder, and pick one record that has an
abstract. Is there anything you would want to know about that text before using it in a review?

Ask only if the participant did not say where the abstract came from: "Where did it come from?"

**T6.** Read the methods sentence about the source in numbers.json, and the paragraph the facilitator gives you
from the limitations page. Would you cite this sentence in your methods section? Why or why not?

Give the participant only the `methods.source_sentence` value and the "Mode `openalex`" paragraph. Nothing else.

**T7.** Copy the flow counts you have now. Delete only the file store\corpus.duckdb in your project folder. Rebuild
the store, and print the flow counts again. Tell me whether the numbers match, and why you think they do or do not.

## Four neutral probes

- "What are you looking for?"
- "What do you expect that to do?"
- "What does that line tell you?"
- "What would you try next?"

Do not say "good", "right", "that is it", or "no, try again".

## Hint thresholds (time without progress)

- 2 minutes: level 1, a pointer with no content.
- 4 minutes: level 2, point to the exact place on screen or in the guide.
- 6 minutes: level 3, say the exact command or value, or type it.

Earlier help only for safety. Log every hint with the words used. Never give an answer, a mode, or a judgement.
"Thank you, I will note that." is the only reply to a complaint during a task. Explain only in the debrief.

## Stop rules

- Task reaches its cap (target plus 3 minutes): stop, record "stopped at cap", move on.
- Level 3 given and still failing: stop the task, record it as failed.
- Participant asks to stop: stop.
- Install cap reached: stop T0, record a blocker, go to the questionnaires.
- Capture cannot run: use the staged fallback from T3 onward, marking tasks `assisted-state`.
- Wrong number on screen that the participant is about to use: let them finish, note the screen text verbatim.
- Shared screen or network down for more than 10 minutes: stop the session.

## Incident rule (a key was seen or typed where others could see it)

1. Stop the share at once. Say: "Please stop sharing for a moment."
2. Say: "A key may have been visible. Please revoke it on the provider's website and create a new one. Do not send it to anyone."
3. Write "a key was visible, value not recorded" with the time and the task. Write no character of the key.
4. Log a bug, severity blocker, with "security" in the description.
5. Owner decides with the facilitator whether the session continues.

## Before T7

Do not tell the participant to stop the screening server. Note whether they stopped it (Ctrl+C) on their own.
If they did not, and the delete fails, record the exact error verbatim and log a docs gap.
