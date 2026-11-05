"""The environment checks of ``prismabib doctor`` (a peer runs them before any project exists).

Each check is one line, prefixed ``ok``, ``warn`` or ``FAIL``. Only ``FAIL`` changes the exit code.
Nothing here prints a key or a masked key. Only :func:`openalex_reachable` makes a request, and
only when the caller asks for it (``doctor --online``). A separate module so that
``source_commands.py`` keeps to the project commands.
"""

from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Final, Literal, NamedTuple

import httpx

from prismabib import __version__
from prismabib.credentials import ENV_FILENAME, _ignore_problem
from prismabib.project import _resolve_projects_root
from prismabib.sources.openalex import _guard_httpx_log
from prismabib.sources.scopus import register_secret_for_redaction

MIN_PYTHON: Final = (3, 11)
OPENALEX_ROOT: Final = "https://api.openalex.org/"
SMART_APP_CONTROL_HINT: Final = (
    "  hint: if Windows (Smart App Control / Application Control) blocks a program, use a "
    "Python installed from python.org (signed), not a store or portable build."
)


class Check(NamedTuple):
    """One doctor line. ``fail`` is the only level that makes ``doctor`` exit 1."""

    level: Literal["ok", "warn", "FAIL"]
    text: str

    @property
    def line(self) -> str:
        """``ok   text`` with the level padded to four characters."""
        return f"{self.level:<4} {self.text}"


def python_check(
    version: tuple[int, int, int] | None = None, executable: str | None = None
) -> Check:
    """Python >= 3.11 (blocking), with the interpreter path."""
    found = version or (sys.version_info[0], sys.version_info[1], sys.version_info[2])
    text = f"python {'.'.join(map(str, found))} ({executable or sys.executable})"
    if found < MIN_PYTHON:
        return Check("FAIL", f"{text}: prismabib needs Python 3.11 or newer")
    return Check("ok", text)


def git_check() -> Check:
    """``git`` on PATH (blocking: the credential writer's ``.gitignore`` guard runs it)."""
    path = shutil.which("git")
    if path is None:
        return Check("FAIL", "git: not found on PATH (needed to confirm .env is git-ignored)")
    return Check("ok", f"git: {path}")


def projects_root_check(root: Path | None) -> Check:
    """The projects root exists, or can be created, and accepts a file; no debris is left."""
    path = (root if root is not None else _resolve_projects_root(None)).resolve()
    probe_dir = path
    while not probe_dir.exists() and probe_dir != probe_dir.parent:
        probe_dir = probe_dir.parent
    probe = probe_dir / f".doctor-{os.getpid()}"
    try:  # one attempt: tempfile retries a PermissionError for minutes on Windows
        os.close(os.open(probe, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
    except OSError as exc:
        return Check("FAIL", f"projects root: {path} is not writable ({type(exc).__name__})")
    finally:
        probe.unlink(missing_ok=True)
    note = "" if path.exists() else " (does not exist yet; it is created on first use)"
    return Check("ok", f"projects root: {path}, writable{note}")


def env_file_check(env_file: Path | None = None) -> Check:
    """Whether ``.env`` is present and git-ignored by a tracked rule; never reads a value."""
    path = (env_file or Path(ENV_FILENAME)).resolve()
    if not path.is_file():
        return Check("ok", ".env: not present (created by `prismabib credentials set`)")
    problem = _ignore_problem(path)
    if problem is None:
        return Check("ok", ".env: present, git-ignored by a tracked rule")
    return Check("warn", f".env: present, not safely git-ignored ({problem})")


def environment_report(root: Path | None, *, system: str | None = None) -> tuple[list[str], bool]:
    """The environment section's lines in print order, and whether any blocking check FAILED."""
    system = system or platform.system()
    first = [python_check(), Check("ok", f"prismabib {__version__}")]
    platform_line = Check("ok", f"platform: {platform.platform()}")
    rest = [git_check(), projects_root_check(root), env_file_check()]
    lines = [c.line for c in [*first, platform_line]]
    if system == "Windows":
        lines.append(SMART_APP_CONTROL_HINT)
    lines += [c.line for c in rest]
    return lines, any(c.level == "FAIL" for c in [*first, *rest])


def openalex_reachable(key: str | None, *, http_client: httpx.Client | None = None) -> Check:
    """One free request to the OpenAlex root; reports reachable, the HTTP status or the error class."""
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    if key:
        register_secret_for_redaction(key)
        _guard_httpx_log(key)
    client = http_client if http_client is not None else httpx.Client(timeout=15.0)
    try:
        response = client.get(OPENALEX_ROOT, headers=headers)
    except (httpx.HTTPError, UnicodeError) as exc:
        return Check("warn", f"openalex: not reachable (network error {type(exc).__name__})")
    finally:
        if http_client is None:
            client.close()
    status = response.status_code
    if status >= 400:
        return Check("warn", f"openalex: api.openalex.org answered HTTP {status}")
    text = f"openalex: api.openalex.org reachable (HTTP {status})"
    if not key:
        text += "; search needs a free key (prismabib credentials set openalex) as keyless search is rate-limited"
    return Check("ok", text)
