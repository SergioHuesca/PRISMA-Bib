# Getting Started

This page takes you from a clone of the repository to a PRISMA 2020 flow count. Which steps
apply depends on one question: do you have Scopus access?

At the end you will have:

- a sealed, immutable Layer 0 archive of what your search returned,
- a Layer 1 DuckDB store rebuilt from it by one command,
- screening decisions recorded as append-only, checksum-guarded events, and
- the PRISMA flow counts derived from all of them, with nothing typed by hand.

## Do you have Scopus access?

Do you have Scopus access (an API key with the COMPLETE view, usually through an institution)?

| Your answer | Source mode | What you need | Where it is covered |
| --- | --- | --- | --- |
| No | `openalex` | A free OpenAlex key. No Scopus key is read. | [Install](#install) and [The OpenAlex-only walkthrough](#the-openalex-only-walkthrough), in full |
| Yes | `scopus` | A Scopus key entitled to the COMPLETE view, and an institutional token if you are off campus. | [The Scopus path](#the-scopus-path) |
| Yes, and you want OpenAlex to fill gaps | `scopus+openalex` | The Scopus key, and an OpenAlex key (the OpenAlex steps use it when it is set). | [The combined path](#the-combined-path) |

Your answer is recorded per project when you run `init`, with `--sources openalex`,
`--sources scopus` or `--sources scopus+openalex`. In a terminal, `init` asks the question if
you leave the flag out. When there is no terminal to ask on (stdin is not a terminal), it refuses
without the flag and writes nothing.

If you answer no, the project is in mode `openalex`, and the Scopus commands refuse to run.
For example, `search` prints this and exits:

```text
`search` is not available in sources mode 'openalex' (<path to project.toml>, [sources] mode). It has nothing to act on in this mode. To use it, create a new project with `prismabib init --sources scopus` (allowed modes: scopus, scopus+openalex).
```

You can change the mode later with `prismabib sources <slug> --set <mode> --actor "<your name>"`.
The change is written as an event in `decisions/source_modes.jsonl`. It is refused in these
cases, and the message says to create a new project instead:

- Once any screening decision exists in the project, no change is allowed.
- Switching to `openalex` is refused if the project has a Scopus search run, in any state.
- Switching away from `openalex` is refused if the project has an OpenAlex search run, in any state.
- Switching from `scopus+openalex` to `scopus` is refused if the project has OpenAlex enrichment
  runs or an active registration.

Whether a key may use the COMPLETE view is decided by Elsevier and by your institution.
prismabib cannot change that. The search is refused rather than run on a lower view. A
researcher without a subscription therefore cannot run a Scopus search. Mode `openalex`
exists for that case: it searches OpenAlex with a free key. It is a different corpus from a
Scopus corpus, and the methods text says which source you used. Read
[Limitations](methodology/limitations.md) before you cite its numbers.

## Install

These steps are the same for every mode.

### Clone and check the version

```bash
git clone https://github.com/SergioHuesca/PRISMA-Bib.git
cd PRISMA-Bib
uv sync --extra viz --extra notebook
uv run prismabib --version
```

`uv sync` alone installs the core package and the CLI, but not the screening view or the
figures. Those need the `viz` extra (Panel and param). Opening the screening notebook needs the
`notebook` extra. The two `--extra` flags above install both. CI runs `uv sync --all-extras`,
which also installs the test and docs tools; you do not need them to run a review.

`uv sync` installs the package and its console script. The version is derived from the git
tag, never typed into `pyproject.toml`. A clone at `v0.5.0` prints `prismabib 0.5.0`. A clone
a few commits past it prints something longer. A tarball with no `.git` prints
`prismabib 0+unknown`. That string is stamped into every capture manifest as `client_version`.

Run every command on this page from the repository root, with `uv run` in front of it.

### Check the environment

```bash
uv run prismabib doctor
```

`doctor` works without a project. It checks the environment and prints one line per check.
On a machine where everything is in place, the output looks like this:

```text
environment:
ok   python 3.11.x (<path to python>)
ok   prismabib <version>
ok   platform: <platform string>
  hint: if Windows (Smart App Control / Application Control) blocks a program, use a Python installed from python.org (signed), not a store or portable build.
ok   git: <path to git>
ok   projects root: <path>, writable
ok   .env: not present (created by `prismabib credentials set`)
```

The hint line appears on Windows only. The projects root line says "does not exist yet" when
the folder has not been created.

Each line starts with a level:

- `ok` passes.
- `warn` does not block anything, but you should look at it. For example, `.env` is present
  and no tracked rule ignores it.
- `FAIL` blocks the run. Python older than 3.11, `git` not on the PATH, and a projects root
  that cannot be written all fail.

`doctor` exits with status 1 only when a check reports `FAIL`. With a project slug it also
prints the project's mode and which keys are set. With `--online` it makes one free request to
`api.openalex.org`. Without `--online` it makes no network request.

### Store your OpenAlex key

1. Create a free account at [openalex.org](https://openalex.org) and copy the API key.
2. Run this from the repository root:

   ```bash
   uv run prismabib credentials set openalex
   ```

   The prompt reads `OPENALEX_API_KEY:`, and what you type is hidden. On success the command
   prints `stored OPENALEX_API_KEY in <path>\.env`.

The key goes into `.env`, in the folder you run the command from. The repository's
`.gitignore` ignores `.env` (its rules are `.env` and `*.env`). The command writes only when
git confirms that a tracked rule ignores the file. It never prints the value.
`uv run prismabib credentials status` shows `OPENALEX_API_KEY: set (.env)` and no value.

The folder must be a git clone. `credentials set` refuses outside a git repository, because
the check that keeps `.env` out of commits runs `git`. Clone with git. Do not download a zip.
A zip or tarball has no `.git`. If you must use an archive, run `git init` in the folder and
then `git add .gitignore`. Staging is enough; a commit is not needed. The check reads the
tracked `.gitignore` rule, so the rule file must be staged or committed. The check also refuses
a `.env` that is already tracked.

You need a key for a real search. On 2026-11-03, OpenAlex refused an anonymous search request as
rate-limited (HTTP 429, "Anonymous search is temporarily rate-limited ... use a free API key"). So a
keyless search cannot capture a review, and prismabib refuses to start one without
`OPENALEX_API_KEY`. A real run stops before its first request if the key is missing, with this
message:

```text
OPENALEX_API_KEY is not set; run `prismabib credentials set openalex`.
```

A `--dry-run` works without a key. It prints `OpenAlex key: NOT SET (a real run is refused)`.

A free key allows about 1,000 search pages a day, at 100 works a page. That figure is OpenAlex's,
as stated on 2026-11-05. OpenAlex can change it. A search of 108 works costs 2 pages, which is
$0.002 at list price. The allowance resets at 00:00 UTC.

## The OpenAlex-only walkthrough

This walkthrough takes about 25 minutes, most of it screening. Everyone runs the same query, so
everyone gets the same corpus:

`"baseball" AND "pose estimation" AND "pitcher"`

On 2026-11-05 this query returned 108 works. OpenAlex's counts change over time, so yours may
differ. If it does, the flow output and the methods sentence show the number you got. The steps
stay the same. This page uses the project slug `baseball-pitch`.

### Step 1. Create the project

```bash
uv run prismabib init baseball-pitch --sources openalex --title "Baseball pitching and pose estimation" --actor "<your name>"
```

`--actor` records who chose the mode. If you leave it out, prismabib uses `git config user.name`.
If that is not set either, it asks for a name in a terminal, and refuses in a script.

The output has this shape. `<projects root>` is your projects folder, by default `projects`:

```text
Created project 'baseball-pitch' at <projects root>/baseball-pitch
Sources mode: openalex (recorded by <your name>; change it with `prismabib sources`)

`prismabib openalex-search` cannot run until you edit these two files, in this order:

  1. <projects root>/baseball-pitch/project.toml
     Fill in `search` under [openalex_query] -- the OpenAlex search itself (words, "quoted phrases", uppercase AND / OR / NOT). It is scaffolded empty, and an empty query is refused rather than sent.
  2. <projects root>/baseball-pitch/criteria.yaml
     Set temporal.year_start / year_end, and whichever of doc_types.include your protocol restricts; every list left empty means no restriction on that dimension, which is a real eligibility choice rather than a placeholder. Replace the starter exclusion reason codes with the ones your review actually distinguishes, and bump `version` whenever you change any of it. The file's own comments explain each block.

Then, in order:
  prismabib openalex-search baseball-pitch --budget 30 --dry-run   # cost; no request
  prismabib openalex-search baseball-pitch --budget 30   # resumable
  prismabib build baseball-pitch --rebuild
  prismabib flow baseball-pitch
```

The list of next commands leaves out `prismabib credentials set openalex` because you stored
the key in the install step. Add `uv run` in front of each command. The `--budget 30` values
in the list are upper limits. This walkthrough uses smaller ones.

Check the mode at any time:

```bash
uv run prismabib sources baseball-pitch
```

```text
mode: openalex
last change: <timestamp> by <your name> (None -> openalex)
```

`init` never overwrites `project.toml` or `criteria.yaml`. Running it again prints `Reused`
and leaves the two files as they are.

### Step 2. Write the query in project.toml

Open `<projects root>/baseball-pitch/project.toml`. It ends with an empty `[openalex_query]`
table. Replace the empty `search = ""` line:

```toml
[openalex_query]
search = '"baseball" AND "pose estimation" AND "pitcher"'
```

Do not add a second `[openalex_query]` table. TOML refuses duplicate tables.

The `[query]` table above it is used only by the Scopus `search` command. This mode does not
use it. Leave it as it is. The query is sent to OpenAlex exactly as you wrote it. Words next to
each other mean AND. Uppercase `AND`, `OR` and `NOT` are operators. Parentheses, wildcards and
Scopus field codes are refused. The full rules are in
[Build an OpenAlex Query](how-to/build-an-openalex-query.md).

The query has no year or document-type filter. Those go in `criteria.yaml`.

### Step 3. Set the eligibility rules in criteria.yaml

Open `<projects root>/baseball-pitch/criteria.yaml`. For this walkthrough, change the year
window. Leave the other lists empty. An example:

```yaml
temporal:
  year_start: 2015
  year_end: 2026
```

- The window is inclusive at both ends. It uses the publication year.
- **An empty list means no restriction on that dimension.** It does not mean "match nothing".
  `subject_areas: []`, `doc_types.include: []` and `languages: []` each accept every work in
  this walkthrough.
- `doc_types.include` takes the codes that the template comments describe: "Scopus-style subtype
  codes; OpenAlex work types are mapped to them: ar (article), re (review), ed (editorial), le
  (letter), er (erratum), bk (book), ch (book chapter), cp (conference paper). Any other OpenAlex
  type is kept as OpenAlex's own word (preprint, dataset, report, ...): list that word to include
  it. A work with no type fails a non-empty list. Empty = accept every type."
- `languages` excludes nothing in this mode. The template says: "OpenAlex records never carry a
  language (it is not derived, ADR 0032), so this rule excludes nothing, whatever you list here.
  A record with no language recorded is kept."
- `subject_areas` is enforceable in this mode. It uses each work's primary topic. Leave it
  empty for this walkthrough.
- The starter exclusion codes under `manual_abstract` are the reasons the screening keys offer.
  Edit them before you screen. Here, `OFF_TOPIC` is key `1`.

When you change anything in `criteria.yaml`, bump `version` (for example, `0.1.0` to `0.1.1`).
Record the change in the repository where you keep your review. The criteria history is read
from git, so an uncommitted change cannot be replayed.

### Step 4. Check the size before you commit to the search

A large search spends quota, and it can be discarded if the count moves while it runs. Check the
count first. This call requests one page only:

```bash
uv run prismabib openalex-search baseball-pitch --budget 1
```

`--budget` is the most pages this call may request. A page holds 100 works. The option is
required, and it is also required with `--dry-run`. The stop line looks like this:

```text
stopped: the page budget of 1 is used (run <run id>, 1 page(s) this call, spent $0.001). The run is not sealed.
Run the same command again to resume from the saved cursor.
```

The stop line does not show how many works OpenAlex reports, so this check reads the count from
the run's `progress.json`. The file is kept while the run is unsealed. A future release may print
the count on the stop line; until then, read it from the file:

```text
<projects root>\baseball-pitch\raw\openalex-search\<run id>\progress.json
```

Look for `total_count`:

```json
  "total_count": 108,
```

For this query the count is 108. If the count is in the hundreds or thousands, narrow `search` in `project.toml` before you go on. A broad query can return thousands of works; one example query returned 2,497. The run you started stays on disk, unsealed. A changed query starts a new run.

The check spends one page. The next call resumes this run, so no page is paid for twice.

### Step 5. Capture the search

Check the cost of the real run first. Nothing is requested or written:

```bash
uv run prismabib openalex-search baseball-pitch --budget 5 --dry-run
```

```text
dry run: no request made, nothing written
query: "baseball" AND "pose estimation" AND "pitcher"
query key: "baseball" AND "pose estimation" AND "pitcher"
select: id,doi,title,publication_year,abstract_inverted_index,authorships,topics,type,primary_location,primary_topic,cited_by_count
page budget: 5 pages of 100 works
cost bound: at most $0.005
OpenAlex key: set
```

Now the real run:

```bash
uv run prismabib openalex-search baseball-pitch --budget 5
```

`--budget 5` is a ceiling. The search needs two pages, so the call stops after the second page.
A sealed run prints this:

```text
run <run id> sealed: 108 works captured of 108 reported by OpenAlex (<n> page(s) this call, spent $<cost>).
Next: prismabib build baseball-pitch --rebuild
```

A run is sealed only when every page arrived and the works captured equal the count OpenAlex
reported. The `<n> page(s) this call` counts only the pages of this call. It is `1 page(s)` if you ran the size check first, because that check fetched page 1; otherwise it is `2 page(s)` for this query.

If the call prints `stopped` instead, run the same command again. It resumes. See
[If something goes wrong](#if-something-goes-wrong).

### Step 6. Build the store

```bash
uv run prismabib build baseball-pitch --rebuild
```

`--rebuild` builds the store from the sealed runs. Without it, an existing store is reused, and
nothing new is loaded. The command also prints one `[info] store.build_store.complete` log line above the summary; it is left out below. The summary looks like this:

```text
Rebuilt store at <projects root>\baseball-pitch\store\corpus.duckdb

  search runs loaded                1
  records (OpenAlex works)        108
  authors                         404
  affiliations                    135
  keywords                         87
  record-keyword links            324
  subject-area links              108
  citation snapshots              108
  openalex-topic links            324

  duplicate DOI groups: 0 (0 records). Reported, never applied -- every one of those rows is still in the store; deduplication is a screening decision.

Next: prismabib flow baseball-pitch
```

What the lines mean:

- `search runs loaded` counts the sealed OpenAlex search runs the store was built from. Each
  sealed run adds one. A `--refresh` run adds one more, and adds no works.
- `records (OpenAlex works)` counts the works loaded. A work found twice is loaded once.
- `authors` and `affiliations` come from the works' authorships, with OpenAlex ids.
- `keywords` and `record-keyword links` are OpenAlex topics, and `openalex-topic links` counts
  the links of that kind.
- `subject-area links` count the works whose primary topic maps to a subject code. A work
  without a mapped code has no subject-area row.
- `citation snapshots` hold each work's `cited_by_count` at the time of the run.
- `duplicate DOI groups` are reported, never removed. Removing one is a screening decision.

The `runs loaded`, `records` and `venues` lines of a Scopus build are replaced by the two lines
at the top. The rest of the summary is the same.

### Step 7. Print the flow counts

```bash
uv run prismabib flow baseball-pitch
```

The output below uses the year window from step 3 (2015 to 2026) and is printed before any
screening. Counts drift as OpenAlex updates its records, so your numbers may differ:

```
PRISMA 2020 flow -- project 'baseball-pitch'

Identification
  records identified (OpenAlex meta.count)            108

Removed before screening
  duplicates across searches                            0
  other reasons (unreadable capture entries)            0

Screening -- automated, from criteria.yaml
  excluded by automated criteria                      -11
      by publication year                              11
      by subject area                                   0
      by document type                                  0
      by conference whitelist                           0
  remaining                                            97
  excluded by language                                  0
  remaining, to title/abstract screening               97

Screening -- title/abstract, from logged human decisions
  excluded                                              0
  unsure or not yet screened                           97
      (unsure never resolves to inclusion; it stays in the queue)
  sought for full-text retrieval                        0

Eligibility -- full text, from logged human decisions
  excluded                                              0
      (no full-text exclusion reason codes logged)
  unsure or not yet screened                            0
      (unsure never resolves to inclusion; it stays in the queue)

Included
  studies in the final corpus                           0
```

Read the signs like this:

- A line with a minus sign subtracts from the line above it. `-1` means one record was removed.
  The automated section has two subtractions: `excluded by automated criteria` and
  `excluded by language`. Each `remaining` line is what is left after the subtraction above it.
- The indented lines under `excluded by automated criteria` are a breakdown of that one line.
  They are plain counts. They add up to it and are not subtracted again.
- `by publication year 11` depends on the window you set in step 3, which for this output is
  2015 to 2026. Counts drift as OpenAlex updates its records, so your numbers may differ.

If the counts do not close, `flow` prints a warning on stderr. No warning means they close.

### Step 8. Screen ten records

Set the project slug and your name. Then open the screener. In PowerShell:

```powershell
$env:PRISMABIB_NOTEBOOK_SLUG = "baseball-pitch"
$env:PRISMABIB_NOTEBOOK_REVIEWER = "<your name>"
uv run panel serve notebooks/01_screen_title_abstract.ipynb --show
```

In bash, put both variables in front of the command on one line. Without the slug the notebook
shows a red **Screening cannot start** message that names the reason. The screen opens with
author names and citation counts absent, not hidden: the view does not receive them. The notebook uses the default. To show them, call `screener(..., blind=False)` yourself.

The keys come from the notebook's key map:

| Key | What it does |
| --- | --- |
| `i` | Include this record. |
| `e`, then a digit | Exclude it. The digit picks the reason from the palette. `1` is the first code under `manual_abstract` in `criteria.yaml`. |
| `u` | Mark unsure. The record stays in the queue. |
| `n` / `p` | Move to the next or previous record without deciding. |
| `z` | Undo this record's decision. With no decision on the record, undo the last one. |
| `?` | Show or hide the help. |

Screen about ten records. The goal is to see how the keys behave, not to finish the review.

Each key that makes a decision is appended to `decisions/decisions.jsonl` before the view moves
to the next record. The file is append-only. Changing your mind adds a new line, and nothing is
edited. The decision log is under `<projects root>/baseball-pitch/decisions/`.

### Step 9. Print the flow again

```bash
uv run prismabib flow baseball-pitch
```

The numbers move as follows:

- `excluded` goes up by one for each record you excluded.
- `sought for full-text retrieval` goes up by one for each record you included.
- `unsure or not yet screened` goes down by the total of those two.
- Undoing a decision with `z` reverses its effect on those lines.
- The automated lines do not change, because screening does not touch them.

No number is typed in. Every line is recomputed from the store and the log each time you run it.

### Step 10. Optional: register a paper OpenAlex did not return

Use this step when you know a paper that the search missed. A reviewer registers it as a record.
The flow counts it on its own `registered` line and never adds it to `identified`. The example
below uses a paper that is not among the 108 works. The full rules are in
[Register and Reverse a Candidate](how-to/register-and-reverse-candidates.md).

1. Paste the candidate. A row is `title<TAB>year[<TAB>doi]`, or the same fields separated by
   commas. Reading from stdin needs `--reviewer`:

   ```bash
   printf 'Deep Residual Learning for Image Recognition\t2016\t10.1109/CVPR.2016.90\n' | uv run prismabib candidates add baseball-pitch --reviewer "<your name>"
   ```

   The output is `added 1, enriched 0, already present 0, conflict 0`. In PowerShell, write the
   row to a file with a real tab between the fields, and pass `--file <path>` instead of piping.

2. Capture the OpenAlex record for the candidates:

   ```bash
   uv run prismabib cascade baseball-pitch
   ```

   Do not add `--opt-in` or `--scopus-budget`. This mode refuses both. The output includes
   `openalex: value 1, not_found 0, refused 0` and `remaining unavailable: 0`, then the per-field
   tables. The response is sealed under `raw/openalex/<run>/response-0000.jsonl`. The run name is
   the directory that `cascade` creates there.

3. Register the candidate. The candidate id is the `candidate_id` field of your row in
   `decisions/candidates.jsonl`. Open that file and copy it. The `candidates` command has only
   `add`, and no command lists ids. The id is a hash of the normalised title and year, so the same
   paper gets the same id however it is pasted. `--payload` is relative to `raw/`:

   ```bash
   uv run prismabib register baseball-pitch <candidate_id> --reviewer "<your name>" --payload openalex/<run>/response-0000.jsonl
   ```

   The output is `registered registered:<id> (event <id>)`. The match rule defaults to `doi`,
   which is what this candidate needs. A candidate with no DOI uses `--match-rule title_year`.
   A near match needs `--match-rule near_confirmed --confirm-near`.

4. Rebuild the store, then print the counts:

   ```bash
   uv run prismabib build baseball-pitch --rebuild
   uv run prismabib flow baseball-pitch
   ```

   The build summary is the same as in step 6, except that `subject-area links` is 109. The
   registered paper adds one subject row. The flow output gains a line under Identification:

   ```text
   registered by a reviewer (other methods)   1
   ```

   `records identified` stays at 108. The registered paper is screened like any other record. If
   it fails a criterion in `criteria.yaml`, it is counted in the automated lines. Otherwise it joins
   the screening queue. The counts in the step 7 sample were printed before this registration and
   do not include it.

5. List the active registrations:

   ```bash
   uv run prismabib registrations baseball-pitch
   ```

   Each line reads `<registration id>  registered:<candidate id>  <doi>  <reviewer>  <timestamp>`.
   The DOI is shown in lower case, as normalised.

### Step 11. Export the citable bundle

```bash
uv run prismabib export baseball-pitch
```

```text

Exported baseball-pitch to <projects root>\baseball-pitch\exports
  figures                         2
  table renderings               18
  numbers.json keys              74
  git commit              <first 12 characters of the commit>
```

The `git commit` line is the commit of the prismabib code that produced the numbers. It is not
the commit of your review folder. If the prismabib clone has uncommitted changes, the export
prints a warning: `manifest.json` records `dirty=true`, and the recorded commit does not describe
the code that produced the numbers. The export still writes its files, but do not cite its
numbers until the tree is clean. The same applies when the commit is on no remote branch, because
a reader cannot fetch the code. Commit or discard your changes in the prismabib clone and export
again.

Commit `criteria.yaml` and `project.toml` before you export. The criteria history is read from
git, so an uncommitted amendment cannot be replayed. This repository ignores `projects/` (the
`projects/` rule in `.gitignore`), so git does not pick the review up by itself. Choose one of
two ways:

- Keep the review in your own repository. Set `PRISMABIB_PROJECTS_ROOT` to a folder inside that
  repository, and commit the two files there.
- Or add the two files to this clone with `-f`, and commit them. The `.gitignore` comment gives
  the same command for `decisions.jsonl`:

  ```bash
  git add -f projects/<slug>/criteria.yaml projects/<slug>/project.toml
  git commit -m "Protocol for <slug>"
  ```

Do not add `raw/`, `store/` or `fulltext/`. Those are licensed or derived, and the ignore rules
keep them out. A force-added file is tracked from then on. If you edit it and do not commit, the
clone is dirty, and the export warns.

The export's dirty check reads the prismabib clone, which is the code that produced the numbers
(`export.py`, `_code_root`). It is not your review folder. A review folder that is git-ignored
does not make that clone dirty. A force-added file that you have edited and not committed does,
because it is in the same repository. The export also warns when the commit is on no remote
branch. That is expected for a local review.

After an export, `exports/` lists these entries:

```text
exports/
├── figures/          (prisma_flow.csv, prisma_flow.svg)
├── tables/
├── manifest.json
├── numbers.json
├── provenance_conflicts.csv
└── records.csv
```

What the export writes, under `exports/`:

- `figures/prisma_flow.svg` is the PRISMA 2020 diagram. `figures/prisma_flow.csv` holds the
  numbers the diagram shows.
- `tables/` holds six tables. Each one is written as `.csv`, `.md` and `.tex`. That makes the
  18 table renderings in the output.
- `records.csv` has one row per record. Each row has its identifiers and, for abstract, authors,
  affiliations and keywords, four provenance columns: `<field>_source`, `<field>_level`,
  `<field>_outcome` and `<field>_payload_ref`.
- `provenance_conflicts.csv` lists disagreements between Scopus and OpenAlex values. This
  walkthrough has no Scopus records, so there is nothing to disagree with.
- `numbers.json` holds the scalars a manuscript may cite. In this mode it includes
  `methods.source_sentence`. For the example query the sentence reads:

  ```text
  Records were identified by searching OpenAlex; no Scopus search was run. The search was `"baseball" AND "pose estimation" AND "pitcher"`, run on 2026-11-05. OpenAlex reported 108 works across 1 distinct query. Metadata, including abstracts rebuilt from OpenAlex's inverted index and subject areas taken from OpenAlex's primary topic, is OpenAlex's own (see Limitations).
  ```

  Your sentence shows your query and your dates.
- `manifest.json` records the criteria version, the run ids, the package version, the git commit,
  whether the tree was dirty, the number included, and when the export ran.

`figures/` and `tables/` are rebuilt from nothing on each export. Do not edit files under
`exports/` by hand. Re-run the export instead.

### Step 12. Fill a manuscript

```bash
uv run prismabib fill <manuscript.md> <projects root>\baseball-pitch\exports\numbers.json -o <filled.md>
```

`fill` substitutes `{{key}}` placeholders from `numbers.json`. It fails in both directions. It
fails if the manuscript cites a key that `numbers.json` does not define, and it fails if
`numbers.json` has a key that the manuscript never cites. A short paragraph therefore fails.
[Run the Demo](how-to/run-the-demo.md) explains the rule in its "Before the day" item 5, "Prepare
the manuscript", and shows how to list the keys to cite. That example is written for a Scopus
demo, so take its key counts as examples and generate your own list from your `numbers.json`.

### What you have now

- A sealed OpenAlex search run under `<projects root>/baseball-pitch/raw/openalex-search/<run id>/`,
  with its response pages and `manifest.json`.
- A Layer 1 store at `<projects root>/baseball-pitch/store/corpus.duckdb`. You can delete it and
  rebuild it from the runs.
- Screening decisions in `decisions/decisions.jsonl`.
- The PRISMA flow counts, from `flow`, which are recomputed from the store and the log.
- The exports under `exports/`, including the diagram and `numbers.json`.

### Where to go next

- [Limitations](methodology/limitations.md). Read the "Mode `openalex`" part before you cite a
  number. It covers OpenAlex's own metadata, the moving counts, the daily allowance and the way
  duplicates are matched.
- [Build an OpenAlex Query](how-to/build-an-openalex-query.md). The query rules and each outcome
  of `openalex-search`.
- [Enter Credentials](how-to/enter-credentials.md). Status, testing and removing keys.
- [Amend Eligibility Criteria](how-to/amend-eligibility-criteria.md). Changing `criteria.yaml`
  after screening has started.
- [PRISMA Mapping](methodology/prisma-mapping.md). Each number in the diagram, and where it comes from.

## The Scopus path

Use this path if you answered yes and you want a Scopus-only review (mode `scopus`). The steps
below are the Scopus steps this page has always had, with the credentials step added. The same
path in more detail, with a look at the Layer 0 files, is in [Run a New Review](how-to/run-a-new-review.md).

!!! warning "A Scopus project needs a Scopus API key entitled to the COMPLETE view, and many researchers do not have one"

    `ScopusClient.search` always requests `view=COMPLETE` and **never** falls back to
    `STANDARD`. A 403 raises `EntitlementError` and the run stops. That is deliberate:
    `STANDARD` omits `authkeywords`, the full `author` block and `dc:description`, so a
    corpus built from it would be missing author keywords, affiliations and abstracts
    while looking complete — quietly biasing every keyword and geography analysis built on
    it afterwards.

    The `COMPLETE` entitlement is granted to **subscribing institutions**, not to personal
    or free keys. In practice:

    1. Running from your institution's network is often enough.
    2. Off campus, ask your university library for a Scopus **institutional token** and set
       `SCOPUS_INSTTOKEN` alongside `SCOPUS_API_KEY`. This is a routine request; librarians
       handle it regularly.
    3. If your institution has no Scopus subscription, use the OpenAlex-only path above
       (source mode `openalex`). It needs no Scopus key.

    Get a key at [dev.elsevier.com](https://dev.elsevier.com). There is no way to discover
    the entitlement problem except by attempting a capture, which is why it is stated here
    first.

Two more access facts worth knowing:

- **Scopus enforces a weekly quota.** A large capture spends it. `prismabib search` is
  resumable and never re-fetches a page Layer 0 already holds, so an interruption costs
  nothing, but a repeatedly restarted *fresh* run does.
- **Scopus is the only source `prismabib search` queries.** No Web of Science. OpenAlex,
  Crossref, Unpaywall and ScienceDirect clients exist, and full-text retrieval is built
  (`prismabib fulltext`). See [Limitations](methodology/limitations.md) before adopting this
  for a real review.

### Credentials

Store the key with the credentials command. It prompts for `SCOPUS_API_KEY` (hidden), then for
`SCOPUS_INSTTOKEN`, which you can leave empty:

```bash
uv run prismabib credentials set scopus
uv run prismabib credentials test scopus
```

`credentials test scopus` makes one request and prints `scopus: ok`, or a 401, 403 or network
result. See [Enter Credentials](how-to/enter-credentials.md).

You can also write `.env` by hand. From the repository root:

```bash
cp .env.example .env
```

Then edit it:

```bash
SCOPUS_API_KEY=<your key>
SCOPUS_INSTTOKEN=          # only if your library issued one; leave empty otherwise
ELSEVIER_SD_API_KEY=       # used by full-text acquisition (ScienceDirect); may be the Scopus key
PRISMABIB_PROJECTS_ROOT=./projects
```

!!! danger "Do not paste your API key into SCOPUS_INSTTOKEN"

    That field is for a separate institutional token issued *for that same key*. Scopus
    reports the mismatch as `401 "Institution Token is not associated with API Key"`. If
    you have no token, leave the variable empty rather than filling it with a placeholder.
    See [Provenance — troubleshooting](architecture/provenance.md#troubleshooting-scopus_insttoken-trap).

`.env` is gitignored and must stay that way. Every secret is typed `SecretStr`, so it
cannot leak through `repr`, `str`, `model_dump`, a traceback, or a log line — the Scopus
client also registers the key with a structlog processor that scrubs it from every event.

`init` itself needs no credential, so you can lay a project out before your key arrives.
`.env` is needed from `prismabib search` onward, which is the first step that talks to Scopus.

### Create the project (`init`)

```bash
uv run prismabib init my-review --sources scopus --title "My systematic review"
```

Options: `--title/-t` (written into `project.toml`), `--root/-r` to override
`PRISMABIB_PROJECTS_ROOT` for this one command, and the source mode of a new project:
`--sources {openalex,scopus,scopus+openalex}`, recorded in `project.toml` under `[sources]`
(ADR 0042). With a terminal and no `--sources`, `init` asks "Do you have access to Scopus?";
in a script it refuses without `--sources`, and writes nothing. A new project also records who
made the choice: `--actor NAME`, or `git config user.name` when omitted. Re-running `init` on an
existing project never prompts and never rewrites it; `prismabib sources <slug> --set <mode>`
changes the mode, and `prismabib doctor <slug>` shows it.

This creates the project skeleton and prints the two files you must edit next:

```
projects/my-review/
├── project.toml          # [project] metadata + the [query] table you fill in
├── criteria.yaml         # your eligibility criteria
├── decisions/
│   └── decisions.jsonl   # empty; the append-only Layer 2 log
├── raw/                  # Layer 0 archive (never git-tracked)
├── store/                # Layer 1 DuckDB (never git-tracked)
├── taxonomy/rules/
├── fulltext/             # (never git-tracked)
└── exports/
```

`init` is idempotent and safe to re-run: it recreates any missing directory but never
overwrites `project.toml`, `criteria.yaml`, or `decisions.jsonl`. Those hold hand-written
methodology and human labour. When it finds an existing project it says `Reused` rather
than `Created`, so it never points you at a template you filled in months ago.

Commit `project.toml` and `criteria.yaml`. Criteria history is resolved from git alone, so
an uncommitted amendment is invisible to replay later.

### Edit `project.toml` — the search itself

```toml
[project]
slug = "my-review"
title = "My systematic review"
created = 2026-08-25
track_decisions = true

[query]
terms = [
  "urban heat island",
  "urban thermal environment",
]
compound_terms = [
  { all = ["heat exposure", "urban"] },
]
fields = ["TITLE-ABS-KEY"]
```

Substitute your own concepts; the shape is what matters.

- `terms` — each rendered as `FIELD("term")` and OR-ed together.
- `compound_terms` — a **list** of `{ all = [...] }` groups, each becoming a parenthesised
  AND group that is itself OR-ed with everything else. A bare string, a single group
  written without its surrounding `[ ]`, or any key other than `all`, is refused rather than
  coerced into something plausible-looking. A longer list is often easier to read as
  repeated `[[query.compound_terms]]` headers, which mean the same thing.
- Keys this table does not define are **refused, not ignored** — `compound_term` without
  its `s` would otherwise drop a whole AND group and run a narrower search than the file
  describes.
- `fields` — applied to every term. With more than one field, each term becomes a
  parenthesised OR across them.

The example above renders exactly:

```
TITLE-ABS-KEY("urban heat island") OR TITLE-ABS-KEY("urban thermal environment") OR (TITLE-ABS-KEY("heat exposure") AND TITLE-ABS-KEY("urban"))
```

Quotes and backslashes in a term are escaped, so a term cannot break out of its clause and
inject Boolean structure. An empty `[query]` table is refused: prismabib will not silently
run your key against the whole of Scopus.

### Edit `criteria.yaml` — who is eligible

`init` writes a commented template. The parts that decide numbers:

```yaml
version: 0.1.0            # semantic; bump it whenever you change anything below

temporal:                 # inclusive on both ends, from the record's cover date
  year_start: 2015
  year_end: 2026

subject_areas: []         # leave empty — see the warning below

doc_types:
  include: [ar, cp]       # Scopus subtype codes; empty = no document-type restriction
  conference_whitelist: []

languages: [English]      # empty = no language restriction

manual_abstract:
  exclude_reason_codes: [OFF_TOPIC, REVIEW_OR_SURVEY, NOT_PRIMARY_RESEARCH]
manual_fulltext:
  exclude_reason_codes: [NO_FULL_TEXT, WRONG_POPULATION, INSUFFICIENT_DATA]
```

Five rules that change published counts, all enforced in code:

- **An empty list means "no restriction on that dimension"**, not "match nothing".
  Emptying `doc_types.include` accepts every type; it does not exclude everything.
- **`languages` matches Scopus's own language string exactly** (case-insensitively):
  `"English"`, not `"en"` or `"eng"`. A record with no language recorded is kept.
- **`conference_whitelist` is a case-insensitive *substring* match on the venue name, and
  applies only to records whose venue is a conference.** A journal article is never
  excluded by it. A short token like `"AI"` matches almost any venue name; prefer a
  distinctive fragment such as `"Computer Vision and Pattern Recognition"`.
- **Unknown keys are refused, not ignored.** Writing `language:` instead of `languages:`
  raises `ConfigError` naming the key, the block it appeared in, and the closest valid
  alternative. A silently dropped key would be an eligibility rule that silently did not
  apply, and the resulting corpus looks entirely plausible.
- **An inverted year window is refused.** `year_start: 2026` with `year_end: 2015` would
  match no record at all and report that automation excluded your entire corpus.

!!! warning "subject_areas cannot be enforced on a Scopus Search API corpus"

    The Search API's `view=COMPLETE` response does not carry subject-area codes, so no
    record in a corpus captured by prismabib has any. Rather than let the filter quietly
    become a no-op — a PRISMA diagram claiming a restriction that never ran — a non-empty
    `subject_areas` against a corpus with no subject-area data raises `ConfigError`.

    Leave it `[]` and record the restriction in your protocol, applying it during
    title/abstract screening where it becomes a logged decision with a reason code. See
    [Limitations](methodology/limitations.md#subject_areas-is-declared-but-not-enforceable)
    for the alternative and its cost.

The reason codes are a **closed vocabulary**: an exclusion citing a code not listed for
that stage is refused, which is what keeps the diagram's exclusion breakdown
pre-registered rather than invented mid-screening. The template ships PRISMA-conventional
starters — edit them to the reasons your review actually distinguishes.

### `search` — capture Layer 0

```bash
uv run prismabib search my-review
```

This spends Scopus quota. Progress goes to stderr, one line per page written; the sealed
run's manifest goes to stdout:

```
Run 20260825T090000Z-3f9a2c11 sealed at projects/my-review/raw/20260825T090000Z-3f9a2c11
  query          TITLE-ABS-KEY("urban heat island") OR ...
  view           COMPLETE
  total_results  1,771  (the PRISMA 'identified' count)
  pages          71
  payload_sha256 9f2c...
  client_version 0.5.0
  criteria       0.1.0

Next: prismabib build my-review
```

- **Interrupting is safe.** Every page is written as it arrives and a cursor is saved after
  each one. Ctrl-C exits `130` and prints how to resume; re-running the same command
  continues from the last page written, at no quota cost for what is already on disk.
  The run's own `_http/` cache serves only this resume and is deleted at seal.
- **A finished run is sealed and immutable.** Sealing is the presence of `manifest.json`;
  every write path refuses a sealed directory in code, not by convention. Running `search`
  again after a completed run starts a *new* run directory rather than amending the old one.
- **`total_results` comes from Scopus's own `opensearch:totalResults`**, never from a count
  of rows or pages. It is this search's contribution to the PRISMA "records identified"
  number: one search string, one term. A project that runs a second, different search string
  adds that search's total too; re-running the *same* string later adds nothing. Such a
  re-run is a new run that **spends the full quota of the query again**: nothing from an
  earlier run is replayed, which is what makes its citation counts current.
- **`raw/` is never git-tracked.** It is large and licensed; the manifest is what makes the
  run reproducible.

### `build` — derive Layer 1

```bash
uv run prismabib build my-review
```

It prints per-table row counts, the duplicate-DOI report, and any affiliation country
string that did not map to an ISO 3166-1 alpha-3 code (kept verbatim, never dropped).

Two things to know:

- **Without `--rebuild`, an existing store is reused as-is** and any Layer 0 run captured
  since it was built is *not* loaded. The output says so explicitly. After a new `search`,
  run `uv run prismabib build my-review --rebuild`.
- **A rebuild is all or nothing.** `--rebuild` builds a new store beside the old one and swaps
  it in only when complete, so a failed or interrupted rebuild keeps the store you had. If the
  store holds no records while a sealed run in `raw/` reports results, a plain `build` raises
  `StoreError`: re-capture that run or inspect its page files. A rebuild reproduces the same
  empty store until `raw/` is fixed.
- **Duplicates are reported, never applied.** Both members of a duplicate-DOI pair stay in
  the store as ordinary rows: removing one is a screening decision, not a load-time one.

The store is derived and disposable. Deleting `projects/my-review/store/corpus.duckdb` and
rebuilding loses nothing — Layer 0 is the archive of record. Deleting the file only bypasses
the refusal above: the rebuild reads the same `raw/` and reproduces the empty store.

### Record your first screening decision

Open `notebooks/01_screen_title_abstract.ipynb`, set the slug, and run it. The same object
serves outside a notebook with no code change:

```bash
PRISMABIB_NOTEBOOK_SLUG=<your-slug> PRISMABIB_NOTEBOOK_REVIEWER=<your-name> \
  uv run panel serve notebooks/01_screen_title_abstract.ipynb --show
```

`PRISMABIB_NOTEBOOK_SLUG` is required for your own project: the notebook defaults to the bundled `reference` fixture, which exists only under the test fixtures. Launch without it and the notebook renders its prose plus a red **Screening cannot start** message naming the project it could not find.

![The screening view: progress and pace, one record, the reason palette, and the mouse
controls](assets/screening-ui.png)

`i` includes · `e` then a digit excludes under that reason code · `u` is unsure · `n`/`p`
move without deciding · `z` withdraws the decision on the record you are looking at · `?`
shows the map. Every resolving keystroke is appended and `fsync`ed before the view moves, so
closing the tab costs at most the record on screen.

Three things on that screen are deliberate and worth knowing before you rely on them:

- **Author names and citation counts are absent**, not hidden. Both move human judgement
  and neither is an eligibility criterion. Pass `blind=False` if your protocol says
  otherwise.
- **The order is seeded from your project slug** and is uncorrelated with citation count,
  so you are not calibrating on the most-cited papers first. It is stable as your corpus
  grows, so re-capturing does not send you back through a reshuffled queue.
- **The record id under the status line carries its current decision**, which is what makes
  `z` checkable: step back with `p`, see `· include`, press `z`, see `· unsure`.

Decisions are events in `decisions.jsonl`, and the same append is available directly —
useful for scripting, and for understanding what the view is doing. Run this from the
repository root:

```python
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus

project = Project.open("my-review")

# The screening queue: everything that survived the automated filters.
corpus = Corpus.open(project)
queue = corpus.records(PrismaStage.LANGUAGE).select("record_id", "year", "title")
print(queue.head(10))

log = DecisionLog(project)
log.append(
    stage=PrismaStage.TITLE_ABSTRACT,
    record_id="scopus:2-s2.0-85101234567",  # one from the queue above
    reviewer="sh",
    decision="exclude",  # "include" | "exclude" | "unsure"
    reason_code="REVIEW_OR_SURVEY",  # required for "exclude"; must be declared
    note="secondary study",
)
```

What that append guarantees:

- The event is written in one `write(2)`, `fsync`ed, and covered by a rewritten
  `decisions.jsonl.sha256` sidecar, under an exclusive `flock`. Hand-editing the log is
  detected on the next read; an *interrupted* append is diagnosed distinctly from tampering.
- `criteria_version` is stamped automatically from the project's current `criteria.yaml`.
  Pass it explicitly only when backfilling a decision genuinely made under a superseded
  version.
- `reason_code` is mandatory for `exclude` and must be one of that stage's declared codes.
- Nothing is ever edited or deleted. Changing your mind means appending a new event; the
  fold key is `(stage, record_id, reviewer)`, so the later event supersedes the earlier one
  for that key and nobody else's decision is touched.
- `"unsure"` never resolves to inclusion. It keeps the record in the queue.

### `flow` — the PRISMA 2020 counts

```bash
uv run prismabib flow my-review
```

```
PRISMA 2020 flow -- project 'my-review'

Identification
  records identified (Scopus total_results)         1,771

Removed before screening
  duplicates across searches                            0
  other reasons (unreadable capture entries)            0

Screening -- automated, from criteria.yaml
  excluded by automated criteria                     -412
      by publication year                             355
      by subject area                                   0
      by document type                                 57
      by conference whitelist                           0
  remaining                                         1,359
  excluded by language                                -37
  remaining, to title/abstract screening            1,322

Screening -- title/abstract, from logged human decisions
  excluded                                             -1
  unsure or not yet screened                        1,321
      (unsure never resolves to inclusion; it stays in the queue)
  sought for full-text retrieval                        0

Eligibility -- full text, from logged human decisions
  excluded                                              0
      (no full-text exclusion reason codes logged)
  unsure or not yet screened                            0
      (unsure never resolves to inclusion; it stays in the queue)

Included
  studies in the final corpus                           0
```

That is the state after exactly one logged exclusion: 1,322 records reached title/abstract
screening, one is excluded, and the other 1,321 are unscreened — which the report calls
"unsure or not yet screened" rather than quietly counting them as anything else.

Every number is recomputed from Layer 1 and `decisions.jsonl` on each run — nothing is
cached, so the report always describes screening as it stands right now.

That transcript is a project with **one** search string, which is why both "removed before
screening" lines are zero — they are printed even then, so that a reader of a published
diagram can see the line was considered rather than omitted. Run a second search string —
edit `[query]`, `search` again, `build --rebuild` — and they come alive: "records identified"
becomes the sum of `total_results` over the project's *distinct* searches rather than the
first search's total, "duplicates across searches" counts the papers both searches returned
(identified twice, stored once), and "other reasons" counts entries the loader could not read
at all. Re-running the *same* search string to refresh citation counts is not a second search:
it changes neither "records identified" nor the duplicate count. See
[ADR 0013](architecture/adr/0013-identified-sums-across-searches.md).

If the counts do not close into a consistent diagram, the command prints a warning on
stderr, still prints the numbers, and still exits `0`. That is not a crash: the usual cause
is a capture that is incomplete or a store built before the last `search` finished. Do not
publish a diagram whose warning you have not explained. See
[PRISMA Mapping — the five consistency equations](methodology/prisma-mapping.md#the-five-consistency-equations)
and [when equation 1 does not close](methodology/prisma-mapping.md#when-equation-1-does-not-close),
which lists every cause observed so far and what to do about each.

### Scopus errors

Every failure prismabib raises on purpose is printed with its class name and its full
message, and exits `1`. The messages are written to be acted on; nothing re-wraps or
truncates them.

| Error | What it means | What to do |
| --- | --- | --- |
| `EntitlementError` | HTTP 403: your key is valid, but not entitled to `view=COMPLETE` | Run from your institution's network, or get an institutional token. The search is refused rather than silently lowered to `STANDARD`; no field-level fallback is implemented yet |
| `AuthError` | HTTP 401: the key itself was rejected | Check `SCOPUS_API_KEY`; check you did not paste the key into `SCOPUS_INSTTOKEN` |
| `QuotaExceededError` | A quota is exhausted: the weekly Scopus quota, or a server-reported reset more than an hour away (prismabib raises rather than sleeping until it) | Wait for the reset, then run the same command again. Everything already captured is in Layer 0 and resumable. A `fulltext` run stops at the error (it does not fail each remaining record) and stays unsealed; the next `prismabib fulltext` resumes it |
| `ConfigError: … SCOPUS_API_KEY` | No `.env` and no environment variable | `uv run prismabib credentials set scopus`, or `cp .env.example .env` |
| `ConfigError` naming a `criteria.yaml` key | An unknown key — usually a typo | The message names the closest valid key and lists what is valid in that block |
| `ConfigError` about `subject_areas` | A restriction that no record in the corpus can be evaluated against | Set `subject_areas: []`; see [Limitations](methodology/limitations.md) |
| `StoreError: No Layer 1 store at …` | Layer 1 has not been built | `uv run prismabib build <slug>` |
| `LogError` about a `reason_code` | The code is not declared for that stage in `criteria.yaml` | Add it to the vocabulary (and bump `version`), or use a declared code |
| `ValidationError: query has no terms` | `[query].terms` and `compound_terms` are both empty | Fill in the query before running `search` |

Anything that is *not* one of these prints a traceback. That is intentional: an unexpected
exception is a bug, and a bug that prints one polite line is a bug nobody can report.

## The combined path

Use this path if you have Scopus and you want OpenAlex to fill the fields that Scopus does not
return (mode `scopus+openalex`). Everything in [The Scopus path](#the-scopus-path) still applies.
These are the differences:

- **Keys.** Store the Scopus key as above. The OpenAlex key is optional here. The OpenAlex steps
  use it when it is set. See [Enter Credentials](how-to/enter-credentials.md).
- **Mode.** Pass `--sources scopus+openalex` to `init`. In a terminal, answer yes to the Scopus
  question, then yes to "Also fill missing fields of Scopus records from OpenAlex?".
- **Search.** `search`, `build` and `flow` are the same as in the Scopus path.
- **Filling gaps.** `prismabib cascade <slug>` fills missing fields from OpenAlex. Each value
  keeps a label that names its source. It fetches Scopus `STANDARD` and `META` only with
  `--opt-in` and an explicit `--scopus-budget`. `prismabib openalex <slug>` runs the OpenAlex
  step alone. Neither command runs in mode `scopus`.
- **`openalex-search`** is refused in this mode. The OpenAlex-only search is for mode `openalex`.
- **Switching back.** You cannot move this project to `scopus` once OpenAlex enrichment runs
  or an active registration exists. See [Do you have Scopus access?](#do-you-have-scopus-access).

## If something goes wrong

| What you see | What it means | What to do |
| --- | --- | --- |
| `doctor`: `FAIL python ...: prismabib needs Python 3.11 or newer` | Your Python is older than 3.11. | Install Python 3.11 or newer, then run `uv sync` again. |
| `doctor`: `FAIL git: not found on PATH (needed to confirm .env is git-ignored)` | `git` is missing or not on the PATH. | Install git, then open a new terminal. |
| `doctor`: `FAIL projects root: <path> is not writable (<error>)` | prismabib cannot create the projects folder. | Set `PRISMABIB_PROJECTS_ROOT` in `.env`, or pass `--root` to the command. |
| `doctor`: `warn .env: present, not safely git-ignored (<reason>)` | A key in `.env` could be committed by mistake. `doctor` still exits `0`. | Run `uv run prismabib credentials status` for the reason, and fix the ignore rule. |
| `OPENALEX_API_KEY is not set; run `prismabib credentials set openalex`.` | A real search needs the key. Nothing was sent. | Run `uv run prismabib credentials set openalex`. |
| `OpenAlex key: NOT SET (a real run is refused)` | The same problem, shown by `--dry-run`. | The same fix. |
| `Refusing to write <path>: <reason>. A key is only stored in a file that a tracked .gitignore ignores ...` | Git does not confirm that `.env` is ignored by a tracked rule. Nothing was written. | Run the command from the repository root. The reason names the check that failed. A folder that is not a git repository (for example, an unpacked zip) gets `is not inside a git repository`: clone with git instead, or run `git init` and then `git add .gitignore`. `.env` tracked by git is refused too. Fix the reason, then run `credentials set` again. |
| `stopped: OpenAlex's daily budget is spent ... It resets in <h> h <mm> min (00:00 UTC)` or `It resets in <mm> min` | The day's allowance is used up. The run is kept, not sealed. | Run the same command after the reset. It resumes from the saved cursor. If the reset time is not known, the line says `It resets at 00:00 UTC.` |
| `prismabib: UpstreamError`, then `OpenAlex request failed (<error type>).` | A network failure in the middle of a run. | Run the same command again. It resumes from the last page saved, and no saved page is requested again. |
| `a sealed run of this query already exists (run <run id>); no request was made.` followed by `Use --refresh to capture it again as a new run.` | The query was captured before. Nothing was requested. | Nothing, if the sealed run is what you want. To capture it again, run the command with `--refresh`. That adds a new run and spends the quota again. |
| `stopped: the page budget of <n> is used` | `--budget` ran out before the search finished. | Run the same command again. It resumes. A larger `--budget` finishes in one call. |
| `run <run id> discarded: <reason>: discard and restart.` | The run can never seal. Examples: the count changed while the run was paged, or OpenAlex rejected the saved cursor. The command exits with status 1. | Run the same command again. It starts a new run and spends the quota again. The discarded run stays on disk and is never used. |
| `` `search` is not available in sources mode 'openalex' (...). `` | A Scopus command in an OpenAlex project. | Use `openalex-search`, or create a new project with `--sources scopus`. |
| `` `openalex-search` is not available in sources mode 'scopus' (...). `` | The OpenAlex command in a Scopus project. | Create a new project with `--sources openalex`. |
| `cannot change the sources mode from ...` | The rules in [Do you have Scopus access?](#do-you-have-scopus-access) refuse the change. | Create a new project instead. |
| `init`: `A new project needs --sources {openalex,scopus,scopus+openalex}` | There is no terminal to ask the question, so the answer must be given. Nothing was written. | Pass `--sources`. |
| `init`: `A new project needs a named actor` | No `--actor`, no `git config user.name`, and no terminal. Nothing was written. | Pass `--actor "<your name>"`. |
| `the OpenAlex query is empty: edit the search placeholder under [openalex_query]` | `search` is still empty. Nothing was sent. | Fill in `search` in `project.toml`. |
| `[openalex_query] ... has key(s) prismabib does not understand` | A key other than `search` is in the table, such as `filter`. | Remove it. Only `search` is allowed. |
| Notebook shows **Screening cannot start** | The slug is missing or does not match a project folder. | Set `PRISMABIB_NOTEBOOK_SLUG` to the folder name under your projects root. |
| `flow` warns that the counts do not close | The store was built before the last capture finished, or a capture is incomplete. | Let the run seal (`openalex-search` or `search`), then `build --rebuild`. Do not publish the diagram until the warning is explained. |
| `doctor` prints the Windows hint, or Windows blocks a program | Smart App Control or Application Control is blocking an unsigned program. | Use a Python installed from python.org (signed), not a store or portable build. |

## What exists today

All four layers (0 to 3) are built and tested. Concretely:

| You can | Command or API |
| --- | --- |
| Check the environment | `prismabib doctor` (with a slug, the project's mode and keys too) |
| Store, check or remove a key | `prismabib credentials` with `set`, `status`, `test` or `clear` |
| Scaffold a project | `prismabib init <slug> --sources <mode>` |
| Capture OpenAlex works into Layer 0 (mode `openalex`) | `prismabib openalex-search <slug> --budget N` |
| Capture Scopus into Layer 0 (mode `scopus`) | `prismabib search <slug>` |
| Build the Layer 1 store | `prismabib build <slug>` |
| Print PRISMA 2020 flow counts | `prismabib flow <slug>` |
| Export the citable bundle | `prismabib export <slug>` |
| Screen title/abstracts | `screener(project, stage=..., reviewer=...)` in a notebook |
| Record a screening decision | `DecisionLog.append(...)` in Python |
| Register a pasted candidate, or reverse a registration | `registration_view(project, reviewer=...)` in a notebook; see [Register and Reverse a Candidate](how-to/register-and-reverse-candidates.md) |
| Replay under amended criteria | `engine.replay(project, criteria_version=...)` |

`prismabib code` does not exist — deliberately, rather than as a stub that accepts arguments
and does nothing.

## Next

These links apply to every path.

- [Run a New Review](how-to/run-a-new-review.md) — the review workflow in more detail.
- [Limitations](methodology/limitations.md) — what this tool cannot do today. Read it before you cite a number.
- [Architecture Overview](architecture/overview.md) — the four-layer model.
- [Testing](testing.md) — the test taxonomy and how to run each subset.
