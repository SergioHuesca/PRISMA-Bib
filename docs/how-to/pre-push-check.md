# Pre-push Check

A local gate that runs the checks which most often turn CI red, before you push. A red CI run
costs about seven minutes. `--quick` takes about 80 seconds on Windows (mypy dominates); the
full gate adds the unit and contract tests. It never commits, pushes or changes a file.

## When to run it

Before every `git push` of a branch for a PR:

```text
uv run python scripts/prepush.py
```

## What it runs

In order, all steps run and a summary at the end lists every failed step:

1. `ruff check .`
2. `ruff format --check .` over the whole tree, markdown code blocks included.
3. detect-secrets over the files this branch changed since it left `origin/main`
   (`git diff origin/main...HEAD`) plus uncommitted and untracked files, with the same baseline and exclusions as the pre-commit hook.
4. `mypy --strict src/prismabib` (Python 3.11 target, as in CI).
5. `pylint --disable=all --enable=duplicate-code --min-similarity-lines=6 src/prismabib`,
   filtered to the blocks this branch touched (see [Duplication step](#duplication-step)).
6. `pytest -m "unit or contract" -q -n 2` with line coverage on for `src/prismabib` (fail-under 0).
7. The complexity-risk step, which reads that coverage (see [Complexity-risk step](#complexity-risk-step)).

| Option | Effect |
|---|---|
| `--quick` | Skip pytest and the complexity-risk step. |
| `--changed` | Only the tests of changed modules (`tests/unit/test_<module>.py`). Falls back to the full unit and contract set if any changed file has no obvious test. Skips the complexity-risk step. |
| `--workers N` | pytest-xdist workers. The default is `-n 2`; this overrides it. |
| `--skip-mypy` | Skip mypy. Prints a warning; CI still runs it. |
| `--fail-fast` | Stop at the first failed step. |
| `--timeout N` | Seconds allowed per step (default 900). A timeout counts as a failure. |

Run `git fetch origin` first so the changed-file list is current.

## Reading the output

Each failed step prints its tool output, then the summary names the step and the exact
command to re-run it. The exit code is non-zero if any step failed.

## Duplication step

**What it checks.** pylint's duplicate-code checker (and nothing else of pylint) runs over the
whole `src/prismabib` tree, because a run over only the touched files cannot see a block shared
with an untouched file. A block is reported only when one of its locations overlaps a line this
branch added or edited: the new side of `git diff -U0` against the merge-base of `origin/main`
and `HEAD`, plus every line of an untracked, non-ignored file. A block made only of unchanged
lines is not reported, even in a touched file, and neither is a removed or moved one. There is
no list of known duplicates.

**Threshold.** `--min-similarity-lines=6`, which finds 9 duplicate pairs on `main` at commit
`308bb02` (measured at 4 lines: 31, 6: 9, 8: 4, 10: 2). The value lives as
`MIN_SIMILARITY_LINES` in `scripts/prepush.py` and is changed only by a separate change, never
by the change that hit it. To clear a reported block, remove the duplicate.

**Skipped:** never. It runs under the default, `--quick` and `--changed` options. It takes
about 90 seconds.

**Failure output.** Both locations of each block, as `path:first-last  <->  path:first-last`.

**Re-run:** the exact command printed in the summary.

**What it writes:** nothing. No tracked file and no untracked, non-ignored file (`--persistent=n`
keeps pylint from writing its stats cache).

**Platform note.** Paths from git and pylint are converted to forward-slash paths relative to the
repository root before they are compared, so the result is the same on every platform.

**Missing module.** If pylint is not installed, the interpreter prints
`<python>: No module named pylint`. The step then fails with the module name and an install
hint, and the other steps still run. Any other pylint failure is an ordinary failure. Install
the development dependencies as described in the next section.

## Complexity-risk step

**What it checks.** Every function in each Python file under `src/prismabib` that this branch
touched (changed since `origin/main`, uncommitted or untracked; files outside the `prisma`
package included; scripts and tests are out of scope) is scored with
`score = complexity^2 x (1 - coverage)^3 + complexity`, where complexity is radon's cyclomatic
complexity and coverage is the share of the function's executable lines that the unit and
contract run executed (nested functions count toward their outer function). The threshold is 10;
a score is never below the complexity, so every function above complexity 10 is over it, and a
function at complexity 10 or less is over it only when poor coverage lifts the score. A touched
file with no coverage data is an error. Functions in files you did not touch, and the legacy
entries for them, are not looked at, so a branch that touches no such file has nothing to score.

| Outcome | Meaning | Fails |
|---|---|---|
| new | Not on the legacy list and score above 10. | yes |
| raised | On the list and complexity above the recorded one. | yes |
| known | On the list and complexity not above the recorded one. | no |
| stale | On the list, but the function is gone or is now at complexity 10 or less. | yes |

The legacy list is `scripts/complexity-legacy.txt`: one entry per function whose complexity was
above 10 when the list was produced, one per line, `file qualname ordinal complexity`
separated by single spaces (forward-slash path relative to the repository root; the ordinal
counts functions with the same qualified name in that file, in line order from 1). The gate
never writes it: a failing run prints the entries to delete (stale) or to lower (a known
function whose complexity went down) and you edit the file by hand. A function at complexity
10 or less is never listed, even when poor coverage makes it a violation.

**Failure output.** For each violating function: file, qualified name, ordinal, complexity,
score and the coverage it was scored with.

**Skipped** (counted as neither passed nor failed, with the reason in the summary): under
`--quick`, under `--changed`, and when the test step failed.

**Re-run.** Its coverage data exists only for the test run that produced it, so the step cannot
be re-run alone: run the whole gate again (`uv run python scripts/prepush.py`), which runs the
tests first.

**What it writes:** only to the gate's temporary directory (the coverage data file, the
temporary coverage config and the JSON report), all removed when the gate exits, whether it
passed or failed. Nothing in the repository changes.

**Platform note.** Some unit and contract tests skip on some platforms, so coverage, and with
it the new-violation result, can differ by platform. The raise rule compares complexity only
and is stable. The first legacy list was recorded on Windows from the unit and contract run, not
from the full-suite coverage of CI. A function covered only by integration tests can be flagged
locally, because the gate does not run those tests.

**Missing module.** If `radon` or `coverage` is not installed, the child run prints the
module name and the install hint, exits non-zero and the summary shows that note. Install the
development dependencies as described in the next section.

## Installing the development dependencies

The duplication and complexity-risk steps need `pylint`, `radon` and `coverage`, which are in
the `dev` extra:

```text
uv sync --all-extras
```

## Allowlisting a secret false positive

A detect-secrets finding is reported as `file:line`; the secret itself is never printed. If
it is a false positive (a hash or checksum constant), append the marker to that line:

```python
CHECKSUM = "..."  # pragma: allowlist secret
```

## Windows caveats

The working copy is on NTFS, where every file looks executable, so ruff reports `EXE002`
everywhere. On win32 the gate drops only `EXE002` lines from `ruff check`. mypy also reports
a few known errors about `fcntl.flock` and `LOCK_*` in `src/prismabib/log.py`, which are
platform-conditional and absent on Linux CI. On win32 only errors matching that pattern are
filtered. Both cases print how many findings were filtered; anything else still fails.
