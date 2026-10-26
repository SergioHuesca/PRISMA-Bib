"""Stacked PRs through ``scripts/train_verify_head.py``: the parent was squash-merged, so the
update merge M (parents P = the PR head, S = main) brings in a change P already holds.

Part 1 simulates the real sequence with porcelain git. Part 2 builds trees with plumbing
(any path bytes, modes, gitlinks) to attack the absorbed-path rule. No network.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "train_verify_head.py"
_spec = importlib.util.spec_from_file_location("train_verify_head", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
verify = _module.verify

Entries = dict[bytes, tuple[str, bytes]]
Change = dict[bytes, tuple[str, bytes] | None]  # None deletes the path
_ZERO = "0" * 40
_G1, _G2 = "1" * 40, "2" * 40


def _git(
    repo: Path, *args: str, env: dict[str, str] | None = None, stdin: bytes | None = None
) -> str:
    out = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, input=stdin,
        env={**os.environ, **(env or {})},
    ).stdout  # fmt: skip
    return out.decode().strip()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    for key, val in (("user.name", "test"), ("user.email", "t@example.invalid"),
                     ("core.autocrlf", "false")):  # fmt: skip
        _git(root, "config", key, val)
    monkeypatch.chdir(root)
    return root


# ---- part 1: the real sequence, porcelain git ------------------------------------------


def _write(repo: Path, rel: str, text: str) -> None:
    (repo / rel).write_text(text, encoding="utf-8", newline="\n")


def _commit_all(repo: Path, msg: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _squash_into_main(repo: Path, branch: str) -> None:
    _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--squash", branch)
    tip = _commit_all(repo, f"squash {branch}")
    _git(repo, "update-ref", "refs/remotes/origin/main", tip)


def _tree(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", f"{ref}^{{tree}}")


def _lines(n: int) -> str:
    return "".join(f"line {i}\n" for i in range(1, n + 1))


@pytest.fixture
def stack(repo: Path) -> Path:
    _write(repo, "base.txt", _lines(10))
    base = _commit_all(repo, "m0")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    # Stacked: b on a, c on b. Each edits its own line of the shared file, far from the others
    # (edits to the same or adjacent lines conflict, and the train halts on DIRTY).
    text = _lines(10).splitlines(keepends=True)
    for name, line in (("a", 1), ("b", 5), ("c", 9)):
        _git(repo, "checkout", "-q", "-b", name)
        _write(repo, f"{name}.txt", f"{name} work\n")
        text[line] = f"edited by {name}\n"
        _write(repo, "base.txt", "".join(text))
        _commit_all(repo, f"pr {name}")
    return repo


@pytest.mark.integration
def test_stack__parent_squash_merged__update_is_ok_and_squash_equals_head(stack: Path) -> None:
    repo = stack
    reviewed = _git(repo, "rev-parse", "b")
    _squash_into_main(repo, "a")
    _git(repo, "checkout", "-q", "b")
    _git(repo, "merge", "--no-ff", "-q", "-m", "update from main", "origin/main")
    head = _git(repo, "rev-parse", "HEAD")
    assert _tree(repo, head) == _tree(repo, reviewed)  # the absorbed case: M's tree == P's
    assert verify(reviewed, head) == {"ok": True}
    _squash_into_main(repo, "b")
    assert _tree(repo, "main") == _tree(repo, head)


@pytest.mark.integration
def test_stack__three_deep_with_updates_between_merges__ok(stack: Path) -> None:
    repo = stack
    reviewed_b, reviewed_c = _git(repo, "rev-parse", "b"), _git(repo, "rev-parse", "c")
    _squash_into_main(repo, "a")
    _git(repo, "checkout", "-q", "c")
    _git(repo, "merge", "--no-ff", "-q", "-m", "update 1", "origin/main")  # absorbs a
    assert verify(reviewed_c, _git(repo, "rev-parse", "HEAD")) == {"ok": True}
    _git(repo, "checkout", "-q", "b")
    _git(repo, "merge", "--no-ff", "-q", "-m", "update b", "origin/main")
    head_b = _git(repo, "rev-parse", "HEAD")
    assert verify(reviewed_b, head_b) == {"ok": True}
    _squash_into_main(repo, "b")
    assert _tree(repo, "main") == _tree(repo, head_b)
    _git(repo, "checkout", "-q", "c")
    _git(repo, "merge", "--no-ff", "-q", "-m", "update 2", "origin/main")  # absorbs b
    head_c = _git(repo, "rev-parse", "HEAD")
    assert verify(reviewed_c, head_c) == {"ok": True}
    _squash_into_main(repo, "c")
    assert _tree(repo, "main") == _tree(repo, head_c)


@pytest.mark.integration
def test_stack__main_also_has_an_independent_commit__remaining_verified_exactly(
    stack: Path,
) -> None:
    repo = stack
    reviewed = _git(repo, "rev-parse", "b")
    _git(repo, "checkout", "-q", "main")
    _write(repo, "indep.txt", "unrelated\n")
    _commit_all(repo, "independent")
    _squash_into_main(repo, "a")
    _git(repo, "checkout", "-q", "b")
    _git(repo, "merge", "--no-ff", "-q", "-m", "update", "origin/main")
    head = _git(repo, "rev-parse", "HEAD")
    assert (repo / "indep.txt").exists()
    assert verify(reviewed, head) == {"ok": True}
    _squash_into_main(repo, "b")
    assert _tree(repo, "main") == _tree(repo, head)


# ---- part 2: plumbing-built trees ------------------------------------------------------


def _f(text: str, mode: str = "100644") -> tuple[str, bytes]:
    return mode, text.encode()


def _sha(repo: Path, data: bytes) -> str:
    return _git(repo, "hash-object", "-w", "--stdin", stdin=data)


def _mktree(repo: Path, entries: Entries) -> str:
    idx = repo / ".git" / "plumbing-index"
    idx.unlink(missing_ok=True)
    cache: dict[bytes, str] = {}
    lines = []
    for path, (mode, payload) in entries.items():
        if mode == "160000":
            sha = payload.decode()
        else:
            if payload not in cache:
                cache[payload] = _sha(repo, payload)
            sha = cache[payload]
        lines.append(f"{mode} {sha}\t".encode() + path + b"\0")
    env = {"GIT_INDEX_FILE": str(idx)}
    # protectNTFS=false: Windows git silently drops names such as a"b or one with a newline.
    _git(repo, "-c", "core.protectNTFS=false", "update-index", "-z", "--add", "--index-info",
         env=env, stdin=b"".join(lines))  # fmt: skip
    return _git(repo, "write-tree", env=env)


def _mkcommit(repo: Path, entries: Entries, *parents: str) -> str:
    args = ["commit-tree", _mktree(repo, entries)]
    for parent in parents:
        args += ["-p", parent]
    return _git(repo, *args, "-m", "x")


def _apply(entries: Entries, change: Change) -> Entries:
    out = dict(entries)
    for path, value in change.items():
        if value is None:
            out.pop(path, None)
        else:
            out[path] = value
    return out


_BASE: Entries = {
    b"a.txt": _f("a\n"), b"c.txt": _f("c\n"), b"d.txt": _f("d\n"), b"old.txt": _f("old\n"),
    b"script.sh": _f("#!/bin/sh\n"), b"base.txt": _f(_lines(10)),
}  # fmt: skip


class Scenario:
    """M0 -> A -> B (= P) with main = M0 + a_change [+ main_extra] squashed (= S)."""

    def __init__(self, repo: Path, a: Change, b: Change | None = None,
                 main_extra: Change | None = None, base: Entries | None = None) -> None:  # fmt: skip
        self.repo = repo
        self.base = dict(_BASE if base is None else base)
        m0 = _mkcommit(repo, self.base)
        self.a_entries = _apply(self.base, a)
        head_a = _mkcommit(repo, self.a_entries, m0)
        self.p_entries = _apply(self.a_entries, b or {})
        self.p = _mkcommit(repo, self.p_entries, head_a)
        self.main_entries = _apply(self.a_entries, main_extra or {})
        self.s = _mkcommit(repo, self.main_entries, m0)
        _git(repo, "update-ref", "refs/remotes/origin/main", self.s)

    def merge(self, entries: Entries) -> str:
        return _mkcommit(self.repo, entries, self.p, self.s)

    def honest(self, **extra: object) -> Entries:
        """P's tree plus main's own change that P does not already hold."""
        return {**self.p_entries, **{k: v for k, v in self.main_entries.items()
                                     if self.p_entries.get(k) != v}}  # fmt: skip

    def check(self, entries: Entries) -> dict[str, object]:
        return verify(self.p, self.merge(entries))


def _halts(result: dict[str, object], needle: str) -> None:
    assert result["ok"] is False, result
    assert needle in str(result["reason"]), result


_A: Change = {b"a.txt": _f("a, changed by A\n"), b"new_a.txt": _f("A adds\n")}
_B: Change = {b"b_only.txt": _f("B adds\n")}


@pytest.mark.integration
def test_absorbed_only__tree_equals_P__ok(repo: Path) -> None:
    sc = Scenario(repo, _A, _B)
    assert sc.check(sc.p_entries) == {"ok": True}


@pytest.mark.integration
def test_absorbed_plus_independent_main_change__ok_when_exact(repo: Path) -> None:
    sc = Scenario(repo, _A, _B, main_extra={b"c.txt": _f("c, by X'\n")})
    assert sc.check(sc.honest()) == {"ok": True}


@pytest.mark.integration
@pytest.mark.parametrize(
    "a",
    [
        {b"script.sh": _f("#!/bin/sh\n", "100755")},
        {b"ln": ("120000", b"target")},
        {b"s": ("160000", _G1.encode())},
        {b"d.txt": None},
        {b"old.txt": None, b"new.txt": _f("old\n")},
    ],
    ids=["mode-only", "symlink", "gitlink", "deletion", "rename"],
)
def test_absorbed_special_entries__held_identically__ok(repo: Path, a: Change) -> None:
    (repo / ".gitmodules").write_text('[submodule "s"]\n\tpath = s\n\turl = ./x\n\tignore = all\n')
    sc = Scenario(repo, a, _B)
    assert sc.check(sc.p_entries) == {"ok": True}


@pytest.mark.integration
def test_attack1__main_changes_a_path_P_holds_differently__tree_equals_P__halts(
    repo: Path,
) -> None:
    sc = Scenario(repo, _A, _B, main_extra={b"a.txt": _f("main's later version of a\n")})
    _halts(sc.check(sc.p_entries), "overlaps the PR's own edits")


@pytest.mark.integration
def test_attack1b__absorbed_present_but_independent_change_dropped__halts(repo: Path) -> None:
    sc = Scenario(repo, _A, _B, main_extra={b"c.txt": _f("c, by X'\n")})
    _halts(sc.check(sc.p_entries), "omits main's change")


@pytest.mark.integration
@pytest.mark.parametrize(
    "target", [b"evil.txt", b"a.txt", b"new_a.txt"], ids=["new", "absorbed", "absorbed2"]
)
def test_attack2__extra_bytes_beyond_main_change__halts(repo: Path, target: bytes) -> None:
    sc = Scenario(repo, _A, _B, main_extra={b"c.txt": _f("c, by X'\n")})
    evil = {**sc.honest(), target: _f("smuggled\n")}
    _halts(sc.check(evil), "adds changes that main does not contain")


@pytest.mark.integration
def test_attack2b__extra_bytes_when_remaining_is_empty__halts(repo: Path) -> None:
    sc = Scenario(repo, _A, _B)
    _halts(sc.check({**sc.p_entries, b"evil.txt": _f("x\n")}), "adds changes")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("main_change", "p_change"),
    [
        ({b"script.sh": _f("#!/bin/sh\n", "100755")}, {}),
        ({b"ln": ("120000", b"one")}, {b"ln": ("120000", b"two")}),
        ({b"s": ("160000", _G1.encode())}, {b"s": ("160000", _G2.encode())}),
    ],
    ids=["mode", "symlink-target", "gitlink-pointer"],
)
def test_attack3__mode_symlink_gitlink_not_held_identically__halts(
    repo: Path, main_change: Change, p_change: Change
) -> None:
    (repo / ".gitmodules").write_text('[submodule "s"]\n\tpath = s\n\turl = ./x\n\tignore = all\n')
    sc = Scenario(repo, _A, p_change, main_extra=main_change)
    result = sc.check(sc.p_entries)
    assert result["ok"] is False
    assert "fresh review" in str(result["reason"]) or "main" in str(result["reason"])


@pytest.mark.integration
def test_attack3b__gitlink_bumped_inside_the_absorbed_set__halts(repo: Path) -> None:
    (repo / ".gitmodules").write_text('[submodule "s"]\n\tpath = s\n\turl = ./x\n\tignore = all\n')
    sc = Scenario(repo, {**_A, b"s": ("160000", _G1.encode())}, _B)
    _halts(sc.check({**sc.p_entries, b"s": ("160000", _G2.encode())}), "adds changes")


@pytest.mark.integration
def test_attack4__main_deleted_P_still_has_it__halts_but_both_deleted_is_ok(repo: Path) -> None:
    both = Scenario(repo, {**_A, b"d.txt": None}, _B)
    assert both.check(both.p_entries) == {"ok": True}
    only_main = Scenario(repo, _A, _B, main_extra={b"d.txt": None})
    _halts(only_main.check(only_main.p_entries), "omits main's change")


@pytest.mark.integration
def test_attack5__renames_are_not_detected__partial_rename_verified_per_path(repo: Path) -> None:
    # A renamed old.txt -> new.txt; P holds the new path only after A, main also deleted old
    # (the rename), so both paths are absorbed. Pretend M keeps old.txt: an extra path, halts.
    sc = Scenario(repo, {b"old.txt": None, b"new.txt": _f("old\n")}, _B)
    _halts(sc.check({**sc.p_entries, b"old.txt": _f("old\n")}), "adds changes")


@pytest.mark.integration
def test_attack6__same_file_conflicting_content__overlap_halts(repo: Path) -> None:
    # Both sides edit line 1 differently: merge-tree conflicts, so rule g cannot apply and the
    # hand-resolved merge is an unreviewed overlap.
    sc = Scenario(
        repo, _A, {b"base.txt": _f(_edit(_L, 0, "PR\n"))},
        main_extra={b"base.txt": _f(_edit(_L, 0, "MAIN\n"))},
    )  # fmt: skip
    _halts(sc.check({**sc.p_entries, b"base.txt": _f(_edit(_L, 0, "PR MAIN\n"))}), "overlaps")


@pytest.mark.integration
def test_attack8__second_parent_not_on_main__halts(repo: Path) -> None:
    sc = Scenario(repo, _A, _B)
    other = _mkcommit(
        repo, {**sc.base, b"side.txt": _f("side\n")}, _git(repo, "rev-parse", sc.s + "~1")
    )
    m = _mkcommit(repo, sc.p_entries, sc.p, other)
    _halts(verify(sc.p, m), "not on main")


@pytest.mark.integration
def test_attack9__hostile_path_bytes__ok_when_exact_and_halts_when_dropped(repo: Path) -> None:
    names = [b"nl\nname", b'q"uote', b":(top)x", b"-n", b"--help", b"glob*[a]?", b"sp ace",
             b"caf\xe9.txt", b":!x", b"\ttab"]  # fmt: skip
    a: Change = {n: _f("A holds " + n.hex() + "\n") for n in names[:6]}
    extra: Change = {n: _f("main " + n.hex() + "\n") for n in names[6:]}
    sc = Scenario(repo, a, _B, main_extra=extra)
    raw = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "-z", sc.s], capture_output=True, check=True
    ).stdout
    assert set(names) <= set(raw.split(bytes(1)))  # the hostile names really are in the tree
    assert sc.check(sc.honest()) == {"ok": True}
    dropped = sc.honest()
    dropped.pop(names[-1])
    _halts(sc.check(dropped), "omits main's change")
    assert sc.check({**sc.honest(), b"*": _f("glob would match all\n")})["ok"] is False


@pytest.mark.integration
def test_attack10__thousands_of_paths__ok_and_still_exact(repo: Path) -> None:
    a: Change = {
        f"dir/absorbed_{i:05d}_{'x' * 80}".encode(): _f(f"a{i % 7}\n") for i in range(4000)
    }
    extra: Change = {
        f"dir/remaining_{i:05d}_{'y' * 80}".encode(): _f(f"r{i % 5}\n") for i in range(4000)
    }
    sc = Scenario(repo, a, _B, main_extra=extra)
    assert sc.check(sc.honest()) == {"ok": True}
    wrong = sc.honest()
    wrong[b"dir/remaining_00042_" + b"y" * 80] = _f("tampered\n")
    _halts(sc.check(wrong), "adds changes")


def _edit(lines: list[str], at: int, text: str) -> str:
    out = list(lines)
    out[at] = text
    return "".join(out)


_L = _lines(10).splitlines(keepends=True)


@pytest.mark.integration
def test_shared_file__child_edits_parents_file__subsumed_main_change_is_absorbed(
    repo: Path,
) -> None:
    a: Change = {b"base.txt": _f(_edit(_L, 1, "by A\n"))}
    both = _edit(_edit(_L, 1, "by A\n").splitlines(keepends=True), 5, "by B\n")
    sc = Scenario(repo, a, {b"base.txt": _f(both)})
    assert sc.check(sc.p_entries) == {"ok": True}


@pytest.mark.integration
def test_shared_file__main_has_more_than_the_parent__tree_equals_P__halts(repo: Path) -> None:
    # main = A's hunk plus a second hunk (line 9) that P lacks: P does not hold main's change.
    a: Change = {b"base.txt": _f(_edit(_L, 1, "by A\n"))}
    both = _edit(_edit(_L, 1, "by A\n").splitlines(keepends=True), 5, "by B\n")
    more = _edit(_edit(_L, 1, "by A\n").splitlines(keepends=True), 8, "main only\n")
    sc = Scenario(repo, a, {b"base.txt": _f(both)}, main_extra={b"base.txt": _f(more)})
    _halts(sc.check(sc.p_entries), "overlaps the PR's own edits")


@pytest.mark.integration
def test_shared_file__merge_result_differs_from_P__halts(repo: Path) -> None:
    a: Change = {b"base.txt": _f(_edit(_L, 1, "by A\n"))}
    both = _edit(_edit(_L, 1, "by A\n").splitlines(keepends=True), 5, "by B\n")
    sc = Scenario(repo, a, {b"base.txt": _f(both)})
    tampered = _edit(both.splitlines(keepends=True), 9, "smuggled\n")
    _halts(sc.check({**sc.p_entries, b"base.txt": _f(tampered)}), "adds changes")


@pytest.mark.integration
def test_rename_detection_off__main_edit_must_not_be_carried_onto_a_renamed_copy(
    repo: Path,
) -> None:
    # P renamed base.txt away; main edited base.txt. With rename detection on, git's merge would
    # put that edit on moved.txt and base.txt would look absorbed (gone on both sides), so
    # M == P would pass and the edit would be dropped. Path-wise merge: modify/delete conflict.
    sc = Scenario(
        repo, _A, {b"base.txt": None, b"moved.txt": _f(_lines(10))},
        main_extra={b"base.txt": _f(_edit(_L, 4, "main edit\n"))},
    )  # fmt: skip
    result = sc.check(sc.p_entries)
    assert result["ok"] is False
    assert "fresh review" in str(result["reason"]) or "omits" in str(result["reason"])


# ---- gitattributes / merge drivers must never change the verdict ----------------------


def _attr_repo(repo: Path, pr_attrs: dict[str, str], main_attrs: dict[str, str]) -> tuple[str, str]:
    """base f='1 2 3'; PR edits line 2 (+ attributes); main deletes line 2 (+ attributes).
    Returns (R, evil M = R's tree with parents R and main: main's deletion reverted).
    The worktree stays checked out on the PR branch, so its .gitattributes are visible."""
    _write(repo, "f", "1\n2\n3\n")
    base = _commit_all(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    _git(repo, "checkout", "-q", "-b", "pr")
    for rel, text in pr_attrs.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        _write(repo, rel, text)
    _write(repo, "f", "1\n2x\n3\n")
    r = _commit_all(repo, "pr")
    _git(repo, "checkout", "-q", "main")
    for rel, text in main_attrs.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        _write(repo, rel, text)
    _write(repo, "f", "1\n3\n")
    tip = _commit_all(repo, "main deletes line 2")
    _git(repo, "update-ref", "refs/remotes/origin/main", tip)
    _git(repo, "checkout", "-q", "pr")
    evil = _git(repo, "commit-tree", _tree(repo, r), "-p", r, "-p", tip, "-m", "evil")
    return r, evil


@pytest.mark.integration
@pytest.mark.parametrize(
    ("pr_attrs", "main_attrs"),
    [
        ({".gitattributes": "* merge=union\n"}, {}),
        ({".gitattributes": "* merge=ours\n"}, {}),
        ({".gitattributes": "* -merge\n"}, {}),
        ({".gitattributes": "*.txt merge=binary\nf merge=binary\n"}, {}),
        ({".gitattributes": "f merge=union\n"}, {}),
        ({".gitattributes": "* export-ignore\n"}, {}),
        ({}, {".gitattributes": "* merge=union\n"}),
        ({"sub/.gitattributes": "* merge=union\n"}, {}),
        ({}, {}),
    ],
    ids=["union", "ours", "-merge", "binary", "union-on-f", "export-ignore", "main-union",
         "nested", "none"],
)  # fmt: skip
def test_attributes__evil_merge_reverting_main__halts_whatever_the_attributes(
    repo: Path, pr_attrs: dict[str, str], main_attrs: dict[str, str]
) -> None:
    r, evil = _attr_repo(repo, pr_attrs, main_attrs)
    _halts(verify(r, evil), "")  # any specific halt reason, but never ok
    result = verify(r, evil)
    assert "overlaps" in str(result["reason"]) or "omits main's change" in str(result["reason"])


@pytest.mark.integration
def test_attributes__info_attributes_cannot_matter(repo: Path) -> None:
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("* merge=union\n")
    r, evil = _attr_repo(repo, {}, {})
    with pytest.raises(_module.GitError, match="attributes"):  # fail closed, never ok
        verify(r, evil)


@pytest.mark.integration
def test_attributes__legit_stacked_update_still_ok_with_and_without_attributes(
    stack: Path,
) -> None:
    repo = stack
    reviewed = _git(repo, "rev-parse", "b")
    _squash_into_main(repo, "a")
    _git(repo, "checkout", "-q", "b")
    _git(repo, "merge", "--no-ff", "-q", "-m", "update", "origin/main")
    head = _git(repo, "rev-parse", "HEAD")
    assert verify(reviewed, head) == {"ok": True}
    _write(repo, ".gitattributes", "* merge=union\n*.txt export-ignore\n")  # worktree only
    assert verify(reviewed, head) == {"ok": True}


@pytest.mark.integration
def test_git_version_below_minimum__raises(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    real = _module._git
    monkeypatch.setattr(
        _module, "_git", lambda *a: b"git version 2.39.5\n" if a[0] == "version" else real(*a)
    )
    with pytest.raises(_module.GitError, match=r"2\.40"):
        verify("a" * 40, "b" * 40)


@pytest.mark.integration
def test_attributes__hostile_xdg_attributes__halts(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Redundant with core.attributesFile=devnull (which replaces the XDG default), so the
    # XDG neutralisation is belt and braces: removing only one of them does not fail this.
    xdg = tmp_path / "xdg"
    (xdg / "git").mkdir(parents=True)
    (xdg / "git" / "attributes").write_text("* merge=union\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    r, evil = _attr_repo(repo, {}, {})
    _halts(verify(r, evil), "")
    assert "omits" in str(verify(r, evil)["reason"]) or "overlaps" in str(verify(r, evil)["reason"])


@pytest.mark.integration
def test_attributes__repo_config_attributesfile__halts(repo: Path, tmp_path: Path) -> None:
    # Only the -c core.attributesFile override stops this: the setting is in the repo config.
    hostile = tmp_path / "hostile-attributes"
    hostile.write_text("* merge=union\n")
    _git(repo, "config", "core.attributesFile", str(hostile))
    r, evil = _attr_repo(repo, {}, {})
    assert verify(r, evil)["ok"] is False


@pytest.mark.integration
def test_attributes__merge_tree_env_is_pinned(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A system-wide gitattributes cannot be written in a test; assert the switch is set instead.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "hostile"))
    seen: list[dict[str, str]] = []
    real = subprocess.run

    def spy(cmd, *args, **kwargs):  # type: ignore[no-untyped-def]
        if "merge-tree" in cmd:
            seen.append(kwargs["env"])
            assert "core.attributesFile=" + os.devnull in cmd
        return real(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", spy)
    r, evil = _attr_repo(repo, {}, {})
    verify(r, evil)
    assert seen
    for env in seen:
        assert env["GIT_ATTR_NOSYSTEM"] == "1"
        assert env["GIT_ATTR_SOURCE"] == _module._EMPTY_TREE
        assert env["XDG_CONFIG_HOME"] != str(tmp_path / "hostile")


def _criss_cross(repo: Path, s_extra: Change) -> tuple[str, str, Entries]:
    """P = merge(X, Y) + pr.txt and S = merge(Y, X) + s_extra: two merge bases, X and Y."""
    m0 = _mkcommit(repo, {b"a.txt": _f("a\n")})
    x = _mkcommit(repo, {b"a.txt": _f("a\n"), b"x.txt": _f("x\n")}, m0)
    y = _mkcommit(repo, {b"a.txt": _f("a\n"), b"y.txt": _f("y\n")}, m0)
    both: Entries = {b"a.txt": _f("a\n"), b"x.txt": _f("x\n"), b"y.txt": _f("y\n")}
    p_entries = {**both, b"pr.txt": _f("pr\n")}
    p = _mkcommit(repo, p_entries, x, y)
    s = _mkcommit(repo, _apply(both, s_extra), y, x)
    _git(repo, "update-ref", "refs/remotes/origin/main", s)
    assert len(_git(repo, "merge-base", "--all", p, s).split()) == 2
    return p, s, p_entries


@pytest.mark.integration
def test_criss_cross__legitimate_update__ok_and_evil_merges_halt(repo: Path) -> None:
    p, s, p_entries = _criss_cross(repo, {b"z.txt": _f("main z\n")})
    legit = _mkcommit(repo, {**p_entries, b"z.txt": _f("main z\n")}, p, s)
    assert verify(p, legit) == {"ok": True}
    dropped = _mkcommit(repo, p_entries, p, s)  # forgets main's z.txt
    assert verify(p, dropped)["ok"] is False
    extra = _mkcommit(repo, {**p_entries, b"z.txt": _f("main z\n"), b"evil": _f("e\n")}, p, s)
    assert verify(p, extra)["ok"] is False


@pytest.mark.integration
def test_criss_cross__nothing_new_on_main__only_exact_tree_ok(repo: Path) -> None:
    p, s, p_entries = _criss_cross(repo, {})
    assert verify(p, _mkcommit(repo, p_entries, p, s)) == {"ok": True}
    evil = _mkcommit(repo, {**p_entries, b"evil": _f("e\n")}, p, s)
    assert verify(p, evil)["ok"] is False
