"""Credentials: the ``.env`` writer and ``prismabib credentials ...`` (ADR 0042 Decision 8).

The writer is the only code that writes a secret. It refuses unless git confirms that the file it
would write is ignored by a rule of the repository's *tracked* ``.gitignore`` (a rule from
``core.excludesFile`` or ``.git/info/exclude`` is absent from a fresh clone, so it does not count),
and that the file is not tracked. It changes only the keys it manages, keeps every other line, and
replaces the file atomically through a ``tmp-<random>.env`` sibling that the ``*.env`` rule covers.

No command takes a key value as an argument: the value comes from a hidden prompt, from ``--stdin``
(``NAME=VALUE`` lines) or from ``--from-env``. Nothing here prints, logs or raises a value; every
secret is also registered with the structlog redaction processor.
"""

from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from io import StringIO
from pathlib import Path
from typing import Annotated, Final

import httpx
import typer
from dotenv import dotenv_values
from dotenv.parser import parse_stream

from prismabib.config import Settings, require_scopus_key
from prismabib.errors import ConfigError
from prismabib.sources.scopus import ScopusClient, register_secret_for_redaction

ENV_FILENAME: Final = ".env"
#: Per service: the keys its ``set`` writes and ``clear`` removes; the first one is required.
SERVICE_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "scopus": ("SCOPUS_API_KEY", "SCOPUS_INSTTOKEN"),
    "openalex": ("OPENALEX_API_KEY",),
    "unpaywall": ("UNPAYWALL_EMAIL",),
}
MANAGED_KEYS: Final = tuple(k for keys in SERVICE_KEYS.values() for k in keys)
#: Not a secret (Unpaywall asks for a real contact address), so its prompt is not hidden.
_NOT_SECRET: Final = frozenset({"UNPAYWALL_EMAIL"})
#: The only ignore rules that count: the exact patterns the repository's ``.gitignore`` carries.
_ACCEPTED_PATTERNS: Final = frozenset({".env", "*.env"})
#: Git variables that would point git away from the directory it is run in.
_GIT_REDIRECTS: Final = frozenset({"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"})
_CHECK_IGNORE_LINE: Final = re.compile(r"^(?P<source>.*?):(?P<line>\d+):(?P<pattern>.*)$")
#: Same shape as the writer's ``tmp-<16 hex>.env`` temp file, used only to ask git about it.
_TEMP_PROBE_NAME: Final = "tmp-0000000000000000.env"
_BARE_VALUE: Final = re.compile(r"[A-Za-z0-9_@.+/:=-]+")
_CONTROL: Final = re.compile(r"[\x00-\x1f\x7f]")


class Service(StrEnum):
    """What ``credentials set`` and ``clear`` act on."""

    scopus = "scopus"
    openalex = "openalex"
    unpaywall = "unpaywall"


class ProbeService(StrEnum):
    """What ``credentials test`` can check (one request each)."""

    scopus = "scopus"


# -- git checks -----------------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str] | None:
    """Run git without a shell, in ``cwd``; ``None`` when git cannot be run."""
    env = {k: v for k, v in os.environ.items() if k not in _GIT_REDIRECTS}
    try:
        return subprocess.run(
            ["git", "-c", "core.quotepath=off", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def _ignore_problem(path: Path) -> str | None:
    """Why git does not confirm ``path`` as safely ignored, or ``None`` when it does.

    "Tracked" means in the index: a ``.gitignore`` that is staged but not yet committed passes,
    although a fresh clone would not have its rule until it is committed.
    """
    folder = path.parent
    if not folder.is_dir():
        return f"the directory {folder} does not exist"
    top = _git(["rev-parse", "--show-toplevel"], folder)
    if top is None:
        return "git is not installed or could not be run"
    if top.returncode != 0:
        if "not a git repository" in top.stderr.lower():
            return f"{folder} is not inside a git repository"
        return "git could not read the repository (for example a safe.directory or ownership error)"
    toplevel = Path(top.stdout.strip()).resolve()

    # First, so that a tracked file gets its own message: check-ignore never reports a tracked
    # path as ignored, so this branch would otherwise be unreachable.
    listed = _git(["ls-files", "--", path.name], folder)
    if listed is None or listed.returncode != 0:
        return "git ls-files failed"
    if listed.stdout.strip():
        return f"{path.name} is tracked by git, so a key written to it could be committed"

    ignored = _git(["check-ignore", "-v", "--", path.name], folder)
    if ignored is None or ignored.returncode not in (0, 1):
        return "git check-ignore failed"
    first = ignored.stdout.splitlines()[0] if ignored.stdout.strip() else ""
    match = _CHECK_IGNORE_LINE.match(first.split("\t", 1)[0])
    if ignored.returncode == 1 or match is None:
        return f"git does not ignore {path.name} here"
    pattern = match["pattern"]
    if pattern not in _ACCEPTED_PATTERNS:
        return f"it is matched only by the rule {pattern!r}, not by `.env` or `*.env`"
    raw_source = match["source"]  # git double-quotes a path with special characters
    source = Path(raw_source[1:-1] if raw_source[:1] == raw_source[-1:] == '"' else raw_source)
    source = source if source.is_absolute() else toplevel / source
    try:
        relative = source.resolve().relative_to(toplevel)
    except ValueError:
        return "the ignore rule comes from a file outside the repository"
    if source.name != ".gitignore" or ".git" in relative.parts:
        return f"the ignore rule comes from {relative.as_posix()}, not a tracked .gitignore"
    tracked_rule_file = _git(["ls-files", "--error-unmatch", "--", relative.as_posix()], toplevel)
    if tracked_rule_file is None or tracked_rule_file.returncode != 0:
        return f"{relative.as_posix()} is not tracked, so a fresh clone would not have the rule"
    # The write goes through a sibling temp file; it must be ignored too, or the plaintext key
    # would sit in an unignored file during the write (a bare `.env` rule does not cover it).
    temp = _git(["check-ignore", "--", _TEMP_PROBE_NAME], folder)
    if temp is None or temp.returncode != 0:
        return (
            f"the temporary file name {_TEMP_PROBE_NAME} is not ignored (add a `*.env` rule), "
            "so the key would sit in an unignored file while it is written"
        )
    return None


def check_env_file_ignored(env_file: Path) -> Path:
    """Return ``env_file`` resolved, or refuse unless git confirms it is safely ignored.

    Raises:
        ConfigError: With the reason, if git is missing, the directory is no repository, the
            file is not ignored by a ``.env``/``*.env`` rule of a tracked ``.gitignore``, or it
            is tracked.
    """
    path = env_file.resolve()
    problem = _ignore_problem(path)
    if problem is not None:
        raise ConfigError(
            f"Refusing to write {path}: {problem}. A key is only stored in a file that a tracked "
            ".gitignore ignores (rule `.env` or `*.env`), so it cannot be committed by mistake."
        )
    return path


# -- the writer -----------------------------------------------------------------------------------


def _quote(value: str) -> str:
    """Render ``value`` the way python-dotenv (so pydantic-settings) reads it back unchanged."""
    if _BARE_VALUE.fullmatch(value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _validated(updates: Mapping[str, str | None]) -> None:
    for name, value in updates.items():
        if name not in MANAGED_KEYS:
            raise ConfigError(f"{name} is not a credential this command manages.")
        if value is None:
            continue
        if not value.strip():
            raise ConfigError(f"The value for {name} is empty.")
        if _CONTROL.search(value):
            raise ConfigError(f"The value for {name} contains a line break or control character.")
        if "${" in value:
            raise ConfigError(f"The value for {name} contains '${{', which .env readers expand.")


def _edited(text: str, updates: Mapping[str, str | None]) -> str:
    """``text`` with the managed keys replaced (or removed, for ``None``); the rest untouched."""
    newline = "\r\n" if "\r\n" in text else "\n"
    out: list[str] = []
    done: set[str] = set()
    for binding in parse_stream(StringIO(text)):
        key = binding.key
        if key is not None and key in updates:
            value = updates[key]
            if value is not None and key not in done:
                out.append(f"{key}={_quote(value)}{newline}")
                done.add(key)
            continue  # a cleared key, or a duplicate of one already written
        out.append(binding.original.string)
    if out and not out[-1].endswith(("\n", "\r")):
        out[-1] += newline
    for key, value in updates.items():
        if value is not None and key not in done:
            out.append(f"{key}={_quote(value)}{newline}")
    return "".join(out)


def write_env(env_file: Path, updates: Mapping[str, str | None]) -> Path:
    """Set (or, with ``None``, remove) managed keys in ``env_file``, atomically and only if safe.

    Args:
        env_file: The file to write; created if missing. The ignore check runs against its
            resolved path, wherever it is.
        updates: ``NAME -> value`` for the managed keys; ``None`` removes the key.

    Returns:
        The resolved path that was written.

    Raises:
        ConfigError: If a value is unusable, git does not confirm the file as ignored (see
            :func:`check_env_file_ignored`), or the write fails (the old file is left in place).
    """
    _validated(updates)
    path = check_env_file_ignored(env_file)
    try:
        old = path.read_bytes().decode("utf-8") if path.exists() else ""
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Could not read {path} as UTF-8 ({type(exc).__name__}).") from exc
    new = _edited(old, updates)
    read_back = dotenv_values(stream=StringIO(new))
    if any(v is not None and read_back.get(k) != v for k, v in updates.items()):
        raise ConfigError("Refusing to write: a value would not read back unchanged from .env.")

    temp = path.parent / f"tmp-{secrets.token_hex(8)}.env"
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as handle:
            handle.write(new.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":  # a no-op on NTFS, which has no POSIX mode bits
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    except OSError as exc:
        raise ConfigError(
            f"Could not write {path} ({type(exc).__name__}); the existing file is unchanged."
        ) from exc
    finally:
        temp.unlink(missing_ok=True)
    return path


# -- status and test ------------------------------------------------------------------------------


def _file_values(env_file: Path) -> dict[str, str | None]:
    try:
        return dotenv_values(env_file) if env_file.is_file() else {}
    except (OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"Could not read {env_file} ({type(exc).__name__}).") from exc


def key_status(name: str, file_values: Mapping[str, str | None]) -> str:
    """``"set (environment)"``, ``"set (.env)"`` or ``"not set"``; never the value.

    The process environment wins over ``.env`` (pydantic-settings reads it first), so a key in
    both is reported as the environment's.
    """
    if os.environ.get(name):
        return "set (environment)"
    return "set (.env)" if file_values.get(name) else "not set"


def status_lines(env_file: Path) -> list[str]:
    """``NAME: set (...)`` / ``NAME: not set`` for every managed key (used by ``doctor`` too)."""
    values = _file_values(env_file)
    return [f"{name}: {key_status(name, values)}" for name in MANAGED_KEYS]


def openalex_key_missing() -> bool:
    """Whether ``OPENALEX_API_KEY`` is unset in the environment and in ``.env`` (no value read out)."""
    line = next(ln for ln in status_lines(Path(ENV_FILENAME)) if ln.startswith("OPENALEX_API_KEY:"))
    return line.endswith("not set")


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of :func:`probe_scopus_key`: whether it worked, and a label with no response data."""

    ok: bool
    label: str


def probe_scopus_key(settings: Settings, *, http_client: httpx.Client | None = None) -> ProbeResult:
    """Make exactly one Scopus request and report ``ok``, ``401``, ``403``, another status or network.

    Args:
        settings: Supplies the key (and the optional institutional token).
        http_client: A client to use instead of a new one (tests pass a mock transport).

    Raises:
        ConfigError: If no Scopus key is set; no request is made.
    """
    key = require_scopus_key(settings).get_secret_value()
    token_value = settings.scopus_insttoken.get_secret_value() if settings.scopus_insttoken else ""
    if not (key.isascii() and token_value.isascii()):
        return ProbeResult(False, "the key or token contains non-ASCII characters; no request made")
    register_secret_for_redaction(key)
    headers = {"X-ELS-APIKey": key, "Accept": "application/json"}
    if settings.scopus_insttoken is not None:
        token = settings.scopus_insttoken.get_secret_value()
        register_secret_for_redaction(token)
        headers["X-ELS-Insttoken"] = token
    client = http_client if http_client is not None else httpx.Client(timeout=30.0)
    try:
        response = client.get(
            ScopusClient.SEARCH_ENDPOINT,
            params={"query": "TITLE-ABS-KEY(prisma)", "count": "1", "view": "STANDARD"},
            headers=headers,
        )
    except (httpx.HTTPError, UnicodeError) as exc:
        return ProbeResult(False, f"network error ({type(exc).__name__})")
    finally:
        if http_client is None:
            client.close()
    status = response.status_code
    if status == 200:
        return ProbeResult(True, "ok")
    if status == 401:
        return ProbeResult(False, "401: the key was rejected")
    if status == 403:
        return ProbeResult(
            False, "403: the key is not entitled here (check the institutional token)"
        )
    return ProbeResult(False, f"http {status}")


# -- commands -------------------------------------------------------------------------------------

credentials_app = typer.Typer(
    name="credentials",
    help="Store, inspect, test and remove API keys in the git-ignored .env (ADR 0042).",
    no_args_is_help=True,
)


def _env_file() -> Path:
    # Settings reads .env from the working directory, so this is the file that matters.
    return Path.cwd() / ENV_FILENAME


def _read_stdin_values(allowed: tuple[str, ...]) -> dict[str, str]:
    values: dict[str, str] = {}
    for number, raw in enumerate(sys.stdin.read().splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, sep, value = line.partition("=")
        if not sep or name.strip() not in allowed:
            # Never echo the line: a mistyped file may hold the bare key.
            raise ConfigError(f"--stdin line {number}: expected NAME=VALUE with NAME in {allowed}.")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]  # one matching pair of quotes, as in a .env line
        values[name.strip()] = value
    return values


def _prompted_values(keys: tuple[str, ...]) -> dict[str, str]:
    values: dict[str, str] = {}
    for position, name in enumerate(keys):
        hidden = name not in _NOT_SECRET
        if position == 0:
            values[name] = typer.prompt(name, hide_input=hidden).strip()
        else:
            answer = typer.prompt(
                f"{name} (optional, Enter to skip)",
                default="",
                show_default=False,
                hide_input=hidden,
            )
            if answer.strip():
                values[name] = answer.strip()
    return values


@credentials_app.command("set")
def set_credentials(
    service: Annotated[Service, typer.Argument(help="What to store a credential for.")],
    from_stdin: Annotated[
        bool, typer.Option("--stdin", help="Read NAME=VALUE lines from standard input.")
    ] = False,
    from_env: Annotated[
        bool, typer.Option("--from-env", help="Take the values from the process environment.")
    ] = False,
) -> None:
    """Store a credential in .env. The value is prompted (hidden), piped or taken from the environment.

    With --stdin, give NAME=VALUE lines (no `export`); one matching pair of surrounding quotes
    around VALUE is removed.
    """
    from prismabib.cli import _echo, _echo_err, _reporting_errors

    with _reporting_errors():
        if from_stdin and from_env:
            raise ConfigError("--stdin and --from-env cannot be combined.")
        keys = SERVICE_KEYS[service.value]
        if from_stdin:
            values = _read_stdin_values(keys)
        elif from_env:
            values = {k: v for k in keys if (v := os.environ.get(k, "").strip())}
        else:
            values = _prompted_values(keys)
        if keys[0] not in values:
            raise ConfigError(f"{keys[0]} is required and was not provided.")
        for name, value in values.items():
            if name not in _NOT_SECRET:
                register_secret_for_redaction(value)
        path = write_env(_env_file(), values)
        _echo(f"stored {', '.join(values)} in {path}")
        shadowed = [k for k in values if os.environ.get(k) and os.environ[k] != values[k]]
        if shadowed:
            _echo_err(f"note: {', '.join(shadowed)} is also set in the environment, which wins.")


@credentials_app.command("clear")
def clear_credentials(
    service: Annotated[Service, typer.Argument(help="What to remove the credential of.")],
) -> None:
    """Remove a service's keys from .env. A key set in the environment is not touched."""
    from prismabib.cli import _echo, _reporting_errors

    with _reporting_errors():
        env_file = _env_file()
        present = [k for k in SERVICE_KEYS[service.value] if _file_values(env_file).get(k)]
        if not present:
            _echo(f"nothing to clear: no {service.value} key in {env_file}")
        else:
            write_env(env_file, dict.fromkeys(present))
            _echo(f"removed {', '.join(present)} from {env_file}")
        still = [k for k in SERVICE_KEYS[service.value] if os.environ.get(k)]
        if still:
            _echo(f"{', '.join(still)} is set in the process environment; unset it there.")


@credentials_app.command("status")
def credentials_status(
    project: Annotated[
        str | None,
        typer.Option("--project", help="Also report what this project's mode does with them."),
    ] = None,
    root: Annotated[Path | None, typer.Option("--root", "-r", show_default=False)] = None,
) -> None:
    """Show which keys are set and where (environment or .env). Never prints a value."""
    from prismabib.cli import _echo, _reporting_errors
    from prismabib.project import Project
    from prismabib.source_mode import checked_mode

    with _reporting_errors():
        env_file = _env_file().resolve()
        problem = _ignore_problem(env_file)
        exists = "exists" if env_file.is_file() else "missing"
        _echo(f"file: {env_file} ({exists})")
        _echo("git-ignored by a tracked rule: " + ("yes" if problem is None else f"no ({problem})"))
        lines = status_lines(env_file)
        for line in lines:
            _echo(line)
        if project is not None:
            mode = checked_mode(Project.open(project, root=root))
            _echo(f"sources mode: {mode}")
            if mode == "openalex" and not lines[0].endswith("not set"):
                _echo(
                    "SCOPUS_API_KEY is stored but unused in this mode; "
                    "`prismabib credentials clear scopus` removes it from .env."
                )


@credentials_app.command("test")
def credentials_test(
    service: Annotated[ProbeService, typer.Argument(help="What to test (one request).")],
    project: Annotated[
        str | None, typer.Option("--project", help="Refuse if this project's mode has no Scopus.")
    ] = None,
    root: Annotated[Path | None, typer.Option("--root", "-r", show_default=False)] = None,
) -> None:
    """Make one Scopus request with the stored key and report ok, 401, 403 or a network error."""
    from prismabib.cli import _echo, _reporting_errors
    from prismabib.project import Project
    from prismabib.source_mode import require_capability

    del service  # only "scopus" exists
    with _reporting_errors():
        if project is not None:
            require_capability(Project.open(project, root=root), "scopus_key_test")
        result = probe_scopus_key(Settings())
        _echo(f"scopus: {result.label}")
        if not result.ok:
            raise typer.Exit(code=1)
