"""Launch smoke for the clean-install job: ``prismabib gui`` serves the app on loopback only.

Run as ``uv run python scripts/gui_launch_smoke.py`` after installing the ``gui`` extra. Starts the
real command with ``--no-browser`` on any free port, expects 200 for the local app and 403 for a
foreign Host, then stops it. Stdlib only.
"""

from __future__ import annotations

import http.client
import re
import subprocess
import sys
import threading

TIMEOUT = 120
#: The whole smoke, so a hung server cannot hold the job until its own limit.
OVERALL_TIMEOUT = 300


def status(port: int, host: str) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    try:
        conn.request("GET", "/", headers={"Host": host})
        return conn.getresponse().status
    finally:
        conn.close()


def main() -> int:
    proc = subprocess.Popen(
        [sys.executable, "-m", "prismabib.cli", "gui", "--no-browser", "--port", "0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    watchdog = threading.Timer(OVERALL_TIMEOUT, proc.kill)  # a kill ends the blocking read below
    watchdog.daemon = True
    watchdog.start()
    try:
        assert proc.stdout is not None
        line = proc.stdout.readline()  # blocks until the app announces itself, or exits
        found = re.search(r"http://127\.0\.0\.1:(\d+)/", line)
        if not found:
            print(f"FAIL: no loopback URL announced within {OVERALL_TIMEOUT}s; got {line!r}")
            return 1
        port = int(found.group(1))
        local, foreign = status(port, f"127.0.0.1:{port}"), status(port, f"evil.example:{port}")
        if (local, foreign) != (200, 403):
            print(f"FAIL: expected 200 and 403, got {local} and {foreign}")
            return 1
        print(f"PASS: gui served on 127.0.0.1:{port} (local 200, foreign Host 403)")
        return 0
    finally:
        watchdog.cancel()
        proc.terminate()
        try:
            proc.wait(TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
