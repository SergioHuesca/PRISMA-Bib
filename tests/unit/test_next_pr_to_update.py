"""The next-PR rule of update-behind-prs.yml (``scripts/next_pr_to_update.py``).

After a push to main only one PR is brought up to date: the lowest-numbered open,
non-draft PR that is BEHIND and has no running checks. Each filter below guards a
case that, if dropped, updates the wrong PR (or a draft, or a PR mid-CI).
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "next_pr_to_update.py"
_spec = importlib.util.spec_from_file_location("next_pr_to_update", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
select_next_pr = _module.select_next_pr

_DONE_CHECK = {
    "__typename": "CheckRun",
    "name": "fast",
    "status": "COMPLETED",
    "conclusion": "SUCCESS",
}


def _pr(
    number: int,
    state: str = "BEHIND",
    *,
    draft: bool = False,
    checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "number": number,
        "isDraft": draft,
        "mergeStateStatus": state,
        "statusCheckRollup": checks if checks is not None else [_DONE_CHECK],
    }


def _running(status: str) -> dict[str, Any]:
    return {"__typename": "CheckRun", "name": "full", "status": status, "conclusion": ""}


@pytest.mark.unit
def test_next_pr__mixed_candidates__picks_lowest_eligible_regardless_of_order() -> None:
    prs = [
        _pr(6),  # eligible, higher number
        _pr(4, "UNKNOWN"),  # not known to be BEHIND
        _pr(3, "CLEAN"),  # nothing to update
        _pr(2, checks=[_running("IN_PROGRESS")]),  # required CI still running
        _pr(1, draft=True),  # drafts are never updated
        _pr(5),  # eligible, lowest eligible
    ]
    assert select_next_pr(prs) == 5
    assert select_next_pr(list(reversed(prs))) == 5


@pytest.mark.unit
def test_next_pr__null_check_rollup__is_treated_as_no_running_checks() -> None:
    # GitHub returns null for a PR that has no checks yet; that is not "running".
    pr = _pr(7)
    pr["statusCheckRollup"] = None
    assert select_next_pr([pr]) == 7


@pytest.mark.unit
def test_next_pr__failed_checks_only__still_eligible() -> None:
    # Only *running* checks exclude a PR; a red PR is still the next one to merge.
    failed = {
        "__typename": "CheckRun",
        "name": "lint",
        "status": "COMPLETED",
        "conclusion": "FAILURE",
    }
    assert select_next_pr([_pr(7, checks=[failed])]) == 7


@pytest.mark.unit
@pytest.mark.parametrize(
    "check",
    [
        _running("QUEUED"),
        _running("PENDING"),
        _running("WAITING"),
        {"__typename": "StatusContext", "context": "ci", "state": "PENDING"},
        {"__typename": "StatusContext", "context": "ci", "state": "EXPECTED"},
    ],
    ids=["queued", "check-pending", "waiting", "status-pending", "status-expected"],
)
def test_next_pr__any_running_check__excluded(check: dict[str, Any]) -> None:
    prs = [_pr(1, checks=[_DONE_CHECK, check]), _pr(2)]
    assert select_next_pr(prs) == 2


@pytest.mark.unit
def test_next_pr__nothing_eligible__returns_none() -> None:
    prs = [
        _pr(1, draft=True),
        _pr(2, "CLEAN"),
        _pr(3, "UNKNOWN"),
        _pr(4, checks=[_running("IN_PROGRESS")]),
    ]
    assert select_next_pr(prs) is None
    assert select_next_pr([]) is None


@pytest.mark.integration
def test_next_pr__cli__prints_number_or_nothing() -> None:
    def run(prs: list[dict[str, Any]]) -> str:
        proc = subprocess.run(
            [sys.executable, str(_SCRIPT)],
            input=json.dumps(prs),
            capture_output=True,
            text=True,
            check=True,
        )
        return proc.stdout

    assert run([_pr(9), _pr(8, draft=True)]) == "9\n"
    assert run([_pr(8, "CLEAN")]) == ""
