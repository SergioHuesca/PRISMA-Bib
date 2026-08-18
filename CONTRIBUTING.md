# Contributing

Thank you for contributing to prismabib. This guide explains the development workflow, conventions, and governance model.

## GitHub workflow

The project is version-managed on GitHub—the remote repository is the single source of truth. All changes flow through pull requests with required checks; direct pushes to `main` are blocked.

### 1. Clone and set up

```bash
gh repo clone SergioHuesca/PRISMA-Bib
cd PRISMA-Bib
uv sync
cp .env.example .env
# Edit .env with your Scopus API credentials
```

### 2. Create a feature branch

Branch names follow the pattern `stage-NN-slug`, where `NN` is the stage number and `slug` is a kebab-case summary:

```bash
git checkout -b stage-02-scopus-acquisition
```

Push the branch to GitHub immediately—work in progress is visible on the remote while it happens:

```bash
git push -u origin stage-02-scopus-acquisition
```

### 3. Commit with Conventional Commits

Each commit must follow [Conventional Commits](https://www.conventionalcommits.org/):

```
<type>(<scope>): <description>

<body>

<footer>
```

**Types** (from BUILD_PLAN.md §3.6.2):

- `feat` — A new feature or capability
- `fix` — A bug fix
- `docs` — Documentation only (README, guides, ADRs, docstrings)
- `test` — Test additions or corrections
- `refactor` — Code restructuring without feature change
- `chore` — Maintenance, dependency updates, tooling
- `perf` — Performance improvement
- `build` — Build system or dependency changes
- `ci` — CI/CD workflow changes

Examples:

```
feat(scopus): implement cursor pagination for result sets
fix(store): correct country normalisation for GB vs UK
docs(testing): add cassette recording guide
test(prisma): add property tests for decision log folding
```

### 4. Push and open a pull request

```bash
git push origin stage-02-scopus-acquisition
```

Open the PR on GitHub. The PR body template (`.github/pull_request_template.md`) includes a checklist; fill it out completely. You must include:

- The stage number and name
- A ticked checklist of acceptance criteria from the BUILD_PLAN, with each claiming test named
- A screenshot for any UI change
- A note on methodology changes and any ADR reference
- An explicit explanation for any regenerated golden snapshots

### 5. Required checks

The PR cannot merge until these GitHub Actions jobs pass:

- `lint` — `ruff check`, `ruff format --check`, `mypy --strict`
- `fast` — `pytest -m "unit or contract"` (< 60 s, fails fast)
- `full` — `pytest -m "not live and not e2e"` with coverage (py3.11, py3.12 matrix)
- `docs` — `mkdocs build --strict`

If a check fails locally, fix it before pushing:

```bash
# Lint and format
ruff check --fix .
ruff format .

# Type check
mypy --strict src/prismabib

# Run tests locally (subset)
pytest -m "unit or contract" -x -q
```

### 6. Code review

The PR is reviewed on GitHub. Review comments are the permanent record—use PR comments, not chat.

**Required reviewers:**

- `src/prismabib/prisma/**` and `docs/methodology/**` → CODEOWNERS route to required review (those paths affect published numbers)
- All other changes → optional review (but see governance deviations below)

### 7. Merge and tag

Once all checks pass:

- Squash-merge the PR with the PR title as the conventional commit subject (GitHub's default)
- The branch is auto-deleted
- Cut an **annotated** tag on the `main` commit: `git tag -a vX.Y.Z -m "Stage NN description"` and push it: `git push origin vX.Y.Z`
- Create a GitHub Release with the CHANGELOG section for that version as the notes

Example:

```bash
git tag -a v0.2.0 -m "Stage 1: Domain model and ADRs"
git push origin v0.2.0
```

## Definition of Done (applies to every stage)

A stage is not complete until:

- [ ] `ruff check` and `ruff format --check` are clean
- [ ] `mypy --strict src/prismabib` is clean
- [ ] `pytest` is green; coverage gates (see Testing) are met
- [ ] Every acceptance criterion (S<NN>-AC<n>) has at least one test marked with it; `pytest --acceptance-report` shows no unclaimed criteria
- [ ] No new test is skipped, `xfail`-without-reason, or marked flaky
- [ ] Public functions have Google-style docstrings; they appear in `docs/reference/`
- [ ] `mkdocs build --strict` succeeds
- [ ] CHANGELOG has an entry under `## [Unreleased]`
- [ ] Any methodology-affecting choice has an ADR or a `docs/methodology/` entry

## Governance deviations (Stage 0)

The project's build plan specifies two governance rules that cannot be simultaneously enforced on a GitHub free-tier, sole-owner account. Both are documented in ADR 0006:

1. **Required approving review (§3.6.3)** — The plan requires ≥1 approving review before merge. GitHub forbids approving your own PR, so a sole owner cannot satisfy this rule. **Actual:** approval requirement is disabled; all other branch protections are active. If a second maintainer joins, this can be re-enabled.

2. **Visibility (§3.6.1)** — The plan designates private as safe default, flipped to public at v1.0.0. GitHub free-tier blocks branch protection on private repositories (HTTP 403 from both the branch-protection and rulesets endpoints). **Actual:** the repository went public in Stage 0 to enable full branch protection, secret scanning, and push protection. History was audited and verified empty before the flip.

Both decisions are irreversible—ADR 0006 is the permanent record.

## Data licensing rules

Scopus and ScienceDirect impose strict licensing constraints. **These rules are not optional:**

- **No licensed content in the repository, ever.** Scopus API responses and full-text payloads are proprietary. They must not be committed.
- **Cassettes are sanitised.** HTTP recordings used for contract tests are passed through `tests/fixtures/sanitise.py`, which strips API keys, replaces prose with synthetic text of similar length, and rewrites author names and affiliations—while preserving the response structure that contract tests actually verify.
- **Pushing to a public repository is irreversible.** If a raw API response is committed to the remote, it survives in:
  - The GitHub server's reflog (even if deleted)
  - Any fork created before deletion
  - Any clone taken before deletion
  - Search engine caches

  **Do not test this theory.** Treat `.env` and `projects/*/raw/` as if they contain classified material—because to Scopus they do.

These rules are enforced by:

- `.gitignore` excluding `projects/*/raw/`, `projects/*/store/`, `.env`, `*.duckdb`
- `.pre-commit-config.yaml` with a custom guard rejecting those paths
- GitHub secret scanning (enabled at Stage 0; flagged on push)
- `detect-secrets` in pre-commit

## Running tests locally

The test suite has seven categories. By default, `live` tests (which hit real APIs) are excluded:

```bash
# Run everything except live tests (used in CI)
pytest

# Run only fast tests (used in pre-commit)
pytest -m "unit or contract" -x -q

# Run a specific category
pytest -m unit
pytest -m integration
pytest -m contract
pytest -m property
pytest -m golden
pytest -m e2e
pytest -m e2e,notebooks  # multiple markers, OR
pytest -m "not live"     # everything except live

# Opt in to live tests (requires SCOPUS_API_KEY, etc.)
pytest -m live

# Parallel execution
pytest -n auto

# Show acceptance-criteria traceability
pytest --acceptance-report
```

See [docs/testing.md](docs/testing.md) for detailed test taxonomy and examples.

## Cassette recording

When tests need to record real HTTP interactions:

1. Ensure `SCOPUS_API_KEY` and related secrets are in `.env`
2. Run the test with `RESPX_CASSETTE_RECORD=once`:
   ```bash
   RESPX_CASSETTE_RECORD=once pytest tests/contract/test_scopus_schema.py -v
   ```
3. The cassette is written to `tests/fixtures/cassettes/`
4. **Sanitise it immediately:**
   ```bash
   python tests/fixtures/sanitise.py tests/fixtures/cassettes/my_cassette.yaml
   ```
5. Verify the sanitised cassette still passes:
   ```bash
   pytest tests/contract/test_scopus_schema.py -v
   ```
6. Commit the sanitised cassette; never commit unsanitised recordings

## Updating golden snapshots

Snapshots (approved outputs) are stored in `tests/golden/` and managed by `syrupy`. When a legitimate change affects a snapshot:

1. Review the diff with `syrupy` output
2. If the diff is correct, update:
   ```bash
   pytest tests/golden/test_figures.py --snapshot-update
   ```
3. **Do not use `--snapshot-update` in CI**—it only runs locally after manual review
4. In your PR, explain why each snapshot changed in the PR body

If `mkdocs build --strict` fails because a documentation page is new or changed, rebuild the docs locally and commit any artefact changes:

```bash
mkdocs build --strict
```

## Pre-commit hooks

Before every commit, pre-commit runs:

```bash
pre-commit run --all-files
```

This is automatic on `git commit` if you've installed the hooks:

```bash
pre-commit install
```

Checks include:

- `ruff check --fix` (linting with auto-fix)
- `ruff format` (formatting)
- `nbstripout` (strip notebook outputs)
- End-of-file-fixer, trailing-whitespace
- `detect-secrets` (find accidentally committed API keys)
- Custom guard rejecting `projects/*/raw/`, `*.duckdb`, `.env` in git

If pre-commit fails, fix the issues and try again:

```bash
git add <fixed files>
git commit
```

## Questions?

- **Architecture and design:** See [docs/architecture/](docs/architecture/), especially the ADRs
- **Test philosophy:** See [docs/testing.md](docs/testing.md)
- **Build plan details:** See [BUILD_PLAN.md](https://github.com/SergioHuesca/PRISMA-Bib/blob/main/BUILD_PLAN.md) in the GitHub repository
