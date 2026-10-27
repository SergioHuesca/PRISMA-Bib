# ruff: noqa: SIM102, PLW1510  (small stand-alone test stubs)
"""``python3`` for the workflow script under test: runs the real scripts, with LF output, and
lets a test stub the verifier or the first (pre) decide through environment variables."""

import json
import os
import subprocess
import sys

a = sys.argv[1:]
base = os.path.basename(a[0]) if a else ""


def log2(o):
    with open(os.environ["HARNESS_LOG2"], "a") as f:
        f.write(json.dumps(o) + "\n")


def emit(s):
    sys.stdout.buffer.write((s + "\n").encode())
    sys.stdout.flush()


if base == "train_verify_head.py":
    log2({"verifier_argv": a[1:]})
    stub = os.environ.get("HARNESS_VERIFY_STUB")
    if stub is not None:
        if stub == "FAIL":
            sys.exit(1)
        emit(stub)
        sys.exit(0)
if base == "train_step.py" and "decide" in a and "--head-check" not in a:
    if os.environ.get("HARNESS_PRE_STUB"):
        sys.stdin.read()
        emit(os.environ["HARNESS_PRE_STUB"])
        sys.exit(0)
if base == "train_merge_async.py":
    log2({"client_argv": a[1:]})
p = subprocess.run([sys.executable, *a], stdout=subprocess.PIPE)
sys.stdout.buffer.write(p.stdout.replace(b"\r\n", b"\n"))
sys.stdout.flush()
sys.exit(p.returncode)
