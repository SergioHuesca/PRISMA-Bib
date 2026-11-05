# Tasks

This page holds the full task definitions. The [run card](run-card.md) holds the same scenario
wording, verbatim, for use during a session. If the two ever differ, the run card's wording is the
one read to the participant, and this page is corrected to match.

Each task has the same parts:

- **Scenario.** Read to the participant, in these words.
- **Starting state.** What must be true before the task starts.
- **Procedure.** The steps. Each step is either a guide step or a facilitator-directed addition,
  as listed in [index](index.md).
- **Success criteria.** What the facilitator observes. All must hold.
- **Cap.** Target plus 3 minutes. Stop rules are in [protocol](protocol.md).
- **Records.** A checklist of 3 to 5 items for the note sheet.
- **Hints.** Time-based thresholds, as in [protocol](protocol.md). The ladder is the same everywhere.

Commands are for PowerShell in Windows Terminal. Run every command from the root of the clone, with
`uv run` in front, as the guide says.

Every session uses the same fixed query: `"baseball" AND "pose estimation" AND "pitcher"`. Participants
do not choose a topic. This keeps the sessions comparable.

## Participant self-check (sent three to five days ahead)

The participant does this alone, on their own computer, and reports the results to the facilitator
by email. The email carries no key and no file.

1. Open PowerShell. Run `git --version`. It prints a version.
2. Run `uv --version`. It prints a version. If the command is not found, tell the facilitator.
3. Open `https://api.openalex.org` in a browser. A page of text appears.
4. Confirm you have a free OpenAlex key from your openalex.org account. Create the key now, before
   the session, if you do not have one. Keep the key in your account. Do not send it.
5. Choose the working folder. It must be a plain local path, such as `C:\Users\<you>\work`. Do not
   use OneDrive, Desktop, or a folder that a sync client watches. Create the folder now.
6. Confirm you can share a window and a sound, on the video call. Confirm you have either a second
   screen or Windows virtual desktops. Press Win+Ctrl+D and check that a new desktop appears, then
   press Win+Ctrl+D again or Win+Ctrl+Left to go back.
7. Check that your laptop is not blocked by an IT policy from installing software in your user
   profile. If it is, tell the facilitator now.
8. Check clipboard history. Press Win+V. If it lists anything, clear it, or turn clipboard history off
   in Windows settings. Copied keys must not stay in it during the session.
9. Check that you can paste into Windows Terminal, with Ctrl+V or with a right-click, as your terminal
   is set up. Paste something harmless into the terminal, then clear the line.
10. In Windows Terminal settings, set the font size to 16 or larger, so the facilitator can read it.

**While you wait.** The install in T0 may run for several minutes with almost no output. That silence is
normal. Do not cancel it. Ask the facilitator's background questions, or read the guide, while it runs.

## Facilitator pre-session check

The facilitator runs this on their own computer, a few days before the session, and again on the
day if the tag under test changes. It takes about 30 minutes, most of it the install.

1. Clone the repository into a new folder. Check out the tag under test.

   ```powershell
   git clone https://github.com/SergioHuesca/PRISMA-Bib.git
   cd PRISMA-Bib
   git checkout <tag or commit under test>
   ```

2. Install, then record the version and run `doctor`. Record the exit code.

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

5. Run the OpenAlex path in a throwaway project, `kit-check`, with the fixed query in
   `[openalex_query]`. Then run the capture steps below.

   ```powershell
   uv run prismabib init kit-check --sources openalex --title "Kit check" --actor "<your name>"
   uv run prismabib openalex-search kit-check --budget 1
   uv run prismabib openalex-search kit-check --budget 5 --dry-run
   uv run prismabib openalex-search kit-check --budget 5
   uv run prismabib build kit-check --rebuild
   uv run prismabib flow kit-check
   uv run prismabib export kit-check
   ```

6. Start the screening view with the slug set, and confirm one record shows. Then stop the server
   with Ctrl+C. Start it once more with the slug unset, and confirm the red **Screening cannot start**
   message appears. Stop it again.

   ```powershell
   $env:PRISMABIB_NOTEBOOK_SLUG = "kit-check"
   $env:PRISMABIB_NOTEBOOK_REVIEWER = "<your name>"
   uv run panel serve notebooks/01_screen_title_abstract.ipynb --show
   ```

7. Build the staged fallback project from step 5. Keep it as `baseball-pitch`, with no key in it.
   Test that the facilitator can send it and that a participant can place it under `projects`.
8. Prepare the note sheet copy, the run card, and the session email with the consent text.
9. Check the consent placeholders and the Session 1 gate in [protocol](protocol.md).

## Path choice

The path is set from the Scopus answer recorded at recruitment. The participant is not asked again in
the session.

| Recruitment answer | Path | Mode |
| --- | --- | --- |
| No, or not sure | OpenAlex-only | `openalex` |
| Yes | OpenAlex-only, unless the owner has approved the Scopus variant for this participant | `openalex` |
| Yes, with the Scopus variant approved | Combined | `scopus+openalex` |

A participant with a Scopus key stores that key in T1, with their own key, whichever path they take.
The Scopus variant spends the participant's Scopus quota, and `search` has no size check before it
runs. The owner approves it per participant, with a narrow search and a time cap agreed in advance.
The default is the OpenAlex-only path, so that all participants do the same tasks. The variant is
described in T2 and T3 below.

## T0. Install, doctor, and the access question

**Scenario (read to the participant):**

> Install the tool by following the guide, from the start of the Install section through the doctor
> step. After doctor, run this command. Leave out any `--sources` flag. The tool will ask you a
> question. Answer it.
>
> `uv run prismabib init access-check --title "Access check" --actor "<your name>"`
>
> Then run this command.
>
> `uv run prismabib sources access-check`
>
> Think aloud as you go.

This throwaway step is a facilitator-directed addition. The guide's own commands always pass
`--sources`, so the question is otherwise never shown. It is written into the scenario above so the
participant reads it as an instruction.

**Starting state.** A Windows 11 user account, with git, uv, and Python 3.11 or newer installed. No
clone, no `.env`. The participant's working folder is the one set in the self-check.

**Procedure.**

1. Read the background answers recorded in block 1 back to the participant, and do not ask about
   Scopus access again.
2. Run `git clone` for the tag under test, then `uv sync --extra viz --extra notebook`. Keep the
   silence. Ask the facilitator's questions while it runs. The install hard cap is 15 minutes from the
   start of `git clone`.
3. Run `uv run prismabib --version`, then `uv run prismabib doctor`.
4. Run the throwaway `init` above with no `--sources` flag. Answer the question the tool asks. If you
   answer y, it asks a second question. Answer it.
5. Run the `sources` command above.
6. After the command, the facilitator asks, once: "What did you expect that question to mean?" Write
   the answer verbatim. Do not explain the question.

The `access-check` folder is not used again. The participant may delete `projects\access-check`.
Nothing else is deleted.

**Success criteria (observable).**

1. `git clone` and `uv sync` finish with no error, within the 15-minute install cap.
2. `--version` prints a line that starts with `prismabib`.
3. `doctor` exits with code 0. In PowerShell, `$LASTEXITCODE` is 0 right after the command. No line
   starts with `FAIL`. A `warn` line is recorded, not counted as a failure.
4. The tool asks "Do you have access to Scopus?" when `init` runs with no `--sources` flag.
5. `sources access-check` prints a mode that matches the answer the participant gave in the tool.

**Cap.** The install hard cap of 15 minutes from the start of `git clone`. The task itself is capped
at its block budget of 15 minutes. If the install passes the cap, stop T0, and record the failure as a
blocker. Go to T1 only if the install succeeded.

**Records checklist.**

- The clone and `uv sync` durations, and any error text verbatim.
- Every `doctor` line that is not `ok`, verbatim, and the exit code.
- The answers the participant gave to the tool's question, and the number of times it was asked.
- The mode `sources` printed, and the participant's answer to the expectations question, verbatim.
- Any Windows prompt, such as Smart App Control, Application Control, or a firewall prompt, with its text.

**Facilitator note.** The Scopus question is not asked by the facilitator in T0. The tool's prompt is
the first encounter. The recruitment answer is only used to assign the path.

## T1. Store the OpenAlex key

**Scenario (read to the participant):**

> Store your OpenAlex key with the guide's credentials step. Copy the key from your openalex.org
> account page on the other desktop. Paste it at the prompt and press Enter. Nothing appears while you
> paste, and that is expected. Then check the stored status.

**Starting state.** T0 is done and doctor passed. The OpenAlex key was created before the session.

**Screen rule.** The participant keeps the share on the working desktop. The openalex.org account page
is on the other virtual desktop, which is not shared. The participant copies the key there and switches
back to paste it. There is no pause in the share and the facilitator does not look away. The key is
pasted into a hidden prompt, so nothing is shown. If a key does appear anywhere on the shared
desktop, the incident rule applies. See [protocol](protocol.md).

**Procedure.**

```powershell
uv run prismabib credentials set openalex
uv run prismabib credentials status
```

Scopus holders store their Scopus key with the same approach:

```powershell
uv run prismabib credentials set scopus
uv run prismabib credentials test scopus
```

The Scopus prompt has two lines. The second, `SCOPUS_INSTTOKEN`, is optional. The participant presses
Enter to skip it if the library issued no token. The test makes one request and prints one result line.

The Connect Scopus panel is a Python class. No notebook in the repository shows it. It is not used
in this round.

**Success criteria (observable).**

1. `credentials set openalex` prints `stored OPENALEX_API_KEY in <path>\.env`.
2. `credentials status` prints `OPENALEX_API_KEY: set (.env)` and `git-ignored by a tracked rule: yes`.
3. The key does not appear in any output the facilitator sees.
4. For a Scopus holder: the set command shows two prompts, the second marked optional. `credentials
   status` shows `SCOPUS_API_KEY: set (.env)`. The test prints one result line, which is recorded
   verbatim. A result other than `scopus: ok` is not the participant's failure.

**Cap.** 7 minutes (target 4, plus 3).

**Records checklist.**

- The two status lines, as printed, with the value part shown only as "set" or "not set".
- Whether the prompt was retyped, refused, or confusing, and the words used.
- The Scopus test line, verbatim, for Scopus holders.
- Whether any key-like text appeared on the shared desktop. Yes or no only. Do not write what it was.

**Hints.** Thresholds as in [protocol](protocol.md). Level 1: "What does the guide say to type?" Level
2: point to the credentials step in the guide. Level 3: say the exact command. The facilitator never
types or reads a key.

## T2. Create the project and write the search

**Scenario (read to the participant):**

> Create a project for this search: "baseball" AND "pose estimation" AND "pitcher". Put the search in
> the project file. Set the year window in criteria.yaml as the guide says. Then check how many works
> the search finds before you capture anything.

**Starting state.** T1 is done. The mode is set by the path.

**Procedure (guide walkthrough, steps 1 to 4).**

1. Create the project. The mode flag matches the path:

   ```powershell
   uv run prismabib init baseball-pitch --sources openalex --title "Baseball pitching and pose estimation" --actor "<your name>"
   ```

2. Open `projects\baseball-pitch\project.toml`. In the `[openalex_query]` table, set `search` to
   `"baseball" AND "pose estimation" AND "pitcher"`. Do not add a second `[openalex_query]` table.
3. Open `projects\baseball-pitch\criteria.yaml`. Set `year_start` and `year_end` under `temporal`, as
   the guide's step 3 says. The template starts at 1900 and the current year. Record the values.
4. Check the size. This call requests one page.

   ```powershell
   uv run prismabib openalex-search baseball-pitch --budget 1
   ```

5. Read the count. Use whichever source shows it:
   - If the stop line prints a count, read it there.
   - Otherwise, while the run is unsealed, read `"total_count": <number>,` from
     `projects\baseball-pitch\raw\openalex-search\<run id>\progress.json`. The file is removed when
     the run seals.
   - If the run sealed on this call, the count is on the sealed line, as "N works captured of M
     reported by OpenAlex". M is the count.
6. Say what the participant will do with the count, and why. Both answers are successes.

**Facilitator notes.**

- For the fixed query, OpenAlex reported 108 works on 2026-11-05. That is two pages at 100 works a page.
  With `--budget 1` the stop line says the run is not sealed. The project owner reports this as
  verified live. This kit did not re-run it.
- The count can drift. A different number is not an error. The participant reports the number they see.
- If the size check seals the run, T3 step 2 prints `already sealed`. Log that as a finding about the
  flow between the check and the capture. Do not explain it to the participant.
- The first-run polish change, PR #278, is expected to print the count on the stop line. It is not on
  main yet. The task works with or without it.

**Scopus variant (only if approved).** The project is created with `--sources scopus+openalex`. The
search goes in the `[query]` table, as the guide's Scopus section describes. There is no size check
for `search`, which spends quota before it reports `total_results`. The owner agrees a narrow search and
a time cap in advance. If the capture is not sealed by the cap, stop, log it, and leave the run on disk.

**Success criteria (observable).**

1. `init` prints `Created project 'baseball-pitch' at ...` and `Sources mode: openalex`.
2. `project.toml` has one `[openalex_query]` table with `search` set to the query. A TOML error is
   recorded verbatim.
3. The size check runs and prints a stop line or a sealed line.
4. The participant reads a count, and states what they will do with it, with a reason.

**Cap.** 9 minutes (target 6, plus 3). **Checkpoint.** T2 must start by minute 25.

**Records checklist.**

- The `init` output lines, and any TOML or YAML error, verbatim.
- The count the participant read, and the source they read it from.
- The decision, and the reason, in the participant's words.
- Whether the size check sealed the run.

**Hints.** Level 1: "Where does the guide say the count is?" Level 2: point to the size-check step.
Level 3: point to the exact line or file. Never say whether a count is too big.

## T3. Capture, build, and read the flow

**Scenario (read to the participant):**

> Capture the search, build the store, and print the flow counts. Then tell me what the number next to
> "records identified" means, and where the number came from.

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
2. The real run prints `run <id> sealed: N works captured of M reported by OpenAlex`. If T2 sealed the
   run, it prints `a sealed run of this query already exists`. That second line is a finding to log, not
   a participant failure.
3. `build --rebuild` prints `Rebuilt store at ...` and a `search runs loaded` line with 1.
4. `flow` prints `records identified (OpenAlex meta.count)` with M.
5. The participant says the number is the count OpenAlex reported for the search, at capture time. It is
   not the number they screened, and they did not type it. They name where they found it.
6. If `flow` prints a warning on the error stream that the counts do not close, record it verbatim. Ask
   what the participant would do before publishing. Do not explain it.

**Cap.** 8 minutes (target 5, plus 3).

**Records checklist.**

- The last output line of each command, verbatim.
- Whether the run sealed on this call, or was already sealed.
- The number on the `records identified` line, and M.
- The participant's answer about the number, in their words.

**Hints.** Level 1: "Read the words on the line you are looking at." Level 2: point to the sealed line and
the `records identified` line, side by side. Level 3: name the command.

## T4. Screen ten records

**Scenario (read to the participant):**

> Open the screening view for this project and screen about ten records. Press ? to see the keys.
> Include some records, exclude some with a reason, mark one unsure, move back and forward, and undo
> one decision. Then print the flow counts again. Tell me what unsure or not yet screened means to you.

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

1. The view opens in the browser and shows one record with a title and a year. Author names and citation
   counts are not shown. That is the default.
2. The participant makes about ten decisions, using include, exclude with a reason, unsure, the move keys,
   and undo. The facilitator notes which keys were used. The decisions are not judged.
3. After the decisions, the lines under `Screening -- title/abstract, from logged human decisions` move.
   `excluded` rises by one for each exclusion. `sought for full-text retrieval` counts the records advanced
   to full text, which the guide describes as the records included. The participant says which lines
   moved.
4. The participant answers the question about "unsure or not yet screened", in their words.
5. If the red **Screening cannot start** message appears, the participant reads the reason and fixes it.
   That is recorded as a stumble, whether or not the fix works the first time.

This task measures execution. It does not test whether the participant discovers the keys unaided. See
[index](index.md).

**Cap.** 11 minutes (target 8, plus 3). Under the minute 25 checkpoint rule, T4 is shortened to five
records. See [index](index.md).

**Records checklist.**

- Decisions by type: include, exclude, unsure, undo. Record counts only, no titles.
- Keys used, by name only, and where the participant hesitated, with the words used.
- The flow lines that moved, and whether the participant explained them.
- The participant's answer about "unsure or not yet screened", verbatim.

**Hints.** Level 1: "Try the help key." Level 2: point to the key table in the guide's screening step.
Level 3: name the key. Never say whether a decision or a reason code is right.

## T5. Export and provenance

**Scenario (read to the participant):**

> Export the review bundle. Open records.csv in the exports folder, and pick one record that has an
> abstract. Is there anything you would want to know about that text before using it in a review?

**Starting state.** T4 is done, or was stopped under its cap.

**Procedure (facilitator-directed addition to the guide, from its step 11).**

```powershell
uv run prismabib export baseball-pitch
```

Then open `projects\baseball-pitch\exports\records.csv`. The export prints `Exported baseball-pitch to ...`,
then count lines and a `git commit` line. It can also print a warning about the prismabib commit, for
example that the tree is dirty or the commit is on no remote branch. Record any warning verbatim, and do
not fix it in the session.

If the participant does not mention where the abstract came from, the facilitator asks once: "Where did
it come from?" Nothing more is asked about the source.

Do not push anything. If the guide asks the participant to commit a file in their own clone, they may
commit it locally. Nothing is pushed.

**Success criteria (observable).**

1. The export line is printed. `exports` holds `records.csv`, `provenance_conflicts.csv`, `numbers.json`,
   and `manifest.json`.
2. The participant says what they would want to know about the abstract before using it. This is recorded
   verbatim, whatever it is.
3. The participant names where the abstract came from, from `records.csv` alone. The header holds
   `abstract_source`, `abstract_level`, `abstract_outcome`, and `abstract_payload_ref`. This criterion does
   not depend on T6.
4. The participant names one way they could check the text. Any check they can carry out counts. For
   example, the record id has the form `openalex:W...`, and they could look it up on openalex.org.

**Cap.** 8 minutes (target 5, plus 3). **Checkpoint.** T5 must start by minute 50. If not, skip T6.

**Records checklist.**

- The export lines, verbatim, including any warning.
- The record id only, not the title.
- The participant's answer to the "want to know" question, verbatim.
- The source the participant named, and whether it came from the file or from a follow-up question.
- The check the participant named, if any.

**Hints.** Level 1: "Look at the header row of records.csv." Level 2: point to the four abstract columns.
Level 3: name the file and the column. Never say which record to choose.

## T6. Methods sentence and the Mode openalex paragraph

**Scenario (read to the participant):**

> Read the methods sentence about the source in numbers.json, and the paragraph the facilitator gives you
> from the limitations page. Would you cite this sentence in your methods section? Why or why not?

**Starting state.** T5 is done.

**Procedure.** The facilitator gives the participant only two items. Nothing else from the limitations
page is shown.

1. The key `methods.source_sentence` in `projects\baseball-pitch\exports\numbers.json`.
2. The paragraph of `docs\methodology\limitations.md` that starts with "Mode `openalex`", in the section
   "Coverage: one source per project, chosen by its source mode". The facilitator's reference is
   [Limitations](../methodology/limitations.md).

The `fill` step of the guide is not part of this round.

**Success criteria (observable).**

1. The participant finds the sentence and reads the query and date it contains.
2. The participant answers the question with a reason. The facilitator does not name a missing item.

**Cap.** 6 minutes (target 3, plus 3). **Priority.** T7 outranks T6. If only one of them can run, run T7.

**Records checklist.**

- The answer, verbatim, and the reason.
- Any item the participant named as missing, verbatim.
- Whether the participant read the sentence aloud.

**Hints.** Level 1: "Search the file for the key." Level 2: point to the section heading. Level 3: point to
the paragraph. Never name a missing item, or say whether a citation is acceptable.

## T7. Delete the store and rebuild

**Scenario (read to the participant):**

> Copy the flow counts you have now. Delete only the file store\corpus.duckdb in your project folder.
> Rebuild the store, and print the flow counts again. Tell me whether the numbers match, and why you
> think they do or do not.

**Starting state.** T4 is done. The flow counts include the screening decisions.

**Procedure.**

1. Copy the flow output to the note sheet, line by line.
2. Delete `projects\baseball-pitch\store\corpus.duckdb`. Delete nothing else. The folder `raw` holds the
   capture.
3. Rebuild, then print the flow:

   ```powershell
   uv run prismabib build baseball-pitch --rebuild
   uv run prismabib flow baseball-pitch
   ```

4. Compare the two outputs line by line.

**Stop the screening server first.** The screening view is still running from T4 unless it was stopped.
The guide does not tell the participant to stop it. Note this as a docs gap if the participant did not
stop it on their own. The screening view opens the store read-only (`screening/ui.py:223`, closed at
`ui.py:251`). Whether a running server keeps a file lock on Windows that blocks the delete is unverified.
If the delete fails, record the exact error verbatim, and stop with Ctrl+C. Do not guess the cause.

`flow` prints no timestamps (`cli.py`, `_print_flow`), so the output can match line by line from one run
to the next.

**Success criteria (observable).**

1. The store file is deleted and the build prints `Rebuilt store at ...`.
2. Every flow line matches the first output.
3. The participant explains why, in their words. The store is derived. The capture under `raw` and the
   decisions log are the record. The guide's "What you have now" list says this.
4. If any line differs, stop and record both outputs verbatim, as a blocker.

**Priority.** T7 outranks T6.

**Cap.** 7 minutes (target 4, plus 3).

**Records checklist.**

- Both outputs, line by line, or a table of the numbers.
- Whether they match.
- The participant's explanation, verbatim.
- Whether the screening server was stopped before the delete, and the delete error if there was one.

**Hints.** Level 1: "Which folder holds the captured search?" Level 2: point to the "What you have now" list.
Level 3: name the file. Never name the cause of a mismatch.

## Wrap-up (not a task)

1. Ask the participant to leave the projects folder as it is. Nothing is deleted at the end.
2. Remind the participant that the key stays on their computer. It is not sent, pasted into a chat, or
   put in a repository file.
3. Say the consent points again: the withdrawal date, and the contact.
4. Move to the questionnaires in [questionnaires](questionnaires.md). They always run.
