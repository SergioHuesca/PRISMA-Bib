"""Merge a stacked head with GitHub's asynchronous merge API, then audit it (ADR 0037, Decision 4).

Usage: ``train_merge_async.py --repo OWNER/REPO --pr N --head SHA [--audit-file FILE]``.
SHA is the VERIFIED head H, 40 lowercase hex; it is always sent as ``sha``. Prints
``{"outcome": "merged"|"wait"|"halt", "reason": "...", "audit": {"ok": bool, "reason": "..."}|null}``
and exits 0 for all three outcomes (the workflow decides from the JSON), 2 on malformed
arguments. When an audit ran, ``--audit-file`` receives ``{merged_pr, failed, reason}``, the
input of ``train_step.py audit-stop``. The token is read by ``gh`` from ``GH_TOKEN``; this
script never prints it, and every message is scrubbed of it and cleaned.

Steps: 1. read the PR (merged: audit; ``head.sha`` != H: wait). 2. ``PUT merge-async`` with
``merge_method=squash``, ``sha=H``. 3. Only a 202 whose ``details.expected_head_sha`` equals H
and that carries a uuid is polled. 4. Poll every 3 s up to 180 s; on timeout re-read the PR after
60 s more. 5. Audit: S = ``merge_commit_sha`` passes iff it has exactly one parent, S != H,
``tree(S) == tree(H)`` and its parent is an ancestor of H and not H itself.

Deviation from ADR step 7 (evidence from the rehearsal): a repeated call during a flight returns
409 with the existing uuid but NO ``expected_head_sha``, so a job made by someone else, or
without ``sha``, cannot be shown to be pinned to H before the irreversible merge. A 409 is
therefore a WAIT: not a halt (a sticky halt for a harmless repeat), and never an adoption. The next run
re-reads the PR: merged -> audit against OUR H; open with the same head -> wait again.
The server rejects a non-current ``sha`` with 400 ("Pull request head branch was modified.")
and fails (does not wait for) a pending required check.

Behaviour (status -> outcome):
  PR merged at step 1                    -> audit: merged, or halt on audit failure
  PR head.sha != H                       -> wait
  PUT 202, expected_head_sha == H, uuid  -> poll
  PUT 409 (any body)                     -> wait, no poll
  PUT anything else, 202 with another or no expected_head_sha, bad uuid -> halt
  poll non-2xx, failed, unknown (case-sensitive) status -> halt
  poll pending/enqueued                  -> keep polling (3 s, up to 180 s)
  poll merged, PR re-read merged is True -> audit; re-read not merged -> halt
  timeout, re-read after 60 s: merged -> audit, else halt
  audit failure                          -> halt with audit.ok false
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from train_step import InputError, clean_text
from train_verify_head import GitError, _exists, _git, _is_ancestor

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)
_OWNER_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})")
_REPO_RE = re.compile(r"[A-Za-z0-9._-]{1,100}")

# (method, path, JSON body or None) -> (HTTP status, parsed JSON or None)
Runner = Callable[[str, str, "dict[str, Any] | None"], "tuple[int, Any]"]


class _Halt(Exception):
    """Stop with this (already cleaned) reason."""


def _safe(text: object, limit: int = 200) -> str:
    """Cleaned, bounded, and with the token (if set) removed."""
    out = str(text)
    token = os.environ.get("GH_TOKEN")
    if token:
        out = out.replace(token, "***")
    return clean_text(out, limit)


def gh_runner(method: str, path: str, body: dict[str, Any] | None) -> tuple[int, Any]:
    """``gh api --include``: argument list, no shell; gh reads the token from GH_TOKEN."""
    cmd = ["gh", "api", "--include", "-X", method, path]
    if body is not None:
        cmd += ["--input", "-"]
    proc = subprocess.run(
        cmd,
        input=None if body is None else json.dumps(body).encode(),
        capture_output=True,
        check=False,
        timeout=60,
    )
    head, _, payload = proc.stdout.decode(errors="replace").replace("\r\n", "\n").partition("\n\n")
    m = re.match(r"HTTP/\S+ (\d{3})", head)
    if m is None:
        raise _Halt("gh api gave no HTTP response")
    try:
        data = json.loads(payload)
    except ValueError:
        data = None
    return int(m.group(1)), data


def _message(data: Any) -> str:
    details = data.get("details") if isinstance(data, dict) else None
    msg = details.get("message") if isinstance(details, dict) else None
    return _safe(msg) if isinstance(msg, str) else ""


def _call(
    runner: Runner, method: str, path: str, body: dict[str, Any] | None = None
) -> tuple[int, Any]:
    try:
        return runner(method, path, body)
    except _Halt:
        raise
    except Exception as exc:  # noqa: BLE001  a runner failure must halt, with a scrubbed reason
        raise _Halt(f"{method} request failed: {_safe(exc)}") from None


def audit(merge_sha: Any, head: str) -> dict[str, Any]:
    """The post-merge audit (Decision 4, step 5). Never raises on a git problem."""
    if not isinstance(merge_sha, str) or not _SHA_RE.fullmatch(merge_sha):
        return _bad("merge_commit_sha missing or malformed")
    if merge_sha == head:
        return _bad("S is H itself, not a squash of H")
    try:
        if not _exists(merge_sha):
            return _bad("the merge commit is not present locally")
        if not _exists(head):
            return _bad("the verified head is not present locally")
        parents = _git("rev-list", "--parents", "-n", "1", merge_sha).decode().split()[1:]
        if len(parents) != 1:
            return _bad(f"merge commit has {len(parents)} parents, not 1")
        if parents[0] == head:
            return _bad("the parent of S is H itself, not a squash of H")
        if _git("rev-parse", f"{merge_sha}^{{tree}}") != _git("rev-parse", f"{head}^{{tree}}"):
            return _bad("tree(S) differs from tree(H)")
        if not _is_ancestor(parents[0], head):
            return _bad("the parent of S is not an ancestor of H")
    except (GitError, OSError) as exc:
        return _bad(f"git failed: {_safe(exc)}")
    return {"ok": True, "reason": "tree(S) == tree(H), one parent, parent is an ancestor of H"}


def _bad(reason: str) -> dict[str, Any]:
    return {"ok": False, "reason": f"audit: {reason}"}


def _validate(repo: str, pr: int, head: object) -> None:
    owner, _, name = repo.partition("/")
    if not (_OWNER_RE.fullmatch(owner) and _REPO_RE.fullmatch(name) and name not in (".", "..")):
        raise InputError("bad owner/repo")
    if type(pr) is not int or pr < 1:
        raise InputError("bad PR number")
    if not isinstance(head, str) or not _SHA_RE.fullmatch(head):
        raise InputError("the head sha must be 40 lowercase hex")


def _get_pr(runner: Runner, path: str) -> dict[str, Any]:
    status, data = _call(runner, "GET", path)
    if status != 200 or not isinstance(data, dict):
        raise _Halt(f"reading the PR failed (HTTP {status})")
    return data


def _finish(
    pr_data: dict[str, Any], head: str, audit_fn: Callable[[Any, str], dict[str, Any]]
) -> dict[str, Any]:
    result = audit_fn(pr_data.get("merge_commit_sha"), head)
    reason = result["reason"]
    return {"outcome": "merged" if result["ok"] else "halt", "reason": reason, "audit": result}


def run(
    repo: str,
    pr: int,
    head: str,
    *,
    runner: Runner = gh_runner,
    audit_fn: Callable[[Any, str], dict[str, Any]] = audit,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
    poll_s: float = 3,
    timeout_s: float = 180,
    grace_s: float = 60,
) -> dict[str, Any]:
    """Merge PR ``pr`` whose verified head is ``head``; returns the outcome record."""
    _validate(repo, pr, head)
    try:
        return _run(repo, pr, head, runner, audit_fn, sleep, clock, poll_s, timeout_s, grace_s)
    except _Halt as halt:
        return {"outcome": "halt", "reason": _safe(halt), "audit": None}


def _run(repo, pr, head, runner, audit_fn, sleep, clock, poll_s, timeout_s, grace_s):  # type: ignore[no-untyped-def]
    path = f"/repos/{repo}/pulls/{pr}"
    data = _get_pr(runner, path)
    if data.get("merged") is True:  # idempotent: audit against OUR head
        return _finish(data, head, audit_fn)
    current = data.get("head")
    if not isinstance(current, dict) or current.get("sha") != head:
        return {
            "outcome": "wait",
            "reason": "the head changed since it was verified",
            "audit": None,
        }

    status, body = _call(
        runner, "PUT", f"{path}/merge-async", {"merge_method": "squash", "sha": head}
    )
    if status == 409:  # never adopted: its pin to H cannot be verified (see the docstring)
        return {
            "outcome": "wait",
            "reason": "a merge request already exists for this PR; re-checked on the next run",
            "audit": None,
        }
    details = body.get("details") if isinstance(body, dict) else None
    details = details if isinstance(details, dict) else {}
    uuid = details.get("uuid")
    if status != 202 or not isinstance(uuid, str) or not _UUID_RE.fullmatch(uuid):
        raise _Halt(f"merge-async refused (HTTP {status}): {_message(body)}")
    if details.get("expected_head_sha") != head:
        raise _Halt("merge-async accepted a job not pinned to the verified head")

    poll = f"{path}/merge-async/{uuid}"
    start = clock()
    while True:
        status, body = _call(runner, "GET", poll)
        state = body.get("status") if isinstance(body, dict) else None
        if not 200 <= status < 300:
            raise _Halt(f"poll failed (HTTP {status})")
        if state == "merged":
            data = _get_pr(runner, path)
            if data.get("merged") is not True:
                raise _Halt("the poll said merged but the PR is not merged")
            return _finish(data, head, audit_fn)
        if state == "failed":
            raise _Halt(f"merge failed: {_message(body)}")
        if state not in ("pending", "enqueued"):
            raise _Halt(f"unknown poll status: {_safe(state, 40)}")
        if clock() - start >= timeout_s:
            break
        sleep(poll_s)

    sleep(grace_s)
    data = _get_pr(runner, path)
    if data.get("merged") is True:
        return _finish(data, head, audit_fn)
    raise _Halt("merge still not done after the timeout and the re-read")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--audit-file", metavar="FILE")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--grace", type=float, default=60)
    args = parser.parse_args(argv)
    try:
        result = run(args.repo, args.pr, args.head, timeout_s=args.timeout, grace_s=args.grace)
    except InputError as exc:
        print(f"train_merge_async: {_safe(exc)}", file=sys.stderr)
        return 2
    print(json.dumps(result), flush=True)
    if args.audit_file and result["audit"] is not None:
        record = {
            "merged_pr": args.pr,
            "failed": not result["audit"]["ok"],
            "reason": result["audit"]["reason"],
        }
        try:
            Path(args.audit_file).write_text(json.dumps(record), encoding="utf-8", newline="\n")
        except OSError as exc:
            print(f"train_merge_async: cannot write the audit file: {_safe(exc)}", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
