"""The merge-train rule of update-behind-prs.yml (``scripts/train_step.py``).

Each test guards one rule of the owner's merge-train spec: a batch is ordered by its
``train:N`` labels, only the head is acted on, a closed-unmerged batch PR halts, and a merge
needs a green, CLEAN, non-draft head whose exact-commit verification passed. Anything else
halts or waits.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "train_step.py"
_spec = importlib.util.spec_from_file_location("train_step", _SCRIPT)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
candidate = _module.candidate
InputError = _module.InputError

# A 40-hex commit id; a repeated pattern so detect-secrets does not flag the literal.
REVIEWED = "ab" * 20
_DONE = {"__typename": "CheckRun", "name": "fast", "status": "COMPLETED", "conclusion": "SUCCESS"}


def decide(prs, head, merge_train_on, closed=()):  # type: ignore[no-untyped-def]
    """Tests supply an empty closed list by default; ``closed=None`` is the missing-list case."""
    return _module.decide(prs, head, merge_train_on, None if closed is None else list(closed))


def _ok(number: int) -> dict[str, Any]:
    return {"pr": number, "ok": True, "reason": ""}


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
    number: int,
    *,
    train: int | None = 1,
    state: str = "CLEAN",
    checks: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    labels = (f"reviewed:{REVIEWED}",) + ((f"train:{train}",) if train is not None else ())
    return _pr(number, state, labels=labels, checks=checks)


def _running(status: str = "IN_PROGRESS") -> dict[str, Any]:
    return {"__typename": "CheckRun", "name": "full", "status": status, "conclusion": ""}


def _failed(name: str = "gates") -> dict[str, Any]:
    return {"__typename": "CheckRun", "name": name, "status": "COMPLETED", "conclusion": "FAILURE"}


def _closed(number: int, label: str | None = None, merged: bool = False) -> dict[str, Any]:
    return {
        "number": number,
        "labels": [] if label is None else [{"name": label}],
        "mergedAt": "2026-10-01T00:00:00Z" if merged else None,
    }


# Rule 1: ordering and scope.


@pytest.mark.unit
def test_train__head_is_lowest_train_label_not_lowest_number() -> None:
    # #3 has the lower PR number but train:2; the head must be #9 (train:1). Ordering by
    # number would pick #3 instead.
    prs = [_pr(3, labels=("train:2",)), _pr(9, labels=("train:1",), state="BEHIND")]
    result = decide(prs, None, True)
    assert (result["action"], result["pr"]) == ("halt", 9)  # no verification: halts on head
    result = decide(prs, _ok(9), True)
    assert (result["action"], result["pr"]) == ("halt", 9)  # label missing: still the head


@pytest.mark.unit
def test_train__closed_open_state_pr_with_lowest_label_is_ignored() -> None:
    # Only open PRs count: a closed train:1 must not hold the head.
    prs = [_ready(2, train=1), _ready(4, train=2)]
    prs[0]["state"] = "CLOSED"
    result = decide(prs, _ok(4), True, [])
    assert (result["action"], result["pr"]) == ("merge", 4)


@pytest.mark.unit
def test_train__empty_list__none() -> None:
    assert decide([], None, True)["action"] == "none"


@pytest.mark.unit
def test_train__non_train_prs_not_updated_while_train_runs() -> None:
    prs = [_pr(3, labels=()), _ready(9, train=1, checks=[_DONE, _running()])]
    result = decide(prs, _ok(9), True)
    assert (result["action"], result["pr"]) == ("wait", 9)


# Closed-unmerged PRs: a closed train:K below the head breaks the batch.


@pytest.mark.unit
def test_closed__unmerged_train_below_head__halts_and_names_the_pr() -> None:
    result = decide([_ready(4, train=2)], _ok(4), True, [_closed(2, "train:1")])
    assert result["action"] == "halt"
    assert "#2" in result["reason"]


@pytest.mark.unit
def test_closed__merged_train_below_head__proceeds() -> None:
    result = decide([_ready(4, train=2)], _ok(4), True, [_closed(2, "train:1", merged=True)])
    assert result["action"] == "merge"


@pytest.mark.unit
def test_closed__train_above_head__does_not_matter() -> None:
    assert decide([_ready(4, train=2)], _ok(4), True, [_closed(5, "train:3")])["action"] == "merge"


@pytest.mark.unit
def test_closed__list_missing_in_train_mode__halts() -> None:
    assert decide([_ready(9)], _ok(9), True, None)["action"] == "halt"


@pytest.mark.unit
def test_closed__list_missing_in_update_only_mode__not_needed() -> None:
    assert decide([_pr(5)], None, False, None)["action"] == "update"


@pytest.mark.unit
def test_closed__malformed_train_label_unmerged__halts() -> None:
    result = decide([_ready(4, train=2)], _ok(4), True, [_closed(2, "train:x")])
    assert result["action"] == "halt"


@pytest.mark.unit
def test_closed__list_at_limit__halts_rather_than_guess() -> None:
    closed = [_closed(100 + i, merged=True) for i in range(100)]
    assert decide([_ready(9)], _ok(9), True, closed)["action"] == "halt"


@pytest.mark.unit
def test_closed__malformed_record__raises() -> None:
    with pytest.raises(InputError):
        decide([_ready(9)], _ok(9), True, [{"number": 2, "labels": "train:1"}])


@pytest.mark.unit
def test_open__list_at_limit_in_train_mode__raises_not_guesses() -> None:
    # A list cut at the gh limit could hide train:1; the train must refuse to decide.
    prs = [_pr(n) for n in range(1, 101)]
    with pytest.raises(InputError):
        decide(prs, None, True)
    assert decide(prs, None, False)["mode"] == "update"


# Rule 2: merge gates.


@pytest.mark.unit
def test_train__all_gates_pass__merge_head_pinned_to_sha() -> None:
    result = decide([_ready(9), _ready(10, train=2)], _ok(9), True)
    assert (result["action"], result["pr"], result["head_sha"]) == ("merge", 9, _sha(9))


@pytest.mark.unit
def test_train__draft_head__halts_never_merges() -> None:
    prs = [_ready(9, state="CLEAN")]
    prs[0]["isDraft"] = True
    assert decide(prs, _ok(9), True)["action"] == "halt"


@pytest.mark.unit
@pytest.mark.parametrize("state", ["UNSTABLE", "HAS_HOOKS", "DIRTY"])
def test_train__not_clean__halts_not_merged(state: str) -> None:
    assert decide([_ready(9, state=state)], _ok(9), True)["action"] == "halt"


@pytest.mark.unit
def test_train__clean_but_no_checks_reported__halts() -> None:
    assert decide([_ready(9, checks=[])], _ok(9), True)["action"] == "halt"


@pytest.mark.unit
def test_train__failed_non_required_check_on_clean_head__halts() -> None:
    # `gates` is not a required check, so GitHub still says CLEAN. The train must stop.
    result = decide([_ready(9, checks=[_DONE, _failed("gates")])], _ok(9), True)
    assert result["action"] == "halt"
    assert "gates" in result["reason"]


@pytest.mark.unit
def test_train__failed_check_outranks_running_check__halts_not_waits() -> None:
    assert decide([_ready(9, checks=[_failed(), _running()])], _ok(9), True)["action"] == "halt"


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
    assert decide([_ready(9, checks=[check])], _ok(9), True)["action"] == "merge"


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
    assert decide([_ready(9, checks=[_DONE, check])], _ok(9), True)["action"] == "wait"


@pytest.mark.unit
def test_train__running_checks_on_behind_head__waits() -> None:
    result = decide([_ready(9, state="BEHIND", checks=[_DONE, _running()])], _ok(9), True)
    assert result["action"] == "wait"


@pytest.mark.unit
def test_train__behind_head_checks_finished__updated_in_train_mode() -> None:
    result = decide([_ready(9, state="BEHIND")], _ok(9), True)
    assert (result["action"], result["pr"], result["mode"]) == ("update", 9, "train")


@pytest.mark.unit
def test_train__blocked_with_green_checks__halts_unresolved_conversation() -> None:
    assert decide([_ready(9, state="BLOCKED")], _ok(9), True)["action"] == "halt"


@pytest.mark.unit
def test_train__blocked_while_checks_run__waits() -> None:
    result = decide([_ready(9, state="BLOCKED", checks=[_running()])], _ok(9), True)
    assert result["action"] == "wait"


@pytest.mark.unit
@pytest.mark.parametrize("state", ["UNKNOWN", "XYZ"])
def test_train__unknown_or_unexpected_state__halts(state: str) -> None:
    assert decide([_ready(9, state=state)], _ok(9), True)["action"] == "halt"


# Rule 3: the exact-commit label and the verification result.


@pytest.mark.unit
def test_label__missing__halts() -> None:
    result = decide([_pr(9, "CLEAN", labels=("train:1",))], _ok(9), True)
    assert result["action"] == "halt"
    assert "reviewed" in result["reason"]


@pytest.mark.unit
@pytest.mark.parametrize(
    "label",
    [
        "reviewed:" + "AB" * 20,  # upper case
        "reviewed:" + "ab" * 19,  # 38 hex
        "reviewed:" + "ab" * 21,  # 42 hex
        "reviewed:" + "ab" * 10,  # the retired 20-hex patch id
        "reviewed:" + "zz" * 20,  # not hex
        "reviewed:" + "ab" * 20 + " ",  # trailing space
    ],
    ids=["upper", "38", "42", "20-retired", "not-hex", "trailing-space"],
)
def test_label__not_exactly_the_regex__halts(label: str) -> None:
    result = decide([_pr(9, "CLEAN", labels=("train:1", label))], _ok(9), True)
    assert result["action"] == "halt"


@pytest.mark.unit
def test_label__two_reviewed_labels__halts() -> None:
    prs = [_pr(9, "CLEAN", labels=("train:1", f"reviewed:{REVIEWED}", "reviewed:" + "cd" * 20))]
    assert decide(prs, _ok(9), True)["action"] == "halt"


@pytest.mark.unit
def test_verify__no_head_check__halts() -> None:
    # Verification not run, or it failed in the workflow: the head halts, never merges.
    result = decide([_ready(9)], None, True)
    assert result["action"] == "halt"
    assert "verify" in result["reason"]


@pytest.mark.unit
def test_verify__failed__halts_with_its_reason() -> None:
    check = {"pr": 9, "ok": False, "reason": "update overlaps the PR's own edits"}
    result = decide([_ready(9)], check, True)
    assert result["action"] == "halt"
    assert "overlaps" in result["reason"]


@pytest.mark.unit
def test_verify__ok_for_another_pr__halts() -> None:
    # The verification must be for this head, not a stale or other PR's result.
    assert decide([_ready(9)], _ok(10), True)["action"] == "halt"


@pytest.mark.unit
def test_verify__ok_not_true__halts() -> None:
    assert decide([_ready(9)], {"pr": 9, "ok": "yes", "reason": ""}, True)["action"] == "halt"


# Halts on the batch shape.


@pytest.mark.unit
def test_train__duplicate_train_numbers__halts_on_second_pr() -> None:
    result = decide([_ready(9, train=1), _ready(10, train=1)], _ok(9), True)
    assert (result["action"], result["pr"]) == ("halt", 10)


@pytest.mark.unit
def test_train__missing_train_number__halts_on_head() -> None:
    result = decide([_ready(9, train=1), _ready(10, train=3)], _ok(9), True)
    assert result["action"] == "halt"
    assert "train:2" in result["reason"]


@pytest.mark.unit
@pytest.mark.parametrize("label", ["train:x", "train:1000", "train:", "TRAIN:1", "train:1 "])
def test_train__malformed_train_label__halts(label: str) -> None:
    prs = [_pr(9, labels=(label,)), _ready(10, train=2)]
    result = decide(prs, _ok(10), True)
    assert (result["action"], result["pr"]) == ("halt", 9)


@pytest.mark.unit
def test_train__two_train_labels_on_one_pr__halts() -> None:
    assert decide([_pr(9, labels=("train:1", "train:2"))], _ok(9), True)["action"] == "halt"


@pytest.mark.unit
@pytest.mark.parametrize("on", [True, False])
def test_train__train_halted_anywhere__nothing_runs(on: bool) -> None:
    prs = [_ready(9), _pr(20, labels=("train:halted",))]
    result = decide(prs, _ok(9), on)
    assert (result["action"], result["pr"]) == ("none", None)
    assert "#20" in result["reason"]


@pytest.mark.unit
def test_train__halted_closed_pr__does_not_stop_the_train() -> None:
    prs = [_ready(9), _pr(20, labels=("train:halted",), pr_state="CLOSED")]
    assert decide(prs, _ok(9), True)["action"] == "merge"


# Merge-train off and update-only mode.


@pytest.mark.unit
def test_train__merge_train_off__head_ready_is_never_merged() -> None:
    result = decide([_ready(9)], _ok(9), False)
    assert (result["action"], result["mode"]) == ("none", "update")


@pytest.mark.unit
def test_train__merge_train_off__train_labelled_head_updated_as_today() -> None:
    result = decide([_ready(9, state="BEHIND")], None, False)
    assert (result["action"], result["pr"], result["mode"]) == ("update", 9, "update")


@pytest.mark.unit
def test_train__no_train_labels__update_only_rule() -> None:
    result = decide([_pr(5), _pr(6, "CLEAN")], None, True)
    assert (result["action"], result["pr"], result["mode"]) == ("update", 5, "update")


@pytest.mark.unit
def test_train__no_train_labels__clean_head_is_not_merged() -> None:
    prs = [_pr(5, "CLEAN", labels=(f"reviewed:{REVIEWED}",))]
    assert decide(prs, None, True)["action"] == "none"


# Candidate: which head the workflow must verify.


@pytest.mark.unit
def test_candidate__lowest_train_head_with_its_shas() -> None:
    prs = [_pr(3, labels=("train:2",)), _pr(9, labels=("train:1", f"reviewed:{REVIEWED}"))]
    assert candidate(prs, True) == (9, _sha(9), REVIEWED)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("prs", "on"),
    [
        ([_ready(9)], False),
        ([_pr(5)], True),
        ([_ready(9), _pr(20, labels=("train:halted",))], True),
        ([_pr(9, labels=("train:x",))], True),
        ([_pr(9, labels=("train:1",))], True),  # no reviewed label: nothing to verify
        ([], True),
    ],
    ids=["merge-off", "no-train", "halted", "malformed", "no-reviewed", "empty"],
)
def test_candidate__no_head_to_verify__none(prs: list[dict[str, Any]], on: bool) -> None:
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
        decide(bad, None, True)


# Sanitising untrusted text (a check name is set by the PR's own CI).

_HOSTILE = "x\n::add-mask::secret @owner [l](u) `cmd` <b>"


@pytest.mark.unit
def test_sanitise__hostile_check_name__cannot_inject_commands_or_markup() -> None:
    result = decide([_ready(9, checks=[_failed(_HOSTILE)])], _ok(9), True)
    reason = result["reason"]
    assert result["action"] == "halt"
    for bad in ("\n", "\r", "::", "@", "[", "]", "`", "<", ">"):
        assert bad not in reason, bad
    assert "add-mask" in reason  # the text survives, neutralised


@pytest.mark.unit
def test_sanitise__long_check_name__truncated_to_60() -> None:
    result = decide([_ready(9, checks=[_failed("n" * 300)])], _ok(9), True)
    assert "n" * 61 not in result["reason"]
    assert "n" * 60 in result["reason"]


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
def test_cli__candidate_and_decide_round_trip(tmp_path: Path) -> None:
    prs = json.dumps([_ready(9)])
    closed_file = tmp_path / "closed.json"
    closed_file.write_text("[]", encoding="utf-8")
    check_file = tmp_path / "verify.json"
    check_file.write_text(json.dumps({"ok": True}), encoding="utf-8")

    def run(*args: str) -> str:
        return subprocess.run(
            [sys.executable, str(_SCRIPT), *args],
            input=prs,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    assert run("candidate", "--merge-train", "on") == f"9 {_sha(9)} {REVIEWED}\n"
    assert run("candidate", "--merge-train", "off") == ""
    out = json.loads(
        run(
            "decide", "--merge-train", "on", "--closed", str(closed_file),
            "--head-pr", "9", "--head-check", str(check_file),
        )
    )  # fmt: skip
    assert (out["action"], out["pr"]) == ("merge", 9)
    # No verification: the head halts.
    halted = json.loads(run("decide", "--merge-train", "on", "--closed", str(closed_file)))
    assert halted["action"] == "halt"
    # Verification supplied but no closed list in train mode: halts.
    no_closed = json.loads(
        run("decide", "--merge-train", "on", "--head-pr", "9", "--head-check", str(check_file))
    )
    assert no_closed["action"] == "halt"


@pytest.mark.integration
def test_cli__unset_variable_means_off() -> None:
    proc = subprocess.run(
        [sys.executable, str(_SCRIPT), "decide"],
        input=json.dumps([_ready(9)]),
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(proc.stdout)["action"] != "merge"
