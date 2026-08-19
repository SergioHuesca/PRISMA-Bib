# Test fixtures: cassettes, sanitisation, and the licence rule

BUILD_PLAN §2.5 (lines 280-298) and §3.7.5 (lines 533-537) govern everything
under this directory. **Read them before touching a cassette.**

## The rule, stated plainly

Scopus/Elsevier content is licensed. This repository is **public**. A
`git push` to GitHub is effectively irreversible -- history can be rewritten
locally, but anything already fetched by a mirror, a fork, or GitHub's own
caches is out. That makes the cost of committing licensed content
asymmetric with the cost of avoiding it: avoiding it costs one extra script
run; committing it costs an uncontrolled, permanent disclosure with no
practical undo.

**Consequently: raw Scopus API responses never touch a `git add`, ever, for
any reason, even temporarily.** Every file under `tests/fixtures/cassettes/`
in this repository has already been through `sanitise.py`
(`tests/fixtures/sanitise.py`) before it was committed. If you are looking
at a file in this directory and cannot point to the `sanitise.py` run that
produced it, do not trust it, and do not add to it.

## Recording procedure (BUILD_PLAN line 833)

1. **Record with a real key**, against the real Scopus API, into a
   directory *outside* the repository (e.g. this project's scratch/temp
   directory -- never `tests/fixtures/` directly, so step 3 cannot be
   skipped by accident). Capture the full response body (and, if the
   cassette needs to pin request shape too, the request headers/URL) as
   plain JSON files.
2. **Run `sanitise.py`** against every recorded file:

   ```python
   import json
   from tests.fixtures.sanitise import sanitise_page

   with open("/path/outside/the/repo/some-page.json") as f:
       raw = json.load(f)

   sanitised = sanitise_page(raw, seed=0)  # seed fixed -> reproducible diffs

   with open("tests/fixtures/cassettes/some-page.json", "w") as f:
       json.dump(sanitised, f, indent=2, ensure_ascii=False, sort_keys=True)
       f.write("\n")
   ```

   `sanitise_page` (see its docstring for the exact rules) regenerates
   `dc:title`, `dc:description`, `dc:creator`, every `author` entry, and
   every `affiliation` entry; everything else -- identifiers, dates, counts,
   the pagination envelope, and crucially *which fields are present on
   which entry* -- passes through unchanged. `sanitise_headers` and
   `sanitise_query_string` do the equivalent job for request headers/URLs,
   fully redacting `X-ELS-APIKey`/`X-ELS-Insttoken`/`apiKey`/`insttoken`.
3. **Diff the sanitised output against the previous cassette** (if any)
   before committing. A cassette regeneration that isn't reviewable the same
   way a code diff is reviewable is a cassette that shouldn't be
   regenerated silently -- same principle as golden snapshots (§3.7.5,
   final bullet).
4. **Commit only the sanitised file.** Never the file from step 1. Delete
   the step-1 recording once step 2 has run -- do not leave it lying around
   in a scratch directory that might itself end up `git add -A`-ed by a
   later, less careful command.

## What "sanitised" actually preserves

The point of a cassette is to pin *shape*, not content -- that is the whole
job of `tests/contract/`. `sanitise_page` is built around that: it never
touches field presence/absence, list-vs-scalar shape, or the pagination
envelope (`cursor`, `link`, `opensearch:*`). Two examples worth knowing
about explicitly, because a future maintainer might otherwise "fix" them:

- `cassettes/complete-page-0000.json` has exactly 3 of its 25 entries
  missing `authkeywords`/`affiliation`/`author` entirely (they are
  proceedings-level "Conference Review" records, not individual papers,
  which genuinely carry no author list). This is real API behaviour, not a
  fixture bug -- **do not "complete" those 3 entries.** It is precisely the
  structure `test_contract__search_response__has_required_fields` and
  `test_capture__*` exercise: a real page is not uniform, and code that
  assumes every entry has every field will break on real data long before
  it breaks on this cassette.
- `cassettes/standard-page-0000.json` has *zero* entries with
  `authkeywords`, `author`, `author-count`, `dc:description`, `fund-acr`,
  `fund-no`, or `fund-sponsor` -- the exact 7-field gap `view=STANDARD`
  leaves relative to `view=COMPLETE`. That gap is the reason
  `ScopusClient.search` never falls back to `STANDARD` on a 403 (BUILD_PLAN
  §5 risk 1); `test_contract__standard_view_response__lacks_authkeywords`
  pins it so nobody "simplifies" that guard away later.

## Files in this directory

| File | Source | Notes |
| --- | --- | --- |
| `sanitise.py` | -- | The sanitiser itself; see its own docstring. |
| `cassettes/complete-page-0000.json` | Real `view=COMPLETE` recording, page 0 of a live search | 22/25 entries carry the COMPLETE-only fields; 3 do not (see above). |
| `cassettes/complete-page-0001.json` | Real `view=COMPLETE` recording, page 1 (via cursor) of the same search | All 25 entries carry the COMPLETE-only fields. |
| `cassettes/standard-page-0000.json` | Real `view=STANDARD` recording, page 0 of the same query | Zero entries carry the 7 COMPLETE-only fields. |
| `cassettes/error-401-invalid-apikey.json` | Real recorded `service-error` body for a mismatched institution token | Contains no PII/licensed content -- an error message, not bibliographic data -- so it is committed verbatim, unsanitised. |
| `cassettes/error-403-entitlement.json` | **Not** a live recording | Modelled on the same `service-error.status.{statusCode,statusText}` shape as the 401 cassette above, for a hypothetical `view=COMPLETE` entitlement denial. A live 403-on-COMPLETE recording would need a second, non-entitled API key, which this project does not have and is not worth acquiring solely to record one JSON body; the *shape* (not the exact `statusText`) is what `test_search__403_on_complete_view__raises_entitlement_error` depends on, and that shape is verified live by the sibling 401 cassette. |

## The sanitiser is tested too (§3.7.5, line 535)

`tests/unit/fixtures/test_sanitise.py::test_sanitise__real_key_present__is_redacted`
asserts `sanitise_headers`/`sanitise_query_string` actually remove a
planted secret value rather than merely reformatting it.
