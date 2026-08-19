# Run a New Review

Step-by-step guide for executing a full PRISMA + bibliometric review from start to finish.

This document covers **Steps 1–2 only** (Stage 2). Steps 3–10 are added in later stages; see the roadmap section at the end.

## Step 1: Create a project and fill in `project.toml`

### 1a. Clone and set up the repository

```bash
git clone https://github.com/SergioHuesca/PRISMA-Bib.git
cd PRISMA-Bib
uv sync
```

### 1b. Configure environment variables

Create a `.env` file (never committed) in the repository root:

```bash
SCOPUS_API_KEY=<your_api_key>
SCOPUS_INSTTOKEN=                # Leave empty unless your institution issued a separate token
ELSEVIER_SD_API_KEY=             # May be the same as SCOPUS_API_KEY if you have SD entitlements
PRISMABIB_PROJECTS_ROOT=./projects
```

**Important:** Do not paste your API key into `SCOPUS_INSTTOKEN`. That field is for a separate institutional token from your library. If you mistakenly put your API key there, Scopus returns `401 "Institution Token is not associated with API Key"`. See [Provenance — Troubleshooting](../architecture/provenance.md#troubleshooting-scopus_insttoken-trap) for details.

### 1c. Create your project directory

```bash
mkdir -p projects/<slug>
```

where `<slug>` is a short identifier for your review (e.g., `vad-2026` for "video anomaly detection, 2026").

### 1d. Create and fill `project.toml`

Create `projects/<slug>/project.toml`:

```toml
[project]
slug = "vad-2026"
title = "Video anomaly detection in surveillance systems"
created = 2026-01-15
track_decisions = true

[query]
terms = [
  "video anomaly detection",
  "surveillance anomaly detection",
]
compound_terms = [
  { all = ["abnormal event detection", "video"] },
]
fields = ["TITLE-ABS-KEY"]

[snapshot]
retrieved_at = 2026-01-15T09:00:00Z
scopus_view = "COMPLETE"
```

**Explanation:**

- **`[project]`:**
  - `slug`: A machine-readable identifier (used for directory names)
  - `title`: A human-readable title for the review
  - `created`: The date you started the review (ISO 8601)
  - `track_decisions`: If `true`, screening decisions are saved to `decisions.jsonl` for auditability (recommended)

- **`[query]`:**
  - `terms`: Simple terms, each wrapped in `TITLE-ABS-KEY("...")` and OR-ed together
  - `compound_terms`: Groups of terms that must all co-occur (AND-ed within the group, OR-ed with the main terms). Each entry is a mapping `{ all = [...] }` with a list of strings
  - `fields`: The Scopus field codes to search (currently `["TITLE-ABS-KEY"]` is the standard)

The query builder renders this into:

```
TITLE-ABS-KEY("video anomaly detection") OR TITLE-ABS-KEY("surveillance anomaly detection") OR (TITLE-ABS-KEY("abnormal event detection") AND TITLE-ABS-KEY("video"))
```

See [Architecture Overview — query builder](../architecture/overview.md) for more on the query format.

## Step 2: Run the search and capture Layer 0

### 2a. Execute the search

In a Python notebook or script:

```python
from pathlib import Path
from prismabib.project import Project
from prismabib.capture.writer import capture_search

project = Project(Path("./projects/vad-2026"))
manifest = capture_search(project)

print(f"Run ID: {manifest.run_id}")
print(f"Total records: {manifest.total_results}")
print(f"Pages fetched: {manifest.pages_fetched}")
print(f"Payload SHA-256: {manifest.payload_sha256}")
```

This function:

1. Reads `project.toml` and builds the Boolean query
2. Connects to the Scopus API with `view=COMPLETE` (never falls back to `STANDARD`; if `COMPLETE` is not available, an `EntitlementError` is raised and the run stops)
3. Paginates through all results using cursor pagination (starting from `cursor=*`)
4. Writes each page to `projects/vad-2026/raw/<run_id>/page-0000.jsonl`, `page-0001.jsonl`, etc.
5. On completion, writes `projects/vad-2026/raw/<run_id>/manifest.json` and seals the run

**If interrupted mid-run:** Call `capture_search(project)` again. It finds the unsealed run directory, resumes from the first missing page (zero quota cost for cached pages), and completes the run.

### 2b. What is written

After the run completes, your project directory contains:

```
projects/vad-2026/
├── project.toml                       # (you created this)
├── raw/
│   ├── 20260115T090000Z-3f9a2c11/     # run directory
│   │   ├── page-0000.jsonl            # First page of results (25 records each)
│   │   ├── page-0001.jsonl
│   │   ├── ...
│   │   ├── page-0070.jsonl            # Last page (71 pages total for 1,771 records)
│   │   └── manifest.json              # Sealed: query, timestamp, total_results, SHA-256
│   └── _cache/                        # HTTP cache (never git-tracked)
```

**Key points:**

- **`raw/` is never tracked by git** (see [Architecture Overview § What is versioned](../architecture/overview.md#repository-structure))—it is large, licensed, and the manifest is what matters for reproducibility
- **`manifest.json` is the source of truth** for the PRISMA "records identified" count (1,771 in this example), not a count of rows or pages
- **`payload_sha256`** is the SHA-256 hash of all pages concatenated in order—if you re-run the query later, the hash will be identical (proof that Scopus has not changed)
- The run is now **sealed**—no further writes to this directory are permitted

### 2c. Verify the run

Print the manifest to inspect the acquisition:

```python
import json

manifest_path = Path("./projects/vad-2026/raw/20260115T090000Z-3f9a2c11/manifest.json")
manifest_data = json.loads(manifest_path.read_text())
print(json.dumps(manifest_data, indent=2))
```

You should see:
- `query`: the Boolean string built from your `project.toml`
- `total_results`: the PRISMA "records identified" count (1,771)
- `pages_fetched`: how many pages Scopus returned (71)
- `payload_sha256`: the content hash for provenance verification
- `view`: must be `"COMPLETE"`

---

## What happens next

Stage 2 (this stage) covers **Steps 1–2 only**: creating a project and capturing Layer 0.

**Step 3 onwards are delivered in later stages:**

- **Step 3 (Stage 3):** Title/abstract screening — use the Panel UI to filter records based on title and abstract
- **Step 4 (Stage 4):** Full-text eligibility — apply detailed criteria and attempt to capture full text
- **Step 5 (Stage 5):** Keyword and co-author network analysis
- **Step 6 (Stage 6):** Citation and impact analysis
- **Step 7 (Stage 7):** Geographic and institutional analysis
- **Step 8 (Stage 8):** Taxonomy coding — code records using versioned rules and human overrides
- **Step 9 (Stage 9):** Dashboard exploration — interactive queries over the corpus
- **Step 10 (Stage 10):** Export and report — generate PRISMA flow diagrams, figures, and manuscript assets

Each notebook will include expected inputs, key parameters, expected outputs, and troubleshooting.

---

See [Architecture Overview](../architecture/overview.md) for the four-layer design concept, [Provenance](../architecture/provenance.md) to understand how numbers trace back to Scopus, and [Getting Started](../getting-started.md) for a faster walkthrough of the full workflow (once it is complete).
