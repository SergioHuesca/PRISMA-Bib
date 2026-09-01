# Build a Scopus Query

This guide walks you through constructing a defensible Boolean query for your systematic review, using prismabib's tools. The focus is on understanding *what actually happens* when you fill in `project.toml` and `criteria.yaml`, and where each piece goes.

The worked example covers **carbon capture and utilization**, a different domain from your own review. Use it to understand the mechanics, then apply them to your topic.

## The Core Distinction: Query vs. Criteria

This is the most important idea, and the place researchers most often get wrong.

**The `[query]` table in `project.toml`** is server-side. It is the Boolean string sent to Scopus. Every term you put there changes *what Scopus identifies* as matching your search. If you put a term in the query and then remove it, you get a smaller corpus—because Scopus never sees those records at all.

**The `criteria.yaml` file** is client-side. It describes rules applied *after* Scopus has returned results. A criterion filters the records you've already identified, removing them before screening. If you add a criterion and then remove it, your corpus shrinks only if you re-run your screening; Scopus has already returned all those records.

**Why this matters for PRISMA:** The diagram tracks *exclusions*, and those two kinds of exclusions are reported differently.

- Records excluded by the query never appear in the "records identified" count. They are not excluded; they were never identified.
- Records excluded by `criteria.yaml` are *automatically* excluded and appear in the diagram as an automated-exclusion count.

A researcher who puts all their restrictions in `criteria.yaml` ends up with a smaller corpus than one who puts the same restrictions in the query. Both get the same final set of included studies, but their PRISMA diagrams tell different stories about the search. The second diagram says "Scopus found 10,000, we automatically excluded 9,900"; the first says "Scopus found 100, we automatically excluded 0." Only one story is true for the search you actually ran.

**Decision rule:** If a restriction is available *both* ways, prefer the query—because it is honest about what Scopus found. Only move it to `criteria.yaml` if:

1. Scopus's Search API does not support it server-side (e.g., language filtering), or
2. You want to explore sensitivity to that restriction later (e.g., "what if we included reviews too?").

---

## The `[query]` Table: Terms and Compound Terms

Your query lives in `project.toml` under `[query]`. Three keys control it:

```toml
[query]
terms = []                      # simple terms, OR-ed together
compound_terms = []             # AND-groups, each OR-ed with everything else
fields = ["TITLE-ABS-KEY"]      # the Scopus field code(s) to search in
```

### Simple Terms

Each entry in `terms` becomes one clause, OR-ed with all the others:

```toml
[query]
terms = [
  "carbon capture and utilization",
  "carbon capture and storage",
  "direct air capture",
]
```

renders to:

```
TITLE-ABS-KEY("carbon capture and utilization") OR TITLE-ABS-KEY("carbon capture and storage") OR TITLE-ABS-KEY("direct air capture")
```

Use simple terms when you have a list of concepts, any one of which makes a record relevant. Each term is wrapped individually and matched against the fields you name.

### Compound Terms: AND-Groups

A `compound_terms` entry is a mapping with one key, `all`, containing a list of sub-terms. All sub-terms in the group must co-occur:

```toml
compound_terms = [
  { all = ["carbon capture", "economic"] },
  { all = ["carbon capture", "policy"] },
]
```

renders to:

```
(TITLE-ABS-KEY("carbon capture") AND TITLE-ABS-KEY("economic")) OR (TITLE-ABS-KEY("carbon capture") AND TITLE-ABS-KEY("policy"))
```

Each group is parenthesised and AND-ed inside, then OR-ed with everything else. This is how you express "carbon capture *combined with* economic analysis *or* policy analysis"—without it, you'd get records that merely mention economic issues in passing, nowhere near carbon capture.

**Critical TOML shape:** Compound terms are a **list of tables**. The shape is:

```toml
compound_terms = [
  { all = ["term1", "term2"] },
  { all = ["term3", "term4"] },
]
```

**Not** a single table (which would be):

```toml
compound_terms = { all = [...] }  # WRONG
```

**Not** a bare list of lists:

```toml
compound_terms = [
  ["term1", "term2"],             # WRONG
  ["term3", "term4"],
]
```

prismabib refuses all three wrong shapes rather than guessing what you meant, and the message usually names what you wrote. One case does not: writing a single table instead of a list of tables reports a *bare string* that you never wrote — see the [troubleshooting table](#troubleshooting-common-errors) for why, and [#27](https://github.com/SergioHuesca/PRISMA-Bib/issues/27). If you omit the `s` in `compound_terms`, that typo is refused too—not silently dropped, leaving your search narrower than the file describes.

### Multiple Fields

If you want to search a term across multiple field codes, list them:

```toml
[query]
fields = ["TITLE-ABS-KEY", "AUTHKEY"]
```

Each term is then searched in both fields, and the results are OR-ed:

```
(TITLE-ABS-KEY("carbon capture") OR AUTHKEY("carbon capture"))
```

With compound terms, each sub-term gets its own OR across fields, nested correctly:

```toml
compound_terms = [
  { all = ["carbon capture", "direct air"] }
]
fields = ["TITLE-ABS-KEY", "AUTHKEY"]
```

renders:

```
((TITLE-ABS-KEY("carbon capture") OR AUTHKEY("carbon capture")) AND (TITLE-ABS-KEY("direct air") OR AUTHKEY("direct air")))
```

### Field Codes Available

Scopus's Search API supports many field codes. Here are the most common:

| Code | Covers |
| --- | --- |
| `TITLE-ABS-KEY` | Title, abstract, author keywords (the default; recommended for most searches) |
| `TITLE` | Title only |
| `ABS` | Abstract only |
| `AUTHKEY` | Author keywords only (not indexer keywords) |
| `KEY` | All keywords (author keywords *and* subject indexing) |
| `SUBJAREA(...)` | Subject area codes; see [Subject Areas](#subject-areas) |

Use `TITLE-ABS-KEY` unless you have a reason to narrow it. A title-only search is narrower and riskier—some studies have uninformative titles. An abstract-only search risks noise from off-topic mentions.

### Quoted Phrases vs. Free Text

Every term in the query is wrapped in double quotes:

```
TITLE-ABS-KEY("carbon capture")
```

This makes Scopus treat the term as a phrase—words must appear together and in that order. `"carbon capture"` matches "carbon capture and storage" (the phrase is substring-contained) but *not* "capture of carbon" (words are reversed).

If you want to search for word variants—like `"capture"` matching both `capture` and `capturing"—use Scopus's truncation syntax, an asterisk:

```toml
terms = [
  "carbon captur*",   # matches capture, capturing, captured, captures, etc.
  "direct air captur*",
]
```

renders:

```
TITLE-ABS-KEY("carbon captur*") OR TITLE-ABS-KEY("direct air captur*")
```

The asterisk is sent to Scopus as-is and interpreted on their server, not by prismabib.

---

## The Time Window: Query vs. Criteria

Time is specified in two places, and they do different things.

### In the Query: Scopus supports it, the `[query]` table does not render it

Scopus accepts `PUBYEAR` in the Boolean query string perfectly well — `TITLE("baseball") AND PUBYEAR > 2020` returns results from the live API. What does *not* happen is prismabib putting it there for you: `build_query_for_project` renders only `terms` and `compound_terms`, so **the query it sends carries no year restriction at all**, whatever `criteria.yaml` says.

You can confirm this for your own project:

```python
from prismabib.query import build_query_for_project
from prismabib.project import Project

print(build_query_for_project(Project.open("my-review")))
```

The rendered string contains no `PUBYEAR`. This is why a project whose criteria say `year_start: 2015` can still identify records from 1986 — Scopus was never asked to restrict them, and the engine removes them afterwards as an automated exclusion.

**That difference is a methodological choice, not a detail.** Restricting in the query makes `identified` smaller; restricting in `criteria.yaml` leaves `identified` large and reports the difference as *"records excluded by year"* in the PRISMA diagram. The second is usually what a systematic review wants, because the diagram then shows the reader how many records the restriction removed. Restricting in the query hides that number — it was never retrieved, so it cannot be reported.

If you do want the server-side restriction, pass the whole query explicitly:

```python
from prismabib.capture.writer import capture_search
from prismabib.project import Project

project = Project.open("carbon-capture")
manifest = capture_search(project, query='TITLE-ABS-KEY("carbon captur*") AND PUBYEAR > 2010')
```

This narrows what Scopus identifies, so the "records identified" count reflects the restriction. The exact query is recorded in `manifest.json`.

Otherwise, let Scopus return every year and filter in `criteria.yaml` (see below) — which is the default, and the one that keeps the exclusion visible in the diagram.

### In `criteria.yaml`: Automated Filtering

The `temporal` block in `criteria.yaml` defines an inclusive year window. This is an
*excerpt* — `criteria.yaml` also requires `subject_areas`, `doc_types`, `languages`
and the two `manual_*` blocks, and pasting only what is shown here is rejected with a
message naming each missing one. `prismabib init` writes a complete file; edit that
rather than building one from these fragments. The [full worked example](#a-worked-example-carbon-capture)
below shows every block together.


```yaml
# excerpt from criteria.yaml -- see the worked example below for a complete file
temporal:
  year_start: 2010
  year_end: 2026
```

This filter is applied *after* Scopus returns results. Any record whose year falls outside `year_start` to `year_end` (inclusive) is automatically excluded during the screening setup. The PRISMA diagram shows how many records were automatically excluded by this rule.

**The year comes from the record's cover date** (the date Scopus indexes it under), not the access date or any other field.

**An inverted window is refused.** If you write `year_start: 2026` and `year_end: 2010`, prismabib rejects the file and names the mistake—because an inverted window would exclude *every* record and empty your corpus without any signal that something is wrong. It is easy to transpose two numbers; silently running with an empty corpus is not.

### Choosing Between Them

- **Use the query restriction** if you want to be honest about what Scopus found. If your search was limited to 2010–2026, say so in the query, and the "records identified" number reflects that limitation.
- **Use `criteria.yaml` if** you want to explore the sensitivity of your results to the time window. You can re-run the screening under different temporal restrictions without re-capturing from Scopus. This is rare but legitimate in a protocol that specifically asks "how do results change if we include papers from before 2010?"

**For most systematic reviews, restrict in `criteria.yaml` and leave the query unrestricted.** A PRISMA diagram is supposed to show the reader how many records each criterion removed, and a restriction applied in the query removes them before they are ever counted — the number cannot be reported because it was never retrieved. Restrict in the query only when the corpus would otherwise be unmanageably large, and say so in your protocol, because you are choosing not to report that exclusion.

---

## Subject Areas—A Sharp Edge

**Status: available since v0.12.0.** Subject-area filtering needs data the Search API does not return, so it requires a separate enrichment pass — `prismabib enrich` — against Scopus's Abstract Retrieval API. That endpoint had been addressed wrongly since v0.8.0 and never worked; it was fixed in v0.12.0. Abstract Retrieval is also a **different Scopus entitlement** from Search `view=COMPLETE`, so a key that captures your corpus may still be refused here. Read on for the workaround if it is.

Subject-area codes are Scopus's classification of papers into fields: `COMP` (Computer Science), `ENGI` (Engineering), `LIFE` (Life Sciences), etc. They are useful for narrowing to your discipline.

### Why It Is Complicated

The Scopus Search API's `view=COMPLETE` (the only view prismabib uses) does *not* return subject-area codes. So when you capture from the Search API, no record in Layer 1 carries that data. If you then write a non-empty `subject_areas` restriction in `criteria.yaml`, every record *still passes* because "no data" is treated as "not excludable on this dimension" (see [engine.py](https://github.com/SergioHuesca/PRISMA-Bib/blob/main/src/prismabib/prisma/engine.py)'s module docstring). Your filter silently does nothing, your corpus is smaller than you think, and the PRISMA diagram claims an automated exclusion that never ran.

To catch this, prismabib refuses a non-empty `subject_areas` list when not one record in the corpus carries subject-area data. The error message names the situation plainly and offers two fixes.

### Workaround: SUBJAREA(...) in the Query

Use Scopus's server-side subject-area filter directly in your query string:

```python
from prismabib.capture.writer import capture_search
from prismabib.project import Project

project = Project.open("carbon-capture")
manifest = capture_search(
    project, query='TITLE-ABS-KEY("carbon captur*") AND SUBJAREA(ENGI OR CHEM)'
)
```

This restriction is applied by Scopus *before* results are returned, so records outside those subject areas are never identified. The exact query (including the subject-area restriction) is recorded in `manifest.json`.

**Important caveat:** The `[query]` table in `project.toml` cannot express this directly—every entry is rendered as `FIELD("term")`, so a `SUBJAREA(...)` entry would become a literal text search for that string, matching papers that mention "SUBJAREA" in their abstract. It is a trap that produces a near-empty corpus a researcher may then read as "the search works, but the results are just rare."

If you use `SUBJAREA(...)` in an explicit query, leave `subject_areas: []` in `criteria.yaml` (the default). Do not try to list the codes twice.

### When This Feature Is Ready

Once Abstract Retrieval enrichment merges, you will be able to fill `subject_areas` in `criteria.yaml` and have that restriction actually work. At that point, `prismabib enrich` will fetch subject-area codes from Abstract Retrieval for every record in Layer 1, and the criteria filtering will enforce the restriction correctly. That command is not available on `main` yet.

---

## Publication Types and Document Types

The `doc_types.include` list in `criteria.yaml` restricts which document types are included. This is an automated exclusion and appears in the PRISMA diagram.

### Document-Type Codes

Scopus uses two-letter codes for document types:

| Code | Meaning |
| --- | --- |
| `ar` | Article |
| `ip` | Article in Press |
| `re` | Review |
| `cp` | Conference Paper |
| `cr` | Conference Review |
| `ch` | Book Chapter |
| `bk` | Book |
| `no` | Note |
| `ed` | Editorial |
| `le` | Letter |
| `sh` | Short Survey |
| `er` | Erratum |

Write them in `criteria.yaml` as a list:

```yaml
doc_types:
  include: [ar, cp]      # Articles and conference papers only; everything else is excluded
  conference_whitelist: []
```

An empty list means "no restriction"—every document type is accepted. A non-empty list means *only* those types are kept.

### How Matching Works

**Write the code form.** The matching is not symmetric, and the difference is silent.

`_doc_type_matches` compares the record's stored `doc_type` against your list two ways: directly, and against the *description that each of your codes maps to*. The tolerance is therefore on the **record's** side, not on yours:

| your `include` | record stores `Article` | record stores `ar` |
|---|---|---|
| `[ar]` | matches (via the code→description map) | matches (directly) |
| `[article]` | matches (directly) | **no match** |

So `[ar, cp]` is correct whichever form Scopus supplied, while `[article, conference paper]` silently drops any record that carries the bare code. Scopus supplies the description for most records — a real 1,864-record corpus checked while writing this held `'Article'` and `'Conference Paper'` throughout, and both forms passed the same 1,747 records — but "most" is not "all", and a filter that excludes records for a reason you did not choose is exactly the failure this project exists to prevent.

There is no error and no warning when this happens. The records simply do not appear:

```yaml
doc_types:
  include: [article, conference paper]  # Works ONLY for records stored as descriptions
  include: [ar, cp]                     # Also works
  include: ["Article", "CP"]            # Also works (case-insensitive)
```

---

## Conference Whitelist: Substring Matching

The `doc_types.conference_whitelist` is a separate restriction applied *only* to records whose venue is a conference. It is not applied to journal articles at all.

This is useful when you want to include most conference papers but exclude papers from a specific venue (e.g., excluding workshop or symposium papers and keeping only the main conference).

**Matching is substring, case-insensitive:**

```yaml
doc_types:
  include: []                                    # No restriction on type
  conference_whitelist: ["Computer Vision and Pattern Recognition"]
```

This keeps a record if its venue name contains `"Computer Vision and Pattern Recognition"` (case-insensitive). A venue named `"Proceedings of CVPR (IEEE Conference on Computer Vision and Pattern Recognition) 2024"` would match.

**Be careful with short tokens.** A whitelist containing `["AI"]` would match almost any venue name in existence (it contains "AI" everywhere: "Artificial Intelligence," "Applied Information," etc.). Prefer distinctive fragments.

```yaml
# Good: specific, unlikely to match unintended venues
conference_whitelist: ["Neural Information Processing Systems"]

# Bad: too short, matches everything
conference_whitelist: ["NI"]
```

If `conference_whitelist` is empty (the default), no additional restriction is applied—all conferences pass.

---

## Languages

The `languages` list in `criteria.yaml` filters by the language Scopus has recorded for each paper.

**Match exactly (case-insensitively), using Scopus's own language string:**

```yaml
languages: [English]           # Correct
languages: [English, Spanish]  # Correct; multiple languages are OR-ed
languages: [en]                # WRONG: "en" is not what Scopus returns
languages: [eng]               # WRONG: abbreviations do not match
```

A record with no language recorded is always kept—it is never excluded by a language filter.

The language restriction is applied during the screening setup, before manual review begins. It is an automated exclusion and appears in the PRISMA diagram.

---

## Testing Your Query Before Spending Quota

Scopus quota is limited and weekly. Here is how to validate your query cheaply before running a full search.

### 1. Test the TOML Syntax

Your `project.toml` is parsed as soon as you try to use it. You can validate it without touching the network:

```python
from prismabib.project import Project
from prismabib.query import build_query_for_project

project = Project.open("carbon-capture")

try:
    query_string = build_query_for_project(project)
    print("Query is valid:")
    print(query_string)
except Exception as e:
    print(f"Error: {e}")
```

This catches TOML syntax errors, typos in the `[query]` table shape, and empty queries (no terms at all).

### 2. Read the Query String

Once parsed, inspect the actual Boolean string Scopus will receive:

```python
print(query_string)
```

Does it match your intent? Are all your terms there? Check the parentheses and AND/OR operators. Use the rules from [Terms and Compound Terms](#terms-and-compound-terms) to reason through what it will match.

### 3. Run a Scopus Search *Manually*

Visit [scopus.com/search](https://scopus.com/search) (if you have access), paste your query into the advanced search, and see what Scopus returns. This costs no API quota and tells you whether your query is too narrow (returning 0 results) or too wide (returning millions).

### 4. Run a Small Capture

Once you are confident in the query, run a capture with a page limit. `prismabib search` fetches one page (25 results) at a time. You can interrupt it after a few pages and re-run later to complete:

```bash
# Run the search; it will continue from where it left off if interrupted
uv run prismabib search carbon-capture
# Wait for a few pages (a minute or two), then Ctrl+C
# Later, run the same command again to resume
uv run prismabib search carbon-capture
```

This spends a small amount of quota and tells you whether the search is working. If you are unhappy with the results, edit `project.toml` and run the search again—the resumable capture means you do not have to re-pay for pages you already have.

### 5. Check the Manifest

Once you have captured a run (complete or partial), read its manifest:

```json
{
  "query": "TITLE-ABS-KEY(...)",
  "view": "COMPLETE",
  "total_results": 2150,
  "pages_fetched": 5,
  "payload_sha256": "...",
  ...
}
```

`total_results` is what Scopus says it found for your query. `pages_fetched` is how many pages you have downloaded. The manifest tells you the exact query that was sent.

---

## A Worked Example: Carbon Capture

Here is a complete example for a systematic review of carbon capture and utilization research.

### Project Setup

```bash
uv run prismabib init carbon-capture --title "Carbon capture and utilization: a systematic review"
```

### `project.toml`

```toml
[project]
slug = "carbon-capture"
title = "Carbon capture and utilization: a systematic review"
created = 2026-09-01
track_decisions = true

# Carbon capture covers multiple areas: direct air capture (DAC),
# point-source capture, and utilization. The query uses compound_terms
# to ensure papers address carbon capture *and* at least one of the
# critical processes or applications.

[query]
terms = []

compound_terms = [
  # Carbon capture *and* economic analysis
  { all = ["carbon capture", "cost"] },
  { all = ["carbon capture", "economic"] },

  # Carbon capture *and* utilization pathways
  { all = ["carbon capture", "utilization"] },
  { all = ["carbon capture", "uses"] },
  { all = ["direct air capture", "utilization"] },

  # Carbon capture *and* technology focus
  { all = ["carbon capture", "adsorption"] },
  { all = ["carbon capture", "absorption"] },
  { all = ["carbon dioxide removal", "technology"] },
]

fields = ["TITLE-ABS-KEY"]
```

This renders to a Boolean query that Scopus evaluates server-side. Each compound_terms group ensures that papers address both carbon capture AND one of the secondary concepts (economics, utilization, or technology), reducing noise from papers that mention carbon in an unrelated context.

### `criteria.yaml`

```yaml
version: 0.1.0

temporal:
  year_start: 2005
  year_end: 2026

subject_areas: []

doc_types:
  include: [ar, cp, re]                    # Articles, conference papers, reviews
  conference_whitelist: []

languages: [English]

manual_abstract:
  exclude_reason_codes:
    - WRONG_DOMAIN              # Carbon capture not a focus (e.g., general climate mitigation)
    - NOT_PRIMARY_RESEARCH      # Review, survey, or opinion piece
    - MISSING_METHODS           # Technology described but no methodology or evaluation
    - NARROW_SCOPE              # Theoretical or modeling only; no experimental or applied component

manual_fulltext:
  exclude_reason_codes:
    - WRONG_DOMAIN              # Abstract suggested carbon capture; full text does not address it
    - INCOMPLETE_DATA           # Methods or results not sufficiently detailed to extract
    - DUPLICATE_REPORT          # Same study as an already-included paper (preprint, translated)
    - NO_ENGLISH_VERSION        # Full text available only in another language
```

### Running the Review

```bash
# Capture from Scopus
uv run prismabib search carbon-capture

# Build Layer 1
uv run prismabib build carbon-capture

# Open the screening interface and begin title/abstract review
```

---

## Troubleshooting: Common Errors

| Symptom | Likely Cause | Fix |
| --- | --- | --- |
| `ConfigError: ... is not a [query] key; did you mean ...?` | Typo in `[query]` table key (e.g., `compound_term` without the `s`) | Check spelling; prismabib names the closest valid key |
| `ConfigError: query.compound_terms entry must be a mapping ...` | Wrong shape for compound_terms (missing brackets, or a bare list instead of `{all: [...]}`) | Use `compound_terms = [{all: [...]}]` or repeated `[[query.compound_terms]]` headers |
| `ConfigError: ... must not be a bare string (got 'all'); did you mean the single-term group {'all': ['all']}?` | You wrote **one table instead of a list of tables** — `compound_terms = {all = [...]}` rather than `compound_terms = [{all = [...]}]`. The message is misleading here: `compound_terms` is iterated, and iterating a table yields its *keys*, so prismabib sees the string `'all'` and reports that. It is not describing what you wrote. | Wrap it: `compound_terms = [{all = [...]}]`, or use the `[[query.compound_terms]]` header form, which cannot be written wrong |
| `ValidationError: query has no terms: ...both empty` | Both `terms` and `compound_terms` are empty | Add at least one term to either list |
| `ConfigError: temporal.year_end precedes temporal.year_start` | Year window is inverted (e.g., `year_start: 2026, year_end: 2010`) | Swap the two values |
| `ConfigError: ... restricts subject_areas to [...], but not one of the ... records in this corpus carries subject-area data` | Trying to filter by subject_areas on a corpus from the Search API | Leave `subject_areas: []` and use `SUBJAREA(...)` in the query instead |
| `ConfigError: version ... is not a semantic version` | `version` in criteria.yaml is not `MAJOR.MINOR.PATCH` shaped | Use `0.1.0`, `1.0.0`, etc. |
| `ConfigError: an unknown key is not a criteria.yaml key` | Typo in criteria.yaml (e.g., `language:` instead of `languages:`) | Check spelling; the message names the closest valid key |

---

## Design Principles

This query system is built around a few core ideas:

1. **Silence is dangerous.** A misspelled key should fail loudly, not drop a search term. A wrong shape for compound_terms should raise an error naming what you wrote, not guess.

2. **The PRISMA diagram is only as honest as your search.** If you filter server-side (in the query), say so; if you filter client-side (in criteria.yaml), that is a different story the diagram must tell correctly.

3. **Reviewers are domain experts, not programmers.** Error messages name the mistake and suggest the fix, rather than dumping a technical trace.

4. **One read, no surprises.** When you run a search twice with the same query, you get the same results (modulo Scopus's own index changing). Criteria that drift on each run are useless for a reproducible review.

---

## See Also

- [Run a New Review](run-a-new-review.md) — the full end-to-end workflow, including `prismabib search`, building Layer 1, and screening.
- [Amend Eligibility Criteria](amend-eligibility-criteria.md) — changing `criteria.yaml` mid-review, with auditability.
- [Architecture Overview](../architecture/overview.md) — the four-layer data model and how each is used.
- [Provenance](../architecture/provenance.md) — how a PRISMA number traces back to a Scopus manifest.
