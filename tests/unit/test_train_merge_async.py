"""The merge client of ADR 0037 PR 3 (``scripts/train_merge_async.py``).

Pure unit tests: a scripted fake runner stands in for ``gh api``, a fake clock for time, a
fake callable for the audit. No network, no git, never the real clock.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

_SCRIPT = Path(__file__).parent.parent.parent / "scripts" / "train_merge_async.py"
_spec = importlib.util.spec_from_file_location("train_merge_async", _SCRIPT)
assert _spec is not None and _spec.loader is not None
tm = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tm)

H = "ab" * 20
S = "cd" * 20
UUID = "0b3a6b5e-1c2d-4e3f-8a9b-0123456789ab"
REPO = "own/repo"
TOKEN = "ghs_SENTINELTOKEN123"  # pragma: allowlist secret
OPEN = (200, {"merged": False, "head": {"sha": H}})
MERGED_PR = (200, {"merged": True, "head": {"sha": H}, "merge_commit_sha": S})
ACCEPTED = (
    202,
    {"status": "pending", "details": {"uuid": UUID, "expected_head_sha": H}},
)
PENDING = (200, {"status": "pending", "details": {"message": "Merge request is in progress."}})
MERGED = (200, {"status": "merged"})


class Fake:
    """Scripted runner: replies are consumed in order; every call is recorded."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(self, method: str, path: str, body: Any) -> tuple[int, Any]:
        self.calls.append((method, path, body))
        assert self.replies, f"unexpected call {method} {path}"
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


class Clock:
    def __init__(self) -> None:
        self.t = 0.0
        self.sleeps: list[float] = []

    def now(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        assert len(self.sleeps) < 500, "runaway polling"
        self.t += s


def go(
    fake: Fake, clock: Clock | None = None, head: Any = H, ok: bool = True, **kw: Any
) -> dict[str, Any]:
    clock = clock or Clock()
    seen: list[tuple[Any, str]] = []

    def fake_audit(s: Any, h: str) -> dict[str, Any]:
        seen.append((s, h))
        return {"ok": ok, "reason": "fake audit"}

    out = tm.run(
        REPO, 7, head, runner=fake, audit_fn=fake_audit, sleep=clock.sleep, clock=clock.now, **kw
    )
    out["_audited"] = seen
    return out


# ---- inputs refused before any call ---------------------------------------------------


@pytest.mark.parametrize(
    "head", [None, "", "AB" * 20, "ab" * 19, "ab" * 20 + "0", "g" * 40, H.upper()]
)
def test_bad_head__refused_before_any_call(head: Any) -> None:
    fake = Fake()
    with pytest.raises(tm.InputError):
        go(fake, head=head)
    assert fake.calls == []


@pytest.mark.parametrize(
    "repo", ["own", "own/re po", "own/../x", "a;b/c", "own/repo/x", "-x/y", "own/..", "own/repo\n"]
)
def test_bad_repo__refused_before_any_call(repo: str) -> None:
    fake = Fake()
    with pytest.raises(tm.InputError):
        tm.run(repo, 7, H, runner=fake)
    assert fake.calls == []


@pytest.mark.parametrize("pr", [0, -1, True, "7"])
def test_bad_pr__refused(pr: Any) -> None:
    with pytest.raises(tm.InputError):
        tm.run(REPO, pr, H, runner=Fake())


@pytest.mark.parametrize("uuid", ["../x", "abc", f"{UUID}/../../merge", f"{UUID}?x=1", ""])
def test_bad_uuid__halts_before_polling(uuid: str) -> None:
    fake = Fake(OPEN, (202, {"details": {"uuid": uuid}}))
    out = go(fake)
    assert out["outcome"] == "halt"
    assert len(fake.calls) == 2


# ---- the call -------------------------------------------------------------------------


def test_happy_path__sha_always_sent__merged() -> None:
    fake = Fake(OPEN, ACCEPTED, PENDING, MERGED, MERGED_PR)
    out = go(fake)
    assert out["outcome"] == "merged" and out["audit"] == {"ok": True, "reason": "fake audit"}
    assert out["_audited"] == [(S, H)]
    method, path, body = fake.calls[1]
    assert (method, path) == ("PUT", f"/repos/{REPO}/pulls/7/merge-async")
    assert body == {"merge_method": "squash", "sha": H}  # no merge_action
    assert fake.calls[2][:2] == ("GET", f"/repos/{REPO}/pulls/7/merge-async/{UUID}")


def test_400_stale__halts_without_polling() -> None:
    fake = Fake(
        OPEN,
        (
            400,
            {"status": "failed", "details": {"message": "Pull request head branch was modified."}},
        ),
    )
    out = go(fake)
    assert out["outcome"] == "halt" and "modified" in out["reason"] and "400" in out["reason"]
    assert len(fake.calls) == 2 and out["_audited"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"status": "pending", "details": {"message": "exists", "uuid": UUID}},
        {"status": "pending", "details": {"message": "exists"}},
        {"status": "pending", "details": {"uuid": UUID, "expected_head_sha": "ef" * 20}},
        {"status": "pending", "details": {"uuid": UUID, "expected_head_sha": H}},
        None,
    ],
)
def test_409_any_body__wait_never_adopted_no_poll(body: Any) -> None:
    fake = Fake(OPEN, (409, body))
    out = go(fake)
    assert out["outcome"] == "wait" and "next run" in out["reason"]
    assert [c[0] for c in fake.calls] == ["GET", "PUT"] and out["_audited"] == []


@pytest.mark.parametrize(
    "details",
    [
        {"uuid": UUID, "expected_head_sha": "ef" * 20},
        {"uuid": UUID},
        {"uuid": UUID, "expected_head_sha": H.upper()},
        {"uuid": UUID, "expected_head_sha": None},
    ],
)
def test_202_not_pinned_to_h__halts_without_polling(details: Any) -> None:
    fake = Fake(OPEN, (202, {"status": "pending", "details": details}))
    out = go(fake)
    assert out["outcome"] == "halt" and "pinned" in out["reason"]
    assert [c[0] for c in fake.calls] == ["GET", "PUT"]


def test_poll_merged_but_pr_not_merged__halts() -> None:
    out = go(Fake(OPEN, ACCEPTED, MERGED, (200, {"merged": False, "merge_commit_sha": S})))
    assert out["outcome"] == "halt" and out["_audited"] == []


@pytest.mark.parametrize("code", [404, 500, 403, 302])
def test_poll_non_2xx_with_merged_body__halts(code: int) -> None:
    fake = Fake(OPEN, ACCEPTED, (code, {"status": "merged"}))
    out = go(fake)
    assert out["outcome"] == "halt" and len(fake.calls) == 3 and out["_audited"] == []


@pytest.mark.parametrize("word", ["MERGED", "Merged", "PENDING", "Failed", " merged"])
def test_poll_status_is_case_sensitive__halts(word: str) -> None:
    fake = Fake(OPEN, ACCEPTED, (200, {"status": word}))
    out = go(fake)
    assert out["outcome"] == "halt" and out["_audited"] == [] and len(fake.calls) == 3
    assert "unknown poll status" in out["reason"]


def test_main__audit_file_unwritable__json_first_exit_1_no_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = Fake(OPEN, ACCEPTED, MERGED, MERGED_PR)
    monkeypatch.setattr(tm, "audit", lambda s, h: {"ok": True, "reason": "fine"})
    monkeypatch.setattr(
        tm.run,
        "__kwdefaults__",
        {**tm.run.__kwdefaults__, "runner": fake, "audit_fn": tm.audit, "sleep": Clock().sleep},
    )
    bad = tmp_path / "no" / "such" / "dir" / "a.json"
    assert tm.main(["--repo", REPO, "--pr", "7", "--head", H, "--audit-file", str(bad)]) == 1
    cap = capsys.readouterr()
    assert json.loads(cap.out)["outcome"] == "merged"
    assert "Traceback" not in cap.err and "cannot write" in cap.err


@pytest.mark.parametrize(
    "reply",
    [
        (200, {"details": {"uuid": UUID}}),
        (201, {"details": {"uuid": UUID}}),
        (500, None),
        (403, {"message": "no"}),
        (202, None),
        (202, {"details": {}}),
    ],
)
def test_other_responses__halt(reply: Any) -> None:
    fake = Fake(OPEN, reply)
    out = go(fake)
    assert out["outcome"] == "halt" and len(fake.calls) == 2


def test_runner_exception__halts_and_never_echoes_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    out = go(Fake(OPEN, RuntimeError(f"boom with {TOKEN} inside")))
    assert out["outcome"] == "halt"
    assert TOKEN not in json.dumps(out)


# ---- the poll -------------------------------------------------------------------------


def test_poll_pending_and_enqueued_continue() -> None:
    enq = (200, {"status": "enqueued"})
    clock = Clock()
    out = go(Fake(OPEN, ACCEPTED, PENDING, enq, PENDING, MERGED, MERGED_PR), clock)
    assert out["outcome"] == "merged" and clock.sleeps == [3, 3, 3]


def test_poll_failed__halts_with_message() -> None:
    failed = (
        200,
        {"status": "failed", "details": {"message": 'Required status check "gates" is failing.'}},
    )
    fake = Fake(OPEN, ACCEPTED, failed)
    out = go(fake)
    assert out["outcome"] == "halt" and "gates" in out["reason"] and out["_audited"] == []
    assert len(fake.calls) == 3


@pytest.mark.parametrize(
    "reply",
    [
        (200, {"status": "weird"}),
        (200, {}),
        (200, None),
        (200, ["merged"]),
        (404, {"message": "Not Found"}),
        (500, None),
        (200, {"status": None}),
    ],
)
def test_poll_unknown_or_bad__halts_at_once(reply: Any) -> None:
    fake = Fake(OPEN, ACCEPTED, reply)
    out = go(fake)
    assert out["outcome"] == "halt" and len(fake.calls) == 3 and out["_audited"] == []


def test_poll_merged_but_no_merge_commit__audit_failure() -> None:
    out = tm.run(
        REPO,
        7,
        H,
        runner=Fake(OPEN, ACCEPTED, MERGED, (200, {"merged": True})),
        sleep=Clock().sleep,
    )
    assert out["outcome"] == "halt" and out["audit"]["ok"] is False
    assert "merge_commit_sha" in out["audit"]["reason"]


def test_audit_failure__halt_with_audit_record() -> None:
    out = go(Fake(OPEN, ACCEPTED, MERGED, MERGED_PR), ok=False)
    assert out["outcome"] == "halt" and out["audit"]["ok"] is False


# ---- timeout --------------------------------------------------------------------------


def test_timeout__polls_up_to_180s_then_rereads_after_60s_merged_audits() -> None:
    clock = Clock()
    fake = Fake(OPEN, ACCEPTED, *[PENDING] * 61, MERGED_PR)
    out = go(fake, clock)
    assert out["outcome"] == "merged"
    assert clock.t == 240 and clock.sleeps[-1] == 60 and clock.sleeps[:-1] == [3] * 60
    assert fake.replies == []  # exactly 61 polls (t = 0..180), then one PR read


def test_timeout__one_poll_short_of_180__keeps_polling() -> None:
    clock = Clock()
    out = go(Fake(OPEN, ACCEPTED, *[PENDING] * 60, MERGED, MERGED_PR), clock)
    assert out["outcome"] == "merged" and clock.t == 180


def test_timeout__still_open_after_reread__halts() -> None:
    out = go(Fake(OPEN, ACCEPTED, *[PENDING] * 61, OPEN))
    assert out["outcome"] == "halt" and out["audit"] is None and out["_audited"] == []


def test_timeout__reread_fails__halts() -> None:
    out = go(Fake(OPEN, ACCEPTED, *[PENDING] * 61, (502, None)))
    assert out["outcome"] == "halt"


# ---- step 1 ---------------------------------------------------------------------------


def test_head_changed__wait_and_no_merge_call() -> None:
    fake = Fake((200, {"merged": False, "head": {"sha": "ef" * 20}}))
    out = go(fake)
    assert out["outcome"] == "wait" and len(fake.calls) == 1


def test_already_merged__audits_with_our_head_and_no_put() -> None:
    fake = Fake((200, {"merged": True, "head": {"sha": "ef" * 20}, "merge_commit_sha": S}))
    out = go(fake)
    assert out["outcome"] == "merged" and out["_audited"] == [(S, H)]
    assert [c[0] for c in fake.calls] == ["GET"]


def test_already_merged_without_merge_commit__audit_failure() -> None:
    out = tm.run(REPO, 7, H, runner=Fake((200, {"merged": True})))
    assert out["outcome"] == "halt" and out["audit"]["ok"] is False


@pytest.mark.parametrize("reply", [(404, None), (200, None), (200, {"merged": False})])
def test_first_read_bad__halts_or_waits_never_merges(reply: Any) -> None:
    fake = Fake(reply)
    out = go(fake)
    assert out["outcome"] in ("halt", "wait") and len(fake.calls) == 1


# ---- token, cleaning, runner, CLI -----------------------------------------------------


def test_token_never_in_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    msg = f"bad {TOKEN}\n::error::x"
    fake = Fake(OPEN, (400, {"details": {"message": msg}}))
    monkeypatch.setattr(tm, "gh_runner", fake)
    monkeypatch.setattr(tm.run, "__kwdefaults__", {**tm.run.__kwdefaults__, "runner": fake})
    assert tm.main(["--repo", REPO, "--pr", "7", "--head", H]) == 0
    cap = capsys.readouterr()
    assert TOKEN not in cap.out + cap.err
    assert json.loads(cap.out)["outcome"] == "halt" and "::" not in cap.out


def test_gh_runner__argument_list_no_shell_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GH_TOKEN", TOKEN)
    seen: dict[str, Any] = {}

    def fake_run(cmd: Any, **kw: Any) -> Any:
        seen.update(cmd=cmd, kw=kw)
        out = b'HTTP/2.0 202 Accepted\r\nX-A: b\r\n\r\n{"status":"pending"}'
        return subprocess.CompletedProcess(cmd, 0, out, b"")

    monkeypatch.setattr(tm.subprocess, "run", fake_run)
    status, data = tm.gh_runner("PUT", "/repos/o/r/pulls/1/merge-async", {"sha": H})
    assert (status, data) == (202, {"status": "pending"})
    assert isinstance(seen["cmd"], list) and TOKEN not in " ".join(seen["cmd"])
    assert not seen["kw"].get("shell") and json.loads(seen["kw"]["input"]) == {"sha": H}


def test_gh_runner__no_http_line__halts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tm.subprocess, "run", lambda c, **k: subprocess.CompletedProcess(c, 1, b"", b"err")
    )
    with pytest.raises(tm._Halt):
        tm.gh_runner("GET", "/x", None)


def test_main__malformed_arguments__exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert tm.main(["--repo", REPO, "--pr", "7", "--head", "ABC"]) == 2
    with pytest.raises(SystemExit) as exc:
        tm.main(["--repo", REPO])
    assert exc.value.code == 2


def test_main__writes_audit_file_in_audit_stop_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake = Fake(OPEN, ACCEPTED, MERGED, MERGED_PR)
    monkeypatch.setattr(tm, "audit", lambda s, h: {"ok": False, "reason": "tree(S) differs"})
    monkeypatch.setattr(
        tm.run,
        "__kwdefaults__",
        {**tm.run.__kwdefaults__, "runner": fake, "audit_fn": tm.audit, "sleep": Clock().sleep},
    )
    f = tmp_path / "a.json"
    assert tm.main(["--repo", REPO, "--pr", "7", "--head", H, "--audit-file", str(f)]) == 0
    record = json.loads(f.read_text())
    assert record == {"merged_pr": 7, "failed": True, "reason": "tree(S) differs"}
    ts_spec = importlib.util.spec_from_file_location(
        "ts_for_audit", _SCRIPT.with_name("train_step.py")
    )
    assert ts_spec is not None and ts_spec.loader is not None
    ts = importlib.util.module_from_spec(ts_spec)
    ts_spec.loader.exec_module(ts)
    pr = {
        "number": 8,
        "isDraft": False,
        "mergeStateStatus": "CLEAN",
        "headRefOid": H,
        "labels": [{"name": "train:2"}],
    }
    assert ts.audit_stop(record, [pr]) == 8


def test_400_is_never_success_even_with_a_uuid() -> None:
    fake = Fake(
        OPEN,
        (
            400,
            {
                "status": "failed",
                "details": {"uuid": UUID, "message": "Pull request head branch was modified."},
            },
        ),
    )
    out = go(fake)
    assert out["outcome"] == "halt" and len(fake.calls) == 2
