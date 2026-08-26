# ADR 0009: Mutation Scope Is Configured, Not Passed on the Command Line

## Status

Accepted — Stage 4, 2026-08-24. Deviation from a frozen BUILD_PLAN line (§2.6 requires an
ADR for any deviation from a frozen contract).

## Context

BUILD_PLAN §3.7.7 line 580 freezes the weekly mutation job as a literal command:

```
weekly        mutmut run --paths-to-mutate src/prismabib/prisma,src/prismabib/taxonomy
```

That command cannot run in this repository, for two independent reasons.

**1. It is the mutmut 2.x CLI.** `pyproject.toml` asks for `mutmut>=2.4.0` and `uv.lock`
resolves **3.7.0**, whose `run` subcommand takes no path option at all:

```console
$ uv run mutmut run --help
Usage: mutmut run [OPTIONS] [MUTANT_NAMES]...

Options:
  --max-children INTEGER
  --help                  Show this message and exit.
```

mutmut 3 reads its scope from a `[tool.mutmut]` table in `pyproject.toml`
(`mutmut/configuration.py`, `_config_reader`), which this repository did not have.
`--paths-to-mutate` would be rejected by click as an unknown option — the job would fail
on its first scheduled run, not degrade quietly.

**2. `src/prismabib/taxonomy` does not exist.** It is a Stage 8 deliverable. Naming a
non-existent path in the scope makes the run fail today even under a 2.x CLI.

The dependency floor is not the culprit here and could not have prevented this: `>=2.4.0`
was written in Stage 0 against a plan drafted when 2.x was current, and a floor with no
ceiling is how the lockfile came to hold 3.7.0. The frozen line simply pre-dates the major
version the project actually installs.

## Decision

**Adopt the mutmut 3.x CLI and express the scope declaratively.**

`pyproject.toml` gains:

```toml
[tool.mutmut]
source_paths = ["src/prismabib"]
only_mutate = ["src/prismabib/prisma/*"]
pytest_add_cli_args_test_selection = [
    "tests/unit/prisma",
    "tests/integration/prisma",
    "tests/property/test_engine_invariants.py",
]
```

and `.github/workflows/weekly-mutation.yml` runs `uv run mutmut run --max-children 4`.

Three details of that table are load-bearing and are the reason it is not a mechanical
transcription of line 580:

- **`source_paths` is the whole package, not `prisma/`.** mutmut copies exactly the
  configured paths into `mutants/` and then puts `mutants/src` on `sys.path`
  (`setup_source_paths`). With `source_paths = ["src/prismabib/prisma"]` there would be no
  `mutants/src/prismabib/__init__.py`, and every test would fail at import. `only_mutate`
  is what confines *mutation* to `prisma/`; `source_paths` only says what gets copied.
- **`src/prismabib/taxonomy/*` is absent from `only_mutate` until Stage 8**, when the
  package exists. Line 580's intent — mutate the engine and the taxonomy — is honoured in
  two steps rather than one, and the pyproject comment names the obligation.
- **Test selection is narrowed to the PRISMA tests.** The full suite cannot run from
  inside `mutants/`: the Stage 0 meta-tests read repository files (`BUILD_PLAN.md`,
  `.github/`, `.pre-commit-config.yaml`) that are not part of the copied tree. A mutant
  killed only by a test outside this selection is reported as *survived*, which errs
  toward more triage work rather than a flattering number.

The gate itself is unchanged from §3.7.6: kill rate ≥ 85%, and the workflow **fails**
below it. That enforcement is a separate step because `mutmut run` exits 0 regardless of
how many mutants survive — verified by running it against a known survivor.

## Alternatives rejected

### 1. Pin `mutmut<3` so that line 580's command runs verbatim

Add an upper bound, re-lock to the 2.x line, and the frozen command works unmodified. No
ADR, no `[tool.mutmut]`, no divergence between plan and repository.

Rejected. mutmut 2 is not maintained; 3.x is a full rewrite and the line the project would
be pinning to receives no fixes. The cost is paid in the places that matter most for this
project: a rewritten runner that copies the tree and runs mutants in forked children
(3.x) versus 2.x's in-place source rewriting, which is the mechanism most likely to leave
a corrupted working tree behind after an interrupted run — on a repository whose entire
premise is that its outputs are trustworthy. It would also fix a security- and
correctness-relevant dependency at a version that Dependabot could never advance, for the
lifetime of the project.

And what would be bought is a *command string*, not a behaviour. BUILD_PLAN's requirement
is "mutation testing runs weekly against `prisma/` (and later `taxonomy/`) with a kill
rate ≥ 85%". Both CLIs satisfy that; only one of them is a version anybody will fix a bug
in. Pinning to an unmaintained major version to preserve the spelling of an invocation is
the worse trade.

### 2. Keep the 2.x invocation and shim it

Wrap the weekly job in a script that accepts `--paths-to-mutate` and translates it into
3.x configuration, so the documented command keeps working.

Rejected: it preserves the appearance of compliance while adding a layer that has to be
maintained, tested, and understood by anyone debugging a mutation run. The plan's command
would still not be the command that executes. A shim that lies about which tool is running
is worse than an honest deviation with an ADR against it.

### 3. Configure the scope but keep passing `taxonomy/` now

Include `src/prismabib/taxonomy/*` in `only_mutate` immediately, so the scope matches line
580 the moment Stage 8 lands, with no further edit.

Rejected: `only_mutate` is a filter over files that exist, so an entry matching nothing is
inert — and inert configuration that *looks* like a gate is precisely the "quiet gate
erosion" the coverage-gates table already exists to prevent. Worse, when Stage 8 does land,
`taxonomy/` would silently enter the mutation gate with nobody having decided it was ready.
The Stage 8 obligation is recorded in a comment beside the table and in
`.claude/PROGRESS.md` instead.

## Consequences

### 1. The weekly command in BUILD_PLAN is documentation, not the invocation

Line 580 no longer describes what CI runs. Anyone reconciling the plan against the
repository will find the difference; this ADR is the record that it is deliberate. The
workflow header and the `[tool.mutmut]` comment both point back here.

### 2. Changing the mutation scope is a `pyproject.toml` change

The scope now lives in version control next to the coverage gates rather than inside a
workflow's shell line — reviewable, diffable, and covered by CODEOWNERS review the same
way any other project configuration is. It also means the scope is identical locally and
in CI: `uv run mutmut run` on a laptop mutates exactly what the weekly job mutates.

### 3. Stage 8 has an explicit obligation

Adding `src/prismabib/taxonomy/*` to `only_mutate`, and its tests to the selection, is
part of Stage 8's definition of done. Until then the weekly gate covers `prisma/` only,
and the 85% figure is a statement about `prisma/` alone.

### 4. A mutmut 4 would break this the same way

The configuration keys are 3.x's (`source_paths`, `only_mutate`,
`pytest_add_cli_args_test_selection` — the last two renamed from 2.x's `paths_to_mutate`
and `tests_dir`, which 3.7.0 still accepts with a `DeprecationWarning`). This ADR does not
protect against the next rewrite; it records that the project follows the tool rather than
freezing it.

## Constraints

- **The kill-rate gate is unchanged.** ≥ 85% on `prisma/`, per §3.7.6. This ADR changes
  how the run is invoked and scoped, not what is required of it.
- **The weekly job is not a required status check** and must not become one: it runs on a
  schedule, so it never reports a context for a pull request, and branch protection would
  block on a check that never arrives. Same ruling as the nightly `live` job.
- **`mutmut run` cannot be trusted to fail.** It exits 0 with survivors. Any future
  rework of the workflow must keep the threshold enforced by a step of its own.

## Related decisions

- **ADR 0007** (`FlowCounts` Unresolved Fields) and **ADR 0008** (Multi-Reviewer
  Adjudication): the other two Stage 4 deviations, both in `prisma/` — the package this
  gate exists to protect

## References

- BUILD_PLAN §3.7.7 line 580 (the frozen weekly command)
- BUILD_PLAN §3.7.6 (mutation testing weekly, kill rate ≥ 85%, survivors triaged into the
  backlog)
- BUILD_PLAN §Stage 4 line 1055 (`mutmut` runs weekly over `prisma/`; surviving mutants
  become backlog issues)
- BUILD_PLAN §2.6 (a deviation from a frozen contract requires an ADR)
- `pyproject.toml` — `[tool.mutmut]`
- `.github/workflows/weekly-mutation.yml` — the weekly job and the kill-rate gate
- mutmut 3.7.0 `mutmut/configuration.py` (`_load_config`) and `mutmut/__main__.py`
  (`setup_source_paths`, `_run`) — the source of the key names and of the exit-code
  behaviour described above

---

This ADR deviates from BUILD_PLAN §3.7.7 line 580. Reverting to the 2.x CLI, or changing
the mutation scope beyond adding `taxonomy/` at Stage 8, requires a new ADR that supersedes
this one (§2.6).
