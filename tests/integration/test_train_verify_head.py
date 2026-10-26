"""The exact-commit gate of ``scripts/train_verify_head.py`` in throwaway repositories.

A head passes only if it is the reviewed commit plus verified update merges of main. Each
test builds its own repository under tmp_path and guards one rule. No network.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "train_verify_head.py"
_spec = importlib.util.spec_from_file_location("train_verify_head", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
verify = _module.verify

_BASE_A = b"".join(f"line {i}\n".encode() for i in range(1, 11))
_BIN = b"\x00\x01\x02binary\x00payload"
_UNICODE = "dir/ü ä file.txt"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _write(repo: Path, rel: str, data: bytes) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """main with a base commit (origin/main), and the PR branch 'pr' checked out."""
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    # The repo's own config must not matter; these pins are the ones the tests need.
    _git(root, "config", "user.name", "test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    _write(root, "a.txt", _BASE_A)
    _write(root, "b.bin", _BIN)
    _write(root, _UNICODE, b"unicode base\n")
    base = _commit(root, "base")
    _git(root, "update-ref", "refs/remotes/origin/main", base)
    _git(root, "checkout", "-q", "-b", "pr")
    monkeypatch.chdir(root)
    return root


def _pr_change(repo: Path, rel: str, data: bytes) -> str:
    _write(repo, rel, data)
    return _commit(repo, f"pr: {rel}")


def _advance_main(repo: Path, changes: dict[str, bytes]) -> None:
    """Commit changes on main and point origin/main at the new tip; return to pr."""
    _git(repo, "checkout", "-q", "main")
    for rel, data in changes.items():
        _write(repo, rel, data)
    tip = _commit(repo, "main advances")
    _git(repo, "update-ref", "refs/remotes/origin/main", tip)
    _git(repo, "checkout", "-q", "pr")


def _update_pr(repo: Path) -> str:
    _git(repo, "merge", "--no-ff", "-q", "-m", "update from main", "origin/main")
    return _git(repo, "rev-parse", "HEAD")


def _rev(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", ref)


@pytest.mark.integration
def test_same_head__ok(repo: Path) -> None:
    reviewed = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    assert verify(reviewed, reviewed) == {"ok": True}


@pytest.mark.integration
def test_one_extra_normal_commit__halts(repo: Path) -> None:
    reviewed = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    head = _pr_change(repo, "a.txt", b"changed again\n" + _BASE_A)
    result = verify(reviewed, head)
    assert result["ok"] is False
    assert "two-parent merge" in result["reason"]


@pytest.mark.integration
def test_valid_update_merge_on_disjoint_files__ok(repo: Path) -> None:
    reviewed = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    _advance_main(repo, {"other.txt": b"main only\n"})
    head = _update_pr(repo)
    assert verify(reviewed, head) == {"ok": True}


@pytest.mark.integration
def test_chain_of_two_valid_updates__ok(repo: Path) -> None:
    reviewed = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    _advance_main(repo, {"other.txt": b"one\n"})
    _update_pr(repo)
    _advance_main(repo, {"more.txt": b"two\n"})
    head = _update_pr(repo)
    assert verify(reviewed, head) == {"ok": True}


@pytest.mark.integration
def test_update_where_pr_and_main_edit_same_file__clean_merge__ok(repo: Path) -> None:
    # The PR changes line 1 and main changes line 10 of the same file: git merges it cleanly
    # and the result is git's own merge of the two (rule g).
    reviewed = _pr_change(
        repo, "a.txt", b"PR CHANGE\n" + b"".join(f"line {i}\n".encode() for i in range(2, 11))
    )
    main_version = b"".join(f"line {i}\n".encode() for i in range(1, 10)) + b"MAIN CHANGE\n"
    _advance_main(repo, {"a.txt": main_version})
    head = _update_pr(repo)
    assert verify(reviewed, head) == {"ok": True}


@pytest.mark.integration
def test_update_where_pr_and_main_edit_same_lines__hand_resolved__halts_as_overlap(
    repo: Path,
) -> None:
    rest = _BASE_A.split(b"\n", 1)[1]
    reviewed = _pr_change(repo, "a.txt", b"PR CHANGE\n" + rest)
    _advance_main(repo, {"a.txt": b"MAIN CHANGE\n" + rest})
    subprocess.run(
        ["git", "merge", "--no-ff", "--no-commit", "origin/main"],
        cwd=repo, capture_output=True, check=False,
    )  # conflicts on purpose  # fmt: skip
    _write(repo, "a.txt", b"BOTH\n" + rest)
    head = _commit(repo, "hand-resolved")
    result = verify(reviewed, head)
    assert result["ok"] is False
    assert "overlaps the PR's own edits" in result["reason"]


@pytest.mark.integration
def test_merge_of_a_non_main_branch__halts(repo: Path) -> None:
    reviewed = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    _git(repo, "checkout", "-q", "-b", "other", _rev(repo, "origin/main"))
    _write(repo, "side.txt", b"not on main\n")
    _commit(repo, "other branch")
    _git(repo, "checkout", "-q", "pr")
    _git(repo, "merge", "--no-ff", "-q", "-m", "merge other", "other")
    head = _rev(repo, "HEAD")
    result = verify(reviewed, head)
    assert result["ok"] is False
    assert "not on main" in result["reason"]


@pytest.mark.integration
def test_evil_merge_that_adds_an_extra_change__halts(repo: Path) -> None:
    reviewed = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    _advance_main(repo, {"other.txt": b"main only\n"})
    _git(repo, "merge", "--no-ff", "--no-commit", "-q", "origin/main")
    _write(repo, "evil.txt", b"smuggled\n")
    head = _commit(repo, "evil merge")
    result = verify(reviewed, head)
    assert result["ok"] is False
    assert "adds changes that main does not contain" in result["reason"]


@pytest.mark.integration
def test_binary_file_changed_on_main__handled_and_ok(repo: Path) -> None:
    reviewed = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    _advance_main(repo, {"b.bin": _BIN + b"\x07"})
    head = _update_pr(repo)
    assert verify(reviewed, head) == {"ok": True}


@pytest.mark.integration
def test_binary_change_smuggled_into_merge__halts(repo: Path) -> None:
    reviewed = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    _advance_main(repo, {"other.txt": b"main only\n"})
    _git(repo, "merge", "--no-ff", "--no-commit", "-q", "origin/main")
    _write(repo, "b.bin", b"\x00smuggled\x00")
    head = _commit(repo, "smuggled binary")
    result = verify(reviewed, head)
    assert result["ok"] is False


@pytest.mark.integration
def test_rewritten_history__reviewed_not_ancestor__halts(repo: Path) -> None:
    reviewed = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    base = _rev(repo, "origin/main")
    _git(repo, "checkout", "-q", "-b", "replacement", base)
    replacement = _pr_change(repo, "c.txt", b"replaced\n")
    result = verify(reviewed, replacement)
    assert result["ok"] is False
    assert "not an ancestor" in result["reason"]


@pytest.mark.integration
def test_unknown_reviewed_object__halts(repo: Path) -> None:
    head = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    result = verify("ab" * 20, head)
    assert result["ok"] is False
    assert "not in this repository" in result["reason"]


@pytest.mark.integration
def test_octopus_merge__halts(repo: Path) -> None:
    reviewed = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    _advance_main(repo, {"other.txt": b"main only\n"})
    _git(repo, "checkout", "-q", "-b", "other", _rev(repo, "origin/main~1"))
    _write(repo, "side.txt", b"side\n")
    _commit(repo, "side")
    _git(repo, "checkout", "-q", "pr")
    _git(repo, "merge", "--no-ff", "-q", "-m", "octopus", "origin/main", "other")
    head = _rev(repo, "HEAD")
    result = verify(reviewed, head)
    assert result["ok"] is False
    assert "octopus" in result["reason"]


@pytest.mark.integration
def test_reviewed_commit_off_first_parent_chain__halts(repo: Path) -> None:
    # The reviewed commit is the second parent of a merge: an ancestor, but not on the chain.
    _git(repo, "checkout", "-q", "-b", "side", _rev(repo, "origin/main"))
    reviewed = _pr_change(repo, "side.txt", b"side reviewed\n")
    _git(repo, "checkout", "-q", "pr")
    _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    _git(repo, "merge", "--no-ff", "-q", "-m", "merge side", "side")
    head = _rev(repo, "HEAD")
    result = verify(reviewed, head)
    assert result["ok"] is False
    assert "first-parent chain" in result["reason"]


@pytest.mark.integration
def test_unicode_and_space_paths__ok(repo: Path) -> None:
    reviewed = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    _advance_main(repo, {_UNICODE: b"main edited the unicode path\n"})
    head = _update_pr(repo)
    assert verify(reviewed, head) == {"ok": True}


@pytest.mark.integration
def test_hostile_git_config_is_ignored__result_unchanged(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    _advance_main(repo, {"other.txt": b"main only\n"})
    head = _update_pr(repo)
    hostile = tmp_path / "hostile.gitconfig"
    hostile.write_text(
        "[core]\n\tquotepath = false\n[diff]\n\tnoprefix = true\n\tmnemonicPrefix = true\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(hostile))
    assert verify(reviewed, head) == {"ok": True}


@pytest.mark.integration
@pytest.mark.parametrize(
    "args",
    [["not-a-sha", "ab" * 20], [], ["ab" * 20]],
    ids=["bad-sha", "no-args", "one-arg"],
)
def test_cli__malformed_arguments__exit_2(args: list[str], repo: Path) -> None:
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ},
    )
    assert proc.returncode == 2
    assert proc.stdout == ""


@pytest.mark.integration
def test_cli__decision__exit_0_with_json(repo: Path) -> None:
    reviewed = _pr_change(repo, "a.txt", b"changed\n" + _BASE_A)
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), reviewed, reviewed],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout) == {"ok": True}
