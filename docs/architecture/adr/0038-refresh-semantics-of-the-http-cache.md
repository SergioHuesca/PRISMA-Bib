# ADR 0038: Refresh Semantics of the HTTP Cache

## Status

Accepted — 2026-10-28, on the merge of the pull request that adds this record, which the owner approves. Until the
merge, this record was `Proposed` on the branch `docs/adr-0038-refresh-semantics`. Accepting it changes no code and no
test: it fixes the design that the pull request PR4 ("Code changes of PR4") implements. The owner's answers to the three
open design questions are in "Questions put to the owner, and the answers".

ADRs 0001 to 0037 are frozen. This record does not edit any of them. `BUILD_PLAN.md` is frozen too; Decision 6 states
what this record supersedes in it.

## Context

`capture_search` (`src/prismabib/capture/writer.py`) always builds `HttpCache(raw/_cache)`. The cache
(`src/prismabib/sources/cache.py`) is keyed on `(url, params)` and never expires. Reproduced on `bf14af4`: a second
`capture_search` of the same query, in a new run directory, made **0** network calls and sealed a run with a new
`started_at`. `store/load.py` uses `manifest.started_at` as `retrieved_at` of every citation snapshot, so citation counts
fetched months earlier were labelled as fresh, in an archive that cannot be edited afterwards (Layer 0 is immutable).

The docs tell the user to do exactly this to refresh counts (`docs/getting-started.md`, `docs/how-to/run-a-new-review.md`:
"re-run the same query to refresh citation counts"). `docs/how-to/run-a-new-review.md` also says a warm cache reproduces
`payload_sha256` byte for byte.

Unpaywall and Crossref store a 404 as an empty body (`sources/unpaywall.py`, `sources/crossref.py`), so a DOI that was
"not found" once is "not found" forever, even after the publisher registers it. A test asserts that
(`test_lookup__404__returns_none_and_caches_the_negative_result`).

Every use of the cache (grep of `HttpCache(` and `cache=`):

| Caller | Cache root today | Run kind |
|---|---|---|
| `capture/writer.py` (Scopus search) | `raw/_cache` | search run; resume by `cursor.json` |
| `capture/enrich.py` (Scopus abstracts) | `raw/_cache` | abstract run; resume by `progress.json` |
| `fulltext/resolve.py` `default_chain` (ScienceDirect, Crossref TDM, Unpaywall) | `fulltext/_cache` | full-text run; resume by `progress.json` |
| `capture/openalex.py` | `cache=None` | no cache; unaffected |

## Decision

1. **The rule.** A new capture run never reads an HTTP cache written by an earlier run. The cache serves only to resume an
   **unsealed** run: the same run directory. A run is classified by code that already exists, with no new state: it is a
   *resume* if `_find_resumable_run` finds an unsealed directory (no `manifest.json`, see `is_sealed`) whose `cursor.json`
   or `progress.json` matches (query, view, endpoint, or records digest). Otherwise it is *new*. A sealed run is never
   resumed and never written (`guard_writable`).
2. **Where the cache lives.** Inside the run directory it serves: `<run_dir>/_http/` for search, abstract and full-text
   runs. A new run directory starts with no cache by construction, so nothing leaks between runs and no key needs a run
   id. Sealing removes `_http/` and the sidecar (`cursor.json`, `progress.json`) only AFTER the atomic manifest write, and
   removing `_http/` is best-effort (`shutil.rmtree(..., ignore_errors=True)`), so a Windows file lock cannot raise after
   the run is sealed. A crash between the manifest and the removal leaves a sealed directory that still holds `_http/`.
   That is harmless: `payload_sha256` hashes only the listed payload files, and no loader scans inside run directories.
   An unsealed run keeps `_http/` on disk (under `raw/` and `fulltext/runs/`, both gitignored through `projects/*/` and
   guarded by `scripts/reject_licensed_content.sh`); a full-text `_http/` holds every ScienceDirect response until seal.
3. **"Not found" is never cached.** Unpaywall and Crossref 404 responses are not stored. Inside a resumed run a 404 is
   requested again: one call, accepted. A 200 body is cached for resume as before.
4. **Existing `raw/_cache` and `fulltext/_cache`.** They are never read again. They stay in `NON_RUN_DIRNAMES` so the run
   scans keep skipping them; they are safe to delete and are gitignored. No migration, and the tool deletes nothing. An
   unsealed legacy search run resumes from its cursor; if it has none it replays from page 0 and spends quota once. An
   unsealed legacy abstract run loses its partial batch (up to 99 records, which `capture/enrich.py` relied on
   `raw/_cache` to recover) and re-requests it once.
5. **`retrieved_at`.** Unchanged in code: `manifest.started_at`, which a resumed run keeps from its first attempt. After
   this change it is true: every response in the run was obtained by that run, at or after `started_at` and before
   `finished_at`. For a run resumed days later it is a conservative lower bound (older than some pages), never a newer
   label than the data.
6. **Acceptance criterion S02-AC2.** What it protects is that `payload_sha256` is a deterministic function of the
   response bodies: the payload bytes are a canonical re-encoding (sorted keys, compact separators) of the parsed page, so
   the same bodies always give the same hash, which is what makes the hash a citable identifier and Layer 1 rebuildable
   from Layer 0. It does not protect "a re-run costs no quota". The criterion text "Re-running with the cache warm produces
   a byte-identical payload hash" (BUILD_PLAN, S02 acceptance criteria; test-table row
   `test_capture__warm_cache_rerun__payload_sha256_is_identical`) is superseded by: "Re-running against responses with the
   same bodies produces a byte-identical payload hash; a new run issues new requests." The test is re-scoped, keeps the
   `S02-AC2` marker, and changes from `route.call_count == 1` to `== 2` with equal hashes. The cache-hit behaviour moves to
   the resume criterion (S02-AC3), tested through resume (Verification). The deliverable wording "so re-runs during
   development cost no quota" (`sources/cache.py` line in BUILD_PLAN) and the risk 2 clause "Aggressive local HTTP cache"
   (BUILD_PLAN risk table) are superseded for new runs: the mitigations that remain are the resumable cursor and never
   re-fetching what Layer 0 already holds. The criterion text is transcribed in `tests/acceptance_criteria.toml`
   (S02-AC2), which `tests/markers.py` reads, so that line changes with this record. The restatement in
   `tests/integration/capture/test_enrich.py` is re-scoped the same way. The frozen ADR 0011 (around line 160, "those are
   cache hits when `raw/_cache/` is warm") becomes stale for new runs; this record is the current statement.
7. **No switch.** No CLI option, no API parameter, no setting. A refresh is "run the search again"; a resume is automatic.
   An opt-in `--use-cache` would keep the defect available on demand.
8. **Quota.** Re-running a search or abstract capture now spends the full quota of the query. The docs say so where they
   tell the user to re-run to refresh counts.
9. **Full-text runs.** The same rule, with the same per-run directory. `default_chain` (a `@contextmanager` whose
   `finally` closes the three clients) has one production caller (`fulltext/run.py`,
   `with default_chain(project, settings) as resolvers:`), and it binds an `HttpCache` into each client at construction,
   before `capture_fulltext` chooses the run directory. Mechanism: a chain factory. `default_chain` takes a `cache_dir`
   argument; the factory is a callable from the run's `_http/` path to the context manager `default_chain` already is;
   `run.py` passes `lambda cache_dir: default_chain(project, settings, cache_dir=cache_dir)`. Chosen over a late-bind
   setter on the three clients because the cache root stays immutable once a client exists, and no resolver needs a path
   to its client.
   - **Ownership of closing moves from `run.py` to `capture.py`.** After the resume-or-new decision and before the
     attempt loop, `capture_fulltext` does `with factory(run_dir / "_http") as resolvers:`, so the `EntitlementError` raise
     path still closes the clients. `_http/` is removed after the `with` exits and after the atomic manifest write,
     best-effort (Decision 2); `HttpCache` is plain files, so removal is safe on Windows.
   - **The `resolvers=` parameter keeps working unchanged**, with no per-run cache, for callers that pass ready-made
     resolvers. The factory is a second, optional parameter and is the production path used by `run.py`; exactly one of
     the two is given. Reason: 15 call sites pass `resolvers=` (12 in `tests/integration/fulltext/test_capture.py`, 2 in
     `test_entitlement_attribution.py`, 1 in `test_run.py`) and none of them builds an HTTP client, so replacing the
     parameter would rewrite them for no behaviour.
   - `already_resolved_record_ids` is separate and unchanged: it skips records already resolved in sealed runs.

## Alternatives considered

- **Cache with expiry (TTL).** Rejected. It needs a number nobody can justify (citation counts change daily, entitlement
  rarely), makes whether a run touches the network depend on the clock (a machine-dependence this project avoids), and
  still labels data up to TTL old as fresh unless `retrieved_at` also changes.
- **Leave as is and document.** Rejected. The archive cannot be corrected after sealing, and the docs themselves
  recommend the action that produces the stale labels.
- **Keep the shared cache and add `--refresh`.** Rejected. The safe behaviour would be opt-in and the default would stay
  wrong.
- **Record the real fetch time per response.** Not now. It is a larger schema change (manifest, loader); the rule makes
  the existing field correct.

## Consequences

- Positive: `retrieved_at` is true; a refresh really refreshes; "not found" can change; no cross-run state under `raw/`.
- Negative: development re-runs cost quota. Mitigation: the cursor resume still avoids re-fetching an interrupted run, and
  tests mock the network.
- An interrupted run that is resumed late mixes ages (Decision 5); see open question 1.
- Docs that say a warm cache reproduces the hash, or that a re-run is free, become wrong and change in PR4.

## Questions put to the owner, and the answers

1. Where does the resume-only cache live? In a per-run `_http/` folder inside the run directory, removed at seal.
   The alternative (no cache for a new run, the shared cache only on resume) was not chosen: it is simpler but gives up
   replay of already-paid responses within an abstract run.
2. Should a resume refuse an unsealed run older than some age? No. A resume continues an old unsealed run, and
   `retrieved_at` stays a conservative lower bound for it (Decision 6).
3. Should the tool warn when it finds a legacy `raw/_cache` or `fulltext/_cache`? No. The folders are documented as
   safe to delete and nothing new is printed.

## Verification

Tests PR4 adds or changes. Each new test must be shown to fail on `bf14af4`.

- Change `test_capture__warm_cache_rerun__payload_sha256_is_identical` (rename to `..._rerun_same_responses__...`): equal
  hashes, `route.call_count == 2`, marker `S02-AC2`.
- Add `test_capture__second_run_same_query__issues_new_requests_and_new_started_at`.
- Add `test_capture__legacy_raw_cache__is_never_read`: a poisoned entry in `raw/_cache` for the request URL does not
  reach the payload.
- Add `test_capture__resume_without_cursor__replays_from_run_cache`: an unsealed run with `_http/` and no `next_cursor`
  resumes with no extra call; marker `S02-AC3`.
- Add `test_capture__seal__removes_run_cache_and_leaves_payload_hash_unchanged`.
- Change `test_enrich__warm_cache_rerun__...` as in Decision 6; add the resume and seal tests for abstracts.
- Change the Unpaywall `test_lookup__404__returns_none_and_caches_the_negative_result` and its Crossref counterpart to
  `..._does_not_cache_the_negative_result` (a second lookup issues a request).
- Change `test_default_chain__caches_http_responses_under_fulltext_dir_not_raw_dir`: nothing under `raw/` or
  `fulltext/_cache`; the cache is under the run directory and gone after seal. The other `default_chain` callers pass the
  new `cache_dir`: `tests/integration/fulltext/test_resolve.py` (about L327 and L728) and
  `tests/integration/fulltext/test_entitlement_attribution.py` (about L115).
- `test_capture__cold_cache_resume__starts_from_persisted_cursor` (`test_writer.py`, about L248) removes
  `raw_dir/"_cache"`; and in `test_enrich.py` `_clear_http_cache` (about L175) with
  `test_enrich__resumed_at_a_batch_boundary_with_a_cold_cache__refetches_nothing` (about L834): they must clear
  `<run_dir>/_http` instead, or they become vacuous.
- `tests/integration/fulltext/test_capture.py` gains tests for the factory path (closing on the `EntitlementError`
  path, `_http/` gone after seal, `resolvers=` untouched); `tests/integration/fulltext/test_run.py` covers `run.py`
  passing the factory.
- `tests/unit/capture/test_layout.py` (about L49 and L194) asserts the exact `NON_RUN_DIRNAMES` set and `__all__`; both
  change with `HTTP_CACHE_DIRNAME`.
- Run `pytest -n 4` as well as single-process, and `mkdocs build --strict`.

## Code changes of PR4

- `capture/writer.py`, `capture/enrich.py`: `HttpCache(run_dir / "_http")`; after the manifest write, remove it
  best-effort (Decision 2).
- `fulltext/resolve.py` (`default_chain(..., cache_dir)`), `fulltext/capture.py` (chain factory), `fulltext/run.py`
  (passes the factory): Decision 9; remove at seal as above.
- `sources/unpaywall.py`, `sources/crossref.py`: do not store a 404.
- `capture/layout.py`: an `HTTP_CACHE_DIRNAME` constant; `CACHE_DIRNAME` stays in `NON_RUN_DIRNAMES` for legacy dirs.
- `tests/acceptance_criteria.toml` S02-AC2 text.
- Docstrings that say a warm cache makes a re-run free or the payload byte-stable: `capture/writer.py` (about L23-27,
  L275), `sources/cache.py` (about L10-11), `capture/enrich.py` (about L76, L98, L115-116, L498, L685-694).
- Docs: `README.md` (about L75), `docs/architecture/provenance.md` (about L27, L102, L143-148),
  `docs/how-to/run-a-new-review.md` (about L249, L262, and the layout tree), `docs/getting-started.md` (quota note),
  `CHANGELOG.md`. The resume message in `cli.py` (about L323) and its test stay true, as do
  `docs/how-to/run-a-new-review.md` about L234 (cursor resume) and L306 (legacy dirname skipped).
- The tests above.

> **Note added 2026-10-29 (clarifications; this note does not change the record above).**
> 1. `default_chain(..., cache_dir=None)` means no cache: the three clients are built without an `HttpCache`
>    (`src/prismabib/fulltext/resolve.py`, about L905-940). Production always passes the run's `_http/` (Decision 9).
> 2. In `capture_fulltext` (`src/prismabib/fulltext/capture.py`, about L693-883) the `with ExitStack()` block encloses the
>    attempt loop and the seal (manifest build, atomic write, `progress.json` unlink). The clients close when that `with`
>    exits, after the seal.
> 3. `_http/` is removed by `shutil.rmtree(..., ignore_errors=True)` after the `with` exits, and only if the run is sealed.
>    An unsealed or budget-stopped run keeps `_http/` (Decision 2).
