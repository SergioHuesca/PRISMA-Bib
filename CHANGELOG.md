# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/SergioHuesca/PRISMA-Bib/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/SergioHuesca/PRISMA-Bib/releases/tag/v0.1.0
