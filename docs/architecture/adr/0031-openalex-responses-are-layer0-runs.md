# ADR 0031: OpenAlex Responses Are Captured as Layer 0 Runs

## Status

Proposed — 2026-10-21. Adds a Layer 0 run kind for OpenAlex responses. It is the capture that
[ADR 0028](0028-field-level-fallback-cascade.md) Decision 3 requires, and no code writes it yet.
It extends the Layer 0 layout and changes one `PayloadRef` form, so it is a deviation under §2.6
and needs this record. No OpenAlex capture code is written before the owner accepts it. It moves to
Accepted when the owner approves it on merge.

## Context

### The spec clauses this record relies on

The clauses below are quoted as they appear in this repository. BUILD_PLAN.md is not published
here (see [provenance.md](../provenance.md)), and it was not readable from the worktree where this
record was drafted. Each clause is therefore quoted from the ADR or the code that cites it, and the
owner must check the wording against the frozen spec before acceptance (open question 8).

- **§2.2, Layer 1.** "Layer 1 must be reconstructible from Layer 0 by running one function."
  (quoted in [ADR 0011](0011-abstract-retrieval-for-subject-areas.md), Context.)
- **§2.2, line 102.** "persisted verbatim before parsing" (quoted in the module docstring of
  `src/prismabib/capture/writer.py`).
- **§2.2, Layer 0 immutability.** Paraphrased: a sealed run directory is never written again. The
  rule is enforced in `src/prismabib/capture/layout.py` (`guard_writable`).
- **§2.2, Layer 0 contents.** Paraphrased, as [ADR 0028](0028-field-level-fallback-cascade.md)
  Decision 3 states it: Layer 0 holds the raw HTTP responses.
- **§5 risk 2.** "never re-fetch what Layer 0 already holds" (quoted in ADR 0011).
- **§5 risk 1.** "Fail loudly, never degrade" (quoted in ADR 0028).
- **§2.6.** "§2.6 requires an ADR" for a deviation from a frozen contract (ADR 0012, Context).

Deviations from the frozen contract that this record makes, each under §2.6:

1. A new Layer 0 run kind, `raw/openalex/<run_id>/`, with its own manifest schema.
2. A `PayloadRef` form for OpenAlex that addresses a work by its OpenAlex id, not by a line offset
   (Decision 2). The spec defines `PayloadRef` as "Layer 0 file + line offset".
3. Stored bodies are the verbatim bytes of each response. The Scopus writer re-encodes its pages
   canonically. Decision 2 explains why OpenAlex does not.

### What the client asks for today

`src/prismabib/sources/openalex.py` has these properties, which the capture must reproduce:

- The endpoint is `https://api.openalex.org/works`.
- The `select` list is `id,doi,title,publication_year,abstract_inverted_index,authorships,topics`.
  Every stored body therefore holds only those fields, and the loader must read the request's
  `select` from the manifest rather than assume it.
- DOIs are looked up 50 per request with `filter=doi:a|b|...`. A DOI containing `|` or `,` is
  looked up singly by path, `/works/doi:<doi>`, and a whole-batch 404 falls back to single lookups.
- Title lookups use `search=` with `filter=publication_year:<year>` and `per-page=25`. The exact
  normalised-title match happens in the client, in `search_title`.
- The API key is sent as the `api_key` query parameter, and it is left out of the cache key.
- A 401 or 403 raises `AuthError` or `EntitlementError`, except in an opt-in run, where it returns
  `Refused(status)`. A 404 returns `NotFound`.

Three things the client does not do yet, and which this record requires: it persists nothing under
`raw/`; its bodies go to `raw/_cache/`, which is disposable; and `Refused` results are not stored.

### Why Layer 0 and not the cache

[ADR 0011](0011-abstract-retrieval-for-subject-areas.md) Context rejects `raw/_cache/` for the same
reasons here. The cache is gitignored and disposable, so a `build_store(rebuild=True)` after a
cleanup would have to re-spend OpenAlex requests, or would silently lose the OpenAlex data. OpenAlex
also changes over time, so an OpenAlex value is only reproducible if its retrieval date and bytes
are recorded when it is fetched.

## Decision

### 1. One run per acquisition, under `raw/openalex/<run_id>/`

```text
raw/openalex/<run_id>/
├── response-0000.json   # verbatim bytes of HTTP response 0, including refusals and 404s
├── response-0001.json   # ... one file per HTTP response, in request order
├── progress.json        # resumption sidecar; deleted on seal; never hashed
└── manifest.json        # the seal: OpenAlexRunManifest
```

- The run id comes from `layout.new_run_id()`. The seal is the presence of `manifest.json`, read
  through `layout.is_sealed`. Every write goes through `guard_writable`, and every file is written
  through `atomic_write_bytes`.
- A call resumes an unsealed run only when its request plan is identical: the same identifiers in
  the same order, and the same `fallback_opt_in`. This is the rule `writer._find_resumable_run`
  applies to Scopus. `progress.json` holds the plan and the index of the next unwritten request.
  A sealed run is never resumed.
- The run is not a row in `runs`. OpenAlex identifies no records, and `runs.total_results` is the
  only source of the identified count (BUILD_PLAN S02-AC5). OpenAlex therefore has no
  `total_results` field, the same as `AbstractRunManifest`.
- The directory name is `openalex`. Nothing else lives under `raw/` with that name.

### 2. One file per HTTP response, and the bytes are the payload

Each HTTP response, including a refusal, a 404, and a batch 404 that is later split into single
lookups, is written as `response-NNNN.json`. The file holds the response body exactly as received.
It is not re-encoded. `payload_sha256` and each file's `sha256` are taken over those bytes.

This differs from the Scopus pages on purpose. `writer._write_page` re-encodes canonically so that a
warm cache reproduces the same bytes. An OpenAlex body is already the stored artefact, so the
canonical form adds nothing and would change what "verbatim" means. A body is also one document
with `meta` and `results`. Splitting it into lines, as the Scopus writer does, would be a
prismabib-shaped transform over upstream data. `capture/enrich.py` makes the same argument for its
envelope.

**`PayloadRef`.** The spec defines a payload reference as a file and a line offset. A verbatim JSON
document has no line per work. An OpenAlex `PayloadRef` is therefore the pair (file, OpenAlex work
id), where the id is the `id` field in `results`. This is the deviation listed under Context, and
open question 2 asks the owner to confirm it or choose the line-based alternative.

### 3. The manifest: `OpenAlexRunManifest`

`OpenAlexRunManifest` is a new model in `src/prismabib/capture/manifest.py`. It does not reuse
`RunManifest`, which the scanner in `store/load.py` would parse as a Scopus search run.

| Field | Content |
|---|---|
| `run_id`, `started_at`, `finished_at` | UTC. `started_at` dates the first attempt, so it survives resumption. |
| `endpoint` | `https://api.openalex.org/works`. The base URL only, never a URL with a query string. |
| `fallback_opt_in` | Boolean. The opt-in flag of ADR 0028 resolved item 4. |
| `api_key_used` | Boolean. Whether a key was sent. Never its value. |
| `requests` | One `OpenAlexRequest` per HTTP request, in request order. |
| `payload_files` | The `response-NNNN.json` names, in request order. |
| `payload_sha256` | SHA-256 over the concatenated bytes of `payload_files`, in order. Same pattern as `RunManifest`. |
| `client_version`, `criteria_version` | As in the other manifests. |

Each `OpenAlexRequest` holds:

- `index` and `file`, the position and filename of the response.
- `lookup`, one of `doi_batch`, `doi_single`, or `title_search`.
- `identifiers`, the batch this request was asked for. For DOIs, the normalised lowercase DOIs.
  For titles, a list of `{title, year}`.
- `params`, the query parameters as sent, with `api_key` removed. For `doi_single`, the DOI is in
  the path, so `params` holds only `select`.
- `http_status`, `retrieved_at` (UTC, the time of this request), `sha256`, and `bytes`.
- `outcomes`, one entry per identifier (Decision 5).

Retrieval date: `retrieved_at` is the real request time. The HTTP cache is not consulted by the
capture path (Decision 7), so no request is answered from a file written by an earlier run.

### 4. What is stored, and where the abstract text comes from

**Stored:** the verbatim response bodies, and the manifest. Nothing else is Layer 0 for OpenAlex.

**Not stored as source of truth:** rebuilt abstract text, author lists, affiliation lists, topic
names, and title matches. These are Layer 1 values, computed from the stored bodies by one loader
path. The abstract is rebuilt by `openalex.rebuild_abstract` from `abstract_inverted_index`. The
authors and affiliations come from `authorships_to_fields`, the topics from `topics_to_keywords`.

**Title matching moves to the loader.** ADR 0028 Decision 4 (exact normalised title and the same
year) is applied by the loader to the stored bodies. The `search_title` result in the client is
advisory and is not stored. Otherwise a rebuild would need the client's matching code to reach the
same records, and the rebuild would no longer be one function.

**Layer 1 reads Layer 0 only.** The loader is the only writer of Layer 1 (ADR 0028 Decision 3).
The client's return values are never persisted and never read by the loader.

### 5. Attempt outcomes, and the opt-in flag

**Per request (transport, stored in the manifest):**

- `value`: HTTP 200 with a body. For a title search, `value` means at least one candidate was
  returned, not that a match was found.
- `not_found`: HTTP 404 on a `doi_single` lookup.
- `refused`, with `http_status` 401 or 403. Stored only when `fallback_opt_in` is true. Outside an
  opt-in run, the call raises `AuthError` or `EntitlementError`, writes no manifest, and leaves the
  run unsealed, as ADR 0028 Decision 7 requires for Scopus.

**Per identifier (in `outcomes`):** `value`, `not_found`, or `refused` with its `http_status`. A
DOI that is absent from a 200 batch response is `not_found`. A DOI in a batch that returned 404 is
`not_found` only after its single lookup returns 404.

**Absent is not a transport outcome.** A work that is found, whose requested field is empty, is
`absent` for that field. ADR 0028 Decision 1 requires the field outcome, and it is recorded in
Layer 1 provenance. It is derived from the stored body and is not written to the manifest, because
a second copy in the manifest could disagree with the body. This is a deliberate reading of the
requirement, and open question 7 asks the owner to confirm it.

**The opt-in flag.** `fallback_opt_in` is recorded in every manifest. A run with it false contains
no `refused` outcome, and a test enforces this (Decision 8). A run with it true may contain refusals
and still seals, so its methods text must state the flag.

### 6. The API key rule

The key is `Settings.openalex_api_key`. It is never written to:

1. **A payload.** The stored bytes are the response body, which does not contain the key. If a body
   does contain it, the writer refuses to write that file, the run stays unsealed, and the error names
   the file without the key (open question 3).
2. **A manifest.** `params` excludes `api_key`. `api_key_used` is a boolean.
3. **A log.** The client logs the endpoint and never the query string. The key is also registered
   with `register_secret_for_redaction`, as `sources/scopus.py` does. `openalex.py` does not do this
   today, and the implementation must add it.
4. **A cache key.** The client already passes `params` without the key to `HttpCache`. Capture no
   longer uses the cache (Decision 7), so this rule holds by construction, and a test pins it.

One path also leaks the key outside the client's own code. httpx logs each request URL, query string
included, at INFO level. The implementation must either send the key in a request header, if OpenAlex
accepts that (not verified in this record, which made no live calls; open question 4), or hold the
`httpx` logger at WARNING for the duration of an OpenAlex run.

### 7. Retrieval date, reruns, and the loader's choice

- A rerun is a **new run**, with its own `run_id`, `started_at`, `retrieved_at` values, and
  `payload_sha256`. A sealed run is never overwritten, and the HTTP cache is not a shortcut. The
  only reuse is resuming an unsealed run with an identical plan (Decision 1).
- Layer 1 takes each field's value from the most recent sealed run that holds a `value` outcome for
  that identifier. Run ids sort by time, so the latest run id wins. The supplying run and its
  `retrieved_at` go into the field's provenance. Older runs stay in Layer 0 and are not merged.
  Open question 6 asks whether the owner prefers the first value instead.
- The reproducibility claim covers the recorded payloads only (ADR 0028 resolved item 7). A
  methods text cites the run ids and retrieval dates it used.
- Capture does not use `raw/_cache/`. Every run spends its own requests against OpenAlex's usage
  limits. Open question 5 asks the owner to accept that cost.

### 8. How the loader recognises the run

- **`NON_RUN_DIRNAMES`:** add the name `openalex` to `layout.NON_RUN_DIRNAMES`, with a constant
  `OPENALEX_DIRNAME = "openalex"` beside `ABSTRACTS_DIRNAME`. Today `raw/openalex/` is invisible to the
  Scopus scans only because it has no `manifest.json` directly inside it, which is the accident
  ADR 0011 named and rejected for `raw/abstracts/`. Naming it makes the rule explicit.
- **Discovery:** `store/load.py` gains `_sealed_openalex_run_dirs(raw_dir)`. It lists the sealed
  children of `raw/openalex/`, the same way `_sealed_abstract_run_dirs` does for `raw/abstracts/`.
- **Fold-in order:** after the search runs, because attachment needs `seen_record_ids`, and before
  `_finalise_subject_areas`. Attachment follows ADR 0028 Decision 4: a DOI equal to the record's DOI,
  or else an exact normalised title with the same year. A title match that is not exact goes to the
  review list. No record is created from an OpenAlex body.
- **Malformed bodies:** a body that is not the documented shape is recorded as a malformed entry
  (ADR 0012), not skipped silently.

## Alternatives considered

**Write OpenAlex data straight into Layer 1.** Rejected. Layer 1 would hold data that exists nowhere
in Layer 0. A rebuild would lose it, or would have to re-spend requests, which is the failure
ADR 0011 rejected for the same reason. It also breaks the rule that the loader is the only Layer 1
writer (ADR 0028 Decision 3).

**Cache OpenAlex responses only in `raw/_cache/`.** Rejected. The cache is gitignored and
disposable, and it is keyed on the request rather than on a run. It has no manifest, no seal, and
no retrieval date per response. A cold cache silently changes the corpus, and a reader cannot tell
which OpenAlex data a methods text used.

**Reuse `RunManifest` and the `raw/<run_id>/` layout.** Rejected. The scanner would parse OpenAlex
runs as Scopus search runs, and `RunManifest.total_results` would be a second candidate for the
identified count (BUILD_PLAN S02-AC5).

**Canonical JSONL, as the Scopus writer does.** Rejected for the reason in Decision 2. The payload
would no longer be the response, and the byte-for-byte claim would be weaker than the one the
spec asks for.

**One run per record.** Rejected. It multiplies requests and manifests for no gain. A run per
acquisition, with batches inside it, matches how the client already batches.

## Consequences

1. OpenAlex data becomes reproducible from recorded bytes, with a retrieval date per response.
2. The client's capture path changes. It writes Layer 0 before it returns anything, does not use the
   cache, and stores refusals. The current `_get_works` cache store is bypassed for capture.
3. `layout.NON_RUN_DIRNAMES` gains a name, and the Scopus scans keep their behaviour.
4. A store built from OpenAlex runs needs a rebuild to show any OpenAlex value, which is the rebuild
   the spec already requires. A pre-change store has no OpenAlex rows, and the build report says
   so, as ADR 0030 Decision 4 does for topics.
5. Capture spends OpenAlex requests on every rerun. This is the cost of not caching.
6. A refusal in an opt-in run is data and stays in the store, so the methods must count it. ADR
   0021's attribution rule is the reference for how it is reported.
7. A PayloadRef addressed by work id is a new form. Anything that reads `payload_line` must check
   the run kind first.

## Verification the implementation must include

Each test must be shown to fail on the defect it guards, following the project rule that a test
which covers a line proves nothing until it fails on the original defect.

1. **Rebuild from Layer 0.** Build a project with one OpenAlex run, delete `corpus.duckdb`, and run
   `build_store(project, rebuild=True)`. The table checksums and the OpenAlex provenance rows must
   be identical. The abstract text must equal `rebuild_abstract` applied to the stored
   `abstract_inverted_index`. The test fails if the loader reads client return values rather than
   the stored bodies.
2. **No key in any stored file.** Run a capture with a sentinel key, through a fake HTTP client
   that returns a 200, a 401, and a 404. Scan every file under `raw/openalex/<run_id>/`, the
   captured log output, and the cache directory for the sentinel. The test must fail on a naive
   implementation that writes the wire params into the manifest.
3. **A rerun does not overwrite.** Seal a run, then run the same plan again. Assert that the second
   run has a new `run_id`, that the first run's files and manifest bytes are unchanged (hashed before
   and after), and that a write aimed at the sealed directory raises `SealedRunError`.

Further checks the implementation needs, lighter than the three above:

4. A refusal outside an opt-in run raises `AuthError` or `EntitlementError`, leaves no manifest,
   and stores nothing sealed.
5. A resumed run with an identical plan does not re-request a response already on disk.
6. `raw/openalex/` is in `NON_RUN_DIRNAMES`, and a Scopus scan of a project that contains an
   OpenAlex run finds only the Scopus runs.
7. The suite runs with `pytest -n auto` and in a single process, and the rebuild test runs on Linux
   and Windows.

## Open questions for the owner

Each question must be answered before this record is accepted.

1. **Own ADR or ADR 0028 amendment?** ADR 0030 Decision 6 leaves open whether the OpenAlex capture is
   specified in its own record or as an amendment to ADR 0028. This record is written as the own
   record. Confirm.
2. **`PayloadRef` form.** Decision 2 addresses an OpenAlex work by its id, in a verbatim body. The
   alternative is a line-based form, which needs canonical JSONL and gives up verbatim bytes. Which
   does the owner choose?
3. **A body that contains the key.** Decision 6 refuses the write and leaves the run unsealed. The
   alternative is to store the body and fail the run. Which?
4. **Header or log suppression.** Does OpenAlex accept the key in a request header? If it does, the
   query-string leak disappears. This record makes no live call to check it.
5. **Quota.** Capture never uses the cache, so reruns spend requests. Does the owner accept that?
6. **Which value wins.** Decision 7 takes the latest sealed value per field. Does the owner prefer the
   first value, or the latest with all earlier values listed in provenance?
7. **`absent` in the manifest.** Decision 5 keeps `absent` out of the manifest, because it is derived
   from the body. Does the owner accept that reading of "value, absent, not_found, refused"?
8. **Spec wording.** The §2.2, §5 and §2.6 clauses above are quoted from this repository, not from
   BUILD_PLAN.md. Does the owner confirm they match the frozen spec?

## Related decisions

- [ADR 0011](0011-abstract-retrieval-for-subject-areas.md): the Layer 0 run kind nested under `raw/`
  and skipped by name, which Decisions 1 and 8 follow.
- [ADR 0012](0012-persisting-skipped-layer0-entries.md): a skipped Layer 0 entry is a row in Layer 1,
  which Decision 8 applies to malformed bodies.
- [ADR 0021](0021-entitlement-refusals-are-attributed-to-a-publisher.md): how refusals are attributed,
  which Consequence 6 follows.
- [ADR 0028](0028-field-level-fallback-cascade.md): the cascade whose Decision 3 this record
  implements, and whose resolved items 1, 4, 7 and the title rule of Decision 4 it applies.
- ADR 0030, the OpenAlex topics keyword kind. Its Decision 6 points here. ADR 0030 is on branch
  `docs/adr-openalex-topic-keyword` and is not yet on `main`, so it is not linked.
- ADR 0029, reviewer registration of a candidate. It is on branch
  `docs/adr-candidate-registration-stage2` and is not yet on `main`. It does not affect this record.
