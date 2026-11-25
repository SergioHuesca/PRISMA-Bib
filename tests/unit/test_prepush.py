"""The local pre-push gate (``scripts/prepush.py``): its pure decision functions.

No test runs a real tool or touches the network; the step runner takes an injected runner.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "prepush.py"
_spec = importlib.util.spec_from_file_location("prepush", _SCRIPT)
assert _spec is not None and _spec.loader is not None
prepush = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prepush)

KNOWN = 'src\\prismabib\\log.py:166: error: Module has no attribute "flock"  [attr-defined]'
KNOWN_LOCK = 'src/prismabib/log.py:170: error: Module has no attribute "LOCK_SH"  [attr-defined]'
ASSIGNMENT = (
    "src\\prismabib\\log.py:201: error: Incompatible types in assignment "
    '(expression has type "flock", variable has type "int")  [assignment]'
)
UNKNOWN = "src\\prismabib\\log.py:12: error: Incompatible return value type  [return-value]"
OTHER_FILE = 'src\\prismabib\\store.py:9: error: Module has no attribute "flock"  [attr-defined]'


def _build(**overrides: object) -> list[object]:
    kwargs: dict[str, object] = {
        "platform": "linux",
        "quick": False,
        "skip_mypy": False,
        "workers": 2,
        "secrets_files": ["a.py"],
        "baseline": "b.json",
        "test_paths": None,
    }
    kwargs.update(overrides)
    return prepush.build_steps(**kwargs)


def _names(steps: list[object]) -> list[str]:
    return [s.name for s in steps]  # type: ignore[attr-defined]


@pytest.mark.unit
def test_mypy_filter__known_fcntl_errors_on_win32__are_dropped_and_counted() -> None:
    out = f"{KNOWN}\n{KNOWN_LOCK}\nFound 2 errors in 1 file"

    ok, note = prepush.mypy_evaluator("win32")(1, out)

    assert ok
    assert "filtered 2" in note


@pytest.mark.unit
def test_mypy_filter__unknown_error_next_to_known_ones__still_fails() -> None:
    out = f"{KNOWN}\n{UNKNOWN}"

    ok, _ = prepush.mypy_evaluator("win32")(1, out)

    assert not ok


@pytest.mark.unit
def test_mypy_filter__same_message_in_another_file__is_not_filtered() -> None:
    ok, _ = prepush.mypy_evaluator("win32")(1, OTHER_FILE)

    assert not ok


@pytest.mark.unit
def test_mypy_filter__known_errors_on_linux__fail() -> None:
    ok, _ = prepush.mypy_evaluator("linux")(1, KNOWN)

    assert not ok


@pytest.mark.unit
def test_mypy_filter__nonzero_exit_without_any_error_line__fails() -> None:
    ok, _ = prepush.mypy_evaluator("win32")(2, "Traceback: mypy crashed")

    assert not ok


@pytest.mark.unit
def test_ruff_filter__exe002_on_win32_passes_but_other_rules_fail() -> None:
    exe = "scripts/a.py:1:1: EXE002 The file is executable but no shebang is present"
    real = "scripts/a.py:3:1: F401 `os` imported but unused"

    assert prepush.ruff_evaluator("win32")(1, exe)[0]
    assert not prepush.ruff_evaluator("win32")(1, exe + "\n" + real)[0]
    assert not prepush.ruff_evaluator("linux")(1, exe)[0]


@pytest.mark.unit
def test_changed_files__diff_and_porcelain__are_merged_without_deleted_files() -> None:
    porcelain = ' M src/prismabib/log.py\x00?? scripts/new.py\x00R  renamed.py\x00old.py\x00 D gone.py\x00?? we"ird name.py\x00?? café.py'

    got = prepush.parse_changed_files("src/prismabib/log.py\0README.md\0", porcelain + "\0")

    assert got == [
        "README.md",
        "café.py",
        "renamed.py",
        "scripts/new.py",
        "src/prismabib/log.py",
        'we"ird name.py',
    ]


@pytest.mark.unit
def test_git_args__committed_changes__use_three_dot_diff_and_nul_output() -> None:
    assert prepush.DIFF_ARGS == ["diff", "-z", "--name-only", "origin/main...HEAD"]
    assert "-z" in prepush.STATUS_ARGS
    assert "--porcelain" in prepush.STATUS_ARGS


@pytest.mark.unit
def test_build_steps__option_like_file_name__is_separated_by_double_dash() -> None:
    step = _build(secrets_files=["-evil.py", "b.py"])[2]

    cmd = step.cmd  # type: ignore[attr-defined]
    assert cmd[cmd.index("-evil.py") - 1] == "--"


@pytest.mark.unit
def test_secrets_evaluator__failure_without_location__echoes_raw_output_truncated() -> None:
    out = "\n".join(f"line {i}" for i in range(100))

    ok, note = prepush.secrets_evaluator(3, out)

    assert not ok
    assert "exit 3" in note
    assert "line 0" in note
    assert "line 39" in note
    assert "line 40" not in note


@pytest.mark.unit
def test_mypy_filter__assignment_error_mentioning_flock__is_not_dropped() -> None:
    ok, _ = prepush.mypy_evaluator("win32")(1, f"{KNOWN}\n{ASSIGNMENT}")

    assert not ok


@pytest.mark.unit
def test_run_steps__timeout__fails_with_a_clear_note() -> None:
    results = prepush.run_steps([prepush.Step("slow", ["x"])], lambda _c: (124, "x exceeded"))
    text, code = prepush.summarize(results)

    assert code == 1
    assert "timed out" in text


@pytest.mark.unit
def test_run__missing_binary__returns_not_found_code_not_a_traceback() -> None:
    rc, out = prepush._run(["no-such-binary-prepush-xyz"], ".")

    assert rc == prepush.NOT_FOUND_RC
    assert "no-such-binary-prepush-xyz" in out


@pytest.mark.unit
def test_run__command_over_timeout__returns_timeout_code() -> None:
    rc, _ = prepush._run([sys.executable, "-c", "import time; time.sleep(30)"], ".", timeout=0.5)

    assert rc == prepush.TIMEOUT_RC


@pytest.mark.unit
def test_secrets_candidates__excluded_and_missing_paths__are_dropped() -> None:
    changed = ["a.py", "tests/golden/x.json", ".secrets.baseline", "gone.py"]

    got = prepush.secrets_candidates(changed, lambda p: p != "gone.py")

    assert got == ["a.py"]


@pytest.mark.unit
def test_secrets_evaluator__finding__is_reported_with_file_line_and_hint_not_the_secret() -> None:
    out = "Secret Type: Hex High Entropy String\nLocation:    src/x.py:42\nabc123hidden\n"

    ok, note = prepush.secrets_evaluator(1, out)

    assert not ok
    assert "src/x.py:42" in note
    assert "# pragma: allowlist secret" in note
    assert "abc123hidden" not in note


@pytest.mark.unit
def test_secrets_evaluator__clean_run__passes() -> None:
    assert prepush.secrets_evaluator(0, "")[0]


@pytest.mark.unit
def test_build_steps__quick__has_no_pytest_step() -> None:
    assert "pytest unit+contract" in _names(_build())
    assert "pytest unit+contract" not in _names(_build(quick=True))


@pytest.mark.unit
def test_build_steps__skip_mypy_and_no_secret_files__omit_those_steps() -> None:
    names = _names(_build(skip_mypy=True, secrets_files=[]))

    assert "mypy --strict" not in names
    assert "detect-secrets" not in names
    assert names[:2] == ["ruff check", "ruff format --check"]


@pytest.mark.unit
def test_build_steps__workers_and_test_paths__reach_pytest_command() -> None:
    step = _build(workers=3, test_paths=["tests/unit/test_log.py"])[-1]

    cmd = step.cmd  # type: ignore[attr-defined]
    assert cmd[cmd.index("-n") + 1] == "3"
    assert cmd[-1] == "tests/unit/test_log.py"


@pytest.mark.unit
def test_select_tests__mapped_modules__are_selected_else_full_run() -> None:
    exists = {"tests/unit/test_log.py"}.__contains__

    assert prepush.select_tests(["src/prismabib/log.py"], exists) == ["tests/unit/test_log.py"]
    assert prepush.select_tests(["src/prismabib/nomap.py"], exists) is None
    assert prepush.select_tests(["src/prismabib/log.py", "pyproject.toml"], exists) is None
    assert prepush.select_tests([], exists) is None


@pytest.mark.unit
def test_select_tests__deleted_test_file__is_not_passed_and_falls_back_to_full_set() -> None:
    exists = {"tests/unit/test_log.py"}.__contains__

    both = ["tests/unit/test_gone.py", "tests/unit/test_log.py"]
    assert prepush.select_tests(both, exists) == ["tests/unit/test_log.py"]
    assert prepush.select_tests(["tests/unit/test_gone.py"], exists) is None


@pytest.mark.unit
def test_run_steps__failing_step__gives_nonzero_exit_and_summary_lists_all_failures() -> None:
    steps = [
        prepush.Step("one", ["tool-a", "x"]),
        prepush.Step("two", ["tool-b"]),
        prepush.Step("three", ["tool-c", "--flag"]),
    ]
    rcs = {"tool-a": 1, "tool-b": 0, "tool-c": 3}

    results = prepush.run_steps(steps, lambda cmd: (rcs[cmd[0]], ""))
    text, code = prepush.summarize(results)

    assert code == 1
    assert "re-run: tool-a x" in text
    assert "re-run: tool-c --flag" in text
    assert "[ok] two" in text
    assert "2 step(s) failed" in text


@pytest.mark.unit
def test_run_steps__fail_fast__stops_after_first_failure() -> None:
    steps = [prepush.Step("one", ["a"]), prepush.Step("two", ["b"])]

    results = prepush.run_steps(steps, lambda _cmd: (1, ""), fail_fast=True)

    assert [r.name for r in results] == ["one"]


@pytest.mark.unit
def test_summarize__all_green__exits_zero() -> None:
    results = prepush.run_steps([prepush.Step("one", ["a"])], lambda _cmd: (0, ""))

    assert prepush.summarize(results)[1] == 0


class _FakeRunner:
    """Records every command it is asked to run; answers from ``rcs`` keyed by argv[0]."""

    def __init__(self, rcs: dict[str, tuple[int, str]]) -> None:
        self.rcs = rcs
        self.calls: list[str] = []

    def __call__(self, cmd: Sequence[str]) -> tuple[int, str]:
        self.calls.append(cmd[0])
        return self.rcs[cmd[0]]


@pytest.mark.unit
def test_run_steps__prerequisite_failed__dependent_is_skipped_and_never_run() -> None:
    steps = [prepush.Step("base", ["tool-a"]), prepush.Step("dep", ["tool-b"], after="base")]
    runner = _FakeRunner({"tool-a": (1, ""), "tool-b": (0, "")})

    results = prepush.run_steps(steps, runner)

    assert runner.calls == ["tool-a"]
    assert [(r.name, r.skipped, r.note) for r in results] == [
        ("base", False, ""),
        ("dep", True, "prerequisite 'base' failed"),
    ]


@pytest.mark.unit
def test_run_steps__prerequisite_passed__dependent_runs_normally() -> None:
    steps = [prepush.Step("base", ["tool-a"]), prepush.Step("dep", ["tool-b"], after="base")]
    runner = _FakeRunner({"tool-a": (0, ""), "tool-b": (0, "")})

    results = prepush.run_steps(steps, runner)

    assert runner.calls == ["tool-a", "tool-b"]
    assert [r.skipped for r in results] == [False, False]


@pytest.mark.unit
def test_run_steps__prerequisite_itself_skipped__dependent_is_skipped() -> None:
    steps = [
        prepush.Step("base", [], skip_reason="--quick"),
        prepush.Step("dep", ["tool-b"], after="base"),
    ]
    runner = _FakeRunner({"tool-b": (0, "")})

    results = prepush.run_steps(steps, runner)

    assert runner.calls == []
    assert results[1].note == "prerequisite 'base' did not run"


@pytest.mark.unit
def test_run_steps__step_excluded_by_option__is_skipped_with_reason_and_not_run() -> None:
    runner = _FakeRunner({})
    skips = prepush.option_skips(quick=True, skip_mypy=True)

    results = prepush.run_steps(skips, runner)
    text, code = prepush.summarize(results)

    assert runner.calls == []
    assert code == 0
    assert "[skip] mypy --strict  (--skip-mypy)" in text
    assert "[skip] pytest unit+contract  (--quick)" in text


@pytest.mark.unit
def test_summarize__passes_and_skips_only__exits_zero_with_counts() -> None:
    steps = [prepush.Step("a", ["x"]), prepush.Step("b", [], skip_reason="--quick")]

    text, code = prepush.summarize(prepush.run_steps(steps, _FakeRunner({"x": (0, "")})))

    assert code == 0
    assert "1 passed, 0 failed, 1 skipped" in text


@pytest.mark.unit
def test_summarize__failure_and_skip__exits_one_with_counts() -> None:
    steps = [prepush.Step("a", ["x"]), prepush.Step("b", ["y"], after="a")]

    text, code = prepush.summarize(prepush.run_steps(steps, _FakeRunner({"x": (1, "")})))

    assert code == 1
    assert "0 passed, 1 failed, 1 skipped" in text
    assert "[skip] b  (prerequisite 'a' failed)" in text


@pytest.mark.unit
def test_summarize__step_with_rerun_text__prints_it_instead_of_the_bare_command() -> None:
    step = prepush.Step("risk", ["tool-r", "--cov"], rerun="run the tests first, then tool-r")

    text, _ = prepush.summarize(prepush.run_steps([step], _FakeRunner({"tool-r": (1, "")})))

    assert "re-run: run the tests first, then tool-r" in text
    assert "re-run: tool-r --cov" not in text


@pytest.mark.unit
def test_summarize__timed_out_step__summary_line_names_the_timeout() -> None:
    runner = _FakeRunner({"slow": (124, "slow step exceeded the 5s timeout (--timeout N)")})

    text, _ = prepush.summarize(prepush.run_steps([prepush.Step("slow", ["slow"])], runner))

    assert "[FAIL] slow  (timed out)" in text


@pytest.mark.unit
def test_build_steps__each_option__selects_the_literal_step_set() -> None:
    full = [
        "ruff check",
        "ruff format --check",
        "detect-secrets",
        "mypy --strict",
        "duplicate-code",
        "pytest unit+contract",
    ]

    assert _names(_build()) == full
    assert _names(_build(quick=True)) == full[:5]
    assert _names(_build(skip_mypy=True)) == [*full[:3], *full[4:]]
    assert _names(_build(test_paths=["tests/unit/test_log.py"])) == full
    changed_cmd = _build(test_paths=["tests/unit/test_log.py"])[-1].cmd  # type: ignore[attr-defined]
    assert changed_cmd[-1] == "tests/unit/test_log.py"


@pytest.mark.unit
def test_select_tests__changed_file_list__selects_the_literal_test_set() -> None:
    exists = {"tests/unit/test_log.py", "tests/unit/test_store.py"}.__contains__

    got = prepush.select_tests(
        ["src/prismabib/store.py", "src/prismabib/log.py", "tests/unit/test_log.py"], exists
    )

    assert got == ["tests/unit/test_log.py", "tests/unit/test_store.py"]


_FIXTURES = Path(__file__).parent.parent / "fixtures" / "prepush"
_TWO_BLOCKS = (
    "************* Module prismabib.viz.__init__\n"
    "src\\prismabib\\viz\\__init__.py:1:0: R0801: Similar lines in 2 files\n"
    "==prismabib.a:[10:20]\n"
    "==prismabib.sub.b:[5:15]\n"
    "    x = 1 (duplicate-code)\n"
    "src\\prismabib\\viz\\__init__.py:1:0: R0801: Similar lines in 2 files\n"
    "==prismabib.c:[100:110]\n"
    "==prismabib.d.__init__:[1:11]\n"
    "    y = 2 (duplicate-code)\n"
)
_Added = dict[str, list[tuple[int, int]]]


@pytest.mark.unit
def test_parse_pylint_duplicates__real_fixture__reads_nine_pairs_with_two_locations() -> None:
    text = (_FIXTURES / "pylint_duplicate_code_6.txt").read_text(encoding="utf-8")

    blocks = prepush.parse_pylint_duplicates(text)

    assert len(blocks) == 9
    assert [len(b.locations) for b in blocks] == [2] * 9
    assert blocks[0].locations == (
        ("src/prismabib/gui/capture_layer.py", 141, 163),
        ("src/prismabib/gui/openalex_capture_layer.py", 85, 107),
    )


@pytest.mark.unit
def test_parse_pylint_duplicates__wrong_format__fails_clearly() -> None:
    with pytest.raises(ValueError, match="expected a '==module"):
        prepush.parse_pylint_duplicates("x.py:1:0: R0801: Similar lines in 2 files\nnot a location")
    with pytest.raises(ValueError, match="R0801"):
        prepush.parse_pylint_duplicates("R0801 but a different shape")
    assert prepush.parse_pylint_duplicates("") == []


@pytest.mark.unit
def test_normalize_path__backslashes_and_root__gives_forward_slash_relative_form() -> None:
    assert prepush.normalize_path("D:\\repo\\src\\prismabib\\a.py", "D:\\repo") == (
        "src/prismabib/a.py"
    )
    assert prepush.normalize_path(".\\src\\a.py") == "src/a.py"
    assert prepush.normalize_path("src/a.py", "/repo") == "src/a.py"


_DIFF = (
    "diff --git a/src/prismabib/a.py b/src/prismabib/a.py\n"
    "--- a/src/prismabib/a.py\n"
    "+++ b/src/prismabib/a.py\n"
    "@@ -3,0 +4,2 @@ def f():\n"
    "+x\n"
    "+y\n"
    "@@ -30 +32 @@\n"
    "-old\n"
    "+new\n"
    "@@ -40,3 +41,0 @@\n"
    "-gone\n"
    "diff --git a/src/prismabib/old.py b/src/prismabib/old.py\n"
    "--- a/src/prismabib/old.py\n"
    "+++ /dev/null\n"
    "@@ -1,2 +0,0 @@\n"
)


@pytest.mark.unit
def test_parse_added_lines__zero_context_diff__keeps_only_new_side_additions() -> None:
    assert prepush.parse_added_lines(_DIFF) == {"src/prismabib/a.py": [(4, 5), (32, 32)]}


@pytest.mark.unit
def test_filter_duplicates__location_overlapping_an_added_line__is_reported() -> None:
    blocks = prepush.parse_pylint_duplicates(_TWO_BLOCKS)

    kept = prepush.filter_duplicates(blocks, {"src/prismabib/sub/b.py": [(14, 14)]})

    assert [b.locations[0][0] for b in kept] == ["src/prismabib/a.py"]


@pytest.mark.unit
def test_filter_duplicates__touched_file_but_unchanged_block__is_not_reported() -> None:
    blocks = prepush.parse_pylint_duplicates(_TWO_BLOCKS)

    # a.py is touched, outside lines 10-19; the removal-only hunk adds nothing at all.
    assert prepush.filter_duplicates(blocks, {"src/prismabib/a.py": [(20, 30)]}) == []
    assert prepush.filter_duplicates(blocks, prepush.parse_added_lines(_DIFF)) == []


@pytest.mark.unit
def test_untracked_files__porcelain__lists_only_untracked_paths() -> None:
    porcelain = "?? src/prismabib/new.py\0 M src/prismabib/a.py\0A  src/b.py\0"

    assert prepush.untracked_files(porcelain) == ["src/prismabib/new.py"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("C:\\v\\python.exe: No module named pylint\n", True),
        ("/v/bin/python: No module named pylint", True),
        ("C:\\v\\python.exe: No module named radon\n", False),
        ("E   ModuleNotFoundError: No module named 'pylint'", False),
        ("C:\\v\\python.exe: No module named pylint.extra", False),
        ("see: No module named pylint, then retry", False),
    ],
)
def test_missing_module_note__line_forms__only_the_exact_interpreter_line_matches(
    output: str, expected: bool
) -> None:
    note = prepush.missing_module_note(output, "pylint")

    assert (note is not None) is expected
    if note:
        assert "'pylint'" in note
        assert "uv sync --all-extras" in note


def _dup_step(added: _Added | None = None) -> Any:
    return next(s for s in _build(added_lines=added) if s.name == "duplicate-code")  # type: ignore[attr-defined]


@pytest.mark.unit
def test_duplication_step__command__scans_the_whole_tree_at_the_recorded_similarity() -> None:
    assert _dup_step().cmd[1:] == [
        "-m",
        "pylint",
        "--disable=all",
        "--enable=duplicate-code",
        "--min-similarity-lines=6",
        "--persistent=n",
        "--score=n",
        "src/prismabib",
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "options",
    [{}, {"quick": True}, {"test_paths": ["tests/unit/test_log.py"]}, {"skip_mypy": True}],
)
def test_duplication_step__every_option__is_selected(options: dict[str, object]) -> None:
    assert "duplicate-code" in _names(_build(**options))
    skipped = [s.name for s in prepush.option_skips(quick=True, skip_mypy=True)]
    assert "duplicate-code" not in skipped


@pytest.mark.unit
def test_duplication_step__block_on_a_changed_line__fails_and_names_both_locations() -> None:
    step = _dup_step({"src/prismabib/sub/b.py": [(14, 14)]})

    (result,) = prepush.run_steps([step], _FakeRunner({step.cmd[0]: (8, _TWO_BLOCKS)}))

    assert not result.ok
    assert "src/prismabib/a.py:10-19  <->  src/prismabib/sub/b.py:5-14" in result.note
    assert "prismabib/c.py" not in result.note


@pytest.mark.unit
def test_duplication_step__block_outside_the_change__passes_with_a_note() -> None:
    step = _dup_step({"src/prismabib/other.py": [(1, 9)]})

    (result,) = prepush.run_steps([step], _FakeRunner({step.cmd[0]: (8, _TWO_BLOCKS)}))

    assert result.ok
    assert result.note == "2 known duplicate block(s) outside this change"


@pytest.mark.unit
def test_duplication_step__clean_run__passes() -> None:
    step = _dup_step()

    (result,) = prepush.run_steps([step], _FakeRunner({step.cmd[0]: (0, "")}))

    assert result.ok


@pytest.mark.unit
def test_duplication_step__crash_or_unparseable_output__fails() -> None:
    step = _dup_step()
    py = step.cmd[0]

    (crash,) = prepush.run_steps([step], _FakeRunner({py: (32, "usage: pylint")}))
    (bad,) = prepush.run_steps([step], _FakeRunner({py: (8, "R0801 odd shape")}))

    assert not crash.ok
    assert not bad.ok


class _Runner:
    """Answers every command with rc 0, except pylint, which reports itself missing."""

    def __init__(self, pylint: tuple[int, str]) -> None:
        self.pylint = pylint
        self.calls: list[str] = []

    def __call__(self, cmd: Sequence[str]) -> tuple[int, str]:
        self.calls.append(" ".join(cmd[1:3]))
        return self.pylint if "pylint" in cmd else (0, "")


@pytest.mark.unit
def test_duplication_step__pylint_missing__fails_with_note_and_other_steps_still_run() -> None:
    runner = _Runner((1, "C:\\v\\python.exe: No module named pylint\n"))

    results = prepush.run_steps(_build(), runner)

    by_name = {r.name: r for r in results}
    assert not by_name["duplicate-code"].ok
    assert by_name["duplicate-code"].note == (
        "module 'pylint' is missing; install the development dependencies: uv sync --all-extras"
    )
    assert by_name["pytest unit+contract"].ok
    assert "-m pytest" in runner.calls


@pytest.mark.unit
def test_missing_module_phrase__in_a_failing_test_step__is_an_ordinary_failure() -> None:
    pytest_step = next(s for s in _build() if s.name == "pytest unit+contract")
    runner = _FakeRunner({pytest_step.cmd[0]: (1, "E   ModuleNotFoundError: No module named 'x'")})

    (result,) = prepush.run_steps([pytest_step], runner)

    assert not result.ok
    assert "missing" not in result.note
