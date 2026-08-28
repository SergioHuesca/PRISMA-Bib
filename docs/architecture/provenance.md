# Provenance

This page explains how any number in prismabib output traces back to a versioned source. **This is the page a journal referee reads to decide whether to believe the numbers.**

## The chain of provenance

Every number published in a prismabib review is derived through four immutable layers. Here's how a claim like "Scopus returned 1,771 records on 2026-01-15" remains provable years later:

```
Raw Scopus HTTP response bytes
        ↓ (cached, pre-parse)
raw/<run_id>/page-NNNN.jsonl (verbatim JSONL)
        ↓
raw/<run_id>/manifest.json (SHA-256, query, timestamp, total_results)
        ↓ (parsed)
Layer 1: DuckDB (rebuildable from Layer 0)
        ↓
Layer 2: Decision log (append-only events)
        ↓
Layer 3: Figures, tables, PRISMA flow diagram
```

### Layer 0: Raw Capture (immutable)

Every HTTP response from Scopus is persisted **verbatim** before any parsing happens. This layer is what allows the provenance chain:

- **Cache keying:** Each API call is keyed on `(URL, parameters)` and cached in `raw/_cache/` before parsing
- **Page storage:** Parsed pages are written to `raw/<run_id>/page-0000.jsonl`, `page-0001.jsonl`, etc. in fetch order
- **Envelope storage:** The HTTP response envelope (status, headers, `opensearch:totalResults`, cursor tokens, etc.) is written separately to `raw/<run_id>/page-0000.meta.json`, `page-0001.meta.json`, etc.
- **Manifest seal:** Once all pages are written, `raw/<run_id>/manifest.json` is written—this file's presence is the **only** signal that the run is complete and sealed

#### Page file format (true JSON Lines)

Each `raw/<run_id>/page-NNNN.jsonl` file contains **one Scopus entry object per line**, in JSON Lines format (RFC 4180 variant: one JSON object per line, newline-separated, no array wrapper):

```jsonl
{"eid":"2-s2.0-85123456789","dc:title":"Example Paper","prism:coverDate":"2023-01-15",...}
{"eid":"2-s2.0-85123456790","dc:title":"Another Paper","prism:coverDate":"2023-02-20",...}
{"eid":"2-s2.0-85123456791","dc:title":"Third Paper","prism:coverDate":"2023-03-10",...}
```

**Key property:** Each line is independently parseable and corresponds to exactly one record. The line index (0-based, matching Python's `enumerate()` convention) directly identifies a record within a page file—so a `(payload_file, payload_line)` pair uniquely addresses one record.

#### Metadata file format (response envelope)

Each `raw/<run_id>/page-NNNN.meta.json` file contains the HTTP response envelope metadata as a single JSON object:

```json
{
  "page_number": 0,
  "query_string": "TITLE-ABS-KEY(...)",
  "opensearch:totalResults": 1771,
  "opensearch:itemsPerPage": 25,
  "opensearch:startIndex": 1,
  "cursor": "*",
  "link": [
    {"@ref": "first", "@href": "..."},
    {"@ref": "next", "@href": "..."}
  ]
}
```

The manifest (see below) references only the `.jsonl` files, never the `.meta.json` files. Layer 1's loader reads the entries from `.jsonl` and the manifest, never the envelope metadata.

#### The manifest structure

Every acquisition run writes exactly one `manifest.json`:

```json
{
  "run_id": "20260115T090000Z-3f9a2c11",
  "started_at": "2026-01-15T09:00:00Z",
  "finished_at": "2026-01-15T09:45:23Z",
  "endpoint": "https://api.elsevier.com/content/search/scopus",
  "query": "TITLE-ABS-KEY(\"video anomaly detection\") OR ...",
  "view": "COMPLETE",
  "total_results": 1771,
  "pages_fetched": 71,
  "payload_files": ["page-0000.jsonl", "page-0001.jsonl", ..., "page-0070.jsonl"],
  "payload_sha256": "abc123def456...",
  "client_version": "0.3.0",
  "criteria_version": "2.0"
}
```

**Critical invariant:** `manifest.total_results` is the **only** source of the PRISMA "records identified" count. It is read directly from the Scopus API's own `opensearch:totalResults` field on the first page, never derived from a row count or page count.

Where a review ran more than one search string, the diagram's `identified` is the **sum** of `total_results` over the project's distinct searches — one term per distinct query, taken from that query's earliest run. A run that re-queries the same search string (a citation refresh) joins an existing term and adds nothing, so refreshing never moves the identification count. See [ADR 0013](adr/0013-identified-sums-across-searches.md).

#### Layer 0 immutability

Once `manifest.json` exists in a run directory, that run is **sealed**:

- No file in the run directory may ever be written again
- A second call to `capture_search()` with the same query starts a fresh run directory with a new `run_id`
- The sealed directory is part of the repository's `raw/` tree and is included in `.gitignore` because Scopus content is licensed

If `capture_search()` is interrupted mid-run (before `manifest.json` is written):

- A `cursor.json` sidecar exists recording which pages have been durably written
- The next call to `capture_search()` finds this unsealed run and resumes from the first missing page
- Resumption replays from `cursor=*` through all pages (cached, zero quota cost), but only writes new pages to disk

### Tracing a number back to the manifest

Given a count in a figure or table (e.g., "1,771 records"), here's how to verify it:

1. **Locate the manifest:**
   - The project's `raw/` directory contains one or more run directories: `raw/<run_id>/`
   - Each has a `manifest.json`
   - Choose the run corresponding to your publication date. If the review ran several search strings, there is one such run **per search**, and the published identification count is their `total_results` summed — check every manifest, not the first one

2. **Check the query and view:**
   - `manifest.query` must match your review's search strategy
   - `manifest.view` must be `"COMPLETE"` (anything else means the review is incomplete)

3. **Verify the hash:**
   - Concatenate all `.jsonl` files listed in `manifest.payload_files`, in order
   - Compute `SHA-256` of the concatenated bytes (the `.meta.json` envelope files are not included in the hash)
   - Compare to `manifest.payload_sha256`
   - If they match, every byte is unchanged since the run completed

4. **Trace a record to its entry:**
   - A record's `payload_file` and `payload_line` (stored in Layer 1's `records` table) directly identify the raw JSON entry
   - For example, if `payload_file` is `"20260115T090000Z-3f9a2c11/page-0003.jsonl"` and `payload_line` is `42`, open that file, read line 42 (0-indexed, so the 43rd line), and parse it as JSON
   - Every field in that JSON object traces back to the exact raw Scopus entry that produced the record

5. **Read `total_results`:**
   - This is this search's contribution to the PRISMA "records identified" count; the published number is the sum over the project's distinct searches
   - No other derivation is permitted — never a row count, never a page count, and never the same query's total counted twice

### Tracing a PRISMA count through the flow diagram

The PRISMA flow diagram (e.g., "records after screening: 1,550") is derived from Layer 2, the decision log. Each row's count is a direct query over the log:

- Layer 0 `total_results` → Layer 1 normalized records
- Layer 2 screening events → current set membership (derived by folding the log at query time)
- No count is ever stored or copied
- Every figure re-folds the log to compute counts, ensuring they never drift from the log

### Byte-identical re-runs (determinism proof)

A key property of the architecture: re-running the exact same query with a warm cache produces byte-identical outputs:

- The HTTP cache is keyed on `(URL, parameters)`, so a warm re-run returns the same response bytes without quota cost
- Parsing and re-serialization of those bytes is deterministic (JSON with sorted keys, compact separators)
- So `payload_sha256` is byte-identical on a re-run
- This proves: "I did not touch the data; Scopus returned the same results on [date]"

### What is versioned in git (and what is not)

- **Not in git:** `raw/` directory (Layer 0 is gitignored and push-guarded by `scripts/reject_licensed_content.sh`)
- **Optionally in git:** `projects/<slug>/decisions/decisions.jsonl` and its `.sha256` sidecar (Layer 2 event log; recommended so human decisions are not lost)
- **In git:** `projects/<slug>/criteria.yaml`, `taxonomy/rules/*.yaml` (methodology audit trail)
- **On release tags only:** Aggregate export tables and figures (attached as GitHub Release assets, not in tree)

### Troubleshooting: SCOPUS_INSTTOKEN trap

If you are off-campus or using an institution token, you must configure:

```bash
SCOPUS_INSTTOKEN=<your institutional token>
```

**Do not supply your API key here.** This field is for a separate institutional token issued by your library. If you paste your API key into `SCOPUS_INSTTOKEN`, Scopus returns:

```
401 "Institution Token is not associated with API Key"
```

To fix: leave `SCOPUS_INSTTOKEN` empty and use only `SCOPUS_API_KEY`, or obtain a correct institutional token from your library.

### Reproducibility and audit

The entire chain is auditable:

- **Manifest audit:** Show the `manifest.json` file to a reviewer; they can verify the query, timestamp, and hash
- **Byte verification:** They can download the `raw/<run_id>/` directory and hash-check it themselves
- **Decision audit:** Show `decisions.jsonl` (if tracked); it lists every screening decision, reviewer, and timestamp
- **Criteria audit:** Show `criteria.yaml` and the `criteria_version` from the manifest; they are the authoritative screening rules

No number in the published output is ever typed or copied. Every figure is re-derived from the manifest + decision log at query time.

---

See [Architecture Overview](overview.md) for the four-layer design; `BUILD_PLAN.md` §2.2 (lines 99–121) for the layer invariants. That specification is the project's internal contract and is not published in this repository, so it is cited by section rather than linked.
