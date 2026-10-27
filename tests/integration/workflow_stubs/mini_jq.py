# ruff: noqa: SIM115, UP031, BLE001  (small stand-alone test stubs)
"""Minimal jq for machines without jq: exactly the filters update-behind-prs.yml uses.
An unknown filter exits 3 loudly."""

import io
import json
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, newline="\n", encoding="utf-8")
args = sys.argv[1:]
raw, binds, pos, i = False, {}, [], 0
while i < len(args):
    if args[i] == "-r":
        raw = True
    elif args[i] == "--argjson":
        binds[args[i + 1]] = json.loads(args[i + 2])
        i += 2
    else:
        pos.append(args[i])
    i += 1
flt, files = pos[0].strip(), pos[1:]
try:
    data = json.loads(open(files[0], encoding="utf-8").read() if files else sys.stdin.read())
except Exception as exc:
    print("jq: error: %s" % exc, file=sys.stderr)
    sys.exit(2)


def emit(v):
    print(v if isinstance(v, str) and raw else json.dumps(v))


def get(k):
    return data.get(k) if isinstance(data, dict) else None


if flt == '.[] | select(.mergeStateStatus == "UNKNOWN") | .number':
    for p in data:
        if p.get("mergeStateStatus") == "UNKNOWN":
            emit(p["number"])
elif flt in (".mergeStateStatus", ".action", ".mode", ".reason", ".outcome", ".failed"):
    emit(get(flt[1:]))
elif flt in (".pr // empty", ".head_sha // empty"):
    v = get(flt.split()[0][1:])
    if v not in (None, False):
        emit(v)
elif flt == "map(if .number == $r.number then $r else . end)":
    print(json.dumps([binds["r"] if p["number"] == binds["r"]["number"] else p for p in data]))
elif flt == ".stack != null and .stack.position >= 2":
    s = get("stack")
    emit(s is not None and s["position"] >= 2)
elif flt == ".stack != null":
    emit(get("stack") is not None)
elif flt == "(.verifier_args // [])[]":
    for v in get("verifier_args") or []:
        emit(v)
else:
    print("mini_jq: unsupported filter %r" % flt, file=sys.stderr)
    sys.exit(3)
