"""The merge-train rule of update-behind-prs.yml (``scripts/train_step.py``).

Each test guards one rule of the owner's merge-train spec: a batch is ordered by its
``train:N`` labels, only the head is acted on, and a merge needs a green, CLEAN, non-draft
head whose net patch id matches its ``reviewed:`` label. Anything else halts or waits.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS = Path(__file__).parent.parent.parent / "scripts"
_SCRIPT = _SCRIPTS / "train_step.py"
_spec = importlib.util.spec_from_file_location("train_step", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
decide = _module.decide
candidate = _module.candidate
InputError = _module.InputError

PID = "0123456789ab"
_DONE = {"__typename": "CheckRun", "name": "fast", "status": "COMPLETED", "conclusion": "SUCCESS"}


def _sha(number: int) -> str:
    return f"{number:040x}"


def _pr(
    number: int,
    state: str = "BEHIND",
    *,
    draft: bool = False,
    labels: tuple[str, ...] = (),
    checks: list[dict[str, Any]] | None = None,
    pr_state: str = "OPEN",
) -> dict[str, Any]:
    return {
        "number": number,
        "state": pr_state,
        "isDraft": draft,
        "mergeStateStatus": state,
        "headRefOid": _sha(number),
        "labels": [{"name": name} for name in labels],
        "statusCheckRollup": checks if checks is not None else [_DONE],
    }


def _ready(
    number: int, *, train: int | None = 1, state: str = "CLEAN", checks=None
) -> dict[str, Any]:
    labels = (f"reviewed:{PID}",) + ((f"train:{train}",) if train is not None else ())
    return _pr(number, state, labels=labels, checks=checks)


def _running(status: str = "IN_PROGRESS") -> dict[str, Any]:
    return {"__typename": "CheckRun", "name": "full", "status": status, "conclusion": ""}


def _failed(name: str = "gates") -> dict[str, Any]:
    return {"__typename": "CheckRun", "name": name, "status": "COMPLETED", "conclusion": "FAILURE"}


def _ids(*numbers: int) -> dict[int, str]:
    return dict.fromkeys(numbers, PID)


# Rule 1: ordering and scope.


@pytest.mark.unit
def test_train__head_is_lowest_train_label_not_lowest_number() -> None:
    # #3 has the lower PR number but train:2; the head must be #9 (train:1). Ordering by
    # number would pick #3 and ask for its patch id instead.
    prs = [_pr(3, labels=("train:2",)), _ready(9, train=1, state="BEHIND")]
    result = decide(prs, _ids(9), True)
    assert (result["action"], result["pr"]) == ("update", 9)


@pytest.mark.unit
def test_train__closed_pr_with_lowest_train_label_is_ignored() -> None:
    # Only open PRs count: a closed train:1 must not hold the head.
    prs = [_ready(2, train=1, state="CLEAN"), _ready(4, train=2, state="CLEAN")]
    prs[0]["state"] = "CLOSED"
    result = decide(prs, _ids(4), True)
    assert (result["action"], result["pr"]) == ("merge", 4)


@pytest.mark.unit
def test_train__empty_list__none() -> None:
    assert decide([], {}, True)["action"] == "none"


@pytest.mark.unit
def test_train__non_train_prs_not_updated_while_train_runs() -> None:
    # The train head is still running checks; the BEHIND non-train PR #3 is not touched.
    prs = [_pr(3, labels=()), _ready(9, train=1, checks=[_DONE, _running()])]
    result = decide(prs, _ids(9), True)
    assert (result["action"], result["pr"]) == ("wait", 9)


# Rule 2: merge gates.


@pytest.mark.unit
def test_train__all_gates_pass__merge_head_pinned_to_sha() -> None:
    result = decide([_ready(9), _ready(10, train=2)], _ids(9), True)
    assert result["action"] == "merge"
    assert result["pr"] == 9
    assert result["head_sha"] == _sha(9)


@pytest.mark.unit
def test_train__draft_head__halts_never_merges() -> None:
    prs = [_ready(9, state="CLEAN")]
    prs[0]["isDraft"] = True
    assert decide(prs, _ids(9), True)["action"] == "halt"


@pytest.mark.unit
@pytest.mark.parametrize("state", ["UNSTABLE", "HAS_HOOKS", "DIRTY"])
def test_train__not_clean__halts_not_merged(state: str) -> None:
    # Only CLEAN merges. UNSTABLE is what non-required failures look like; it must not merge.
    result = decide([_ready(9, state=state)], _ids(9), True)
    assert result["action"] == "halt"


@pytest.mark.unit
def test_train__clean_but_no_checks_reported__halts() -> None:
    assert decide([_ready(9, checks=[])], _ids(9), True)["action"] == "halt"


@pytest.mark.unit
def test_train__failed_non_required_check_on_clean_head__halts() -> None:
    # `gates` is not a required check, so GitHub still says CLEAN. The train must stop.
    result = decide([_ready(9, checks=[_DONE, _failed("gates")])], _ids(9), True)
    assert result["action"] == "halt"
    assert "gates" in result["reason"]


@pytest.mark.unit
def test_train__failed_check_outranks_running_check__halts_not_waits() -> None:
    result = decide([_ready(9, checks=[_failed(), _running()])], _ids(9), True)
    assert result["action"] == "halt"


@pytest.mark.unit
@pytest.mark.parametrize(
    "check",
    [
        _DONE,
        {**_DONE, "conclusion": "SKIPPED"},
        {**_DONE, "conclusion": "NEUTRAL"},
        {"__typename": "StatusContext", "context": "ci", "state": "SUCCESS"},
    ],
    ids=["success", "skipped", "neutral", "status-success"],
)
def test_train__passing_conclusions__do_not_block(check: dict[str, Any]) -> None:
    assert decide([_ready(9, checks=[check])], _ids(9), True)["action"] == "merge"


@pytest.mark.unit
@pytest.mark.parametrize(
    "check",
    [
        _running("QUEUED"),
        _running("IN_PROGRESS"),
        {"__typename": "StatusContext", "context": "ci", "state": "PENDING"},
        {"__typename": "StatusContext", "context": "ci", "state": "EXPECTED"},
    ],
    ids=["queued", "in-progress", "status-pending", "status-expected"],
)
def test_train__clean_head_with_running_check__waits(check: dict[str, Any]) -> None:
    result = decide([_ready(9, checks=[_DONE, check])], _ids(9), True)
    assert result["action"] == "wait"


@pytest.mark.unit
def test_train__running_checks_on_behind_head__waits() -> None:
    result = decide([_ready(9, state="BEHIND", checks=[_DONE, _running()])], _ids(9), True)
    assert result["action"] == "wait"


@pytest.mark.unit
def test_train__behind_head_with_checks_finished__updated_in_train_mode() -> None:
    result = decide([_ready(9, state="BEHIND")], _ids(9), True)
    assert (result["action"], result["pr"], result["mode"]) == ("update", 9, "train")
    assert "net patch" in result["reason"]


@pytest.mark.unit
def test_train__blocked_with_green_checks__halts_unresolved_conversation() -> None:
    # The Copilot-thread case: checks are green but a thread is unresolved.
    assert decide([_ready(9, state="BLOCKED")], _ids(9), True)["action"] == "halt"


@pytest.mark.unit
def test_train__blocked_while_checks_run__waits() -> None:
    result = decide([_ready(9, state="BLOCKED", checks=[_running()])], _ids(9), True)
    assert result["action"] == "wait"


@pytest.mark.unit
@pytest.mark.parametrize("state", ["UNKNOWN", "XYZ"])
def test_train__unknown_or_unexpected_state__halts(state: str) -> None:
    assert decide([_ready(9, state=state)], _ids(9), True)["action"] == "halt"


# Rule 3: halts.


@pytest.mark.unit
def test_train__missing_reviewed_label__halts() -> None:
    prs = [_pr(9, "CLEAN", labels=("train:1",))]
    result = decide(prs, _ids(9), True)
    assert result["action"] == "halt"
    assert "reviewed" in result["reason"]


@pytest.mark.unit
def test_train__wrong_reviewed_label__halts() -> None:
    prs = [_pr(9, "CLEAN", labels=("train:1", "reviewed:ffffffffffff"))]
    assert decide(prs, _ids(9), True)["action"] == "halt"


@pytest.mark.unit
def test_train__malformed_reviewed_label_is_not_accepted() -> None:
    # Upper-case hex does not match the regex, so the label does not count: missing, halt.
    prs = [_pr(9, "CLEAN", labels=("train:1", "reviewed:0123456789AB"))]
    assert decide(prs, _ids(9), True)["action"] == "halt"


@pytest.mark.unit
def test_train__patch_id_mismatch__halts_not_merged() -> None:
    # The reviewed id matches the label, but the head content changed since review.
    prs = [_ready(9)]
    result = decide(prs, {9: "aaaaaaaaaaaa"}, True)
    assert result["action"] == "halt"
    assert "mismatch" in result["reason"]


@pytest.mark.unit
def test_train__patch_id_not_computed__halts() -> None:
    assert decide([_ready(9)], {}, True)["action"] == "halt"


@pytest.mark.unit
def test_train__duplicate_train_numbers__halts_on_second_pr() -> None:
    result = decide([_ready(9, train=1), _ready(10, train=1)], _ids(9, 10), True)
    assert (result["action"], result["pr"]) == ("halt", 10)


@pytest.mark.unit
def test_train__missing_train_number__halts_on_head() -> None:
    # train:1 and train:3 with no train:2: the numbering must be consecutive.
    result = decide([_ready(9, train=1), _ready(10, train=3)], _ids(9), True)
    assert result["action"] == "halt"
    assert "train:2" in result["reason"]


@pytest.mark.unit
@pytest.mark.parametrize("label", ["train:x", "train:1000", "train:", "TRAIN:1", "train:1 "])
def test_train__malformed_train_label__halts(label: str) -> None:
    prs = [_pr(9, labels=(label,)), _ready(10, train=2)]
    result = decide(prs, _ids(10), True)
    assert (result["action"], result["pr"]) == ("halt", 9)


@pytest.mark.unit
def test_train__two_train_labels_on_one_pr__halts() -> None:
    prs = [_pr(9, labels=("train:1", "train:2"))]
    assert decide(prs, _ids(9), True)["action"] == "halt"


@pytest.mark.unit
@pytest.mark.parametrize("on", [True, False])
def test_train__train_halted_anywhere__nothing_runs(on: bool) -> None:
    # Even a ready head does nothing while any open PR carries train:halted.
    prs = [_ready(9), _pr(20, labels=("train:halted",))]
    result = decide(prs, _ids(9), on)
    assert (result["action"], result["pr"]) == ("none", None)
    assert "#20" in result["reason"]


@pytest.mark.unit
def test_train__halted_closed_pr__does_not_stop_the_train() -> None:
    prs = [_ready(9), _pr(20, labels=("train:halted",), pr_state="CLOSED")]
    assert decide(prs, _ids(9), True)["action"] == "merge"


# Rule 4: MERGE_TRAIN off and update-only mode.


@pytest.mark.unit
def test_train__merge_train_off__head_ready_is_never_merged() -> None:
    result = decide([_ready(9)], _ids(9), False)
    assert result["action"] != "merge"
    assert (result["action"], result["mode"]) == ("none", "update")


@pytest.mark.unit
def test_train__merge_train_off__train_labelled_head_updated_as_today() -> None:
    # Update-only mode: the lowest-numbered open PR is updated if BEHIND, as before.
    result = decide([_ready(9, state="BEHIND")], {}, False)
    assert (result["action"], result["pr"], result["mode"]) == ("update", 9, "update")


@pytest.mark.unit
def test_train__no_train_labels__update_only_rule() -> None:
    prs = [_pr(5), _pr(6, "CLEAN")]
    result = decide(prs, {}, True)
    assert (result["action"], result["pr"], result["mode"]) == ("update", 5, "update")


@pytest.mark.unit
def test_train__no_train_labels__clean_head_is_not_merged() -> None:
    assert decide([_pr(5, "CLEAN", labels=(f"reviewed:{PID}",))], {}, True)["action"] == "none"


@pytest.mark.unit
def test_train__behind_head_without_review_label__halts_not_updated() -> None:
    # The review check runs before the update: a BEHIND head with no verdict is a halt.
    result = decide([_pr(9, "BEHIND", labels=("train:1",))], _ids(9), True)
    assert result["action"] == "halt"


# Candidate: which head the workflow must compute the patch id for.


@pytest.mark.unit
def test_candidate__lowest_train_head_with_its_sha() -> None:
    prs = [_pr(3, labels=("train:2",)), _pr(9, labels=("train:1",))]
    assert candidate(prs, True) == (9, _sha(9))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("prs", "on"),
    [
        ([_ready(9)], False),
        ([_pr(5)], True),
        ([_pr(9, labels=("train:1",)), _pr(20, labels=("train:halted",))], True),
        ([_pr(9, labels=("train:x",))], True),
        ([], True),
    ],
    ids=["merge-off", "no-train", "halted", "malformed", "empty"],
)
def test_candidate__no_head_to_check__none(prs: list[dict[str, Any]], on: bool) -> None:
    assert candidate(prs, on) is None


# Input validation and the CLI.


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad",
    [
        {"number": 1},
        [{"number": "9", "isDraft": False, "mergeStateStatus": "CLEAN"}],
        [{**_pr(9), "headRefOid": "abc"}],
        [{**_pr(9), "labels": ["train:1"]}],
    ],
    ids=["not-a-list", "string-number", "short-sha", "labels-not-objects"],
)
def test_input__wrong_shape__raises(bad: Any) -> None:
    with pytest.raises(InputError):
        decide(bad, {}, True)


@pytest.mark.integration
def test_cli__malformed_json__exits_nonzero_and_prints_nothing() -> None:
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "decide", "--merge-train", "on"],
        input="not json",
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 2
    assert proc.stdout == ""


@pytest.mark.integration
def test_cli__decide_and_candidate_round_trip() -> None:
    prs = json.dumps([_ready(9)])

    def run(*args: str) -> str:
        return subprocess.run(
            [sys.executable, str(_SCRIPT), *args],
            input=prs,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    assert run("candidate", "--merge-train", "on") == f"9 {_sha(9)}\n"
    assert run("candidate", "--merge-train", "off") == ""
    out = json.loads(run("decide", "--merge-train", "on", "--patch-id", f"9:{PID}"))
    assert (out["action"], out["pr"]) == ("merge", 9)
    # No patch id supplied: the head fails closed to a halt.
    assert json.loads(run("decide", "--merge-train", "on"))["action"] == "halt"


@pytest.mark.integration
def test_cli__unset_variable_means_off() -> None:
    # An unset repository variable reaches the script as the default "off": no failure.
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "decide"],
        input=json.dumps([_ready(9)]),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout)["action"] != "merge"
