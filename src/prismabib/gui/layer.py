"""The layer every screen calls (GUI spec #312, ticket #319).

Plain functions over library code: no widgets, no subprocess, no terminal text. Screens call an
operation, and on failure pass the exception to :func:`translate`, which returns a sentence and the
next step. Step progress and step locks are functions of what is on disk.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Final, Self, TypeVar

import httpx
import yaml

from prismabib.capture.layout import NON_RUN_DIRNAMES, OPENALEX_SEARCH_DIRNAME, is_sealed
from prismabib.errors import (
    AuthError,
    CaptureCancelledError,
    ConfigError,
    EntitlementError,
    LogError,
    OperationCancelled,
    ProjectLockedError,
    ProjectVersionError,
    QuotaExceededError,
    RateLimitError,
    StepLockedError,
    UpstreamError,
    ValidationError,
)
from prismabib.gui.query_form_model import QueryInputs
from prismabib.gui.query_form_model import save_query as _save_query
from prismabib.onboarding import create_project as _create_project
from prismabib.prisma.events import CURRENT_SCHEMA_VERSION
from prismabib.project import Criteria, Project
from prismabib.source_mode import SourceMode, SourceModeEvent, set_mode
from prismabib.sources.openalex import DailyBudgetExhaustedError
from prismabib.user_settings import user_settings_path

# -- steps, progress and locks -------------------------------------------------------------------

STEPS: Final = (
    "source_mode",
    "query",
    "criteria",
    "capture",
    "build",
    "screening",
    "flow",
    "export",
)


def _nonblank(path: Path) -> bool:
    return path.is_file() and bool(path.read_bytes().strip())


def search_runs(project: Project) -> list[Path]:
    """Every search Run directory, in any state (sealed, unsealed or discarded)."""
    raw = project.raw_dir
    if not raw.is_dir():
        return []
    runs = [p for p in raw.iterdir() if p.is_dir() and p.name not in NON_RUN_DIRNAMES]
    openalex = raw / OPENALEX_SEARCH_DIRNAME
    if openalex.is_dir():
        runs += [p for p in openalex.iterdir() if p.is_dir()]
    return sorted(runs)


def _query_written(project: Project) -> bool:
    try:
        parsed = tomllib.loads((project.root / "project.toml").read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return False
    query, openalex = parsed.get("query", {}), parsed.get("openalex_query", {})
    return bool(
        query.get("terms") or query.get("compound_terms") or str(openalex.get("search", "")).strip()
    )


def _criteria_written(project: Project) -> bool:
    """Whether Criteria differ from the scaffold: another version, or any restriction set."""
    try:
        c = project.criteria
    except ConfigError:
        return False
    return (
        c.version != "0.1.0"
        or c.temporal.year_start != 1900
        or bool(c.subject_areas or c.doc_types.include or c.languages)
    )


def step_progress(project: Project) -> dict[str, bool]:
    """Which steps are done, in order, read from the Project's folder only."""
    screened = _nonblank(project.decisions_path)
    built = project.db_path.is_file()
    exports = project.root / "exports"
    return {
        "source_mode": True,  # a Project always has an effective mode (legacy when no table)
        "query": _query_written(project),
        "criteria": _criteria_written(project),
        "capture": any(is_sealed(run) for run in search_runs(project)),
        "build": built,
        "screening": screened,
        "flow": built and screened,
        "export": exports.is_dir() and any(p.is_file() for p in exports.rglob("*")),
    }


def first_unfinished(progress: dict[str, bool]) -> str | None:
    """The step a reopened Project resumes at, or ``None`` when every step is done."""
    return next((step for step, done in progress.items() if not done), None)


def locks(project: Project) -> dict[str, str | None]:
    """For each lockable step, the reason it is read-only, or ``None`` when it can be changed."""
    decided = _nonblank(project.decisions_path)
    ran = bool(search_runs(project))
    source = None
    if decided:
        source = "The Source mode is fixed because a Decision has been logged."
    elif ran:
        source = (
            "The Source mode is fixed because a Run exists (a discarded Run still counts). "
            "A different mode needs a new Project."
        )
    return {
        "source_mode": source,
        "query": (
            "The query is read-only because a Run has started. "
            "A different search needs a new Project."
            if ran
            else None
        ),
        "criteria": (
            "The Criteria are read-only because a Decision has been logged. "
            "Amending them after screening starts is not supported."
            if decided
            else None
        ),
    }


def require_unlocked(project: Project, step: str) -> None:
    """Raise :class:`StepLockedError` with the reason if ``step`` is read-only."""
    reason = locks(project)[step]
    if reason:
        raise StepLockedError(reason)


# -- opening and the first operations ------------------------------------------------------------

#: The Project format version: the ``schema_version`` its logs are written with. This
#: prismabib opens the current one and the one before it; anything else is refused.
CURRENT_PROJECT_VERSION: Final = CURRENT_SCHEMA_VERSION
_LOG_GLOBS: Final = ("decisions/*.jsonl", "taxonomy/*.jsonl")


def _versions_found(root: Path) -> list[int]:
    found: list[int] = []
    for pattern in _LOG_GLOBS:
        for path in sorted(root.glob(pattern)):
            try:
                lines = path.read_bytes().decode("utf-8").split("\n")[:-1]  # as the logs do
                for line in lines:
                    version = json.loads(line)["schema_version"]
                    if type(version) is not int:
                        raise TypeError("schema_version is not an integer")
                    found.append(version)
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise ProjectVersionError(
                    "unreadable", None, f"{path.name} could not be read."
                ) from exc
    return found


def check_project_version(root: Path) -> int:
    """Read a Project's format version without writing anything.

    Returns:
        The oldest version found (the current one for a Project with no events yet).

    Raises:
        ProjectVersionError: If a log is unreadable, or its version is newer than the current
            one or older than the one before it.
    """
    found = _versions_found(root) or [CURRENT_PROJECT_VERSION]
    if max(found) > CURRENT_PROJECT_VERSION:
        raise ProjectVersionError("newer", str(max(found)))
    if min(found) < CURRENT_PROJECT_VERSION - 1:
        raise ProjectVersionError("older", str(min(found)))
    return min(found)


def open_project(slug: str, *, root: Path) -> Project:
    """Open a Project this version can read; the folder is never modified.

    Raises:
        ConfigError: If there is no such Project.
        ProjectVersionError: If its format is newer, too old, or unreadable.
    """
    project = Project.open(slug, root=root)
    check_project_version(project.root)
    return project


def _require_reviewer(reviewer: str) -> str:
    if not reviewer.strip():
        raise ValidationError("Enter the reviewer name: every Decision will carry it.")
    return reviewer.strip()


def create_project(
    slug: str, *, title: str, root: Path, source_mode: SourceMode, reviewer: str
) -> Project:
    """Create a Project; refuses a blank reviewer name or a folder that already exists."""
    name = _require_reviewer(reviewer)
    if (root / slug).exists():
        raise ValidationError(f"A Project named {slug!r} already exists. Choose another name.")
    return _create_project(slug, title=title, root=root, source_mode=source_mode, reviewer=name)


# -- the single-writer lock ----------------------------------------------------------------------

LOCK_NAME: Final = ".prismabib.lock"
_F = TypeVar("_F", bound=Callable[..., object])
#: Names of the layer functions that change a Project; each refuses a read-only session.
WRITE_OPERATIONS: Final[list[str]] = []


def _pid_alive(pid: int) -> bool:
    if sys.platform == "win32":  # os.kill would terminate the process there
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined,unused-ignore]
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if handle:
            kernel32.CloseHandle(handle)
        return bool(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Session:
    """One window's hold on a Project: write rights, or a read-only view with the reason.

    Open with :func:`open_session`; call :meth:`close` (or use ``with``) to release the lock.
    A lock left by a crashed process makes the session read-only until :meth:`take_over` is
    called with ``confirmed=True``.
    """

    def __init__(self, project: Project) -> None:
        self.project = project
        self.reason: str | None = None
        self.stale = False
        self._acquire()

    @property
    def read_only(self) -> bool:
        return self.reason is not None

    @property
    def _lock(self) -> Path:
        return self.project.root / LOCK_NAME

    def _acquire(self) -> None:
        try:
            fd = os.open(self._lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            self._explain_holder()
            return
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"pid": os.getpid()}, handle)
        self.reason, self.stale = None, False

    def _explain_holder(self) -> None:
        try:
            pid = int(json.loads(self._lock.read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError, TypeError):
            pid = -1
        self.stale = pid <= 0 or not _pid_alive(pid)
        self.reason = (
            "A window that was working on this Project closed unexpectedly and left its lock."
            if self.stale
            else "Another window is working on this Project, so this one is read-only."
        )

    def take_over(self, *, confirmed: bool) -> None:
        """Take write rights from a crashed window's lock, only after the researcher confirms."""
        if not self.read_only:
            return
        if not self.stale:
            raise ProjectLockedError(self.reason or "")
        if not confirmed:
            raise ValidationError("Confirm taking over the lock left by the closed window.")
        self._lock.unlink(missing_ok=True)
        self._acquire()

    def close(self) -> None:
        """Release the lock if this session holds it; the session is read-only afterwards."""
        if not self.read_only:
            self._lock.unlink(missing_ok=True)
            self.reason = "This session is closed."

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def require_writable(self) -> Project:
        """The Project, or :class:`ProjectLockedError` with the reason if read-only."""
        if self.reason is not None:
            raise ProjectLockedError(self.reason)
        return self.project


def open_session(slug: str, *, root: Path) -> Session:
    """Open a Project (see :func:`open_project`) and try to take its write lock."""
    return Session(open_project(slug, root=root))


_CRITERIA_HEADER: Final = (
    "# Eligibility criteria, written by the prismabib app. You can edit this file by hand.\n"
    "# An empty list means no restriction. Unknown keys are refused.\n"
)


def write_operation(fn: _F) -> _F:
    """Register ``fn`` as a write operation: its first argument is a :class:`Session`."""
    WRITE_OPERATIONS.append(fn.__name__)
    return fn


@write_operation
def set_source_mode(session: Session, mode: SourceMode, *, reviewer: str) -> SourceModeEvent | None:
    """Change the Source mode unless the session is read-only or the step is locked."""
    project = session.require_writable()
    name = _require_reviewer(reviewer)
    require_unlocked(project, "source_mode")
    return set_mode(project, mode, actor=name)


@write_operation
def save_query(session: Session, inputs: QueryInputs) -> None:
    """Save the query (terms, years, subject areas) unless read-only or a Run has started."""
    project = session.require_writable()
    require_unlocked(project, "query")
    _save_query(project, inputs)


@write_operation
def save_criteria(session: Session, criteria: Criteria) -> Path:
    """Write ``criteria.yaml`` unless the session is read-only or a Decision has been logged.

    The file stays the hand-editable source of truth; ``criteria`` is already validated by the
    library's own model. Bytes are written with ``\\n`` endings on every platform.
    """
    project = session.require_writable()
    require_unlocked(project, "criteria")
    path = project.root / "criteria.yaml"
    text = _CRITERIA_HEADER + yaml.safe_dump(criteria.model_dump(), sort_keys=False)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(text.encode("utf-8"))
    tmp.replace(path)
    return path


# -- errors, in the researcher's words -----------------------------------------------------------


class ErrorClass(StrEnum):
    """Every kind of failure a screen can show."""

    INPUT = "input"
    PREREQUISITE = "prerequisite"
    CREDENTIALS = "credentials"
    QUOTA = "quota"
    NETWORK = "network"
    PROJECT_LOCKED = "project_locked"
    STEP_LOCKED = "step_locked"
    VERSION = "version"
    CANCELLED = "cancelled"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True)
class UserMessage:
    """What the researcher reads: what happened, and what to do next."""

    error_class: ErrorClass
    sentence: str
    next_step: str


#: ``None`` means the exception's own message is the sentence (it is written for researchers).
SENTENCES: Final[dict[ErrorClass, str | None]] = {
    ErrorClass.INPUT: None,
    ErrorClass.PREREQUISITE: None,
    ErrorClass.CREDENTIALS: "A key is missing, or the service did not accept it.",
    ErrorClass.QUOTA: "The quota for this service is used up for now.",
    ErrorClass.NETWORK: "The connection to the service was interrupted or is offline.",
    ErrorClass.PROJECT_LOCKED: "Another window is working on this Project.",
    ErrorClass.STEP_LOCKED: None,
    ErrorClass.VERSION: None,
    ErrorClass.CANCELLED: "Stopped as you asked. Nothing was lost.",
    ErrorClass.UNEXPECTED: "Something unexpected went wrong.",
}
NEXT_STEPS: Final[dict[ErrorClass, str]] = {
    ErrorClass.INPUT: "Correct the entry and try again.",
    ErrorClass.PREREQUISITE: "Finish the earlier step it names, then try again.",
    ErrorClass.CREDENTIALS: "Open Settings, check the key for this service, then try again.",
    ErrorClass.QUOTA: (
        "Wait for the allowance to reset (the size check shows when), or narrow the search."
    ),
    ErrorClass.NETWORK: (
        "Check your connection and run the step again; pages already fetched are kept."
    ),
    ErrorClass.PROJECT_LOCKED: "Close the other window, or open this Project there read-only.",
    ErrorClass.STEP_LOCKED: "Nothing to do here; to change it, start a new Project.",
    ErrorClass.VERSION: "The Project folder was not changed.",
    ErrorClass.CANCELLED: "Run the step again when you are ready; it resumes where it stopped.",
    ErrorClass.UNEXPECTED: "Try again; if it keeps happening, send the log file named above.",
}
_VERSION_NEXT: Final = {
    "older": "Open it with the prismabib that made it, or start a new Project with the same query.",
    "newer": "Update prismabib, then open the Project again.",
    "unreadable": "Check decisions/ and taxonomy/ for a damaged or hand-edited file, then retry.",
}
_VERSION_WHAT: Final = {
    "older": "was made by an older prismabib than this one can open",
    "newer": "was made by a newer prismabib than this one",
    "unreadable": "has a file this prismabib cannot read",
}


def _classify(exc: BaseException) -> ErrorClass:
    # Order matters: the more specific class comes first.
    if isinstance(exc, ProjectVersionError):
        return ErrorClass.VERSION
    if isinstance(exc, LogError) and "schema_version" in str(exc):
        return ErrorClass.VERSION
    if isinstance(exc, StepLockedError):
        return ErrorClass.STEP_LOCKED
    if isinstance(exc, ProjectLockedError):
        return ErrorClass.PROJECT_LOCKED
    if isinstance(exc, CaptureCancelledError | OperationCancelled):
        return ErrorClass.CANCELLED
    if isinstance(exc, QuotaExceededError | DailyBudgetExhaustedError):
        return ErrorClass.QUOTA
    if isinstance(exc, AuthError | EntitlementError):
        return ErrorClass.CREDENTIALS
    if isinstance(exc, RateLimitError | UpstreamError | httpx.TransportError | ConnectionError):
        return ErrorClass.NETWORK
    if isinstance(exc, ValidationError):
        return ErrorClass.INPUT
    if isinstance(exc, ConfigError):
        return ErrorClass.CREDENTIALS if "_API_KEY" in str(exc) else ErrorClass.PREREQUISITE
    return ErrorClass.UNEXPECTED


def log_path() -> Path:
    """Where unexpected errors are logged: beside the per-user settings file."""
    return user_settings_path().with_name("gui.log")


def _log_unexpected(exc: BaseException) -> str:
    # Only the exception type is written: its message or traceback could quote a key.
    try:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{datetime.now(UTC).isoformat()} unexpected {type(exc).__name__}\n")
    except (OSError, ConfigError):
        return "The log could not be written either."
    return f"The log is {path}."


def translate(exc: BaseException) -> UserMessage:
    """Turn any exception into a sentence and the next step; never a traceback."""
    kind = _classify(exc)
    sentence = SENTENCES[kind] or str(exc)
    next_step = NEXT_STEPS[kind]
    if kind is ErrorClass.VERSION:
        version = exc if isinstance(exc, ProjectVersionError) else None
        what = _VERSION_WHAT[version.kind] if version else "was written by another prismabib"
        seen = f" (format version {version.found})" if version and version.found else ""
        sentence = f"This Project {what}{seen}. Its folder has not been changed."
        next_step = _VERSION_NEXT[version.kind] if version else next_step
    elif kind is ErrorClass.UNEXPECTED:
        sentence = f"{sentence} {_log_unexpected(exc)}"
    return UserMessage(kind, sentence, next_step)
