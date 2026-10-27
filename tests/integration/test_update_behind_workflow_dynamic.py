"""Run the real ``run:`` script of update-behind-prs.yml under real bash (ADR 0037 PR 4).

A fake ``gh`` first on PATH serves canned JSON and logs every call; ``python3`` is a shim that runs
the REAL scripts from a copy of ``scripts/``. Only the stacked verifier is stubbed (building a
rule-h commit graph is out of scope here; the non-stacked scenarios run the real verifier). The git
side is a throwaway clone with a local bare ``origin``. Each scenario asserts the exact list of
``gh`` calls. The mutation tests apply one mutation each to the script and require the matching
scenario assertion to fail. The module is skipped, never failed, when bash is missing. jq is the
real one on POSIX and ``mini_jq.py`` (exactly the filters used) on Windows or when jq is missing.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).parent.parent.parent
STUBS = Path(__file__).parent / "workflow_stubs"
WF = ROOT / ".github" / "workflows" / "update-behind-prs.yml"


def _bash() -> str | None:
    if sys.platform == "win32":  # System32\bash.exe is WSL: use Git's
        git_exe = shutil.which("git")
        if git_exe is None:
            return None
        for parent in Path(git_exe).resolve().parents[:3]:
            cand = parent / "bin" / "bash.exe"
            if cand.exists():
                return str(cand)
        return None
    return shutil.which("bash")


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="real bash is required (Git Bash on Windows)")

SCRIPT = yaml.safe_load(WF.read_text(encoding="utf-8"))["jobs"]["update"]["steps"][-1]["run"]
OKV = json.dumps({"ok": True, "reason": "stub"})
STACKS_ON = {"MERGE_TRAIN": "on", "MERGE_TRAIN_STACKS": "on", "HARNESS_VERIFY_STUB": OKV}
STACKS_OFF = {"MERGE_TRAIN": "on", "MERGE_TRAIN_STACKS": "off"}
UPDATE_ONLY = {"MERGE_TRAIN": "off"}
# a first decide whose verifier_args are not the shape decide produces
HOSTILE_PRE = json.dumps(
    {
        "action": "wait", "pr": 12, "head_sha": "x", "reason": "r", "mode": "train",
        "verifier_args": ["--parent-reviewed", "Z" * 40, "--parent-head", "1" * 40,
                          "--parent-merge", "2" * 40],
    }
)  # fmt: skip
UUID = "0b3a6b5e-1c2d-4e3f-8a9b-0123456789ab"
GITENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
    "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z",
    "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z",
}


def px(p: Path | str) -> str:
    return Path(p).as_posix()


def git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "core.fileMode=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **GITENV},
    )
    return proc.stdout.strip()


class Fx:
    d: Path
    seed: Path
    bare: Path
    repo: Path
    A: str
    P1: str
    P1b: str
    M: str
    C0: str
    H: str
    S: str
    Sbad: str
    H1: str


def build_fixture(root: Path, name: str) -> Fx:
    f = Fx()
    f.d = root / name
    f.seed, f.bare, f.repo = f.d / "seed", f.d / "origin.git", f.d / "repo"
    f.seed.mkdir(parents=True)
    git(f.seed, "init", "-q", "-b", "main")
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(f.bare)], check=True)

    def commit(msg: str, files: dict[str, str], parent: str | None = None) -> str:
        if parent:
            git(f.seed, "checkout", "-q", "--detach", parent)
        for k, v in files.items():
            (f.seed / k).write_bytes(v.encode())
        git(f.seed, "add", "-A")
        git(f.seed, "commit", "-q", "-m", msg)
        return git(f.seed, "rev-parse", "HEAD")

    f.A = commit("A", {"base.txt": "1\n"})
    f.P1 = commit("P1", {"p.txt": "p\n"}, f.A)
    f.P1b = commit("P1b", {"p2.txt": "p2\n"}, f.P1)
    f.M = commit("M", {"p.txt": "p\n", "p2.txt": "p2\n"}, f.A)  # the parent's squash
    f.C0 = commit("C0", {"c.txt": "c\n"}, f.P1)  # the child as reviewed
    f.H = commit("H", {"c.txt": "c\n"}, f.M)  # the child as GitHub rebased it
    f.S = commit("S", {"c.txt": "c\n"}, f.M)  # tree(S) == tree(H)
    f.Sbad = commit("Sbad", {"c.txt": "c\n", "x.txt": "evil\n"}, f.M)
    f.H1 = commit("H1", {"c.txt": "c\n"}, f.M)  # a non-stacked PR
    git(f.seed, "remote", "add", "origin", str(f.bare))
    refs = {
        "refs/heads/main": f.M,
        "refs/pull/10/head": f.P1b,
        "refs/pull/10/orig": f.P1,
        "refs/pull/12/head": f.H,
        "refs/pull/12/orig": f.C0,
        "refs/pull/20/head": f.H1,
    }
    for ref, sha in refs.items():
        git(f.seed, "push", "-q", "origin", f"{sha}:{ref}")
    subprocess.run(["git", "clone", "-q", str(f.bare), str(f.repo)], check=True)
    shutil.copytree(ROOT / "scripts", f.repo / "scripts")
    return f


def ck() -> list[dict[str, str]]:
    return [
        {"__typename": "CheckRun", "name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"}
    ]


def prrec(n: int, labels: list[str], head: str, mss: str = "CLEAN") -> dict[str, Any]:
    return {
        "number": n,
        "state": "OPEN",
        "isDraft": False,
        "mergeStateStatus": mss,
        "headRefOid": head,
        "labels": [{"name": x} for x in labels],
        "statusCheckRollup": ck(),
        "baseRefName": "main",
    }


def pulls(n: int, stack: dict[str, int] | None = None, **k: Any) -> dict[str, Any]:
    body = {"number": n, "state": "open", "merged": False, "stack": stack, **k}
    return {"status": 200, "body": body}


def now_minus(minutes: int) -> str:
    return (dt.datetime.now(dt.UTC) - dt.timedelta(minutes=minutes)).strftime("%Y-%m-%dT%H:%M:%SZ")


def stacked(fx: Fx, s_commit: str = "S", mss: str = "CLEAN") -> dict[str, Any]:
    """Head #12 at position 2 of stack 4; parent #10 merged 2 minutes ago, its merge M in main."""
    sha = getattr(fx, s_commit)
    head = {"sha": fx.H}
    put = "PUT /repos/o/r/pulls/12/merge-async"
    two = {"id": 1, "number": 4, "position": 2, "size": 2}
    parent = {
        "number": 10, "state": "closed", "merged": True, "merged_at": now_minus(2),
        "merge_commit_sha": fx.M, "head": {"sha": fx.P1b},
        "labels": [{"name": f"reviewed:{fx.P1}"}],
    }  # fmt: skip
    return {
        "open": [prrec(12, ["train:1", f"reviewed:{fx.C0}"], fx.H, mss)],
        "closed": [],
        "open_after": [prrec(13, ["train:2", "reviewed:" + "9" * 40], "9" * 40)],
        "api": {
            "GET /repos/o/r/pulls/12": [
                pulls(12, two, head=head, labels=[{"name": f"reviewed:{fx.C0}"}]),
                {"status": 200, "body": {"merged": False, "head": head}},
                {"status": 200, "body": {"merged": True, "head": head, "merge_commit_sha": sha}},
            ],
            "GET /repos/o/r/stacks/4": {
                "status": 200,
                "body": {"pull_requests": [{"number": 10}, {"number": 12}]},
            },
            "GET /repos/o/r/pulls/10": {"status": 200, "body": parent},
            put: {
                "status": 202,
                "body": {"status": "pending", "details": {"uuid": UUID, "expected_head_sha": fx.H}},
            },
            f"GET /repos/o/r/pulls/12/merge-async/{UUID}": {
                "status": 200,
                "body": {"status": "merged"},
            },
        },
        "allow": [put, "pr edit", "pr comment"],
        # GitHub creates S after the workflow's last fetch: it appears on origin at the PUT.
        "hooks": {
            put: f'git -C "{px(fx.seed)}" push -q origin {sha}:refs/heads/main',
        },
    }


class Result:
    fx: Fx

    def __init__(self, proc: subprocess.CompletedProcess[str], d: Path) -> None:
        def read(x: str) -> str:
            return (d / x).read_text() if (d / x).exists() else ""

        self.rc, self.out, self.err = proc.returncode, proc.stdout, proc.stderr
        self.calls = [json.loads(x) for x in read("gh.log").splitlines()]
        self.log2 = [json.loads(x) for x in read("log2.log").splitlines()]
        self.summary = read("summary.md")

    @property
    def names(self) -> list[str]:
        out = []
        for c in self.calls:
            a = c["argv"]
            if c.get("list"):
                out.append(f"pr list {c['list']}")
            elif a[0] == "api":
                out.append("api " + c["key"])
            elif a[1] in ("merge", "edit"):
                out.append("pr " + " ".join(a[1:]))
            else:
                out.append(f"pr {a[1]} {a[2]}")
        return out

    def puts(self) -> list[dict[str, Any]]:
        return [json.loads(c["stdin"]) for c in self.calls if c.get("key", "").startswith("PUT")]


@pytest.fixture(scope="module")
def stub_bin(tmp_path_factory: pytest.TempPathFactory) -> Path:
    b = tmp_path_factory.mktemp("bin")
    py = px(sys.executable)

    def wrapper(name: str, target: str) -> None:
        p = b / name
        p.write_bytes(f'#!/usr/bin/env bash\nexec "{py}" "{px(STUBS / target)}" "$@"\n'.encode())
        p.chmod(0o755)

    wrapper("gh", "fake_gh.py")
    wrapper("python3", "python3_shim.py")
    if sys.platform == "win32" or shutil.which("jq") is None:
        wrapper("jq", "mini_jq.py")
    return b


Runner = Callable[..., Result]


@pytest.fixture
def runner(tmp_path: Path, stub_bin: Path) -> Runner:
    count = 0

    def go(
        scenario_of: Callable[[Fx], dict[str, Any]],
        env: dict[str, str],
        script: str = SCRIPT,
    ) -> Result:
        nonlocal count
        count += 1
        fx = build_fixture(tmp_path, f"w{count}")
        d = fx.d
        (d / "tmp").mkdir()
        (d / "summary.md").write_text("")
        (d / "scenario.json").write_text(json.dumps(scenario_of(fx)))
        (d / "run.sh").write_bytes(script.encode())
        e = {
            **os.environ,
            **GITENV,
            "PATH": str(stub_bin) + os.pathsep + os.environ["PATH"],
            "FAKE_GH_SCENARIO": px(d / "scenario.json"),
            "FAKE_GH_LOG": px(d / "gh.log"),
            "FAKE_GH_STATE": px(d / "state.json"),
            "HARNESS_LOG2": px(d / "log2.log"),
            "GH_TOKEN": "SENTINEL-TOKEN-xyz123",
            "GH_REPO": "o/r",
            "RUNNER_TEMP": px(d / "tmp"),
            "GITHUB_STEP_SUMMARY": px(d / "summary.md"),
            "GITHUB_SHA": "a" * 40,
            "TZ": "Asia/Tokyo",
            "LC_ALL": "C",
            "MSYS_NO_PATHCONV": "1",
        }
        if sys.platform == "win32":  # subprocess cannot run the PATH wrapper there
            e["FAKE_GH_PY"] = str(STUBS / "fake_gh.py")
            e["PYTHONPATH"] = os.pathsep.join([str(STUBS / "win"), e.get("PYTHONPATH", "")])
        for k in ("MERGE_TRAIN", "MERGE_TRAIN_STACKS", "HARNESS_VERIFY_STUB", "HARNESS_PRE_STUB"):
            e.pop(k, None)
        e.update(env)
        proc = subprocess.run(
            [str(BASH), "--noprofile", "--norc", "-e", px(d / "run.sh")],
            cwd=fx.repo, env=e, capture_output=True, text=True, timeout=300, check=False,
        )  # fmt: skip
        res = Result(proc, d)
        res.fx = fx
        return res

    return go


# ---- scenarios: a builder, an environment and an assertion ------------------------------------


def sc_nonstacked(fx: Fx) -> dict[str, Any]:
    return {
        "open": [prrec(20, ["train:1", f"reviewed:{fx.H1}"], fx.H1)],
        "closed": [],
        "api": {"GET /repos/o/r/pulls/20": pulls(20, head={"sha": fx.H1}, labels=[])},
        "allow": ["pr merge"],
    }


def sc_update_only(
    fx: Fx, stack: dict[str, int] | None = None, reply: dict[str, Any] | None = None
) -> dict[str, Any]:
    return {
        "open": [prrec(20, [], fx.H1, mss="BEHIND")],
        "closed": [],
        "api": {"GET /repos/o/r/pulls/20": reply or pulls(20, stack)},
        "allow": ["pr update-branch", "pr comment"],
    }


def sc_s7_train(fx: Fx) -> dict[str, Any]:
    pos1 = {"id": 1, "number": 4, "position": 1, "size": 2}
    return {
        "open": [prrec(20, ["train:1", f"reviewed:{fx.H1}"], fx.H1, mss="BEHIND")],
        "closed": [],
        "api": {"GET /repos/o/r/pulls/20": pulls(20, pos1)},
        "allow": ["pr update-branch", "pr comment", "pr edit"],
    }


def no_unexpected(r: Result) -> None:
    bad = [c for c in r.calls if c.get("UNEXPECTED") or c.get("UNEXPECTED_MUTATION")]
    assert not bad, bad
    assert "SENTINEL-TOKEN" not in r.out + r.err + r.summary


def assert_s1(r: Result) -> None:
    assert r.rc == 0, r.err
    assert r.names == [
        "pr list open",
        "pr list closed",
        f"pr merge 20 --squash --match-head-commit {r.fx.H1}",
    ]
    no_unexpected(r)


def assert_s2(r: Result) -> None:
    assert r.rc == 0, r.err
    assert r.names == [
        "pr list open",
        "pr list closed",
        "api GET /repos/o/r/pulls/20",
        f"pr merge 20 --squash --match-head-commit {r.fx.H1}",
    ]
    no_unexpected(r)


def assert_s3(r: Result) -> None:
    assert r.rc == 0, (r.out, r.err)
    assert not [n for n in r.names if n.startswith(("pr merge", "pr edit"))], r.names
    assert r.puts() == [{"merge_method": "squash", "sha": r.fx.H}]
    assert r.names.count("pr list open") == 1  # the audit passed: no re-list
    client = next(x["client_argv"] for x in r.log2 if "client_argv" in x)
    assert "--audit-file" in client and client[client.index("--head") + 1] == r.fx.H
    no_unexpected(r)


def assert_s5(r: Result) -> None:
    assert r.rc == 0, r.err
    assert not [n for n in r.names if "update-branch" in n or "merge" in n or "PUT" in n]
    assert "pr edit 12 --add-label train:halted" in r.names and "pr comment 12" in r.names
    no_unexpected(r)


def assert_s6(r: Result) -> None:
    assert r.rc == 1, (r.out, r.err)
    assert r.names.count("pr list open") == 2  # re-listed after the merge
    assert "pr edit 13 --add-label train:halted" in r.names  # the open successor
    assert not [n for n in r.names if n.startswith("pr edit 12")]  # never the merged PR
    assert "audit failed" in r.out
    no_unexpected(r)


def assert_s6n(r: Result) -> None:
    assert r.rc == 1, (r.out, r.err)
    assert not [n for n in r.names if n.startswith("pr edit")]
    assert "pr comment 12" in r.names  # on the merged PR
    assert "::error::" in r.out
    no_unexpected(r)


def assert_s7(r: Result) -> None:
    assert r.rc == 0, r.err
    assert not [n for n in r.names if "update-branch" in n]
    assert "::warning::" in r.out and "::notice::" not in r.out
    assert "stacked and BEHIND" in r.summary and "updating" not in r.summary.split("stacked")[-1]
    no_unexpected(r)


def assert_s7_train(r: Result) -> None:
    assert r.rc == 1, (r.out, r.err)
    assert not [n for n in r.names if "update-branch" in n]
    assert "pr edit 20 --add-label train:halted" in r.names and "pr comment 20" in r.names
    no_unexpected(r)


def assert_s8(r: Result) -> None:
    assert r.rc == 0, r.err
    assert r.names == [
        "pr list open",
        "pr list closed",
        "api GET /repos/o/r/pulls/20",
        "pr update-branch 20",
        "pr comment 20",
    ]
    no_unexpected(r)


def assert_s9e(r: Result) -> None:
    assert r.rc == 0, r.err
    assert not [n for n in r.names if "update-branch" in n]
    assert "::warning::" in r.out and "stack check failed" in r.out
    no_unexpected(r)


def assert_hostile(r: Result) -> None:
    assert not any("verifier_argv" in x for x in r.log2), "the verifier ran on unvalidated args"


Scenario = tuple[Callable[[Fx], dict[str, Any]], dict[str, str], Callable[[Result], None]]
SCENARIOS: dict[str, Scenario] = {
    "S1": (sc_nonstacked, STACKS_OFF, assert_s1),
    "S2": (sc_nonstacked, STACKS_ON, assert_s2),
    "S3": (stacked, STACKS_ON, assert_s3),
    "S5": (lambda fx: stacked(fx, mss="BEHIND"), STACKS_ON, assert_s5),
    "S6": (lambda fx: stacked(fx, "Sbad"), STACKS_ON, assert_s6),
    "S6n": (lambda fx: stacked(fx, "Sbad") | {"open_after": []}, STACKS_ON, assert_s6n),
    "S7": (
        lambda fx: sc_update_only(fx, {"id": 1, "number": 4, "position": 1, "size": 2}),
        UPDATE_ONLY,
        assert_s7,
    ),
    "S7-train": (sc_s7_train, STACKS_OFF, assert_s7_train),
    "S8": (sc_update_only, UPDATE_ONLY, assert_s8),
    "S9e": (
        lambda fx: sc_update_only(fx, reply={"status": 502, "body": {}}),
        UPDATE_ONLY,
        assert_s9e,
    ),
    "HOSTILE": (stacked, {**STACKS_ON, "HARNESS_PRE_STUB": HOSTILE_PRE}, assert_hostile),
}


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_scenario(runner: Runner, name: str) -> None:
    build, env, check = SCENARIOS[name]
    check(runner(build, env))


# ---- dynamic mutations: each must break the matching scenario's assertion --------------------


def _mut(old: str, new: str) -> str:
    assert old in SCRIPT, old
    return SCRIPT.replace(old, new, 1)


_VARGS = '[[ "${vargs[1]}" =~ $sha_re && "${vargs[3]}" =~ $sha_re && "${vargs[5]}" =~ $sha_re ]]'
DYNAMIC_MUTATIONS = {
    "stack-check API error masked as false": ("|| echo unknown)", "|| echo false)", "S9e"),
    "if true in front of gh pr merge": (
        'if [ "$stacked" != true ]; then', "if true; then", "S3"),
    "audit halt labels the merged PR": ("pr=$succ\n", "pr=$merged_pr\n", "S6"),
    "stacked halt in train mode removed": (
        '[ "$stacked" = true ] && [ "$mode" = train ]', "false", "S7-train"),
    "stacked in update-only mode not skipped": (
        'elif [ "$stacked" = true ]; then', "elif false; then", "S7"),
    "sha-regex guard on verifier_args removed": (_VARGS, "true", "HOSTILE"),
    "audit-stop runs even when the audit passed": (
        '[ "$(jq -r .failed "$audit")" = true ]', "true", "S3"),
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(DYNAMIC_MUTATIONS))
def test_dynamic_mutation__breaks_a_scenario(runner: Runner, name: str) -> None:
    old, new, which = DYNAMIC_MUTATIONS[name]
    build, env, check = SCENARIOS[which]
    result = runner(build, env, _mut(old, new))
    with pytest.raises(AssertionError):
        check(result)
