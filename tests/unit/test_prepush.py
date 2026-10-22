"""The local pre-push gate (``scripts/prepush.py``): its pure decision functions.

No test runs a real tool or touches the network; the step runner takes an injected runner.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "prepush.py"
_spec = importlib.util.spec_from_file_location("prepush", _SCRIPT)
assert _spec is not None and _spec.loader is not None
prepush = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(prepush)

KNOWN = 'src\\prismabib\\log.py:166: error: Module has no attribute "flock"  [attr-defined]'
KNOWN_LOCK = "src/prismabib/log.py:170: error: Module has no attribute LOCK_SH  [attr-defined]"
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
    porcelain = ' M src/prismabib/log.py\n?? scripts/new.py\nR  old.py -> renamed.py\n D gone.py\n?? "docs/with space.md"'

    got = prepush.parse_changed_files("src/prismabib/log.py\nREADME.md\n", porcelain)

    assert got == [
        "README.md",
        "docs/with space.md",
        "renamed.py",
        "scripts/new.py",
        "src/prismabib/log.py",
    ]


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
