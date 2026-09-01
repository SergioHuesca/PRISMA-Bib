# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.15.0] — 2026-09-01

### Added

- **`subject_areas` works.** Sealed `raw/abstracts/` runs now load into Layer 1, so a project
  that has run `prismabib enrich` can filter on ASJC subject areas and report the exclusion
  in its PRISMA diagram as `by subject area: N`. This is the Layer 1 half of
  [ADR 0011](docs/architecture/adr/0011-abstract-retrieval-for-subject-areas.md), which was
  split into two PRs in v0.8.0 and of which only the Layer 0 half shipped — enrichment has
  been writing payloads nothing read for four releases. See
  [ADR 0018](docs/architecture/adr/0018-abstract-runs-in-layer-1.md) and #31.

- **Two new Layer 1 tables** (third addition to the frozen schema, after ADRs 0012 and 0013):
  `abstract_runs` carries each enrichment run's provenance including its `payload_sha256`, and
  `record_subject_area_coverage` records per record whether Scopus assigned areas, assigned
  none, returned 404, or refused with 403. **A record with no coverage row was never asked** —
  that third state is why the table exists. "We restricted to computer science and this paper
  carries no classification" and "we restricted to computer science and never looked this
  paper up" are different methodological claims, and `subject_areas` alone could not tell them
  apart.

  `runs` gains no row from an abstract run, so `identified` is unaffected — an abstract run
  identifies nothing (S02-AC5), and a test asserts it.

- **A half-enriched corpus is refused rather than filtered blind.** Once enrichment has been
  run at all, a record that was never looked up makes `subject_areas` unenforceable and the
  engine raises, naming how many records remain and that enrichment is resumable. Without
  this, such a record passes the filter for the same reason a genuinely unclassified one
  does, and the diagram reports one "excluded by subject area" figure computed over an
  unknown fraction of the review — an exhausted quota, an interrupted run or a `--budget`
  cap all produce that state. A corpus that was never enriched is *not* refused on these
  grounds: sparse data is what the "no data, so passes" branch deliberately tolerates.

### Changed

- `prismabib build` reports the new counts, and `StoreStats` gains `abstract_runs_loaded`,
  `record_subject_area_coverage_loaded` and `unmatched_abstract_record_ids` — a record present
  in an abstract run but absent from `records` is skipped and counted, never silently dropped. On the `rebuild=False` reuse path that field has no table to be read back from, so
  `prismabib build` says so explicitly instead of printing nothing — a line that appears only
  when non-empty cannot be told apart from "nothing was skipped".

- **Subject-area extraction from an abstract payload takes `@code` only**, never the
  `@code or $` fallback the search-entry path uses. `capture.enrich` already treats an entry
  without a code as *no* evidence of codes and seals such a record `no_subject_areas`; the
  lenient reader made the layers contradict each other, storing `"Artificial Intelligence"`
  as an area code, marking the record `assigned`, and then **excluding** a record Layer 0 had
  said to keep.

**No published number moves.** The reference fixture declares `subject_areas: []` and has no
abstract runs, so every golden value is byte-identical; `reference_table_checksums.json` gained
two empty-table digests and altered nothing.

## [0.14.0] — 2026-09-01

### Fixed

- **`subject_areas` matched nothing, and would have inverted the filter the moment anyone
  enriched a corpus.** Scopus returns each subject area as `@code: "2202"` *and*
  `@abbrev: "ENGI"`; Layer 1 stores `@code`, while `criteria.yaml` teaches — and researchers
  write — the four-letter form. `{"2202"} & {"engi"}` is empty, so the comparison could never
  succeed. Because a record with *no* subject-area data passes by design, the result was
  precisely backwards: every **enriched** record excluded, only records that failed to enrich
  kept. On a four-record corpus with `[COMP, ENGI, MATH, MULT]`, three were excluded where one
  should have been.

  Both sides are now normalised to ASJC's four-letter grouping through a new checked-in table,
  `src/prismabib/asjc.py`. See
  [ADR 0017](docs/architecture/adr/0017-subject-areas-match-by-asjc-grouping.md).

  **This does not make `subject_areas` usable.** `prismabib enrich` writes Abstract Retrieval
  payloads into `raw/abstracts/`, but `store/load.py` excludes that directory by name and reads
  no abstract payloads, so the filter still has no data and is still refused — ADR 0011 split
  its work into a Layer 0 PR and a Layer 1 PR, and only the first shipped. This fixes the
  comparison, which was independently wrong; the Layer 1 loader is separate outstanding work.
  Running `prismabib enrich` today spends about one quota call per record and changes nothing
  observable.

- **An unmappable subject-area code no longer excludes a whole corpus in silence.** A store
  whose rows are all values the ASJC table cannot map — an entry that arrived without `@code`,
  so the loader stored the human-readable name, or a grouping ASJC adds later — passed the
  "does any record carry a row?" guard and then failed every comparison: whole corpus excluded,
  `by subject area: N` on the diagram, no error. The guard now demands a row it can actually
  use, and unmapped values are logged as `prisma.subject_areas.unmapped`.

- **`replay()` could resolve criteria that were never in force.** `_criteria_at_commit`
  swallowed a validation failure and walked to an older commit, so if two commits declared the
  same `version` and the newer one no longer validated, replay silently recomputed the review
  under the older one. It now raises when the failing commit declares the version being
  searched for — that tolerance was meant for commits declaring *other* versions.

### Added

- **`criteria.yaml` refuses a subject-area code ASJC does not define.** `COMPUTER` matches no
  record and would narrow a review to nothing while reading as a deliberate restriction;
  `1702` names one category but can only be matched at its grouping, so it would silently
  widen to all of `COMP`. Both are refused at load, with the reason, rather than producing a
  plausible wrong number later.

### Changed

- The `criteria.yaml` template no longer says `subject_areas` is "NOT YET ENFORCEABLE" and no
  longer recommends server-side `SUBJAREA(...)` instead. It explains that the filter requires
  `prismabib enrich`, that enrichment must precede screening, and that filtering here rather
  than in the query is what makes the exclusion **reportable** — a record excluded by a
  server-side `SUBJAREA` is never identified, so it can never appear in the flow diagram.

## [0.13.0] — 2026-09-01

### Added

- **Automated exclusions are now reported with reasons.** The PRISMA diagram, `numbers.json`
  and `prismabib flow` break `excluded_automated` down into `year`, `subject_area`,
  `doc_type` and `venue` instead of printing one combined figure. PRISMA 2020 asks for
  exclusions to be reported with reasons, and a single number cannot do that: on this
  project's own corpus the combined 754 turned out to be 673 by year and 81 by document
  type, with the declared subject-area restriction excluding **nothing** — a filter reported
  in the methods section that never removed a record. See
  [ADR 0016](docs/architecture/adr/0016-automated-exclusion-reasons.md).

  A record that fails several criteria at once is attributed to **the first criterion it
  fails**, so the four counts sum to the total rather than exceeding it (on the fixture
  built for this, a naive count of every failure gives 20 against a true total of 10).
  `FlowCounts.assert_consistent()` gains equation 5 to check exactly that; equations 1-4
  keep their numbers. Every reason key is always present, reporting `0` when a criterion
  excluded nothing, so "not filtered" and "filtered, excluded nothing" stay distinguishable.

### Changed

- **`FlowCounts` gains `excluded_automated_by_reason`** (third deviation from the frozen
  contract, after ADRs 0007 and 0013). No existing field or equation changes, and **no
  published number moves** — the goldens gained four keys and altered none.
- `docs/assets/prisma-flow-example.svg` regenerated, since the figure gained four lines.
  Re-read by hand afterwards: 120 identified, 24 excluded (all conference-whitelist), 96
  after automated filters, 5 included.

### Fixed

- **Diagram boxes no longer overflow.** Box height was a fixed constant sized for two- and
  three-line boxes; the six-line automated box pushed its heading and its only non-zero
  count *outside* the rect, drawn over the arrows — in `prismabib export`'s output and in
  the figure shipped in the docs. Height is now derived from the line count, and a test
  asserts every text baseline lies inside its own box. Nothing caught this because the
  diagram tests read the text elements' content and the golden compares bytes against a file
  produced by the same renderer; both are blind to geometry.
- **`assert_consistent()` now rejects a negative per-reason count** (`-5 + 15 == 10` closes
  equation 5 happily) **and a breakdown whose key set is not exactly the four reasons** (a
  dropped zero-valued reason also sums correctly). Previously `"flow.excluded_automated.year":
  -5` could reach `numbers.json` and the published figure.
- **The source CSV beside the figure carries the reason breakdown.** `_flow_source_rows`
  skipped the mapping field, so `prisma_flow.csv` held four fewer numbers than
  `prisma_flow.svg` showed, violating S10-AC1. The round-trip test walked CSV → SVG only and
  could not see it; it now walks both directions.
- `docs/getting-started.md` and `docs/how-to/run-the-demo.md` showed `prismabib flow` output
  the tool no longer produces, and `test_docs_examples.py` parsed those blocks positionally
  in a way that would have failed had they been corrected — the suite was enforcing that the
  documentation stay wrong. The parser now reads the breakdown from the page and checks it
  sums.

## [0.12.0] — 2026-09-01

### Added

- **`prismabib enrich <slug>`** — the CLI command for Scopus Abstract Retrieval. The
  enrichment has shipped since v0.8.0 (ADR 0011) and was reachable only from Python, so a
  researcher whose `criteria.yaml` restricts `subject_areas` met a documented feature they
  could not invoke. `--budget` spends a known slice of a weekly quota; the run is resumable.

### Fixed

- **Abstract Retrieval addressed the wrong endpoint and had never worked against real
  Scopus.** A prismabib record id is `scopus:2-s2.0-<digits>` (BUILD_PLAN §3.2) — an
  **EID** — and the client sent it to `/content/abstract/scopus_id/`, which expects the
  bare numeric id. Measured on 2026-09-01, same record, same key:
  `/scopus_id/2-s2.0-105044913252` → **404**, `/scopus_id/105044913252` → **200**,
  `/eid/2-s2.0-105044913252` → **200**. It now uses the `/eid/` path, which takes exactly
  what the store holds.

  Shipped in v0.8.0 and undetected for three releases because **every test mocked whatever
  URL the client sent**: `tests/integration/capture/test_enrich.py` restated the endpoint
  prefix as a literal instead of deriving it from `ScopusClient`, so 22 tests agreed with
  the client rather than with Elsevier. The prefix is now derived, and
  `test_abstract__url__addresses_the_eid_endpoint_with_the_stored_identifier` asserts the
  URL against the API's contract rather than against our own mock.

- **A run whose every request 404s now stops instead of spending the quota.** The 404s
  above were recorded as withdrawn records and the run carried on. On the corpus this was
  found with, that meant 1,864 calls — an entire weekly quota — to seal a run with zero
  subject areas and a manifest asserting that every record in a live corpus had been
  withdrawn from Scopus. Recording a falsehood about the corpus is worse than failing.

  A run that has never once succeeded now stops after
  `CONSECUTIVE_NOT_FOUND_LIMIT` consecutive 404s, writes nothing, seals nothing, and raises
  an error naming both possible causes — a 404 alone cannot distinguish them — with the
  one-step way to tell them apart: request a record id straight out of a fresh
  `prismabib search` result. A handful of genuinely withdrawn records still completes the
  run, which the control test pins.

## [0.11.0] — 2026-09-01

### Added

- **Stage 10: export and reporting — a researcher can now complete a review.** Capture,
  build, screen, `prismabib export`, `prismabib fill`. This is the release the amended
  plan moved ahead of Stages 6–9 to reach ([ADR 0015](docs/architecture/adr/0015-stage-order-and-stage-10-scope.md)),
  because a PRISMA review is complete without full-text resolution, bibliometrics or a
  taxonomy, and is not complete without something citable to show for the screening.
- **`prismabib export <slug>`** writes `exports/`: the PRISMA 2020 diagram with its source
  CSV beside it, four tables as CSV/Markdown/LaTeX `booktabs`, `numbers.json` and
  `manifest.json`.
- **`numbers.json`, the anti-drift mechanism.** A manuscript never contains a literal
  count; it contains `{{flow.included}}`, substituted at build time. Every value is a JSON
  scalar — a list has no sensible rendering inside a sentence — and the key set is
  snapshotted, so adding or removing a key is a reviewable diff rather than a surprise.
- **`prismabib fill`** substitutes those placeholders and **exits non-zero in both
  directions**: on a key the manuscript cites and `numbers.json` does not define, *and* on
  a key `numbers.json` defines that the manuscript never cites. The second is the drift
  nobody notices — a number that stopped being cited, whose claim may have been edited away
  with it. Placeholders inside fenced code blocks are left alone.
- **`manifest.json` records the git commit of prismabib itself**, not the project
  directory, because that is what makes a number traceable to a published state of the
  code (BUILD_PLAN line 450). A dirty tree sets `dirty: true` and warns; a commit on no
  remote branch sets `commit_is_pushed: false` and warns separately, since `dirty` does not
  cover it and an unfetchable SHA is just as untraceable.
- **The PRISMA diagram is hand-written SVG, not a plotting library.** Rasterised output
  embeds font metrics and library versions, so the same counts render to different bytes on
  a different machine — which Stage 11's "clean clone on a different machine" criterion
  would then have to exclude. Text SVG with explicit geometry has no such dependency.
- **`Project.title`**, read from `project.toml`. Written since Stage 1 and never read: a
  PRISMA diagram captioned with a slug is not something a manuscript can carry.
- **The `gates` CI job**, enforcing the per-module coverage floors of BUILD_PLAN §3.7.6.
  `[tool.prismabib.coverage_gates]` has declared them since Stage 2 while nothing enforced
  them — only the global `fail_under = 85` ran, so a module could sit far below its own
  stated floor with CI green. Every declared gate passes today; the job is non-required
  initially, on the `e2e` precedent.

### Fixed

- **`venues.total` counted venue *rows*, not venues.** The store writes one `venues` row
  per record, so the reference corpus reported **120 venues** for a corpus published across
  **22** — a plausible wrong number, sitting beside a `venues.top1.count` of 96, and frozen
  into a golden as ground truth. It counts `DISTINCT name` now.
- **`numbers.json` and the top-venues table used two different definitions of "a venue"** —
  `name` versus `(name, venue_type)` — so a venue Scopus indexes under two aggregation
  types appeared as one venue in the prose and two rows in the table beside it. Both group
  on `name`; the table reports `mixed` when Scopus disagrees with itself, and
  `tests/integration/report/test_bundle_consistency.py` now compares the two paths.
- **`prismabib fill` escapes LaTeX specials in string values for a `.tex` manuscript.**
  `numbers.json` carries venue names, and "Robotics & Automation" substituted raw aborts
  `pdflatex` at the citing sentence. `tables.py` had escaped its generated tables from the
  start, so one export could produce a table that compiled beside a sentence that did not.
  The escaping is imported from `tables.py` rather than reimplemented.
- **`numbers.json` that is not a JSON object is rejected readably.** A bare number raised
  `TypeError: argument of type 'int' is not iterable`; a bare *string* was worse, since
  iterating it yields characters and `fill` reported single letters as unused keys while
  appearing to work.
- **The CLI export summary printed `None` instead of `(none)`** for a missing commit:
  `str(None)` is truthy, so the fallback was dead in exactly the case it existed for.

### Changed

- **`FlowCounts.assert_consistent` is now visible to mutation testing.** Its equations moved
  to a module-level function, because mutmut does not mutate the body of a decorated class
  and `FlowCounts` is a `@dataclass(frozen=True)` — so the check that decides whether a
  published diagram adds up generated **zero** mutants while showing 100% line and branch
  coverage. It now generates 51, of which 33 die; the 18 survivors are all string labels in
  the error message. Done before the export contract froze around `FlowCounts`, which is
  when it stops being a one-class change (raised as part of #23).


### Fixed

- **The mutation gate is measured over mutants that can change behaviour.** The weekly
  mutation workflow fired for the first time on 2026-08-31 and failed at 81.40% against an
  85% gate — the project's first real measurement, not a regression. Triage split it
  cleanly: **92.92% on the 805 mutants that can change behaviour, 37.91% on 211 that only
  rewrite string literals.** After this change the gate reads **91.04%** (802 killed plus
  one timeout of 882 considered, nothing unchecked). mutmut generates three mutants per string segment (an `XX…XX`
  wrap, a lowercased copy, an uppercased copy), so one carefully written error message
  produced 54 mutants and 44 survivors — a quarter of every survivor in the project.
  Killing those means asserting messages verbatim, character and case, which breaks on
  every rewording and catches no defect. Reaching 85% needed 37 of the 57 structural
  survivors killed, and triage found only 20–26 of them killable — so the gate was
  *probably* unreachable, by a handful of mutants, on a triage judgement. **An earlier
  draft of this entry said "killing every structural survivor still reaches only 84.8%,
  so the gate was unreachable"; that arithmetic was wrong** — the correct figure is 87.0%,
  and the error flattered the conclusion. ADR 0014 records it rather than quietly dropping
  it. Only diagnostic message bodies are exempt, per statement; the condition that decides
  whether to raise, and every short semantic string (`"shared"`/`"exclusive"` lock modes,
  `"--format=%H"`, stage names, decision values, reason codes) stay mutated. SQL was in an
  earlier draft and was removed: a pragma suppresses by line, and the SQL sits inside the
  statement that runs it, so exempting it also exempted `row = connection.execute(...)` →
  `row = None` — which makes `identified` in the published PRISMA diagram silently zero.
  The 85% threshold is unchanged. See [ADR 0014](docs/architecture/adr/0014-mutation-gate-excludes-diagnostic-prose.md).
- **`MonotonicUlidFactory`'s randomness-overflow branch was untested and wrong-proof.**
  Reaching it takes roughly 2**80 calls, so no test could, and it silently accepted a
  mutation setting `timestamp_ms = 1` — stamping every later event one millisecond after
  the Unix epoch and destroying the ordering the decision log's fold depends on. The
  randomness source is now injectable so the branch is reachable, and the boundary is
  pinned from both sides: landing exactly on the mask must *not* bump the millisecond.
  The id's timestamp is also asserted to be the real Unix millisecond, which a merely
  monotonic conversion would not be.
- **An ambient `GIT_DIR` resolved a superseded `criteria.yaml` from another repository**
  and returned it as this project's protocol history — verified per parameter by
  injection: the decoy's `year_start` comes back, silently, because
  `rev-parse --show-toplevel` still answers with the project while `git log` reads the
  decoy's objects. `GIT_WORK_TREE` fails loudly instead. Both are wrong; only one is
  quiet. `_git_environment` had always dropped `GIT_DIR`/`GIT_WORK_TREE`;
  nothing asserted it, and removing the `env=` argument entirely left the suite green.
- **The checksum sidecar's covered prefix is accumulated across lines**, and the only test
  covering it left a one-line prefix, where accumulating and replacing are
  indistinguishable. Replacing it turns an interrupted append into a tampering report —
  opposite recovery instructions, delivered to a reviewer who has just lost power partway
  through screening.

## [0.10.0] — 2026-08-31

### Added

- **Screening (Stage 5): a keyboard-first Panel view over a deterministic, resumable
  queue.** `screener(project, stage=..., reviewer=...)` renders in a notebook cell and
  serves under `panel serve` from the same construction. `i` includes, `e` then a digit
  excludes under a numbered reason code, `u` marks unsure, `n`/`p` navigate, `z` undoes,
  `?` shows the map — all delivered by a document-level listener, so the bindings work
  without the mouse entering the widget. Every resolving keystroke is appended and
  `fsync`ed before the view moves on.
- **The reason palette is rendered from `criteria.yaml`**, numbered `1..9` in declaration
  order. Adding a code to the protocol surfaces it with no code change, which is what
  keeps the UI from offering an exclusion the decision log would refuse.
- **Author names and citation counts are omitted by default** (`blind=True`). Both move
  human judgement and neither is an eligibility criterion, so they are left out of the
  view model rather than hidden in the rendering — a field that was never put in the
  model cannot be restored by a later change to the markup.
- **Progress, pace and ETA**, measured over the *current session* rather than the whole
  log: re-opening a half-finished review must not report months of screening as though it
  had happened since the notebook was opened. Elapsed runs from the session's start, not
  from its first decision, and no rate is shown at all for the first thirty seconds —
  early on, the denominator is small enough that one fast keystroke dominates it, and a
  reviewer reading "hundreds per minute" a moment after their first decision has no
  evidence of their own to contradict it. Changing a decision you already made is not a
  second decision. BUILD_PLAN calls this display "what sustains a multi-hour task", which
  only holds while the numbers are true.
- **`ScreeningQueue`** — the pure-logic half: an order seeded from the project slug and
  uncorrelated with citation count, stable as the corpus grows between captures, resumable
  per reviewer from the log, with `unsure` deliberately not resolving and `undo` appending
  a superseding reversal rather than editing an append-only file.
- **`z` reverses the record on screen**, including after `p`. The status line names the
  current record's existing decision, so stepping back to re-read something already
  decided is no longer blind and the reversal is visible where it landed.
- **`test_notebook__01_screen_title_abstract__executes`** — BUILD_PLAN names this test and
  the `notebook` marker was registered for it, but nothing in the suite ran the notebook:
  it was executed only by the non-required `notebooks` job, so renaming a keyword could
  break a researcher's first five minutes with every required check green. Sockets stay
  banned for it except on loopback, which a Jupyter kernel needs and Scopus is not.
- **`docs/reference/` gained its `screening/` section**, which that page's own header
  calls a visible omission that `mkdocs build --strict` cannot catch.
- **The view actually renders under `panel serve`.** Panel derives the Bokeh model's type
  name from the component class's `__name__`, and a leading underscore produces a type the
  browser cannot resolve — at which point Bokeh fails the *whole document* and the reviewer
  gets a blank page, with the reason only in a console they have no reason to open.
  `_KeyboardBridge` was the entire defect. Nothing caught it because nothing in the suite
  loads the page in a browser; driving a real one also verified the keyboard end to end for
  the first time.
- **`docs/getting-started.md` documents the screening step**, with a screenshot. It said
  "there is no screening UI yet" in three places.

## [0.9.0] — 2026-08-28

### Changed

- **`identified` now sums `total_results` across a project's distinct searches**
  (ADR 0013), instead of taking the earliest run's alone. A real corpus with two
  different search strings reported 651 against a store of 1,864 records, drove
  `excluded_automated` negative, and made `assert_consistent()` fail permanently
  with a remedy that could not work. The sum is over *distinct queries* — one
  total per query, the earliest — so re-running a search to refresh citation
  counts still cannot inflate the number, which is what the previous rule
  existed to protect.
- **`FlowCounts` gains `duplicates_across_searches` and `removed_other_reasons`**,
  PRISMA 2020's two "records removed before screening" lines. Both are needed:
  summing alone leaves equation 1 off by the entries `build_store` could not
  load. `prismabib flow` renders them as their own block.
- Equation 1 becomes `identified - duplicates_across_searches -
  removed_other_reasons - excluded_automated == after_automated`.

### Added

- Layer 1 table `run_duplicates` (ADR 0013), counting papers a run re-found that
  an earlier run **under a different query** had already loaded. Measured during
  the load rather than derived: `records.run_id` keeps only the first run that
  loaded a record, and deriving the figure as a remainder would make equation 1
  close by construction — absorbing a manifest that disagrees with its own
  corpus, which is the defect that equation exists to catch.

### Note

A single-search project's numbers are unchanged; verified against the reference
fixture, whose golden gains one table checksum and no changed value.

### Changed

- **A malformed Layer 0 entry no longer aborts the whole load.** `build_store` raised when
  any entry lacked `dc:title` or `prism:coverDate` — fields Scopus always sends. A real
  capture returned 1,945 records with exactly one missing `dc:title`, and the other 1,944
  became unloadable with no way forward: Layer 0 is immutable, and re-capturing means a
  drifted index. Such an entry is now skipped, and `build_store`'s documented
  `Raises: ValidationError` contract is gone with the behaviour.
- **`build_store` raises `StoreError` on a capture nothing can be loaded from.** Skipping
  is right for an entry and wrong for a capture: with `dc:title` stripped from all 120
  reference entries the build returned normally with `records_loaded=0`, and
  `prismabib build --rebuild` exited `0` printing `records 0` and a next-step hint. It now
  refuses when every entry was skipped, or when more than 5% were and at least 10 were, and
  leaves **no** store behind — a store that exists is one the next `prismabib build` reuses
  and reports as clean. See ADR 0012 for the thresholds.
- **The `prismabib build` summary renders skipped entries.** They were reported only through
  a structlog warning that scrolls past above the summary, while `unmapped_country_values` —
  which loses no record — got a rendered line.
- **Log volume is capped.** `store.load.malformed_entries_skipped` names at most 20
  references plus a `truncated` flag, and `store.build_store.complete` carries
  `malformed_entries_skipped_count` instead of the full tuple. At 1,945 skips the previous
  shape was 1,946 lines, the last one enormous.
- **A skipped re-capture's citation snapshot is kept when its record is in the store.** The
  count is present, parseable, and independent of the field that failed; discarding it left
  a citation trend reading "5 as of January, nothing since" for a record whose February
  count had been captured and parsed. Kept only when some run loaded the record — the schema
  declares no foreign keys, so an orphan snapshot would be caught by nothing.

### Added

- **`StoreStats.malformed_entries_skipped`** — `"<run_id>/<page>:<line>"` for every Layer 0
  entry the loader could not turn into a record, covering both a missing `eid` and an
  unparseable required field. It counts *entries*, not records: an entry skipped for a paper
  an earlier run already loaded costs no record.
- **A `malformed_entries` table in Layer 1** (ADR 0012), which is what makes that field
  honest on the default path. It was an in-memory tally with no column behind it, so
  `build_store(p, rebuild=True)` reported the skip and any later `build_store(p)` — what
  `prismabib build <slug>` runs without `--rebuild` — reported `()`, which reads as "nothing
  was skipped". **This is a deviation from the frozen BUILD_PLAN schema**: it adds one table
  and changes no existing table, column, type, count, or checksum. The committed golden
  snapshot gains exactly one key, the SHA-256 of the empty byte string.

## [0.8.0] — 2026-08-28

### Added

- **Scopus Abstract Retrieval enrichment as a new Layer 0 run kind.**
  `criteria.yaml` has always had a `subject_areas` filter and nothing has ever been able to
  apply it: the Search API does not return subject-area codes at any view prismabib is
  permitted to use. Measured on a real 651-record corpus, 0 of 125 sampled entries carried
  a `subject-area` key, and the two committed `view=COMPLETE` cassettes are 50 more real
  entries with the same result — now pinned by
  `test_contract__search_complete_response__carries_no_subject_areas`. The codes live in
  the separate Abstract Retrieval API. `prismabib.capture.enrich.capture_abstracts` fetches
  them, one call per record, and writes the verbatim responses to
  `raw/abstracts/<run_id>/`, sealed with its own `AbstractRunManifest`. See ADR 0011 for
  why that is Layer 0 rather than a cache, why it is nested rather than a sibling of the
  search runs, and why it is never a row in `runs`.
- **`prismabib.capture.layout`**, the shared Layer 0 on-disk vocabulary — what marks a run
  sealed, which directories under `raw/` are not runs, the sealed-write guard, the atomic
  write, and the run-id format. A pure refactor out of `capture/writer.py`, which
  re-exports `is_sealed` and `SealedRunError` unchanged. `store/load.py` had been carrying
  a hand-copied duplicate of `_CACHE_DIRNAME` and now imports the definition.
- **`sanitise_abstract`** in `tests/fixtures/sanitise.py`, for the Abstract Retrieval
  envelope. Unlike `sanitise_page` it **fails closed**: an unrecognised container raises
  rather than being copied through, because on a public repository a sanitiser that quietly
  passes unknown fields publishes licensed prose and reports success.

### Changed

- **Nothing that produces a number.** No loader, engine, or schema change; no fixture or
  golden-snapshot regeneration. `subject_areas` still loads zero rows and the PRISMA engine
  still refuses a declared subject filter, exactly as before. Consuming this data is a
  separate change, deliberately: the change that adds HTTP code moves no counts, and the
  change that moves counts adds no HTTP code.

## [0.7.0] — 2026-08-27

### Added

- **Windows support for the decision log.** `prismabib.prisma.log` did a module-level
  `import fcntl`, so on Windows the module failed at *import*: a researcher there could
  capture a corpus and build a store, then discover at the first screening decision that
  the one irreplaceable part of the pipeline had never been able to run. Locking now goes
  through one of two backends selected on `sys.platform` — `fcntl.flock` unchanged on
  POSIX, `msvcrt.locking` on Windows — with both platform modules imported inside the
  functions that need them. See ADR 0010 for the design and for the one named deviation:
  Windows has no shared byte-range lock, so a read's shared lock degrades to an exclusive
  one there (never weaker, only less concurrent).
- **`.gitattributes`**, without which no Windows checkout could work: Git for Windows
  defaults to `core.autocrlf=true`, which would rewrite the reference fixture's captured
  Scopus pages and break the `payload_sha256` that covers their bytes.
- **`full-windows` CI job** (`windows-latest`, Python 3.12). Deliberately not a required
  check yet, on the same reasoning as `e2e`: it has no passing history. It is the only
  check of the Windows code against a real Windows machine.

### Fixed

- **`decisions.jsonl` and its checksum sidecar are now written binary.** Without
  `O_BINARY` the Windows C runtime rewrites every `\n` as `\r\n` on disk and hides it
  again on read, so prismabib would have agreed with its own sidecar while an external
  `sha256sum` disagreed — and the sidecar is deliberately `sha256sum`-compatible so that a
  reviewer can verify their own screening record with a tool that is not ours. Byte-level
  assertions now guard both files on every platform.
- **`build_store(project, rebuild=True)` explains an undeletable store** instead of
  raising a bare `PermissionError`. On Windows the DuckDB file cannot be removed while any
  connection to it is open, and the likeliest holder is the caller's own `Corpus` or an
  earlier notebook kernel.
- **Nesting the decision log's file lock raises instead of deadlocking.** Each `_locked`
  call opens a new descriptor, so a nested one asked the OS for a second conflicting lock
  from the same thread — an unbounded, silent hang on POSIX. Nothing nested it; the guard
  keeps that true on both platforms.

## [0.6.1] — 2026-08-26

### Changed

- **`[query]` refuses keys it does not understand**, naming the closest valid one —
  the same treatment `criteria.yaml` already got in 0.6.0, and for the same reason.
  `compound_term` without its `s` was silently dropped and the search ran without
  that AND group, returning a narrower, entirely plausible-looking corpus.

### Fixed

- **A malformed `[query].compound_terms` is now diagnosed by prismabib rather than
  by Pydantic.** Every wrong shape TOML permits there — bare strings, a single group
  written without its surrounding `[ ]`, a `[query.compound_terms]` header that should
  have been doubled, a bare nested list, a scalar — gets a message naming the mistake
  and writing out the corrected line. Previously these produced a validation dump
  citing `_CompoundTerm`, a private class the reader cannot look up; a scalar produced
  an uncaught `TypeError`, which the CLI reports as a bug in prismabib rather than as
  a mistake in the file.
- A `query` key that is not a table no longer surfaces the private name `_QuerySpec`.

## [0.6.0] — 2026-08-26

Phase 0a: making the tool honest for researchers who are not its author. This
release is **breaking**: `criteria.yaml` files that previously loaded may now be
refused, deliberately.

### Added

- **A CLI**: `prismabib init | search | build | flow`. The README had instructed
  users to run `prismabib init` since Stage 0; no CLI existed. `code` and `export`
  are absent rather than stubbed, since commands that error on use are how the
  README became untrustworthy in the first place.
- **`examples/worked_example.py`** — the whole pipeline end to end with **no Scopus
  key and no quota**, so a researcher can see it work before deciding whether to
  request institutional access.
- `docs/methodology/limitations.md` — what this tool cannot currently do, in one
  place, framed as current state rather than apology.
- `CITATION.cff`, asking specifically for the version: a citation without one
  cannot be checked by a reader, which is the project's whole premise.

### Changed

- **`criteria.yaml` refuses keys it does not understand**, naming the closest valid
  one. It previously dropped them silently, so a misspelled `language:` or a
  plausible-but-unsupported `study_designs:` produced no error and no filtering —
  a wrong corpus reached by a typo.
- **A declared `subject_areas` restriction is refused when no record can be judged
  against it.** The Scopus Search API's `view=COMPLETE` returns no subject-area
  codes, so every real corpus is in that state: the filter silently matched
  everything and the diagram claimed a restriction that never ran.
- An inverted `temporal` window is refused; it emptied the corpus and made the
  diagram report that the automated filter removed the entire search.
- `Project.init` ships a working PRISMA reason-code vocabulary and documents the
  semantics that cannot be guessed — `conference_whitelist` is a substring match
  applying only to conference venues, `languages` matches Scopus's string exactly.
- The 403 entitlement error and the truncated-decision-log error now say what to
  **do**, not only what went wrong. Both are failures that end a first session.
- `docs/getting-started.md` is the real path rather than a stub; `README` and
  `docs/index.md` no longer claim "Stage 0 (v0.1.0)" four stages late.

### Fixed

- **The package version no longer lies.** `pyproject.toml` said `0.1.0` at tag
  `v0.5.0`, and that string is sealed into every run manifest as `client_version`,
  permanently. Now derived from the git tag.
- **`prismabib init` no longer requires a Scopus key** to create a directory.
- Auto-filed issues assign to `${{ github.repository_owner }}`, and the live
  governance suite derives its slug from `origin`, so a fork fails on behaviour
  rather than identity.

## [0.5.0] — 2026-08-25

### Added

**PRISMA engine and the Layer 2 decision log (Stage 4)**

- `prisma/events.py`, `prisma/log.py`: append-only `decisions.jsonl`, fsync'd per write
  with a SHA-256 sidecar and `flock`-guarded, plus monotonic stdlib ULIDs. Hand-editing
  raises `LogError`; an *interrupted append* is diagnosed distinctly from tampering, so a
  reviewer who lost power is not told their own irreplaceable screening log "may have
  been edited by hand".
- `prisma/criteria.py`: criteria history resolved from git alone (`log --follow` /
  `show <hash>:<path>`) — there is no per-version archive directory, so an uncommitted
  amendment is invisible to replay.
- `prisma/engine.py`: the formal sets `S_raw`/`A`/`L`/`M_abs`/`M_full`/`C`, plus `replay`
  for criteria amendments. `A` and `L` are pure functions of `criteria.yaml` and Layer 1
  and can never be widened by a logged event.
- `prisma/flow.py`: `FlowCounts` and `assert_consistent()`.
- Property suite proving `C ⊆ M_abs ⊆ (S_raw ∩ A ∩ L) ⊆ S_raw` for arbitrary generated
  event streams, plus a stateful `DecisionLogMachine`.
- `weekly-mutation.yml` (kill-rate gate over `prisma/`) and an `e2e` CI job.
- ADR 0007 (`unsure_*` fields), ADR 0008 (multi-reviewer adjudication: any `exclude`
  wins, then any `unsure`, include only if unanimous among reviewers who logged),
  ADR 0009 (mutmut 3.x configures scope in `pyproject.toml`; BUILD_PLAN line 580's
  `--paths-to-mutate` is the 2.x CLI and cannot run).

### Changed

- `FlowCounts` gains `unsure_title_abstract` and `unsure_fulltext` (ADR 0007). BUILD_PLAN's
  frozen shape had nowhere to put a record that was screened but not resolved, so the
  partition could not close on a `decision = "unsure"` the spec itself admits.
- `compute_flow_counts` takes **one** consistent snapshot — one Layer 1 read, one criteria
  parse, one log fold (was 6 / 4 / 3+). Because both `unsure_*` fields are partition
  remainders, drift between separate folds was absorbed silently: a diagram reporting
  `unsure_title_abstract = -4` passed `assert_consistent()`.
- `assert_consistent()` now rejects any negative count *before* checking the four
  equations. An equality between two sums closes over a negative term exactly as happily
  as a positive one, so the equations alone could not catch it.
- `Corpus.records`/`keywords` answer every `PrismaStage`; `AUTOMATED` and `LANGUAGE` are
  computed without reading Layer 2 at all, so a corrupt `decisions.jsonl` cannot fail a
  question it cannot influence, and asking one no longer *creates* a screening log for a
  project that has never screened.

### Fixed

- `excluded_fulltext` key order was `PYTHONHASHSEED`-dependent, so `numbers.json` would
  not have been byte-identical across machines — the property Stage 11 is graded on.
- `criteria.py` decoded git output with the machine's locale; under a non-UTF-8 8-bit
  locale a non-ASCII `criteria.yaml` would have been mis-decoded **silently**, changing
  `A` and therefore the published `excluded_automated`.
- `DecisionLog` created its parent directory. Git cannot store an empty directory, so a
  project cloned with `track_decisions = false` arrived without `decisions/` and the first
  screening decision died on `FileNotFoundError`.

## [0.4.0] — 2026-08-24

### Added

**Normalised store, Layer 1 (Stage 3)**

- `store/schema.sql`: the 11 tables of BUILD_PLAN lines 847-879, verbatim.
- `store/load.py`: `build_store()` — the one function §2.2 requires Layer 1 to be
  reconstructible by — plus the read-facing `Corpus` handle (`records`, `keywords`,
  `citations`). `store/db.py`: `connect()`.
- `store/checksums.py`: deterministic, DuckDB-version-independent per-table SHA-256
  (rows sorted by key, cells canonicalised). Never DuckDB internals, whose churn
  across releases would train exactly the habit §5 risk 11 warns about.
- `stage.py`: `PrismaStage` as a leaf module, so Stage 4's `prisma/` can import it
  downward. Only `RAW` is answerable from Layer 1; every other member raises
  `NotImplementedError` naming the missing engine.
- Citations are point-in-time: `citation_snapshots` keyed `(record_id, retrieved_at)`,
  with `retrieved_at` from the run manifest's `started_at` — never wall-clock at load,
  which would give every rebuild a new key and break both byte-stability and idempotence.
- Countries normalise to ISO 3166-1 alpha-3; an unmapped string is logged and counted as
  unknown, never dropped, so the geography total still equals the record count.
- Keywords store both `term_raw` and `term_norm`. Duplicate DOIs are reported, not merged.
- `tests/fixtures/projects/reference/`: a frozen, deterministic 120-record Layer 0
  archive exercising all eight §3.7.5 edge cases, produced by a checked-in generator
  that calls `writer._write_page` directly so it cannot drift from production.

### Changed

- **Layer 0 format (amends Stage 2).** `page-NNNN.jsonl` is now true JSON Lines — one
  record per line — with the response envelope in a sibling `page-NNNN.meta.json`.
  Previously the whole envelope was a single line, which pinned `payload_line` at `0`
  for every record: it addressed the page, never the record, so `PayloadRef`'s
  "file + line offset" carried no information and per-record provenance did not exist.
  S03-AC2 would have passed regardless, because line 0 is valid JSON.
- `build_store` uses DuckDB's columnar ingest rather than `executemany`, whose cost is
  per *call* — a single-row insert costs 0.37s, and eleven tables paid it eleven times.
  **5.35s → 0.19s** on the reference fixture, identical results, no new dependency.

### Fixed

- **Layer 1 was not machine-independent.** DuckDB's `TIMESTAMP` is naive, so timezone-aware
  datetimes were converted to the *host's* local time — the same Layer 0 archive stored
  `09:00` on a UTC runner and `03:00` on a UTC-6 workstation. This broke S03-AC1 and would
  have broken Stage 11's central criterion, since every citation snapshot date would shift
  by the reader's UTC offset: two researchers, identical code and data, different published
  dates, with nothing to indicate why.
- The same bug on the read path: `Corpus.citations(at=...)` returned 120 rows under UTC and
  0 under UTC-6 for the same store and argument, because `manifest.started_at` is aware and
  lands exactly on the snapshot boundary.
- `Corpus._query` crashed on a column whose first 100 values were NULL (polars infers a
  schema from 100 rows by default). `records.doi` is nullable and a corpus can open with
  100 DOI-less conference papers.
- The §2.5 data guard rejected the reference fixture: its `projects/*/raw/` pattern was
  unanchored and matched `tests/fixtures/projects/`, blocking the fixture §3.7.5 requires
  be committed. Anchored to the repository root, and now covered by tests in both
  directions — it had none before.

## [0.3.0] — 2026-08-20

### Added

**Scopus acquisition and Layer 0 capture (Stage 2)** — the first stage with a real
external dependency:

- `sources/scopus.py`: `ScopusClient`. `view=COMPLETE` is mandatory; a 403 raises
  `EntitlementError` and stops, never falling back to `STANDARD`. Measured against the
  live API, COMPLETE returns seven fields STANDARD lacks (`authkeywords`, `author`,
  `author-count`, `dc:description`, `fund-*`), so a silent downgrade would remove the
  keyword co-occurrence network and the geography analysis outright.
- Cursor pagination from the start (`start`/`count` caps at 5,000), `count=25`, guarded
  against a stale cursor looping forever. `tenacity` backoff on 429/5xx only — 401 and
  403 are never retried, because retrying a bad key burns weekly quota.
- `sources/ratelimit.py`: header-aware token bucket, hard-capped at a configurable
  6 req/s. `sources/cache.py`: content-addressed HTTP cache so development re-runs cost
  no quota.
- `capture/writer.py`: `capture_search` writes `raw/<run_id>/page-NNNN.jsonl` plus
  `manifest.json`. A run directory is **sealed** once `manifest.json` exists and every
  write path refuses a sealed directory — Layer 0 immutability enforced in code, not by
  convention. Resumption state lives in a `cursor.json` sidecar outside the sealed
  archive, and stores the cursor itself so a resumed run continues from where it stopped
  rather than re-fetching pages Layer 0 already holds.
- `query.py`: Boolean query builder. Raises on any `compound_terms` shape it cannot
  interpret rather than coercing.
- `.github/workflows/nightly.yml`: the `live` contract suite, scheduled off-peak. It
  comments on an existing open issue rather than opening a new one each night. Per
  §3.7.7 it is scheduled, **not** a required check — a cron job cannot gate a pull
  request, and gating `main` on Elsevier's uptime would block every merge during an
  outage.
- Contract-test cassettes, recorded once against the live API and sanitised. Titles,
  abstracts, author keywords, names, affiliations, and all person/institution
  identifiers are regenerated; structure — field presence and absence, scalar-vs-list
  shape, pagination envelope — is preserved exactly, because structure is what the
  contract tests exist to pin.

### Fixed

- `build_query` silently produced a **wrong** query. Given BUILD_PLAN §3.1's real TOML
  (`compound_terms = [{ all = [...] }]`) it iterated the mapping's keys and emitted
  `TITLE-ABS-KEY("all")` without raising — a wrong corpus, and every downstream number
  wrong with no signal anywhere.
- `Project.init` scaffolded only `[project]`, omitting §3.1's `[query]` table, so
  `capture_search` failed on every freshly-initialised project citing a section the
  template had never offered.
- `RateLimiter.acquire` could spin forever. The wait is computed as `deficit / rate` and
  converted back by refill as `elapsed * rate`; that round trip is not exact, so with a
  clock advancing by precisely the requested amount the token count settles one ULP below
  1.0 and the loop never exits. Real `time.sleep` masks it by overshooting. A hang at page
  40 of 71 is a silent stall, not a crash.
- The sanitiser passed real ORCIDs and Scopus author/affiliation ids through untouched,
  attached to fabricated names — a resolvable pointer to a named living researcher,
  republished on a public repository under someone else's name. Identifiers are now
  remapped through a stable map that preserves cross-references, and synthetic ORCIDs are
  deliberately check-digit-invalid.

## [0.2.0] — 2026-08-19

### Added

**Domain model (Stage 1)** — the vocabulary of the system, before any I/O exists:

- `models.py`: `Author`, `Affiliation`, `Venue`, `Record`, `PayloadRef`, with the field
  sets frozen at BUILD_PLAN lines 660-697.
- `Affiliation.country` normalises to ISO 3166-1 alpha-3 through a checked-in table
  (`countries.py`). An unmapped string is **preserved verbatim and logged**, never
  dropped — risk 8 is geography being silently understated. Already-alpha-3 input is not
  re-flagged.
- Scopus emits `affiliations` and `afid` as scalar-or-list inconsistently; both coerce at
  the validation boundary.
- `dedup_key` / `normalise_doi` per §3.2, as pure functions of a `Record` so the store can
  use them without depending on acquisition.
- `project.py`: `Project.init` / `open` / `criteria` / `raw_dir` / `db_path` /
  `decisions_path`. `init` is idempotent in the way that matters — a second call never
  truncates an existing `project.toml`, `criteria.yaml`, or `decisions.jsonl`. Those are
  hand-edited methodology and unrepeatable human labour.
- `config.py`: settings via `pydantic-settings`. A missing key raises `ConfigError` naming
  `SCOPUS_API_KEY`, and the value never surfaces in `repr`, `str`, or `model_dump`.
- `errors.py`: the §3.3 exception taxonomy.

**Documentation:**

- ADRs 0001-0005, recording the §1.2 fixed decisions.
- `docs/architecture/data-model.md` (Stage 1 half).
- `docs/architecture/overview.md` gains a running "additions to BUILD_PLAN §2.3" list, so
  deviations from the frozen layout stay visible in one place.

**Tests:**

- All 12 tests of the Stage 1 table, plus `tests/factories.py` (polyfactory, so factories
  cannot drift from the schema) and `tests/builders.py` (`SyntheticCorpus`).
- The round-trip property test pins its mandated edge cases with `@example` rather than
  relying on hypothesis to draw them.
- Coverage is 100% on every module, reached by asserting behaviour: no `fail_under` was
  lowered and no `# pragma: no cover` was added.

### Changed

- mypy skips numpy's stubs. numpy ships PEP 695 `type` statements that cannot be parsed
  under `python_version = "3.11"`, and it arrives transitively via pydantic, so every
  module importing pydantic aborted the run. The 3.11 type target was kept deliberately —
  the CI matrix runs 3.11, and that target is what catches typing the code cannot execute
  there.

## [0.1.0] — 2026-08-18

### Added

**Repository bootstrap and governance:**
- GitHub repository created at `SergioHuesca/PRISMA-Bib` (public, branch protection active)
- Branch protection on `main` with required status checks: `lint`, `fast`, `full`, `docs`
- Squash-merge-only strategy; automatic branch deletion on merge
- Secret scanning and push protection enabled
- GitHub Pages configured for documentation deployment

**Python package structure:**
- `src/prismabib` layout with Python 3.11+ support
- `uv` for reproducible environments and dependency management
- Package metadata in `pyproject.toml` with version `0.1.0`
- Pre-commit hooks: `ruff` (lint/format), `nbstripout`, `detect-secrets`, custom guards for secrets

**Test harness (§3.7.3 rules enforced by construction):**
- Socket ban via `pytest-socket` (autouse fixture); live tests explicitly re-enable sockets
- Frozen clock via `time-machine` (fixed instant 2024-01-15 12:00:00 UTC)
- Seeded, monotonic `IdFactory` for deterministic ID generation
- `tmp_project` fixture matching §2.3 directory layout (`raw/`, `store/`, `decisions/`, etc.)
- `--acceptance-report` traceability plugin (tests/markers.py) to claim acceptance criteria
- All markers registered with `--strict-markers` (no silent typos)

**CI/CD pipeline:**
- GitHub Actions workflows for lint → type check → test → docs build
- Parallel test matrices (Python 3.11, 3.12)
- Coverage upload and enforcement (§3.7.6 gates)
- Documentation builds to GitHub Pages on push to `main`

**Documentation skeleton:**
- MkDocs with Material theme, `mkdocstrings[python]` for API docs
- Architecture section with ADR framework (ADRs 0001–0005 reserved for Stage 1)
- Methodology section (content added Stages 1–11)
- How-to guides section (content added Stages 2, 5, 8, 10)
- Testing page with harness explanation (extended in later stages)
- `docs/getting-started.md`, `docs/architecture/overview.md`, and reference pages

**Configuration and licensing:**
- `.env.example` for Scopus and ScienceDirect credentials
- `.gitignore` per §2.5 (Layer 0 raw, `corpus.duckdb`, `.env` excluded)
- MIT license
- Data licensing rules documented in CONTRIBUTING.md and enforced by pre-commit

### Fixed

- Governance deviation ADR 0006 created: documents the unavoidable trade-off between sole-owner approval requirement (impossible on GitHub free tier) and public visibility (needed for branch protection). Repository is public; approval rule is disabled until a second maintainer joins.

### Notes

This release establishes a fully governed repository before any source code beyond the test harness is written. The four-layer architecture is specified but not yet implemented. See [BUILD_PLAN.md](https://github.com/SergioHuesca/PRISMA-Bib/blob/main/BUILD_PLAN.md) for the full roadmap.

**Acceptance criteria (Stage 0, all claimed):**
Transcribed from BUILD_PLAN.md lines 629-634. Criteria that are facts about GitHub's
servers are claimed by `tests/live/` tests marked both `live` and `acceptance`:
collected, so `--acceptance-report` counts the claim, but deselected from the default
run so the socket ban holds.

- S00-AC1: repository exists, `git remote -v` shows it as `origin`, `main` is pushed
- S00-AC2: `uv sync && uv run pytest` green from a clean clone **taken from GitHub**, not
  from the working copy
- S00-AC3: `mkdocs build --strict` succeeds, and `docs.yml` has published the site to
  GitHub Pages at least once
- S00-AC4: `pre-commit run --all-files` clean
- S00-AC5: CI green on a pull request, and that PR cannot be merged while a check is red
- S00-AC6: a direct `git push origin main` is rejected by branch protection

[Unreleased]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.12.0...HEAD
[0.12.0]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.6.1...v0.7.0
[0.6.1]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.6.0...v0.6.1
[0.6.0]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/SergioHuesca/PRISMA-Bib/releases/tag/v0.1.0
