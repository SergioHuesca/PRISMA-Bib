"""Edge cases of ``scripts/train_verify_head.py``: submodule pointers, long chains, duplicate
updates and shallow clones. Each test builds its own repository under tmp_path; no network.
"""

from __future__ import annotations

import importlib.util
import subprocess
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "train_verify_head.py"
_spec = importlib.util.spec_from_file_location("train_verify_head", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
verify = _module.verify
GitError = _module.GitError

_GITMODULES = b'[submodule "s"]\n\tpath = s\n\turl = ./x\n\tignore = all\n'


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str) -> str:
    # `add -A` would stage a removal for a gitlink `s` whose directory does not exist,
    # silently dropping the pointer the test just set. Leave `s` to update-index.
    _git(repo, "add", "-A", "--", ".", ":(exclude)s")
    _git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _write(repo: Path, rel: str, data: bytes) -> None:
    (repo / rel).write_bytes(data)


def _gitlink(repo: Path, path: str, sha: str) -> None:
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{sha},{path}")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.name", "test")
    _git(root, "config", "user.email", "test@example.invalid")
    _git(root, "config", "core.autocrlf", "false")
    _write(root, "a.txt", b"base\n")
    base = _commit(root, "base")
    _git(root, "update-ref", "refs/remotes/origin/main", base)
    _git(root, "checkout", "-q", "-b", "pr")
    monkeypatch.chdir(root)
    return root


def _advance_main(repo: Path, rel: str, data: bytes) -> None:
    _git(repo, "checkout", "-q", "main")
    _write(repo, rel, data)
    tip = _commit(repo, f"main: {rel}")
    _git(repo, "update-ref", "refs/remotes/origin/main", tip)
    _git(repo, "checkout", "-q", "pr")


def _update_pr(repo: Path) -> str:
    _git(repo, "merge", "--no-ff", "-q", "-m", "update from main", "origin/main")
    return _git(repo, "rev-parse", "HEAD")


# Submodule pointers: a gitlink in a merge must be compared exactly.


def _reviewed_with_ignored_submodule(repo: Path) -> str:
    _write(repo, ".gitmodules", _GITMODULES)
    return _commit(repo, "reviewed: declares submodule s with ignore = all")


@pytest.mark.integration
def test_evil_merge_adding_a_gitlink_under_ignore_all__halts(repo: Path) -> None:
    reviewed = _reviewed_with_ignored_submodule(repo)
    _advance_main(repo, "other.txt", b"main only\n")
    _git(repo, "merge", "--no-ff", "--no-commit", "-q", "origin/main")
    _gitlink(repo, "s", "1" * 40)
    head = _commit(repo, "evil: adds submodule pointer")
    result = verify(reviewed, head)
    assert result["ok"] is False
    assert "adds changes that main does not contain" in result["reason"]


@pytest.mark.integration
def test_evil_merge_bumping_an_existing_gitlink__halts(repo: Path) -> None:
    _write(repo, ".gitmodules", _GITMODULES)
    _gitlink(repo, "s", "2" * 40)
    reviewed = _commit(repo, "reviewed: submodule s at 2222")
    _advance_main(repo, "other.txt", b"main only\n")
    _git(repo, "merge", "--no-ff", "--no-commit", "-q", "origin/main")
    _gitlink(repo, "s", "3" * 40)
    head = _commit(repo, "evil: bumps submodule pointer")
    result = verify(reviewed, head)
    assert result["ok"] is False


@pytest.mark.integration
def test_valid_update_where_main_adds_a_gitlink__ok(repo: Path) -> None:
    reviewed = _reviewed_with_ignored_submodule(repo)
    _git(repo, "checkout", "-q", "main")
    _gitlink(repo, "s", "4" * 40)
    tip = _commit(repo, "main: adds submodule pointer")
    _git(repo, "update-ref", "refs/remotes/origin/main", tip)
    _git(repo, "checkout", "-q", "pr")
    head = _update_pr(repo)
    assert verify(reviewed, head) == {"ok": True}


# Long chains, duplicate updates and shallow clones.


@pytest.mark.integration
def test_long_chain_of_twenty_updates__ok_within_seconds(repo: Path) -> None:
    reviewed = _commit(repo, "reviewed")
    head = reviewed
    started = time.monotonic()
    for i in range(20):
        _advance_main(repo, f"f{i}.txt", f"main {i}\n".encode())
        head = _update_pr(repo)
    assert verify(reviewed, head) == {"ok": True}
    assert time.monotonic() - started < 120


@pytest.mark.integration
def test_bad_merge_in_the_middle_of_a_long_chain__halts_naming_it(repo: Path) -> None:
    reviewed = _commit(repo, "reviewed")
    bad = ""
    for i in range(12):
        _advance_main(repo, f"f{i}.txt", f"main {i}\n".encode())
        if i == 6:
            _git(repo, "merge", "--no-ff", "--no-commit", "-q", "origin/main")
            _write(repo, "evil.txt", b"smuggled\n")
            bad = _commit(repo, "evil middle merge")
        else:
            _update_pr(repo)
    head = _git(repo, "rev-parse", "HEAD")
    result = verify(reviewed, head)
    assert result["ok"] is False
    assert bad[:12] in result["reason"]


@pytest.mark.integration
def test_same_update_merged_twice__ok(repo: Path) -> None:
    # A second merge of an S already in the history adds no change: the same tree, S ancestor.
    reviewed = _commit(repo, "reviewed")
    _advance_main(repo, "other.txt", b"main only\n")
    m1 = _update_pr(repo)
    s = _git(repo, "rev-parse", "origin/main")
    tree = _git(repo, "rev-parse", f"{m1}^{{tree}}")
    dup = _git(repo, "commit-tree", tree, "-p", m1, "-p", s, "-m", "duplicate update")
    assert verify(reviewed, dup) == {"ok": True}


@pytest.mark.integration
def test_shallow_clone_without_merge_base__never_ok(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    reviewed = _commit(repo, "reviewed")
    _advance_main(repo, "other.txt", b"main only\n")
    head = _update_pr(repo)
    clone = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth", "1", f"file://{repo.as_posix()}", str(clone)],
        check=True,
        capture_output=True,
    )
    monkeypatch.chdir(clone)
    try:
        result = verify(reviewed, head)
    except GitError:
        return  # refusing outright is also a halt
    assert result["ok"] is False
