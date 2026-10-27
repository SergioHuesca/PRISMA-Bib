"""The facts assembler of ADR 0037 PR 4 (``scripts/train_facts.py``).

No network, no real git: a path-keyed fake runner stands in for ``gh api`` and a recording fake
for git. ``now`` is injected. The record is also fed to the real ``train_step.decide`` parser.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

_SCRIPTS = Path(__file__).parent.parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))
_spec = importlib.util.spec_from_file_location("train_facts", _SCRIPTS / "train_facts.py")
assert _spec is not None and _spec.loader is not None
tf = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tf)
import train_step  # noqa: E402

REPO = "own/repo"
NOW = datetime(2026, 10, 27, 1, 30, 0, tzinfo=UTC)
H = "a1" * 20
RC = "b2" * 20
PH = "c3" * 20
RP = "d4" * 20
MP = "e5" * 20
TOKEN = "ghs_SENTINELTOKEN123"  # pragma: allowlist secret


def pr_rec(number: int, head: str = H, labels: list[str] | None = None, **extra: Any) -> dict:
    return {
        "number": number,
        "head": {"sha": head},
        "labels": [{"name": n} for n in (labels if labels is not None else [])],
        **extra,
    }


STACK2 = {"id": 9, "number": 4, "position": 2, "size": 3}
STACK_REC = {"pull_requests": [{"number": 177}, {"number": 178}, {"number": 179}]}


def child(stack: Any = STACK2, labels: list[str] | None = None) -> dict:
    rec = pr_rec(178, H, [f"reviewed:{RC}"] if labels is None else labels)
    if stack != "absent":
        rec["stack"] = stack
    return rec


def parent(**over: Any) -> dict:
    rec = pr_rec(
        177,
        PH,
        [f"reviewed:{RP}", "train:1"],
        merged=True,
        state="closed",
        merged_at="2026-10-27T01:25:58Z",
        merge_commit_sha=MP,
    )
    rec.update(over)
    return rec


class Fake:
    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        self.calls.append((method, path, body))
        reply = self.routes[path]
        if isinstance(reply, Exception):
            raise reply
        return reply if isinstance(reply, tuple) else (200, reply)


class Git:
    """Scripted git: ancestor answers consumed in order; records every call."""

    def __init__(self, *ancestor: int, fetch: int = 0) -> None:
        self.answers = list(ancestor)
        self.fetch = fetch
        self.calls: list[list[str]] = []

    def __call__(self, args: list[str]) -> int:
        self.calls.append(args)
        if args[0] == "fetch":
            return self.fetch
        return self.answers.pop(0)


def routes(head: Any = None, stack_rec: Any = STACK_REC, par: Any = None) -> dict[str, Any]:
    return {
        f"/repos/{REPO}/pulls/178": child() if head is None else head,
        f"/repos/{REPO}/stacks/4": stack_rec,
        f"/repos/{REPO}/pulls/177": parent() if par is None else par,
    }


def build(r: dict[str, Any], git: Git | None = None):
    fake = Fake(r)
    out = tf.build(REPO, 178, runner=fake, git=git or Git(0), now=lambda: NOW)
    return out, fake


# ---- not stacked, position 1 -------------------------------------------------------------


@pytest.mark.parametrize("stack", ["absent", None])
def test_not_stacked__absent_and_null_alike__no_further_calls(stack):
    (rec, shas), fake = build({f"/repos/{REPO}/pulls/178": child(stack)})
    assert rec == {"pr": 178, "now": "2026-10-27T01:30:00Z", "stack": None, "parent": None}
    assert shas == [RC]
    assert [c[1] for c in fake.calls] == [f"/repos/{REPO}/pulls/178"]
    assert all(c[0] == "GET" for c in fake.calls)


def test_position_1__no_parent_no_stack_call():
    one = {"id": 9, "number": 4, "position": 1, "size": 3}
    (rec, shas), fake = build({f"/repos/{REPO}/pulls/178": child(one)})
    assert rec["stack"] == one and rec["parent"] is None and shas == [RC]
    assert len(fake.calls) == 1


# ---- position 2 --------------------------------------------------------------------------


def test_position_2_merged_parent__full_record_and_shas():
    (rec, shas), _ = build(routes(), Git(0))
    assert rec["stack"] == STACK2
    assert rec["parent"] == {
        "number": 177,
        "merged": True,
        "merged_at": "2026-10-27T01:25:58Z",
        "merge_commit_sha": MP,
        "in_main": True,
        "reviewed": [f"reviewed:{RP}"],
        "head_sha": PH,
        "state": "CLOSED",
    }
    assert shas == [RC, RP, PH, MP]
    # the record is what decide's strict parser accepts
    parsed = train_step._parse_facts(json.loads(json.dumps(rec)))
    assert parsed["parent"]["in_main"] is True


def test_stack_entry_keys_not_trusted_for_labels__parent_labels_come_from_the_pull():
    # the /stacks entry carries no labels or merge sha; extra keys are ignored
    rec = {"pull_requests": [{"number": 177, "merge_commit_sha": "x"}, {"number": 178}]}
    (out, _), _ = build(routes(stack_rec=rec))
    assert out["parent"]["merge_commit_sha"] == MP


def test_unmerged_open_parent__not_in_main_no_git_no_parent_shas():
    par = parent(merged=False, state="open", merged_at=None, merge_commit_sha=None)
    git = Git()
    (rec, shas), _ = build(routes(par=par), git)
    assert rec["parent"]["merged"] is False and rec["parent"]["state"] == "OPEN"
    assert rec["parent"]["in_main"] is False and rec["parent"]["merge_commit_sha"] is None
    assert shas == [RC] and git.calls == []


def test_closed_unmerged_parent__state_closed():
    par = parent(merged=False, state="closed", merged_at=None, merge_commit_sha=None)
    (rec, _), _ = build(routes(par=par), Git())
    assert rec["parent"]["state"] == "CLOSED" and rec["parent"]["merged"] is False


# ---- labels ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("labels", "expect_rp"),
    [
        ([], False),
        ([f"reviewed:{RP}"], True),
        ([f"reviewed:{RP}", f"reviewed:{RC}"], False),
        (["reviewed:XYZ"], False),
        (["reviewed:" + RP.upper()], False),
    ],
)
def test_parent_labels__only_one_valid_gives_r_parent(labels, expect_rp):
    (rec, shas), _ = build(routes(par=parent(labels=[{"name": n} for n in labels])), Git(0))
    assert (RP in shas) is expect_rp
    assert rec["parent"]["reviewed"] == [n for n in labels if n.startswith("reviewed:")]


def test_child_without_single_valid_label__no_r_child():
    (_, shas), _ = build(routes(head=child(labels=[f"reviewed:{RC}", f"reviewed:{RP}"])), Git(0))
    assert RC not in shas


@pytest.mark.parametrize("labels", ["x", [1], [{"name": 1}], None])
def test_malformed_labels__input_error(labels):
    bad = parent()
    bad["labels"] = labels
    with pytest.raises(train_step.InputError):
        build(routes(par=bad), Git(0))


# ---- in_main -----------------------------------------------------------------------------


def test_in_main_false_then_true_after_one_refetch():
    git = Git(1, 0)
    (rec, _), _ = build(routes(), git)
    assert rec["parent"]["in_main"] is True
    assert [c[0] for c in git.calls] == ["merge-base", "fetch", "merge-base"]
    assert git.calls[0] == ["merge-base", "--is-ancestor", MP, "origin/main"]
    assert git.calls[1] == ["fetch", "--no-tags", "--quiet", "origin", "main"]


def test_in_main_false_after_refetch__exactly_one_refetch():
    git = Git(1, 1)
    (rec, _), _ = build(routes(), git)
    assert rec["parent"]["in_main"] is False
    assert [c[0] for c in git.calls].count("fetch") == 1


def test_refetch_fails__in_main_false_and_no_second_probe(capsys):
    git = Git(1, fetch=1)
    (rec, _), _ = build(routes(), git)
    assert rec["parent"]["in_main"] is False and len(git.calls) == 2
    assert "re-fetch" in capsys.readouterr().err


def test_in_main_true_first_time__no_fetch():
    git = Git(0)
    build(routes(), git)
    assert [c[0] for c in git.calls] == ["merge-base"]


# ---- errors ------------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["178", "4", "177"])
def test_404_anywhere__input_error(which):
    r = routes()
    key = {
        "178": f"/repos/{REPO}/pulls/178",
        "4": f"/repos/{REPO}/stacks/4",
        "177": f"/repos/{REPO}/pulls/177",
    }[which]
    r[key] = (404, {"message": "Not Found"})
    with pytest.raises(train_step.InputError, match="404"):
        build(r, Git(0))


def test_runner_exception__scrubbed_input_error(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    r = routes()
    r[f"/repos/{REPO}/pulls/178"] = RuntimeError(f"boom {TOKEN}")
    with pytest.raises(train_step.InputError) as exc:
        build(r)
    assert TOKEN not in str(exc.value) and "***" in str(exc.value)


@pytest.mark.parametrize("payload", [[], "x", None, 7])
def test_non_object_json__input_error(payload):
    with pytest.raises(train_step.InputError):
        build({f"/repos/{REPO}/pulls/178": (200, payload)})


@pytest.mark.parametrize(
    "stack",
    [
        "x",
        {"id": 9, "number": 4, "position": 2},  # size missing
        {"id": 9, "number": 4, "position": "2", "size": 3},
        {"id": 9, "number": 4, "position": True, "size": 3},
        {"id": 9, "number": 4, "position": 0, "size": 3},
        {"id": 9, "number": 4, "position": 4, "size": 3},
        {"number": 4, "position": 2, "size": 3},
    ],
)
def test_bad_stack_object__input_error(stack):
    with pytest.raises(train_step.InputError):
        build({f"/repos/{REPO}/pulls/178": child(stack)})


@pytest.mark.parametrize(
    "stack_rec",
    [
        {"pull_requests": [{"number": 178}, {"number": 179}]},  # no parent entry
        {"pull_requests": [{"number": 177}, {"number": 179}]},  # head missing
        {"pull_requests": [{"number": 178}, {"number": 177}]},  # head at position 1
        {"pull_requests": [{"number": 177}, {"number": 178}, {"number": 178}]},  # twice
        {"pull_requests": [{"number": 177}, {"number": 179}, {"number": 178}]},  # wrong position
        {"pull_requests": "x"},
        {"pull_requests": [None, {"number": 178}]},
        {"pull_requests": [{"number": "177"}, {"number": 178}]},
        {},
    ],
)
def test_stack_record_without_a_usable_parent_entry__input_error(stack_rec):
    with pytest.raises(train_step.InputError):
        build(routes(stack_rec=stack_rec), Git(0))


@pytest.mark.parametrize(
    "over",
    [
        {"number": 999},  # response for another PR
        {"merged": "yes"},
        {"merged": None},
        {"state": "OPEN"},  # REST says lowercase
        {"state": 3},
        {"merged_at": "2026-10-27 01:25:58"},
        {"merged_at": None},
        {"merge_commit_sha": None},
        {"merge_commit_sha": "e5" * 19},
        {"merge_commit_sha": MP.upper()},
        {"head": {"sha": "nothex"}},
        {"head": None},
    ],
)
def test_bad_parent_fields__input_error(over):
    with pytest.raises(train_step.InputError):
        build(routes(par=parent(**over)), Git(0))


@pytest.mark.parametrize("head_sha", ["zz", "A1" * 20, None, 5])
def test_bad_head_sha__input_error(head_sha):
    rec = child()
    rec["head"] = {"sha": head_sha}
    with pytest.raises(train_step.InputError):
        build({f"/repos/{REPO}/pulls/178": rec})


def test_parent_is_the_head__input_error():
    with pytest.raises(train_step.InputError):
        build(routes(stack_rec={"pull_requests": [{"number": 178}, {"number": 178}]}), Git(0))


@pytest.mark.parametrize("repo", ["a", "a/b/c", "../x", "a/..", "a b/c", ""])
def test_bad_repo__input_error(repo):
    with pytest.raises(train_step.InputError):
        tf.build(repo, 178, runner=Fake({}), git=Git(), now=lambda: NOW)


def test_bad_pr__input_error():
    with pytest.raises(train_step.InputError):
        tf.build(REPO, 0, runner=Fake({}), git=Git(), now=lambda: NOW)


def test_shas_are_deduplicated_and_all_40_hex():
    same = parent(head={"sha": RP})  # P_final == R_parent
    (_, shas), _ = build(routes(par=same), Git(0))
    assert len(shas) == len(set(shas))
    assert all(len(s) == 40 and s == s.lower() for s in shas)


# ---- CLI ---------------------------------------------------------------------------------


def test_cli__writes_files_lf_only(tmp_path, monkeypatch):
    fake = Fake(routes())
    monkeypatch.setattr(tf, "gh_runner", fake)
    real_build = tf.build
    monkeypatch.setattr(
        tf, "build", lambda *a, **k: real_build(*a, **{**k, "runner": fake, "git": Git(0)})
    )
    out, shas = tmp_path / "f.json", tmp_path / "s.txt"
    rc = tf.main(
        ["--repo", REPO, "--pr", "178", "--out", str(out), "--shas-out", str(shas),
         "--now", "2026-10-27T01:30:00Z"]
    )  # fmt: skip
    assert rc == 0
    assert json.loads(out.read_text())["now"] == "2026-10-27T01:30:00Z"
    assert shas.read_bytes() == "".join(s + "\n" for s in [RC, RP, PH, MP]).encode()


def test_cli__bad_input_exits_2_scrubbed_and_writes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    fake = Fake({f"/repos/{REPO}/pulls/178": RuntimeError(f"x {TOKEN}")})
    real_build = tf.build
    monkeypatch.setattr(tf, "build", lambda *a, **k: real_build(*a, **{**k, "runner": fake}))
    out, shas = tmp_path / "f.json", tmp_path / "s.txt"
    rc = tf.main(["--repo", REPO, "--pr", "178", "--out", str(out), "--shas-out", str(shas)])
    err = capsys.readouterr().err
    assert rc == 2 and TOKEN not in err and not out.exists() and not shas.exists()


def test_cli__bad_now_exits_2(tmp_path):
    rc = tf.main(
        ["--repo", REPO, "--pr", "1", "--out", str(tmp_path / "a"),
         "--shas-out", str(tmp_path / "b"), "--now", "yesterday"]
    )  # fmt: skip
    assert rc == 2
