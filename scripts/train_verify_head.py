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
  c. S is an ancestor of main: a merge of any other branch is refused;
  d. the merge adds only main's own change: ``git diff P M`` is byte-identical to
     ``git diff MB S`` (MB = merge-base of P and S), both with --binary --full-index;
  e. if the PR and main edited the same file the two diffs differ; that is reported as an
     overlap and needs a fresh review (fail closed).
  f. stacked PRs: a path that main changed (MB..S) but whose change P already holds is
     ABSORBED, e.g. the parent PR's own commits after the parent was squash-merged. "Holds"
     means git's own path-wise merge of P and S (merge-tree, renames off, MB pinned) yields
     P's entry there: P equals S, or S's hunks are all already in P (the child also edits the
     parent's file). Rule d then applies to the REMAINING paths only: the raw diff of P
     against M must be exactly main's raw diff restricted to them (so M equals P on every
     absorbed path and equals S on the rest). Rule e is checked on the remaining paths. If
     that merge conflicts, only paths where P equals S are absorbed. With no absorbed path,
     d and e are unchanged.

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
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_step import clean_text

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}
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


def _git(*args: str) -> bytes:
    proc = subprocess.run(["git", *_OPTS, *args], env=_ENV, capture_output=True, check=False)
    if proc.returncode != 0:
        raise GitError(f"git {args[0]} failed: {proc.stderr.decode(errors='replace').strip()}")
    return proc.stdout


def _is_ancestor(a: str, b: str) -> bool:
    proc = subprocess.run(
        ["git", *_OPTS, "merge-base", "--is-ancestor", a, b],
        env=_ENV,
        capture_output=True,
        check=False,
    )
    if proc.returncode in (0, 1):
        return proc.returncode == 0
    raise GitError(f"merge-base --is-ancestor failed: {proc.stderr.decode(errors='replace')}")


def _exists(sha: str) -> bool:
    proc = subprocess.run(
        ["git", *_OPTS, "cat-file", "-e", f"{sha}^{{commit}}"],
        env=_ENV,
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


def _merged_tree(p: str, s: str, mb: str) -> str | None:
    """The tree of git's own merge of P and S on base MB, or None if it has conflicts.
    Writes tree objects (no refs). Renames off, so the merge is path-wise like _raw."""
    proc = subprocess.run(
        ["git", *_OPTS, "-c", "merge.renames=false", "merge-tree", "--write-tree",
         f"--merge-base={mb}", p, s],
        env=_ENV, capture_output=True, check=False,
    )  # fmt: skip
    if proc.returncode == 1:
        return None
    tree = proc.stdout.splitlines()[0].decode() if proc.stdout else ""
    if proc.returncode != 0 or not _SHA_RE.fullmatch(tree):
        raise GitError(f"merge-tree failed: {proc.stderr.decode(errors='replace').strip()}")
    return tree


def _halt(reason: str) -> dict[str, object]:
    return {"ok": False, "reason": clean_text(reason, limit=300)}


def verify(reviewed: str, head: str, main: str = "origin/main") -> dict[str, object]:
    if not _SHA_RE.fullmatch(reviewed) or not _SHA_RE.fullmatch(head):
        raise InputError("both shas must be 40 lowercase hex")
    if not _exists(reviewed):
        return _halt("the reviewed commit is not in this repository")
    if not _exists(head):
        raise GitError("the head commit is not in this repository; the fetch failed")
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
        main_raw = _raw(mb, s)
        # Rule f: paths main changed whose change P already holds.
        merged = _merged_tree(p, s, mb)
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
    args = parser.parse_args(argv)
    try:
        result = verify(args.reviewed, args.head, args.main)
    except (InputError, GitError) as exc:
        print(f"train_verify_head: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main())
