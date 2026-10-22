"""Local pre-push gate: the checks that turn CI red, run before the push.

Runs ruff check, ruff format --check (whole tree), detect-secrets over the changed files,
mypy --strict and the unit + contract tests, then prints a summary listing every failed step
with the exact command to re-run it. It never commits, pushes or changes a tracked file.

    python scripts/prepush.py [--quick] [--changed] [--workers N] [--skip-mypy] [--fail-fast]
                                 [--timeout N]

Project tools are invoked through this interpreter (``sys.executable -m ...``).
"""

from __future__ import annotations

import argparse
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NamedTuple

BASE_REF = "origin/main"
DEFAULT_TIMEOUT = 900
TIMEOUT_RC = 124  # same convention as coreutils `timeout`
NOT_FOUND_RC = 127  # same convention as a shell for "command not found"
RAW_OUTPUT_LINES = 40

# Three dots: only what this branch changed since it left origin/main, not what others merged.
DIFF_ARGS = ["diff", "-z", "--name-only", f"{BASE_REF}...HEAD"]
STATUS_ARGS = ["status", "--porcelain", "-z", "--untracked-files=all"]

# Mirrors the `exclude` of the detect-secrets hook in .pre-commit-config.yaml.
SECRETS_EXCLUDE = re.compile(
    r"^(\.secrets\.baseline"
    r"|tests/fixtures/cassettes/.*\.json"
    r"|tests/fixtures/projects/.*\.(json|jsonl)"
    r"|tests/golden/.*\.json)$"
)

# Known, platform-only findings. On Windows/NTFS every file looks executable (ruff EXE002),
# and fcntl does not exist (mypy errors in log.py). Linux CI never sees either.
RUFF_KNOWN_WIN32 = re.compile(r"^\S.*:\d+:\d+: EXE002 ")
RUFF_FINDING = re.compile(r"^\S.*:\d+:\d+: [A-Z]+\d+ ")
MYPY_KNOWN_WIN32 = re.compile(
    r"^src[/\\]prismabib[/\\]log\.py:\d+: error: "
    r'Module has no attribute "(?:flock|LOCK_SH|LOCK_EX|LOCK_UN)"\s+\[attr-defined\]\s*$'
)
MYPY_FINDING = re.compile(r"^\S.*:\d+: error: ")
SECRET_LOCATION = re.compile(r"^Location:\s+(.+):(\d+)\s*$", re.MULTILINE)

Runner = Callable[[Sequence[str]], tuple[int, str]]
Evaluator = Callable[[int, str], tuple[bool, str]]


class Step(NamedTuple):
    name: str
    cmd: list[str]
    evaluate: Evaluator | None = None


class StepResult(NamedTuple):
    name: str
    cmd: list[str]
    ok: bool
    note: str
    output: str


def filter_known(
    output: str, known: re.Pattern[str], finding: re.Pattern[str]
) -> tuple[str, int, int]:
    """Drop lines matching ``known``; return (kept text, dropped count, remaining findings)."""
    kept: list[str] = []
    dropped = 0
    for line in output.splitlines():
        if known.search(line):
            dropped += 1
        else:
            kept.append(line)
    return "\n".join(kept), dropped, sum(1 for ln in kept if finding.search(ln))


def _evaluator(known: re.Pattern[str], finding: re.Pattern[str], platform: str) -> Evaluator:
    def evaluate(rc: int, out: str) -> tuple[bool, str]:
        if rc == 0:
            return True, ""
        if platform != "win32":
            return False, ""
        _, dropped, remaining = filter_known(out, known, finding)
        note = f"filtered {dropped} known win32 platform finding(s)"
        # rc != 0 with nothing left and nothing filtered is a crash, not a pass.
        return (remaining == 0 and dropped > 0), note

    return evaluate


def ruff_evaluator(platform: str) -> Evaluator:
    return _evaluator(RUFF_KNOWN_WIN32, RUFF_FINDING, platform)


def mypy_evaluator(platform: str) -> Evaluator:
    return _evaluator(MYPY_KNOWN_WIN32, MYPY_FINDING, platform)


def parse_changed_files(diff_z: str, porcelain_z: str) -> list[str]:
    """Union of ``git diff -z --name-only`` and ``git status --porcelain -z`` paths (sorted).

    NUL-delimited, so names with spaces, quotes or non-ASCII characters arrive unquoted.
    Porcelain v1 entries are ``XY path``; a rename or copy is ``XY new`` followed by an extra
    ``old`` field, which is skipped (the old path no longer exists).
    """
    paths = {p for p in diff_z.split("\0") if p}
    entries = iter(porcelain_z.split("\0"))
    for entry in entries:
        if len(entry) < 4:
            continue
        status, path = entry[:2], entry[3:]
        if status[0] in "RC" or status[1] in "RC":
            next(entries, None)
        if "D" not in status:
            paths.add(path)
    return sorted(paths)


def secrets_candidates(changed: Sequence[str], exists: Callable[[str], bool]) -> list[str]:
    return [p for p in changed if not SECRETS_EXCLUDE.match(p) and exists(p)]


def parse_secret_findings(output: str) -> list[tuple[str, int]]:
    """(file, line) pairs from detect-secrets hook output; never the secret itself."""
    return [(m.group(1), int(m.group(2))) for m in SECRET_LOCATION.finditer(output)]


def secrets_evaluator(rc: int, out: str) -> tuple[bool, str]:
    findings = parse_secret_findings(out)
    if findings:
        lines = [f"  {path}:{line}" for path, line in findings]
        hint = "false positive? append `# pragma: allowlist secret` to that line"
        return False, "possible secret(s) at:\n" + "\n".join(lines) + "\n  " + hint
    if rc == 0:
        return True, ""
    # Stale baseline, exit 3, a crash: never fail silently. The hook prints types and
    # locations, not secret values, so echoing its output is safe.
    raw = "\n".join(out.splitlines()[:RAW_OUTPUT_LINES])
    return False, f"hook failed (exit {rc}) without a parseable finding; raw output:\n{raw}"


def select_tests(changed: Sequence[str], exists: Callable[[str], bool]) -> list[str] | None:
    """Test files for a ``--changed`` run, or None meaning 'run the full unit+contract set'."""
    selected: set[str] = set()
    for path in changed:
        if path.startswith("tests/") and Path(path).name.startswith("test_"):
            if exists(path):  # a deleted or renamed-away test must never reach pytest
                selected.add(path)
        elif path.startswith("src/prismabib/") and path.endswith(".py"):
            candidate = f"tests/unit/test_{Path(path).stem}.py"
            if not exists(candidate):
                return None
            selected.add(candidate)
        else:
            return None  # config, docs, conftest, ...: unsure, so run everything
    return sorted(selected) or None


def build_steps(
    *,
    platform: str,
    quick: bool,
    skip_mypy: bool,
    workers: int,
    secrets_files: Sequence[str],
    baseline: str | None,
    test_paths: Sequence[str] | None,
) -> list[Step]:
    py = sys.executable
    steps = [
        Step(
            "ruff check",
            [py, "-m", "ruff", "check", ".", "--output-format", "concise"],
            ruff_evaluator(platform),
        ),
        Step("ruff format --check", [py, "-m", "ruff", "format", "--check", "."]),
    ]
    if secrets_files and baseline:
        hook = "import sys; from detect_secrets.pre_commit_hook import main; sys.exit(main())"
        steps.append(
            Step(
                "detect-secrets",
                [py, "-c", hook, "--baseline", baseline, "--", *secrets_files],
                secrets_evaluator,
            )
        )
    if not skip_mypy:
        steps.append(
            Step(
                "mypy --strict",
                [py, "-m", "mypy", "--strict", "src/prismabib"],
                mypy_evaluator(platform),
            )
        )
    if not quick:
        pytest_cmd = [py, "-m", "pytest", "-m", "unit or contract", "-q", "--no-cov"]
        pytest_cmd += ["-n", str(workers), *(test_paths or [])]
        steps.append(Step("pytest unit+contract", pytest_cmd))
    return steps


def run_steps(steps: Sequence[Step], runner: Runner, fail_fast: bool = False) -> list[StepResult]:
    results: list[StepResult] = []
    for step in steps:
        rc, out = runner(step.cmd)
        if rc == TIMEOUT_RC:
            ok, note = False, f"timed out: {out.strip()}"
        else:
            ok, note = step.evaluate(rc, out) if step.evaluate else (rc == 0, "")
        results.append(StepResult(step.name, step.cmd, ok, note, out))
        if fail_fast and not ok:
            break
    return results


def summarize(results: Sequence[StepResult]) -> tuple[str, int]:
    """Human summary plus the process exit code (non-zero if any step failed)."""
    lines = ["", "== pre-push summary =="]
    for r in results:
        extra = f"  ({r.note})" if r.ok and r.note else ""
        lines.append(f"[{'ok' if r.ok else 'FAIL'}] {r.name}{extra}")
    failed = [r for r in results if not r.ok]
    if failed:
        lines.append("")
        lines.append(f"{len(failed)} step(s) failed:")
        for r in failed:
            lines.append(f"- {r.name}")
            if r.note:
                lines.append(f"  {r.note}")
            lines.append(f"  re-run: {shlex.join(r.cmd)}")
    else:
        lines.append("All steps passed.")
    return "\n".join(lines), 1 if failed else 0


def _run(cmd: Sequence[str], cwd: str, timeout: float = DEFAULT_TIMEOUT) -> tuple[int, str]:
    """Run ``cmd``; a timeout or a missing binary becomes an exit code, not a traceback."""
    try:
        proc = subprocess.run(list(cmd), cwd=cwd, capture_output=True, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        return TIMEOUT_RC, f"{cmd[0]} step exceeded the {timeout:g}s timeout (--timeout N)"
    except FileNotFoundError:
        return NOT_FOUND_RC, f"command not found: {cmd[0]}"
    # UTF-8 explicitly: git -z emits raw UTF-8 paths whatever the console code page is.
    return proc.returncode, (proc.stdout + proc.stderr).decode("utf-8", errors="replace")


def _git(root: str, *args: str) -> tuple[int, str]:
    return _run(["git", *args], root)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Local pre-push gate (docs/how-to/pre-push-check.md)")
    ap.add_argument("--quick", action="store_true", help="skip pytest")
    ap.add_argument("--changed", action="store_true", help="only tests of changed modules")
    ap.add_argument("--workers", type=int, default=2, help="pytest-xdist workers (default 2)")
    ap.add_argument("--skip-mypy", action="store_true", help="skip mypy (CI will still run it)")
    ap.add_argument("--fail-fast", action="store_true", help="stop at the first failed step")
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="seconds per step")
    args = ap.parse_args(argv)

    rc, top = _run(["git", "rev-parse", "--show-toplevel"], ".")
    if rc == NOT_FOUND_RC:
        print("git is not installed or not on PATH", file=sys.stderr)
        return 2
    if rc != 0:
        print("not inside a git checkout", file=sys.stderr)
        return 2
    root = top.strip()

    rc, diff = _git(root, *DIFF_ARGS)
    if rc != 0:
        print(f"warning: {BASE_REF} not found (git fetch origin?); using local changes only")
        diff = ""
    _, porcelain = _git(root, *STATUS_ARGS)
    changed = parse_changed_files(diff, porcelain)

    def exists(p: str) -> bool:
        return (Path(root) / p).is_file()

    if args.skip_mypy:
        print("WARNING: --skip-mypy: CI `lint` runs mypy --strict and may still fail.")
    test_paths = select_tests(changed, exists) if args.changed else None

    with tempfile.TemporaryDirectory() as tmp:
        # The hook may rewrite its baseline; give it a copy so no tracked file changes.
        baseline = None
        if exists(".secrets.baseline"):
            baseline = str(Path(tmp) / "baseline.json")
            shutil.copy(Path(root) / ".secrets.baseline", baseline)
        steps = build_steps(
            platform=sys.platform,
            quick=args.quick,
            skip_mypy=args.skip_mypy,
            workers=args.workers,
            secrets_files=secrets_candidates(changed, exists),
            baseline=baseline,
            test_paths=test_paths,
        )
        results = run_steps(steps, lambda cmd: _run(cmd, root, args.timeout), args.fail_fast)

    for r in results:
        if not r.ok and r.name != "detect-secrets":  # secrets output is never echoed
            print(f"\n--- {r.name} ---\n{r.output.rstrip()}")
    text, code = summarize(results)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
