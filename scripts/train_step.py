"""Decide the one action of update-behind-prs.yml: the merge train, or update-only mode.

Pure logic: no GitHub or git calls. The workflow feeds it the output of
``gh pr list --json number,state,isDraft,mergeStateStatus,headRefOid,labels,statusCheckRollup``
and executes the action it returns. Subcommands:

``candidate --merge-train on|off``
    Prints ``<pr> <head sha> <reviewed sha>`` for the train head when its single
    ``reviewed:<40 hex>`` label is valid. Prints nothing otherwise.

``decide --merge-train on|off [--closed FILE] [--head-pr N --head-check FILE]
[--facts FILE] [--merge-train-stacks on|off]``
    FILE for --closed is ``gh pr list --state closed --search "is:unmerged"`` output; without
    it a train halts. FILE for --head-check is the JSON of train_verify_head.py for PR N;
    without it the head halts. Prints one JSON object
    ``{"action", "pr", "head_sha", "reason", "mode"}``. action is one of none, update, merge,
    halt, wait; mode is train, update or none. A wait never merges and never updates the
    branch: the workflow does nothing.

    The lists are NOT filtered by base (ADR 0037 Decision 3): each entry may carry
    ``baseRefName``, absent meaning ``main``. The train head is the lowest ``train:N`` among
    all open PRs. Only update-only mode keeps "the lowest-numbered open PR targeting main",
    and it filters inside the call to select_next_pr.

    Known cliff, not fixed here (ADR 0037 owner question 4): a closed-unmerged list of
    LIST_LIMIT (100) entries halts the train for good; every PR closed unmerged moves the
    repository closer to it.

    FILE for --facts is the head's facts record (ADR 0037 Decision 3)::

        {"pr": 178, "now": "2026-10-27T01:30:00Z",
         "stack": null | {"id": 1, "number": 4, "position": 2, "size": 3},
         "parent": null | {"number": 177, "merged": true, "merged_at": "...Z",
                           "merge_commit_sha": "40 hex", "in_main": true,
                           "reviewed": ["reviewed:<40 hex>"],
                           "head_sha": "40 hex",        # extension, see below
                           "state": "OPEN"|"CLOSED"}}  # extension, see below

    ``head_sha`` (the parent's final head P_final, which the stacked verifier call needs but
    the ADR schema does not list) and ``state`` (only to word the halt for an unmerged
    parent) are optional extensions. Without ``head_sha`` no verifier call can be built and
    the head follows the pending rule. ``now`` is injected, never read from the clock.
    Without --facts no head is stacked. Anything inconsistent exits 2.

    A decision on the stacked path (position 2, merged parent, valid parent label, ``head_sha``)
    also carries ``"verifier_args": ["--parent-reviewed", R, "--parent-head", P,
    "--parent-merge", M]``, to append to ``train_verify_head.py <R_child> <H>``. The path is
    chosen only by the stack record, never by the shape of the head.

``audit-stop --audit FILE [--prs FILE]``
    FILE for --audit is ``{"merged_pr": N, "failed": bool, "reason": str}``. The freshly
    re-listed open PRs come from --prs or stdin. Prints the number of the lowest OPEN
    ``train:N`` PR (skipping ``merged_pr`` if still listed): the PR that gets ``train:halted``.
    Prints ``none`` when there is no open successor, or when the audit did not fail.

The rules are in the header of .github/workflows/update-behind-prs.yml and in
docs/how-to/merge-train.md. Malformed input exits 2 (ValueError): a bad response must
fail the step, never act on a guess.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

# The update-only rule lives in next_pr_to_update.py. The workflow runs this file from
# scripts/, so the import works there and in the unit tests.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from next_pr_to_update import select_next_pr

OPEN = "OPEN"
HALTED = "train:halted"
# The workflow asks gh for at most this many PRs. A full list may be cut short, so the
# train refuses to decide rather than miss a label.
LIST_LIMIT = 100
# fullmatch, not match: `$` would accept a trailing newline in a label or argument.
_TRAIN_RE = re.compile(r"train:[0-9]{1,3}")
# The reviewed label is the reviewed head commit itself. 9 + 40 = 49 chars, within GitHub's
# 50-char label limit.
_REVIEWED_RE = re.compile(r"reviewed:[0-9a-f]{40}")
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_PASS_CONCLUSIONS = frozenset({"SUCCESS", "SKIPPED", "NEUTRAL"})
_PENDING_STATES = frozenset({"PENDING", "EXPECTED"})
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

PASS, RUNNING, BAD = "pass", "running", "bad"
# A parent merged less than this long ago may still be mid-rebase on GitHub's side.
PENDING_WINDOW = timedelta(minutes=10)
_TS_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")


class InputError(ValueError):
    """The input is not the shape that the workflow produces."""


def clean_text(text: str, limit: int = 60) -> str:
    """Make untrusted text (a check name, set by PR CI) safe for stdout, the step summary and
    a comment: no control characters (CR/LF end a workflow command), no workflow-command
    ``::``, no markdown links or mentions, and a bounded length."""
    text = _CONTROL_RE.sub(" ", text)
    while "::" in text:
        text = text.replace("::", ": :")
    text = text.replace("`", "'").replace("@", "(at)")
    text = text.translate({ord("<"): "(", ord(">"): ")", ord("["): "(", ord("]"): ")"})
    return text[:limit]


def _validate(prs: Any) -> list[dict[str, Any]]:
    if not isinstance(prs, list):
        raise InputError("expected a JSON list of PR records")
    for pr in prs:
        if not isinstance(pr, dict):
            raise InputError("a PR record is not a JSON object")
        number = pr.get("number")
        if type(number) is not int or number < 1:
            raise InputError(f"bad PR number: {number!r}")
        if type(pr.get("isDraft")) is not bool:
            raise InputError(f"#{number}: isDraft must be a boolean")
        if not isinstance(pr.get("mergeStateStatus"), str):
            raise InputError(f"#{number}: mergeStateStatus must be a string")
        sha = pr.get("headRefOid")
        if not isinstance(sha, str) or not _SHA_RE.fullmatch(sha):
            raise InputError(f"#{number}: headRefOid must be 40 lowercase hex")
        labels = pr.get("labels")
        if not isinstance(labels, list) or not all(
            isinstance(label, dict) and isinstance(label.get("name"), str) for label in labels
        ):
            raise InputError(f"#{number}: labels must be a list of {{name}} objects")
        checks = pr.get("statusCheckRollup")
        if checks is not None and (
            not isinstance(checks, list) or not all(isinstance(c, dict) for c in checks)
        ):
            raise InputError(f"#{number}: statusCheckRollup must be a list or null")
        if "state" in pr and not isinstance(pr["state"], str):
            raise InputError(f"#{number}: state must be a string")
        if "baseRefName" in pr and not isinstance(pr["baseRefName"], str):
            raise InputError(f"#{number}: baseRefName must be a string")
    return prs


def _validate_closed(closed: Any) -> list[dict[str, Any]]:
    if not isinstance(closed, list):
        raise InputError("expected a JSON list of closed PR records")
    for pr in closed:
        if not isinstance(pr, dict) or type(pr.get("number")) is not int or pr["number"] < 1:
            raise InputError("a closed PR record has a bad number")
        labels = pr.get("labels")
        if not isinstance(labels, list) or not all(
            isinstance(label, dict) and isinstance(label.get("name"), str) for label in labels
        ):
            raise InputError(f"#{pr['number']}: labels must be a list of {{name}} objects")
        if pr.get("mergedAt") is not None and not isinstance(pr.get("mergedAt"), str):
            raise InputError(f"#{pr['number']}: mergedAt must be a string or null")
        if "baseRefName" in pr and not isinstance(pr["baseRefName"], str):
            raise InputError(f"#{pr['number']}: baseRefName must be a string")
    return closed


def _base(pr: dict[str, Any]) -> str:
    """The base branch of a listed PR; an entry without ``baseRefName`` targets main."""
    return str(pr.get("baseRefName", "main"))


def _names(pr: dict[str, Any]) -> list[str]:
    return [label["name"] for label in pr["labels"]]


def _train_names(pr: dict[str, Any]) -> list[str]:
    """Every label that claims to be a train position, valid or not (case-insensitive)."""
    return [
        name for name in _names(pr) if name.lower().startswith("train:") and name.lower() != HALTED
    ]


def _outcome(check: dict[str, Any]) -> str:
    # A commit status (StatusContext) carries `state`; a check run carries `status`/`conclusion`.
    if check.get("__typename") == "StatusContext" or ("state" in check and "status" not in check):
        if check.get("state") == "SUCCESS":
            return PASS
        return RUNNING if check.get("state") in _PENDING_STATES else BAD
    if (check.get("status") or "COMPLETED") != "COMPLETED":
        return RUNNING
    return PASS if check.get("conclusion") in _PASS_CONCLUSIONS else BAD


def _rollup(pr: dict[str, Any]) -> tuple[str, str]:
    """Return (BAD, name) for the first failing check, else (RUNNING, "") or (PASS, "").

    A failing check outranks a running one: the train stops rather than waits for it.
    """
    outcomes = [
        (_outcome(check), clean_text(str(check.get("name") or check.get("context") or "unnamed")))
        for check in pr["statusCheckRollup"] or []
    ]
    for outcome, name in outcomes:
        if outcome == BAD:
            return BAD, name
    if any(outcome == RUNNING for outcome, _ in outcomes):
        return RUNNING, ""
    return PASS, ""


def _ts(value: Any, what: str) -> datetime:
    if not isinstance(value, str) or not _TS_RE.fullmatch(value):
        raise InputError(f"facts: {what} must be a UTC timestamp like 2026-10-27T01:30:00Z")
    try:
        return datetime.fromisoformat(value[:-1]).replace(tzinfo=UTC)
    except ValueError as exc:
        raise InputError(f"facts: {what} is not a real timestamp") from exc


def _int(value: Any, what: str) -> int:
    if type(value) is not int or value < 1:
        raise InputError(f"facts: {what} must be a positive integer")
    return value


def _opt_sha(value: Any, what: str) -> str | None:
    if value is not None and (not isinstance(value, str) or not _SHA_RE.fullmatch(value)):
        raise InputError(f"facts: {what} must be 40 lowercase hex or null")
    return value


def _parse_facts(raw: Any) -> dict[str, Any]:
    """Validate the head's facts record strictly. Anything off raises InputError."""
    if not isinstance(raw, dict):
        raise InputError("facts: expected a JSON object")
    pr = _int(raw.get("pr"), "pr")
    now = _ts(raw.get("now"), "now")
    stack = raw.get("stack")
    parent = raw.get("parent")
    if stack is None:
        if parent is not None:
            raise InputError("facts: parent must be null when stack is null")
        return {"pr": pr, "now": now, "stack": None, "parent": None}
    if not isinstance(stack, dict):
        raise InputError("facts: stack must be an object or null")
    position = _int(stack.get("position"), "stack.position")
    if position > _int(stack.get("size"), "stack.size"):
        raise InputError("facts: stack.position is above stack.size")
    _int(stack.get("id"), "stack.id")
    _int(stack.get("number"), "stack.number")
    if position == 1:
        if parent is not None:
            raise InputError("facts: parent must be null at stack position 1")
        return {"pr": pr, "now": now, "stack": stack, "parent": None}
    if not isinstance(parent, dict):
        raise InputError("facts: parent is required at stack position 2 or higher")
    if _int(parent.get("number"), "parent.number") == pr:
        raise InputError("facts: the parent is the head itself")
    if type(parent.get("merged")) is not bool or type(parent.get("in_main")) is not bool:
        raise InputError("facts: parent.merged and parent.in_main must be booleans")
    reviewed = parent.get("reviewed")
    if not isinstance(reviewed, list) or not all(isinstance(x, str) for x in reviewed):
        raise InputError("facts: parent.reviewed must be a list of strings")
    merge_sha = _opt_sha(parent.get("merge_commit_sha"), "parent.merge_commit_sha")
    head_sha = _opt_sha(parent.get("head_sha"), "parent.head_sha")
    merged_at = (
        None if parent.get("merged_at") is None else _ts(parent["merged_at"], "parent.merged_at")
    )
    state = parent.get("state")
    if state is not None and state not in ("OPEN", "CLOSED"):
        raise InputError("facts: parent.state must be OPEN, CLOSED or absent")
    if parent["merged"] and (merged_at is None or merge_sha is None):
        raise InputError("facts: a merged parent needs merged_at and merge_commit_sha")
    return {
        "pr": pr,
        "now": now,
        "stack": stack,
        "parent": {
            "number": parent["number"],
            "merged": parent["merged"],
            "merged_at": merged_at,
            "merge_commit_sha": merge_sha,
            "in_main": parent["in_main"],
            "reviewed": reviewed,
            "head_sha": head_sha,
            "state": state,
        },
    }


def _out(action: str, pr: dict[str, Any] | None, reason: str, mode: str) -> dict[str, Any]:
    return {
        "action": action,
        "pr": pr["number"] if pr else None,
        "head_sha": pr["headRefOid"] if pr else None,
        "reason": reason,
        "mode": mode,
    }


def _update_only(open_prs: list[dict[str, Any]]) -> dict[str, Any]:
    # The one base filter left (ADR 0037 Decision 3): update-only mode targets main.
    number = select_next_pr([pr for pr in open_prs if _base(pr) == "main"])
    if number is None:
        return _out(
            "none",
            None,
            "nothing to update: the lowest-numbered open PR is not BEHIND, or its checks are running",
            "update",
        )
    pr = next(p for p in open_prs if p["number"] == number)
    return _out(
        "update", pr, f"#{number} is the lowest-numbered open PR and BEHIND; updating", "update"
    )


def _head(
    pr: dict[str, Any],
    head_check: dict[str, Any] | None,
    facts: dict[str, Any] | None = None,
    stacks_on: bool = False,
) -> dict[str, Any]:
    """Apply rules 2 and 4 to the train head. Every branch that is not an explicit merge,
    update or wait is a halt. ``facts`` is the parsed record (None: not stacked).

    Order: draft, label, stack path and pending wait, verifier result, checks."""
    n = pr["number"]
    extra: dict[str, Any] = {}

    def halt(reason: str) -> dict[str, Any]:
        return {**_out("halt", pr, f"#{n}: {reason}", "train"), **extra}

    def act(action: str, reason: str) -> dict[str, Any]:
        return {**_out(action, pr, f"#{n}: {reason}", "train"), **extra}

    if pr["isDraft"]:
        return halt("the head is a draft; batch PRs must be marked ready before the train starts")
    reviewed = [name for name in _names(pr) if name.startswith("reviewed:")]
    if not reviewed:
        return halt("missing reviewed:<40 hex> label")
    if len(reviewed) > 1 or not _REVIEWED_RE.fullmatch(reviewed[0]):
        return halt("reviewed: label must be exactly one reviewed:<40 lowercase hex> label")
    stack = facts["stack"] if facts is not None else None
    if stack is None:
        if _base(pr) != "main":
            return halt(f"not stacked, but its base is {clean_text(_base(pr))}, not main")
    elif not stacks_on:
        return halt("a stacked head, and MERGE_TRAIN_STACKS is not on")
    elif stack["position"] == 1:
        if _base(pr) != "main":
            return halt(f"stack position 1 but its base is {clean_text(_base(pr))}, not main")
    else:
        assert facts is not None
        parent = facts["parent"]
        if not parent["merged"]:
            where = {"OPEN": "is still open", "CLOSED": "was closed unmerged"}.get(
                parent["state"], "is not merged (open, or closed unmerged)"
            )
            return halt(f"the parent #{parent['number']} {where}")
        if stack["position"] > 2:
            return halt(f"three-level stacks are not enabled (stack position {stack['position']})")
        labels = [x for x in parent["reviewed"] if x.startswith("reviewed:")]
        valid = len(labels) == 1 and _REVIEWED_RE.fullmatch(labels[0]) is not None
        if valid and parent["head_sha"] is not None:
            extra["verifier_args"] = [
                "--parent-reviewed",
                labels[0][len("reviewed:") :],
                "--parent-head",
                parent["head_sha"],
                "--parent-merge",
                parent["merge_commit_sha"],
            ]
        problem = None
        if not valid:
            problem = f"the parent #{parent['number']} has no single valid reviewed:<40 hex> label"
        elif parent["head_sha"] is None:
            problem = "the parent's final head is unknown; no verifier call can be built"
        elif _base(pr) != "main":
            problem = f"the base is {clean_text(_base(pr))}, not main"
        elif not parent["in_main"]:
            problem = "the parent's merge commit is not in origin/main yet"
        elif head_check is None or head_check.get("pr") != n:
            problem = "no head verification for this PR: the verify step did not run"
        elif head_check.get("ok") is not True:
            reason = clean_text(str(head_check.get("reason") or "no reason given"), limit=200)
            problem = f"head verification failed: {reason}"
        if problem is not None:
            if facts["now"] - parent["merged_at"] < PENDING_WINDOW:
                return act("wait", f"pending, the parent merged under 10 minutes ago: {problem}")
            return halt(problem)
    if stack is None or stack["position"] == 1:
        if head_check is None or head_check.get("pr") != n:
            return halt("no head verification for this PR: the verify step did not run")
        if head_check.get("ok") is not True:
            reason = clean_text(str(head_check.get("reason") or "no reason given"), limit=200)
            return halt(f"head verification failed: {reason}")
    outcome, failed = _rollup(pr)
    if outcome == BAD:
        return halt(f"check '{failed}' is not passing")
    running = outcome == RUNNING
    state = pr["mergeStateStatus"]
    if state == "DIRTY":
        return halt("the branch conflicts with main")
    if state == "UNKNOWN":
        return halt("mergeStateStatus is still UNKNOWN after the re-reads")
    if state == "CLEAN":
        if running:
            return act("wait", "checks are still running")
        if not pr["statusCheckRollup"]:
            return halt("CLEAN but no checks were reported")
        return act("merge", "all checks pass, the branch is CLEAN and the head is verified")
    if state == "BEHIND":
        if running:
            return act("wait", "BEHIND with checks running")
        return act(
            "update",
            "BEHIND with checks finished; updating from main, the head is verified before merge",
        )
    if state == "BLOCKED":
        # BLOCKED while required checks are pending is normal; with none reported yet, wait
        # for the next trigger. BLOCKED with every check green is a conversation or rule.
        if running or not pr["statusCheckRollup"]:
            return act("wait", "BLOCKED while checks are pending")
        return halt(
            "BLOCKED with every check green: an unresolved conversation or another requirement"
        )
    if state == "UNSTABLE" and running:
        return act("wait", "UNSTABLE while checks are running")
    return halt(f"unexpected mergeStateStatus {state}")


def _check_open_limit(open_prs: list[dict[str, Any]]) -> None:
    if len(open_prs) >= LIST_LIMIT:
        raise InputError(
            f"{LIST_LIMIT} or more open PRs: the list is cut at the gh limit, "
            "so the train cannot see every label"
        )


def _closed_hit(closed: list[dict[str, Any]], head_n: int) -> str | None:
    """A closed-unmerged PR whose train label sits below the head: the batch was broken."""
    for pr in closed:
        if pr["mergedAt"] is not None:
            continue
        for name in _train_names(pr):
            if not _TRAIN_RE.fullmatch(name):
                return f"#{pr['number']} was closed unmerged with a malformed train label"
            if int(name[len("train:") :]) < head_n:
                return (
                    f"#{pr['number']} was closed unmerged as {name}, below the head "
                    f"train:{head_n}; the batch is broken"
                )
    return None


def decide(
    prs: Any,
    head_check: dict[str, Any] | None,
    merge_train_on: bool,
    closed: list[dict[str, Any]] | None = None,
    facts: Any = None,
    stacks_on: bool = False,
) -> dict[str, Any]:
    """``facts`` is the head's facts record (module docstring), or None (not stacked);
    ``stacks_on`` is MERGE_TRAIN_STACKS. ``head_check`` is ``{"pr": N, "ok": bool, "reason": str}`` from train_verify_head.py for
    the candidate head, or None (not run, or it failed: the head halts). ``closed`` is the
    closed PRs (number, labels, mergedAt); None halts the train, since it cannot rule out a
    closed-unmerged PR promoting the next one."""
    prs = _validate(prs)
    if closed is not None:
        closed = _validate_closed(closed)
    if facts is not None:
        facts = _parse_facts(facts)
    open_prs = [pr for pr in prs if pr.get("state", OPEN) == OPEN]
    if not open_prs:
        return _out("none", None, "no open PRs", "none")
    for pr in open_prs:
        if any(name.lower() == HALTED for name in _names(pr)):
            return _out(
                "none",
                None,
                f"#{pr['number']} carries train:halted; nothing runs until it is removed",
                "none",
            )
    if not merge_train_on:
        return _update_only(open_prs)
    _check_open_limit(open_prs)

    def halt_on(pr: dict[str, Any], reason: str) -> dict[str, Any]:
        return _out("halt", pr, f"#{pr['number']}: {reason}", "train")

    for pr in open_prs:
        names = _train_names(pr)
        if len(names) > 1:
            return halt_on(pr, "more than one train label")
        if names and not _TRAIN_RE.fullmatch(names[0]):
            return halt_on(pr, "malformed train label (expected train:N with N of 1 to 3 digits)")
    numbered = sorted(
        ((int(_train_names(pr)[0][len("train:") :]), pr) for pr in open_prs if _train_names(pr)),
        key=lambda item: (item[0], item[1]["number"]),
    )
    if not numbered:
        return _update_only(open_prs)
    for (a, first), (b, second) in pairwise(numbered):
        if a == b:
            return halt_on(
                second, f"train:{a} is on both #{first['number']} and #{second['number']}"
            )
    head_n, head = numbered[0]
    present = {n for n, _ in numbered}
    gaps = [n for n in range(head_n, numbered[-1][0] + 1) if n not in present]
    if gaps:
        return halt_on(head, f"train:{gaps[0]} is missing; train numbers must be consecutive")
    if closed is None:
        return halt_on(head, "the closed PR list was not supplied; cannot rule out a broken batch")
    if len(closed) >= LIST_LIMIT:
        return halt_on(
            head,
            f"the closed PR list is at the {LIST_LIMIT}-PR limit; cannot see every closed PR",
        )
    hit = _closed_hit(closed, head_n)
    if hit is not None:
        return halt_on(head, hit)
    if facts is not None and facts["pr"] != head["number"]:
        raise InputError(f"facts are for #{facts['pr']}, but the train head is #{head['number']}")
    return _head(head, head_check, facts, stacks_on)


def candidate(prs: Any, merge_train_on: bool) -> tuple[int, str, str] | None:
    """The train head (number, head sha, reviewed sha) whose verification the workflow runs.
    None when there is no head, or its reviewed label is not exactly one valid label."""
    prs = _validate(prs)
    open_prs = [pr for pr in prs if pr.get("state", OPEN) == OPEN]
    if not merge_train_on or any(name.lower() == HALTED for pr in open_prs for name in _names(pr)):
        return None
    _check_open_limit(open_prs)
    numbered = []
    for pr in open_prs:
        names = _train_names(pr)
        if any(not _TRAIN_RE.fullmatch(name) for name in names):
            return None
        numbered.extend((int(name[len("train:") :]), pr) for name in names)
    if not numbered:
        return None
    _, head = min(numbered, key=lambda item: item[0])
    reviewed = [name for name in _names(head) if name.startswith("reviewed:")]
    if len(reviewed) != 1 or not _REVIEWED_RE.fullmatch(reviewed[0]):
        return None
    return head["number"], head["headRefOid"], reviewed[0][len("reviewed:") :]


def _head_check(pr_number: int, path: str) -> dict[str, Any]:
    """Wrap the verify script's JSON with the PR number it was run for."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise InputError("head check must be a JSON object")
    return {"pr": pr_number, "ok": data.get("ok") is True, "reason": str(data.get("reason", ""))}


def _audit(raw: Any) -> dict[str, Any]:
    if (
        not isinstance(raw, dict)
        or type(raw.get("merged_pr")) is not int
        or raw["merged_pr"] < 1
        or type(raw.get("failed")) is not bool
        or not isinstance(raw.get("reason"), str)
    ):
        raise InputError("audit must be {merged_pr: positive int, failed: bool, reason: str}")
    return raw


def audit_stop(audit: Any, open_prs: Any) -> int | None:
    """After a failed post-merge audit, the OPEN PR that gets ``train:halted``: the lowest
    open ``train:N`` (ties by number), never ``merged_pr``. A label on the merged (closed) PR
    would stop nothing, because decide scans only open PRs. None: nothing to stop."""
    audit = _audit(audit)
    prs = _validate(open_prs)
    if not audit["failed"]:
        return None
    found = [
        (int(name[len("train:") :]), pr["number"])
        for pr in prs
        if pr.get("state", OPEN) == OPEN and pr["number"] != audit["merged_pr"]
        for name in _train_names(pr)
        if _TRAIN_RE.fullmatch(name)
    ]
    return min(found)[1] if found else None


def _load_json(text: str) -> Any:
    """json.loads for the new inputs: absurdly nested input is an InputError, not a traceback.
    The existing readers (stdin PR list, --closed) are left as they were."""
    try:
        return json.loads(text)
    except RecursionError as exc:
        raise InputError("JSON nested too deeply") from exc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("candidate", "decide"):
        p = sub.add_parser(name)
        # Any value other than "on" means off: an unset repository variable must not fail.
        p.add_argument("--merge-train", default="off")
        if name == "decide":
            p.add_argument("--closed", metavar="FILE", help="JSON list of closed PRs")
            p.add_argument("--head-pr", type=int, metavar="N")
            p.add_argument("--head-check", metavar="FILE", help="verify output for --head-pr")
            p.add_argument("--facts", metavar="FILE", help="the head's facts record (JSON)")
            p.add_argument("--merge-train-stacks", default="off")
    stop = sub.add_parser("audit-stop")
    stop.add_argument("--audit", metavar="FILE", required=True)
    stop.add_argument("--prs", metavar="FILE", help="open PR list (default: stdin)")
    args = parser.parse_args(argv)
    try:
        if args.command == "audit-stop":
            audit = _load_json(Path(args.audit).read_text(encoding="utf-8"))
            if args.prs is not None:
                open_prs = _load_json(Path(args.prs).read_text(encoding="utf-8"))
            else:
                open_prs = _load_json(sys.stdin.read())
            target = audit_stop(audit, open_prs)
            print("none" if target is None else target)
            return 0
        prs = json.load(sys.stdin)
        merge_on = args.merge_train == "on"
        if args.command == "candidate":
            found = candidate(prs, merge_on)
            if found is not None:
                print(f"{found[0]} {found[1]} {found[2]}")
        else:
            closed = None
            if args.closed is not None:
                closed = json.loads(Path(args.closed).read_text(encoding="utf-8"))
            head_check = None
            if args.head_check is not None:
                if args.head_pr is None:
                    raise InputError("--head-check needs --head-pr")
                head_check = _head_check(args.head_pr, args.head_check)
            facts = None
            if args.facts is not None:
                facts = _load_json(Path(args.facts).read_text(encoding="utf-8"))
                # A PRESENT --facts that is not an object (a failed API call yielding null)
                # must not read as "not stacked".
                if not isinstance(facts, dict):
                    raise InputError("facts: the file must hold a JSON object")
            stacks_on = args.merge_train_stacks == "on"
            print(json.dumps(decide(prs, head_check, merge_on, closed, facts, stacks_on)))
    except ValueError as exc:  # includes json.JSONDecodeError and InputError
        print(f"train_step: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
