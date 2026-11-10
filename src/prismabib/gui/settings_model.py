"""What the Settings view shows and changes (GUI spec #312, ticket #323)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from prismabib.gui import home_model
from prismabib.gui.layer import _require_reviewer
from prismabib.user_settings import (
    REVIEWER_NAME_KEY,
    read_user_setting,
    user_settings_path,
    write_user_settings,
)


@dataclass(frozen=True)
class Locations:
    """Where the researcher's data and the per-user file are; what the uninstall page points to."""

    projects_folder: Path
    settings_file: Path


def locations(root: Path | None = None) -> Locations:
    return Locations(home_model.resolve_folder(root).path, user_settings_path())


def reviewer_name() -> str:
    """The reviewer name kept in the per-user file, or an empty string."""
    return read_user_setting(REVIEWER_NAME_KEY) or ""


def save_reviewer_name(name: str) -> str:
    """Store ``name`` (stripped) in the per-user file and return it.

    Raises:
        ValidationError: If the name is blank.
    """
    name = _require_reviewer(name)
    write_user_settings({REVIEWER_NAME_KEY: name})
    return name
