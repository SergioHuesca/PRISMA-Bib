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
   id. Sealing deletes `_http/` together with the sidecar (`cursor.json`, `progress.json`), so a sealed run directory holds
   what it holds today, and `payload_sha256` is unaffected (it hashes the payload files only).
3. **"Not found" is never cached.** Unpaywall and Crossref 404 responses are not stored. Inside a resumed run a 404 is
   requested again: one call, accepted. A 200 body is cached for resume as before.
4. **Existing `raw/_cache` and `fulltext/_cache`.** They are never read again. They stay in `NON_RUN_DIRNAMES` so the run
   scans keep skipping them; they are safe to delete and are gitignored. No migration, and the tool deletes nothing. An
   unsealed run started before the upgrade resumes from its cursor; if it has none it replays from page 0 and spends quota
   once (the case the cursor exists to avoid).
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
   development cost no quota" (`sources/cache.py` line in BUILD_PLAN) is superseded for new runs. The restatement in
   `tests/integration/capture/test_enrich.py` is re-scoped the same way.
7. **No switch.** No CLI option, no API parameter, no setting. A refresh is "run the search again"; a resume is automatic.
   An opt-in `--use-cache` would keep the defect available on demand.
8. **Quota.** Re-running a search or abstract capture now spends the full quota of the query. The docs say so where they
   tell the user to re-run to refresh counts.
9. **Full-text runs.** The same rule, with the same per-run directory. `default_chain` is built before the run directory
   exists, so PR4 passes the cache root when `capture_fulltext` resolves the run directory (resume or new), not at chain
   construction. `already_resolved_record_ids` is separate and unchanged: it skips records already resolved in sealed runs.

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
  `fulltext/_cache`; the cache is under the run directory and gone after seal.
- Run `pytest -n auto` as well as single-process, and `mkdocs build --strict`.

## Code changes of PR4

- `capture/writer.py`, `capture/enrich.py`: `HttpCache(run_dir / "_http")`; remove it at seal next to the sidecar.
- `fulltext/resolve.py`, `fulltext/capture.py`: per-run cache root as in Decision 9; remove at seal.
- `sources/unpaywall.py`, `sources/crossref.py`: do not store a 404.
- `capture/layout.py`: an `HTTP_CACHE_DIRNAME` constant; `CACHE_DIRNAME` stays in `NON_RUN_DIRNAMES` for legacy dirs.
- Docstrings (`writer.py`, `cache.py`), `docs/getting-started.md`, `docs/how-to/run-a-new-review.md`, `CHANGELOG.md`.
- The tests above.
