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
6. `pytest -m "unit or contract" -q --no-cov -n 2`.

| Option | Effect |
|---|---|
| `--quick` | Skip pytest. |
| `--changed` | Only the tests of changed modules (`tests/unit/test_<module>.py`). Falls back to the full unit and contract set if any changed file has no obvious test. |
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

## Installing the development dependencies

The duplication step needs `pylint`, which is in the `dev` extra:

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
