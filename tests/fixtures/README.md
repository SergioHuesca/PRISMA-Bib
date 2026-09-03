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

   For an **Abstract Retrieval** response, use `sanitise_abstract` instead —
   the envelope and key vocabulary are different enough that `sanitise_page`
   would silently pass licensed prose through (authors are `ce:surname`/
   `@auid`, not `surname`/`authid`; the abstract is at
   `coredata["dc:description"]`):

   ```python
   from tests.fixtures.sanitise import sanitise_abstract

   sanitised = sanitise_abstract(raw, seed=0)
   ```

   `sanitise_abstract` **fails closed**: it raises `UnsanitisedFieldError`,
   naming the field, on any container it has not been taught. A live
   `view=FULL` recording *will* hit this, on `item.bibrecord` — the subtree
   that repeats the abstract, the author list and the affiliations in a
   different schema. That is deliberate friction, not a bug: teach the
   sanitiser that subtree, with a test, before committing such a cassette.
   Subject-area codes pass through verbatim, because they are the entire
   reason the Abstract Retrieval call exists — a cassette whose `@code`s had
   been regenerated would pin nothing.

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
| `cassettes/abstract-full-multi-subject-area.json` | **Not** a live recording | Modelled on Elsevier's documented Abstract Retrieval `FULL` response and passed through `sanitise_abstract`. Carries three `subject-area` entries as a **list**. See the caveat below. |
| `cassettes/abstract-full-single-subject-area.json` | **Not** a live recording | Same, with `subject-area` as a **lone mapping** — Scopus collapses a single-element container, and code that assumes a list breaks on real data. |
| `cassettes/abstract-full-no-subject-areas.json` | **Not** a live recording | Same, with no `subject-areas` key at all (a conference-review record). This is the shape that makes `AbstractUnavailable(reason="no_subject_areas")` necessary. |
| `cassettes/error-403-entitlement.json` | **Not** a live recording | Modelled on the same `service-error.status.{statusCode,statusText}` shape as the 401 cassette above, for a hypothetical `view=COMPLETE` entitlement denial. A live 403-on-COMPLETE recording would need a second, non-entitled API key, which this project does not have and is not worth acquiring solely to record one JSON body; the *shape* (not the exact `statusText`) is what `test_search__403_on_complete_view__raises_entitlement_error` depends on, and that shape is verified live by the sibling 401 cassette. |
| `cassettes/sciencedirect-article-full-modelled.xml` | **Not** a live recording | Stage 6 (ADR 0019). Modelled on Elsevier's publicly documented Article Retrieval `view=FULL` XML response shape (`full-text-retrieval-response`, `coredata`, `originalText`, the `ce:` element vocabulary for `ce:abstract`/`ce:sections`/`ce:section`/`ce:para`) rather than recorded, for the same reason as the `abstract-full-*.json` cassettes below: this project has no ScienceDirect Article Retrieval entitlement to record a real one against. Every title, author name and paragraph in it is synthetic prose written for this fixture -- there is nothing in it for a sanitiser to redact, so none was run. `test_extract__sciencedirect_xml__yields_expected_sections` and `test_contract__sciencedirect_article_retrieval__required_fields_present` pin *this project's belief* about the response shape, not a verified live one -- the same caveat as the Abstract Retrieval cassettes; see below. |

## The sanitiser is tested too (§3.7.5, line 535)

`tests/unit/fixtures/test_sanitise.py::test_sanitise__real_key_present__is_redacted`
asserts `sanitise_headers`/`sanitise_query_string` actually remove a
planted secret value rather than merely reformatting it.

## The Abstract Retrieval cassettes are modelled, and what that costs

The three `abstract-full-*.json` cassettes were **not** recorded from a live call. They
were built from Elsevier's documented Abstract Retrieval `FULL` response shape and run
through the real `sanitise_abstract`, so the committed bytes are the sanitiser's own
output — the same status, and for a related reason, as `error-403-entitlement.json`.

Say plainly what that means for the tests that read them:
`test_contract__abstract_response__carries_coded_subject_areas` and its two siblings pin
*this project's belief* about the response shape. **They would not fail if Scopus changed
it**, which is the one thing a contract test is supposed to do (§3.7.2). Replacing them
with a sanitised real recording is outstanding work — see ADR 0011, consequence 4.

One contract test in that group does have live force and is the one the feature actually
rests on: `test_contract__search_complete_response__carries_no_subject_areas` runs against
`complete-page-0000.json` and `complete-page-0001.json` — 50 real recorded `view=COMPLETE`
entries, not one of which carries a `subject-area` key. That is the measurement that says
the Abstract Retrieval call is necessary at all, and it is made against real data.

## `sciencedirect-article-full-modelled.xml` is modelled too, for the same reason

Same status as the Abstract Retrieval cassettes above, and the same cost: nobody on this
project holds a ScienceDirect Article Retrieval entitlement to record a real `view=FULL`
response against, so `test_extract__sciencedirect_xml__yields_expected_sections` and
`test_contract__sciencedirect_article_retrieval__required_fields_present` pin this
project's belief about Elsevier's documented shape, not a verified live one. Obtaining a
real entitlement and replacing this cassette with a genuine (sanitised) recording is
outstanding work, exactly as ADR 0011 consequence 4 already notes for the Abstract
Retrieval side.
