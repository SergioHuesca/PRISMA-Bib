"""Rule g of ``scripts/train_verify_head.py``: an update merge whose tree is exactly git's own
clean, attribute-neutral merge of the PR head and main passes, even when both edited the same
file (the CHANGELOG). Anything else, and every conflict, still halts. Real temp repos."""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

_HERE = Path(__file__).parent
_spec = importlib.util.spec_from_file_location(
    "stacked_helpers", _HERE / "test_train_verify_head_stacked.py"
)
assert _spec is not None and _spec.loader is not None
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)
verify, _git, _f, _G1 = h.verify, h._git, h._f, h._G1
repo = h.repo  # the fixture, reused

_CHANGELOG = (
    "## Unreleased\n\n### Added\n\n- a1\n\n### Changed\n\n- c1\n\n### Fixed\n\n- f1\n\n"
    "### Docs\n\n- d1\n"
)


def _bullet(section: str, text: str, base: str = _CHANGELOG) -> str:
    marker = f"### {section}\n\n"
    return base.replace(marker, f"{marker}- {text}\n")


def _commit_all(repo: Path, msg: str) -> str:
    return h._commit_all(repo, msg)


def _merge_tree(repo: Path, a: str, b: str, base: str | None = None) -> str:
    args = ["-c", "merge.renames=false", "merge-tree", "--write-tree"]
    if base:
        args.append(f"--merge-base={base}")
    return _git(repo, *args, a, b).splitlines()[0]


def _entries_of(repo: Path, tree: str) -> h.Entries:
    out = subprocess.run(
        ["git", "ls-tree", "-r", "-z", tree], cwd=repo, check=True, capture_output=True
    ).stdout
    entries: h.Entries = {}
    for rec in filter(None, out.split(b"\0")):
        meta, path = rec.split(b"\t", 1)
        mode, _kind, sha = meta.decode().split()
        if mode == "160000":
            entries[path] = (mode, sha.encode())
        else:
            blob = subprocess.run(
                ["git", "cat-file", "blob", sha], cwd=repo, check=True, capture_output=True
            ).stdout
            entries[path] = (mode, blob)
    return entries


def _sprint(repo: Path) -> dict[str, str]:
    """pr1 and pr2 independent, pr3 stacked on pr1; each adds a bullet in its own section of the
    one CHANGELOG.md and a file."""
    h._write(repo, "CHANGELOG.md", _CHANGELOG)
    base = _commit_all(repo, "m0")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    heads = {}
    for name, parent, section in (
        ("pr1", "main", "Added"),
        ("pr2", "main", "Changed"),
        ("pr3", "pr1", "Fixed"),
    ):
        _git(repo, "checkout", "-q", "-b", name, parent)
        current = (repo / "CHANGELOG.md").read_text()
        h._write(repo, "CHANGELOG.md", _bullet(section, name, current))
        h._write(repo, f"{name}.txt", f"{name}\n")
        heads[name] = _commit_all(repo, name)
    return heads


def _update(repo: Path, branch: str) -> str:
    _git(repo, "checkout", "-q", branch)
    _git(repo, "merge", "--no-ff", "-q", "-m", "update from main", "origin/main")
    return _git(repo, "rev-parse", "HEAD")


@pytest.mark.integration
def test_sprint__three_prs_sharing_the_changelog__no_halt(repo: Path) -> None:
    heads = _sprint(repo)
    h._squash_into_main(repo, "pr1")
    m2 = _update(repo, "pr2")
    assert verify(heads["pr2"], m2) == {"ok": True}  # shared CHANGELOG.md: rule g
    h._squash_into_main(repo, "pr2")
    assert h._tree(repo, "main") == h._tree(repo, m2)
    m3 = _update(repo, "pr3")
    assert verify(heads["pr3"], m3) == {"ok": True}  # stacked: absorbed paths + shared file
    h._squash_into_main(repo, "pr3")
    assert h._tree(repo, "main") == h._tree(repo, m3)
    text = (repo / "CHANGELOG.md").read_text()
    assert all(f"- {x}\n" in text for x in ("pr1", "pr2", "pr3", "a1"))
    assert all((repo / f"pr{i}.txt").exists() for i in (1, 2, 3))


def _two_prs(repo: Path) -> tuple[str, str, str]:
    """pr edits Changed, main edits Added: git merges cleanly. Returns (R, main tip, T)."""
    h._write(repo, "CHANGELOG.md", _CHANGELOG)
    base = _commit_all(repo, "m0")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    _git(repo, "checkout", "-q", "-b", "pr")
    h._write(repo, "CHANGELOG.md", _bullet("Changed", "pr"))
    r = _commit_all(repo, "pr")
    _git(repo, "checkout", "-q", "main")
    h._write(repo, "CHANGELOG.md", _bullet("Added", "main"))
    tip = _commit_all(repo, "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", tip)
    _git(repo, "checkout", "-q", "pr")
    return r, tip, _merge_tree(repo, r, tip, base)


def _evil(repo: Path, r: str, tip: str, entries: h.Entries) -> dict[str, object]:
    return verify(r, h._mkcommit(repo, entries, r, tip))


@pytest.mark.integration
@pytest.mark.parametrize(
    "change",
    [
        {b"evil.txt": _f("x\n")},
        {b"CHANGELOG.md": ("100755", _CHANGELOG.encode())},
        {b"ln": ("120000", b"/etc/passwd")},
        {b"s": ("160000", _G1.encode())},
        {b"CHANGELOG.md": None},
    ],
    ids=["extra-file", "mode-change", "symlink", "gitlink", "deleted"],
)
def test_g__clean_merge_plus_anything__halts(repo: Path, change: h.Change) -> None:
    r, tip, t = _two_prs(repo)
    assert _evil(repo, r, tip, _entries_of(repo, t)) == {"ok": True}  # control
    assert _evil(repo, r, tip, h._apply(_entries_of(repo, t), change))["ok"] is False


@pytest.mark.integration
def test_g__one_extra_byte_in_the_shared_file__halts(repo: Path) -> None:
    r, tip, t = _two_prs(repo)
    entries = _entries_of(repo, t)
    entries[b"CHANGELOG.md"] = ("100644", entries[b"CHANGELOG.md"][1] + b"x")
    assert _evil(repo, r, tip, entries)["ok"] is False


@pytest.mark.integration
def test_g__tree_equal_to_P_drops_mains_change__halts(repo: Path) -> None:
    r, tip, _t = _two_prs(repo)
    result = _evil(repo, r, tip, _entries_of(repo, h._tree(repo, r)))
    assert result["ok"] is False
    assert "overlaps" in str(result["reason"]) or "omits" in str(result["reason"])


@pytest.mark.integration
def test_g__same_place_bullets_conflict__hand_resolved_merge_halts(repo: Path) -> None:
    h._write(repo, "CHANGELOG.md", _CHANGELOG)
    base = _commit_all(repo, "m0")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    _git(repo, "checkout", "-q", "-b", "pr")
    h._write(repo, "CHANGELOG.md", _bullet("Added", "pr"))
    r = _commit_all(repo, "pr")
    _git(repo, "checkout", "-q", "main")
    h._write(repo, "CHANGELOG.md", _bullet("Added", "main"))
    tip = _commit_all(repo, "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", tip)
    both = _CHANGELOG.replace("- a1\n", "- pr\n- main\n- a1\n")
    result = _evil(repo, r, tip, {b"CHANGELOG.md": _f(both)})
    h._halts(result, "overlaps")


@pytest.mark.integration
def test_g__two_merge_bases__fails_closed(repo: Path) -> None:
    m0 = h._mkcommit(repo, {b"CHANGELOG.md": _f(_CHANGELOG)})
    x = h._mkcommit(repo, {b"CHANGELOG.md": _f(_CHANGELOG), b"x.txt": _f("x\n")}, m0)
    y = h._mkcommit(repo, {b"CHANGELOG.md": _f(_CHANGELOG), b"y.txt": _f("y\n")}, m0)
    both = {b"x.txt": _f("x\n"), b"y.txt": _f("y\n")}
    p = h._mkcommit(repo, {**both, b"CHANGELOG.md": _f(_bullet("Changed", "pr"))}, x, y)
    s = h._mkcommit(repo, {**both, b"CHANGELOG.md": _f(_bullet("Added", "main"))}, y, x)
    _git(repo, "update-ref", "refs/remotes/origin/main", s)
    assert len(_git(repo, "merge-base", "--all", p, s).split()) == 2
    t = _merge_tree(repo, p, s, _git(repo, "merge-base", p, s))  # single-base merge tree
    m = h._mkcommit(repo, _entries_of(repo, t), p, s)
    h._halts(verify(p, m), "overlaps")


@pytest.mark.integration
def test_g__union_attribute_and_hostile_xdg__cannot_pass_a_different_tree(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    xdg = tmp_path / "xdg"
    (xdg / "git").mkdir(parents=True)
    (xdg / "git" / "attributes").write_text("* merge=union\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    h._write(repo, "f", "1\n2\n3\n")
    base = _commit_all(repo, "base")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    _git(repo, "checkout", "-q", "-b", "pr")
    h._write(repo, ".gitattributes", "f merge=union\n")
    h._write(repo, "f", "1\n2 pr\n3\n")
    r = _commit_all(repo, "pr")
    _git(repo, "checkout", "-q", "main")
    h._write(repo, "f", "1\n2 main\n3\n")
    tip = _commit_all(repo, "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", tip)
    _git(repo, "checkout", "-q", "pr")
    _git(repo, "merge", "--no-ff", "-q", "-m", "union merge", "origin/main")  # union: no conflict
    h._halts(verify(r, _git(repo, "rev-parse", "HEAD")), "overlaps")


@pytest.mark.integration
def test_g__renormalize_config__verdict_unchanged(repo: Path) -> None:
    _git(repo, "config", "merge.renormalize", "true")
    r, tip, t = _two_prs(repo)
    assert _evil(repo, r, tip, _entries_of(repo, t)) == {"ok": True}
    extra = h._apply(_entries_of(repo, t), {b"x": _f("x")})
    assert _evil(repo, r, tip, extra)["ok"] is False


@pytest.mark.integration
def test_g__rename_vs_modify_conflict__hand_resolved_halts(repo: Path) -> None:
    h._write(repo, "f.txt", h._lines(10))
    base = _commit_all(repo, "m0")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    _git(repo, "checkout", "-q", "-b", "pr")
    _git(repo, "mv", "f.txt", "g.txt")
    r = _commit_all(repo, "rename")
    _git(repo, "checkout", "-q", "main")
    edited = h._lines(10).replace("line 5", "line five")
    h._write(repo, "f.txt", edited)
    tip = _commit_all(repo, "edit")
    _git(repo, "update-ref", "refs/remotes/origin/main", tip)
    assert _evil(repo, r, tip, {b"g.txt": _f(edited)})["ok"] is False


@pytest.mark.integration
def test_g__clean_merge_of_a_branch_not_on_main__halts(repo: Path) -> None:
    r, _tip, _t = _two_prs(repo)
    _git(repo, "checkout", "-q", "-b", "other", _git(repo, "rev-parse", "origin/main~1"))
    h._write(repo, "side.txt", "side\n")
    _commit_all(repo, "other")
    _git(repo, "checkout", "-q", "pr")
    _git(repo, "merge", "--no-ff", "-q", "-m", "merge other", "other")
    h._halts(verify(r, _git(repo, "rev-parse", "HEAD")), "not on main")


@pytest.mark.integration
def test_g__conflicted_merge_tree_with_markers_committed_as_is__halts(repo: Path) -> None:
    h._write(repo, "CHANGELOG.md", _CHANGELOG)
    base = _commit_all(repo, "m0")
    _git(repo, "update-ref", "refs/remotes/origin/main", base)
    _git(repo, "checkout", "-q", "-b", "pr")
    h._write(repo, "CHANGELOG.md", _bullet("Added", "pr"))
    r = _commit_all(repo, "pr")
    _git(repo, "checkout", "-q", "main")
    h._write(repo, "CHANGELOG.md", _bullet("Added", "main"))
    tip = _commit_all(repo, "main")
    _git(repo, "update-ref", "refs/remotes/origin/main", tip)
    proc = subprocess.run(
        ["git", "merge-tree", "--write-tree", r, tip], cwd=repo, capture_output=True, check=False
    )
    assert proc.returncode == 1  # conflicts
    tree = proc.stdout.decode().splitlines()[0]
    marked = _entries_of(repo, tree)
    assert b"<<<<<<<" in marked[b"CHANGELOG.md"][1]
    assert _evil(repo, r, tip, marked)["ok"] is False
