# ruff: noqa: SIM115, SIM102, UP031  (small stand-alone test stubs)
"""Fake ``gh`` for tests/integration/test_update_behind_workflow_dynamic.py.

Scenario JSON in $FAKE_GH_SCENARIO, JSONL call log in $FAKE_GH_LOG, counters in $FAKE_GH_STATE.
Every call is logged. A mutating ``gh pr`` command or non-GET API call that the scenario does not
allow is logged as UNEXPECTED_MUTATION and fails with exit 99. Never contacts a network.
"""

import io
import json
import os
import subprocess
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, newline="\n")
argv = sys.argv[1:]
sc = json.load(open(os.environ["FAKE_GH_SCENARIO"]))
st_path = os.environ["FAKE_GH_STATE"]
st = json.load(open(st_path)) if os.path.exists(st_path) else {}
LOG = os.environ["FAKE_GH_LOG"]
stdin = sys.stdin.read() if "--input" in argv else None


def log(extra=None):
    entry = {"argv": argv, "stdin": stdin, **(extra or {})}
    with open(LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def nxt(key, seq):
    i = st.get(key, 0)
    st[key] = i + 1
    json.dump(st, open(st_path, "w"))
    return seq[min(i, len(seq) - 1)]


def failing(k):
    return any(k.startswith(p) for p in sc.get("fail", []))


def hook(key):
    cmd = sc.get("hooks", {}).get(key)
    if cmd:
        subprocess.run(cmd, shell=True, check=True)


MUTATING = {"merge", "edit", "comment", "update-branch", "close", "review", "ready", "reopen"}
if argv[:1] == ["pr"] and len(argv) > 1:
    key = "pr " + argv[1]
    if failing(key):
        log({"failed": True})
        sys.exit(1)
    if argv[1] in MUTATING:
        if key not in sc.get("allow", []):
            log({"UNEXPECTED_MUTATION": True})
            sys.exit(99)
        log()
        sys.exit(0)
    if argv[1] == "list":
        which = "closed" if "closed" in argv else "open"
        data = sc[which]
        if which == "open" and "open_after" in sc and os.path.exists(LOG):
            if '"key": "PUT' in open(LOG).read():
                data = sc["open_after"]
        log({"list": which})
        print(json.dumps(data))
        sys.exit(0)
    if argv[1] == "view":
        log()
        print(json.dumps(nxt("view" + argv[2], sc["view"][argv[2]])))
        sys.exit(0)
if argv[:1] == ["api"]:
    method, path, jq, include, i = "GET", None, None, False, 1
    while i < len(argv):
        a = argv[i]
        if a == "-X":
            method = argv[i + 1]
            i += 1
        elif a == "--include":
            include = True
        elif a == "--jq":
            jq = argv[i + 1]
            i += 1
        elif a == "--input":
            i += 1
        elif not a.startswith("-"):
            path = a
        i += 1
    key = method + " /" + path.lstrip("/")
    if failing("api " + key):
        log({"failed": True, "key": key})
        sys.exit(1)
    table = sc.get("api", {})
    if key not in table or (method != "GET" and key not in sc.get("allow", [])):
        log({"UNEXPECTED": True, "key": key})
        sys.exit(98)
    v = table[key]
    resp = nxt(key, v) if isinstance(v, list) else v
    log({"key": key, "status": resp["status"]})
    if method != "GET":
        hook(key)
    body = json.dumps(resp.get("body"))
    if include:
        sys.stdout.write(
            "HTTP/2.0 %d X\nContent-Type: application/json\n\n%s\n" % (resp["status"], body)
        )
        sys.exit(0 if resp["status"] < 400 else 1)
    if resp["status"] >= 400:
        sys.exit(1)
    if jq == "(.stack // null) != null":
        print(json.dumps(resp["body"].get("stack") is not None))
        sys.exit(0)
    if jq:
        sys.exit(97)
    print(body)
    sys.exit(0)
log({"UNEXPECTED": True})
sys.exit(98)
