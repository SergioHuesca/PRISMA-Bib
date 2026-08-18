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

## Mutation testing (Stage 4+)

Mutation testing (via `mutmut`) runs weekly against `src/prismabib/prisma/` and `src/prismabib/taxonomy/` with a required kill rate ≥ 85%. Surviving mutants are triaged into the issue backlog. This catches the classic failure where a module has 100% coverage but no meaningful assertions.

**In Stage 0 and Stage 1-3:** Mutation testing is deferred. No mutants are generated yet because the modules don't exist.

**In Stage 4:** The first mutation test run validates that the PRISMA event-folding logic has no silent defects. If a mutant survives, it means the test suite is missing a case and the backlog issue explains which case.

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
