# Run card

One page for use during a session. Scenario wording is copied verbatim from [tasks](tasks.md). Rules are in
[protocol](protocol.md).

**Opening.** Read after consent: "I wrote this tool. You will not hurt my feelings. Criticism is the most useful thing
you can give me."

**Recording: option C.** Not recorded. No note-taker. Write shorthand only: time, task code, outcome letter (N, 1, 2, 3,
F), hint level, one verbatim phrase per stumble, and verbatim only in the verbatim fields. Write up within 30 minutes.
No clean-up later.

## Clock (planned 75, hard end 75)

| Minutes | Block | Check |
| --- | --- | --- |
| 0 to 5 | Consent, share check, BQ1 to BQ3. BQ4 recorded silently. | Working desktop shared, font 16 or larger |
| 5 to 20 | T0 install, doctor, throwaway init. Install cap 15 from `git clone`. Hard cap 20. | No questions during install |
| 20 to 24 | T1 key handover and credentials, cap 7 | Share paused for the key |
| 24 to 30 | T2 project and search, cap 9. **Minute 25: T2 must have started, or shorten T4 to five records.** | |
| 30 to 35 | T3 capture, build, flow, cap 8 | |
| 35 to 43 | T4 screen ten records, cap 11 | |
| 43 to 48 | T5 export and provenance, cap 8. **Minute 50: T5 must have started, or skip T6.** | |
| 48 to 51 | T6 methods sentence, cap 6. Skipped if T7 needs the time. | Paragraph pasted to the chat |
| 51 to 55 | T7 delete and rebuild, cap 7. Outranks T6. | Stop-first sequence if the delete fails |
| 55 to 65 | SUS and custom items, about 10 minutes. **Start by minute 60, whatever is unfinished.** | |
| 65 to 70 | Debrief: four questions, then a one-minute member check | |
| 70 to 75 | Slack | |

## Background questions (block 1, before install)

- BQ1: "What is your role?" Free text.
- BQ2: "How many literature reviews or systematic reviews have you worked on?" None. One or two. Three or more.
- BQ3: "How comfortable are you with a terminal or PowerShell?" Not at all. Occasionally. Regularly.
- BQ4: not read aloud. Record the recruitment answer (Scopus access: yes, no, or not sure) silently.

## Scenario texts (verbatim)

**T0.** Install the tool by following the guide, from the start of the Install section through the doctor step. After
doctor, run this command. Leave out any `--sources` flag. The tool will ask you a question. Answer it.

`uv run prismabib init access-check --title "Access check" --actor "<your name>"`

Then run this command.

`uv run prismabib sources access-check`

Think aloud as you go.

After T0, ask once: "What did you expect that question to mean?"

**T1.** Store your OpenAlex key with the guide's credentials step. Before you copy the key, I will ask you to pause the
share. Copy the key on a screen that is not shared, and close the account page. Then resume the share, paste the key at
the prompt, and press Enter. Nothing appears while you paste, and that is expected. Then clear the clipboard: press Win+V
and clear all. Then check the stored status.

**T2.** Create a project for this search: "baseball" AND "pose estimation" AND "pitcher". Put the search in the project
file. Set the year window in criteria.yaml as the guide says. Then check how many works the search finds before you
capture anything.

**T3.** Capture the search, build the store, and print the flow counts. Then tell me what the number next to "records
identified" means, and where the number came from.

**T4.** Open the screening view for this project and screen about ten records. Press ? to see the keys. Include some
records, exclude some with a reason, mark one unsure, move back and forward, and undo one decision. Then print the flow
counts again. Tell me what unsure or not yet screened means to you.

**T5.** Export the review bundle. Open records.csv in the exports folder, and pick one record that has an abstract. Is
there anything you would want to know about that text before using it in a review?

Ask only if the participant did not say where the abstract came from: "Where did it come from?"

**T6.** Read the methods sentence about the source in numbers.json, and the paragraph I paste into the call chat from the
limitations page. Would you cite this sentence in your methods section? Why or why not?

**T7.** Copy the flow counts you have now. Delete only the file store\corpus.duckdb in your project folder. Rebuild the
store, and print the flow counts again. Tell me whether the numbers match, and why you think they do or do not.

## Level 1 phrase per task (the 2-minute pointer)

- T0: "What does the guide say comes next?"
- T1: "What does the guide say to type?"
- T2: "Where does the guide say the count is?"
- T3: "Read the words on the line you are looking at."
- T4: "Try the help key."
- T5: "Look at the header row of records.csv."
- T6: "Search the file for the key."
- T7: "Which folder holds the captured search?"

## Four neutral probes

- "What are you looking for?"
- "What do you expect that to do?"
- "What does that line tell you?"
- "What would you try next?"

Do not say "good", "right", "that is it", or "no, try again".

## Hint thresholds (no progress; the timer is paused while a command runs)

- 2 minutes: level 1 (the phrase above).
- 4 minutes: level 2, point to the exact place on screen or in the guide.
- 6 minutes: level 3, say the exact command or value, or type it.

Earlier help only for safety. Log task, time and level. Words only if off-script. "Thank you, I will note that." is the
only reply to a complaint during a task.

## Key handover (T1)

Pause the share. Copy the key on a screen that is not shared. Close the account page. Resume the share. Paste into the
hidden prompt and press Enter. Clear the clipboard (Win+V, clear all). The facilitator never looks at the key page.

## Screenshots

Tool output only (errors, flow output). The participant sends it by call chat or email. Check it for keys and user names
before keeping it. Crop it to the output. Never the account page or `.env`.

## Stop rules

- Task reaches its cap: stop, record "stopped at cap", move on.
- Level 3 given and still failing: stop the task, record it as failed.
- Participant asks to stop: stop.
- Install cap reached: stop T0, record a blocker, go to the questionnaires.
- Capture cannot run: use the staged folder `baseball-pitch-staged` from T3 onward, marking tasks `assisted-state`.
- Wrong number on screen that the participant is about to use: let them finish, note the screen text verbatim.
- Share or network down for more than 10 minutes: stop the session.

## Incident rule (a key was seen, typed, or sent where others could see it)

1. Stop the share at once. Say: "Please stop sharing for a moment."
2. Say: "A key may have been visible. Please revoke it on the provider's website and create a new one. Do not send it to
   anyone."
3. Write "a key was visible, value not recorded", with the time and the task. Write no character of the key.
4. Log a bug, severity blocker, with "security" in the description.
5. The owner decides with the facilitator whether the session continues.

## Debrief member check (one minute)

Read back the stumbles noted so far. Ask: "What is missing or wrong?" Note the answer as the participant's correction.

Details for T7, including the stop-first sequence, are in [tasks](tasks.md).
