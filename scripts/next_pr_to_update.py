"""Choose the one PR that update-behind-prs.yml brings up to date.

Reads ``gh pr list --json number,isDraft,mergeStateStatus,statusCheckRollup`` output
on stdin and prints the number of the PR to update, or nothing. The rule is documented
in the header of ``.github/workflows/update-behind-prs.yml``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

# Drafts are eligible to be the next PR to merge. Flip to False to skip them; the
# head of the queue is then the lowest-numbered non-draft PR.
INCLUDE_DRAFTS = True

# A check run that has not COMPLETED (QUEUED, IN_PROGRESS, PENDING, WAITING, REQUESTED)
# or a commit status still PENDING/EXPECTED counts as running. Anything else does not.
_RUNNING_STATES = frozenset({"PENDING", "EXPECTED"})


def has_running_check(pr: dict[str, Any]) -> bool:
    for check in pr.get("statusCheckRollup") or []:
        if (check.get("status") or "COMPLETED") != "COMPLETED":
            return True
        if check.get("state") in _RUNNING_STATES:
            return True
    return False


def select_next_pr(prs: list[dict[str, Any]]) -> int | None:
    # Only the head of the queue is ever updated. Updating any other PR makes it BEHIND
    # again after the next merge and wastes CI. A CLEAN or CI-running head means nothing
    # to do now; the workflow_run trigger re-evaluates when that CI finishes. DIRTY
    # (conflicts) cannot be fixed by a bot update, so it leaves the queue.
    queue = sorted(
        (
            pr
            for pr in prs
            if pr["mergeStateStatus"] != "DIRTY" and (INCLUDE_DRAFTS or not pr["isDraft"])
        ),
        key=lambda pr: pr["number"],
    )
    if not queue:
        return None
    head = queue[0]
    if head["mergeStateStatus"] != "BEHIND" or has_running_check(head):
        return None
    return head["number"]


def main() -> None:
    number = select_next_pr(json.load(sys.stdin))
    if number is not None:
        print(number)


if __name__ == "__main__":
    main()
