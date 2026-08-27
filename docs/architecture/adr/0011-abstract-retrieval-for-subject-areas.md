# ADR 0011: Subject Areas Come From a Separate Abstract Retrieval Run in Layer 0

## Status

Accepted — Phase 0b, 2026-08-27. Not a deviation from a frozen BUILD_PLAN contract: §Stage
2 fixes how a *search* is captured and §3.1 declares `criteria.yaml`'s `subject_areas`
field, but neither says where subject-area codes come from — because when the plan was
written it was assumed they came with the search response. They do not. What this ADR
records is where they come from instead, and why that answer is a second kind of Layer 0
run rather than any of the cheaper places it could have gone.

## Context

`criteria.yaml` has had a `subject_areas` filter since Stage 1, and nothing has ever been
able to apply it. `prisma/engine.py` refuses outright:

```python
raise ConfigError(
    f"{project.root / 'criteria.yaml'} restricts subject_areas to "
    f"{list(criteria.subject_areas)!r}, but not one of the {len(attributes)} records "
    "carries a subject area ..."
)
```

`store/load.py` documents its own `subject_area_links_loaded` as "always `0` for a store
built purely from Scopus Search API `view=COMPLETE` captures". Both of those were written
from the observed behaviour of the loader; neither established *why*, and "the Search API
does not return them" and "our parser does not read them" are very different defects with
the same symptom.

It is the first. Measured against a real 651-record corpus captured at `view=COMPLETE`:
**0 of 125 sampled entries carried a `subject-area` key.** The two committed
`view=COMPLETE` cassettes are 50 more real entries with the same result, and
`test_contract__search_complete_response__carries_no_subject_areas` now pins it. The
Search API simply does not carry the field, at any view this project is permitted to use
(`STANDARD` is refused outright by §5 risk 1, and it does not carry them either).

The codes live in the **Abstract Retrieval API** — `GET
/content/abstract/scopus_id/{id}`, one call per record — as
`abstracts-retrieval-response.subject-areas.subject-area[]`, each entry carrying `@code`
(the ASJC classification number the criteria list is matched against), `@abbrev` (the
four-letter top-level grouping) and `$` (the human-readable name).

Three facts constrain where those responses may be put.

**One call per record, against a weekly quota.** ~1,800 records for the working corpus. A
design that can re-spend that on a rebuild, a cleanup, or an interruption is not viable,
and the failure is invisible: quota is consumed silently and the artefacts look identical
either way.

**§2.2's reconstructibility rule.** "Layer 1 must be reconstructible from Layer 0 by
running one function." Whatever holds these responses has to still be there when
`build_store(rebuild=True)` runs six months later on a different machine.

**Layer 0 is immutable once sealed.** The records being enriched were captured by runs
that already carry a `manifest.json`. There is no writable place inside them and no
operation in this codebase un-seals one.

## Decision

**Fetch Abstract Retrieval responses as a new kind of Layer 0 run, written verbatim to
`raw/abstracts/<run_id>/`, sealed with its own `AbstractRunManifest`, and never recorded
as a row in the `runs` table.**

```text
raw/abstracts/<run_id>/
├── abstracts-0000.jsonl   # verbatim responses, one per line, 100 records per file
├── progress.json          # resumption sidecar; deleted on seal; never hashed
└── manifest.json          # the seal
```

`capture/enrich.py::capture_abstracts` drives it, mirroring `capture_search`'s structure
one-for-one: find-or-create a run directory, load or initialise the sidecar, iterate, write
a batch, persist progress, seal.

### Layer 0, not the HTTP cache

`raw/_cache/` already holds every response body verbatim, which makes it look like the
answer costs nothing. It is not, and the reason is written into the cache's own docstring:
it is gitignored, disposable, and keyed on `(url, params)` as a *quota* optimisation, not
as a record of what was captured. A `rebuild=True` after a routine cleanup would silently
produce a corpus with no subject areas in it — no error, no missing file, just a
`subject_areas` table that is empty for a reason nobody can reconstruct — or would
re-spend ~1,800 calls to avoid that. Neither is "reconstructible by running one function".

### Nested under `abstracts/`, not beside the search runs

A sibling `raw/<run_id>/` would be found by every scan that looks for search runs.
`store/load.py::_sealed_run_dirs` would read the directory's `manifest.json` as a
`RunManifest`, and `capture/writer.py::_find_resumable_run` would consider resuming it.
The loader's first act on the payload would be `_cover_date_from_entry`, which raises on a
missing `prism:coverDate` — a field an Abstract Retrieval response does not have at the top
level. Nesting puts the seals one level down, where neither scan looks.

That nesting alone would have been enough, which is precisely the problem: it would have
been enough *by accident*. Both scans now skip `abstracts/` **by name**, via a shared
`capture/layout.py::NON_RUN_DIRNAMES`, and `tests/unit/capture/test_layout.py` plants a
`manifest.json` directly at `raw/abstracts/manifest.json` so that the name check is the
only thing left holding. Deleting it fails a test instead of working anyway.

### Never a row in `runs`

S02-AC5 makes `runs.total_results` the only sanctioned source of the PRISMA "records
identified" count. An abstract run identifies nothing — it re-describes records some search
run already identified — so a row for it would need either a fabricated `total_results` or
a real one that double-counts those records into the identification number.
`AbstractRunManifest` therefore has no `total_results` field at all, so the mistake cannot
be made by filling in a field that was sitting there.

### The shared vocabulary moved first

`manifest.json` as the seal, `_cache` as a non-run directory, the sealed-write guard, the
atomic write, and the run-id format were private to `capture/writer.py`, and
`store/load.py` carried a hand-copied duplicate of `_CACHE_DIRNAME` with a comment pointing
at the original. Two constants that must stay equal, on the two sides of the Layer 0 /
Layer 1 boundary. They are now defined once in `capture/layout.py`; `writer.py` re-exports
`is_sealed` and `SealedRunError` so its public surface is unchanged, and a test asserts
they are the *same objects*, not merely same-named ones.

### `view=FULL`, never degraded

`view=META` is a cheaper entitlement and **does** carry subject areas. It is refused for
the same reason `view=STANDARD` is refused on the search side: a corpus whose areas came
from two different views is not one filter, and a partially degraded run is
indistinguishable after the fact from a clean one.

### The entitlement probe

Abstract Retrieval is a different entitlement from Search `view=COMPLETE`, and a key
entitled for one is commonly not entitled for the other. The failure is a flat 403 on
every record, so discovering it record by record costs a weekly quota to learn one fact. A
403 on the **first record attempted in an invocation** is therefore treated as a missing
entitlement: it re-raises after exactly one call, with a message that names Abstract
Retrieval and says explicitly that this is *not* the Search entitlement. A 403 on any
later record is a per-record embargo, recorded as `AbstractUnavailable(reason=
"not_entitled")`, and the run continues.

The cost is stated rather than hidden: if the first record in sorted order is itself
embargoed while the key is fine, the run refuses. Re-running with an explicit `record_ids`
list fixes that in one command; probing a second record to disambiguate would make "1,800
wasted calls" reachable again through a different door.

### `unavailable` is manifest metadata

A record with no subject-area codes in Layer 1 is ambiguous on its own: it could mean
"Scopus assigns this record none" or "we never asked about this record". Only the run's own
record of what it asked can tell those apart, and a `subject_areas` filter treats an
unevaluable record differently from an excluded one — so the distinction decides screening
outcomes. It is kept in `manifest.json`, not in a payload file, because it is a fact about
the run rather than about any upstream response, and the payload files are reserved for
bytes Scopus actually sent.

### Batching by position

Payload file `N` always covers `records[N*100:(N+1)*100]` of the sorted record list,
however many of those Scopus served and whatever happened to the run in between. A partial
batch is never written. That is what makes an interrupted-and-resumed run byte-identical to
an uninterrupted one — and therefore what makes `payload_sha256` a citable identifier of a
capture rather than a record of when someone pressed Ctrl-C. The price is that a resumed
run may re-request up to 99 records; those are cache hits when `raw/_cache/` is warm, and
one batch of quota when it is not.

## Alternatives rejected

### 1. Parse the subject areas and discard the responses

Call Abstract Retrieval, read `subject-areas` out of each response, write the codes
straight into the `subject_areas` table, and keep nothing else. No new Layer 0 run kind, no
manifest, no seal, no ~1,800 stored JSON documents — and the store ends up with exactly the
rows the feature needs.

Rejected because it breaks §2.2 outright. Layer 1 would hold data that exists nowhere in
Layer 0, so `build_store(rebuild=True)` — the operation the whole architecture is arranged
to make cheap and safe — would either drop every subject area or re-spend the full weekly
quota to rebuild them. The store would no longer be a pure function of `raw/`, which is the
single property that makes the numbers in the paper reproducible by a reader who has the
repository and the corpus.

It is also irreversibly lossy in a way that is easy to underestimate. A `view=FULL`
response carries funding data, the full affiliation tree, index terms, and the abstract
itself; a later stage that wants any of those has to re-capture against a Scopus index that
has since drifted, and cannot get the *same* answers back at all. Storing the response and
parsing later costs disk, which is free; parsing and discarding costs a capture, which is
not.

### 2. The Serial Title API, keyed on ISSN

`GET /content/serial/title?issn=...` returns `subject-area` entries for a *journal*. A
1,800-record corpus has far fewer distinct ISSNs — plausibly 300–450 — so this is roughly
4–6× fewer calls for what looks like the same information, and the results are cacheable
across projects because a journal's classification does not change per paper.

Rejected on coverage first. It is keyed on ISSN, and conference proceedings frequently have
none — the existing `complete-page-0000.json` cassette carries three proceedings-level
records for exactly this reason, and a bibliometric review of an engineering topic is
substantially conference literature. Those records would get no subject area at all, and
`_passes_subject_areas` treats "no data" as *not an automated exclusion*, so they would
silently pass a filter they were never evaluated against. A filter that quietly does not
apply to one publication venue type is worse than no filter: it looks applied.

It is also a different question. Serial Title answers "how is this journal classified",
Abstract Retrieval answers "how is this paper classified", and Scopus does not require them
to agree — an AI paper in a general engineering journal carries `1702` on the record and
not on the serial. Substituting one for the other changes what `subject_areas` in
`criteria.yaml` *means*, silently, which is a protocol change and would need its own
amendment rather than an implementation choice. And Serial Title carries its own
entitlement, so the cheaper path is not reliably available either.

### 3. `view=META` instead of `view=FULL`

`META` is the cheapest Abstract Retrieval view, is granted to more keys than `FULL`, and
— the point — **does** carry `subject-areas`. Every record this project needs a code for
would get one, at a lower entitlement bar and with fewer bytes stored.

Rejected, and it is the alternative that most deserved to be taken seriously, because it
would work. Two reasons it is still wrong.

The first is uniformity. A run that requests `FULL` and falls back to `META` on a 403
produces a corpus whose subject areas came from two different views, with nothing in the
data recording which record came from which. Whether the two views' `subject-areas` blocks
are identical for every record is not something this project can verify, and "probably the
same" is the exact shape of the claim §5 risk 1 exists to refuse — it is the same argument
that refuses `STANDARD` on the search side, where the fallback also *mostly* works.

The second is that `META` discards the rest of the response, which reintroduces
alternative 1's loss for every record it covers. If the entitlement genuinely is not
available, the honest outcome is the one the code now produces: a refusal that names
Abstract Retrieval, tells the operator what to ask their library for, and says that
`subject_areas: []` plus a recorded protocol limitation is the supported fallback. A
degraded corpus that nobody can identify as degraded is not a better outcome than a
documented limitation.

## Consequences

### 1. This change moves no published number, on purpose

No loader change, no engine change, no schema change, no fixture regeneration, no golden
update. `subject_areas` still loads zero rows; the engine still refuses a declared subject
filter with the same message. The PR that adds HTTP code changes no counts, and the PR that
changes counts adds no HTTP code — so if a number moves, exactly one change is a candidate.

### 2. A returned `AbstractRunManifest` is not proof of a sealed run

When `budget` stops an invocation short, `capture_abstracts` returns a manifest describing
what is durably on disk and writes no `manifest.json`. The absence of the file, not the
return value, means "unfinished". A caller that needs to know must compare
`records_fetched` against `records_requested`; this is the one place where the return type
is less informative than the filesystem, and it is a consequence of keeping the signature
free of a status flag.

### 3. A 404 is now a fact about the index, not a transient fault

`ScopusClient._raise_for_status` previously mapped every unexpected status, 404 included,
onto a *retryable* `UpstreamError`. A withdrawn record therefore consumed the whole retry
budget and then aborted the run. Over an 1,800-record enrichment that is close to certain
— Scopus withdraws and merges records, so an identifier captured in an earlier search run
can stop resolving later — and it would cost the operator hours of quota-bound progress
for something retrying cannot change.

`RecordNotFoundError` is now raised instead, and it is deliberately outside the retry set.
It lives in `sources/scopus.py` rather than `errors.py`, following the
`SealedRunError` precedent: §3.3 froze that taxonomy, and this is a condition only this
source can report. `capture_abstracts` records the record as
`AbstractUnavailable(reason="not_found")` and continues, which is what keeps the
three-way distinction a later reader needs — *Scopus has no such record* is neither *we
never asked* nor *Scopus assigns no subject areas*.

This does change `search()`'s behaviour: a 404 there is no longer retried either. That is
strictly better, since a search endpoint returning 404 is not a transient condition.

### 4. The Abstract Retrieval cassettes are modelled, not recorded

`tests/fixtures/cassettes/abstract-full-*.json` were built from Elsevier's documented
response shape and run through the real `sanitise_abstract`, not recorded from a live call
— the same status as `error-403-entitlement.json`, and it is written down in
`tests/fixtures/README.md`. The consequence is exact: those three contract tests pin *this
project's belief* about the response shape, and would not fail if Scopus changed it. The
one contract test here that does have live force is
`test_contract__search_complete_response__carries_no_subject_areas`, which runs against 50
real recorded `view=COMPLETE` entries. Replacing the modelled cassettes with a real
recording is outstanding work, and until it is done the shape claims rest on
documentation.

### 5. `sanitise_abstract` fails closed, and a real recording will hit that

A live `view=FULL` response carries an `item.bibrecord` subtree that repeats the abstract,
the author list and the affiliations in a different schema. `sanitise_abstract` raises
`UnsanitisedFieldError` on it by name rather than copying it through, so whoever records
the real cassette must teach the sanitiser that subtree first. That is deliberate friction:
on a public repository, a sanitiser that quietly passes through what it does not recognise
publishes licensed prose and reports success.

### 6. Enrichment runs are gitignored like every other Layer 0 payload

`projects/*/raw/` in `.gitignore` and `^projects/[^/]+/(raw|store|fulltext)/` in
`scripts/reject_licensed_content.sh` both match nested paths, so `raw/abstracts/` is
covered by the §2.5 guard with no change to either. Verified by the guard's own tests.

## Constraints

- **`RunManifest` is untouched.** Its `total_results` remains the only source of the PRISMA
  identification count, and no abstract run writes a `runs` row.
- **The view never degrades.** `FULL`, always, on every request, on every retry.
- **Payload lines are verbatim responses**, with no prismabib envelope around them. Identity
  is recoverable from `coredata.eid`, which is pinned by a contract test precisely because
  the no-envelope decision depends on it.
- **`progress.json` is never part of `payload_sha256`** and is deleted on seal.
- **A partial batch is never written**, so payload bytes do not depend on where a run was
  interrupted.
- **The rate limiter is constructed fresh per run.** Scopus quotas are per-API; an
  inherited bucket would throttle this run against a quota it does not spend.

## Related decisions

- **ADR 0001** (DuckDB as Analytical Store): Layer 1 is rebuilt from Layer 0 by one
  function — the property alternative 1 would have broken
- **ADR 0006** (Public Repository and Single-Owner Review): why a sanitiser that fails
  closed is the right default for anything derived from a licensed API

## References

- BUILD_PLAN §2.2 (Layer 0 immutability; Layer 1 reconstructible from Layer 0), §2.5
  (licensed content), §3.1 (`criteria.yaml`'s `subject_areas`), §Stage 2 lines 758–800 (the
  Scopus client contract), S02-AC5 (`total_results` as the sole identification count), §5
  risk 1 (never degrade the view) and risk 2 (never re-fetch what Layer 0 already holds)
- `src/prismabib/capture/enrich.py` — `capture_abstracts`; `src/prismabib/capture/layout.py`
  — the shared Layer 0 vocabulary; `src/prismabib/capture/manifest.py` —
  `AbstractRunManifest`, `AbstractUnavailable`
- `tests/contract/test_scopus_contract.py` —
  `test_contract__search_complete_response__carries_no_subject_areas` (50 real entries, 0
  with subject areas) and the Abstract Retrieval shape tests
- `tests/integration/capture/test_enrich.py` — the call-counter suite;
  `tests/unit/capture/test_layout.py` — the by-name exclusion
- [Elsevier Abstract Retrieval API](https://dev.elsevier.com/documentation/AbstractRetrievalAPI.wadl)
  — views and response shape
- [Elsevier Serial Title API](https://dev.elsevier.com/documentation/SerialTitleAPI.wadl) —
  the ISSN-keyed alternative rejected above

---

This ADR records where subject-area codes come from and where the responses are stored.
Moving the payloads out of Layer 0, flattening `raw/abstracts/` beside the search runs,
giving an abstract run a `runs` row, or allowing a fallback to `view=META` requires a new
ADR that supersedes this one (§2.6).
