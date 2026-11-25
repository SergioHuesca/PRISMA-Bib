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
        "cov_config": "/tmp-x/cov.ini",
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
    assert prepush.DIFF_ARGS == ["diff", "-z", "--name-only", "--no-renames", "origin/main...HEAD"]
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
    steps = _build(workers=3, test_paths=["tests/unit/test_log.py"])
    step = next(s for s in steps if s.name == "pytest unit+contract")  # type: ignore[attr-defined]

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
    assert "[skip] complexity-risk  (--quick)" in text


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
        "complexity-risk",
    ]
    changed = _build(test_paths=["tests/unit/test_log.py"], changed=True)

    assert _names(_build()) == full
    assert _names(_build(quick=True)) == full[:5]
    assert _names(_build(skip_mypy=True)) == [*full[:3], *full[4:]]
    assert _names(changed) == full[:6]
    assert changed[-1].cmd[-1] == "tests/unit/test_log.py"  # type: ignore[attr-defined]


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
def test_zero_context_diff_args__pins_and_existing_arguments__are_exactly_these() -> None:
    # The one accepted exception to behaviour-only: main() cannot run in a unit test.
    assert prepush.zero_context_diff_args("BASE") == [
        "-c",
        "core.quotepath=off",
        "diff",
        "-U0",
        "--no-color",
        "--src-prefix=a/",
        "--dst-prefix=b/",
        "-M",
        "--no-ext-diff",
        "--no-textconv",
        "BASE",
    ]


@pytest.mark.unit
def test_parse_added_lines__header_with_trailing_tab__is_read_without_the_tab() -> None:
    diff = (
        "diff --git a/dir with space/a.py b/dir with space/a.py\n"
        "--- a/dir with space/a.py\t\n"
        "+++ b/dir with space/a.py\t\n"
        "@@ -0,0 +1,2 @@\n"
        "+x\n"
        "+y\n"
    )

    assert prepush.parse_added_lines(diff) == {"dir with space/a.py": [(1, 2)]}


@pytest.mark.unit
def test_parse_added_lines__backslash_in_a_header_path__is_kept_as_written() -> None:
    diff = "diff --git a/d\\a.py b/d\\a.py\n--- a/d\\a.py\n+++ b/d\\a.py\n@@ -0,0 +1 @@\n+x\n"

    assert prepush.parse_added_lines(diff) == {"d\\a.py": [(1, 1)]}


@pytest.mark.unit
def test_parse_added_lines__header_shaped_content_in_a_hunk__keeps_later_hunks_and_files() -> None:
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -0,0 +1,2 @@\n"
        "+first\n"
        "+++ b/other.py\n"  # an added line whose text is "++ b/other.py"
        "@@ -9 +10 @@\n"
        "-old\n"
        "+new\n"
        "@@ -20,0 +21 @@\n"
        "+++ not a path\n"  # an added line whose text is "++ not a path"
        "@@ -30,0 +31 @@\n"
        "+later\n"
        "diff --git a/c.py b/c.py\n"
        "--- a/c.py\n"
        "+++ b/c.py\n"
        "@@ -0,0 +5 @@\n"
        "+z\n"
    )

    assert prepush.parse_added_lines(diff) == {
        "a.py": [(1, 2), (10, 10), (21, 21), (31, 31)],
        "c.py": [(5, 5)],
    }


@pytest.mark.unit
def test_parse_added_lines__control_character_in_a_content_line__creates_no_line() -> None:
    diff = (
        "diff --git a/a.py b/a.py\n"
        "--- a/a.py\n"
        "+++ b/a.py\n"
        "@@ -0,0 +1 @@\n"
        "+x\x0cdiff --git a/z.py b/z.py\x0c+++ b/z.py\x0c@@ -0,0 +50,3 @@\n"
    )

    assert prepush.parse_added_lines(diff) == {"a.py": [(1, 1)]}


@pytest.mark.unit
def test_parse_added_lines__control_deleted_file_marker__is_accepted() -> None:
    diff = (
        "diff --git a/old.py b/old.py\n"
        "deleted file mode 100644\n"
        "--- a/old.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-a\n"
        "-b\n"
        "diff --git a/n.py b/n.py\n"
        "--- a/n.py\n"
        "+++ b/n.py\n"
        "@@ -0,0 +3 @@\n"
        "+n\n"
    )

    assert prepush.parse_added_lines(diff) == {"n.py": [(3, 3)]}


@pytest.mark.unit
def test_parse_added_lines__control_blocks_without_a_header__add_nothing_and_do_not_fail() -> None:
    diff = (
        "warning: in the working copy of 'x', LF will be replaced by CRLF\n"
        "diff --git a/img.png b/img.png\n"
        "index 1111111..2222222 100644\n"
        "Binary files a/img.png and b/img.png differ\n"
        "diff --git a/run.sh b/run.sh\n"
        "old mode 100644\n"
        "new mode 100755\n"
        "diff --git a/before.py b/after.py\n"
        "similarity index 100%\n"
        "rename from before.py\n"
        "rename to after.py\n"
        "diff --git a/n.py b/n.py\n"
        "--- a/n.py\n"
        "+++ b/n.py\n"
        "@@ -0,0 +3 @@\n"
        "+n\n"
    )

    assert prepush.parse_added_lines(diff) == {"n.py": [(3, 3)]}


@pytest.mark.unit
@pytest.mark.parametrize(
    "porcelain",
    ["?? dir with space/new é.py\0 M a.py\0", "?? dir with space/new é.py\0"],
)
def test_file_list_parsers__control_names_with_spaces_and_non_ascii__are_read_whole(
    porcelain: str,
) -> None:
    assert prepush.untracked_files(porcelain) == ["dir with space/new é.py"]
    got = prepush.parse_changed_files("sp ace/é.py\0", porcelain)
    assert "sp ace/é.py" in got
    assert "dir with space/new é.py" in got


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
    [{}, {"quick": True}, {"changed": True}, {"skip_mypy": True}],
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


_ROOT_INIT = (_FIXTURES / "pylint_duplicate_code_root_init.txt").read_text(encoding="utf-8")


@pytest.mark.unit
def test_parse_pylint_duplicates__bare_root_name__maps_to_the_root_init_module() -> None:
    (block,) = prepush.parse_pylint_duplicates(_ROOT_INIT)

    assert block.locations == (
        ("src/prismabib/other.py", 4, 10),
        ("src/prismabib/__init__.py", 4, 10),
    )


@pytest.mark.unit
def test_duplication_step__root_init_block_on_an_added_line__fails_and_names_the_file() -> None:
    step = _dup_step({"src/prismabib/__init__.py": [(5, 5)]})

    (result,) = prepush.run_steps([step], _FakeRunner({step.cmd[0]: (8, _ROOT_INIT)}))

    assert not result.ok
    assert "src/prismabib/other.py:4-10  <->  src/prismabib/__init__.py:4-10" in result.note


@pytest.mark.unit
def test_duplication_step__root_init_block_outside_the_change__passes_with_a_note() -> None:
    step = _dup_step({"src/prismabib/__init__.py": [(20, 25)]})

    (result,) = prepush.run_steps([step], _FakeRunner({step.cmd[0]: (8, _ROOT_INIT)}))

    assert result.ok
    assert result.note == "1 known duplicate block(s) outside this change"


@pytest.mark.unit
def test_duplication_step__control_subpackage_init_block_on_an_added_line__fails() -> None:
    # Control: the subpackage case already mapped correctly before the root fix.
    step = _dup_step({"src/prismabib/d/__init__.py": [(3, 3)]})

    (result,) = prepush.run_steps([step], _FakeRunner({step.cmd[0]: (8, _TWO_BLOCKS)}))

    assert not result.ok
    assert "src/prismabib/c.py:100-109  <->  src/prismabib/d/__init__.py:1-10" in result.note


@pytest.mark.unit
def test_parse_pylint_duplicates__name_only_prefixed_by_root__maps_as_before() -> None:
    out = (
        "src\\prismabib_x.py:1:0: R0801: Similar lines in 2 files\n"
        "==prismabib_x:[1:11]\n"
        "==prismabib.other:[4:14]\n"
        "    y = 2 (duplicate-code)\n"
    )

    (block,) = prepush.parse_pylint_duplicates(out)

    assert block.locations == (
        ("src/prismabib_x.py", 1, 10),
        ("src/prismabib/other.py", 4, 13),
    )


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


# --- #453: complexity-risk rules, parsers, test-step coverage arguments ---------------------

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "prepush"
_SRC = "src/m.py"


def _rec(qualname: str, start: int, end: int, cc: int, ordinal: int = 1) -> Any:
    return prepush.FunctionRecord(_SRC, qualname, ordinal, start, end, cc)


def _cov(executed: Sequence[int], missing: Sequence[int], excluded: Sequence[int] = ()) -> Any:
    return prepush.FileCoverage(frozenset(executed), frozenset(missing), frozenset(excluded))


def _scored(cc: int, cov: float, qualname: str = "f", ordinal: int = 1) -> Any:
    return prepush.Scored(_SRC, qualname, ordinal, cc, cov, prepush.crap_score(cc, cov))


def _entry(cc: int, qualname: str = "f", ordinal: int = 1) -> Any:
    return prepush.LegacyEntry(_SRC, qualname, ordinal, cc)


def _kinds(scored: list[Any], legacy: list[Any]) -> list[str]:
    return [o.kind for o in prepush.classify_outcomes(scored, legacy)]


@pytest.mark.unit
def test_test_step__arguments__turn_coverage_on_outside_the_repo_with_fail_under_zero() -> None:
    step = next(s for s in _build(cov_config="/outside/cov.ini") if s.name.startswith("pytest"))

    cmd = step.cmd  # type: ignore[attr-defined]
    assert "--no-cov" not in cmd
    assert "--cov=src/prismabib" in cmd
    assert "--cov-config=/outside/cov.ini" in cmd
    assert "--cov-fail-under=0" in cmd
    assert "--cov-report=" in cmd
    assert "unit or contract" in cmd
    assert cmd[cmd.index("-n") + 1] == "2"


@pytest.mark.unit
def test_coverage_config__text__sets_data_file_branch_and_copies_exclusions() -> None:
    text = prepush.coverage_config_text(
        "/t/covdata/.coverage", ["pragma: no cover", "def __repr__"]
    )

    assert text == (
        "[run]\ndata_file = /t/covdata/.coverage\nbranch = True\n\n[report]\n"
        "exclude_lines =\n    pragma: no cover\n    def __repr__\n"
    )


@pytest.mark.unit
def test_coverage_config__write__creates_the_data_dir_next_to_the_config(tmp_path: Path) -> None:
    path = prepush.write_coverage_config(str(tmp_path), ["pragma: no cover"])

    assert path == str(tmp_path / "cov.ini")
    assert (tmp_path / "covdata").is_dir()
    assert "data_file = " in Path(path).read_text("utf-8")


@pytest.mark.unit
def test_exclude_lines__from_pyproject_text__are_read_and_missing_means_empty() -> None:
    text = '[tool.coverage.report]\nexclude_lines = ["a", "b.c"]\n'

    assert prepush.load_exclude_lines(text) == ["a", "b.c"]
    assert prepush.load_exclude_lines("[tool.other]\nx = 1\n") == []


@pytest.mark.unit
def test_coverage_json_cmd__has_rcfile_fail_under_zero_and_output() -> None:
    cmd = prepush.coverage_json_cmd("/t/cov.ini", "/t/cov.json")

    assert cmd[1:] == [
        "-m",
        "coverage",
        "json",
        "--rcfile=/t/cov.ini",
        "--fail-under=0",
        "-o",
        "/t/cov.json",
    ]


@pytest.mark.unit
def test_parse_radon_cc__real_fixture__gives_each_function_once_with_ordinals() -> None:
    text = (_FIXTURES / "radon_cc.json").read_text("utf-8")

    records = prepush.parse_radon_cc(text)

    assert [tuple(r) for r in records] == [
        ("sample_module.py", "plain", 1, 6, 9, 3),
        ("sample_module.py", "decorated_wrapper", 1, 12, 13, 1),
        ("sample_module.py", "decorated", 1, 18, 21, 2),
        ("sample_module.py", "Box.method", 1, 25, 30, 3),
        ("sample_module.py", "Box.size", 1, 33, 34, 1),
        ("sample_module.py", "Box.size", 2, 37, 39, 2),
        ("sample_module.py", "outer", 1, 42, 48, 1),
        ("sample_module.py", "outer.inner", 1, 43, 46, 2),
        ("sample_module.py", "fetch", 1, 51, 54, 2),
    ]


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad", ["not json", "[1]", '{"a.py": {"x": 1}}', '{"a.py": [{"name": "f"}]}']
)
def test_parse_radon_cc__wrong_format__raises_a_clear_error(bad: str) -> None:
    with pytest.raises(ValueError, match="radon output"):
        prepush.parse_radon_cc(bad)


@pytest.mark.unit
def test_parse_coverage_json__real_fixture__gives_normalised_per_file_lines() -> None:
    text = (_FIXTURES / "coverage_report.json").read_text("utf-8")

    cov = prepush.parse_coverage_json(text)

    assert sorted(cov) == ["src/prismabib/ids.py", "src/prismabib/progress.py"]
    ids = cov["src/prismabib/ids.py"]
    assert sorted(ids.executed)[:5] == [7, 9, 10, 11, 12]
    assert ids.missing == frozenset()
    assert sorted(ids.excluded) == list(range(14, 31))


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad", ["nope", "{}", '{"files": {"a.py": {"executed_lines": [1]}}}', "[]"]
)
def test_parse_coverage_json__wrong_format__raises_a_clear_error(bad: str) -> None:
    with pytest.raises(ValueError, match="coverage JSON"):
        prepush.parse_coverage_json(bad)


@pytest.mark.unit
def test_parse_coverage_json__nothing_executed_anywhere__is_an_error_not_zero_percent() -> None:
    empty = (
        '{"files": {"a.py": {"executed_lines": [], "missing_lines": [1], "excluded_lines": []}}}'
    )

    with pytest.raises(ValueError, match="no data was collected"):
        prepush.parse_coverage_json(empty)


@pytest.mark.unit
def test_join__method_nested_decorated_and_async__get_coverage_from_their_range() -> None:
    fns = [
        _rec("Box.method", 25, 30, 3),
        _rec("outer", 42, 48, 1),  # includes inner's lines 43-46
        _rec("outer.inner", 43, 46, 2),
        _rec("decorated", 18, 21, 2),  # range starts at def: the missing decorator line 17 is out
        _rec("fetch", 51, 54, 2),  # async
    ]
    cov = {
        _SRC: _cov(
            executed=[26, 27, 28, 29, 43, 44, 45, 47, 18, 19, 20, 21, 51, 52, 53, 54],
            missing=[30, 46, 48, 17],
        )
    }

    got = [(s.qualname, s.coverage) for s in prepush.score_functions(fns, cov)]

    assert got == [
        ("Box.method", 0.8),  # 26-29 ran, 30 missing
        ("outer", 4 / 6),  # 43,44,45,47 ran; 46,48 missing
        ("outer.inner", 0.75),  # 43,44,45 ran; 46 missing
        ("decorated", 1.0),
        ("fetch", 1.0),
    ]


@pytest.mark.unit
def test_join__no_executable_lines_in_range__coverage_is_one_and_score_is_complexity() -> None:
    cov = {_SRC: _cov(executed=[1], missing=[2], excluded=[5, 6])}

    (s,) = prepush.score_functions([_rec("stub", 5, 6, 4)], cov)

    assert (s.coverage, s.score) == (1.0, 4.0)


@pytest.mark.unit
def test_join__file_absent_from_coverage__is_zero_coverage_and_reported() -> None:
    fns = [_rec("f", 1, 9, 3)]
    other = {"src/other.py": _cov([1], [])}

    (s,) = prepush.score_functions(fns, other)

    assert (s.coverage, s.score) == (0.0, 12.0)
    assert prepush.files_without_coverage(fns, other) == [_SRC]
    assert prepush.files_without_coverage(fns, {_SRC: _cov([1], [])}) == []


@pytest.mark.unit
def test_crap_score__literals() -> None:
    assert prepush.crap_score(10, 0.0) == 110.0
    assert prepush.crap_score(10, 0.5) == 22.5
    assert prepush.crap_score(5, 1.0) == 5.0


@pytest.mark.unit
def test_parse_radon_cc__same_name_in_two_places__ordinals_follow_line_order() -> None:
    text = (
        '{"a.py": [{"type":"function","name":"f","lineno":30,"endline":32,"complexity":2,'
        '"closures":[]},{"type":"function","name":"f","lineno":5,"endline":7,"complexity":1,'
        '"closures":[]}]}'
    )

    got = [(r.qualname, r.ordinal, r.start) for r in prepush.parse_radon_cc(text)]

    assert got == [("f", 1, 5), ("f", 2, 30)]


@pytest.mark.unit
def test_outcomes__unlisted_function_above_threshold__is_new() -> None:
    assert _kinds([_scored(11, 1.0)], []) == ["new"]
    assert _kinds([_scored(3, 1.0)], []) == []


@pytest.mark.unit
def test_outcomes__complexity_ten_with_low_coverage__is_new_and_not_listable() -> None:
    (o,) = prepush.classify_outcomes([_scored(10, 0.5)], [])

    assert (o.kind, o.score) == ("new", 22.5)
    assert _kinds([_scored(10, 0.5)], [_entry(10)]) == ["stale"]


@pytest.mark.unit
def test_outcomes__listed_function__raised_known_lowered_or_stale() -> None:
    assert _kinds([_scored(13, 1.0)], [_entry(12)]) == ["raised"]
    assert _kinds([_scored(12, 0.0)], [_entry(12)]) == ["known"]  # score above 10, cc unchanged
    assert _kinds([_scored(11, 1.0)], [_entry(12)]) == ["known"]  # lowered
    assert _kinds([_scored(9, 1.0)], [_entry(12)]) == ["stale"]  # now at 10 or below
    assert _kinds([], [_entry(12)]) == ["stale"]  # function gone


@pytest.mark.unit
def test_outcomes__identity_is_file_name_and_ordinal__not_line() -> None:
    got = prepush.classify_outcomes([_scored(15, 1.0, "f", 2)], [_entry(15, "f", 1)])

    assert [(o.kind, o.ordinal) for o in got] == [("stale", 1), ("new", 2)]


# --- #454: the complexity-risk step and its child mode ---------------------------------------

_COV_JSON = (
    '{"files": {"src\\\\prismabib\\\\m.py": {"executed_lines": [1, 2, 3],'
    ' "missing_lines": [5, 6], "excluded_lines": []}}}'
)
# g (lines 1-3) is fully executed, f (lines 5-6) not at all: scores 12.0 and 156.0.
_RADON = (
    '{"src\\\\prismabib\\\\m.py": ['
    '{"type": "function", "name": "g", "lineno": 1, "endline": 3, "complexity": 12,'
    ' "closures": []},'
    '{"type": "function", "name": "f", "lineno": 5, "endline": 6, "complexity": 12,'
    ' "closures": []}]}'
)


class _ChildRunner:
    """Fake for the child: writes the coverage JSON where asked, answers radon with ``radon``."""

    def __init__(self, cov: tuple[int, str], radon: tuple[int, str]) -> None:
        self.cov, self.radon = cov, radon
        self.out_json = ""
        self.radon_cmd: list[str] = []

    def __call__(self, cmd: Sequence[str]) -> tuple[int, str]:
        if "coverage" in cmd:
            self.out_json = cmd[cmd.index("-o") + 1]
            if self.cov[0] == 0:
                Path(self.out_json).write_text(self.cov[1], "utf-8")
                return 0, "Wrote JSON report"
            return self.cov
        self.radon_cmd = list(cmd)
        return self.radon


def _risk(
    runner: Any,
    cov_config: str,
    legacy: str,
    touched: Sequence[str] = ("src/prismabib/m.py",),
    exists: Any = lambda _path: True,
) -> tuple[int, str]:
    return prepush.complexity_risk(runner, cov_config, legacy, touched, exists)  # type: ignore[no-any-return]


@pytest.mark.unit
def test_complexity_scope__changed_files__keeps_package_python_only() -> None:
    changed = [
        "src\\prismabib\\store\\load.py",
        "src/other_pkg/x.py",
        "src/prismabib/data.toml",
        "scripts/prepush.py",
        "tests/unit/test_x.py",
        "src/prismabib/a.py",
    ]

    assert prepush.complexity_scope(changed) == [
        "src/prismabib/a.py",
        "src/prismabib/store/load.py",
    ]


@pytest.mark.unit
def test_complexity_risk__nothing_touched__passes_without_running_a_tool() -> None:
    runner = _ChildRunner((0, _COV_JSON), (0, _RADON))

    code, text = _risk(runner, "/c", "src/prismabib/m.py f 1 12\n", touched=[])

    assert (code, text, runner.out_json) == (
        0,
        "complexity-risk: no touched Python file under src/prismabib",
        "",
    )


@pytest.mark.unit
def test_complexity_risk__untouched_files__are_not_scored_and_their_entries_not_stale() -> None:
    runner = _ChildRunner((0, _COV_JSON), (0, _RADON))
    legacy = "src/prismabib/m.py f 1 12\nsrc/prismabib/m.py g 1 12\nsrc/prismabib/z.py q 1 20\n"

    code, text = _risk(runner, "/c", legacy)

    assert code == 0
    assert runner.radon_cmd[-1] == "src/prismabib/m.py"
    assert "z.py" not in text


@pytest.mark.unit
def test_complexity_risk__deleted_touched_file__its_entries_are_stale_and_radon_skips_it() -> None:
    runner = _ChildRunner((0, _COV_JSON), (0, "{}"))
    gone = "src/prismabib/gone.py"

    code, text = _risk(runner, "/c", f"{gone} q 1 20\n", touched=[gone], exists=lambda _p: False)

    assert (code, runner.radon_cmd) == (1, [])
    assert (
        text.splitlines()[0]
        == f"[stale] {gone} q#1: recorded 20; gone or now at 10 or less: delete"
    )


def _step(name: str) -> Any:
    return next(s for s in _build(cov_config="/outside/cov.ini") if s.name == name)  # type: ignore[attr-defined]


@pytest.mark.unit
def test_complexity_step__arguments_and_dependency__one_child_command_after_the_tests() -> None:
    step = _step("complexity-risk")

    assert step.cmd[1:] == [str(_SCRIPT.resolve()), "--complexity-risk", "/outside/cov.ini"]
    assert step.after == "pytest unit+contract"
    assert step.rerun.startswith("a fresh test run comes first")
    assert "--complexity-risk" not in step.rerun  # no printed command stands alone


@pytest.mark.unit
@pytest.mark.parametrize(
    ("quick", "changed", "reason"), [(True, False, "--quick"), (False, True, "--changed")]
)
def test_complexity_step__quick_or_changed__is_skipped_with_reason(
    quick: bool, changed: bool, reason: str
) -> None:
    runner = _FakeRunner({})

    skips = prepush.option_skips(quick=quick, skip_mypy=False, changed=changed)
    text, code = prepush.summarize(prepush.run_steps(skips, runner))

    assert f"[skip] complexity-risk  ({reason})" in text
    assert (runner.calls, code) == ([], 0)


@pytest.mark.unit
def test_complexity_step__test_step_failed__is_skipped_and_never_run() -> None:
    steps = [s for s in _build() if s.name in ("pytest unit+contract", "complexity-risk")]
    runner = _FakeRunner({steps[0].cmd[0]: (1, "boom")})

    results = prepush.run_steps(steps, runner)

    assert [(r.name, r.skipped) for r in results] == [
        ("pytest unit+contract", False),
        ("complexity-risk", True),
    ]
    assert results[1].note == "prerequisite 'pytest unit+contract' failed"
    assert len(runner.calls) == 1


@pytest.mark.unit
def test_complexity_step__failing_run__summary_has_platform_note_and_no_bare_command() -> None:
    step = _step("complexity-risk")
    runner = _FakeRunner({step.cmd[0]: (1, "[new] src/m.py f#1: complexity 12")})

    (result,) = prepush.run_steps([step._replace(after=None)], runner)
    text, code = prepush.summarize([result])

    assert code == 1
    assert "new-violation results may differ by platform" in text
    assert "re-run: a fresh test run comes first: python scripts/prepush.py" in text
    assert "--complexity-risk" not in text


@pytest.mark.unit
def test_complexity_risk__new_violation_beside_known_legacy__fails_naming_the_new_one() -> None:
    runner = _ChildRunner((0, _COV_JSON), (0, _RADON))

    code, text = _risk(runner, "/c.ini", "src/prismabib/m.py g 1 12\n")

    assert code == 1
    assert text.splitlines() == [
        "[new] src/prismabib/m.py f#1: complexity 12, score 156.0, coverage 0.00",
        "[known] src/prismabib/m.py g#1: complexity 12, score 12.0, coverage 1.00",
        "complexity-risk: 1 problem(s), 1 known legacy function(s)",
    ]


@pytest.mark.unit
def test_complexity_risk__all_listed_and_unchanged__passes() -> None:
    legacy = "src/prismabib/m.py f 1 12\nsrc/prismabib/m.py g 1 12\n"

    code, text = _risk(_ChildRunner((0, _COV_JSON), (0, _RADON)), "/c", legacy)

    assert code == 0
    assert text.splitlines()[-1] == "complexity-risk: 0 problem(s), 2 known legacy function(s)"


@pytest.mark.unit
def test_complexity_risk__stale_and_lowered_entries__print_what_to_delete_or_lower() -> None:
    legacy = "src/prismabib/gone.py h 1 14\nsrc/prismabib/m.py f 1 15\nsrc/prismabib/m.py g 1 12\n"

    touched = ["src/prismabib/gone.py", "src/prismabib/m.py"]

    code, text = _risk(_ChildRunner((0, _COV_JSON), (0, _RADON)), "/c", legacy, touched)

    assert code == 1
    assert text.splitlines() == [
        "[stale] src/prismabib/gone.py h#1: recorded 14; gone or now at 10 or less: delete",
        (
            "[known] src/prismabib/m.py f#1: complexity 12, score 156.0, coverage 0.00;"
            " lower the entry from 15 to 12"
        ),
        "[known] src/prismabib/m.py g#1: complexity 12, score 12.0, coverage 1.00",
        "complexity-risk: 1 problem(s), 2 known legacy function(s)",
    ]


@pytest.mark.unit
def test_complexity_risk__raised_legacy_function__fails_showing_the_recorded_value() -> None:
    legacy = "src/prismabib/m.py f 1 11\nsrc/prismabib/m.py g 1 12\n"

    code, text = _risk(_ChildRunner((0, _COV_JSON), (0, _RADON)), "/c", legacy)

    assert code == 1
    assert text.splitlines()[0] == (
        "[raised] src/prismabib/m.py f#1: complexity 12, score 156.0, coverage 0.00 (recorded 11)"
    )


@pytest.mark.unit
def test_complexity_risk__function_in_file_without_coverage__is_an_error() -> None:
    cov = (
        '{"files": {"src/prismabib/other.py": {"executed_lines": [1], "missing_lines": [],'
        ' "excluded_lines": []}}}'
    )

    code, text = _risk(_ChildRunner((0, cov), (0, _RADON)), "/c", "")

    assert code == 1
    assert text.splitlines()[0] == "[error] src/prismabib/m.py: no coverage data"


@pytest.mark.unit
@pytest.mark.parametrize("module", ["coverage", "radon"])
def test_complexity_risk__missing_module__child_fails_with_note_and_parent_reports_it(
    module: str,
) -> None:
    gone = (1, f"C:\\v\\python.exe: No module named {module}\n")
    runner = _ChildRunner(gone if module == "coverage" else (0, _COV_JSON), gone)
    hint = "install the development dependencies: uv sync --all-extras"

    code, text = _risk(runner, "/c", "")

    assert (code, text) == (1, f"note: module '{module}' is missing; {hint}")
    assert prepush.complexity_evaluator(code, text) == (
        False,
        f"module '{module}' is missing; {hint}",
    )


@pytest.mark.unit
def test_complexity_risk__tools__run_on_the_package_tree_and_leave_no_temp_files() -> None:
    runner = _ChildRunner((0, _COV_JSON), (0, _RADON))

    _risk(runner, "/c", "")

    assert runner.radon_cmd[-4:] == ["cc", "-s", "-j", "src/prismabib/m.py"]
    assert not Path(runner.out_json).parent.exists()


@pytest.mark.unit
def test_parse_legacy__lines__become_entries_and_a_bad_line_fails_naming_it() -> None:
    text = "src/prismabib/a.py A.f 2 13\nsrc/prismabib/b.py g 1 11\n"

    assert prepush.parse_legacy(text) == [
        prepush.LegacyEntry("src/prismabib/a.py", "A.f", 2, 13),
        prepush.LegacyEntry("src/prismabib/b.py", "g", 1, 11),
    ]
    with pytest.raises(ValueError, match="line 1"):
        prepush.parse_legacy("src/prismabib/a.py A.f two 13\n")


@pytest.mark.unit
def test_legacy_list__committed_file__parses_with_only_complexity_above_ten() -> None:
    text = (_SCRIPT.parent / "complexity-legacy.txt").read_text("utf-8")

    entries = prepush.parse_legacy(text)

    assert entries
    assert all(e.complexity > 10 for e in entries)
    assert all(e.file.startswith("src/prismabib/") and "\\" not in e.file for e in entries)
    assert text.endswith("\n") and "\r" not in text
    assert prepush.legacy_format_problems(text, "working copy") == []


def _fmt(text: str) -> list[str]:
    return prepush.legacy_format_problems(text, "working copy")  # type: ignore[no-any-return]


_L1 = "src/prismabib/a.py f 1 12\n"
_L2 = "src/prismabib/b.py g 1 12\n"
_FINAL_NL = (
    "[legacy-format] working copy: final newline: the file must end with exactly one newline"
)


@pytest.mark.unit
def test_legacy_format__correct_text__passes() -> None:
    assert _fmt(_L1 + _L2) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "src\\prismabib\\a.py f 1 12\n",
            ["[legacy-format] working copy: forward slashes: line 1 has a backslash"],
        ),
        (
            _L2 + _L1,
            [
                (
                    "[legacy-format] working copy: sort order: src/prismabib/a.py f#1 (complexity 12)"
                    " must come after src/prismabib/b.py g#1 (complexity 12) (UTF-8 bytes)"
                )
            ],
        ),
        (
            "src/prismabib/a.py f 1 12\r\n",
            ["[legacy-format] working copy: line endings: must be LF, found a carriage return"],
        ),
        ("src/prismabib/a.py f 1 12", [_FINAL_NL]),
        (
            "src/prismabib/a.py f 1 12\n\n",
            [_FINAL_NL, "[legacy-format] working copy: entry shape: line 2 is blank"],
        ),
        (
            "src/prismabib/a.py f 1 12 \n",
            [
                "[legacy-format] working copy: trailing spaces: line 1",
                (
                    "[legacy-format] working copy: entry shape: legacy list line 1:"
                    " want 'file qualname ordinal complexity'"
                ),
            ],
        ),
    ],
)
def test_legacy_format__broken_text__fails_naming_the_rule(text: str, expected: list[str]) -> None:
    assert _fmt(text) == expected


@pytest.mark.unit
def test_legacy_format__utf8_byte_order__uppercase_before_lowercase_and_non_ascii_last() -> None:
    ok = "src/prismabib/B.py f 1 12\nsrc/prismabib/a.py f 1 12\nsrc/prismabib/é.py f 1 12\n"
    swapped = "src/prismabib/a.py f 1 12\nsrc/prismabib/B.py f 1 12\n"

    assert _fmt(ok) == []
    assert len(_fmt(swapped)) == 1 and "sort order" in _fmt(swapped)[0]


@pytest.mark.unit
def test_legacy_format__ordinals_sort_as_numbers() -> None:
    assert _fmt("src/prismabib/a.py f 2 12\nsrc/prismabib/a.py f 10 12\n") == []


def _entries(text: str) -> list[Any]:
    return prepush.parse_legacy(text)  # type: ignore[no-any-return]


def _shrink(base: str, work: str) -> list[str]:
    return prepush.legacy_shrink_problems(_entries(base), _entries(work))  # type: ignore[no-any-return]


@pytest.mark.unit
def test_legacy_shrink__entry_added_without_partner__fails() -> None:
    assert _shrink(_L1, _L1 + _L2) == [
        (
            "[legacy-shrink] added without a removed entry to replace:"
            " src/prismabib/b.py g#1 (complexity 12)"
        )
    ]


@pytest.mark.unit
def test_legacy_shrink__complexity_raised_in_place__fails_and_lowered_passes() -> None:
    assert _shrink("src/prismabib/a.py f 1 12\n", "src/prismabib/a.py f 1 13\n") == [
        "[legacy-shrink] raised in place: src/prismabib/a.py f#1 (complexity 13), was 12"
    ]
    assert _shrink("src/prismabib/a.py f 1 12\n", "src/prismabib/a.py f 1 11\n") == []


@pytest.mark.unit
def test_legacy_shrink__moved_function__equal_passes_and_raised_fails() -> None:
    base = "src/prismabib/a.py f 1 12\n"

    assert _shrink(base, "src/prismabib/z.py f 1 12\n") == []
    assert _shrink(base, "src/prismabib/z.py f 1 13\n") == [
        (
            "[legacy-shrink] replacement raises complexity: src/prismabib/z.py f#1 (complexity 13)"
            " replaces src/prismabib/a.py f#1 (complexity 12)"
        )
    ]


@pytest.mark.unit
def test_legacy_shrink__two_new_against_one_removed__fails() -> None:
    work = "src/prismabib/y.py f 1 12\nsrc/prismabib/z.py f 1 12\n"

    assert _shrink("src/prismabib/a.py f 1 12\n", work) == [
        (
            "[legacy-shrink] added without a removed entry to replace:"
            " src/prismabib/z.py f#1 (complexity 12)"
        )
    ]


@pytest.mark.unit
@pytest.mark.parametrize(("removed", "ok"), [(7, True), (4, False)])
def test_legacy_shrink__new_five_against_removed__passes_only_when_removed_is_at_least_five(
    removed: int, ok: bool
) -> None:
    result = _shrink(f"src/prismabib/a.py f 1 {removed}\n", "src/prismabib/z.py f 1 5\n")

    assert (result == []) is ok


@pytest.mark.unit
def test_legacy_shrink__removed_entry_without_partner__is_accepted() -> None:
    assert _shrink(_L1 + _L2, _L1) == []


@pytest.mark.unit
def test_legacy_shrink__pairing_is_highest_first__not_in_file_order() -> None:
    base = "src/prismabib/a.py f 1 4\nsrc/prismabib/b.py f 1 9\n"
    work = "src/prismabib/y.py f 1 3\nsrc/prismabib/z.py f 1 8\n"

    assert _shrink(base, work) == []


@pytest.mark.unit
def test_legacy_count__one_above_base__fails_and_equal_or_fewer_passes() -> None:
    assert prepush.legacy_count_problems(3, 4) == [
        "[legacy-shrink] count rule: 4 entries, base has 3"
    ]
    assert prepush.legacy_count_problems(3, 3) == []
    assert prepush.legacy_count_problems(3, 2) == []


class _VcsRunner:
    """Fake of the version-control calls: ``base`` is the base copy, ``None`` means absent."""

    def __init__(self, base: str | None, merge_base: tuple[int, str] = (0, "abc123\n")) -> None:
        self.base, self.merge_base = base, merge_base
        self.calls: list[list[str]] = []

    def __call__(self, cmd: Sequence[str]) -> tuple[int, str]:
        self.calls.append(list(cmd))
        if cmd[1] == "merge-base":
            return self.merge_base
        if cmd[1] == "ls-tree":
            return 0, "" if self.base is None else "scripts/complexity-legacy.txt\n"
        return 0, self.base or ""


def _integrity(base: str | None, work: str | None, **kw: Any) -> tuple[list[str], list[str]]:
    return prepush.legacy_integrity(_VcsRunner(base, **kw), work)  # type: ignore[no-any-return]


@pytest.mark.unit
def test_legacy_integrity__unresolved_merge_base__fails_with_the_fetch_message() -> None:
    result = _integrity(_L1, _L1, merge_base=(128, "fatal: Not a valid object name"))

    assert result == (
        [
            (
                "[legacy-shrink] cannot resolve or read the merge-base with origin/main:"
                " fetch the base branch (git fetch origin main) and run the gate again"
            )
        ],
        [],
    )


@pytest.mark.unit
def test_legacy_integrity__no_list_at_base_but_a_working_list__is_accepted_with_a_note() -> None:
    assert _integrity(None, _L1) == (
        [],
        ["legacy list is new in this change (absent at the merge-base): accepted"],
    )


@pytest.mark.unit
def test_legacy_integrity__no_list_at_base_and_a_broken_working_list__fails_the_format() -> None:
    problems, _ = _integrity(None, _L2 + _L1)

    assert len(problems) == 1 and "sort order" in problems[0]


@pytest.mark.unit
def test_legacy_integrity__list_in_base_missing_from_working_copy__fails() -> None:
    assert _integrity(_L1, None) == (
        ["[legacy-shrink] the list is in the base and missing from the working copy"],
        [],
    )


@pytest.mark.unit
def test_legacy_integrity__reads_the_base_copy_with_forward_slashes() -> None:
    runner = _VcsRunner(_L1)

    prepush.legacy_integrity(runner, _L1)

    assert runner.calls == [
        ["git", "merge-base", "origin/main", "HEAD"],
        ["git", "ls-tree", "--name-only", "abc123", "--", "scripts/complexity-legacy.txt"],
        ["git", "show", "abc123:scripts/complexity-legacy.txt"],
    ]


@pytest.mark.unit
def test_legacy_integrity__base_copy_is_format_checked_on_its_raw_text() -> None:
    problems, _ = _integrity("src/prismabib/a.py f 1 12\r\n", _L1)

    assert problems == [
        "[legacy-format] base copy: line endings: must be LF, found a carriage return"
    ]


@pytest.mark.unit
def test_legacy_integrity__shrinking_list_passes_and_growing_list_fails() -> None:
    assert _integrity(_L1 + _L2, _L1) == ([], [])
    assert _integrity(_L1, _L1 + _L2)[0] == [
        (
            "[legacy-shrink] added without a removed entry to replace:"
            " src/prismabib/b.py g#1 (complexity 12)"
        ),
        "[legacy-shrink] count rule: 2 entries, base has 1",
    ]


@pytest.mark.unit
def test_complexity_evaluator__legacy_problem__is_the_summary_note() -> None:
    out = "[legacy-shrink] count rule: 2 entries, base has 1\ncomplexity-risk: 0 problem(s)"

    assert prepush.complexity_evaluator(1, out) == (
        False,
        "[legacy-shrink] count rule: 2 entries, base has 1",
    )


@pytest.mark.unit
def test_complexity_evaluator__new_violation__names_the_function_then_the_platform_note() -> None:
    out = (
        "[new] src/prismabib/m.py f#1: complexity 14, score 14.0, coverage 0.00\n"
        "[raised] src/prismabib/m.py g#1: complexity 15, score 15.0, coverage 0.00 (recorded 12)\n"
        "complexity-risk: 2 problem(s), 0 known legacy function(s)"
    )

    assert prepush.complexity_evaluator(1, out) == (
        False,
        "[new] src/prismabib/m.py f#1: complexity 14, score 14.0, coverage 0.00; "
        + prepush.PLATFORM_NOTE,
    )


@pytest.mark.unit
def test_complexity_evaluator__failure_without_a_reason_line__is_the_platform_note() -> None:
    assert prepush.complexity_evaluator(1, "complexity-risk: 1 problem(s)") == (
        False,
        prepush.PLATFORM_NOTE,
    )


@pytest.mark.unit
def test_git_args__rename__lists_the_old_path_as_touched() -> None:
    # `git diff -z --name-only --no-renames` prints a rename as a delete plus an add.
    assert "--no-renames" in prepush.DIFF_ARGS
    diff = "src/prismabib/old.py\0src/prismabib/new.py\0"

    got = prepush.complexity_scope(prepush.parse_changed_files(diff, ""))

    assert got == ["src/prismabib/new.py", "src/prismabib/old.py"]


@pytest.mark.unit
def test_coverage_config__awkward_data_file_path__is_read_back_unchanged(tmp_path: Path) -> None:
    from coverage.config import read_coverage_config

    for data_file in [
        "C:\\Users\\a b\\Temp\\tmp 1\\covdata\\.coverage",
        "/t/a b/$HOME/100%/.coverage",
    ]:
        cfg = tmp_path / "cov.ini"
        cfg.write_text(prepush.coverage_config_text(data_file, ["pragma: no cover"]), "utf-8")

        got = read_coverage_config(str(cfg), warn=lambda _msg, **_kw: None)

        assert got.data_file == data_file
        assert got.exclude_list == ["pragma: no cover"]


@pytest.mark.unit
def test_duplication_base__merge_base_resolves__is_the_sha_without_warning() -> None:
    assert prepush.duplication_base(lambda _cmd: (0, "abc123\n")) == ("abc123", None)


@pytest.mark.unit
def test_duplication_base__merge_base_fails__falls_back_to_head_with_a_warning() -> None:
    base, warning = prepush.duplication_base(lambda _cmd: (128, "fatal: Not a valid object name"))

    assert base == "HEAD"
    assert warning == (
        "warning: cannot find the merge-base with origin/main: committed duplicates are not "
        "checked; fetch the base branch (git fetch origin main)"
    )
