"""What the home screen decides, without widgets.

The first-run prerequisite check, the Projects folder (resolve, validate, remember) and the list of
Projects found there. Nothing here writes inside the installed package or anywhere but the folder
the researcher chose and the per-user settings file.
"""

from __future__ import annotations

import platform
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

import prismabib
from prismabib.doctor_checks import (
    SMART_APP_CONTROL_HINT,
    Check,
    git_check,
    projects_root_check,
    python_check,
    uv_check,
)
from prismabib.errors import ConfigError
from prismabib.gui.path_limit import MAX_PATH_CHARS
from prismabib.project import Project
from prismabib.user_settings import (
    FIRST_RUN_SEEN_KEY,
    PROJECTS_FOLDER_KEY,
    read_user_setting,
    write_user_settings,
)

# -- first-run prerequisites ---------------------------------------------------------------------

_PROBLEMS: Final = {
    "python": (
        "Python 3.11 or newer was not found.",
        "Install Python 3.11 or newer from python.org, then start prismabib again.",
    ),
    "git": (
        "git was not found.",
        "Install git from git-scm.com, then start prismabib again.",
    ),
    "uv": (
        "uv was not found.",
        "Install uv (see docs.astral.sh/uv), then start prismabib again.",
    ),
}
APP_CONTROL_HINT: Final = SMART_APP_CONTROL_HINT.replace("  hint: ", "").strip()
FIREWALL_NOTE: Final = (
    "The first time the app starts, Windows may ask whether to allow it through the firewall. "
    "Choose Private networks only (or Cancel): the app talks to this computer only, so it works "
    "either way."
)


@dataclass(frozen=True)
class Problem:
    """A failed prerequisite: what is wrong, and how to fix it."""

    name: str
    sentence: str
    fix: str


@dataclass(frozen=True)
class PrerequisiteReport:
    """The failed prerequisites, and the application-control hint when it applies."""

    problems: list[Problem] = field(default_factory=list)
    app_control_hint: str | None = None


def default_checks() -> dict[str, Check]:
    """Run the real checks: Python, git and uv."""
    return {"python": python_check(), "git": git_check(), "uv": uv_check()}


def prerequisite_problems(
    checks: Mapping[str, Check], *, windows: bool | None = None, first_run: bool
) -> PrerequisiteReport:
    """Each failed check with its fix. The Windows hint shows on Windows on first run or a failure."""
    problems = [Problem(name, *_PROBLEMS[name]) for name, c in checks.items() if c.level == "FAIL"]
    if windows is None:
        windows = platform.system() == "Windows"
    hint = APP_CONTROL_HINT if windows and (problems or first_run) else None
    return PrerequisiteReport(problems, hint)


def take_first_run() -> bool:
    """True the first time it is called for this user (and records that), False afterwards."""
    if read_user_setting(FIRST_RUN_SEEN_KEY):
        return False
    write_user_settings({FIRST_RUN_SEEN_KEY: "1"})
    return True


# -- the Projects folder -------------------------------------------------------------------------

_SYNC_NAMES: Final = (
    "onedrive",
    "dropbox",
    "google drive",
    "icloud",
    "box sync",
    "mobile documents",
)


def is_synced(path: Path) -> bool:
    """Whether a folder on ``path`` is under a known sync service (by folder name)."""
    return any(part.lower().startswith(_SYNC_NAMES) for part in path.parts)


@dataclass(frozen=True)
class FolderCheck:
    """``refusal`` blocks the choice; ``warning`` does not."""

    refusal: str | None = None
    warning: str | None = None


def check_folder(path: Path, *, is_synced: Callable[[Path], bool] = is_synced) -> FolderCheck:
    """Whether ``path`` can be the Projects folder, decided before any capture can start."""
    path = path.expanduser().absolute()
    text = str(path)
    if len(text) > MAX_PATH_CHARS:
        return FolderCheck(
            refusal=(
                f"This path is {len(text)} characters; the limit is {MAX_PATH_CHARS} "
                "(Windows stops at 260 and a Project needs room inside the folder). "
                "Choose a shorter path."
            )
        )
    if Path(prismabib.__file__).parent in (path, *path.parents):
        return FolderCheck(refusal="That folder is inside the installed program. Choose another.")
    probe = projects_root_check(path)
    if probe.level == "FAIL":
        return FolderCheck(refusal=f"prismabib cannot write to this folder ({probe.text}).")
    warning = None
    if is_synced(path):
        warning = (
            "This folder is in a synced location. Sync can lock or rewrite files while prismabib "
            "works; a folder outside it is safer."
        )
    return FolderCheck(warning=warning)


@dataclass(frozen=True)
class FolderState:
    """The folder in use and, when it is a remembered folder that is gone, why it cannot be."""

    path: Path
    missing: str | None = None


def resolve_folder(explicit: Path | None) -> FolderState:
    """An explicit root, else the remembered folder, else ``projects`` in the current directory."""
    if explicit is not None:
        return FolderState(explicit)
    try:
        remembered = read_user_setting(PROJECTS_FOLDER_KEY)
    except ConfigError:
        remembered = None
    if remembered:
        path = Path(remembered)
        if not path.is_dir():
            return FolderState(
                path,
                f"The Projects folder {path} no longer exists. Choose another folder, "
                "or create it again.",
            )
        return FolderState(path)
    return FolderState(Path.cwd() / "projects")


def use_folder(
    path: Path, *, create: bool, is_synced: Callable[[Path], bool] = is_synced
) -> FolderCheck:
    """Validate ``path``, create it when asked, and remember it. A refusal changes nothing."""
    path = path.expanduser().absolute()
    result = check_folder(path, is_synced=is_synced)
    if result.refusal:
        return result
    if not path.is_dir():
        if not create:
            return FolderCheck(refusal=f"{path} does not exist. Use Create to make it.")
        path.mkdir(parents=True, exist_ok=True)
    write_user_settings({PROJECTS_FOLDER_KEY: str(path)})
    return result


@dataclass(frozen=True)
class ProjectEntry:
    slug: str
    title: str


def list_projects(root: Path) -> list[ProjectEntry]:
    """The Projects in ``root`` (folders holding a ``project.toml``), by slug."""
    if not root.is_dir():
        return []
    return [
        ProjectEntry(p.name, Project(slug=p.name, root=p).title)
        for p in sorted(root.iterdir())
        if (p / "project.toml").is_file()
    ]
