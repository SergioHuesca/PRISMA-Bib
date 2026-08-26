# Testing

This project's test suite is designed to prevent wrong numbers in published output. See BUILD_PLAN.md §3.7 for the full philosophy and methodology.

## Test taxonomy

Every test belongs to exactly one of seven categories, each with a directory, pytest marker, and speed budget:

| Kind | Directory | Marker | Budget | Mocks | Purpose |
| --- | --- | --- | --- | --- | --- |
| **Unit** | `tests/unit/` | `unit` | < 50 ms | nothing | Pure logic, no I/O. The majority by count. |
| **Integration** | `tests/integration/` | `integration` | < 2 s | network only | Real DuckDB, real filesystem, real file writes. |
| **Contract** | `tests/contract/` | `contract` | < 200 ms | — | Payload-shape tests against sanitised cassettes. Fails when Scopus changes its schema. |
| **Property** | `tests/property/` | `property` | < 10 s | nothing | Hypothesis: invariants over generated inputs and event streams. |
| **Golden** | `tests/golden/` | `golden` | < 1 s | network | Snapshot/approval tests for captions, LaTeX tables, SVG flow diagrams. |
| **E2E** | `tests/e2e/` | `e2e` | < 60 s | network | Full pipeline on the frozen reference project (Layer 0 → exports). |
| **Live** | `tests/live/` | `live` | unbounded | nothing | Real Scopus/ScienceDirect APIs. Deselected by default; runs nightly and on demand. |

## Running tests

### Default run (excludes live tests)

```bash
pytest
```

This runs all tests except `live` and is what CI runs. All categories except `live` must pass for a PR to merge.

### Fast pre-commit subset

```bash
pytest -m "unit or contract" -x -q
```

This is what runs in pre-commit hooks (target: < 15 s). `-x` exits on first failure for faster feedback; `-q` is quiet mode.

### Specific categories

```bash
# Unit tests only (pure logic, fast)
pytest -m unit

# Integration tests (real filesystem, real DuckDB)
pytest -m integration

# Contract tests (cassette-based shape validation)
pytest -m contract

# Property tests (hypothesis-based invariants)
pytest -m property

# Golden tests (snapshots)
pytest -m golden

# End-to-end (full pipeline)
pytest -m e2e

# Live tests (real Scopus APIs; requires secrets)
pytest -m live

# Multiple markers (OR logic)
pytest -m "e2e or notebooks"

# Exclude a category
pytest -m "not live"
```

### Parallel execution

Large CI runs use parallelism:

```bash
pytest -n auto
```

### Acceptance criteria traceability

Every stage's acceptance criteria must be claimed by at least one test. Check coverage with:

```bash
pytest --acceptance-report
```

This prints a matrix of `S<stage>-AC<n>` criteria against the tests that claim them. An unclaimed criterion fails CI.

Example output:

```
Acceptance criteria traceability
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
S00-AC1  test_github_repository__exists__main_pushed
S00-AC2  test_package__imports__exposes_version
S00-AC3  test_mkdocs__builds__with_strict_mode
[...]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
All 6 criteria claimed.
```

## Stage 0 test harness

The following infrastructure is built into Stage 0 and inherited by all later stages:

### Socket ban

By default, the test suite bans all outbound network connections:

```python
pytest.FixtureRequest.node.get_closest_marker("live")
```

A `pytest-socket` autouse fixture raises `SocketBlockedError` if any test tries to open a real socket. This enforces that only `tests/live/` tests (marked `live`) can hit real APIs.

**Why:** Prevents accidental network calls, ensures contract tests use cassettes, and keeps the default suite deterministic and fast.

**Opt-in for live tests:**

Tests that need real Scopus/ScienceDirect access are marked `@pytest.mark.live`. The socket policy fixture detects this marker and re-enables sockets. A live test does not need per-test boilerplate—the marker is sufficient.

```python
@pytest.mark.live
def test_scopus__real_api_returns_records():
    # Socket is enabled here; httpx.get() works
    ...
```

### Frozen clock

All tests run with a frozen system time to ensure reproducibility:

```python
datetime.now(UTC) == datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
```

The `time-machine` library freezes the clock at Stage 0's FROZEN_INSTANT. Tests never depend on real time, so:

- Same input always produces same output
- Tests never fail at midnight or leap-year boundaries
- Test timestamps are predictable in assertions

Example:

```python
def test_manifest__records_timestamp__is_fixed(frozen_time):
    manifest = create_manifest()
    assert manifest.timestamp == datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)
```

### Seeded ID factory

Tests inject a monotonic, deterministic ID generator:

```python
@pytest.fixture
def id_factory() -> IdFactory:
    return SeededIdFactory(seed=0, prefix="id")


def test_event__generates_ids__in_sequence(id_factory):
    e1 = Event(event_id=id_factory())
    e2 = Event(event_id=id_factory())
    assert e1.event_id == "id-000000"
    assert e2.event_id == "id-000001"
```

Two runs of the same test produce identical ID sequences—no entropy, no flakiness. Production code takes an `IdFactory` protocol parameter instead of calling `uuid.uuid4()` directly, so tests can inject this seeded version.

### tmp_project fixture

All tests that need a project directory structure get a temporary, pre-built layout:

```python
@pytest.fixture
def tmp_project(tmp_path: Path) -> TmpProject:
    """A temporary projects/<slug>/ skeleton per §2.3."""
    ...
    return TmpProject(
        root=...,
        raw=...,
        store_dir=...,
        decisions_dir=...,
        taxonomy_rules_dir=...,
        fulltext=...,
        exports=...,
        project_toml=...,
        criteria_yaml=...,
        decisions_jsonl=...,
    )
```

The fixture creates all required directories and empty placeholder files. Each test gets a clean, isolated temporary project. This is forward-compatible with Stage 1's `Project` class—Stage 1 builds the real class on top of the same on-disk shape without changing this fixture.

Example:

```python
def test_project__loads__reads_project_toml(tmp_project):
    tmp_project.project_toml.write_text("[project]\nslug = 'test'")
    project = Project.load(tmp_project.root)
    assert project.slug == "test"
```

### Acceptance-marker plugin

Tests claim acceptance criteria with `@pytest.mark.acceptance()`:

```python
@pytest.mark.acceptance("S04-AC3")
def test_decision_log__hand_edited_file__raises_log_error(...):
    ...
```

The plugin (in `tests/markers.py`) collects these markers during test collection and generates the `--acceptance-report` output. An unclaimed criterion means a stage's requirements are not tested—that is a defect and fails the build.

The marker is deliberately hooked at `pytest_itemcollected` (before `-m` deselection) so a test deselected by `-m "not live"` still claims its criterion.

## Recording cassettes (Stage 2+)

HTTP cassettes are recorded once against the real API, then sanitised and committed. Recording happens in Stage 2 when Scopus acquisition is built.

### When to record

- When you add a new API call path
- When Scopus schema changes and the old cassette no longer matches
- When you need to test a retry/backoff behavior against realistic payloads

### Recording procedure

1. **Ensure your `.env` has real credentials:**
   ```bash
   SCOPUS_API_KEY=your-real-key
   SCOPUS_INSTTOKEN=optional-institution-token
   ELSEVIER_SD_API_KEY=your-sd-key
   ```

2. **Run the test with recording enabled:**
   ```bash
   RESPX_CASSETTE_RECORD=once pytest tests/contract/test_scopus_schema.py -v
   ```

   The test will hit the real API and record the response to `tests/fixtures/cassettes/`. If the cassette already exists, `record=once` uses the existing cassette (does not re-record).

3. **Verify the cassette was created:**
   ```bash
   ls tests/fixtures/cassettes/
   ```

4. **Sanitise it immediately:**
   ```bash
   python tests/fixtures/sanitise.py tests/fixtures/cassettes/my_cassette.yaml
   ```

   This script:
   - Strips `SCOPUS_API_KEY` and `SCOPUS_INSTTOKEN` from headers/query strings
   - Replaces paper titles and abstracts with synthetic text of comparable length and character class
   - Rewrites author names and affiliations to a synthetic-but-realistic set
   - Preserves response structure (field presence, scalar vs. list quirks, pagination shape, error bodies)

5. **Verify the sanitised cassette still passes:**
   ```bash
   pytest tests/contract/test_scopus_schema.py -v
   ```

   If the test passes with the sanitised cassette, the structure is preserved. If it fails, the sanitiser modified something critical—debug and re-record.

6. **Commit the sanitised cassette:**
   ```bash
   git add tests/fixtures/cassettes/my_cassette.yaml
   git commit -m "test(contract): record scopus query cassette"
   ```

   **Never commit an unsanitised cassette.** The unsanitised version should not exist in the repository.

## Golden snapshots (Stage 0+)

Golden tests use `syrupy` to approve output snapshots: captions, LaTeX tables, SVG flow diagrams, and metadata JSON.

### Recording/approving snapshots

The first time a golden test runs, it fails and prints the diff:

```bash
pytest tests/golden/test_figures.py -v
```

If the output is correct, update the snapshot:

```bash
pytest tests/golden/test_figures.py --snapshot-update
```

The `syrupy` plugin creates or updates `tests/golden/__snapshots__/test_figures.ambr` (Amber format).

**Important:** `--snapshot-update` only works locally; CI never uses this flag. This prevents CI from silently accepting any change. A PR that changes snapshots must include an explanation in the PR body.

### Legitimate snapshot changes

Update a snapshot when:

- Adding a new figure or table (new test, new snapshot)
- Changing captions or labels intentionally (breaking change; document it)
- Fixing a rendering bug (explain the fix in the PR)
- Refactoring calculation logic that changes values (explain why in the PR and commit message)

**Do not** update snapshots to cover a bug—fix the bug first, then update if needed.

### Reviewing snapshot diffs

Always review what changed:

```bash
syrupy --snapshot-update  # Creates the snapshot
git diff tests/golden/__snapshots__/
```

Or interactively:

```bash
pytest tests/golden/test_figures.py -v
```

Look for unexpected changes—a snapshot diff that doesn't match the commit message is a sign something is wrong.

## Why the PRISMA engine is tested differently (Stage 4+)

Every other module in this codebase is gated on coverage. `src/prismabib/prisma/` is gated on
coverage **and** on mutation testing, because coverage cannot detect the failure this module
is capable of.

### What mutation testing is

A mutation testing tool edits your source code on purpose, one small change at a time, and
re-runs the test suite against each edited copy. Typical edits: `<=` becomes `<`, `+` becomes
`-`, `and` becomes `or`, a constant is bumped, a comparison is negated, a return value is
replaced.

Each edited copy is a **mutant**. If some test fails, the mutant is **killed** — the suite
noticed the behaviour change. If the whole suite still passes, the mutant **survived**: the
code behaves differently and no test cares. The **kill rate** is killed mutants over mutants
run.

A surviving mutant is not a bug in itself. It is evidence of a *hole in the tests* — a
behaviour the suite executes but does not check. Which is precisely the thing coverage
reports as green.

### Why this project needs it specifically

BUILD_PLAN §5 risk 12 names the hazard: *high coverage with weak assertions — tests that
execute code without checking it.* Likelihood high, impact high. The coverage gate on
`prisma/engine.py`, `prisma/log.py`, and `prisma/flow.py` is 100% line **and** 100% branch,
which sounds absolute and is not: a test that calls `compute_flow_counts()` and asserts the
result is not `None` covers every line in the module and asserts nothing worth knowing.

The reason that matters here more than elsewhere is the shape of the failure mode. This
system's characteristic defect is not a crash or a stack trace — it is **a plausible wrong
number in a published paper**. Consider `engine._passes_temporal`:

```python
return criteria.temporal.year_start <= attrs.year <= criteria.temporal.year_end
```

Mutate the second `<=` to `<` and every record published in the final year of the window
silently leaves the corpus. The suite goes green if no test happens to assert on a
boundary-year record. `after_language` drops from 1,550 to 1,509, `included` drops with it,
every figure downstream shifts, and 1,509 looks exactly as reasonable as 1,550 in a
manuscript. Nobody catches it in review, because nothing looks wrong.

Mutation testing is the only automated check that asks the right question of that line: *if
this comparison were wrong, would any test say so?*

The same argument applies to `log.fold_events`'s `(ts, event_id)` tie-break, to
`_aggregate_record_decisions`'s precedence rules ([ADR 0008](architecture/adr/0008-multi-reviewer-adjudication.md)),
and to each of the four equations in `FlowCounts.assert_consistent()` — every one of which is
a small comparison or arithmetic expression whose corruption produces a *credible* number
rather than an obvious failure.

### The gate

| Aspect | Setting |
| --- | --- |
| **Tool** | `mutmut` (declared in the `dev` extra in `pyproject.toml`) |
| **Scope** | `src/prismabib/prisma/`, plus `src/prismabib/taxonomy/` from Stage 8 |
| **Threshold** | kill rate ≥ 85% |
| **Cadence** | **weekly**, not per-PR — a run re-executes the covering tests once per mutant, and there are thousands of mutants |
| **On survival** | an issue from `.github/ISSUE_TEMPLATE/surviving_mutant.md`, triaged into the backlog as a missing test |

Per-PR mutation testing is explicitly deferred (BUILD_PLAN §8): weekly is the v1.0 cadence,
and per-PR becomes reasonable only if incremental mode proves reliable on this codebase. The
release checklist re-checks the kill rate before `v1.0.0`.

Nothing about this gate replaces the rest of the Stage 4 suite — Hypothesis property tests on
the set algebra, the stateful `DecisionLogMachine`, the golden `FlowCounts` fixture. Mutation
testing tests *the tests*; those tests still have to exist for it to have anything to grade.

### Running it locally

```bash
uv run mutmut run            # generate and test mutants (slow — expect minutes to hours)
uv run mutmut results        # list mutants that were not killed
uv run mutmut browse         # interactive TUI over the same results
uv run mutmut show <mutant>  # the diff for one mutant, by name from `results`
```

Notes that will save you an afternoon:

- **`mutmut` 3.x is configured in `pyproject.toml` under `[tool.mutmut]`**, not on the
  command line. The relevant key is `source_paths`. If no such section exists, mutmut guesses
  `src/` and mutates the entire package — correct, but far slower than you want.
  BUILD_PLAN §3.7.7's `mutmut run --paths-to-mutate src/prismabib/prisma,src/prismabib/taxonomy`
  is mutmut 2.x syntax; `pyproject.toml` asks only for `mutmut>=2.4.0` and `uv.lock` currently
  resolves 3.7.0, which does not accept that flag.
- **`mutmut run` accepts mutant names, including `fnmatch` globs**, so you can scope a run to
  one function or module without editing the config: `uv run mutmut run '*prisma.flow*'`.
- **A run creates a `mutants/` directory** at the repository root — a full working copy of the
  source and test trees. It is build output. Do not commit it; delete it when you are done.
- **The suite must be green before you start.** `mutmut` runs the tests unmutated first and
  aborts if they fail, which is the correct behaviour and an easy five minutes to lose.

### Reading a surviving mutant

`mutmut results` prints mutant names; `mutmut show <name>` prints the diff between the
original function and the mutated one. A name encodes the module, the function, and the index
of the mutation within it, so `…prisma.flow.x_compute_flow_counts__mutmut_7` is the seventh
mutation generated inside `compute_flow_counts`.

Work through it in this order:

1. **Read the diff.** What behaviour changed?
2. **Decide whether the change is observable at all.** If the two versions are genuinely
   equivalent — a mutated constant that never reaches an output, a reordering with no
   semantics — you have an *equivalent mutant*. It cannot be killed by any test. Record why in
   the issue and close it. Be sceptical of yourself here: "equivalent mutant" is the most
   comfortable and most commonly wrong conclusion.
3. **If it is observable, name the input that would show it.** For the `<=` → `<` example: a
   record published exactly in `year_end`. That sentence is the missing test.
4. **Write the test, and confirm it fails before you fix anything.** A test that passes
   against the mutant kills nothing. Use `mutmut apply <mutant>` to put the mutation into the
   working tree, watch your new test go red, then revert with `git checkout` — this is the
   same discipline as this project's general rule that a test which matters must be seen
   failing on the original defect.
5. **Re-run that mutant** (`uv run mutmut run <mutant-name>`) to confirm it is now killed.

A surviving mutant that gets closed by loosening an assertion, or by deleting the mutated
line, has been mis-triaged. The output of triage is a new assertion about behaviour, or a
documented equivalence — never a weaker suite.

## Coverage gates (§3.7.6)

Coverage is a floor, not a goal. These gates are enforced in CI:

| Path | Line | Branch | Rationale |
| --- | --- | --- | --- |
| `prisma/engine.py`, `prisma/log.py`, `prisma/flow.py` | 100% | **100%** | Every number derives from these |
| `store/`, `taxonomy/` | ≥ 95% | ≥ 90% | Silent miscounts originate here |
| `bibliometrics/`, `report/` | ≥ 90% | — | Arithmetic |
| `sources/`, `fulltext/` | ≥ 85% | — | I/O-heavy; contract tests carry weight |
| `viz/`, `screening/ui.py` | ≥ 60% | — | Presentation; smoke-level is honest |
| Global | ≥ 85% | — | Overall threshold |

Additionally, `diff-cover` requires ≥ 90% coverage on lines changed in the PR. This prevents rot while old code sits untested.

Check coverage locally:

```bash
pytest --cov=src/prismabib --cov-report=term-missing
```

## Test data policy

### No licensed content

Scopus API responses and full-text PDFs/XML are proprietary. They must never be committed to the repository. This is enforced by:

- `.gitignore` excluding `projects/*/raw/`, `*.duckdb`
- `.pre-commit-config.yaml` with a custom guard
- `detect-secrets` pre-commit hook
- GitHub secret scanning on the public repository

### Cassette sanitisation

HTTP cassettes are sanitised before commit (see "Recording cassettes" above). The sanitiser preserves structure (what contract tests verify) while removing prose.

### Reference fixture project

The frozen reference project (`tests/fixtures/projects/reference/`) is a synthetic ~120-record Layer 0 archive designed to exercise edge cases:

- A record with no abstract
- A record with scalar `afid` (vs. list)
- A non-English record
- A record with 40 authors
- A record with zero keywords
- A duplicate DOI
- A 2026 partial-year record
- An unmapped country string

This project is frozen and versioned. Changing it requires a PR that explains why and updates affected golden snapshots in the same commit.

## Naming and style conventions

Follow these conventions so test output is readable and searchable:

1. **Test name format:** `test_<unit>__<scenario>__<expected>`
   - Double underscores separate the three parts
   - Example: `test_flow_counts__language_filter_excludes_27__after_language_is_1107`

2. **Markers and fixtures are lowercase, with underscores:** `@pytest.mark.live`, `def tmp_project()`

3. **Arrange–Act–Assert, visibly:** Blank lines separating sections
   ```python
   def test_event_log__append__persists(tmp_project):
       # Arrange
       log = EventLog(path=tmp_project.decisions_jsonl)
       event = Event(...)

       # Act
       log.append(event)

       # Assert
       assert log.load_all()[0] == event
   ```

4. **One behaviour per test:** If a test needs a comment to explain what it asserts, the name is wrong. Rename it.

5. **Assert on domain objects, not strings:** Compare `FlowCounts` instances. Except in golden tests, where the rendering *is* the subject.

6. **No conditionals in tests:** An `if` means it is two tests. Parameterise instead:
   ```python
   @pytest.mark.parametrize(
       "language,expected",
       [
           ("English", 45),
           ("Chinese", 5),
       ],
   )
   def test_corpus__filters_language(language, expected): ...
   ```

## Troubleshooting

### "Test passed locally but CI failed"

- Check Python version: `python --version` (CI tests on 3.11 and 3.12)
- Run the full CI suite locally: `pytest -m "not live" -n auto --cov`
- Check for platform-specific issues (Windows vs. Unix path separators, timezone)
- Run `pre-commit run --all-files` to catch lint/format differences

### "Socket is blocked but I need real network for this test"

Add the `live` marker: `@pytest.mark.live`. Do not disable socket ban with a fixture override.

### "A snapshot is constantly changing"

If a golden snapshot fluctuates every run, it usually means:
- The test is not using a frozen clock (add `frozen_time` fixture)
- The test is not using a seeded ID factory (inject `id_factory`)
- The test is not sorting collections before assertions
- A random element is being used (generate test data, don't randomise it)

### "Coverage dropped"

- Run `pytest --cov-report=html` and open `htmlcov/index.html` to find uncovered lines
- Check if you deleted a test by mistake—`git diff` before committing
- Use `pytest --cov-report=term-missing` to see which lines/branches lack coverage
- Remember: coverage is a floor. Low coverage on a presentation module is acceptable.

## More information

- **Test philosophy:** See BUILD_PLAN.md §3.7
- **Acceptance criteria:** See BUILD_PLAN.md §3.7.8
- **Rules of engagement:** See BUILD_PLAN.md §3.7.3 (1–12 conventions enforced by lint, fixture, or review)
