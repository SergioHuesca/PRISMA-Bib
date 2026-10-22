"""Choose the one PR that update-behind-prs.yml brings up to date after a push to main.

Reads ``gh pr list --json number,isDraft,mergeStateStatus,statusCheckRollup`` output
on stdin and prints the number of the next PR to merge, or nothing. The definition
lives in the header of the ``update`` job in ``.github/workflows/update-behind-prs.yml``.
"""

from __future__ import annotations

import json
import sys
from typing import Any

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
    eligible = [
        pr["number"]
        for pr in prs
        if not pr["isDraft"] and pr["mergeStateStatus"] == "BEHIND" and not has_running_check(pr)
    ]
    return min(eligible, default=None)


def main() -> None:
    number = select_next_pr(json.load(sys.stdin))
    if number is not None:
        print(number)


if __name__ == "__main__":
    main()
