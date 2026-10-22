"""The next-PR rule of update-behind-prs.yml (``scripts/next_pr_to_update.py``).

Only the head of the queue is ever updated: the lowest-numbered open PR targeting main,
drafts included, excluding DIRTY (conflicts). It is updated only when it is BEHIND with no
running checks. Each test below guards a case that, if the rule changed, would update the
wrong PR, update a PR mid-CI, or cascade updates onto PRs that the next merge re-breaks.
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
def test_next_pr__head_behind_with_finished_checks__selected() -> None:
    prs = [_pr(6), _pr(5)]
    assert select_next_pr(prs) == 5


@pytest.mark.unit
def test_next_pr__shuffled_input__same_head() -> None:
    # PR 4 has conflicts (DIRTY), so it leaves the queue and PR 5 becomes the head.
    prs = [_pr(6), _pr(4, "DIRTY"), _pr(5), _pr(9, "UNKNOWN")]
    assert select_next_pr(prs) == 5
    assert select_next_pr(list(reversed(prs))) == 5


@pytest.mark.unit
def test_next_pr__head_is_draft__still_selected() -> None:
    prs = [_pr(3, draft=True), _pr(4)]
    assert select_next_pr(prs) == 3


@pytest.mark.unit
def test_next_pr__head_clean__nothing_selected_even_if_later_pr_behind() -> None:
    # No cascade: PR 2 is BEHIND, but updating it would make it BEHIND again after the
    # next merge. Only the head is considered.
    prs = [_pr(1, "CLEAN"), _pr(2)]
    assert select_next_pr(prs) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "check",
    [
        _running("QUEUED"),
        _running("IN_PROGRESS"),
        _running("PENDING"),
        _running("WAITING"),
        _running("REQUESTED"),
        {"__typename": "StatusContext", "context": "ci", "state": "PENDING"},
        {"__typename": "StatusContext", "context": "ci", "state": "EXPECTED"},
    ],
    ids=[
        "queued",
        "in-progress",
        "check-pending",
        "waiting",
        "requested",
        "status-pending",
        "status-expected",
    ],
)
def test_next_pr__head_with_running_check__nothing_selected(check: dict[str, Any]) -> None:
    prs = [_pr(1, checks=[_DONE_CHECK, check]), _pr(2)]
    assert select_next_pr(prs) is None


@pytest.mark.unit
def test_next_pr__head_dirty__skipped_and_next_lowest_becomes_head() -> None:
    # A bot update cannot fix conflicts, so DIRTY leaves the queue.
    assert select_next_pr([_pr(1, "DIRTY"), _pr(2)]) == 2
    # The new head is CLEAN, so nothing is updated: DIRTY did not make PR 3 eligible.
    assert select_next_pr([_pr(1, "DIRTY"), _pr(2, "CLEAN"), _pr(3)]) is None


@pytest.mark.unit
def test_next_pr__head_unknown__nothing_selected() -> None:
    # UNKNOWN is not BEHIND. The head is not skipped in favour of a later PR either.
    assert select_next_pr([_pr(1, "UNKNOWN"), _pr(2)]) is None


@pytest.mark.unit
def test_next_pr__null_check_rollup__is_treated_as_no_running_checks() -> None:
    # GitHub returns null for a PR that has no checks yet; that is not "running".
    pr = _pr(7)
    pr["statusCheckRollup"] = None
    assert select_next_pr([pr]) == 7


@pytest.mark.unit
def test_next_pr__failed_checks_only__still_selected() -> None:
    # Only *running* checks hold a PR back; a red head is still the next one to merge.
    failed = {
        "__typename": "CheckRun",
        "name": "lint",
        "status": "COMPLETED",
        "conclusion": "FAILURE",
    }
    assert select_next_pr([_pr(7, checks=[failed])]) == 7


@pytest.mark.unit
def test_next_pr__empty_list__returns_none() -> None:
    assert select_next_pr([]) is None


@pytest.mark.unit
def test_next_pr__include_drafts_false__draft_head_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    # The INCLUDE_DRAFTS switch: with drafts excluded, the head is the lowest non-draft.
    monkeypatch.setattr(_module, "INCLUDE_DRAFTS", False)
    assert select_next_pr([_pr(1, draft=True), _pr(2)]) == 2
    assert select_next_pr([_pr(1, draft=True)]) is None


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

    assert run([_pr(9), _pr(8, draft=True)]) == "8\n"
    assert run([_pr(9)]) == "9\n"
    assert run([_pr(8, "CLEAN")]) == ""


@pytest.mark.unit
@pytest.mark.parametrize("state", ["SUCCESS", "FAILURE", "ERROR"])
def test_next_pr__finished_commit_status__is_not_running(state: str) -> None:
    # A StatusContext (commit status) carries `state`, not `status`; only PENDING and
    # EXPECTED mean it is still running.
    check = {"__typename": "StatusContext", "context": "ci/legacy", "state": state}
    assert select_next_pr([_pr(3, checks=[check])]) == 3


@pytest.mark.unit
def test_next_pr__cancelled_check_head__is_selected() -> None:
    # A cancelled run is COMPLETED, so the head is not "running" and gets updated.
    cancelled = {**_DONE_CHECK, "conclusion": "CANCELLED"}
    assert select_next_pr([_pr(3, checks=[cancelled])]) == 3


@pytest.mark.integration
def test_next_pr__cli_malformed_input__fails_visibly() -> None:
    # A bad `gh` response must fail the step, not silently update nothing.
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        input="not json",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode != 0
    assert proc.stdout == ""
