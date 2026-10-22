"""Decide the one action of update-behind-prs.yml: the merge train, or update-only mode.

Pure logic: no GitHub or git calls. The workflow feeds it the output of
``gh pr list --json number,state,isDraft,mergeStateStatus,headRefOid,labels,statusCheckRollup``
and executes the action it returns. Subcommands:

``candidate --merge-train on|off``
    Prints ``<pr> <head sha>`` for the train head, whose net patch id the workflow must
    compute before ``decide``. Prints nothing when there is no head to check.

``decide --merge-train on|off [--patch-id N:PID ...]``
    Prints one JSON object ``{"action", "pr", "head_sha", "reason", "mode"}``. action is one
    of none, update, merge, halt, wait; mode is train, update or none.

The rules are in the header of .github/workflows/update-behind-prs.yml and in
docs/how-to/merge-train.md. Malformed input exits 2 (ValueError): a bad response must
fail the step, never act on a guess.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from itertools import pairwise
from pathlib import Path
from typing import Any

# The update-only rule lives in next_pr_to_update.py. The workflow runs this file from
# scripts/, so the import works there and in the unit tests.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from next_pr_to_update import select_next_pr

OPEN = "OPEN"
HALTED = "train:halted"
# fullmatch, not match: `$` would accept a trailing newline in a label or argument.
_TRAIN_RE = re.compile(r"train:[0-9]{1,3}")
_REVIEWED_RE = re.compile(r"reviewed:[0-9a-f]{12}")
_SHA_RE = re.compile(r"[0-9a-f]{40}")
_PATCH_ARG_RE = re.compile(r"([0-9]{1,9}):([0-9a-f]{12})")
_PASS_CONCLUSIONS = frozenset({"SUCCESS", "SKIPPED", "NEUTRAL"})
_PENDING_STATES = frozenset({"PENDING", "EXPECTED"})

PASS, RUNNING, BAD = "pass", "running", "bad"


class InputError(ValueError):
    """The input is not the shape that ``gh pr list --json`` produces."""


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
    return prs


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
        (_outcome(check), str(check.get("name") or check.get("context") or "unnamed"))
        for check in pr["statusCheckRollup"] or []
    ]
    for outcome, name in outcomes:
        if outcome == BAD:
            return BAD, name
    if any(outcome == RUNNING for outcome, _ in outcomes):
        return RUNNING, ""
    return PASS, ""


def _out(action: str, pr: dict[str, Any] | None, reason: str, mode: str) -> dict[str, Any]:
    return {
        "action": action,
        "pr": pr["number"] if pr else None,
        "head_sha": pr["headRefOid"] if pr else None,
        "reason": reason,
        "mode": mode,
    }


def _update_only(open_prs: list[dict[str, Any]]) -> dict[str, Any]:
    number = select_next_pr(open_prs)
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


def _head(pr: dict[str, Any], patch_ids: dict[int, str]) -> dict[str, Any]:
    """Apply rules 2 and 4 to the train head. Every branch that is not an explicit merge,
    update or wait is a halt."""
    n = pr["number"]

    def halt(reason: str) -> dict[str, Any]:
        return _out("halt", pr, f"#{n}: {reason}", "train")

    def act(action: str, reason: str) -> dict[str, Any]:
        return _out(action, pr, f"#{n}: {reason}", "train")

    if pr["isDraft"]:
        return halt("the head is a draft; batch PRs must be marked ready before the train starts")
    reviewed = [name for name in _names(pr) if _REVIEWED_RE.fullmatch(name)]
    if not reviewed:
        return halt("missing reviewed:<patch id> label")
    if len(reviewed) > 1:
        return halt("more than one reviewed: label")
    if n not in patch_ids:
        return halt("net patch id was not computed for the head")
    reviewed_id = reviewed[0].removeprefix("reviewed:")
    if reviewed_id != patch_ids[n]:
        return halt(
            f"patch id mismatch: reviewed {reviewed_id}, current {patch_ids[n]}; "
            "the content changed after review"
        )
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
        return act(
            "merge", "all checks pass, the branch is CLEAN and the net patch matches its review"
        )
    if state == "BEHIND":
        if running:
            return act("wait", "BEHIND with checks running")
        return act(
            "update",
            "BEHIND with checks finished; updating from main, the net patch is verified before merge",
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


def decide(prs: Any, patch_ids: dict[int, str], merge_train_on: bool) -> dict[str, Any]:
    prs = _validate(prs)
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
    return _head(head, patch_ids)


def candidate(prs: Any, merge_train_on: bool) -> tuple[int, str] | None:
    """The train head (number, head sha) whose net patch id the workflow must compute."""
    prs = _validate(prs)
    open_prs = [pr for pr in prs if pr.get("state", OPEN) == OPEN]
    if not merge_train_on or any(name.lower() == HALTED for pr in open_prs for name in _names(pr)):
        return None
    numbered = []
    for pr in open_prs:
        names = _train_names(pr)
        if any(not _TRAIN_RE.fullmatch(name) for name in names):
            return None
        numbered.extend((int(name[len("train:") :]), pr) for name in names)
    if not numbered:
        return None
    _, head = min(numbered, key=lambda item: item[0])
    return head["number"], head["headRefOid"]


def _patch_ids(args: list[str]) -> dict[int, str]:
    ids: dict[int, str] = {}
    for arg in args:
        match = _PATCH_ARG_RE.fullmatch(arg)
        if not match:
            raise InputError(f"bad --patch-id {arg!r}; expected N:<12 hex>")
        ids[int(match.group(1))] = match.group(2)
    return ids


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("candidate", "decide"):
        p = sub.add_parser(name)
        # Any value other than "on" means off: an unset repository variable must not fail.
        p.add_argument("--merge-train", default="off")
        if name == "decide":
            p.add_argument("--patch-id", action="append", default=[], metavar="N:PID")
    args = parser.parse_args(argv)
    try:
        prs = json.load(sys.stdin)
        merge_on = args.merge_train == "on"
        if args.command == "candidate":
            found = candidate(prs, merge_on)
            if found is not None:
                print(f"{found[0]} {found[1]}")
        else:
            print(json.dumps(decide(prs, _patch_ids(args.patch_id), merge_on)))
    except ValueError as exc:  # includes json.JSONDecodeError and InputError
        print(f"train_step: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
