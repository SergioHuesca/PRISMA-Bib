"""Decide whether a PR head is its reviewed commit plus only verified update merges.

Usage: ``train_verify_head.py <reviewed_sha> <head_sha> [--main origin/main]``. Both shas must
be 40 lowercase hex. The caller has fetched main and the PR head (``pull/<n>/head``) as data;
nothing from the PR is checked out or executed. Prints ``{"ok": true}`` or
``{"ok": false, "reason": "..."}`` and exits 0. Exits 2 on malformed arguments or an
unexpected git failure; the workflow then halts the head.

Rules, all of which must hold for ``ok``:
  a. the reviewed commit R is an ancestor of H on H's first-parent chain (or R == H);
  b. every commit from R to H on the first-parent chain is a merge with exactly two parents
     (P first, S second): a one-parent commit is an unreviewed change, an octopus is refused;
  c. S is an ancestor of main (not necessarily its tip): a merge of any other branch is
     refused;
  d. the merge adds only main's own change: ``git diff P M`` is byte-identical to
     ``git diff MB S`` (MB = merge-base of P and S), both with --binary --full-index;
  e. if the PR and main edited the same file the two diffs differ; that is reported as an
     overlap and needs a fresh review (fail closed).
  f. stacked PRs: a path that main changed (MB..S) but whose change P already holds is
     ABSORBED, e.g. the parent PR's own commits after the parent was squash-merged. "Holds"
     means git's own path-wise merge of P and S (merge-tree, renames off, MB pinned, git >= 2.40, gitattributes and merge
     drivers ignored) yields
     P's entry there: P equals S, or S's hunks are all already in P (the child also edits the
     parent's file). Rule d then applies to the REMAINING paths only: the raw diff of P
     against M must be exactly main's raw diff restricted to them (so M equals P on every
     absorbed path and equals S on the rest). Rule e is checked on the remaining paths. If
     that merge conflicts, only paths where P equals S are absorbed. With no absorbed path,
     d and e are unchanged.

  g. clean automerge: M's tree is exactly the tree of git's own merge of P and S (the same
     attribute-neutral merge-tree as rule f, one merge base, no conflicts). Rules d, e and f
     are then not needed. M is a deterministic function of the reviewed head and main, so no
     byte can be injected; whether the merged result is semantically right is left to the
     required CI checks that run on the merged head (as for GitHub's update-branch; if its merge differs, the tree differs and the head halts). Checked
     before any halt of d, e or f; a conflicting merge never passes g.

Stacked children (ADR 0037): with ``--parent-reviewed R_parent --parent-head P_final
--parent-merge M_parent`` (all or none) rule h REPLACES the ``R == H`` shortcut and rule a. H is
a child that GitHub rebased onto main after its parent's squash merge. Walk H's first-parent
chain: two-parent merges at the top are update merges made after the rebase; T is the first
commit with one parent; the series is ``main..T`` on the first-parent chain and X the parent of
its bottom commit.
  h1. series not empty, every commit one parent, bottom commit has a parent, X is an ancestor of
      main, at most 500 commits;
  h2. R_parent is a proper ancestor of R_child (``reviewed``);
  h3. tree(T) is the tree of ``merge-tree --write-tree --merge-base=R_parent X R_child`` (the
      pinned environment of rule g), i.e. X + (R_child - R_parent);
  h4. that merge has no conflict;
  h5. each update merge above T passes ``verify(T, H)`` (rules a to g, T as reviewed commit);
  h6. tree(M_parent) == tree(P_final) and ``verify(R_parent, P_final)`` (rules a to g) passes.

Git runs with global and system config neutralised and the diff options pinned, so a
machine's git settings cannot change the result.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_step import clean_text

_SHA_RE = re.compile(r"[0-9a-f]{40}")
# Owned and empty: git reads $XDG_CONFIG_HOME/git/attributes (default ~/.config/git/attributes)
# whatever GIT_ATTR_SOURCE says, so point it somewhere with no attributes.
_EMPTY_XDG = tempfile.TemporaryDirectory(prefix="train-verify-xdg-")


def _env() -> dict[str, str]:
    """The pinned environment of every git call, built per call from the current one."""
    return {
        **os.environ,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_ATTR_NOSYSTEM": "1",
        "XDG_CONFIG_HOME": _EMPTY_XDG.name,
        "GIT_TERMINAL_PROMPT": "0",
    }


# Merge attributes (`merge=union`, `-merge`, ...) are PR-controlled and would change the merged
# tree: read attributes from the empty tree, so no .gitattributes anywhere is consulted.
_EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"  # pragma: allowlist secret
_MIN_GIT = (2, 40)  # GIT_ATTR_SOURCE, merge-tree --write-tree
_OPTS = [
    "-c", "core.quotepath=true",
    "-c", "diff.noprefix=false",
    "-c", "diff.mnemonicPrefix=false",
    "-c", "core.autocrlf=false",
]  # fmt: skip
# --ignore-submodules=none: a gitlink (submodule pointer) is compared even when .gitmodules
# says `ignore = all`, otherwise an evil merge could add or bump a pointer unseen.
_DIFF = [
    "diff", "--binary", "--full-index", "--no-ext-diff", "--no-textconv", "--no-renames",
    "--ignore-submodules=none", "--src-prefix=a/", "--dst-prefix=b/",
]  # fmt: skip


class InputError(ValueError):
    """Malformed arguments."""


class GitError(RuntimeError):
    """git failed in a way that is not a yes/no answer."""


def _git(*args: str, timeout: float | None = None) -> bytes:
    """``timeout`` (seconds) is for network calls only; None keeps local calls unbounded."""
    try:
        proc = subprocess.run(
            ["git", *_OPTS, *args], env=_env(), capture_output=True, check=False, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        raise GitError(f"git {args[0]} timed out after {timeout:g}s") from None
    if proc.returncode != 0:
        raise GitError(f"git {args[0]} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def _is_ancestor(a: str, b: str) -> bool:
    proc = subprocess.run(
        ["git", *_OPTS, "merge-base", "--is-ancestor", a, b],
        env=_env(),
        capture_output=True,
        check=False,
    )
    if proc.returncode in (0, 1):
        return proc.returncode == 0
    raise GitError(f"merge-base --is-ancestor failed: {proc.stderr.decode(errors='replace')}")


def _exists(sha: str) -> bool:
    proc = subprocess.run(
        ["git", *_OPTS, "cat-file", "-e", f"{sha}^{{commit}}"],
        env=_env(),
        capture_output=True,
        check=False,
    )
    return proc.returncode == 0


def _diff(a: str, b: str) -> bytes:
    return _git(*_DIFF, a, b)


def _changed_paths(a: str, b: str) -> set[bytes]:
    return set(filter(None, _git("diff", "--name-only", "-z", "--no-renames", a, b).split(b"\0")))


def _raw(a: str, b: str) -> dict[bytes, bytes]:
    """path -> ``:oldmode newmode oldsha newsha status`` for every difference of two trees. Full
    object ids and modes, so equal records mean equal content (a gitlink too). Compared in
    Python, not through a pathspec: ``git diff`` has no --pathspec-from-file, and a path list
    on argv has a length limit and pathspec magic."""
    out = _git(
        "diff", "--raw", "-z", "--no-abbrev", "--no-renames", "--no-ext-diff",
        "--ignore-submodules=none", a, b,
    ).split(b"\0")  # fmt: skip
    if out and out[-1] == b"":
        out.pop()
    if len(out) % 2:
        raise GitError("unexpected git diff --raw output")
    return {out[i + 1]: out[i] for i in range(0, len(out), 2)}


def _check_git_version() -> None:
    out = _git("version").decode()
    m = re.search(r"(\d+)\.(\d+)", out)
    if m is None or (int(m[1]), int(m[2])) < _MIN_GIT:
        raise GitError(f"git >= 2.40 is required, found: {out.strip()}")


def _check_no_info_attributes() -> None:
    """GIT_ATTR_SOURCE does not cover $GIT_DIR/info/attributes; a CI clone has none."""
    path = Path(_git("rev-parse", "--git-path", "info/attributes").decode().strip())
    if path.is_file() and path.read_bytes().strip():
        raise GitError(f"{path} sets attributes that would change the merge; remove it")


def _merged_tree(p: str, s: str, mb: str) -> str | None:
    """The tree of git's own merge of P and S on base MB, or None if it has conflicts.
    Writes tree objects (no refs). Renames off, so the merge is path-wise like _raw."""
    proc = subprocess.run(
        ["git", *_OPTS, "-c", "merge.renames=false", "-c", f"core.attributesFile={os.devnull}",
         "merge-tree", "--write-tree", f"--merge-base={mb}", p, s],
        env={**_env(), "GIT_ATTR_SOURCE": _EMPTY_TREE}, capture_output=True, check=False,
    )  # fmt: skip
    if proc.returncode == 1:
        return None
    tree = proc.stdout.splitlines()[0].decode() if proc.stdout else ""
    if proc.returncode != 0 or not _SHA_RE.fullmatch(tree):
        raise GitError(f"merge-tree failed: {proc.stderr.decode(errors='replace').strip()}")
    return tree


def _halt(reason: str) -> dict[str, object]:
    return {"ok": False, "reason": clean_text(reason, limit=300)}


_MAX_SERIES = 500


def _parents(commit: str) -> list[str]:
    return [x.decode() for x in _git("rev-list", "--parents", "-n", "1", commit).split()[1:]]


def _tree(commit: str) -> str:
    return _git("rev-parse", f"{commit}^{{tree}}").decode().strip()


def _verify_stacked(
    reviewed: str, head: str, main: str, parent_reviewed: str, parent_head: str, parent_merge: str
) -> dict[str, object]:
    """Rule h (ADR 0037): the rebased head of a stacked child whose parent has merged."""
    for sha in (parent_reviewed, parent_head, parent_merge):
        if not _exists(sha):
            return _halt(f"h6: parent commit {sha[:12]} is not in this repository")
    merges: list[str] = []
    t = head
    while len(parents := _parents(t)) == 2:
        merges.append(t)
        t = parents[0]
        if len(merges) > _MAX_SERIES:
            return _halt("h1: too many update merges above the rebased series")
    if len(parents) != 1:
        return _halt("h1: the head chain has a root commit or an octopus merge")
    # Commits of T not reachable from main, with their parents; the walk below follows the
    # first parent only, so a merge in the series is seen and halts.
    unmerged = {
        (f := line.split())[0]: f[1:]
        for line in _git("rev-list", "--parents", f"{main}..{t}").decode().splitlines()
    }
    series: list[str] = []
    x = t
    while x in unmerged:
        if len(unmerged[x]) != 1:
            return _halt(f"h1: series commit {x[:12]} is a merge or a root commit")
        series.append(x)
        if len(series) > _MAX_SERIES:
            return _halt("h1: the series has more than 500 commits")
        x = unmerged[x][0]
    if not series:
        return _halt("h1: the series is empty: the head is already on main")
    if not _is_ancestor(x, main):
        return _halt("h1: the parent of the series is not on main")
    if reviewed == parent_reviewed or not _is_ancestor(parent_reviewed, reviewed):
        return _halt("h2: R_parent is not a proper ancestor of the reviewed commit")
    merged = _merged_tree(x, reviewed, parent_reviewed)
    if merged is None:
        return _halt("h4: replaying the reviewed change onto main conflicts")
    if _tree(t) != merged:
        return _halt("h3: the rebased series is not main plus the reviewed change")
    if merges:
        sub = verify(t, head, main)
        if not sub["ok"]:
            return _halt(f"h5: update merge above the series: {sub['reason']}")
    if _tree(parent_merge) != _tree(parent_head):
        return _halt("h6: the parent's merge commit tree differs from its final head")
    sub = verify(parent_reviewed, parent_head, main)
    if not sub["ok"]:
        return _halt(f"h6: the parent's final head is not its reviewed commit: {sub['reason']}")
    return {"ok": True}


def verify(
    reviewed: str,
    head: str,
    main: str = "origin/main",
    parent_reviewed: str | None = None,
    parent_head: str | None = None,
    parent_merge: str | None = None,
) -> dict[str, object]:
    if not _SHA_RE.fullmatch(reviewed) or not _SHA_RE.fullmatch(head):
        raise InputError("both shas must be 40 lowercase hex")
    parent = (parent_reviewed, parent_head, parent_merge)
    if any(p is not None for p in parent) and not all(
        p is not None and _SHA_RE.fullmatch(p) for p in parent
    ):
        raise InputError("the three parent arguments go together and must be 40 lowercase hex")
    _check_git_version()
    _check_no_info_attributes()
    if not _exists(reviewed):
        return _halt("the reviewed commit is not in this repository")
    if not _exists(head):
        raise GitError("the head commit is not in this repository; the fetch failed")
    if parent_reviewed is not None and parent_head is not None and parent_merge is not None:
        return _verify_stacked(reviewed, head, main, parent_reviewed, parent_head, parent_merge)
    if reviewed == head:
        return {"ok": True}
    if not _is_ancestor(reviewed, head):
        return _halt("the reviewed commit is not an ancestor of the head: history was rewritten")
    chain = _git("rev-list", "--first-parent", head).split()
    if reviewed.encode() not in chain:
        return _halt("the reviewed commit is not on the head's first-parent chain")
    commits = _git("rev-list", "--first-parent", "--reverse", f"{reviewed}..{head}").split()
    for raw in commits:
        m = raw.decode()
        parents = _git("rev-list", "--parents", "-n", "1", m).split()[1:]
        if len(parents) != 2:
            return _halt(
                f"commit {m[:12]} is not a two-parent merge: an unreviewed change or an octopus"
            )
        p, s = (x.decode() for x in parents)
        if not _is_ancestor(s, main):
            return _halt(f"commit {m[:12]} merges {s[:12]}, which is not on main")
        mb = _git("merge-base", p, s).decode().strip()
        merged = _merged_tree(p, s, mb)
        # Rule g: M is exactly git's own clean merge of P and S. Only with one merge base:
        # with several, the real merge uses a virtual base that --merge-base cannot name.
        if (
            merged is not None
            and len(_git("merge-base", "--all", p, s).split()) == 1
            and _git("rev-parse", f"{m}^{{tree}}").decode().strip() == merged
        ):
            continue
        main_raw = _raw(mb, s)
        # Rule f: paths main changed whose change P already holds.
        absorbed = set(main_raw) - set(_raw(p, merged or s))
        if absorbed:
            remaining = {k: v for k, v in main_raw.items() if k not in absorbed}
            if _raw(p, m) == remaining:
                continue
            overlap = _changed_paths(mb, p) & set(remaining)
        else:
            if _diff(p, m) == _diff(mb, s):
                continue
            overlap = _changed_paths(mb, p) & _changed_paths(mb, s)
        if overlap:
            return _halt("update overlaps the PR's own edits: needs a fresh review")
        return _halt(
            f"merge {m[:12]} adds changes that main does not contain or omits main's change"
        )
    return {"ok": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reviewed")
    parser.add_argument("head")
    parser.add_argument("--main", default="origin/main")
    parser.add_argument("--parent-reviewed")
    parser.add_argument("--parent-head")
    parser.add_argument("--parent-merge")
    args = parser.parse_args(argv)
    try:
        result = verify(
            args.reviewed, args.head, args.main,
            args.parent_reviewed, args.parent_head, args.parent_merge,
        )  # fmt: skip
    except (InputError, GitError) as exc:
        print(f"train_verify_head: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
