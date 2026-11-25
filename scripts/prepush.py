"""Local pre-push gate: the checks that turn CI red, run before the push.

Runs ruff check, ruff format --check (whole tree), detect-secrets over the changed files,
mypy --strict, pylint's duplicate-code check (blocks touching this branch's lines) and the
unit + contract tests (with coverage) and the complexity-risk step that reads that coverage
(skipped under --quick, --changed and when the tests fail), then prints a summary listing every failed step
with the exact command to re-run it. It never commits, pushes or changes a tracked file.

    python scripts/prepush.py [--quick] [--changed] [--workers N] [--skip-mypy] [--fail-fast]
                                 [--timeout N]

Project tools are invoked through this interpreter (``sys.executable -m ...``).
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import tomllib
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

# Measured in #450 on main 308bb02: 4 -> 31 pairs, 6 -> 9, 8 -> 4, 10 -> 2. Change it only in
# its own change, never in the feature change that hit it (docs/how-to/pre-push-check.md).
MIN_SIMILARITY_LINES = 6
PYLINT_REFACTOR_RC = 8
DEV_INSTALL_HINT = "install the development dependencies: uv sync --all-extras"
DUP_HEADER = re.compile(r"R0801: Similar lines in (\d+) files")
DUP_LOCATION = re.compile(r"^==([\w.]+):\[(\d+):(\d+)\]\s*$")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")

Runner = Callable[[Sequence[str]], tuple[int, str]]
Evaluator = Callable[[int, str], tuple[bool, str]]


class Step(NamedTuple):
    name: str
    cmd: list[str]
    evaluate: Evaluator | None = None
    after: str | None = None  # name of a prerequisite step; skipped when it did not pass
    skip_reason: str | None = None  # set: never run, recorded as skipped with this reason
    rerun: str | None = (
        None  # printed instead of the bare command (e.g. "run pytest first, then ...")
    )


class StepResult(NamedTuple):
    name: str
    cmd: list[str]
    ok: bool  # True for a skipped step too: skipped is neither passed nor failed
    note: str
    output: str
    skipped: bool = False
    rerun: str | None = None


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


def normalize_path(path: str, root: str = "") -> str:
    """Forward-slash path relative to ``root``: the one form every rule compares."""
    p = path.replace("\\", "/")
    r = root.replace("\\", "/").rstrip("/")
    if r and p.startswith(r + "/"):
        p = p[len(r) + 1 :]
    while p.startswith("./"):
        p = p[2:]
    return p


def missing_module_note(output: str, module: str) -> str | None:
    """Note for a line that is exactly the interpreter's ``<exe>: No module named <module>``.

    Only for the radon, coverage and pylint invocations. A pytest failure message such as
    ``ModuleNotFoundError: No module named 'x'`` is quoted, so it never matches.
    """
    if re.search(rf"^\S.*: No module named {re.escape(module)}\s*$", output, re.MULTILINE):
        return f"module '{module}' is missing; {DEV_INSTALL_HINT}"
    return None


class DuplicateBlock(NamedTuple):
    """One pylint duplicate-code group: its locations as (path, first line, last line)."""

    locations: tuple[tuple[str, int, int], ...]


def parse_pylint_duplicates(output: str) -> list[DuplicateBlock]:
    """Parse pylint R0801 groups. Paths come from the dotted module, never from the header.

    The header carries OS separators and a meaningless location; the ``==module:[a:b]`` lines
    are the real ones, with ``b`` exclusive. Raises ValueError on any other shape.
    """
    blocks: list[DuplicateBlock] = []
    lines = output.splitlines()
    i = 0
    while i < len(lines):
        header = DUP_HEADER.search(lines[i])
        i += 1
        if not header:
            continue
        locations = []
        for _ in range(int(header.group(1))):
            m = DUP_LOCATION.match(lines[i]) if i < len(lines) else None
            if not m:
                raise ValueError(
                    f"pylint output: expected a '==module:[a:b]' line, got {lines[i : i + 1]}"
                )
            path = "src/" + m.group(1).replace(".", "/") + ".py"
            locations.append((path, int(m.group(2)), int(m.group(3)) - 1))
            i += 1
        blocks.append(DuplicateBlock(tuple(locations)))
    if "R0801" in output and not blocks:
        raise ValueError("pylint output mentions R0801 but no duplicate group could be read")
    return blocks


def parse_added_lines(diff: str) -> dict[str, list[tuple[int, int]]]:
    """New-side line ranges (first, last) per file from ``git diff -U0`` output."""
    added: dict[str, list[tuple[int, int]]] = {}
    current: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            target = line[4:]
            current = normalize_path(target[2:]) if target.startswith("b/") else None
        elif line.startswith("@@") and current:
            m = HUNK.match(line)
            if m:
                start, count = int(m.group(1)), int(m.group(2) or 1)
                if count:
                    added.setdefault(current, []).append((start, start + count - 1))
    return added


def untracked_files(porcelain_z: str) -> list[str]:
    """Untracked, non-ignored paths from ``git status --porcelain -z``."""
    return sorted(e[3:] for e in porcelain_z.split("\0") if e.startswith("?? "))


def filter_duplicates(
    blocks: Sequence[DuplicateBlock], added: dict[str, list[tuple[int, int]]]
) -> list[DuplicateBlock]:
    """Keep blocks with a location that overlaps a line this branch added or edited."""
    return [
        b
        for b in blocks
        if any(
            first <= hi and lo <= last
            for path, first, last in b.locations
            for lo, hi in added.get(path, [])
        )
    ]


def duplication_evaluator(added: dict[str, list[tuple[int, int]]]) -> Evaluator:
    def evaluate(rc: int, out: str) -> tuple[bool, str]:
        if rc == 0:
            return True, ""
        if note := missing_module_note(out, "pylint"):
            return False, note
        if rc != PYLINT_REFACTOR_RC:  # anything but "R-messages only" is a crash, not a duplicate
            return False, f"pylint failed (exit {rc}) without a duplicate report"
        try:
            blocks = parse_pylint_duplicates(out)
        except ValueError as exc:
            return False, str(exc)
        mine = filter_duplicates(blocks, added)
        if not mine:
            return True, f"{len(blocks)} known duplicate block(s) outside this change"
        found = ["  " + "  <->  ".join(f"{p}:{a}-{b}" for p, a, b in blk.locations) for blk in mine]
        return False, "duplicated block(s) touching your changes; merge them:\n" + "\n".join(found)

    return evaluate


COMPLEXITY_THRESHOLD = 10
COVERAGE_SOURCE = "src/prismabib"


class FunctionRecord(NamedTuple):
    """One function from radon: identity is (file, qualname, ordinal); lines are not identity."""

    file: str
    qualname: str
    ordinal: int  # among functions with this qualname in this file, in line order from 1
    start: int  # the definition line, not the first decorator (radon, measured in #450)
    end: int
    complexity: int


class FileCoverage(NamedTuple):
    executed: frozenset[int]
    missing: frozenset[int]
    excluded: frozenset[int]


class Scored(NamedTuple):
    file: str
    qualname: str
    ordinal: int
    complexity: int
    coverage: float
    score: float


class LegacyEntry(NamedTuple):
    file: str
    qualname: str
    ordinal: int
    complexity: int


class Outcome(NamedTuple):
    kind: str  # "new" | "raised" | "known" | "stale"; all but "known" fail the step
    file: str
    qualname: str
    ordinal: int
    complexity: int  # now; 0 for a function that no longer exists
    score: float
    coverage: float
    recorded: int | None  # the legacy entry's complexity, None for "new"


def _radon_entries(item: object, prefix: str, file: str) -> list[tuple[str, int, int, int]]:
    """(qualname, start, end, complexity) for a function and, recursively, its closures."""
    if not isinstance(item, dict):
        raise ValueError(  # noqa: TRY004 - one error type for every wrong format
            f"radon output: {file}: expected an object per function, got {item!r}"
        )
    try:
        name = prefix + str(item["name"])
        row = (name, int(item["lineno"]), int(item["endline"]), int(item["complexity"]))
        closures = item.get("closures", [])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"radon output: {file}: function entry lacks {exc!r}") from exc
    rows = [row]
    for closure in closures:
        rows += _radon_entries(closure, name + ".", file)
    return rows


def parse_radon_cc(text: str, root: str = "") -> list[FunctionRecord]:
    """Function records from ``radon cc -s -j`` (JSON, not the text form: it has no end line).

    Methods are read from the flat ``method`` entries (radon also lists them under their class,
    so reading both would count each twice); classes themselves are ignored. Raises ValueError
    on any other shape.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"radon output is not JSON: {exc}") from exc
    if not isinstance(data, dict) or not all(isinstance(v, list) for v in data.values()):
        raise ValueError("radon output: expected an object mapping a file to a list of entries")
    records: list[FunctionRecord] = []
    for raw_file, items in data.items():
        file = normalize_path(raw_file, root)
        rows: list[tuple[str, int, int, int]] = []
        for item in items:
            kind = item.get("type") if isinstance(item, dict) else None
            if kind == "class":
                continue
            if kind == "method":  # ponytail: a nested class's methods are assumed flat too
                item = {**item, "name": f"{item.get('classname', '')}.{item.get('name', '')}"}
            rows += _radon_entries(item, "", file)
        seen: dict[str, int] = {}
        for name, start, end, cc in sorted(rows, key=lambda r: (r[1], r[0])):
            seen[name] = seen.get(name, 0) + 1
            records.append(FunctionRecord(file, name, seen[name], start, end, cc))
    return records


def parse_coverage_json(text: str, root: str = "") -> dict[str, FileCoverage]:
    """Per-file executed, missing and excluded lines from ``coverage json``.

    Keys are normalised. Raises ValueError on a wrong shape, and on data in which no line of
    any file executed: that is a run that collected nothing (e.g. the editable install pointing
    at another checkout), and reading it as 0% coverage would hide the cause.
    """
    try:
        data = json.loads(text)
        files = data["files"]
        result = {
            normalize_path(path, root): FileCoverage(
                frozenset(info["executed_lines"]),
                frozenset(info["missing_lines"]),
                frozenset(info["excluded_lines"]),
            )
            for path, info in files.items()
        }
    except (json.JSONDecodeError, KeyError, TypeError, AttributeError) as exc:
        raise ValueError(f"coverage JSON: unexpected shape ({exc!r})") from exc
    if result and not any(c.executed for c in result.values()):
        raise ValueError("coverage JSON: no line executed in any file; no data was collected")
    return result


def function_coverage(fn: FunctionRecord, cov: FileCoverage | None) -> float:
    """Share of the executable lines in the function's range that executed (nested included)."""
    if cov is None:
        return 0.0
    in_range = range(fn.start, fn.end + 1)
    executed = sum(1 for n in in_range if n in cov.executed and n not in cov.excluded)
    missing = sum(1 for n in in_range if n in cov.missing and n not in cov.excluded)
    return executed / (executed + missing) if executed + missing else 1.0


def crap_score(complexity: int, coverage: float) -> float:
    return complexity**2 * (1 - coverage) ** 3 + complexity


def score_functions(
    functions: Sequence[FunctionRecord], coverage: dict[str, FileCoverage]
) -> list[Scored]:
    """Join radon's ranges with coverage's lines. A file with no coverage data scores 0."""
    scored = []
    for f in functions:
        cov = function_coverage(f, coverage.get(f.file))
        scored.append(
            Scored(f.file, f.qualname, f.ordinal, f.complexity, cov, crap_score(f.complexity, cov))
        )
    return scored


def files_without_coverage(
    functions: Sequence[FunctionRecord], coverage: dict[str, FileCoverage]
) -> list[str]:
    """Files that have functions but no coverage data: an error, never read as covered."""
    return sorted({f.file for f in functions if f.file not in coverage})


def classify_outcomes(scored: Sequence[Scored], legacy: Sequence[LegacyEntry]) -> list[Outcome]:
    """new / raised / known / stale against the legacy list (see the #449 spec)."""
    listed = {(e.file, e.qualname, e.ordinal): e.complexity for e in legacy}
    outcomes: list[Outcome] = []
    present: set[tuple[str, str, int]] = set()
    for s in scored:
        key = (s.file, s.qualname, s.ordinal)
        present.add(key)
        recorded = listed.get(key)
        if recorded is None:
            kind = "new" if s.score > COMPLEXITY_THRESHOLD else ""
        elif s.complexity <= COMPLEXITY_THRESHOLD:
            kind = "stale"  # fixed: the entry must go
        else:
            kind = "raised" if s.complexity > recorded else "known"
        if kind:
            outcomes.append(Outcome(kind, *key, s.complexity, s.score, s.coverage, recorded))
    outcomes += [
        Outcome("stale", e.file, e.qualname, e.ordinal, 0, 0.0, 0.0, e.complexity)
        for e in legacy
        if (e.file, e.qualname, e.ordinal) not in present
    ]
    return sorted(outcomes, key=lambda o: (o.file, o.qualname, o.ordinal))


def load_exclude_lines(pyproject_text: str) -> list[str]:
    report = tomllib.loads(pyproject_text).get("tool", {}).get("coverage", {}).get("report", {})
    return [str(x) for x in report.get("exclude_lines", [])]


def coverage_config_text(data_file: str, exclude_lines: Sequence[str]) -> str:
    """Temporary coverage config: data file in the gate's temp dir, project exclusions kept.

    ``--cov-config`` replaces the pyproject coverage settings, so the exclusions are copied.
    """
    lines = ["[run]", f"data_file = {data_file}", "branch = True", "", "[report]"]
    lines += ["exclude_lines ="] + [f"    {x}" for x in exclude_lines]
    return "\n".join(lines) + "\n"


def write_coverage_config(tmp: str, exclude_lines: Sequence[str]) -> str:
    """Write the config under ``tmp`` (outside the repository); return its path."""
    data_dir = Path(tmp) / "covdata"
    data_dir.mkdir(exist_ok=True)  # coverage does not create the data file's directory
    cfg = Path(tmp) / "cov.ini"
    cfg.write_text(coverage_config_text(str(data_dir / ".coverage"), exclude_lines), "utf-8")
    return str(cfg)


def coverage_json_cmd(cov_config: str, out_json: str) -> list[str]:
    return [
        sys.executable,
        *("-m", "coverage", "json", f"--rcfile={cov_config}", "--fail-under=0"),
        *("-o", out_json),
    ]


LEGACY_LIST = "scripts/complexity-legacy.txt"
PLATFORM_NOTE = (
    "new-violation results may differ by platform (some tests skip on some platforms); "
    "the raise rule compares complexity only and is stable"
)
TEST_STEP = "pytest unit+contract"
COMPLEXITY_STEP = "complexity-risk"
COMPLEXITY_RERUN = (
    "a fresh test run comes first: python scripts/prepush.py (this step cannot run alone)"
)


def parse_legacy(text: str) -> list[LegacyEntry]:
    """Entries from the legacy list: one ``file qualname ordinal complexity`` per line."""
    entries = []
    for n, line in enumerate(text.splitlines(), 1):
        if not line:
            continue
        try:
            head, ordinal, complexity = line.rsplit(" ", 2)
            file, qualname = head.rsplit(" ", 1)  # only the file could hold a space
            entries.append(LegacyEntry(file, qualname, int(ordinal), int(complexity)))
        except ValueError as exc:
            raise ValueError(
                f"legacy list line {n}: want 'file qualname ordinal complexity'"
            ) from exc
    return entries


def complexity_scope(changed: Sequence[str]) -> list[str]:
    """The touched Python files under the package source tree (scripts and tests are out)."""
    paths = {normalize_path(p) for p in changed}
    return sorted(p for p in paths if p.startswith(COVERAGE_SOURCE + "/") and p.endswith(".py"))


def complexity_risk(
    runner: Runner,
    cov_config: str,
    legacy_text: str,
    touched: Sequence[str],
    exists: Callable[[str], bool],
) -> tuple[int, str]:
    """The child mode: coverage JSON + radon + the #453 rules; returns (exit code, output).

    Scores every function of each touched file in scope, and looks only at the legacy entries
    of those files. A deleted touched file has no functions, so its entries come out stale.
    """
    py = sys.executable
    if not touched:
        return 0, f"complexity-risk: no touched Python file under {COVERAGE_SOURCE}"
    with tempfile.TemporaryDirectory() as tmp:
        out_json = str(Path(tmp) / "cov.json")
        rc, out = runner(coverage_json_cmd(cov_config, out_json))
        note = missing_module_note(out, "coverage")
        if note or rc != 0:
            return 1, f"note: {note}" if note else f"coverage json failed:\n{out.rstrip()}"
        cov_text = Path(out_json).read_text("utf-8")
    present = [f for f in touched if exists(f)]
    radon_json = "{}"
    if present:
        rc, radon_json = runner([py, "-m", "radon", "cc", "-s", "-j", *present])
        note = missing_module_note(radon_json, "radon")
        if note or rc != 0:
            return 1, f"note: {note}" if note else f"radon failed:\n{radon_json.rstrip()}"
    try:
        functions = parse_radon_cc(radon_json)
        coverage = parse_coverage_json(cov_text)
        legacy = [e for e in parse_legacy(legacy_text) if e.file in touched]
    except ValueError as exc:
        return 1, f"error: {exc}"
    lines = [f"[error] {f}: no coverage data" for f in files_without_coverage(functions, coverage)]
    for o in classify_outcomes(score_functions(functions, coverage), legacy):
        name = f"{o.file} {o.qualname}#{o.ordinal}"
        if o.kind == "stale":
            lines.append(
                f"[stale] {name}: recorded {o.recorded}; gone or now at 10 or less: delete"
            )
            continue
        now = f"complexity {o.complexity}, score {o.score:.1f}, coverage {o.coverage:.2f}"
        if o.kind == "known" and o.recorded is not None and o.complexity < o.recorded:
            lines.append(
                f"[known] {name}: {now}; lower the entry from {o.recorded} to {o.complexity}"
            )
        else:
            was = f" (recorded {o.recorded})" if o.kind == "raised" else ""
            lines.append(f"[{o.kind}] {name}: {now}{was}")
    bad = [x for x in lines if not x.startswith("[known]")]
    known = len(lines) - len(bad)
    lines.append(f"complexity-risk: {len(bad)} problem(s), {known} known legacy function(s)")
    return (1 if bad else 0), "\n".join(lines)


def complexity_evaluator(rc: int, out: str) -> tuple[bool, str]:
    """The child's last line when it passed; its ``note:`` line or the platform note when not."""
    lines = [x for x in out.splitlines() if x.strip()]
    if rc == 0:
        return True, lines[-1] if lines else ""
    notes = [x[6:] for x in lines if x.startswith("note: ")]
    return False, notes[0] if notes else PLATFORM_NOTE


def build_steps(
    *,
    cov_config: str,
    platform: str,
    quick: bool,
    skip_mypy: bool,
    workers: int,
    secrets_files: Sequence[str],
    baseline: str | None,
    test_paths: Sequence[str] | None,
    added_lines: dict[str, list[tuple[int, int]]] | None = None,
    changed: bool = False,
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
    dup_cmd = [py, "-m", "pylint", "--disable=all", "--enable=duplicate-code"]
    dup_cmd += [f"--min-similarity-lines={MIN_SIMILARITY_LINES}", "--persistent=n", "--score=n"]
    # Whole tree: a run over touched files alone cannot see a block shared with an untouched one.
    steps.append(
        Step(
            "duplicate-code", [*dup_cmd, "src/prismabib"], duplication_evaluator(added_lines or {})
        )
    )
    if not quick:
        pytest_cmd = [py, "-m", "pytest", "-m", "unit or contract", "-q"]
        # Coverage on, for the complexity-risk step; 0 because this is a subset of the suite.
        pytest_cmd += [f"--cov={COVERAGE_SOURCE}", f"--cov-config={cov_config}"]
        pytest_cmd += ["--cov-fail-under=0", "--cov-report="]
        pytest_cmd += ["-n", str(workers), *(test_paths or [])]
        steps.append(Step(TEST_STEP, pytest_cmd))
    if not quick and not changed:
        # One command: this script again, in the child mode; it needs the test step's coverage.
        child = [py, str(Path(__file__).resolve()), "--complexity-risk", cov_config]
        steps.append(
            Step(
                COMPLEXITY_STEP,
                child,
                complexity_evaluator,
                after=TEST_STEP,
                rerun=COMPLEXITY_RERUN,
            )
        )
    return steps


def option_skips(*, quick: bool, skip_mypy: bool, changed: bool = False) -> list[Step]:
    """Steps that an option leaves out, as never-run steps carrying the reason."""
    skips = []
    if skip_mypy:
        skips.append(Step("mypy --strict", [], skip_reason="--skip-mypy"))
    if quick:
        skips.append(Step(TEST_STEP, [], skip_reason="--quick"))
    if quick or changed:
        reason = "--quick" if quick else "--changed"
        skips.append(Step(COMPLEXITY_STEP, [], skip_reason=reason, rerun=COMPLEXITY_RERUN))
    return skips


def _skipped(step: Step, reason: str) -> StepResult:
    return StepResult(step.name, step.cmd, True, reason, "", skipped=True, rerun=step.rerun)


def run_steps(steps: Sequence[Step], runner: Runner, fail_fast: bool = False) -> list[StepResult]:
    results: list[StepResult] = []
    by_name: dict[str, StepResult] = {}
    for step in steps:
        prereq = by_name.get(step.after) if step.after else None
        if step.skip_reason:
            result = _skipped(step, step.skip_reason)
        elif step.after and (prereq is None or prereq.skipped or not prereq.ok):
            state = "did not run" if prereq is None or prereq.skipped else "failed"
            result = _skipped(step, f"prerequisite '{step.after}' {state}")
        else:
            rc, out = runner(step.cmd)
            if rc == TIMEOUT_RC:
                ok, note = False, f"timed out: {out.strip()}"
            else:
                ok, note = step.evaluate(rc, out) if step.evaluate else (rc == 0, "")
            result = StepResult(step.name, step.cmd, ok, note, out, rerun=step.rerun)
        results.append(result)
        by_name[step.name] = result
        if fail_fast and not result.ok:
            break
    return results


def summarize(results: Sequence[StepResult]) -> tuple[str, int]:
    """Human summary plus the process exit code (non-zero if any step failed)."""
    lines = ["", "== pre-push summary =="]
    for r in results:
        if r.skipped:
            lines.append(f"[skip] {r.name}  ({r.note})")
            continue
        timeout = "  (timed out)" if not r.ok and r.note.startswith("timed out") else ""
        extra = f"  ({r.note})" if r.ok and r.note else timeout
        lines.append(f"[{'ok' if r.ok else 'FAIL'}] {r.name}{extra}")
    failed = [r for r in results if not r.ok]
    skipped = [r for r in results if r.skipped]
    passed = len(results) - len(failed) - len(skipped)
    lines.append(f"{passed} passed, {len(failed)} failed, {len(skipped)} skipped")
    if failed:
        lines.append("")
        lines.append(f"{len(failed)} step(s) failed:")
        for r in failed:
            lines.append(f"- {r.name}")
            if r.note:
                lines.append(f"  {r.note}")
            lines.append(f"  re-run: {r.rerun or shlex.join(r.cmd)}")
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
    ap.add_argument("--complexity-risk", metavar="COV_CONFIG", help=argparse.SUPPRESS)  # child mode
    args = ap.parse_args(argv)

    if args.complexity_risk:
        root = str(Path(__file__).resolve().parent.parent)
        try:
            legacy = (Path(root) / LEGACY_LIST).read_text("utf-8")
        except FileNotFoundError:
            legacy = ""
        rc, diff = _git(root, *DIFF_ARGS)
        _, porcelain = _git(root, *STATUS_ARGS)
        touched = complexity_scope(parse_changed_files(diff if rc == 0 else "", porcelain))
        code, text = complexity_risk(
            lambda cmd: _run(cmd, root, args.timeout),
            args.complexity_risk,
            legacy,
            touched,
            lambda p: (Path(root) / p).is_file(),
        )
        print(text)
        return code

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
    # Same base as the three-dot diff above; HEAD (local changes only) when it cannot resolve.
    mb_rc, mb = _git(root, "merge-base", BASE_REF, "HEAD")
    base = mb.strip() if mb_rc == 0 else "HEAD"
    _, zero_diff = _git(root, "-c", "core.quotepath=off", "diff", "-U0", "--no-color", base)
    added = parse_added_lines(zero_diff)
    for path in untracked_files(porcelain):
        try:  # every line of an untracked, non-ignored file counts as added
            count = len((Path(root) / path).read_bytes().splitlines())
        except OSError:
            continue
        if count:
            added[normalize_path(path)] = [(1, count)]

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
        try:
            exclude = load_exclude_lines((Path(root) / "pyproject.toml").read_text("utf-8"))
        except OSError:
            exclude = []
        steps = build_steps(
            cov_config=write_coverage_config(tmp, exclude),
            platform=sys.platform,
            quick=args.quick,
            skip_mypy=args.skip_mypy,
            workers=args.workers,
            secrets_files=secrets_candidates(changed, exists),
            baseline=baseline,
            test_paths=test_paths,
            added_lines=added,
            changed=args.changed,
        )
        steps += option_skips(quick=args.quick, skip_mypy=args.skip_mypy, changed=args.changed)
        results = run_steps(steps, lambda cmd: _run(cmd, root, args.timeout), args.fail_fast)

    for r in results:
        if not r.ok and r.name != "detect-secrets":  # secrets output is never echoed
            print(f"\n--- {r.name} ---\n{r.output.rstrip()}")
    text, code = summarize(results)
    print(text)
    return code


if __name__ == "__main__":
    sys.exit(main())
