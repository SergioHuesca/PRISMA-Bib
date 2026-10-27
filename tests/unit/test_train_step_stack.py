"""Stacked heads in the merge-train decision (ADR 0037 PR 2, ``scripts/train_step.py``).

Pure unit tests: no network, no git, an injected ``now`` in UTC. They cover visibility of
lists that are not filtered by base, the pending wait, path selection by the stack record,
and the ``audit-stop`` subcommand.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "train_step.py"
_spec = importlib.util.spec_from_file_location("train_step_stack", _SCRIPT)
assert _spec is not None and _spec.loader is not None
ts = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ts)

R_CHILD = "ab" * 20
R_PARENT = "cd" * 20
P_FINAL = "ef" * 20
M_PARENT = "12" * 20
_DONE = {"__typename": "CheckRun", "name": "fast", "status": "COMPLETED", "conclusion": "SUCCESS"}
MERGED_AT = "2026-10-27T01:00:00Z"
NOW_2MIN = "2026-10-27T01:02:00Z"
NOW_9MIN = "2026-10-27T01:09:59Z"
NOW_10MIN = "2026-10-27T01:10:00Z"
NOW_11MIN = "2026-10-27T01:11:00Z"


def _pr(
    number: int,
    train: int | None,
    *,
    base: str | None = None,
    state: str = "CLEAN",
    reviewed: str | None = R_CHILD,
    draft: bool = False,
) -> dict[str, Any]:
    labels = ([f"reviewed:{reviewed}"] if reviewed else []) + (
        [f"train:{train}"] if train is not None else []
    )
    pr: dict[str, Any] = {
        "number": number,
        "state": "OPEN",
        "isDraft": draft,
        "mergeStateStatus": state,
        "headRefOid": f"{number:040x}",
        "labels": [{"name": n} for n in labels],
        "statusCheckRollup": [_DONE],
    }
    if base is not None:
        pr["baseRefName"] = base
    return pr


def _facts(
    pr: int = 2,
    now: str = NOW_2MIN,
    *,
    position: int | None = 2,
    parent_overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    stack = (
        None
        if position is None
        else {"id": 11, "number": 4, "position": position, "size": max(position, 2)}
    )
    parent: dict[str, Any] | None = None
    if position is not None and position >= 2:
        parent = {
            "number": 1,
            "merged": True,
            "merged_at": MERGED_AT,
            "merge_commit_sha": M_PARENT,
            "in_main": True,
            "reviewed": [f"reviewed:{R_PARENT}", "other"],
            "head_sha": P_FINAL,
            **(parent_overrides or {}),
        }
    return {"pr": pr, "now": now, "stack": stack, "parent": parent}


def _bad(number: int, reason: str = "rule h failed") -> dict[str, Any]:
    return {"pr": number, "ok": False, "reason": reason}


def _ok(number: int) -> dict[str, Any]:
    return {"pr": number, "ok": True, "reason": ""}


def _decide(
    prs: list[dict[str, Any]],
    check: dict[str, Any] | None,
    facts: Any = None,
    *,
    stacks: bool = True,
    closed: list[dict[str, Any]] | None = (),  # type: ignore[assignment]
) -> dict[str, Any]:
    return ts.decide(prs, check, True, None if closed is None else list(closed), facts, stacks)


def _child(**kw: Any) -> dict[str, Any]:
    kw.setdefault("base", "main")
    return _pr(2, 1, **kw)


# Visibility: lists that are not filtered by base.


@pytest.mark.unit
def test_visibility__children_on_parent_branch_after_a_merged__wait_on_b_never_merge_d() -> None:
    # A merged; B (train:1) and C (train:2) still show the old parent branch as base for a
    # moment; D (train:4) is independent. With --base main the list is only D.
    prs = [
        _pr(20, 2, base="feat/a"),
        _pr(21, 3, base="feat/b"),
        _pr(40, 4, base="main"),
    ]
    facts = _facts(pr=20, now=NOW_2MIN)
    out = _decide(prs, None, facts)
    assert (out["pr"], out["action"]) == (20, "wait")
    assert out["action"] != "merge"
    assert out["mode"] == "train"


@pytest.mark.unit
def test_visibility__lowest_train_is_head_whatever_its_base() -> None:
    prs = [_pr(40, 4, base="main"), _pr(20, 2, base="feat/a"), _pr(30, 3)]
    # Not stacked, base is not main: halts on #20, not #40.
    out = _decide(prs, _ok(20))
    assert (out["pr"], out["action"]) == (20, "halt")
    assert "base" in out["reason"]


@pytest.mark.unit
def test_visibility__missing_base_means_main() -> None:
    out = _decide([_pr(5, 1)], _ok(5))
    assert out["action"] == "merge"
    assert "verifier_args" not in out


@pytest.mark.unit
def test_visibility__closed_list_has_no_base_filter() -> None:
    # A closed-unmerged train:1 PR whose base is a stack branch still breaks the batch.
    closed = [
        {
            "number": 9,
            "labels": [{"name": "train:1"}],
            "mergedAt": None,
            "baseRefName": "feat/a",
        }
    ]
    out = _decide([_pr(10, 2, base="main")], _ok(10), closed=closed)
    assert out["action"] == "halt"
    assert "#9" in out["reason"]


@pytest.mark.unit
def test_visibility__update_only_filters_main_inside_select_next_pr() -> None:
    behind_stack = _pr(3, None, base="feat/a", state="BEHIND")
    behind_main = _pr(7, None, base="main", state="BEHIND")
    out = ts.decide([behind_stack, behind_main], None, False)
    # The lowest-numbered PR targeting main is #7, even though #3 is lower.
    assert (out["action"], out["pr"]) == ("update", 7)


@pytest.mark.unit
def test_visibility__bad_base_type_raises() -> None:
    pr = _pr(5, 1)
    pr["baseRefName"] = 3
    with pytest.raises(ts.InputError):
        _decide([pr], _ok(5))


# Pending wait.


@pytest.mark.unit
def test_pending__parent_merged_2_min_ago_verifier_not_ok__waits() -> None:
    out = _decide([_child()], _bad(2), _facts(now=NOW_2MIN))
    assert (out["action"], out["pr"]) == ("wait", 2)


@pytest.mark.unit
def test_pending__parent_merged_11_min_ago_verifier_not_ok__halts_with_reason() -> None:
    out = _decide([_child()], _bad(2, "tree differs"), _facts(now=NOW_11MIN))
    assert out["action"] == "halt"
    assert "tree differs" in out["reason"]


@pytest.mark.unit
def test_pending__window_boundary_is_10_minutes() -> None:
    assert _decide([_child()], _bad(2), _facts(now=NOW_9MIN))["action"] == "wait"
    assert _decide([_child()], _bad(2), _facts(now=NOW_10MIN))["action"] == "halt"


@pytest.mark.unit
def test_pending__in_main_false_waits_even_when_verifier_says_ok() -> None:
    facts = _facts(parent_overrides={"in_main": False})
    out = _decide([_child()], _ok(2), facts)
    assert out["action"] == "wait"
    late = _facts(now=NOW_11MIN, parent_overrides={"in_main": False})
    out = _decide([_child()], _ok(2), late)
    assert out["action"] == "halt"
    assert "origin/main" in out["reason"]


@pytest.mark.unit
def test_pending__transient_head_base_not_main__waits_then_halts() -> None:
    # #3 right after #2 merged: base is still the branch of #2 and the head is the old one.
    child = _pr(3, 1, base="feat/b")
    facts = _facts(pr=3)
    assert _decide([child], _ok(3), facts)["action"] == "wait"
    late = _facts(pr=3, now=NOW_11MIN)
    assert _decide([child], _ok(3), late)["action"] == "halt"


@pytest.mark.unit
def test_pending__parent_without_valid_label__waits_then_halts() -> None:
    for bad in ([], ["other"], [f"reviewed:{R_PARENT}", f"reviewed:{M_PARENT}"], ["reviewed:abc"]):
        early = _facts(parent_overrides={"reviewed": bad})
        assert _decide([_child()], _ok(2), early)["action"] == "wait"
        late = _facts(now=NOW_11MIN, parent_overrides={"reviewed": bad})
        out = _decide([_child()], _ok(2), late)
        assert out["action"] == "halt"
        assert "verifier_args" not in out


@pytest.mark.unit
def test_pending__verifier_did_not_run__waits_then_halts() -> None:
    assert _decide([_child()], None, _facts())["action"] == "wait"
    assert _decide([_child()], _ok(99), _facts())["action"] == "wait"
    assert _decide([_child()], None, _facts(now=NOW_11MIN))["action"] == "halt"


@pytest.mark.unit
def test_pending__parent_head_unknown__waits_then_halts() -> None:
    early = _facts(parent_overrides={"head_sha": None})
    assert _decide([_child()], _ok(2), early)["action"] == "wait"
    late = _facts(now=NOW_11MIN, parent_overrides={"head_sha": None})
    assert _decide([_child()], _ok(2), late)["action"] == "halt"


@pytest.mark.unit
def test_pending__wait_comes_after_draft_and_label_halts() -> None:
    facts = _facts()
    draft = _decide([_child(draft=True)], _bad(2), facts)
    assert draft["action"] == "halt" and "draft" in draft["reason"]
    nolabel = _decide([_child(reviewed=None)], _bad(2), facts)
    assert nolabel["action"] == "halt" and "reviewed" in nolabel["reason"]


@pytest.mark.unit
def test_pending__wait_comes_before_the_verifier_result_and_checks() -> None:
    # Failing checks and a BEHIND head do not matter while pending: still a wait.
    child = _child(state="BEHIND")
    child["statusCheckRollup"] = [{**_DONE, "conclusion": "FAILURE"}]
    assert _decide([child], _bad(2), _facts())["action"] == "wait"


@pytest.mark.unit
def test_pending__pending_head_is_never_updated() -> None:
    child = _child(state="BEHIND")
    for check in (None, _bad(2)):
        out = _decide([child], check, _facts())
        assert out["action"] not in ("update", "merge")
    # Once the verifier is ok, BEHIND updates as for any head.
    assert _decide([child], _ok(2), _facts())["action"] == "update"


@pytest.mark.unit
def test_pending__now_before_merged_at_is_still_a_wait() -> None:
    out = _decide([_child()], _bad(2), _facts(now="2026-10-27T00:59:00Z"))
    assert out["action"] == "wait"


# Path selection, by the stack record only.


@pytest.mark.unit
def test_path__position_2_merged_parent__stacked_call_with_right_arguments() -> None:
    out = _decide([_child()], _ok(2), _facts())
    assert out["action"] == "merge"
    assert out["verifier_args"] == [
        "--parent-reviewed",
        R_PARENT,
        "--parent-head",
        P_FINAL,
        "--parent-merge",
        M_PARENT,
    ]


@pytest.mark.unit
def test_path__stacked_ready_head_still_needs_clean_and_checks() -> None:
    running = _child(state="CLEAN")
    running["statusCheckRollup"] = [{**_DONE, "status": "IN_PROGRESS"}]
    assert _decide([running], _ok(2), _facts())["action"] == "wait"
    out = _decide([_child(state="DIRTY")], _ok(2), _facts())
    assert out["action"] == "halt"


@pytest.mark.unit
def test_path__no_stack_record__existing_path_whatever_the_head_looks_like() -> None:
    # X1: an old head plus an update merge from main looks the same as any other head. The
    # record says "not stacked", so the existing path decides, with no stacked arguments.
    for facts in (None, _facts(position=None)):
        out = _decide([_child(state="BEHIND")], _ok(2), facts)
        assert out["action"] == "update"
        assert "verifier_args" not in out


@pytest.mark.unit
def test_path__record_says_stacked__verifier_not_ok_is_not_judged_by_shape() -> None:
    # X1, other direction: the stack record chooses the stacked path, so a head whose
    # verification failed is not merged because it "looks" like an ordinary head.
    out = _decide([_child()], _bad(2), _facts(now=NOW_11MIN))
    assert out["action"] == "halt"


@pytest.mark.unit
def test_path__position_1__existing_verifier_path() -> None:
    facts = _facts(position=1)
    assert _decide([_child()], _ok(2), facts)["action"] == "merge"
    assert "verifier_args" not in _decide([_child()], _ok(2), facts)
    out = _decide([_child()], _bad(2), facts)
    assert out["action"] == "halt"  # no pending window at position 1


@pytest.mark.unit
def test_path__position_3_merged_parent__halts_three_level() -> None:
    out = _decide([_child()], _ok(2), _facts(position=3))
    assert out["action"] == "halt"
    assert "three-level stacks are not enabled" in out["reason"]


@pytest.mark.unit
@pytest.mark.parametrize("position", [1, 2, 3])
def test_path__stacks_off__any_stacked_head_halts(position: int) -> None:
    out = _decide([_child()], _ok(2), _facts(position=position), stacks=False)
    assert out["action"] == "halt"
    assert "MERGE_TRAIN_STACKS" in out["reason"]


@pytest.mark.unit
def test_path__stacks_off_but_no_record__unchanged() -> None:
    assert _decide([_child()], _ok(2), None, stacks=False)["action"] == "merge"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("state", "word"), [("OPEN", "still open"), ("CLOSED", "closed unmerged"), (None, "not merged")]
)
def test_path__parent_not_merged__halts(state: str | None, word: str) -> None:
    over = {"merged": False, "merged_at": None, "merge_commit_sha": None, "state": state}
    out = _decide([_child()], _ok(2), _facts(parent_overrides=over))
    assert out["action"] == "halt"
    assert word in out["reason"]


@pytest.mark.unit
def test_path__position_3_with_open_parent_names_the_parent() -> None:
    over = {"merged": False, "merged_at": None, "merge_commit_sha": None, "state": "OPEN"}
    out = _decide([_child()], _ok(2), _facts(position=3, parent_overrides=over))
    assert out["action"] == "halt"
    assert "still open" in out["reason"]


# audit-stop.


def _audit(merged: int = 10, failed: bool = True) -> dict[str, Any]:
    return {"merged_pr": merged, "failed": failed, "reason": "tree differs"}


@pytest.mark.unit
def test_audit_stop__open_successor_is_chosen() -> None:
    prs = [_pr(30, 3), _pr(20, 2), _pr(40, None)]
    assert ts.audit_stop(_audit(10), prs) == 20


@pytest.mark.unit
def test_audit_stop__stale_list_with_the_merged_pr_does_not_pick_it() -> None:
    prs = [_pr(10, 1), _pr(20, 2)]
    assert ts.audit_stop(_audit(10), prs) == 20
    closed = _pr(10, 1)
    closed["state"] = "MERGED"
    assert ts.audit_stop(_audit(10), [closed, _pr(20, 2)]) == 20


@pytest.mark.unit
def test_audit_stop__target_is_always_an_open_pr() -> None:
    prs = [_pr(10, 1), {**_pr(15, 2), "state": "CLOSED"}, _pr(20, 3)]
    target = ts.audit_stop(_audit(10), prs)
    assert target == 20
    assert next(p for p in prs if p["number"] == target)["state"] == "OPEN"


@pytest.mark.unit
def test_audit_stop__none_when_no_open_successor() -> None:
    assert ts.audit_stop(_audit(10), []) is None
    assert ts.audit_stop(_audit(10), [_pr(10, 1)]) is None
    assert ts.audit_stop(_audit(10), [_pr(5, None)]) is None


@pytest.mark.unit
def test_audit_stop__audit_not_failed__none() -> None:
    assert ts.audit_stop(_audit(10, failed=False), [_pr(20, 2)]) is None


# InputError cases and the CLI.


def _run(tmp_path: Path, argv: list[str], stdin: Any, monkeypatch: pytest.MonkeyPatch) -> int:
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(stdin)))
    return int(ts.main(argv))


def _write(tmp_path: Path, name: str, data: Any) -> str:
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8", newline="\n")
    return str(path)


def _mut(**path_value: Any) -> dict[str, Any]:
    facts = _facts()
    for dotted, value in path_value.items():
        node = facts
        *parents, last = dotted.split("__")
        for key in parents:
            node = node[key]
        node[last] = value
    return facts


_BAD_FACTS = [
    pytest.param(_mut(pr=3), id="pr-not-head"),
    pytest.param(_mut(pr="2"), id="pr-str"),
    pytest.param(_mut(pr=True), id="pr-bool"),
    pytest.param(_mut(now="2026-10-27 01:02:00"), id="now-format"),
    pytest.param(_mut(now="2026-13-27T01:02:00Z"), id="now-month"),
    pytest.param(_mut(now=5), id="now-int"),
    pytest.param(_mut(stack__position=0), id="position-0"),
    pytest.param(_mut(stack__position="2"), id="position-str"),
    pytest.param(_mut(stack__position=5), id="position-above-size"),
    pytest.param(_mut(parent=None), id="position-2-without-parent"),
    pytest.param(_mut(parent__merged="yes"), id="merged-str"),
    pytest.param(_mut(parent__in_main=None), id="in-main-null"),
    pytest.param(_mut(parent__merged_at="yesterday"), id="merged-at"),
    pytest.param(_mut(parent__merged_at=None), id="merged-without-merged-at"),
    pytest.param(_mut(parent__merge_commit_sha="abc"), id="sha-short"),
    pytest.param(_mut(parent__merge_commit_sha="AB" * 20), id="sha-upper"),
    pytest.param(_mut(parent__merge_commit_sha=None), id="merged-without-sha"),
    pytest.param(_mut(parent__head_sha="zz" * 20), id="head-sha"),
    pytest.param(_mut(parent__reviewed="reviewed:x"), id="reviewed-str"),
    pytest.param(_mut(parent__reviewed=[1]), id="reviewed-int"),
    pytest.param(_mut(parent__state="MERGED"), id="state"),
    pytest.param(_mut(parent__number=2), id="parent-is-head"),
    pytest.param({**_facts(position=None), "parent": {"number": 1}}, id="parent-without-stack"),
    pytest.param({**_facts(position=1), "parent": _facts()["parent"]}, id="parent-at-position-1"),
    pytest.param([], id="not-an-object"),
]


@pytest.mark.unit
@pytest.mark.parametrize("facts", _BAD_FACTS)
def test_facts__inconsistent__raises_input_error(facts: Any) -> None:
    with pytest.raises(ts.InputError):
        _decide([_child()], _ok(2), facts)


@pytest.mark.unit
def test_facts__valid_but_unused_record_for_other_pr_still_raises() -> None:
    # The record is for the head only; a record for another PR is an inconsistency.
    with pytest.raises(ts.InputError):
        _decide([_pr(5, 1)], _ok(5), _facts(pr=2))


@pytest.mark.unit
def test_cli__bad_facts_exits_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    facts = _write(tmp_path, "facts.json", _mut(now="soon"))
    closed = _write(tmp_path, "closed.json", [])
    check = _write(tmp_path, "check.json", {"ok": True})
    argv = ["decide", "--merge-train", "on", "--merge-train-stacks", "on", "--closed", closed]
    argv += ["--head-pr", "2", "--head-check", check, "--facts", facts]
    assert _run(tmp_path, argv, [_child()], monkeypatch) == 2


@pytest.mark.unit
def test_cli__unparseable_facts_file_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bad = tmp_path / "facts.json"
    bad.write_text("{not json", encoding="utf-8", newline="\n")
    closed = _write(tmp_path, "closed.json", [])
    argv = ["decide", "--merge-train", "on", "--closed", closed, "--facts", str(bad)]
    assert _run(tmp_path, argv, [_child()], monkeypatch) == 2


@pytest.mark.unit
def test_cli__stacked_decision_prints_verifier_args(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    facts = _write(tmp_path, "facts.json", _facts())
    closed = _write(tmp_path, "closed.json", [])
    check = _write(tmp_path, "check.json", {"ok": True})
    argv = ["decide", "--merge-train", "on", "--merge-train-stacks", "on", "--closed", closed]
    argv += ["--head-pr", "2", "--head-check", check, "--facts", facts]
    assert _run(tmp_path, argv, [_child()], monkeypatch) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["action"] == "merge"
    assert out["verifier_args"][1] == R_PARENT


@pytest.mark.unit
def test_cli__stacks_default_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    facts = _write(tmp_path, "facts.json", _facts())
    closed = _write(tmp_path, "closed.json", [])
    argv = ["decide", "--merge-train", "on", "--closed", closed, "--facts", facts]
    assert _run(tmp_path, argv, [_child()], monkeypatch) == 0
    assert json.loads(capsys.readouterr().out)["action"] == "halt"


@pytest.mark.unit
def test_cli__audit_stop_prints_open_successor_or_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    audit = _write(tmp_path, "audit.json", _audit(10))
    prs = _write(tmp_path, "prs.json", [_pr(10, 1), _pr(20, 2)])
    assert _run(tmp_path, ["audit-stop", "--audit", audit, "--prs", prs], None, monkeypatch) == 0
    assert capsys.readouterr().out.strip() == "20"
    # From stdin, with no open successor.
    assert _run(tmp_path, ["audit-stop", "--audit", audit], [_pr(10, 1)], monkeypatch) == 0
    assert capsys.readouterr().out.strip() == "none"


@pytest.mark.unit
@pytest.mark.parametrize(
    "audit",
    [
        [],
        {"merged_pr": 10, "failed": True},
        {"merged_pr": "10", "failed": True, "reason": "x"},
        {"merged_pr": 0, "failed": True, "reason": "x"},
        {"merged_pr": 10, "failed": "yes", "reason": "x"},
        {"merged_pr": 10, "failed": True, "reason": 5},
    ],
)
def test_cli__audit_stop_malformed_audit_exits_2(
    audit: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, "audit.json", audit)
    assert _run(tmp_path, ["audit-stop", "--audit", path], [_pr(20, 2)], monkeypatch) == 2


@pytest.mark.unit
def test_cli__audit_stop_malformed_pr_list_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _write(tmp_path, "audit.json", _audit(10))
    assert _run(tmp_path, ["audit-stop", "--audit", path], {"not": "a list"}, monkeypatch) == 2


# Review follow-ups.


@pytest.mark.unit
def test_path__position_1_with_non_main_base__halts() -> None:
    # Without this halt the head would take the old verifier path and be squash-merged
    # into a non-main base branch.
    out = _decide([_pr(2, 1, base="feature")], _ok(2), _facts(position=1))
    assert out["action"] == "halt"
    assert "position 1" in out["reason"]


@pytest.mark.unit
@pytest.mark.parametrize("content", ["null", "[]", "1", '"x"', "true", "false", "{}"])
def test_cli__facts_file_present_but_not_a_usable_object__exits_2(
    content: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # null is not "no facts": a failed API call must not judge a stacked child as plain.
    facts = tmp_path / "facts.json"
    facts.write_text(content, encoding="utf-8", newline="\n")
    closed = _write(tmp_path, "closed.json", [])
    check = _write(tmp_path, "check.json", {"ok": True})
    argv = ["decide", "--merge-train", "on", "--closed", closed, "--head-pr", "2"]
    argv += ["--head-check", check, "--facts", str(facts)]
    assert _run(tmp_path, argv, [_child()], monkeypatch) == 2


@pytest.mark.unit
def test_cli__deeply_nested_facts_and_audit_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deep = tmp_path / "deep.json"
    deep.write_text("[" * 100000, encoding="utf-8", newline="\n")
    closed = _write(tmp_path, "closed.json", [])
    argv = ["decide", "--merge-train", "on", "--closed", closed, "--facts", str(deep)]
    assert _run(tmp_path, argv, [_child()], monkeypatch) == 2
    assert _run(tmp_path, ["audit-stop", "--audit", str(deep)], [_pr(20, 2)], monkeypatch) == 2
    audit = _write(tmp_path, "audit.json", _audit(10))
    argv = ["audit-stop", "--audit", audit, "--prs", str(deep)]
    assert _run(tmp_path, argv, None, monkeypatch) == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    "facts",
    [
        # Each is otherwise consistent when True is read as 1 (the parent is not the head).
        _mut(pr=True, parent__number=5),
        {**_mut(stack__position=True), "parent": None},
        {**_mut(stack__position=True, stack__size=True), "parent": None},
    ],
)
def test_facts__bool_for_int__raises_input_error(facts: Any) -> None:
    # bool is an int subclass: `pr: true` must not read as 1.
    with pytest.raises(ts.InputError):
        _decide([_pr(1, 1, base="main")], _ok(1), facts)
