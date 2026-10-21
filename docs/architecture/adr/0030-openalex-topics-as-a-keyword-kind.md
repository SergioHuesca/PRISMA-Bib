# ADR 0030: OpenAlex Topics as a New Keyword Kind

## Status

Accepted — 2026-10-21. Accepted by the owner; the open questions were decided as recorded.

Amends the data model and the frozen DDL. The allowed values of `record_keywords.kind` change from
`author` and `index` to include `openalex-topic`, and the column gains a `CHECK` constraint. The
frozen BUILD_PLAN line 871 is the clause changed. Under §2.6, this record is the recorded deviation,
and BUILD_PLAN itself is not edited. It is the separate ADR that
[ADR 0028](0028-field-level-fallback-cascade.md) resolved item 5 requires.

## Context

[ADR 0028](0028-field-level-fallback-cascade.md) Decision 2 says OpenAlex topics are stored with
source `openalex-topic` and are never compared one-to-one with Scopus author keywords. Resolved
item 5 says they feed the keyword network as a new keyword kind, separate from `author` and
`index`, and that this needs an ADR amending the data model. This record is that ADR.

### The current contract, as written

`record_keywords` allows exactly two kinds. The contract exists in four places, and none of them
is a database constraint:

- `src/prismabib/store/schema.sql`, line 38, the DDL: `CREATE TABLE record_keywords (record_id TEXT,
  keyword_id TEXT, kind TEXT);  -- author|index`. The table declares no primary key. The `kind`
  column is plain `TEXT`, with no `CHECK` and no DuckDB `ENUM`. The same comment is in BUILD_PLAN
  line 871, which is frozen.
- `src/prismabib/store/load.py`, the `Corpus.keywords` docstring: "Not validated against that
  closed set here -- an unrecognised ``kind`` simply matches no rows, since
  ``record_keywords.kind`` is plain ``TEXT``, not a DuckDB ``ENUM``."
- `docs/architecture/data-model.md`, the `record_keywords` entry: `kind` ("author" or "index",
  per the capture source).
- The loader's writes, which are the only producers. `load.py` calls
  `_accumulate_keyword(..., kind="author")` for `record.author_keywords`, and
  `_accumulate_keyword(..., kind="index")` for `record.index_keywords`. The second call is
  unreachable today, because `_record_from_entry` hardcodes `index_keywords=[]`.

So no schema check enforces the set. A new kind would be accepted silently by the database today.
Decision 3 adds the check.

Readers of `record_keywords`:

| Reader | Kind it reads | Changed by this ADR? |
|---|---|---|
| `Corpus.keywords(kind="author", stage)` in `store/load.py` | caller's `kind`, default `"author"` | No default change |
| `keyword_frequency`, `keyword_evolution` in `bibliometrics/keywords.py` | `kind="author"` default | No |
| `keyword_cooccurrence_network` in `bibliometrics/network.py` | `kind="author"` default | No |
| `taxonomy/coder.py` lines 377-378 | `"author"` and `"index"`, literally | No; topics are not a rule field |
| `screening/ui.py` line 231 | `rk.kind = 'author'`, literally | No |
| Build statistics, `store/load.py` line 2094 | every kind, through `_count(connection, "record_keywords")` into `record_keyword_links_loaded` | **Yes, for topic-bearing stores** (Decision 4) |

Every analysis reader filters on one kind. The one reader that counts across all kinds is the
build report's `record_keyword_links_loaded`. It is a build statistic, not an analysis input, and
it changes for any store that contains topic rows.

`coauthorship_network` reads authorships, not keywords, and is outside this decision.

### The OpenAlex side already exists, and stores nothing

`src/prismabib/sources/openalex.py` already defines `KEYWORD_KIND = "openalex-topic"`, a
`TopicKeyword(name, score)`, and `topics_to_keywords()`. Its module docstring says "Nothing here
writes to the store ... persisting it needs a separate ADR (ADR 0028, resolved item 5)." This
record is that ADR, and it keeps the name already in code.

There is no OpenAlex capture writer and no OpenAlex Layer 0 directory. `grep` finds OpenAlex only
in `config.py` and `sources/openalex.py`. So topics cannot reach the store until the OpenAlex
payload capture of [ADR 0028](0028-field-level-fallback-cascade.md) Decision 3 exists. Decision 6
names ADR 0031 as the record that specifies it.

## Decision

### 1. Naming: one string, two roles

ADR 0028 names the source `openalex-topic` (Decision 2) and the kind "a new keyword kind" (resolved
item 5). This record makes them the same string. The source is OpenAlex topics. The
`record_keywords.kind` value is `'openalex-topic'`, and it records that source. The name is the
existing `KEYWORD_KIND` constant, so code, store and methods text use one spelling.

Topic names pass through the same `_normalise_keyword_term` as author keywords and use the same
`keyword_id` hash. One keyword dictionary serves every kind, so a topic named "Machine Learning"
and an author keyword "machine learning" share a `keywords` row and differ only in
`record_keywords.kind`. The raw form stays in `keywords.term_raw`, as ADR 0028 Decision 2 needs.

Topics are stored as `record_keywords` rows with no score. **No score column is added.** The
OpenAlex `score` is not part of the store, and the DDL change in Decision 3 is the CHECK only. The
score remains in the Layer 0 payload, where ADR 0031 governs it.

### 2. Topic networks are built on their own. No mixed networks

Topics are stored in `record_keywords` with `kind = 'openalex-topic'`, and a topic network is built
from that kind alone, through the existing `kind` parameter:
`keyword_cooccurrence_network(corpus, kind="openalex-topic")`. No signature changes.

Topics stay out of every existing keyword output by default, through the existing kind filter.
`Corpus.keywords` selects `WHERE rk.kind = ?`, and the default kind is `"author"`. A default call
therefore returns the rows it returned before this change, because no existing caller asks for a
topic. The rules are:

- **No function gains a default that reads topics.** Every keyword function keeps `kind="author"`.
- **No mixed author-and-topic network is built.** A co-occurrence graph combining two vocabularies
  is out of scope. Each network is single-kind.
- **Topic networks label themselves.** `kind` is already a scalar entry in `params`, so the
  caption shows `kind=openalex-topic`, and the figure export records the same value.

The functions that read kind, and the requirement on each:

- `bibliometrics/keywords.py`: `keyword_frequency` and `keyword_evolution`. Default output
  unchanged.
- `bibliometrics/network.py`: `keyword_cooccurrence_network`. Default output unchanged.
- `store/load.py`: `Corpus.keywords`. Its `kind` parameter already accepts any string, and its
  docstring changes from "``author`` or ``index``" to the allowed set.
- `taxonomy/coder.py`: reads `author` and `index` literally. Unchanged, and it stays so, because a
  topic is not a taxonomy rule field.
- `screening/ui.py`: reads `author` literally. Unchanged.
- `viz/dashboard.py` and `viz/figures.py` call the bibliometrics functions with their defaults.
  They inherit the guarantee.

### 3. The schema change: a CHECK constraint, as an amendment to a frozen clause

**This changes frozen DDL.** BUILD_PLAN line 871 reads `CREATE TABLE record_keywords (record_id TEXT,
keyword_id TEXT, kind TEXT);  -- author|index`, and `schema.sql` declares that its tables are
reproduced from the frozen spec and that changing one needs the spec updated first. Under §2.6 this
record is the recorded deviation, and BUILD_PLAN is not edited. The amendment is:

- **DDL:** `kind TEXT CHECK (kind IN ('author', 'index', 'openalex-topic'))`. Columns and types are
  otherwise unchanged. No table is added.
- **Enforcement:** the database rejects an undocumented kind at insert. This replaces the
  convention in Context, which the database did not enforce. The loader's `index` write stays
  valid, because `'index'` is in the set.
- **Allowed set:** written once, as a constant in `store/`. The DDL and the constant must match, and
  a test asserts it (Decision 7). `schema.sql`, the `Corpus.keywords` docstring,
  `data-model.md`, and `limitations.md` (which says `record_keywords` never contains a row of kind
  `"index"`, which stays true) are updated in the implementation pull request, not in this one,
  because until the implementation exists they would be false.
- **Loader ordering:** `record_keywords` has no primary key (`schema.sql` line 38), so its row
  order is not defined by the table. The order dependence is in `keywords`. `_accumulate_keyword`
  uses `acc.keywords.setdefault(keyword_id, ...)` (`load.py` line 1173), which keeps the first-seen
  `term_raw`. The first-seen form therefore follows the order in which the loader meets each term.
  Two rules hold this stable:
  - **Topics are accumulated after the Scopus pass.** An author keyword's `term_raw` then wins over
    a topic with the same normalised term, and no existing keyword's displayed form changes.
  - **Topic entries are sorted before accumulation**, by normalised name within each record, and
    records are visited in their sorted order. OpenAlex serves `topics` in its own array order,
    and that order is not part of the contract. A sort makes the winning `term_raw` for a
    topic-only term independent of payload order.

### 4. Migration: none in place. A store is rebuilt from Layer 0

`store/db.py` says a store "is a build artefact, not a migrated database". The CHECK applies at
table creation, so a store built by the new code has it, and a store built before has none. No
migration is written, and the rebuild is the only way a store gains topic rows or the constraint.
No schema version is added, and a pre-change store is not refused.
`_refuse_stale_schema` checks table presence only, and it does not detect this change.

**The build report states the topic count per kind.** `record_keyword_links_loaded`
(`load.py` line 2094) counts every kind. For a topic-bearing store it therefore changes, and the
build report gains a per-kind count, so a reader sees how many topic links were loaded. A store built
before the change reports no `openalex-topic` count. That is visible, not silent.

### 5. Checksums and the frozen reference fixture

The checksum is a hash of row content, not of DDL. `store/checksums.py` `table_checksum` runs
`SELECT *` in sort-key order and hashes the cells (`checksums.py`, lines 105 to 134). Nothing
hashes `schema.sql`, so the CHECK does not move any checksum by itself.

- **A store with no topic rows keeps every table checksum.** This includes every Scopus-only
  store and the frozen reference fixture.
- **A store with topic rows changes `record_keywords` and `keywords`.** `record_keywords` gains
  rows, and `keywords` gains a row for each topic-only term. Every other table is unchanged. The
  change is confined to new, topic-bearing stores. `record_keywords` sorts by
  `(record_id, keyword_id, kind)`, so the new rows sort deterministically.

**BUILD_PLAN line 901** requires `build_store(project, rebuild=True)` on the same Layer 0 input to
produce a byte-stable set of table checksums. This change keeps it. The reference fixture has no
OpenAlex data: `grep` for "openalex" under `tests/fixtures/projects/reference/` finds nothing.
`tests/fixtures/openalex/` holds hand-written client cassettes, and its README says they are not
Layer 0 store input.

**The reference fixture does not need to be regenerated.** The golden
`tests/golden/store/__snapshots__/reference_table_checksums.json` and
`tests/golden/report/__snapshots__/reference_numbers.json` stay as they are. BUILD_PLAN line 927
says golden checksums are recomputed "only when the reference fixture or the loader semantics
deliberately change". Neither does here, for Scopus-only input. The implementation pull request
must say so in its description, under the rule in BUILD_PLAN line 927.

A test of topic-bearing stores uses a **new** fixture, or a project built in `tmp_path`. It does
not touch the frozen reference fixture, which BUILD_PLAN line 536 freezes and versions.

### 6. Prerequisite: the OpenAlex Layer 0 capture is specified in ADR 0031

The rebuild test in Decision 7 cannot exist until OpenAlex payloads are Layer 0 data. ADR 0028
Decision 3 requires that they be written under `raw/` with a sealed manifest and the checksum
pattern of `capture/writer.py`. The owner has chosen ADR 0031
as the home for that specification, covering the directory layout and its
`capture/layout.py::NON_RUN_DIRNAMES` entry, following the
[ADR 0011](0011-abstract-retrieval-for-subject-areas.md) precedent. ADR 0031 specifies it. This record
depends on it and does not settle its layout.

### 7. Verification the implementation must include

Each test must be shown to fail on the defect it guards, following the project rule that a test
covering a line proves nothing until it fails on the original defect.

1. **Schema test.** `test_record_keywords__kind__rejects_undocumented_value`: inserting a `kind`
   outside the set raises a constraint error. This fails on the current DDL, which has no CHECK.
   `test_record_keywords__kinds_written__are_in_the_documented_set`: the kinds the loader writes,
   and every kind in a built store, are inside the allowed-set constant, which matches the DDL and
   the documentation.
2. **Default output is unchanged.** Build a Scopus-only store and a topic-bearing store from the
   same Scopus input. Assert that `keyword_frequency`, `keyword_evolution` and
   `keyword_cooccurrence_network` with defaults give the same `data`, `params`, `params_json`, and
   caption bytes on both. Also assert that the existing golden tests (`reference_numbers.json`,
   the VOSviewer export, the caption tests) pass with no snapshot change. This fails if a default
   is changed to read topics.
3. **Rebuild from Layer 0.** Build a topic-bearing project, delete `corpus.duckdb`, rebuild, and
   assert that the table checksums are identical and the topic rows are equal. This mirrors
   `test_build_store__after_deleting_db__reproduces_identical_checksums`.
4. **Term precedence and order.** A term that is both an author keyword and a topic keeps the
   author keyword's `term_raw`. Shuffling the topic array order within a payload does not change
   any `term_raw`. Each test fails if the pass order or the sort is removed.
5. **Build report.** The report states the `openalex-topic` count, and a Scopus-only store reports
   none.
6. **Runs in CI.** The suite runs with `pytest -n auto` and in a single process, and the
   topic-bearing check runs on Linux and Windows.

## Alternatives considered

**A separate table, `record_topics`.** Rejected by owner decision. Topics are stored as a kind in
`record_keywords`. The separate table would have given isolation by construction, but it duplicates
the keyword dictionary and its normalisation, needs a second `Corpus` method, and adds a table.

**Merge topics into author keywords.** Rejected. ADR 0028 Decision 2 says topics are never compared
one-to-one with author keywords, and a merged kind would change every default network silently.

**Do not store topics.** Rejected. Topics would then exist only in Layer 0 and could feed a network
only by re-querying OpenAlex. A live query is not reproducible from the store (ADR 0028 resolved
item 7).

**A mixed author-and-topic network.** Rejected by owner decision. It would combine two vocabularies
in one co-occurrence graph. Topic networks are built on their own.

**Keep the kind enforced only by tests.** Rejected by owner decision. A test catches an undocumented
kind after the fact, and the CHECK refuses it at insert. The cost is a change to frozen DDL, which
this record states as an amendment.

## Consequences

1. Topics become a queryable keyword source with the same normalisation and dictionary as author
   keywords. No default output changes.
2. The frozen DDL changes by one CHECK, recorded here as the §2.6 amendment to BUILD_PLAN line 871.
3. A pre-change store has no `openalex-topic` rows and no CHECK, and the build report says so
   (Decision 4). Its analysis outputs are unchanged.
4. A record with no topic rows is either "OpenAlex has no topics for it" or "OpenAlex was never
   asked". `record_keywords` cannot tell these apart, the same ambiguity
   [ADR 0011](0011-abstract-retrieval-for-subject-areas.md) resolves for subject areas. The per-field
   outcome of ADR 0028 must carry it, and the implementation must not infer it from the presence of
   rows.
5. The frozen reference fixture and its goldens do not change. The BUILD_PLAN line 901 criterion
   holds without regeneration.
6. The build report's `record_keyword_links_loaded` changes for topic-bearing stores, and
   `keywords_loaded` may change with it.

### Published outputs the Scopus-only claim covers

The claim is that this change moves no value in an output produced from a Scopus-only store. The
outputs it covers, each checked against the code:

- `numbers.json` from `prismabib export` on a Scopus-only store, the artefact ADR 0027 Decision 3
  tests across Linux and Windows;
- the keyword outputs `keyword_frequency`, `keyword_evolution` and `keyword_cooccurrence_network`
  with default arguments, including their `data`, `params`, `params_json` and captions;
- the VOSviewer export of the co-occurrence network (`_export_vosviewer`);
- the taxonomy coder output, which reads `author` and `index` only;
- the screening view's author-keyword listing (`screening/ui.py`);
- the golden snapshots `tests/golden/report/__snapshots__/reference_numbers.json` and
  `tests/golden/store/__snapshots__/reference_table_checksums.json`; and
- the build report's per-kind counts for a Scopus-only store, which show no topic count.

Outputs from a topic-bearing store are outside that claim. They are reproducible from recorded
Layer 0 payloads (ADR 0028 resolved item 7), and they are not covered by the frozen-fixture
cross-runner test until a topic-bearing fixture is added to that check.

### 8. Narrowing of ADR 0027 Decision 3 (owner decision)

**The owner has decided this narrowing. It takes effect when this record is accepted.**

ADR 0027 Decision 3 treats `numbers.json` as the artefact that must be byte-identical across two
runners for the published corpus. From acceptance of this record, that claim is limited to
Scopus-only stores. Topic-bearing stores are **outside the byte check** until a topic-bearing
fixture exists and has its own cross-runner test. Until then, no topic-based number may be cited
as covered by the ADR 0027 byte check.

## Open questions for the owner

Decided by the owner, and recorded above as final:

- a kind in `record_keywords` rather than a separate table (Decision 1 and Alternatives);
- no mixed author-and-topic network (Decision 2);
- a CHECK constraint as a §2.6 amendment to BUILD_PLAN line 871 (Decision 3);
- the build report states the topic count per kind, with no refusal and no new schema version
  (Decision 4);
- the first-seen `term_raw` precedence (Decision 3);
- the OpenAlex Layer 0 capture is specified in its own ADR, 0031 (Decision 6);
- no topic score is stored, and no score column is added (Decision 1); and
- the narrowing of ADR 0027 Decision 3 to Scopus-only stores (Decision 8).

No open questions remain for this record. ADR 0031, which specifies the OpenAlex capture, is in PR #146
and is not yet on `main`.

## Related decisions

- [ADR 0011](0011-abstract-retrieval-for-subject-areas.md): a Layer 0 run kind nested under `raw/`
  and skipped by name, the precedent ADR 0031 follows.
- ADR 0031: the OpenAlex Layer 0 capture, which Decision 6 names and which this record does not write.
  It is in PR #146 and is not yet on `main`, so it is not linked.
- [ADR 0022](0022-the-analysis-result-contract-and-its-provenance.md): `params` is part of the
  analysis result, which is why the topic network records its `kind` as a scalar.
- [ADR 0027](0027-what-v1-0-0-claims.md): the cross-runner criterion that Decision 3 of this record
  narrows to Scopus-only stores (Decision 8).
- [ADR 0028](0028-field-level-fallback-cascade.md): the cascade whose Decision 2 and resolved item 5
  this record implements, and whose item 7 bounds the reproducibility claim.
