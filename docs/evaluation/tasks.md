# Tasks

This page holds the full task definitions. The [run card](run-card.md) holds the same scenario wording, verbatim,
for use during a session. If the two ever differ, the run card's wording is read to the participant, and this page
is corrected to match.

Each task has the same parts:

- **Scenario.** Read to the participant, in these words.
- **Starting state.** What must be true before the task starts.
- **Procedure.** Each step is a guide step or a facilitator-directed addition, listed in [index](index.md).
- **Success criteria.** What the facilitator observes. All must hold.
- **Cap.** Target plus 3 minutes. Stop rules are in [protocol](protocol.md).
- **Level 1 phrase.** The one scripted pointer for the task. Used at the 2-minute threshold.
- **Records.** A checklist of 3 to 5 items. Verbatim wording only in the verbatim-allowed fields in
  [protocol](protocol.md), section 6.

Commands are for PowerShell in Windows Terminal. Run every command from the root of the clone, with `uv run` in
front, as the guide says.

Every session uses the same fixed search: `"baseball" AND "pose estimation" AND "pitcher"`. Participants do not
choose a topic.

**Terms.** The tasks use the tool's own words, such as "sources mode" and "records identified". They define none of
them for the participant.

## Timeline (planned minutes, 75 in total)

| Minutes | Block | Budget |
| --- | --- | --- |
| 0 to 5 | Consent confirmation, share check, BQ1 to BQ3 asked, BQ4 recorded silently (see the run card) | 5 |
| 5 to 20 | T0: install (cap 15 minutes from `git clone`), doctor, throwaway init, sources. Hard cap 20 minutes. | 15 |
| 20 to 24 | T1: store the OpenAlex key (includes the key handover) | 4 |
| 24 to 30 | T2: project and search. **T2 must start by minute 25.** | 6 |
| 30 to 35 | T3: capture, build, flow | 5 |
| 35 to 43 | T4: screen ten records | 8 |
| 43 to 48 | T5: export and provenance. **T5 must start by minute 50.** | 5 |
| 48 to 51 | T6: methods sentence and the Mode openalex paragraph | 3 |
| 51 to 55 | T7: delete the store and rebuild | 4 |
| 55 to 65 | Questionnaires: SUS and custom items, about 10 minutes. **Start by minute 60, whatever is unfinished.** | 10 |
| 65 to 70 | Debrief: four questions and a one-minute member check | 5 |
| 70 to 75 | Slack | 5 |

The total is 75. The hard end is minute 75. The rules for overruns are in [protocol](protocol.md), section 4.

## Participant self-check (sent three to five days ahead)

The participant does this alone, and returns the results to the facilitator by email. The email carries no key and no
file.

1. Open PowerShell. Run `git --version`. It prints a version.
2. Run `uv --version`. It prints a version. If the command is not found, tell the facilitator.
3. Open `https://api.openalex.org` in a browser. A page of text appears.
4. Confirm you have a free OpenAlex key from your openalex.org account. Create the key now if you do not have one.
   Keep it in your account. Do not send it.
5. Choose the working folder. It must be a plain local path, such as `C:\Users\<you>\work`. Do not use OneDrive,
   Desktop, or a folder a sync client watches. Create the folder now.
6. Confirm you can share a window and a sound on the call. Set up a Windows virtual desktop with Win+Ctrl+D. Your
   working desktop holds the terminal, Notepad, one browser window, and a CSV viewer. You will share **the working
   desktop**. Keep the account page on another desktop.
7. Check that your laptop is not blocked by IT policy from installing software in your user profile. If it is, tell
   the facilitator now.
8. Check clipboard history. Press Win+V. Clear the list, and turn clipboard history off in Windows settings if you
   prefer. Copied keys must not stay in it.
9. Check that you can paste into Windows Terminal with Ctrl+V or a right-click, as your terminal is set up. Paste
   something harmless, then clear the line.
10. Set the Windows Terminal font size to 16 or larger.

**During the install.** The install in T0 may run for several minutes with little output. That silence is normal. Do
not cancel it. Wait, and read the guide if you wish. No questions are asked during the install.

## Facilitator pre-session check

The facilitator runs this on their own computer, a few days before the first session, and again on the day if the tag
under test changes. It takes about 30 minutes, most of it the install.

1. Clone the repository into a new folder. Check out the tag under test.

   ```powershell
   git clone https://github.com/SergioHuesca/PRISMA-Bib.git
   cd PRISMA-Bib
   git checkout <tag or commit under test>
   ```

2. Install, record the version, and run `doctor`. Record the exit code.

   ```powershell
   uv sync --extra viz --extra notebook
   uv run prismabib --version
   uv run prismabib doctor
   ```

3. Open `docs/getting-started.md` in the clone. Check that it is the guide the participant opens.
4. Store your own OpenAlex key. Never use a participant's key.

   ```powershell
   uv run prismabib credentials set openalex
   ```

5. Run the fixed search in a throwaway project, `kit-check`, and record the real output of the size check. This is the
   check that reports the count for the fixed search, 108 works on the date the query was last run.

   ```powershell
   uv run prismabib init kit-check --sources openalex --title "Kit check" --actor "<your name>"
   ```

   Put the search in `[openalex_query]` of `projects\kit-check\project.toml`, then run:

   ```powershell
   uv run prismabib openalex-search kit-check --budget 1
   ```

   Record the last line of the output verbatim. On the tag under test, the stop line may print the count, or may not.
   The line that prints the count, `OpenAlex reports <N> works for this query; <K> more page(s) of 100 needed.`, exists
   only once polish-2 is released. Record which form the tag under test prints. This check has no other purpose.

6. Finish the throwaway capture, so the staged folder can be built from it:

   ```powershell
   uv run prismabib openalex-search kit-check --budget 5 --dry-run
   uv run prismabib openalex-search kit-check --budget 5
   uv run prismabib build kit-check --rebuild
   uv run prismabib flow kit-check
   uv run prismabib export kit-check
   ```

7. **Lock test (settles the question in gate G10).** Start the screening view with the slug set. Confirm one record
   shows. While it is still running, try to delete `projects\kit-check\store\corpus.duckdb`. Record whether the
   delete succeeds, and the exact error if it does not. Then stop the server with Ctrl+C.

   ```powershell
   $env:PRISMABIB_NOTEBOOK_SLUG = "kit-check"
   $env:PRISMABIB_NOTEBOOK_REVIEWER = "<your name>"
   uv run panel serve notebooks/01_screen_title_abstract.ipynb --show
   ```

   If the delete fails while the server runs, T7 uses the stop-first sequence in the T7 section, and the guide gap is
   logged as in T7.

8. Start the screening view once more with the slug unset. Confirm the red **Screening cannot start** message
   appears. Stop it with Ctrl+C.
9. Build the staged fallback. Copy the `kit-check` project to a folder named `baseball-pitch-staged`. In its
   `project.toml`, set the recorded actor to `kit-staged`. Check the whole folder for keys, and remove any file that
   holds one. Test that the facilitator can send the folder, and that a participant can place it under `projects`.
10. Prepare the note sheet copy, the run card, and the consent email. Check the placeholders and the gate in
    [protocol](protocol.md), section 7.

## Path choice

The path is set from the Scopus answer recorded at recruitment. The participant is not asked again in the session.

| Recruitment answer | Path | Mode |
| --- | --- | --- |
| No, or not sure | OpenAlex-only | `openalex` |
| Yes | OpenAlex-only, unless the owner has approved the Scopus variant for this participant | `openalex` |
| Yes, with the Scopus variant approved | Combined | `scopus+openalex` |

A participant with a Scopus key stores that key in T1, with their own key, whichever path they take. The Scopus
variant spends the participant's Scopus quota, and `search` has no size check before it runs. The owner approves it
per participant, with a narrow search and a time cap. The default is the OpenAlex-only path, so that all participants
do the same tasks.

## T0. Install, doctor, and the access question

**Scenario (read to the participant):**

> Install the tool by following the guide, from the start of the Install section through the doctor step. After
> doctor, run this command. Leave out any `--sources` flag. The tool will ask you a question. Answer it.
>
> `uv run prismabib init access-check --title "Access check" --actor "<your name>"`
>
> Then run this command.
>
> `uv run prismabib sources access-check`
>
> Think aloud as you go.

The throwaway step is a facilitator-directed addition. The guide's own commands always pass `--sources`, so the
question is otherwise never shown.

**Starting state.** Windows 11 user account, with git, uv, and Python 3.11 or newer installed. No clone, no `.env`.

**Procedure.**

1. Clone the tag under test, then run `uv sync --extra viz --extra notebook`. Keep the silence. The install cap is 15
   minutes from the start of `git clone`.
2. Run `uv run prismabib --version`, then `uv run prismabib doctor`.
3. Run the throwaway `init` above with no `--sources` flag. The tool asks its question. The participant answers it. If
   the answer is y, it asks a second question. The participant answers that too.
4. Run the `sources` command above.
5. The facilitator asks once: "What did you expect that question to mean?" The answer is written verbatim.

The facilitator does not read the access question aloud, and does not ask about Scopus access. The tool's own prompt
is the first encounter with the wording of the question. The recruitment answer was recorded silently and is not read
aloud. The `access-check` folder is not used again, and the participant may delete `projects\access-check`.

**Success criteria (observable).**

1. `git clone` and `uv sync` finish with no error, within the 15-minute install cap.
2. `--version` prints a line that starts with `prismabib`.
3. `doctor` exits with code 0. In PowerShell, `$LASTEXITCODE` is 0 right after the command. No line starts with `FAIL`.
   A `warn` line is recorded, not counted as a failure.
4. The tool asks "Do you have access to Scopus?" when `init` runs with no `--sources` flag.
5. `sources access-check` prints a mode, and the participant has answered the question the tool asked.

**Cap.** Hard cap 20 minutes. The install hard cap of 15 minutes comes first. If the install is capped, T0 stops, the
failure is a blocker, and the session goes to the questionnaires.

**Level 1 phrase.** "What does the guide say comes next?"

**Records checklist.**

- The clone and `uv sync` durations, and any error text verbatim.
- Every `doctor` line that is not `ok`, verbatim, and the exit code.
- The number of times the tool asked the access question.
- The mode `sources` printed.
- Verbatim: the answer to "What did you expect that question to mean?"

## T1. Store the OpenAlex key

**Scenario (read to the participant):**

> Store your OpenAlex key with the guide's credentials step. Before you copy the key, I will ask you to pause the
> share. Copy the key on a screen that is not shared, and close the account page. Then resume the share, paste the
> key at the prompt, and press Enter. Nothing appears while you paste, and that is expected. Then clear the clipboard:
> press Win+V and clear all. Then check the stored status.

**Starting state.** T0 is done and doctor passed. The OpenAlex key was created before the session.

**The key handover (fixed sequence).** The facilitator does not look at the key page at any time.

1. The participant pauses or stops the share.
2. The participant creates or copies the key on their own screen, which is not shared.
3. The participant closes the account page after copying.
4. The participant resumes the share on the working desktop.
5. The participant pastes the key into the hidden prompt and presses Enter. Nothing appears.
6. The participant clears the clipboard: Win+V, then clear all.

The rehearsal (gate G10) records what the share shows during a desktop switch on the chosen call platform. That
behaviour is unverified in this kit. If the share shows the account page at any point, the incident rule applies. See
[protocol](protocol.md), section 5.

**Procedure.**

```powershell
uv run prismabib credentials set openalex
uv run prismabib credentials status
```

Scopus holders store their Scopus key the same way:

```powershell
uv run prismabib credentials set scopus
uv run prismabib credentials test scopus
```

The Scopus prompt has two lines. The second, `SCOPUS_INSTTOKEN`, is optional, and the participant presses Enter to
skip it if the library issued no token. The test makes one request and prints one result line.

The Connect Scopus panel is a Python class, and no notebook in the repository shows it. It is not used in this round.

**Success criteria (observable).**

1. `credentials set openalex` prints `stored OPENALEX_API_KEY in <path>\.env`.
2. `credentials status` prints `OPENALEX_API_KEY: set (.env)` and `git-ignored by a tracked rule: yes`.
3. The key does not appear on the shared desktop, and does not appear in any output.
4. The clipboard is cleared, as step 6 says.
5. For a Scopus holder, the test line is recorded verbatim. A result other than `scopus: ok` is not the participant's
   failure.

**Cap.** 7 minutes (target 4, plus 3).

**Level 1 phrase.** "What does the guide say to type?"

**Records checklist.**

- The two status lines, as printed, with the value shown only as "set" or "not set".
- Whether the prompt was retried, refused, or confusing, and the words used if off-script.
- The Scopus test line, verbatim, for Scopus holders.
- Whether the share was paused and resumed as the sequence says, yes or no.
- Whether any key-like text appeared on the shared desktop. Yes or no only.

## T2. Create the project and write the search

**Scenario (read to the participant):**

> Create a project for this search: "baseball" AND "pose estimation" AND "pitcher". Put the search in the project file.
> Set the year window in criteria.yaml as the guide says. Then check how many works the search finds before you capture
> anything.

**Starting state.** T1 is done.

**Procedure (guide walkthrough, steps 1 to 4).**

1. Create the project, with the flag for the path:

   ```powershell
   uv run prismabib init baseball-pitch --sources openalex --title "Baseball pitching and pose estimation" --actor "<your name>"
   ```

2. Open `projects\baseball-pitch\project.toml`. In the `[openalex_query]` table, set `search` to
   `"baseball" AND "pose estimation" AND "pitcher"`. Do not add a second `[openalex_query]` table.
3. Open `projects\baseball-pitch\criteria.yaml`. Set `year_start` and `year_end` under `temporal`, as the guide's step 3
   says. Record the values.
4. Check the size. This call requests one page.

   ```powershell
   uv run prismabib openalex-search baseball-pitch --budget 1
   ```

5. Read the count. Use whichever source shows it:
   - If the stop line prints a count, read it there.
   - Otherwise, while the run is unsealed, read `"total_count": <number>,` from
     `projects\baseball-pitch\raw\openalex-search\<run id>\progress.json`. The file is removed when the run seals.
   - If the run sealed on this call, the count is on the sealed line, as "N works captured of M reported by OpenAlex".
6. Say what the participant will do with the count, and why. Both answers are successes.

**Facilitator notes.**

- The count can drift. A different number is not an error. The participant reports the number they see.
- If the size check seals the run, T3 step 2 prints `a sealed run of this query already exists`. Log that as a finding
  about the flow between the check and the capture. Do not explain it to the participant.
- The pre-session check (step 5) recorded the form of the stop line on the tag under test.

**Scopus variant (only if approved).** The project is created with `--sources scopus+openalex`. The search goes in the
`[query]` table, as the guide's Scopus section describes. There is no size check for `search`, which spends quota
before it reports `total_results`. The owner agrees a narrow search and a time cap in advance.

**Success criteria (observable).**

1. `init` prints `Created project 'baseball-pitch' at ...` and `Sources mode: openalex`.
2. `project.toml` has one `[openalex_query]` table with `search` set to the query.
3. The size check runs and prints a stop line or a sealed line.
4. The participant reads a count and states what they will do with it, with a reason.

**Cap.** 9 minutes (target 6, plus 3). **Checkpoint.** T2 must start by minute 25.

**Level 1 phrase.** "Where does the guide say the count is?"

**Records checklist.**

- The `init` output lines, and any TOML or YAML error, verbatim.
- The count the participant read, and the source they read it from.
- The decision and reason, in the participant's words, if off-script.
- Whether the size check sealed the run.

## T3. Capture, build, and read the flow

**Scenario (read to the participant):**

> Capture the search, build the store, and print the flow counts. Then tell me what the number next to "records
> identified" means, and where the number came from.

**Starting state.** T2 is done.

**Procedure (guide walkthrough, steps 5 to 7).**

```powershell
uv run prismabib openalex-search baseball-pitch --budget 5 --dry-run
uv run prismabib openalex-search baseball-pitch --budget 5
uv run prismabib build baseball-pitch --rebuild
uv run prismabib flow baseball-pitch
```

**Success criteria (observable).**

1. The dry run prints `OpenAlex key: set`.
2. The real run prints `run <id> sealed: N works captured of M reported by OpenAlex`. If T2 sealed the run, it prints
   `a sealed run of this query already exists`. That line is logged as a finding, not as a participant failure.
3. `build --rebuild` prints `Rebuilt store at ...` and a `search runs loaded` line with 1.
4. `flow` prints `records identified (OpenAlex meta.count)` with M.
5. The participant says the number is the count OpenAlex reported for the search at capture time. They name where they
   found it.
6. If `flow` prints a warning on the error stream that the counts do not close, record it verbatim. Do not explain it.

**Cap.** 8 minutes (target 5, plus 3).

**Level 1 phrase.** "Read the words on the line you are looking at."

**Records checklist.**

- The last output line of each command, verbatim.
- Whether the run sealed on this call, or was already sealed.
- The number on the `records identified` line, and M.
- Verbatim: the participant's meaning of the number.

## T4. Screen ten records

**Scenario (read to the participant):**

> Open the screening view for this project and screen about ten records. Press ? to see the keys. Include some records,
> exclude some with a reason, mark one unsure, move back and forward, and undo one decision. Then print the flow counts
> again. Tell me what unsure or not yet screened means to you.

**Starting state.** T3 is done.

**Procedure (guide walkthrough, steps 8 and 9).**

```powershell
$env:PRISMABIB_NOTEBOOK_SLUG = "baseball-pitch"
$env:PRISMABIB_NOTEBOOK_REVIEWER = "<your name>"
uv run panel serve notebooks/01_screen_title_abstract.ipynb --show
```

The keys come from the view's own key map. Press `?` to show them.

| Key | Action |
| --- | --- |
| `i` | Include the record. |
| `e`, then a digit | Exclude it. The digit picks the reason. `1` is `OFF_TOPIC`, the first starter code. |
| `u` | Mark unsure. The record stays in the queue. |
| `n` or `p` | Next or previous record, without a decision. |
| `z` | Undo. |
| `?` | Show or hide the help. |

After the decisions, the participant runs `flow` again.

**Success criteria (observable).**

1. The view opens in the browser and shows one record with a title and a year. Author names and citation counts are not
   shown. That is the default.
2. The participant makes about ten decisions, using include, exclude with a reason, unsure, the move keys, and undo.
3. After the decisions, the lines under `Screening -- title/abstract, from logged human decisions` move. `excluded` rises
   by one for each exclusion. `sought for full-text retrieval` counts the records advanced to full text, which the guide
   describes as the records included. The participant says which lines moved.
4. The participant answers the question about "unsure or not yet screened".
5. If the red **Screening cannot start** message appears, the participant reads the reason and fixes it. That is a
   stumble, whether or not the fix works the first time.

This task measures execution. It does not test whether the participant discovers the keys unaided. The keys are in the
view's help, and the guide points to them.

**Cap.** 11 minutes (target 8, plus 3). T4 is the task that the minute 25 checkpoint shortens to five records. See
[protocol](protocol.md), section 4.

**Level 1 phrase.** "Try the help key."

**Records checklist.**

- Decisions by type: include, exclude, unsure, undo. Counts only, no titles.
- Keys used, by name only, and where the participant hesitated.
- The flow lines that moved, and whether the participant explained them.
- Verbatim: the participant's meaning of "unsure or not yet screened".

## T5. Export and provenance

**Scenario (read to the participant):**

> Export the review bundle. Open records.csv in the exports folder, and pick one record that has an abstract. Is there
> anything you would want to know about that text before using it in a review?

**Starting state.** T4 is done, or was shortened or stopped.

**Procedure (facilitator-directed addition to the guide, from its step 11).**

```powershell
uv run prismabib export baseball-pitch
```

Then open `projects\baseball-pitch\exports\records.csv`. The export prints `Exported baseball-pitch to ...`, then count
lines and a `git commit` line. It can print a warning about the prismabib commit, for example that the tree is dirty or
the commit is on no remote branch. Record any warning verbatim. Do not fix it in the session.

If the participant does not say where the abstract came from, the facilitator asks once: "Where did it come from?" Nothing
more is asked about the source.

Do not push anything. If the guide asks the participant to commit a file in their own clone, they may commit it locally.

**Success criteria (observable).**

1. The export line is printed, and `exports` holds `records.csv`, `provenance_conflicts.csv`, `numbers.json`, and
   `manifest.json`.
2. The participant gives an answer to the question about the text. Recorded verbatim.
3. The participant names where the abstract came from, from `records.csv` alone. The header holds `abstract_source`,
   `abstract_level`, `abstract_outcome`, and `abstract_payload_ref`. This criterion does not depend on T6.
4. The participant names one way they could check the text. Any check they can carry out counts. For example, the record
   id has the form `openalex:W...`, which they could look up on openalex.org.

**Cap.** 8 minutes (target 5, plus 3). **Checkpoint.** T5 must start by minute 50. If not, skip T6.

**Level 1 phrase.** "Look at the header row of records.csv."

**Records checklist.**

- The export lines, verbatim, including any warning.
- The record id only, not the title.
- Verbatim: the participant's answer to the question.
- The source the participant named, and whether it came from the file or from the follow-up question.

## T6. Methods sentence and the Mode openalex paragraph

**Scenario (read to the participant):**

> Read the methods sentence about the source in numbers.json, and the paragraph I paste into the call chat from the
> limitations page. Would you cite this sentence in your methods section? Why or why not?

**Starting state.** T5 is done.

**Procedure.**

1. The participant finds the key `methods.source_sentence` in `projects\baseball-pitch\exports\numbers.json`. Nothing
   else in `numbers.json` is opened.
2. The facilitator pastes into the call chat the paragraph of `docs\methodology\limitations.md` that starts with
   "Mode `openalex`", from the section "Coverage: one source per project, chosen by its source mode". The participant
   reads it from the chat. Nothing else from the limitations page is shown. The facilitator's reference is
   [Limitations](../methodology/limitations.md).

The `fill` step of the guide is not part of this round.

**Success criteria (observable).**

1. The participant finds the sentence and reads the query and date it contains.
2. The participant answers the question with a reason. The facilitator does not name a missing item.

**Cap.** 6 minutes (target 3, plus 3). **Priority.** T7 outranks T6. If only one can run, run T7.

**Level 1 phrase.** "Search the file for the key."

**Records checklist.**

- Verbatim: the participant's answer and reason.
- Any item the participant named as missing, verbatim.
- Whether the participant read the sentence aloud.

## T7. Delete the store and rebuild

**Scenario (read to the participant):**

> Copy the flow counts you have now. Delete only the file store\corpus.duckdb in your project folder. Rebuild the store,
> and print the flow counts again. Tell me whether the numbers match, and why you think they do or do not.

**Starting state.** T4 is done. The flow counts include the screening decisions.

**Procedure.**

1. Before anything else, the participant keeps the first flow output in Notepad, and does not retype it.
2. The participant deletes `projects\baseball-pitch\store\corpus.duckdb`. Nothing else is deleted. The folder `raw` holds
   the capture.
3. The participant rebuilds and prints the flow:

   ```powershell
   uv run prismabib build baseball-pitch --rebuild
   uv run prismabib flow baseball-pitch
   ```

4. The participant compares the two outputs line by line. Any line that differs is read out to the facilitator.

**Records, for T7 only.** The facilitator writes "match: yes" or "match: no". If no, the facilitator writes the differing
lines only, as printed by each run. Nothing else is copied.

**Stop-first sequence.** The screening view from T4 may still be running. The guide does not tell the participant to
stop it. Whether a running server blocks the delete is settled by the lock test in the pre-session check (gate G10). If
the delete fails while the server runs, the facilitator tells the participant to stop the server with Ctrl+C, then
retries. The guide gap is logged as a docs finding. The screening view opens the store read-only
(`screening/ui.py:223`, closed at `ui.py:251`).

`flow` prints no timestamps (`cli.py`, `_print_flow`), so the output can match line by line.

**Success criteria (observable).**

1. The store file is deleted and the build prints `Rebuilt store at ...`.
2. Every flow line matches the first output.
3. The participant explains why, in their words. The store is derived, and the capture under `raw` and the decisions log
   are the record. The guide's "What you have now" list says this.
4. If any line differs, stop and record both outputs as a blocker. Do not explain the difference in the session.

**Priority.** T7 outranks T6. **Cap.** 7 minutes (target 4, plus 3).

**Level 1 phrase.** "Which folder holds the captured search?"

**Records checklist.**

- The "match: yes" or "match: no" line, and, if no, the differing lines.
- Whether the stop-first sequence was needed, and the delete error if there was one.
- Verbatim: the participant's explanation of the match.

## Staged fallback

If the capture cannot run (an OpenAlex outage, an exhausted daily allowance, or a capture that fails at the checkpoint),
the facilitator sends the folder `baseball-pitch-staged`, built in the pre-session check. The participant places it under
`projects`. From T3 onward, the slug in every command is `baseball-pitch-staged`. Every task done on the staged folder is
marked `assisted-state` in the note sheet. The staged folder's recorded actor is `kit-staged`.

## Wrap-up (not a task)

1. Ask the participant to leave the projects folder as it is. Nothing is deleted at the end.
2. Remind the participant that the key stays on their computer. It is not sent, pasted into a chat, or put in a repository
   file.
3. Say the consent points again: the withdrawal date, and the contact.
4. Move to the questionnaires in [questionnaires](questionnaires.md). They always run.
