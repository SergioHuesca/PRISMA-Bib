"""Rule h of ``scripts/train_verify_head.py`` (ADR 0037): a stacked child that GitHub rebased
onto main after its parent was squash-merged.

Real temporary repositories, no git mocks, no network. GitHub's rebase is simulated by
cherry-picking the child's commits onto main (rename-aware, like GitHub's). Hostile paths and
long series are built with plumbing so no working tree is involved.
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "train_verify_head.py"
_spec = importlib.util.spec_from_file_location("train_verify_head", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
verify = _module.verify
InputError = _module.InputError

# str writes a file, None deletes it, ("+x", text) writes (or only chmods when text is None)
# and sets the executable bit, ("mv", new) renames.
Change = dict[str, str | tuple[str, str | None] | None]
_MAIN = "origin/main"


def _git(repo: Path, *args: str, stdin: bytes | None = None) -> str:
    out = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, input=stdin
    ).stdout
    return out.decode().strip()


def _lines(n: int = 20, tag: str = "line") -> str:
    return "".join(f"{tag} {i}\n" for i in range(1, n + 1))


def _line_edit(text: str, n: int, new: str) -> str:
    rows = text.splitlines(keepends=True)
    rows[n - 1] = new + "\n"
    return "".join(rows)


_BASE = _lines()
_CHANGELOG = "# Changelog\n\n## Unreleased\n\n### Added\n\n- old one\n- old two\n"


def _apply(repo: Path, change: Change) -> None:
    for rel, val in change.items():
        if val is None:
            _git(repo, "rm", "-q", "-f", rel)
        elif isinstance(val, tuple) and val[0] == "mv":
            _git(repo, "mv", rel, str(val[1]))
        else:
            text = val[1] if isinstance(val, tuple) else val
            if text is not None:
                (repo / rel).parent.mkdir(parents=True, exist_ok=True)
                (repo / rel).write_bytes(text.encode())
            _git(repo, "add", "-A")
            if isinstance(val, tuple):
                _git(repo, "update-index", "--chmod=+x", rel)
    _git(repo, "add", "-A")


def _commit(repo: Path, change: Change, msg: str = "c") -> str:
    _apply(repo, change)
    _git(repo, "commit", "-q", "--allow-empty", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


def _tree(repo: Path, ref: str) -> str:
    return _git(repo, "rev-parse", f"{ref}^{{tree}}")


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "r"
    root.mkdir()
    for key in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{key}_NAME", "test")
        monkeypatch.setenv(f"GIT_{key}_EMAIL", "t@example.invalid")
    _git(root, "init", "-q", "-b", "main")
    for key, val in (("core.autocrlf", "false"), ("core.safecrlf", "false")):
        _git(root, "config", key, val)
    monkeypatch.chdir(root)
    return root


@dataclass
class World:
    repo: Path
    m0: str
    a_first: str
    a1: str  # the parent's reviewed commit (R_parent)
    a_final: str  # the parent's merged head (P_final)
    m_parent: str  # the squash commit on main
    child: list[str]  # the child's commits, bottom first
    rc: str  # the child's reviewed commit (R_child)

    def check(self, head: str, **over: str) -> dict[str, object]:
        a = {"rc": self.rc, "pr": self.a1, "pf": self.a_final, "pm": self.m_parent, **over}
        return verify(a["rc"], head, _MAIN, a["pr"], a["pf"], a["pm"])


def _make_world(
    repo: Path,
    child: list[Change],
    parent: list[Change] | None = None,
    main_extra: Change | None = None,
    a_extra: Change | None = None,
    main_after: Change | None = None,
) -> World:
    """main m0; A (parent) and B (child, cut from A's reviewed commit); main may move; A is
    squash-merged (with a_extra added after B was cut, as a human might)."""
    m0 = _commit(
        repo,
        {"base.txt": _BASE, "f.txt": _lines(20, "f"), "del.txt": "doomed\n",
         "CHANGELOG.md": _CHANGELOG, "run.sh": "echo\n"},
        "m0",
    )  # fmt: skip
    _git(repo, "update-ref", f"refs/remotes/{_MAIN}", m0)
    _git(repo, "checkout", "-q", "-b", "a")
    parent = parent or [{"a.txt": "parent work\n"}]
    a_commits = [_commit(repo, c, f"a{i}") for i, c in enumerate(parent)]
    a1 = a_commits[-1]
    _git(repo, "checkout", "-q", "-b", "b")
    b_commits = [_commit(repo, c, f"b{i}") for i, c in enumerate(child)]
    a_final = a1
    _git(repo, "checkout", "-q", "main")
    if main_extra is not None:
        _commit(repo, main_extra, "main extra")
        _git(repo, "checkout", "-q", "a")  # A is updated from main, as the train would
        _git(repo, "merge", "--no-ff", "-q", "-m", "update a", "main")
        a_final = _git(repo, "rev-parse", "HEAD")
        _git(repo, "checkout", "-q", "main")
    if a_extra is not None:
        _git(repo, "checkout", "-q", "a")
        a_final = _commit(repo, a_extra, "a-extra")
        _git(repo, "checkout", "-q", "main")
    _git(repo, "merge", "--squash", "a")
    m_parent = _commit(repo, {}, "squash a")
    if main_after is not None:
        _commit(repo, main_after, "main after")
    _git(repo, "update-ref", f"refs/remotes/{_MAIN}", "main")
    return World(repo, m0, a_commits[0], a1, a_final, m_parent, b_commits, b_commits[-1])


def _rebase(w: World, onto: str = _MAIN, commits: list[str] | None = None) -> str:
    """GitHub's rebase: replay the commits on top of ``onto`` (rename-aware cherry-picks)."""
    _git(w.repo, "checkout", "-q", "--detach", onto)
    for c in commits or w.child:
        _git(w.repo, "cherry-pick", "--allow-empty", c)
    return _git(w.repo, "rev-parse", "HEAD")


def _double_rebase(w: World) -> str:
    """The pilot's double rebase: first onto the parent's head, then onto main."""
    first = _rebase(w, w.a_final)
    return _rebase(w, _MAIN, _git(w.repo, "rev-list", "--reverse", f"{w.a_final}..{first}").split())


def _advance_main(repo: Path, change: Change) -> None:
    _git(repo, "checkout", "-q", "main")
    _commit(repo, change, "main advances")
    _git(repo, "update-ref", f"refs/remotes/{_MAIN}", "main")


def _update_merge(repo: Path, head: str, evil: Change | None = None) -> str:
    _git(repo, "checkout", "-q", "--detach", head)
    _git(repo, "merge", "--no-ff", "--no-commit", "-q", _MAIN)
    if evil:
        _apply(repo, evil)
    _git(repo, "commit", "-q", "-m", "update from main")
    return _git(repo, "rev-parse", "HEAD")


def _halts(result: dict[str, object], rule: str) -> None:
    assert result["ok"] is False, result
    assert str(result["reason"]).startswith(rule + ":"), result


# ---- accepted -------------------------------------------------------------------------


@pytest.mark.integration
def test_two_level_stack__rebased_onto_main__ok(repo: Path) -> None:
    w = _make_world(repo, [{"base.txt": _line_edit(_BASE, 10, "child edit")}])
    assert w.check(_rebase(w)) == {"ok": True}


@pytest.mark.integration
def test_two_level_stack__double_rebase__ok(repo: Path) -> None:
    w = _make_world(repo, [{"base.txt": _line_edit(_BASE, 10, "child edit")}])
    assert w.check(_double_rebase(w)) == {"ok": True}


@pytest.mark.integration
def test_stack__main_moved_before_the_squash__ok(repo: Path) -> None:
    w = _make_world(
        repo,
        [{"base.txt": _line_edit(_BASE, 10, "child edit")}],
        main_extra={"base.txt": _line_edit(_BASE, 18, "main edit")},
    )
    head = _rebase(w)
    assert "main edit" in (repo / "base.txt").read_text()
    assert w.check(head) == {"ok": True}


@pytest.mark.integration
def test_stack__x_is_an_older_ancestor_of_main__ok(repo: Path) -> None:
    w = _make_world(repo, [{"base.txt": _line_edit(_BASE, 10, "child edit")}])
    head = _rebase(w, w.m_parent)  # rebased when main was at the squash commit
    _advance_main(repo, {"other.txt": "main moved\n"})
    assert w.check(head) == {"ok": True}  # X is not the tip of main


@pytest.mark.integration
def test_stack__three_commit_child__ok(repo: Path) -> None:
    w = _make_world(
        repo,
        [{"base.txt": _line_edit(_BASE, 3, "one")},
         {"b2.txt": "two\n"},
         {"base.txt": _line_edit(_line_edit(_BASE, 3, "one"), 12, "three")}],
    )  # fmt: skip
    head = _rebase(w)
    assert w.check(head) == {"ok": True}
    assert w.check(_double_rebase(w)) == {"ok": True}


@pytest.mark.integration
def test_stack__child_deletes_a_file__ok(repo: Path) -> None:
    w = _make_world(repo, [{"del.txt": None}])
    head = _rebase(w)
    assert not (repo / "del.txt").exists()
    assert w.check(head) == {"ok": True}


@pytest.mark.integration
def test_stack__child_changes_a_mode_and_adds_an_executable__ok(repo: Path) -> None:
    w = _make_world(repo, [{"run.sh": ("+x", None)}, {"tool.sh": ("+x", "echo tool\n")}])
    head = _rebase(w)
    assert _git(repo, "ls-tree", head, "run.sh", "tool.sh").count("100755") == 2
    assert w.check(head) == {"ok": True}


@pytest.mark.integration
def test_stack__adjacent_changelog_hunks__ok(repo: Path) -> None:
    parent_log = _CHANGELOG.replace("- old one", "- parent bullet\n- old one")
    child_log = parent_log.replace("- parent bullet", "- child bullet\n- parent bullet")
    w = _make_world(repo, [{"CHANGELOG.md": child_log}], parent=[{"CHANGELOG.md": parent_log}])
    assert w.check(_rebase(w)) == {"ok": True}


@pytest.mark.integration
def test_stack__main_moved_with_an_honest_update_merge__ok(repo: Path) -> None:
    w = _make_world(repo, [{"base.txt": _line_edit(_BASE, 10, "child edit")}])
    head = _rebase(w)
    _advance_main(repo, {"other.txt": "main moved\n"})
    assert w.check(_update_merge(repo, head)) == {"ok": True}


# ---- halts ----------------------------------------------------------------------------


@pytest.mark.integration
def test_stack__r_parent_not_an_ancestor_of_r_child__halts(repo: Path) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    side = _git(repo, "commit-tree", _tree(repo, "main"), "-p", w.m0, "-m", "side")
    _halts(w.check(_rebase(w), pr=side), "h2")


@pytest.mark.integration
def test_stack__r_parent_equals_r_child__halts(repo: Path) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    _halts(w.check(_rebase(w), pr=w.rc), "h2")


@pytest.mark.integration
def test_stack__extra_bytes_in_the_rebased_head__halts(repo: Path) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    _rebase(w)
    head = _commit(repo, {"smuggled.txt": "evil\n"}, "extra")
    _halts(w.check(head), "h3")


@pytest.mark.integration
def test_stack__a_missing_main_change__halts(repo: Path) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}], main_extra={"mainfile.txt": "main\n"})
    _rebase(w)
    _commit(repo, {"mainfile.txt": None}, "drops main's change")
    _git(repo, "reset", "-q", "--soft", "HEAD~2")  # one commit: child + dropped main file
    head = _commit(repo, {}, "squashed")
    _halts(w.check(head), "h3")


@pytest.mark.integration
def test_stack__child_conflicts_with_main__halts(repo: Path) -> None:
    w = _make_world(
        repo,
        [{"base.txt": _line_edit(_BASE, 5, "CHILD5")}],
        main_extra={"base.txt": _line_edit(_BASE, 5, "MAIN5")},
    )
    # a head carrying the child's bytes on top of main: no merge of the two exists
    head = _git(repo, "commit-tree", _tree(repo, w.rc), "-p", _MAIN, "-m", "forged")
    _halts(w.check(head), "h4")


@pytest.mark.integration
def test_stack__rename_of_a_file_main_edits__halts_though_github_rebase_succeeds(
    repo: Path,
) -> None:
    w = _make_world(
        repo,
        [{"f.txt": ("mv", "g.txt")}],
        main_extra={"f.txt": _line_edit(_lines(20, "f"), 18, "main edit")},
    )
    head = _rebase(w)  # rename-aware: succeeds and keeps main's edit in g.txt
    assert "main edit" in (repo / "g.txt").read_text()
    _halts(w.check(head), "h4")  # our merge is not rename-aware: a documented false halt


@pytest.mark.integration
def test_stack__merge_commit_in_the_series__halts(repo: Path) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    _git(repo, "checkout", "-q", "--detach", _MAIN)
    side = _git(repo, "commit-tree", _tree(repo, _MAIN), "-p", _MAIN, "-m", "side")
    c1 = _commit(repo, {"b.txt": "child\n"}, "c1")
    merge = _git(repo, "commit-tree", _tree(repo, c1), "-p", c1, "-p", side, "-m", "merge")
    head = _git(repo, "commit-tree", _tree(repo, merge), "-p", merge, "-m", "top")
    _halts(w.check(head), "h1")


@pytest.mark.integration
def test_stack__root_commit_series__halts(repo: Path) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    root = _git(repo, "commit-tree", _tree(repo, w.rc), "-m", "orphan root")
    top = _git(repo, "commit-tree", _tree(repo, w.rc), "-p", root, "-m", "on the root")
    _halts(w.check(root), "h1")
    _halts(w.check(top), "h1")


@pytest.mark.integration
def test_stack__head_whose_parent_is_not_on_main__halts(repo: Path) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    _rebase(w)
    side = _commit(repo, {"side.txt": "off main\n"}, "off main")
    head = _git(repo, "commit-tree", _tree(repo, side), "-p", side, "-m", "on a side branch")
    _halts(w.check(head), "h3")  # the side branch's bytes are not main plus the reviewed change


@pytest.mark.integration
def test_stack__head_already_on_main__halts(repo: Path) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    _halts(w.check(w.m_parent), "h1")


@pytest.mark.integration
def test_stack__v3_human_squash_merged_parent_with_an_extra_commit__h6_halts(
    repo: Path,
) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}], a_extra={"extra.txt": "unreviewed\n"})
    head = _rebase(w)
    # rule h itself accepts the child: the extra commit is now main's own change
    assert w.check(head, pf=w.a1, pm=w.a1) == {"ok": True}
    # with the real facts the parent fails h6; no label helps
    _halts(w.check(head), "h6")
    _halts(w.check(head, pr=w.a_final), "h2")


@pytest.mark.integration
def test_stack__v4_old_r_parent_with_a_line_main_reverted__halts_through_h6(
    repo: Path,
) -> None:
    w = _make_world(
        repo,
        [{"b.txt": "child\n"}],
        parent=[{"p.txt": "base\n"}, {"p.txt": "base\nreverted line\n"}],
        main_after={"p.txt": "base\n"},  # main deliberately reverts the parent's line
    )
    _rebase(w)
    _commit(repo, {"p.txt": "base\nreverted line\n"}, "re-adds the reverted line")
    _git(repo, "reset", "-q", "--soft", "HEAD~2")
    head = _commit(repo, {}, "child + re-added line")
    # the old label a_first passes h1 to h5 (the re-added line is in rc - a_first) ...
    _halts(w.check(head, pr=w.a_first), "h6")
    # ... and the true label rejects the head through h3
    _halts(w.check(head), "h3")


@pytest.mark.integration
def test_stack__tree_of_m_parent_differs_from_p_final__halts(repo: Path) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    _halts(w.check(_rebase(w), pm=w.m0), "h6")


@pytest.mark.integration
def test_stack__parent_commit_missing_from_the_repository__halts(repo: Path) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    _halts(w.check(_rebase(w), pm="e" * 40), "h6")


@pytest.mark.integration
def test_stack__rule_h_passes_but_update_merge_is_evil__halts(repo: Path) -> None:
    w = _make_world(repo, [{"base.txt": _line_edit(_BASE, 10, "child edit")}])
    t = _rebase(w)
    assert w.check(t) == {"ok": True}  # the honest T alone is accepted by rule h
    _advance_main(repo, {"other.txt": "main moved\n"})
    honest = _update_merge(repo, t)
    assert w.check(honest) == {"ok": True}  # the honest pair
    evil = _update_merge(repo, t, evil={"evil.txt": "smuggled\n"})
    _halts(w.check(evil), "h5")
    # an evil merge whose second parent is not main
    side = _git(repo, "commit-tree", _tree(repo, t), "-p", t, "-m", "side")
    merge = _git(repo, "commit-tree", _tree(repo, t), "-p", t, "-p", side, "-m", "m")
    _halts(w.check(merge), "h5")


@pytest.mark.integration
def test_stack__x1_old_head_plus_update_merge_with_m_parent__judged_by_rule_h(
    repo: Path,
) -> None:
    w = _make_world(repo, [{"base.txt": _line_edit(_BASE, 10, "child edit")}])
    _git(repo, "checkout", "-q", "--detach", w.rc)  # the old, never-rebased head
    _git(repo, "merge", "--no-ff", "-q", "-m", "update from main", _MAIN)
    x1 = _git(repo, "rev-parse", "HEAD")
    assert verify(w.rc, x1, _MAIN) == {"ok": True}  # today's verifier accepts it ...
    _halts(w.check(x1), "h3")  # ... rule h does not


# ---- arguments ------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "given",
    [c for n in (1, 2) for c in itertools.combinations(("pr", "pf", "pm"), n)],
    ids="-".join,
)
def test_stack__parent_arguments_all_or_none__partial_is_input_error(
    repo: Path, given: tuple[str, ...]
) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    head = _rebase(w)
    vals = {"pr": w.a1, "pf": w.a_final, "pm": w.m_parent}
    kw = {
        {"pr": "parent_reviewed", "pf": "parent_head", "pm": "parent_merge"}[k]: vals[k]
        for k in given
    }
    with pytest.raises(InputError):
        verify(w.rc, head, _MAIN, **kw)
    with pytest.raises(InputError):
        verify(w.rc, head, _MAIN, w.a1, w.a_final, "not-hex")


@pytest.mark.integration
def test_stack__cli__partial_is_exit_2_and_full_prints_ok(
    repo: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    head = _rebase(w)
    assert _module.main([w.rc, head, "--parent-reviewed", w.a1]) == 2
    full = ["--parent-reviewed", w.a1, "--parent-head", w.a_final, "--parent-merge", w.m_parent]
    capsys.readouterr()
    assert _module.main([w.rc, head, *full]) == 0
    assert json.loads(capsys.readouterr().out) == {"ok": True}


@pytest.mark.integration
def test_stack__r_equals_h_shortcut_does_not_apply_to_stacked_heads(repo: Path) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    assert verify(w.rc, w.rc, _MAIN) == {"ok": True}  # today's shortcut
    result = w.check(w.rc)  # an unrebased child is not a rebased head
    assert result["ok"] is False and str(result["reason"]).startswith("h")


# ---- weird attacks --------------------------------------------------------------------


def _union_conflict_world(repo: Path, attrs_in_child: bool) -> tuple[World, str]:
    child: Change = {"base.txt": _line_edit(_BASE, 5, "CHILD5")}
    if attrs_in_child:
        child[".gitattributes"] = "* merge=union\n"
    w = _make_world(repo, [child], main_extra={"base.txt": _line_edit(_BASE, 5, "MAIN5")})
    union = _line_edit(_BASE, 5, "MAIN5\nCHILD5")  # what merge=union would produce
    _git(repo, "checkout", "-q", "--detach", _MAIN)
    head = _commit(
        repo,
        {"base.txt": union, **({".gitattributes": "* merge=union\n"} if attrs_in_child else {})},
    )
    return w, head


@pytest.mark.integration
def test_stack__merge_union_attribute_committed_in_the_child__does_not_change_the_verdict(
    repo: Path,
) -> None:
    w, head = _union_conflict_world(repo, attrs_in_child=True)
    _halts(w.check(head), "h4")


@pytest.mark.integration
def test_stack__hostile_xdg_attributes__do_not_change_the_verdict(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xdg = tmp_path / "xdg"
    (xdg / "git").mkdir(parents=True)
    (xdg / "git" / "attributes").write_text("* merge=union\n")
    glob = tmp_path / "gitconfig"
    glob.write_text(f"[core]\n\tattributesFile = {(xdg / 'git' / 'attributes').as_posix()}\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(glob))
    w, head = _union_conflict_world(repo, attrs_in_child=False)
    _halts(w.check(head), "h4")


def _plumb(repo: Path, base: str, entries: dict[bytes, bytes]) -> str:
    """The tree of ``base`` plus the given path -> blob content entries (any path bytes)."""
    env = {**os.environ, "GIT_INDEX_FILE": str(repo / ".git" / "plumb-index")}
    run = lambda *a, i=None: subprocess.run(  # noqa: E731
        ["git", "-c", "core.protectNTFS=false", *a],
        cwd=repo,
        env=env,
        input=i,
        capture_output=True,
        check=True,
    ).stdout.strip()
    run("read-tree", base)
    info = b""
    for path, data in entries.items():
        blob = run("hash-object", "-w", "--stdin", i=data)
        info += b"100644 " + blob + b"\t" + path + b"\0"
    run("update-index", "-z", "--index-info", i=info)
    return run("write-tree").decode()


_HOSTILE = b'd ir/\xff\xfe weird\nname "q" -rf.txt'


@pytest.mark.integration
def test_stack__hostile_path_bytes__accepted_exactly_and_halts_on_a_different_byte(
    repo: Path,
) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    rc_tree = _plumb(repo, w.rc, {_HOSTILE: b"payload\n"})
    rc = _git(repo, "commit-tree", rc_tree, "-p", w.rc, "-m", "hostile child")
    head_tree = _plumb(repo, _MAIN, {_HOSTILE: b"payload\n", b"b.txt": b"child\n"})
    head = _git(repo, "commit-tree", head_tree, "-p", _MAIN, "-m", "rebased")
    assert w.check(head, rc=rc) == {"ok": True}
    bad_tree = _plumb(repo, _MAIN, {_HOSTILE: b"PAYLOAD\n", b"b.txt": b"child\n"})
    bad = _git(repo, "commit-tree", bad_tree, "-p", _MAIN, "-m", "tampered")
    _halts(w.check(bad, rc=rc), "h3")
    other_path = _plumb(repo, _MAIN, {_HOSTILE + b"x": b"payload\n", b"b.txt": b"child\n"})
    moved = _git(repo, "commit-tree", other_path, "-p", _MAIN, "-m", "other path")
    _halts(w.check(moved, rc=rc), "h3")


def _chain(repo: Path, parent: str, tree: str, n: int) -> str:
    for _ in range(n):
        parent = _git(repo, "commit-tree", tree, "-p", parent, "-m", "c")
    return parent


@pytest.mark.integration
@pytest.mark.parametrize(("extra", "ok"), [(499, True), (500, False)])
def test_stack__series_longer_than_500_commits__halts(repo: Path, extra: int, ok: bool) -> None:
    w = _make_world(repo, [{"b.txt": "child\n"}])
    h1 = _rebase(w)
    rc = _chain(repo, w.rc, _tree(repo, w.rc), extra)
    head = _chain(repo, h1, _tree(repo, h1), extra)
    result = w.check(head, rc=rc)
    if ok:
        assert result == {"ok": True}
    else:
        _halts(result, "h1")
