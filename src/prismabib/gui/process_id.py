"""Which process is this pid: whether it exists, and a token that tells one process from the next.

A lock file records its holder's pid. A pid is reused once the holder is gone, so "the pid
exists" alone keeps a crashed window's lock alive forever on a busy machine. The start token (the
process creation time) tells the original holder from a newer process that got the same pid.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

_QUERY_LIMITED_INFORMATION = 0x1000  # PROCESS_QUERY_LIMITED_INFORMATION


def _open_process(pid: int) -> tuple[Any, Any] | None:
    """Windows only: ``(kernel32, handle)`` for ``pid``, or ``None`` when no such process."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(_QUERY_LIMITED_INFORMATION, False, pid)
    return (kernel32, handle) if handle else None


def pid_alive(pid: int) -> bool:
    """Whether a process with this pid exists (it may be a different process than the holder)."""
    if sys.platform == "win32":  # os.kill would terminate the process there
        opened = _open_process(pid)
        if opened is None:
            return False
        opened[0].CloseHandle(opened[1])
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _windows_start(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes

    opened = _open_process(pid)
    if opened is None:
        return None
    kernel32, handle = opened
    try:
        times = [wintypes.FILETIME() for _ in range(4)]  # creation, exit, kernel, user
        ok = kernel32.GetProcessTimes(handle, *(ctypes.byref(t) for t in times))
        created = times[0]
        return str((created.dwHighDateTime << 32) | created.dwLowDateTime) if ok else None
    finally:
        kernel32.CloseHandle(handle)


def _linux_start(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return None
    # "pid (name) state ...": the name may hold spaces and brackets, so split after the last ")"
    # and take field 22 (starttime, in clock ticks since boot).
    fields = stat.rpartition(")")[2].split()
    return fields[19] if len(fields) > 19 else None


def _ps_start(pid: int) -> str | None:
    try:
        done = subprocess.run(
            ["ps", "-o", "lstart=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() or None


def process_start_token(pid: int) -> str | None:
    """A value that stays the same for one process's whole life and differs between processes.

    It is the process creation time: ``GetProcessTimes`` on Windows, ``/proc/<pid>/stat`` on
    Linux, ``ps -o lstart`` elsewhere (macOS). ``None`` when the platform gives none; the lock
    check then falls back to "does the pid exist", so a reused pid can keep a stale lock alive
    until the researcher confirms the take-over.
    """
    if sys.platform == "win32":
        return _windows_start(pid)
    return _linux_start(pid) if sys.platform.startswith("linux") else _ps_start(pid)
