## Stage: [INSERT STAGE NUMBER, e.g. Stage 0]

**BUILD_PLAN reference:** [INSERT LINK TO BUILD_PLAN.md SECTION, e.g. https://github.com/SergioHuesca/PRISMA-Bib/blob/main/BUILD_PLAN.md#section-anchor]

---

## Acceptance Criteria

All items below must be ticked before merge. For each criterion, name the claiming test (a test ID marked with `@pytest.mark.acceptance("...")`) or a procedure that verifies it (for criteria that are inherently GitHub-server state or require manual observation).

- [ ] **AC1** — [criterion text] | Claiming test: `test_xxx` or procedure: [procedure]
- [ ] **AC2** — [criterion text] | Claiming test: `test_xxx` or procedure: [procedure]
- [ ] **AC3** — [criterion text] | Claiming test: `test_xxx` or procedure: [procedure]

---

## Definition of Done

All boxes must be checked. These are non-negotiable, applied to every stage.

- [ ] `ruff check` and `ruff format --check` clean.
- [ ] `mypy --strict src/prismabib` clean.
- [ ] `pytest` green; coverage gates met (§3.7.6: ≥ 85% default; ≥ 95% for `store`, `prisma`, `taxonomy`; 100% branch on `prisma/engine.py` and `prisma/log.py`).
- [ ] Every acceptance criterion in the stage carries at least one test marked `@pytest.mark.acceptance("S<NN>-AC<n>")`; `pytest --acceptance-report` shows no unclaimed criterion.
- [ ] No new test is skipped, `xfail`-without-reason, or marked `flaky`.
- [ ] Public functions have docstrings (Google style) and appear in `docs/reference/`.
- [ ] `mkdocs build --strict` succeeds.
- [ ] CHANGELOG entry added under `## [Unreleased]`.
- [ ] Any methodology-affecting choice has an ADR or a `docs/methodology/` entry.

---

## UI Changes

- [ ] No UI changes in this PR.
- [ ] UI changes present — **attach a screenshot below**.

[Attach screenshot if applicable]

---

## Methodology Changes

- [ ] No methodology changes in this stage.
- [ ] Methodology changed — ADR reference: [INSERT ADR FILENAME AND LINK]

---

## Golden Snapshot Updates

- [ ] No golden snapshots regenerated.
- [ ] Golden snapshots regenerated — explain the diff:

[Insert detailed explanation of what changed and why, e.g.:
- `tests/fixtures/stores/reference_v2024-Q3_expected.json`: Added field `keyword_coverage_score` per new Stage 7 metric.
- Diff spans lines XXX–YYY; see commit SHA for full diff.
- Regression test `test_xxx` validates the change against independent calculation.
]

---

## Commit Message

PR will squash-merge as: `[type](scope): subject` following Conventional Commits (types: `feat`, `fix`, `docs`, `test`, `refactor`, `chore`, `perf`, `build`, `ci`).

Example: `feat(prisma): derive flow counts from event log`

---

## Checklist Before Requesting Review

- [ ] All acceptance criteria above are ticked with a claiming test or procedure.
- [ ] Definition of Done is complete.
- [ ] CI is green (or expected failures are documented and intentional).
- [ ] Branch is up to date with `main`.
- [ ] Commit message follows Conventional Commits format.
