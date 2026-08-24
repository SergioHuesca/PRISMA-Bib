# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/SergioHuesca/PRISMA-Bib/releases/tag/v0.1.0
