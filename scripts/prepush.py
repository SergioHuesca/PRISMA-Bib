"""Local pre-push gate: the checks that turn CI red, run before the push.

Runs ruff check, ruff format --check (whole tree), detect-secrets over the changed files,
mypy --strict and the unit + contract tests, then prints a summary listing every failed step
with the exact command to re-run it. It never commits, pushes or changes a tracked file.

    python scripts/prepush.py [--quick] [--changed] [--workers N] [--skip-mypy] [--fail-fast]

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
MYPY_KNOWN_WIN32 = re.compile(r"^src[/\\]prismabib[/\\]log\.py:\d+: error: .*(?:flock|LOCK_|fcntl)")
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


def parse_changed_files(diff_names: str, porcelain: str) -> list[str]:
    """Union of ``git diff --name-only`` and ``git status --porcelain`` paths (sorted)."""
    paths = {ln.strip() for ln in diff_names.splitlines() if ln.strip()}
    for line in porcelain.splitlines():
        if len(line) < 4 or "D" in line[:2]:
            continue
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if path:
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
    return rc == 0, ""


def select_tests(changed: Sequence[str], exists: Callable[[str], bool]) -> list[str] | None:
    """Test files for a ``--changed`` run, or None meaning 'run the full unit+contract set'."""
    selected: set[str] = set()
    for path in changed:
        if path.startswith("tests/") and Path(path).name.startswith("test_"):
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
                [py, "-c", hook, "--baseline", baseline, *secrets_files],
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


def _run(cmd: Sequence[str], cwd: str) -> tuple[int, str]:
    proc = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, check=False)
    return proc.returncode, proc.stdout + proc.stderr


def _git(root: str, *args: str) -> tuple[int, str]:
    return _run(["git", *args], root)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Local pre-push gate (docs/how-to/pre-push-check.md)")
    ap.add_argument("--quick", action="store_true", help="skip pytest")
    ap.add_argument("--changed", action="store_true", help="only tests of changed modules")
    ap.add_argument("--workers", type=int, default=2, help="pytest-xdist workers (default 2)")
    ap.add_argument("--skip-mypy", action="store_true", help="skip mypy (CI will still run it)")
    ap.add_argument("--fail-fast", action="store_true", help="stop at the first failed step")
    args = ap.parse_args(argv)

    rc, top = _run(["git", "rev-parse", "--show-toplevel"], ".")
    if rc != 0:
        print("not inside a git checkout", file=sys.stderr)
        return 2
    root = top.strip()

    rc, diff = _git(root, "diff", "--name-only", BASE_REF)
    if rc != 0:
        print(f"warning: {BASE_REF} not found (git fetch origin?); using local changes only")
        diff = ""
    _, porcelain = _git(root, "status", "--porcelain", "--untracked-files=all")
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
        results = run_steps(steps, lambda cmd: _run(cmd, root), args.fail_fast)

    for r in results:
        if not r.ok and r.name != "detect-secrets":  # secrets output is never echoed
            print(f"\n--- {r.name} ---\n{r.output.rstrip()}")
    text, code = summarize(results)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
