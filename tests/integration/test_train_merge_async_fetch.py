"""The audit fetches the squash commit S (and H) by sha when the clone lacks them (ADR 0037 PR 4).

GitHub creates S after the workflow's last fetch, so without the fetch every successful stacked
merge would audit as "not present locally". Real git, a local bare ``origin``; no network.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "train_merge_async.py"
_spec = importlib.util.spec_from_file_location("train_merge_async_fetch", _SCRIPT)
assert _spec is not None and _spec.loader is not None
tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tm)


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True).stdout
    return out.decode().strip()


def _commit(repo: Path, files: dict[str, str], msg: str) -> str:
    for rel, text in files.items():
        (repo / rel).write_bytes(text.encode())
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


class World:
    seed: Path
    clone: Path
    m0: str
    head: str
    squash: str


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "test")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "t@example.invalid")
    w = World()
    w.seed, bare, w.clone = tmp_path / "seed", tmp_path / "origin.git", tmp_path / "clone"
    w.seed.mkdir()
    _git(w.seed, "init", "-q", "-b", "main")
    _git(w.seed, "config", "core.fileMode", "false")
    w.m0 = _commit(w.seed, {"a.txt": "1\n"}, "m0")
    _git(w.seed, "checkout", "-q", "-b", "pr")
    w.head = _commit(w.seed, {"b.txt": "work\n"}, "work")
    _git(w.seed, "checkout", "-q", "main")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
    _git(w.seed, "remote", "add", "origin", str(bare))
    _git(w.seed, "push", "-q", "origin", "main", "pr")
    subprocess.run(["git", "clone", "-q", str(bare), str(w.clone)], check=True)
    _git(w.clone, "config", "core.fileMode", "false")
    _git(w.clone, "fetch", "-q", "origin", "pr")  # the workflow had fetched H
    # GitHub now squash-merges: S (parent m0, tree of H) lands on main, then main moves on, so
    # S is not even the tip of origin/main.
    _git(w.seed, "merge", "--squash", w.head)
    _git(w.seed, "commit", "-q", "-m", "squash")
    w.squash = _git(w.seed, "rev-parse", "HEAD")
    _commit(w.seed, {"c.txt": "later\n"}, "later")
    _git(w.seed, "push", "-q", "origin", "main")
    monkeypatch.chdir(w.clone)
    return w


def test_audit__clone_lacks_s__fetches_it_and_passes(world):
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", world.squash], cwd=world.clone, check=False
        ).returncode
        != 0
    )
    result = tm.audit(world.squash, world.head)
    assert result["ok"] is True, result
    assert (
        subprocess.run(
            ["git", "cat-file", "-e", world.squash], cwd=world.clone, check=False
        ).returncode
        == 0
    )


def test_audit__clone_lacks_h_too__fetches_both(world):
    _git(world.clone, "update-ref", "-d", "refs/remotes/origin/pr")
    _git(world.clone, "gc", "-q", "--prune=now")
    assert tm.audit(world.squash, world.head)["ok"] is True


def test_audit__fetch_fails__audit_failure_with_a_specific_reason(world):
    _git(world.clone, "remote", "set-url", "origin", str(world.clone / "nowhere.git"))
    result = tm.audit(world.squash, world.head)
    assert result["ok"] is False
    assert "cannot fetch the merge commit" in result["reason"]


def test_audit__fetch_succeeds_but_commit_still_missing__failure():
    # an injected fetch that does nothing: the commit stays absent
    result = tm.audit("ab" * 20, "cd" * 20, fetch=lambda sha: None)
    assert result["ok"] is False and "not present locally after fetching" in result["reason"]


def test_audit__fetch_times_out__audit_failure_not_a_crash(world, monkeypatch):
    real_run = subprocess.run
    seen: dict[str, object] = {}

    def run(cmd, *args, **kwargs):
        if "fetch" in cmd:
            seen["timeout"] = kwargs.get("timeout")
            raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout"))
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    result = tm.audit(world.squash, world.head)
    assert result["ok"] is False
    assert "cannot fetch the merge commit" in result["reason"] and "timed out" in result["reason"]
    assert seen["timeout"] == tm.FETCH_TIMEOUT_S == 120


def test_git__default_has_no_timeout_so_local_calls_are_unchanged(world, monkeypatch):
    seen: dict[str, object] = {}
    real_run = subprocess.run

    def run(cmd, *args, **kwargs):
        seen["timeout"] = kwargs.get("timeout", "absent")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", run)
    tm._git("rev-parse", "HEAD")
    assert seen["timeout"] is None


def test_audit__fetch_is_called_once_per_missing_sha_and_never_for_present_ones(world):
    calls: list[str] = []
    tm.audit(world.squash, world.head, fetch=lambda sha: (calls.append(sha), tm._fetch(sha))[1])
    assert calls == [world.squash]  # H was already local


def test_audit__fetch_error_text_is_scrubbed_of_the_token(world, monkeypatch):
    tok = "ghs_" + "SENTINEL123"
    monkeypatch.setenv("GH_TOKEN", tok)

    def boom(sha: str) -> None:
        raise tm.GitError("fatal: could not read " + tok)

    result = tm.audit(world.squash, world.head, fetch=boom)
    assert tok not in result["reason"]
