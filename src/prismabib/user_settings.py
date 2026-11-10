"""The per-user settings file: API keys and the few non-secret settings, outside any Project.

It lives in the operating system's user-config folder, so it is outside every repository and the
git-ignore check that guards an in-repo ``.env`` does not apply to it. Where it and a Project's
environment file both define a key, the Project's value wins. Every path is explicit: nothing here
looks at the working directory. The process environment still beats both files (pydantic-settings).
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Final

from dotenv import dotenv_values

from prismabib.config import Settings
from prismabib.credentials import MANAGED_KEYS, write_env
from prismabib.errors import ConfigError
from prismabib.sources.scopus import register_secret_for_redaction

#: Test-only: points the file elsewhere so tests never touch the real user folder.
USER_FILE_OVERRIDE_VAR: Final = "PRISMABIB_USER_SETTINGS_FILE"
PROJECTS_FOLDER_KEY: Final = "PRISMABIB_PROJECTS_ROOT"
REVIEWER_NAME_KEY: Final = "PRISMABIB_REVIEWER_NAME"
_PLAIN_KEYS: Final = (PROJECTS_FOLDER_KEY, REVIEWER_NAME_KEY)
_ALLOWED: Final = (*MANAGED_KEYS, *_PLAIN_KEYS)


def user_settings_path() -> Path:
    """Where the per-user file is (it need not exist yet).

    Raises:
        ConfigError: If the location is inside a Project folder (a folder with ``project.toml``).
    """
    override = os.environ.get(USER_FILE_OVERRIDE_VAR)
    if override:
        path = Path(override).resolve()
    else:
        home = Path.home()
        if sys.platform == "win32":
            base = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming")
        elif sys.platform == "darwin":
            base = home / "Library" / "Application Support"
        else:
            base = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
        path = (base / "prismabib" / "settings.env").resolve()
    if any((folder / "project.toml").exists() for folder in path.parents):
        raise ConfigError(f"The per-user settings file {path} is inside a Project folder.")
    return path


def read_user_setting(name: str, user_file: Path | None = None) -> str | None:
    """The value of ``name`` in the per-user file, or ``None``; a key's value is the caller's."""
    path = user_file if user_file is not None else user_settings_path()
    return dotenv_values(path).get(name) if path.is_file() else None


def write_user_settings(updates: Mapping[str, str | None], user_file: Path | None = None) -> Path:
    """Set (``None`` removes) keys in the per-user file, creating it; no git check applies."""
    path = user_file if user_file is not None else user_settings_path()
    for name, value in updates.items():
        if value and name in MANAGED_KEYS and name != "UNPAYWALL_EMAIL":
            register_secret_for_redaction(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    return write_env(path, updates, allowed=_ALLOWED, check_ignored=False)


def load_settings(project_env_file: Path | None = None, user_file: Path | None = None) -> Settings:
    """Settings from the per-user file, then ``project_env_file`` (if given), which wins."""
    files = [user_file if user_file is not None else user_settings_path()]
    if project_env_file is not None:
        files.append(project_env_file)
    return Settings(_env_file=tuple(files))
