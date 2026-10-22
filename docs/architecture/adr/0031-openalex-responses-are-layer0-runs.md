# ADR 0031: OpenAlex Responses Are Captured as Layer 0 Runs

## Status

Accepted — 2026-10-21. Accepted by the owner; all open questions were decided as recorded.

Adds a Layer 0 run kind for OpenAlex responses. It is the capture that
[ADR 0028](0028-field-level-fallback-cascade.md) Decision 3 requires, and ADR 0030 Decision 6 names
this record as the place that specifies it. The record changes the Layer 0 layout and the `PayloadRef`
form, and the owner approved each change as a deviation in the section "Deviations from the frozen
contract". The owner's decisions are recorded under "Owner decisions", and the quote check is recorded
under "Verification record".

> **Note added 2026-10-22 (ADR 0032; this note does not change the record above).** The owner approved adding `type`, `language`, `primary_location` and `primary_topic` to the `select` list named in "What the client asks for today" (Context, line 44). The manifest's `api_view` field (Decision 3) records the select actually sent, so each body captured after this note carries the four new fields. Bodies captured before it do not, and the loader refuses a registration that references one ([ADR 0032](0032-registered-records-table-and-classification-map.md), Decision 2).

## Context

### The spec clauses this record relies on

BUILD_PLAN.md is not published in this repository (see [provenance.md](../provenance.md)). The clauses
below were checked against the frozen file and the cited ADRs (Verification record). Clauses marked
*as quoted in* are quoted from the ADR or code that cites them.

- **§2.2, lines 101–102.** "Every HTTP response is persisted verbatim as JSONL before any parsing."
- **§2.2, Layer 1 (BUILD_PLAN.md line 105).** "Layer 1 must be reconstructible from Layer 0 by running
  one function." (as quoted in [ADR 0011](0011-abstract-retrieval-for-subject-areas.md), Context.)
- **§2.2, Layer 0 immutability.** Paraphrased: a sealed run directory is never written again. The
  rule is enforced in `src/prismabib/capture/layout.py` (`guard_writable`).
- **Stage 1, line 696.** The `PayloadRef` is "Layer 0 file + line offset". (as cited in
  `src/prismabib/capture/writer.py`.)
- **Stage 3, line 856.** The schema carries `payload_file` and `payload_line` for each record.
  (as cited in `src/prismabib/capture/writer.py`.)
- **§5 risk 2.** "never re-fetch what Layer 0 already holds" (as quoted in ADR 0011).
- **§5 risk 1.** "Fail loudly, never degrade" (as quoted in ADR 0028).
- **§2.6, line 301.** "Any later deviation from §1.2 requires a new ADR that supersedes the relevant
  one." (Frozen wording, BUILD_PLAN.md line 301.) The deviations below touch §2.2, Stage 1 and Stage 3,
  not §1.2. Under §2.6, this record is the ADR for them (Owner decisions).

### What the client asks for today

`src/prismabib/sources/openalex.py` has these properties, which the capture must reproduce:

- The endpoint is `https://api.openalex.org/works`.
- The `select` list is `id,doi,title,publication_year,abstract_inverted_index,authorships,topics`.
  Every stored body therefore holds only those fields. The `topics` entries carry each topic's
  `score`, so the score is in the stored body.
- DOIs are looked up 50 per request with `filter=doi:a|b|...`. A DOI containing `|` or `,` is looked
  up singly by path, `/works/doi:<doi>`, and a whole-batch 404 falls back to single lookups.
- Title lookups use `search=` with `filter=publication_year:<year>` and `per-page=25`. The exact
  normalised-title match happens in the client, in `search_title`.
- The API key is sent as the `api_key` query parameter, and it is left out of the cache key.
- A 401 or 403 raises `AuthError` or `EntitlementError`, except in an opt-in run, where it returns
  `Refused(status)`. A 404 returns `NotFound`.

Three things the client does not do yet, and which this record requires: it persists nothing under
`raw/`; it stores bodies only in `raw/_cache/`, which is disposable; and `Refused` results are not
stored.

### Why Layer 0 and not the cache

[ADR 0011](0011-abstract-retrieval-for-subject-areas.md) Context rejects `raw/_cache/` for the same
reasons here. The cache is gitignored and disposable, so a `build_store(rebuild=True)` after a
cleanup would have to re-spend OpenAlex requests, or would silently lose the OpenAlex data. OpenAlex
also changes over time, so an OpenAlex value is reproducible only if its retrieval date and bytes
are recorded when it is fetched.

## Decision

### 1. One run per acquisition, under `raw/openalex/<run_id>/`

```text
raw/openalex/<run_id>/
├── response-0000.jsonl   # HTTP response 0: one line, the body bytes, then a newline
├── response-0001.jsonl   # ... one file per HTTP response, in request order
├── progress.json         # resumption sidecar: the plan, next index, any refusal; deleted on seal; never hashed
└── manifest.json         # the seal: OpenAlexRunManifest
```

- The run id comes from `layout.new_run_id()`. The seal is the presence of `manifest.json`, read
  through `layout.is_sealed`. Every write goes through `guard_writable`, and every file is written
  through `atomic_write_bytes`.
- A call resumes an unsealed run only when its request plan is identical: the same identifiers in
  the same order, and the same `fallback_opt_in`. A run that holds a refusal is never resumed
  (Decision 5). A sealed run is never resumed.
- The run is not a row in `runs`. OpenAlex identifies no records, and `runs.total_results` is the
  only source of the identified count (BUILD_PLAN S02-AC5). `OpenAlexRunManifest` has no
  `total_results` field, as `AbstractRunManifest` has none.
- The directory name is `openalex`. Nothing else lives under `raw/` with that name.

### 2. Each HTTP response is one JSONL line, verbatim where it can be

Each HTTP response, including a refusal, a 404, and a batch 404 that is later split into single
lookups, is written as one file, `response-NNNN.jsonl`, containing exactly one line.

- **Verbatim.** When the body contains no `\n` or `\r` byte, the line is the body bytes exactly as
  received, followed by `\n`. The manifest records `encoding: "verbatim"`.
- **Compact fallback.** When the body contains a line break, the writer parses it and writes it
  as compact JSON with key order kept (`json.dumps(..., ensure_ascii=False, separators=(",", ":"))`).
  The manifest records `encoding: "compact"` and `raw_sha256`, the digest of the body as received. This
  is a deviation from the frozen wording (Deviations, item 2). The owner decided that it is an allowed
  fallback, recorded per response. Layer 1 still rebuilds from it (Owner decisions).
- `sha256` in the manifest is the digest of the stored file's bytes. `payload_sha256` is the digest of
  the stored files' bytes concatenated in request order, as `writer._payload_sha256` does.

One response per file, rather than many lines in one file, because the file is the unit of an
atomic write: a resumed run never rewrites a line that is already on disk.

**`PayloadRef`.** A work is addressed by its file, its line, and its OpenAlex id. The line is always
`0`, because each file holds one response, and the id is the `id` of the work in `results`. The
spec's `PayloadRef` is "file + line offset" (Stage 1, line 696), and Stage 3, line 856 defines the
`payload_file` and `payload_line` columns. The `openalex_id` is an extra address component, so this
is listed as a deviation (Deviations, item 4).

### 3. The manifest: `OpenAlexRunManifest`

`OpenAlexRunManifest` is a new model in `src/prismabib/capture/manifest.py`. It does not reuse
`RunManifest`, which the scanner in `store/load.py` would parse as a Scopus search run. This departs from
ADR 0028 Decision 3 (Deviations, item 6).

| Field | Content |
|---|---|
| `run_id`, `started_at`, `finished_at` | UTC. `started_at` dates the first attempt, so it survives resumption. |
| `endpoint` | `https://api.openalex.org/works`. The base URL only, never a URL with a query string. |
| `fallback_opt_in` | Boolean. The opt-in flag of ADR 0028 resolved item 4. |
| `api_key_used` | Boolean. Whether a key was sent. Never its value. |
| `refresh` | Boolean, default `false`. `true` only when the owner asks for identifiers already held to be requested again (Decision 7). |
| `requests` | One `OpenAlexRequest` per HTTP request, in request order. |
| `payload_files`, `payload_sha256` | As in Decision 2. |
| `client_version`, `criteria_version` | As in the other manifests. |

Each `OpenAlexRequest` holds:

- `index`, `file`, and `encoding`, with `raw_sha256` when `encoding` is `"compact"`.
- `lookup`: one of `doi_batch`, `doi_single`, or `title_search`.
- `identifiers`: the batch this request was asked for. For DOIs, the normalised lowercase DOIs. For
  titles, a list of `{title, year}`.
- `params`: the query parameters as sent, with `api_key` removed. For `doi_single` the DOI is in the
  path, so `params` holds only `select`.
- `api_view`: the `select` string. This is the OpenAlex form of the frozen §2.2 "API view" field
  (Deviations, item 3). The frozen "endpoint" and "timestamp" fields map to the manifest's `endpoint`,
  `retrieved_at`, `started_at` and `finished_at`.
- `entitlement_context`: `{api_key_used, fallback_opt_in, refusal_status}`, where `refusal_status` is
  401, 403, or `null`. This is the OpenAlex form of the frozen "entitlement context" field.
- `http_status`, `retrieved_at` (UTC, the time of this request), `result_count`, the number of works in
  `results`, or `null` when the body has no results list. `result_count` is the OpenAlex form of the
  frozen "result count" field.
- `outcomes`: one entry per identifier (Decision 4).

Retrieval date: `retrieved_at` is the real request time. The HTTP cache is not consulted by the
capture path (Decision 7), so no request is answered from a file written by an earlier run.

### 4. Attempt outcomes are stored, and absent is stored too

Each identifier in a request gets one outcome entry in the manifest:

```text
{identifier, outcome, http_status, openalex_id, absent_fields}
```

- `outcome` is one of:
  - `value`: the work was found. For a DOI, the work has that DOI. For a title, the work has the same
    year and the same normalised title (ADR 0028 Decision 4). `openalex_id` is set.
  - `not_found`: the DOI is absent from a 200 batch response, or a `doi_single` lookup returned 404,
    or a title search has no exact match. A near miss is not a `value`, and it goes to the review
    list in Layer 1 (ADR 0028 Decision 4). `openalex_id` is `null`.
  - `refused`: the response was 401 or 403, and `http_status` carries that status. Stored only in an
    opt-in run (Decision 5).
- `absent_fields` lists the fields of a `value` work that are empty: any of `abstract`, `authors`,
  `affiliations`, `keywords`. It is `[]` for every other outcome.

**Decision: `not_found` and `absent` are stored, not derived from the body.** Both are computed once,
at write time, by the same parsing functions the loader uses (`openalex.field_outcomes`,
`authorships_to_fields`, `rebuild_abstract`, `topics_to_keywords`). The manifest then holds the
complete outcome set, so a reader can see which identifiers were attempted and what each returned
without parsing the bodies. A test recomputes both from the stored bytes and fails if they differ
from the manifest (Verification 1). Deriving them only in Layer 1 was the alternative. It was rejected
because it made `not_found` derived and `absent` stored or vice versa, and a refusal's body may be
empty, so the manifest needs its own record of the outcome.

Layer 1 field provenance (ADR 0028 Decision 1) takes each field's outcome from these entries. The
four field outcomes are `value` (the work was found and the field is not in `absent_fields`),
`absent`, `not_found`, and `refused`.

### 5. Refusals: the file is written first, and a refused run never seals

**Outside an opt-in run (`fallback_opt_in=false`).** When a response is 401 or 403:

1. The writer persists the response file, `response-NNNN.jsonl`, as for any response.
2. It records the refusal in `progress.json` as `refusal: {index, status}`.
3. It raises `AuthError` or `EntitlementError`, and no `manifest.json` is written.
4. The run stays unsealed, and it is a dead end. It is never resumed, because a run with a refusal
   is not a resume candidate. A later call starts a new run, with a new `run_id`. The refusal file
   stays in the dead run as evidence and is never loaded, since the run is not sealed.

A run with `fallback_opt_in=false` can therefore never contain a sealed `refused` outcome. The
sealing step checks this too: when `fallback_opt_in` is false, it refuses to write a manifest if any
response is not 200 or 404. Only that case is refused, and an opt-in refusal is exempt from it.

**Inside an opt-in run (`fallback_opt_in=true`).** The same file is written, the identifiers are
recorded as `refused` with `http_status`, and the run continues. An opt-in refusal may seal: the run
seals, and the manifest records `fallback_opt_in: true`, each refusal's `refusal_status`, and a
`refused` outcome for each affected identifier.

### 6. The API key rule

The key is `Settings.openalex_api_key`. It is never written to:

1. **A payload.** The stored bytes are the response body. A body does not normally contain the key.
   If one does, the writer refuses to write that file, the body is never stored, the run stays unsealed,
   and the error names the file without the key. This is the owner's decision (Owner decisions).
2. **A manifest.** `params` excludes `api_key`. `api_key_used` is a boolean.
3. **A log.** The client logs the endpoint and never the query string. The key is also registered with
   `register_secret_for_redaction`, as `sources/scopus.py` does. `openalex.py` does not do this today.
   The implementation must add it.
4. **A cache key.** The client already passes `params` without the key to `HttpCache`. Capture no
   longer uses the cache (Decision 7), so this holds by construction. A test pins it.

**The key travels in a request header.** The owner decided that OpenAlex receives the API key in a
request header, not in the query string. The header name and value format are an implementation check:
they are confirmed against the OpenAlex documentation before the implementation is written. This is not
a further owner decision, and this record does not state the header name. The client never puts the key
in `params`, so it appears in neither the URL nor the `HttpCache` key. The current client sends it as the
`api_key` query parameter, and the implementation must change that.

**The httpx leak.** httpx logs every request at INFO on the logger `httpx`, as `HTTP Request: GET <url>`.
With the header form the URL carries no key. The query-string form is kept as a regression check, so a
future change that moves the key back into the URL is caught. `register_secret_for_redaction` runs as a
structlog processor, and `main` has no bridge from stdlib logging to structlog, so that processor never
sees an httpx record. This record does not rely on it for httpx.

As defence in depth, `openalex.py` attaches a `logging.Filter` to the `httpx` logger once, when the client
is first constructed. The filter replaces the raw key and its percent-encoded form
(`quote_plus(key, safe="")`, because httpx encodes spaces as `+`) in each record's formatted message with
`***`, then clears `record.args`. A filter on a logger applies to records logged directly on that logger,
which is where httpx emits. The filter is process-wide and holds the set of keys that clients have
registered.

**Tests.** Both use the real `httpx.Client` with `httpx.MockTransport`, so no network call is made. Each
sets `caplog` to INFO on the `httpx` logger, runs `OpenAlexClient.lookup_dois` with a sentinel key, and
asserts that `"HTTP Request"` appears in the captured text (so the test is not vacuous) and that neither
the sentinel nor its percent-encoded form does.

- `test_httpx_info_log__header_form__never_contains_the_key`: the key is sent in the header. This is the
  shipped path.
- `test_httpx_info_log__query_form__regression__never_contains_the_key`: the key is sent in the query
  string, with the filter in place. The test must fail with the filter removed.

### 7. Reuse, reruns, and the retrieval date

**Reuse rule (complies with §5 risk 2).** Before it plans a run, the capture subtracts the identifiers
that a sealed OpenAlex run already holds. An identifier is held when a sealed run has an outcome of
`value` or `not_found` for it. A `refused` outcome does not hold it, so a later opt-in run requests it
again. Identifiers are keyed by the normalised DOI, or by `(normalised title, year)`. A new run requests
only the identifiers that remain. When nothing remains, no run is created and no request is made.

**Refresh (a deviation from §5 risk 2).** `refresh=true` requests the held identifiers again. This is
the only way Layer 0 is re-fetched, and it is a deviation from §5 risk 2, so it is listed for the owner
(Deviations, item 5). It is off by default, and the manifest records it as `refresh`. A refresh is a
new run with its own `run_id`, `retrieved_at` values, and `payload_sha256`. A sealed run is never
overwritten.

**Rebuild never requests.** `build_store(rebuild=True)` reads sealed runs only. It makes no HTTP
request, so the rebuild does not spend the quota that ADR 0011 rejected.

**Layer 1 value choice (the owner's decision).** Per identifier and field, Layer 1 takes the value from the
latest sealed run whose outcome for that field is a `value`. A newer outcome of `absent` or `not_found`
does not replace an older value. Older runs stay on disk as the audit trail. Where no sealed run holds a
value for a field, Layer 1 takes the latest outcome, `absent` or `not_found`. Run ids sort by time, so
"latest" is the latest run id. The supplying run and its `retrieved_at` go into the field's provenance.
Under the reuse rule, two sealed runs disagree only after a refresh, so the rule matters only then.

**Reproducibility** covers the recorded payloads only (ADR 0028 resolved item 7). A methods text cites
the run ids and retrieval dates it used.

### 8. How the loader recognises the run

- **`NON_RUN_DIRNAMES`:** add the name `openalex` to `layout.NON_RUN_DIRNAMES`, with a constant
  `OPENALEX_DIRNAME = "openalex"` beside `ABSTRACTS_DIRNAME`. Today `raw/openalex/` is invisible to the
  Scopus scans only because it has no `manifest.json` directly inside it. ADR 0011 named that as an
  accident and rejected it for `raw/abstracts/`. Naming it makes the rule explicit.
- **Discovery:** `store/load.py` gains `_sealed_openalex_run_dirs(raw_dir)`. It lists the sealed
  children of `raw/openalex/`, as `_sealed_abstract_run_dirs` does for `raw/abstracts/`.
- **Fold-in order:** after the search runs, because attachment needs `seen_record_ids`, and before
  `_finalise_subject_areas`. Attachment follows ADR 0028 Decision 4: a DOI equal to the record's DOI,
  or else an exact normalised title with the same year. No record is created from an OpenAlex body.
- **Malformed bodies:** a body that is not the documented shape is recorded as a malformed entry
  (ADR 0012), not skipped silently.
- **Outcome check:** the loader recomputes each outcome from the stored bytes and fails loudly if the
  manifest disagrees (Decision 4).

### 9. What the loader stores, and the topic score

**Stored in Layer 0:** the response bytes, and the manifest. The OpenAlex topic `score` is in the stored
body, because `select` includes `topics`. The score therefore stays in Layer 0 only.

**Not stored as source of truth:** rebuilt abstract text, author lists, affiliation lists, topic names,
and title matches. These are Layer 1 values, computed from the stored bodies by one loader path. The
abstract is rebuilt by `openalex.rebuild_abstract` from `abstract_inverted_index`. The authors and
affiliations come from `authorships_to_fields`, and the topics from `topics_to_keywords`.

**Topics carry no score in Layer 1.** `record_keywords` has no score column, and none is added (ADR 0030
Decision 1). A topic score is read from the stored body if an analysis ever needs it, and that is a
Layer 0 read, not a Layer 1 column.

**Layer 1 reads Layer 0 only.** The loader is the only writer of Layer 1 (ADR 0028 Decision 3). The
client's return values are never persisted and never read by the loader.

## Deviations from the frozen contract

Each item is a change to the frozen contract, listed for owner approval under §2.6. The owner approved
items 1 to 6 as written. Item 6 was approved as written by the owner.

1. **A new Layer 0 run kind**, `raw/openalex/<run_id>/`, with its own manifest schema and a
   `NON_RUN_DIRNAMES` entry. The frozen Layer 0 layout has no OpenAlex run.
2. **The JSONL form.** The frozen §2.2 wording is "Every HTTP response is persisted verbatim as JSONL
   before any parsing." This record writes one response per file, with exactly one JSONL line per file,
   and keeps the bytes verbatim where it can. A body with a line break is written in compact form, and
   the manifest records the encoding and `raw_sha256` (Decision 2; decided by the owner).
3. **The §2.2 manifest fields.** The frozen wording names "API view, endpoint, timestamp, entitlement
   context, result count". This record maps them to OpenAlex as `api_view` (the `select` string),
   `endpoint` (the manifest's base URL), `timestamp` (`retrieved_at` per request, with `started_at` and
   `finished_at` per run), `entitlement_context` (`api_key_used`, `fallback_opt_in`, `refusal_status`),
   and `result_count` (the works in `results`). The owner approved the mapping.
4. **The `PayloadRef` form.** Stage 1, line 696 says "file + line offset", and Stage 3, line 856 defines
   `payload_file` and `payload_line`. An OpenAlex work is addressed by file, line `0`, and `openalex_id`
   (Decision 2).
5. **A refresh re-requests held identifiers.** §5 risk 2 says "never re-fetch what Layer 0 already
   holds". `refresh=true` re-fetches them, so it is off by default and needs owner approval (Decision 7).
6. **The manifest model, the run layout, and the opt-in record differ from ADR 0028 Decision 3.** ADR 0028
   Decision 3 says each attempt is written "with the run manifest and checksum pattern of `capture/writer.py`".
   ADR 0028 resolved item 4 says the fallback opt-in is "recorded in `RunManifest`". This record uses a
   separate `OpenAlexRunManifest` (Decision 3), one response per file (Decision 2), and records
   `fallback_opt_in` in that manifest. It keeps the checksum pattern: a SHA-256 over the stored bytes, the
   seal is `manifest.json`, and the resumption sidecar is deleted on seal. Reason: `RunManifest` is frozen
   by the plan, its `total_results` is the only sanctioned identified count (BUILD_PLAN S02-AC5), and
   reusing it would make the scanner parse OpenAlex runs as Scopus search runs (Alternatives). Owner
   approval was given as written (Owner decisions).

## Alternatives considered

**Write OpenAlex data straight into Layer 1.** Rejected. Layer 1 would hold data that exists nowhere in
Layer 0. A rebuild would lose it, or would have to re-spend requests, which is the failure ADR 0011
rejected for the same reason. It also breaks the rule that the loader is the only Layer 1 writer
(ADR 0028 Decision 3).

**Cache OpenAlex responses only in `raw/_cache/`.** Rejected. The cache is gitignored and disposable, and
it is keyed on the request, not on a run. It has no manifest, no seal, and no retrieval date per response.
A cold cache silently changes the corpus, and a reader cannot tell which OpenAlex data a methods text used.

**Reuse `RunManifest` and the `raw/<run_id>/` layout.** Rejected. The scanner would parse OpenAlex runs as
Scopus search runs, and `RunManifest.total_results` would be a second candidate for the identified count
(BUILD_PLAN S02-AC5).

**One JSONL file with many response lines.** Rejected. A resumed run would have to rewrite the file, which
the atomic-write rule does not allow. One response per file keeps each write atomic.

**One run per record.** Rejected. It multiplies requests and manifests for no gain. A run per acquisition,
with batches inside it, matches how the client already batches.

**Derive `not_found` and `absent` only in Layer 1.** Rejected for the reason in Decision 4. It would leave
the manifest unable to say what was attempted without parsing bodies.

## Consequences

1. OpenAlex data becomes reproducible from recorded bytes, with a retrieval date per response.
2. The client's capture path changes. It writes Layer 0 before it returns anything, does not use the cache,
   and stores refusals. The current cache store in `_get_works` is bypassed for capture.
3. `layout.NON_RUN_DIRNAMES` gains a name. The Scopus scans keep their behaviour.
4. A store built from OpenAlex runs needs a rebuild to show any OpenAlex value, which the spec already
   requires. A pre-change store has no OpenAlex rows, and the build report says so, as ADR 0030 Decision 4
   does for topics.
5. A non-refresh capture spends OpenAlex requests only for identifiers that no sealed run holds. A refresh
   spends them again, by owner choice.
6. A refusal in an opt-in run is data and stays in the store, so the methods must count it. ADR 0021's
   attribution rule is the reference for how it is reported.
7. A `PayloadRef` addressed by work id is a new form. Anything that reads `payload_line` must check the
   run kind first.
8. The httpx filter is process-wide. A test must cover the case where two clients hold different keys.

## Verification the implementation must include

Each test must be shown to fail on the defect it guards, following the project rule that a test which
covers a line proves nothing until it fails on the original defect.

1. **Rebuild from Layer 0.** Build a project with one OpenAlex run, delete `corpus.duckdb`, and run
   `build_store(project, rebuild=True)`. The table checksums and the OpenAlex provenance rows must be
   identical. The abstract text must equal `rebuild_abstract` applied to the stored
   `abstract_inverted_index`. The manifest's outcomes, including `not_found` and `absent_fields`, must
   equal those recomputed from the stored bytes. The test fails if the loader reads client return values,
   or if the manifest and bytes disagree. A second project holds a body with a line break, stored in
   compact form, and its rebuild must match its first build (Decision 2).
2. **No key in any stored file.** Run a capture with a sentinel key, through a fake HTTP client that
   returns a 200, a 401, and a 404. Scan every file under `raw/openalex/<run_id>/`, the captured log
   output, and the cache directory for the sentinel and its percent-encoded form. The test must fail on
   a naive implementation that writes the wire params into the manifest.
3. **The httpx log never contains the key.** `test_httpx_info_log__header_form__never_contains_the_key` and
   `test_httpx_info_log__query_form__regression__never_contains_the_key`, as described in Decision 6. The
   query-form test must fail with the filter removed.
4. **A rerun does not overwrite, and reuse requests nothing held.** Seal a run. Run the same plan again.
   Assert that the second call creates no run and makes no request, because every identifier is held.
   Then assert that a run with a new identifier gets a new `run_id`, that the first run's files and
   manifest bytes are unchanged (hashed before and after), and that a write aimed at the sealed directory
   raises `SealedRunError`. A `refresh=true` call makes a new run with its own `retrieved_at` values.
5. **A refusal without opt-in.** `test_refusal_without_opt_in__writes_response_raises_and_never_seals`:
   a fake 401 response makes the call raise `AuthError`. The response file exists, `progress.json` holds the
   refusal, and no `manifest.json` exists.
6. **A resume does not seal a refusal.** `test_resume_of_refused_run__starts_new_run_and_never_seals_it`:
   after case 5, a second call with `fallback_opt_in=false` and the same plan starts a new run. The dead run
   gets no new request and no manifest. A sealing attempt on a run with `fallback_opt_in=false` that holds
   a non-200/404 response is refused. A run with `fallback_opt_in=true` that holds a 401 or 403 seals, and
   each affected identifier is recorded as `refused`. That case is a positive test: it seals and records
   the refusal outcome.
7. A resumed run with an identical plan does not re-request a response already on disk.
8. `raw/openalex/` is in `NON_RUN_DIRNAMES`, and a Scopus scan of a project that contains an OpenAlex run
   finds only the Scopus runs.
9. The suite runs with `pytest -n auto` and in a single process, and the rebuild test runs on Linux and
   Windows.
10. **A newer `not_found` does not erase an older value.** Seal run A with a `value` for an identifier, then
    seal run B (a refresh) with `not_found` for it. The Layer 1 value must still come from run A, and run B's
    `not_found` is recorded in provenance. The test must fail if the loader takes the latest outcome
    regardless of its kind. The same holds for a newer `absent`.

## Owner decisions

### Decided

Each decision below is the owner's, or is recorded in an earlier ADR as shown.

- **Deviations 1 to 6 approved as written.** The owner approved the new run kind, the JSONL form, the §2.2
  field mapping (`api_view`, `entitlement_context`, `result_count`), the `PayloadRef` form, `refresh`, and
  the manifest model and opt-in record that depart from ADR 0028 Decision 3 (deviation 6).
- **A body that contains the API key is refused.** The writer refuses the write, the body is never
  stored, the run stays unsealed, and the error names the file without the key (Decision 6).
- **Refresh is off by default** (Decision 7, Deviations item 5).
- **Per identifier and field, Layer 1 takes the value from the latest sealed run whose outcome for that field
  is a `value`.** A newer outcome of `absent` or `not_found` does not replace an older value. Older runs stay
  on disk as the audit trail. Where no sealed run holds a value for a field, Layer 1 takes the latest outcome,
  `absent` or `not_found` (Decision 7).
- **The OpenAlex capture is specified in this record, ADR 0031.** Decided by ADR 0030 Decision 6, which
  is Accepted and on main, and names this record.
- **`not_found` and `absent` are stored in the manifest, computed at write time** (Decision 4). The
  manifest needs its own record of each outcome, because a refusal's body may be empty, and a reader
  must see what was attempted without parsing bodies. Deriving them only in Layer 1 was rejected for
  that reason.
- **§2.6 applies to the six deviations, and this ADR is the record.** The six items in "Deviations
  from the frozen contract" are the deviations it covers.
- **No topic score is stored in Layer 1.** Decided by ADR 0030 Decision 1. The score stays in the stored body.
- **The API key is sent in a request header, not the query string** (Decision 6). The owner decided this.
  The header name and value format are an implementation check, confirmed against the OpenAlex
  documentation before the implementation is written.
- **The compact encoding is an allowed fallback** for response bodies that contain line breaks,
  recorded per response in `encoding` and `raw_sha256`. Layer 1 still rebuilds from the stored bytes
  (Decision 2, Verification 1).

### Verification record

The quotes and citations in this record were checked against BUILD_PLAN.md and the cited ADRs at commit
bf5b19f. The checked items are:

- §2.2, lines 101–102, "Every HTTP response is persisted verbatim as JSONL before any parsing."
- §2.2, line 105, "Layer 1 must be reconstructible from Layer 0 by running one function."
- Stage 1, line 696, `PayloadRef` "Layer 0 file + line offset".
- Stage 3, line 856, `payload_file` and `payload_line`.
- The ADR 0011 quotes in this record.
- The ADR 0028 quotes in this record.
- The §5 quotes, risks 1 and 2.
- §2.6, line 301, "Any later deviation from §1.2 requires a new ADR that supersedes the relevant one."

The ADR 0028 line 192 error is a separate correction, and it is not part of this ADR.

## Related decisions

- [ADR 0011](0011-abstract-retrieval-for-subject-areas.md): the Layer 0 run kind nested under `raw/` and
  skipped by name, which Decisions 1 and 8 follow. Also the reason the cache is not a Layer 0 store.
- [ADR 0012](0012-persisting-skipped-layer0-entries.md): a skipped Layer 0 entry is a row in Layer 1, which
  Decision 8 applies to malformed bodies.
- [ADR 0021](0021-entitlement-refusals-are-attributed-to-a-publisher.md): how refusals are attributed,
  which Consequence 6 follows.
- [ADR 0028](0028-field-level-fallback-cascade.md): the cascade whose Decision 3 this record implements, and
  whose resolved items 1, 4 and 7, and the title rule of Decision 4, it applies.
- [ADR 0030](0030-openalex-topics-as-a-keyword-kind.md): the OpenAlex topics keyword kind. It is Accepted
  and on `main`. Its Decision 1 says no score is stored in Layer 1, and its Decision 6 names this record.
- ADR 0029, reviewer registration of a candidate. It is on branch `docs/adr-candidate-registration-stage2`,
  not yet on `main`. It does not affect this record.
