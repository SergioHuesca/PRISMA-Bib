"""Assemble the train head's facts record from GitHub REST (ADR 0037, Decision 3).

Usage: ``train_facts.py --repo OWNER/REPO --pr N --out FILE --shas-out FILE [--main origin/main]
[--now 2026-10-27T01:30:00Z]``. ``--now`` is for tests; the default is the clock.

``--out`` receives the record that ``train_step.py decide --facts`` parses::

    {"pr", "now", "stack": null | {id, number, position, size},
     "parent": null | {number, merged, merged_at, merge_commit_sha, in_main, reviewed,
                       head_sha, state}}

``--shas-out`` receives the commits the workflow must fetch BY SHA as data (never checked out):
one 40-hex per line, deduplicated: the head's reviewed commit R_child, and for a stacked head at
position 2 or higher with a merged parent also R_parent (its single valid ``reviewed:`` label),
P_final (the parent's final head) and M_parent (its merge commit). The workflow adds the
head sha itself. The file is empty when nothing is needed.

Calls (one injectable runner, the ``gh api --include`` runner of train_merge_async.py):
``GET /pulls/{head}`` (``stack`` absent or null: not stacked), ``GET /stacks/{number}`` (the
entry before the head's is the parent; the head's own entry is checked against ``position``),
``GET /pulls/{parent}``. ``in_main`` is ``git merge-base --is-ancestor <merge_commit_sha>
<main>`` after ONE re-fetch of main when it is false. Anything unparseable exits 2: the step
fails, nothing is merged. The token is read by ``gh`` from GH_TOKEN and is never printed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_merge_async import _OWNER_RE, _REPO_RE, Runner, _safe, gh_runner
from train_step import _REVIEWED_RE, _SHA_RE, _TS_RE, InputError
from train_verify_head import _env

# argv after "git" -> exit code. Injectable; the default runs git in the pinned environment.
GitRunner = Callable[[list[str]], int]


def git_runner(args: list[str]) -> int:
    proc = subprocess.run(["git", *args], env=_env(), capture_output=True, check=False, timeout=300)
    return proc.returncode


def _get(runner: Runner, path: str, what: str) -> dict[str, Any]:
    try:
        status, data = runner("GET", path, None)
    except Exception as exc:  # noqa: BLE001  any runner failure is an input failure, scrubbed
        raise InputError(f"{what}: request failed: {_safe(exc)}") from None
    if status != 200 or not isinstance(data, dict):
        raise InputError(f"{what}: HTTP {status} or not a JSON object")
    return data


def _pos_int(value: Any, what: str) -> int:
    if type(value) is not int or value < 1:
        raise InputError(f"{what} must be a positive integer")
    return value


def _label_names(pr: dict[str, Any], what: str) -> list[str]:
    labels = pr.get("labels")
    if not isinstance(labels, list) or not all(
        isinstance(x, dict) and isinstance(x.get("name"), str) for x in labels
    ):
        raise InputError(f"{what}: labels must be a list of {{name}} objects")
    return [x["name"] for x in labels]


def _head_sha(pr: dict[str, Any], what: str) -> str:
    head = pr.get("head")
    sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
        raise InputError(f"{what}: head.sha must be 40 lowercase hex")
    return sha


def _single_valid_review(names: list[str]) -> str | None:
    """The 40-hex of the one valid ``reviewed:`` label, else None."""
    found = [n for n in names if n.startswith("reviewed:")]
    if len(found) == 1 and _REVIEWED_RE.fullmatch(found[0]):
        return found[0][len("reviewed:") :]
    return None


def _stack(pr: dict[str, Any]) -> dict[str, int] | None:
    raw = pr.get("stack")
    if raw is None:  # absent and null alike: not stacked
        return None
    if not isinstance(raw, dict):
        raise InputError("stack must be an object or null")
    stack = {k: _pos_int(raw.get(k), f"stack.{k}") for k in ("id", "number", "position", "size")}
    if stack["position"] > stack["size"]:
        raise InputError("stack.position is above stack.size")
    return stack


def _parent_number(runner: Runner, repo: str, pr: int, stack: dict[str, int]) -> int:
    rec = _get(runner, f"/repos/{repo}/stacks/{stack['number']}", "stack record")
    entries = rec.get("pull_requests")
    if not isinstance(entries, list):
        raise InputError("stack record: pull_requests must be a list")
    numbers = [e.get("number") if isinstance(e, dict) else None for e in entries]
    if numbers.count(pr) != 1 or numbers.index(pr) + 1 != stack["position"]:
        raise InputError("stack record: the head's entry is missing or not at stack.position")
    parent = numbers[stack["position"] - 2]
    return _pos_int(parent, "stack record: the parent's number")


def build(
    repo: str,
    pr: int,
    *,
    runner: Runner = gh_runner,
    git: GitRunner = git_runner,
    main_ref: str = "origin/main",
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> tuple[dict[str, Any], list[str]]:
    """The facts record and the shas to fetch. Raises InputError on anything unparseable."""
    owner, _, name = repo.partition("/")
    if not (_OWNER_RE.fullmatch(owner) and _REPO_RE.fullmatch(name) and name not in (".", "..")):
        raise InputError("bad owner/repo")
    _pos_int(pr, "pr")
    stamp = now().astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    head = _get(runner, f"/repos/{repo}/pulls/{pr}", f"PR #{pr}")
    r_child = _single_valid_review(_label_names(head, f"PR #{pr}"))
    _head_sha(head, f"PR #{pr}")
    shas = [r_child] if r_child else []
    stack = _stack(head)
    record: dict[str, Any] = {"pr": pr, "now": stamp, "stack": stack, "parent": None}
    if stack is None or stack["position"] == 1:
        return record, shas
    pn = _parent_number(runner, repo, pr, stack)
    if pn == pr:
        raise InputError("stack record: the parent is the head itself")
    par = _get(runner, f"/repos/{repo}/pulls/{pn}", f"parent #{pn}")
    if par.get("number") != pn:
        raise InputError(f"parent #{pn}: the response is for another PR")
    merged = par.get("merged")
    state = par.get("state")
    if type(merged) is not bool or state not in ("open", "closed"):
        raise InputError(f"parent #{pn}: merged must be a boolean and state open or closed")
    names = _label_names(par, f"parent #{pn}")
    p_final = _head_sha(par, f"parent #{pn}")
    merged_at: str | None = None
    m_parent: str | None = None
    in_main = False
    if merged:
        merged_at, m_parent = par.get("merged_at"), par.get("merge_commit_sha")
        if not isinstance(merged_at, str) or not _TS_RE.fullmatch(merged_at):
            raise InputError(f"parent #{pn}: merged_at must be a UTC timestamp")
        if not isinstance(m_parent, str) or not _SHA_RE.fullmatch(m_parent):
            raise InputError(f"parent #{pn}: merge_commit_sha must be 40 lowercase hex")
        probe = ["merge-base", "--is-ancestor", m_parent, main_ref]
        in_main = git(probe) == 0
        if not in_main:  # one re-fetch: the parent may have merged after our fetch of main
            if git(["fetch", "--no-tags", "--quiet", "origin", "main"]) != 0:
                print("train_facts: re-fetch of main failed; in_main stays false", file=sys.stderr)
            else:
                in_main = git(probe) == 0
        r_parent = _single_valid_review(names)
        shas += [s for s in (r_parent, p_final, m_parent) if s]
    record["parent"] = {
        "number": pn,
        "merged": merged,
        "merged_at": merged_at,
        "merge_commit_sha": m_parent,
        "in_main": in_main,
        "reviewed": [n for n in names if n.startswith("reviewed:")],
        "head_sha": p_final,
        "state": state.upper(),
    }
    return record, list(dict.fromkeys(shas))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--out", required=True, metavar="FILE")
    parser.add_argument("--shas-out", required=True, metavar="FILE")
    parser.add_argument("--main", default="origin/main")
    parser.add_argument("--now", help="UTC timestamp like 2026-10-27T01:30:00Z (tests)")
    args = parser.parse_args(argv)
    clock = lambda: datetime.now(UTC)  # noqa: E731
    try:
        if args.now is not None:
            if not _TS_RE.fullmatch(args.now):
                raise InputError("--now must be a UTC timestamp like 2026-10-27T01:30:00Z")
            fixed = datetime.fromisoformat(args.now[:-1]).replace(tzinfo=UTC)
            clock = lambda: fixed  # noqa: E731
        record, shas = build(args.repo, args.pr, main_ref=args.main, now=clock)
    except (InputError, ValueError) as exc:
        print(f"train_facts: {_safe(exc)}", file=sys.stderr)
        return 2
    Path(args.out).write_text(json.dumps(record), encoding="utf-8", newline="\n")
    Path(args.shas_out).write_text("".join(s + "\n" for s in shas), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
