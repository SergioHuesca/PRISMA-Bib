"""The post-merge audit of ``scripts/train_merge_async.py`` (ADR 0037 Decision 4, step 5) on
real temporary git repositories: no git mocks, no network, no platform-dependent code."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "train_merge_async.py"
_spec = importlib.util.spec_from_file_location("train_merge_async_audit", _SCRIPT)
assert _spec is not None and _spec.loader is not None
tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tm)


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True).stdout
    return out.decode().strip()


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
    for rel, text in files.items():
        (repo / rel).write_bytes(text.encode())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "test")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "t@example.invalid")
    _git(root, "init", "-q", "-b", "main")
    for key, val in (
        ("core.autocrlf", "false"),
        ("core.safecrlf", "false"),
        ("core.fileMode", "false"),
    ):
        _git(root, "config", key, val)
    monkeypatch.chdir(root)
    return root


def _world(repo: Path) -> tuple[str, str]:
    """main m0; a branch with head H on top of it. Returns (m0, H)."""
    m0 = _commit(repo, {"a.txt": "1\n"}, "m0")
    _git(repo, "checkout", "-q", "-b", "pr")
    head = _commit(repo, {"b.txt": "work\n"}, "work")
    _git(repo, "checkout", "-q", "main")
    return m0, head


def _squash(repo: Path, head: str) -> str:
    _git(repo, "merge", "--squash", head)
    _git(repo, "commit", "-q", "-m", "squash")
    return _git(repo, "rev-parse", "HEAD")


def test_audit__good_squash__passes(repo: Path) -> None:
    _, head = _world(repo)
    s = _squash(repo, head)
    assert tm.audit(s, head)["ok"] is True


def test_audit__main_moved_before_squash__fails_on_the_safe_side(repo: Path) -> None:
    # ADR: "if main moves during the poll the audit may fail without a fault".
    _, head = _world(repo)
    _commit(repo, {"c.txt": "other\n"}, "main moves")
    res = tm.audit(_squash(repo, head), head)
    assert res["ok"] is False and "tree" in res["reason"]


def test_audit__swapped_tree__fails(repo: Path) -> None:
    _, head = _world(repo)
    _git(repo, "merge", "--squash", head)
    (repo / "evil.txt").write_bytes(b"x\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "squash+evil")
    res = tm.audit(_git(repo, "rev-parse", "HEAD"), head)
    assert res["ok"] is False and "tree" in res["reason"]


def test_audit__parent_not_ancestor_of_head__fails(repo: Path) -> None:
    _, head = _world(repo)
    other = _commit(repo, {"c.txt": "other\n"}, "unrelated main work")
    # same tree as H, but its parent (other) is not an ancestor of H
    _git(repo, "checkout", "-q", "--detach", other)
    _git(repo, "read-tree", "--reset", "-u", head)
    _git(repo, "commit", "-q", "-m", "same tree, other parent")
    s = _git(repo, "rev-parse", "HEAD")
    res = tm.audit(s, head)
    assert res["ok"] is False and "ancestor" in res["reason"]


def test_audit__two_parents__fails(repo: Path) -> None:
    _, head = _world(repo)
    _git(repo, "merge", "--no-ff", "-q", "-m", "merge", head)
    s = _git(repo, "rev-parse", "HEAD")
    assert _git(repo, "rev-parse", f"{s}^{{tree}}") == _git(repo, "rev-parse", f"{head}^{{tree}}")
    res = tm.audit(s, head)
    assert res["ok"] is False and "2 parents" in res["reason"]


def test_audit__root_commit__fails(repo: Path) -> None:
    _, head = _world(repo)
    res = tm.audit(_git(repo, "rev-list", "--max-parents=0", "main"), head)
    assert res["ok"] is False and "0 parents" in res["reason"]


def test_audit__merge_commit_missing_locally__fails_with_reason(repo: Path) -> None:
    _, head = _world(repo)
    res = tm.audit("ab" * 20, head)
    assert res["ok"] is False and "merge commit is not present" in res["reason"]


def test_audit__head_missing_locally__fails_with_reason(repo: Path) -> None:
    _, head = _world(repo)
    s = _squash(repo, head)
    res = tm.audit(s, "ab" * 20)
    assert res["ok"] is False and "head is not present" in res["reason"]


@pytest.mark.parametrize("bad", [None, "", "XY", "AB" * 20])
def test_audit__malformed_sha__fails_without_crash(repo: Path, bad: object) -> None:
    _, head = _world(repo)
    assert tm.audit(bad, head)["ok"] is False


def test_run_end_to_end__real_audit_with_fake_runner(repo: Path) -> None:
    _, head = _world(repo)
    s = _squash(repo, head)
    uuid = "0b3a6b5e-1c2d-4e3f-8a9b-0123456789ab"
    replies = iter(
        [
            (200, {"merged": False, "head": {"sha": head}}),
            (202, {"details": {"uuid": uuid}}),
            (200, {"status": "merged"}),
            (200, {"merged": True, "merge_commit_sha": s}),
        ]
    )
    out = tm.run("o/r", 3, head, runner=lambda m, p, b: next(replies), sleep=lambda s_: None)
    assert out["outcome"] == "merged" and out["audit"]["ok"] is True
