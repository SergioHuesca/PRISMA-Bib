# ADR 0030: OpenAlex Topics as a New Keyword Kind

## Status

Proposed — 2026-10-21. Amends the data model: the allowed values of `record_keywords.kind`
change from `author` and `index` to include `openalex-topic`. It is the separate ADR that
[ADR 0028](0028-field-level-fallback-cascade.md) resolved item 5 requires, and no keyword code
is written before it is accepted. The owner accepts or amends it; it moves to Accepted when the
owner approves it on merge. Until then, `main` says nothing about the new kind, and the
open questions at the end of this record are unanswered.

## Context

[ADR 0028](0028-field-level-fallback-cascade.md) Decision 2 says OpenAlex topics are stored with
source `openalex-topic` and are never compared one-to-one with Scopus author keywords. Resolved
item 5 says they feed the keyword network as a new keyword kind, separate from `author` and
`index`, and that this needs an ADR amending the data model. This record is that ADR.

### The current contract, as written

`record_keywords` allows exactly two kinds. The contract exists in four places, and none of
them is a database constraint:

- `src/prismabib/store/schema.sql`, the DDL: `CREATE TABLE record_keywords (record_id TEXT,
  keyword_id TEXT, kind TEXT);  -- author|index`. The `kind` column is plain `TEXT`, with no
  `CHECK` and no DuckDB `ENUM`. The same comment is in BUILD_PLAN line 871, which is frozen.
- `src/prismabib/store/load.py`, the `Corpus.keywords` docstring: "Not validated against that
  closed set here -- an unrecognised ``kind`` simply matches no rows, since
  ``record_keywords.kind`` is plain ``TEXT``, not a DuckDB ``ENUM``."
- `docs/architecture/data-model.md`, the `record_keywords` entry: `kind` ("author" or "index",
  per the capture source).
- The loader's writes, which are the only producers. `load.py` calls
  `_accumulate_keyword(..., kind="author")` for `record.author_keywords`, and
  `_accumulate_keyword(..., kind="index")` for `record.index_keywords`. The second call is
  unreachable today, because `_record_from_entry` hardcodes `index_keywords=[]`.

So there is no schema check that enforces the set. The set is enforced by convention: the
loader writes two literals, and every reader filters on one of them. This record does not
treat that as a defect, but it does mean a new kind cannot be rejected by the database, and
the tests have to do that job (Decision 8).

Every reader of `record_keywords` filters by kind, and none reads all kinds at once:

| Reader | Kind it reads | Changed by this ADR? |
|---|---|---|
| `Corpus.keywords(kind="author", stage)` in `store/load.py` | caller's `kind`, default `"author"` | No default change |
| `keyword_frequency`, `keyword_evolution` in `bibliometrics/keywords.py` | `kind="author"` default | No |
| `keyword_cooccurrence_network` in `bibliometrics/network.py` | `kind="author"` default | No |
| `taxonomy/coder.py` lines 377-378 | `"author"` and `"index"`, literally | No; topics are not a rule field |
| `screening/ui.py` line 231 | `rk.kind = 'author'`, literally | No |

`coauthorship_network` reads authorships, not keywords, and is outside this decision.

### The OpenAlex side already exists, and stores nothing

`src/prismabib/sources/openalex.py` already defines `KEYWORD_KIND = "openalex-topic"`, a
`TopicKeyword(name, score)`, and `topics_to_keywords()`. Its module docstring says "Nothing here
writes to the store ... persisting it needs a separate ADR (ADR 0028, resolved item 5)." This
record is that ADR, and it keeps the name already in code.

There is no OpenAlex capture writer and no OpenAlex Layer 0 directory. `grep` finds OpenAlex
only in `config.py` and `sources/openalex.py`. So the topics cannot reach the store until the
OpenAlex payload capture of [ADR 0028](0028-field-level-fallback-cascade.md) Decision 3 exists.
That is a prerequisite, and Decision 7 names it.

## Decision

### 1. The new kind is `openalex-topic`

Topics are stored as `record_keywords` rows with `kind = 'openalex-topic'`. The name is the
existing `KEYWORD_KIND`, so the source is written the same way in code, in the store, and in
the methods text.

Topic names pass through the same `_normalise_keyword_term` as author keywords and use the same
`keyword_id` hash. One keyword dictionary serves every kind, so a topic named "Machine Learning"
and an author keyword "machine learning" share a `keywords` row and differ only in
`record_keywords.kind`. The raw form stays in `keywords.term_raw`, as ADR 0028 Decision 2 needs.

The OpenAlex `score` is not stored. `record_keywords` has no column for it, and adding one is a
DDL change that this decision does not make (see the open questions). The score stays in the
Layer 0 payload, so a later decision can add it without re-capture.

### 2. Topics stay out of every existing keyword network by default

Isolation rests on the existing kind filter, not on a new mechanism. `Corpus.keywords` selects
`WHERE rk.kind = ?`, and the default kind is `"author"`. A default call therefore returns
exactly the rows it returned before this change, because no existing caller asks for a topic.

Two rules make that hold:

- **No function gains a default that reads topics.** Every keyword function keeps `kind="author"`.
  A new kind is never added to a default set.
- **The explicit parameter adds nothing to default output.** A topic-only network is
  `keyword_cooccurrence_network(corpus, kind="openalex-topic")`, which needs no signature change.
  A network that mixes author keywords and topics needs a new keyword-only parameter
  `extra_kinds: tuple[str, ...] = ()`. The parameter adds the topic kind to the `kind` the
  function already reads. It is recorded in `params` only when it is non-empty.

The second rule is needed because `params` is part of the published output. `params_json` is
written by the figure export (`bibliometrics/base.py`, the export that writes
`params_json_bytes`), and scalar entries of `params` are written into the caption
(`bibliometrics/base.py`, the `scalar_params` block). A parameter that always writes its key
would change those bytes for every existing call, even though the nodes and edges were the same.

The functions that read kind, and the requirement on each:

- `bibliometrics/keywords.py`: `keyword_frequency` and `keyword_evolution`. Default output
  unchanged.
- `bibliometrics/network.py`: `keyword_cooccurrence_network`. Default output unchanged, and its
  `params` and caption unchanged when `extra_kinds` is empty.
- `store/load.py`: `Corpus.keywords`. Its `kind` parameter already accepts any string. Its
  docstring changes from "``author`` or ``index``" to the allowed set.
- `taxonomy/coder.py`: reads `author` and `index` literally. Unchanged, and it must stay so,
  because a topic is not a taxonomy rule field.
- `screening/ui.py`: reads `author` literally. Unchanged.
- `viz/dashboard.py` and `viz/figures.py` call the three bibliometrics functions with their
  defaults. They inherit the guarantee.

A topic-bearing network labels itself. Its `kind` is a scalar parameter, so the caption already
shows `kind=openalex-topic`.

### 3. The schema change: the allowed set grows, the DDL does not

- **DDL:** no change. `record_keywords` keeps its three columns and `kind TEXT`. No table is
  added and no `CHECK` is added. The frozen BUILD_PLAN DDL (line 871) is not edited; its comment
  is superseded by this record.
- **Allowed set:** `author`, `index`, `openalex-topic`. The set is written once, as a constant in
  `store/`, which the loader and the tests import. The text in `schema.sql`, the
  `Corpus.keywords` docstring, `data-model.md` and `limitations.md` (which says
  `record_keywords` never contains a row of kind `"index"`, and stays true) are updated to
  match, in the implementation pull request. They are not changed by this ADR's pull request,
  because until the implementation exists they would be false.
- **Loader:** one more pass after the Scopus record pass writes `openalex-topic` rows. The pass
  runs after the Scopus pass because `_accumulate_keyword` keeps the first-seen raw form
  (`acc.keywords.setdefault`). An author keyword's `term_raw` therefore wins over a topic
  with the same normalised term, and no existing keyword's displayed form changes.

### 4. Migration: none in place. A store is rebuilt from Layer 0

`store/db.py` says a store "is a build artefact, not a migrated database". This change adds
no table and no column, so no migration is written. A store is rebuilt with
`build_store(project, rebuild=True)` from Layer 0, and the rebuild is the only way a store
gains topic rows.

One consequence needs stating. `_refuse_stale_schema` checks table presence only. A store built
before this change has every table, so it is not refused. It also has no topic rows, and nothing
in it says so. A researcher who runs the new code against an old store gets an accurate
Scopus-only store, with no warning that topics are absent. Open question 4 asks the owner
whether that is acceptable.

### 5. Checksums and the frozen reference fixture

The checksum is a hash of row content, not of DDL. `store/checksums.py` `table_checksum` runs
`SELECT *` in sort-key order and hashes the cells (`checksums.py`, lines 105 to 130). Nothing
hashes `schema.sql`.

Therefore:

- **A store with no topic rows keeps every table checksum.** This includes every store built
  from Scopus data alone, and the frozen reference fixture.
- **A store with topic rows changes `record_keywords` and `keywords`.** `record_keywords` gains
  rows, and `keywords` gains a row for each topic-only term. Every other table is unchanged. The
  change is expected and is confined to new, topic-bearing stores. `record_keywords` already
  sorts by `(record_id, keyword_id, kind)`, so the new rows sort deterministically.

**BUILD_PLAN line 901** requires that `build_store(project, rebuild=True)` on the same Layer 0
input produce a byte-stable set of table checksums. This change keeps it. The reference fixture
has no OpenAlex data. `grep` for "openalex" under `tests/fixtures/projects/reference/` finds
nothing. The `tests/fixtures/openalex/` directory holds hand-written API cassettes for the client
(its README says so). They are not Layer 0 store input.

**The reference fixture does not need to be regenerated.** The golden
`tests/golden/store/__snapshots__/reference_table_checksums.json` and
`tests/golden/report/__snapshots__/reference_numbers.json` stay as they are. BUILD_PLAN line 927
says golden checksums are recomputed "only when the reference fixture or the loader semantics
deliberately change". Neither does here, for Scopus-only input. The implementation pull request
must say that in its description, under the rule in BUILD_PLAN line 927.

A test of topic-bearing stores uses a **new** fixture, or a project built in `tmp_path`. It does
not touch the frozen reference fixture, which BUILD_PLAN line 536 freezes and versions.

### 6. Reproducibility, and what ADR 0027's criterion covers

[ADR 0027](0027-what-v1-0-0-claims.md) Decision 3 requires that `numbers.json` from a clean clone
on Linux be byte-identical to the one from a clean clone on Windows. This decision keeps that
criterion for every number already published:

- every published number comes from a Scopus-only store, which this change does not alter;
- the cross-runner test runs on the frozen reference fixture, which has no topics; and
- every default keyword output, including `params_json` and captions, is unchanged (Decision 2).

For a corpus enriched with topics, the reproducibility claim is the one
[ADR 0028](0028-field-level-fallback-cascade.md) resolved item 7 makes: it holds for the recorded
Layer 0 OpenAlex payloads, and a fresh OpenAlex query is a new run with its own checksum. Such a
corpus is **not** covered by the frozen-fixture cross-runner test. Before any topic-based number
is cited, the cross-runner check must also run on a topic-bearing fixture, or the number must be
marked as outside that check. Ordering is the machine-dependence risk here. OpenAlex serves topics
in a stable order, but the loader must sort them through the primary key and not rely on the
order of the JSON array. That is tested on both runners.

### 7. Prerequisite: OpenAlex Layer 0 payloads

The rebuild test in Decision 8 cannot exist until OpenAlex payloads are Layer 0 data. This
record does not design that capture. It requires:

- an OpenAlex run kind written under `raw/` with a sealed manifest and the checksum pattern of
  `capture/writer.py`, as [ADR 0028](0028-field-level-fallback-cascade.md) Decision 3 says;
- its directory name added to `capture/layout.py::NON_RUN_DIRNAMES`, following the
  [ADR 0011](0011-abstract-retrieval-for-subject-areas.md) precedent, so the search-run scans skip
  it by name; and
- the decision on whether that capture is specified in its own ADR (open question 5).

### 8. Verification the implementation must include

Each test below must be shown to fail on the defect it guards, following the project rule that a
test covering a line proves nothing until it fails on the original defect.

1. **Schema test.** `test_record_keywords__kinds_written__are_in_the_documented_set`: the kinds
   the loader writes, and every kind in a built store, are inside the allowed-set constant. The
   constant matches the set in `schema.sql`'s comment and in `data-model.md`. The test fails if a
   loader writes an undocumented kind, or if a doc lists a kind the loader does not write.
2. **Default network output is unchanged.** Build a Scopus-only store and a store with topics
   from the same Scopus input. Assert that `keyword_frequency`, `keyword_evolution` and
   `keyword_cooccurrence_network` with defaults give the same `data`, `params` and `params_json`
   bytes on both. Also assert that the existing golden tests (`reference_numbers.json`, the
   VOSviewer export, the caption tests) pass with no snapshot change. The test fails if a default
   is changed to read topics.
3. **Rebuild from Layer 0.** Build a topic-bearing project, delete `corpus.duckdb`, rebuild, and
   assert the table checksums are identical and the topic rows are equal. This mirrors
   `test_build_store__after_deleting_db__reproduces_identical_checksums`.
4. **Term precedence.** A term that is both an author keyword and a topic keeps the author
   keyword's `term_raw`. This fails if the topic pass is moved before the Scopus pass.
5. **Runs in CI.** The suite runs with `pytest -n auto` and in a single process, and the
   topic-bearing check runs on Linux and Windows.

## Alternatives considered

**A separate table, `record_topics`.** This is the strongest alternative, and the owner may
prefer it. A separate table cannot be read by any existing query, so the default-output guarantee
holds by construction, not by a filter. It also keeps `record_keywords` as the Scopus vocabulary.
It was not chosen because it duplicates the keyword dictionary and its normalisation, needs a
second `Corpus` method, and adds a table, which `schema.sql`'s header says needs its own ADR. The
kind filter already gives the isolation this decision needs, and ADR 0028 already named the kind.

**Merge topics into author keywords.** Rejected. ADR 0028 Decision 2 says topics are never compared
one-to-one with author keywords, and a merged kind would change every default network silently.
That is the outcome this decision must prevent.

**Do not store topics.** Rejected. Topics would then exist only in Layer 0 and could feed a
network only by re-querying OpenAlex. A live query is not reproducible from the store (ADR 0028
resolved item 7), so the network would not be reproducible either.

## Consequences

1. Topics become a queryable keyword source, with the same normalisation and dictionary as author
   keywords, and no default output changes.
2. The contract moves from a comment to a tested constant. The DDL stays as it is, and the
   protection against undocumented kinds moves into tests.
3. Pre-change stores are silently Scopus-only until rebuilt (Decision 4). The methods must say
   which store was used, and open question 4 decides whether the build must say it too.
4. A record with no topic rows is either "OpenAlex has no topics for it" or "OpenAlex
   was never asked". `record_keywords` cannot tell these apart, the same ambiguity
   [ADR 0011](0011-abstract-retrieval-for-subject-areas.md) resolves for subject areas. The
   per-field outcome of ADR 0028 (the `unavailable` literal in exports) must carry it, and the
   implementation must not infer it from the presence of rows.
5. The frozen reference fixture and its goldens do not change. The BUILD_PLAN line 901 criterion
   holds without regeneration.
6. The mixed network adds one parameter, and only when used. Its default is the old behaviour.

## Open questions for the owner

These must be answered before this record is accepted.

1. **Kind or separate table?** This record proposes a kind, because the filter already isolates it.
   The separate table gives isolation by construction at the cost of a second dictionary path.
   Which does the owner want?
2. **Is a mixed author-and-topic network wanted at all?** Combining two vocabularies in one
   co-occurrence graph is a methodological choice, and ADR 0028 keeps them apart. If no mixed
   network is wanted, `extra_kinds` is not built, and only topic-only networks exist.
3. **Database enforcement.** Should `record_keywords.kind` gain a `CHECK (kind IN (...))`? A check
   costs nothing at rebuild, but it changes DDL that BUILD_PLAN freezes. This record proposes
   tests only.
4. **Silent staleness.** Is it acceptable that a store built before this change has no topics and
   says nothing? Options: the build report states the topic count (`record_keyword_links_loaded`
   already counts all kinds, so a per-kind count is needed), or the stale-schema guard refuses old
   stores. Refusing is stricter but needs a stored schema version, which is a new table.
5. **OpenAlex Layer 0 layout.** Is the OpenAlex payload capture (Decision 7) specified in its own
   ADR, or in an amendment to ADR 0028? The rebuild test depends on it.
6. **Topic score.** The OpenAlex `score` is not stored (Decision 1). Does any analysis need it? If
   so, it needs a DDL column, and this record would be amended.
7. **Term precedence.** Confirm that an author keyword's raw form wins over a topic with the same
   normalised term (Decision 3). The alternative is that the first record in load order wins,
   which makes the displayed form depend on the topic pass.

## Related decisions

- [ADR 0011](0011-abstract-retrieval-for-subject-areas.md) — a Layer 0 run kind nested under `raw/`
  and skipped by name, which Decision 7 follows.
- [ADR 0022](0022-the-analysis-result-contract-and-its-provenance.md) — `params` is part of the
  analysis result, which is why Decision 2 adds nothing to it by default.
- [ADR 0027](0027-what-v1-0-0-claims.md) — the cross-runner reproducibility criterion Decision 6
  keeps.
- [ADR 0028](0028-field-level-fallback-cascade.md) — the cascade whose Decision 2 and resolved item 5
  this record implements, and whose item 7 bounds the reproducibility claim.
