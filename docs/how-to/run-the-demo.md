# Run the Demo

A step-by-step walkthrough for presenting prismabib to an audience at a seminar or conference. This guide is written for a researcher who is a competent presenter but not a programmer. It covers a complete end-to-end demo in 10–15 minutes, with a breakdown of what to say at each step and how to recover gracefully if something goes wrong live.

**Before you start:** prismabib requires Scopus entitlement to `view=COMPLETE`. See [Getting Started](../getting-started.md#before-you-invest-any-time-scopus-complete-entitlement) to confirm you have access. If you do not, none of the steps below will work.

---

## Before the day: preparation checklist

This section describes what you must do before you stand up and present. Budget 2–3 hours.

### 1. Choose a topic and build a query (1 hour)

Pick a research topic you understand or a public example. Use [Build a Scopus Query](build-a-query.md) to design a Boolean query. Write it into `project.toml`. Test it by hand at [scopus.com/search](https://scopus.com/search) (if you have access) or by running a small capture with a page limit.

For this walkthrough, we'll use carbon capture and utilization — a domain you can substitute for your own.

### 2. Capture from Scopus (20–30 minutes, but the network does the work)

Run `prismabib search` and let it complete. This spends weekly Scopus quota and is the reason you cannot run it live in front of an audience. Instead, you will show the sealed run as provenance.

```bash
uv run prismabib init demo-talk --title "Carbon capture and utilization: a live demo"
# Edit project.toml and criteria.yaml (see step 3 below)
uv run prismabib search demo-talk
# Wait for it to finish. Interrupt only if network fails; re-run to resume.
```

The run ID printed at the end (e.g., `20260825T090000Z-3f9a2c11`) is what you'll show in the demo. Note it down.

### 3. Screen ~20 records before the demo (20–30 minutes)

Open the screening notebook and manually review at least 20 records. This is load-bearing: a diagram with `included = 0` reads badly to an audience. A diagram where every line has a non-zero count tells the story much better.

```bash
uv run panel serve notebooks/01_screen_title_abstract.ipynb --show
# Or from the notebook: screener(project, stage="title_abstract", reviewer="presenter")
```

Screen records until you have included 5–15 studies. Leave the decision log in place — you'll show it.

### 4. Build and export (5 minutes)

```bash
uv run prismabib build demo-talk --rebuild
uv run prismabib export demo-talk
```

Check that the PRISMA diagram exists and looks reasonable:

```bash
ls -lh projects/demo-talk/exports/figures/
```

### 5. Prepare the manuscript (10 minutes)

**Read this whole step before writing anything.** The obvious short paragraph does not work, and finding that out on stage is the worst possible moment.

`prismabib fill` fails in *both* directions: on a key your manuscript cites that `numbers.json` does not define, **and on a key `numbers.json` defines that your manuscript never cites**. A four-line paragraph cites four keys out of thirty-three, so it exits non-zero with a wall of twenty-nine "unused key" names — before you have had a chance to demonstrate anything.

That is the contract working as designed (a number that stopped being cited may have had its claim edited away with it), but it means **your demo manuscript must cite every key in `numbers.json`.**

Two ways to handle it, and the second is better theatre:

**Either** write a manuscript that cites all of them. Generate the key list from your own export first — the count and the venue names differ per corpus:

```bash
python -c "import json; print('
'.join(sorted(json.load(open('projects/demo-talk/exports/numbers.json')))))"
```

Then save something like this as `paper-draft.md` (this version cites exactly the 33 keys a Stage 10 export produces):

```markdown
## Methods (excerpt)

We identified {{flow.identified}} records from Scopus, of which
{{flow.duplicates_across_searches}} were duplicates across searches and
{{flow.removed_other_reasons}} could not be read from the capture. The automated
filters removed {{flow.excluded_automated}} records, leaving {{flow.after_automated}};
a further {{flow.excluded_language}} were removed by language, leaving
{{flow.after_language}} for title/abstract screening.

At title and abstract we excluded {{flow.excluded_title_abstract}} and left
{{flow.unsure_title_abstract}} unresolved, seeking {{flow.retrieved_fulltext}} reports
for retrieval. At full text {{flow.excluded_fulltext.total}} were excluded and
{{flow.unsure_fulltext}} left unresolved, giving {{flow.included}} included studies.

## Corpus description

The {{corpus.size}} records span {{corpus.year_min}}-{{corpus.year_max}}
({{corpus.year_span}} years) across {{venues.total}} venues, under criteria version
{{criteria.version}}. The most frequent venues were {{venues.top1.name}}
({{venues.top1.count}}), {{venues.top2.name}} ({{venues.top2.count}}),
{{venues.top3.name}} ({{venues.top3.count}}), {{venues.top4.name}}
({{venues.top4.count}}) and {{venues.top5.name}} ({{venues.top5.count}}).

Citation counts were available for {{citations.records_with_a_snapshot}} records,
totalling {{citations.total}} citations, with a median of {{citations.median}} and a
maximum of {{citations.max}}.
```

**Or** make the failure the first beat of the demo deliberately: run `fill` with a short paragraph, let it fail, and say *"it refuses because my paper stopped citing twenty-nine numbers the export still produces — that is the drift it exists to catch"*. Then show the complete version passing. This is more convincing than a clean run, because the audience sees the guard fire rather than being told it exists.

Whichever you choose, **rehearse it**. Confirm the passing version exits zero and the broken version exits non-zero, on the machine you will present from.

### 6. Test everything locally (10 minutes)

Run through every command below, in order, without presenting. Check that:

- `prismabib init` succeeds.
- `prismabib build` finishes without errors.
- `prismabib flow` prints a sensible diagram.
- The screening notebook opens and shows records.
- `prismabib export` writes files.
- `prismabib fill` fills your manuscript correctly.

Note any error messages or unusual output — these are the failure modes you need to handle gracefully live.

### 7. Set up your presentation laptop (5 minutes)

- Clone the repo fresh (not from a working copy you've edited):
  ```bash
  git clone https://github.com/SergioHuesca/PRISMA-Bib.git
  cd PRISMA-Bib
  uv sync
  ```
- Copy your `.env` file with your Scopus key into the repo root (`.env` is gitignored).
- Copy your `projects/demo-talk/` directory into this fresh clone (the `raw/`, `store/`, and `decisions/` directories).
- Verify `uv run prismabib flow demo-talk` prints the diagram correctly.
- Verify `uv run prismabib export demo-talk` exports without errors.
- Open the screening notebook in a browser tab so you can switch to it without delay.

---

## The 10-minute demo

This section is the script you'll read and the commands you'll type in front of the audience.

### Step 0: Open and orient (1 minute)

**What to say:**

> "I'm going to walk through a complete systematic review in about 10 minutes. This is automated from end to end—no numbers are typed by hand. Everything you see comes from a query, some criteria, screening decisions, and a single command that exports everything at once. Let me start with a brand-new project."

**What to do:**

Open a terminal in the fresh clone and check the version:

```bash
uv run prismabib --version
```

You should see something like `prismabib 0.12.0`. Print it so the audience sees it on screen.

---

### Step 1: Create a project (1 minute)

**What to say:**

> "First, we scaffold a project. This creates the directory structure and gives us two files to edit: the query and the eligibility criteria."

**What to do:**

```bash
uv run prismabib init demo-talk --title "Carbon capture and utilization: a live demo"
```

You'll see:

```
Created project 'demo-talk' at ./projects/demo-talk

`prismabib search` cannot run until you edit these two files, in this order:

  1. ./projects/demo-talk/project.toml
     Fill in [query] terms / compound_terms -- the Boolean search itself. They
     are scaffolded empty, and an empty query is refused rather than silently run
     against all of Scopus.
  2. ./projects/demo-talk/criteria.yaml
     Set temporal.year_start / year_end, and whichever of doc_types.include and
     languages your protocol restricts; every list left empty means no restriction on
     that dimension, which is a real eligibility choice rather than a placeholder.
     Replace the starter exclusion reason codes with the ones your review actually
     distinguishes, and bump `version` whenever you change any of it. The file's own
     comments explain each block.

Then, in order:
  prismabib search demo-talk   # spends Scopus quota; resumable
  prismabib build demo-talk
  prismabib flow demo-talk
```

---

### Step 2: Show the configuration files (1 minute)

**What to say:**

> "The tool enforces a split between the query—what Scopus sees—and the criteria—what we apply afterwards. If you want the details on how to design the query, see the Build a Scopus Query guide. For now, here's a small one that will run quickly."

**What to do:**

Open `projects/demo-talk/project.toml` in your editor and show it (or read it from the command line):

```bash
cat projects/demo-talk/project.toml
```

Show the file and explain the structure. Then show `criteria.yaml`:

```bash
cat projects/demo-talk/criteria.yaml
```

Point out the key sections: temporal window, document types, languages, and the exclusion reason codes you'll see during screening. **Do not spend time editing**—the files are already there. Just point and explain.

---

### Step 3: Show the sealed Scopus run (1 minute)

**What to say:**

> "Next, we would run a Scopus search. It spends weekly quota and takes minutes, so I've already captured these results beforehand. Here's what a sealed run looks like—the manifest is what makes it reproducible."

**What to do:**

List the run directory:

```bash
ls -la projects/demo-talk/raw/
```

Show the manifest:

```bash
cat projects/demo-talk/raw/20260825T090000Z-3f9a2c11/manifest.json | head -20
```

Explain:

- **`query`** — the Boolean string Scopus received.
- **`total_results`** — what Scopus said it found (the PRISMA 'identified' count).
- **`payload_sha256`** — the hash of every page, so this run is bit-for-bit reproducible.
- **`client_version`** — prismabib 0.12.0, so the code is named.

---

### Step 4: Build the store (1 minute)

**What to say:**

> "The raw data from Scopus is immutable. Now we build a DuckDB store from it—that's Layer 1. If we change our minds about the criteria later, we rebuild this from the same raw data, so nothing drifts."

**What to do:**

```bash
uv run prismabib build demo-talk --rebuild
```

You'll see output like:

```
Rebuilt store at ./projects/demo-talk/store/corpus.duckdb

  runs loaded                        1
  records                        1,771
  authors                        5,240
  affiliations                   4,891
  venues                           847
  keywords                       8,193
  record-keyword links          15,620
  subject-area links                 0
  citation snapshots                 0

  duplicate DOI groups: 12 (24 records). Reported, never applied -- every one of
  those rows is still in the store; deduplication is a screening decision.

Next: prismabib flow demo-talk
```

Read a few lines aloud so the audience hears the numbers. Note that duplicates are reported but not removed—that's a screening decision.

---

### Step 5: Print the PRISMA counts (1 minute)

**What to say:**

> "Now for the part that makes this a systematic review: the flow diagram. Every number here comes from the store and the screening decisions. Let me print the counts."

**What to do:**

```bash
uv run prismabib flow demo-talk
```

You'll see something like:

```
PRISMA 2020 flow -- project 'demo-talk'

Identification
  records identified (Scopus total_results)         1,771

Removed before screening
  duplicates across searches                            0
  other reasons (unreadable capture entries)            0

Screening -- automated, from criteria.yaml
  excluded by year / subject area / doc type         -412
  remaining                                         1,359
  excluded by language                                -37
  remaining, to title/abstract screening            1,322

Screening -- title/abstract, from logged human decisions
  excluded                                           -150
  unsure or not yet screened                       1,172
      (unsure never resolves to inclusion; it stays in the queue)
  sought for full-text retrieval                       18

Eligibility -- full text, from logged human decisions
  excluded                                              0
      (no full-text exclusion reason codes logged)
  unsure or not yet screened                          18
      (unsure never resolves to inclusion; it stays in the queue)

Included
  studies in the final corpus                         18
```

**What to point out to the audience:**

- The "Identification" line is straight from Scopus's manifest — you cannot fake that number.
- "Excluded by year/doc type" came from the criteria you showed earlier.
- "Excluded by title/abstract" came from screening decisions — each keystroke logged.
- The final count is 18 studies. Every number traces back to decisions or data, never typed by hand.

---

### Step 6: Screen a handful of records live (4 minutes)

**What to say:**

> "This is where the human work actually happens. I'll open the screening interface and show you how it works. Every keystroke is logged to disk immediately—if the power fails, you lose at most the record on screen. Watch the keyboard shortcuts."

**What to do:**

Open the screening notebook. Switch to your browser tab or run:

```bash
uv run panel serve notebooks/01_screen_title_abstract.ipynb --show
```

Wait for it to load. Then open a Python cell and run:

```python
from prismabib.project import Project
from prismabib.screening.ui import screener

project = Project.open("demo-talk")
screener(project, stage="title_abstract", reviewer="presenter")
```

**What to point out while screening:**

The interface shows:

- **Progress:** "n of N decided" and the pace (decisions per minute).
- **The record:** title, abstract, venue, year, doc type, keywords (no author names, no citation count by default—both bias the decision).
- **The status line:** the record ID and its current decision status.
- **The keyboard map:** shown at the bottom, summarized below.

**Keyboard shortcuts** (point at each one as you use it):

- Press **`i`** to include a record.
- Press **`e` then `1`** to exclude under reason code 1 (the first code in your criteria).
- Press **`u`** to mark unsure—the record stays in the queue.
- Press **`n`** or **`p`** to step to the next or previous record without deciding.
- Press **`z`** to undo the last decision.
- Press **`?`** to show/hide the help.

**Live screen 3–4 records.** Include some, exclude some. Show the undo by pressing `z`. After each keystroke, point to the progress line and note that the pace counter updated — the decision was already persisted to disk.

After 3–4 records, close the interface and move on. Do not try to screen many records live—it is slow and loses the audience.

---

### Step 7: Export the citable bundle (1 minute)

**What to say:**

> "Once screening is done—which in a real review takes weeks—we export everything. The diagram, tables, the decision manifest, and numbers.json, which is the file we'll use in the next step to fill in the manuscript."

**What to do:**

```bash
uv run prismabib export demo-talk
```

You'll see:

```
Exported demo-talk to ./projects/demo-talk/exports

  figures                             2
  table renderings                    3
  numbers.json keys                  15
  git commit          3f9a2c11
```

Show the exports directory:

```bash
ls -lh projects/demo-talk/exports/
```

Open the PRISMA diagram in a viewer:

```bash
# On macOS:
open projects/demo-talk/exports/figures/prisma_flow.svg
# On Linux (if you have an image viewer):
display projects/demo-talk/exports/figures/prisma_flow.svg &
```

Or just cat the numbers:

```bash
cat projects/demo-talk/exports/numbers.json | head -10
```

Point out a few keys: `flow.identified`, `flow.included`, etc. These are what the manuscript cites.

---

### Step 8: Fill the manuscript (1 minute)

**What to say:**

> "Finally, we fill in the manuscript. You write your methods with placeholders like `{{flow.included}}`, and we substitute the numbers from numbers.json. The tool fails if the manuscript cites a number that is not there, or if numbers.json has a key nobody cites. Both are drift—the tool catches them."

**What to do:**

Show the paper draft:

```bash
cat paper-draft.md
```

Fill it:

```bash
uv run prismabib fill paper-draft.md projects/demo-talk/exports/numbers.json -o paper-filled.md
```

If it succeeds, you'll see:

```
filled paper-draft.md -> paper-filled.md
```

Show the result:

```bash
cat paper-filled.md
```

You'll see the placeholders replaced:

```markdown
## Results

We identified 1,771 records from the Scopus search.
After removing duplicates, 1,322 records remained for screening.
We excluded 150 at title/abstract,
and included 18 studies in the final corpus.
```

**What to point out:**

- No number was typed. Every count came from the store or the decision log.
- If you change the corpus, re-run `build`, re-run `export`, and the manuscript fills with new numbers automatically.
- If you cite a key that does not exist, `fill` fails. If a number stops being cited, `fill` fails. That is the whole point: it catches the drift that produced the inconsistent manuscript.

---

## If something goes wrong live

### No network / Scopus is down

**Problem:** You try to show the sealed run manifest, but the terminal has no network access.

**Recovery:**

- Have a screenshot or printed copy of the manifest JSON ready.
- Say: "Normally this would come from the Scopus API. Here's what the run looks like" and show the image.
- Skip to step 4 (build) and proceed from there.

### The screening notebook is slow to load

**Problem:** `panel serve` takes 30 seconds or more to start, or the browser is stuck.

**Recovery:**

- Stop the server and restart it while explaining what screening does. Say: "Screening is a notebook interface where every keystroke logs a decision. Here's what a screening decision looks like" and show code from the notebook instead of the live UI.
- Show the decision log file:
  ```bash
  cat projects/demo-talk/decisions/decisions.jsonl | head -5 | python -m json.tool
  ```
- Proceed to step 7 (export).

### `export` prints a "dirty working tree" warning

```
WARNING: exported from a dirty working tree. manifest.json records dirty=true;
the recorded commit does not describe the code that produced these numbers.
```

You have uncommitted changes in your prismabib clone — an edited config, a stray
scratch file. Nothing is broken and the export is complete.

**Do not apologise for it; use it.** It is the provenance guard doing its job:
the manifest records the git commit of prismabib itself, and it is telling you
that commit does not describe the code that just ran. Say so — *"it is refusing
to let me pretend I know which version produced these numbers"* — and move on.

To avoid it entirely, present from a clean clone (`git status` empty), which is
what the preparation checklist asks for. A related warning,
`commit_is_pushed: false`, means the commit exists only on your machine, so a
reader could not fetch the code behind the numbers.

### The diagram shows `included = 0`

**Problem:** Your screening did not include any records, so the diagram looks empty or odd.

**Recovery:**

- Explain: "Here's a corpus where nothing was included—this is the result of screening decisions. If I had included more records beforehand, the diagram would show them. This is what the *process* looks like; the numbers are just small because I didn't screen much for this demo."
- It is honest and teaches the point: the tool reflects *real decisions*, not invented numbers.

### `prismabib flow` prints a warning

**Problem:** The counts do not close (equation 1 fails), and `flow` prints a warning on stderr.

**Recovery:**

- Read the warning aloud. Say: "This happens when the store is incomplete or the capture did not finish. In a real review, this means go back and re-run the search and rebuild. For a demo, it means the data is incomplete, but the process is the same."
- The tool is working as designed: it catches inconsistency rather than hiding it.

### `prismabib fill` fails

**Problem:** The manuscript cites a key that does not exist, or has a key that the manuscript does not cite.

**Recovery:**

- Read the error aloud. Say: "This is the whole point: the tool refuses to let the manuscript and the numbers drift. I would edit the manuscript to match, or re-run the export to match, and fill again."
- It is a feature, not a bug.

---

## Rehearsal checklist

Run through this before the day:

- [ ] Clone the repo fresh and install it.
- [ ] Set up `.env` with your Scopus key.
- [ ] Copy `projects/demo-talk/` into the fresh clone.
- [ ] Run `prismabib flow demo-talk` — it should print the diagram.
- [ ] Run `prismabib export demo-talk` — it should write files.
- [ ] Open the screening notebook — it should load.
- [ ] Try `prismabib fill` with your manuscript — it should succeed.
- [ ] Time yourself going through steps 0–8. Aim for 10–15 minutes.
- [ ] Note any long pauses (network, I/O, kernel startup).
- [ ] Prepare a backup explanation or screenshot for each slow step.

---

## Timing estimate for a 10–15 minute slot

| Step | Time | Notes |
|---|---|---|
| 0. Open and orient | 1 min | Version check, setting expectations |
| 1. Create project | 1 min | `prismabib init` is very fast |
| 2. Show config | 1 min | Just cat the files; do not edit |
| 3. Show sealed run | 1 min | Point out manifest keys |
| 4. Build store | 1 min | `prismabib build` reads Layer 0 and writes DuckDB |
| 5. Print PRISMA counts | 1 min | Read key lines aloud |
| 6. Screen live | 4 min | The slowest part; 3–4 records only |
| 7. Export | 1 min | List the export directory |
| 8. Fill manuscript | 1 min | Show input, show filled output |
| **Total** | **~13 min** | Buffer 2 min for questions or lag |

---

## What this demo does not cover (be honest)

Point out at the end:

- **Full-text retrieval:** prismabib does not download or extract full text yet. [See Limitations](../methodology/limitations.md).
- **Taxonomy coding and dashboard:** Stages 8–10 are not implemented. The tool covers PRISMA Phases 1–2 (identification and screening).
- **Bibliometrics at scale:** Citation counts and geography are loaded into Layer 1, but the analytics are not wired into the export yet.
- **Multi-reviewer agreement:** The decision log tracks reviewer, so inter-rater reliability statistics are *possible* to compute, but nothing computes them automatically yet.

Say: "The scope is deliberate: the tool is honest about what it does today, not what it plausibly does. The four-layer architecture means you can add full-text and taxonomy layers later without rewriting the screening part."

---

## Advanced: scripted screening with no notebook

If the notebook is too slow or the browser is problematic, you can show screening via Python code instead:

```python
from prismabib.project import Project
from prismabib.store.load import Corpus
from prismabib.stage import PrismaStage
from prismabib.prisma.log import DecisionLog

project = Project.open("demo-talk")
corpus = Corpus.open(project)
log = DecisionLog(project)

# Get the queue
queue = corpus.records(PrismaStage.LANGUAGE).select("record_id", "title")
print(queue.head(3))  # Show 3 records

# Append a decision
log.append(
    stage=PrismaStage.TITLE_ABSTRACT,
    record_id="scopus:2-s2.0-85101234567",
    reviewer="presenter",
    decision="include",
    note="carbon capture is central to the method",
)

print("Decision logged.")
```

This is slower than the UI but avoids Panel entirely. Use this as a fallback.

---

## After the demo: what to send

If you are presenting at a conference, share this afterward:

1. **Link to the repository:** https://github.com/SergioHuesca/PRISMA-Bib
2. **Link to the getting-started guide:** https://SergioHuesca.github.io/PRISMA-Bib/getting-started/
3. **Link to this demo guide:** https://SergioHuesca.github.io/PRISMA-Bib/how-to/run-the-demo/
4. **Your project repository** (if you want to share it as an example): the `projects/demo-talk/` directory, zipped.

---

## See also

- [Getting Started](../getting-started.md) — the full end-to-end path with all the details.
- [Build a Scopus Query](build-a-query.md) — how to design the Boolean query properly.
- [Run a New Review](run-a-new-review.md) — the same steps with deeper dives.
- [Architecture Overview](../architecture/overview.md) — how the four layers fit together.
