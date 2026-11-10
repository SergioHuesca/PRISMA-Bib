"""What New Project and the stepper decide, without widgets."""

from __future__ import annotations

import json
import re
import tomllib
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from prismabib.errors import ConfigError, ValidationError
from prismabib.gui import layer
from prismabib.gui.path_limit import path_refusal
from prismabib.onboarding import source_mode_from_answer
from prismabib.project import Project
from prismabib.source_mode import SourceMode

SCOPUS_CHOICES: Final = ("Yes", "No", "Not sure")

LABELS: Final = {
    "source_mode": "Source mode and keys",
    "query": "Query",
    "criteria": "Criteria",
    "capture": "Size check and capture",
    "build": "Build",
    "screening": "Screening",
    "flow": "Flow counts",
    "export": "Export",
}


def derive_slug(name: str) -> str:
    """The folder name for a Project name: lower-case ASCII letters and digits joined by hyphens.

    Raises:
        ValidationError: If nothing usable is left (a path, a blank or only punctuation).
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")
    if not slug:
        raise ValidationError("Enter a Project name with at least one letter or digit.")
    return slug


def folder_refusal(name: str, root: Path) -> str | None:
    """Why ``name`` cannot be used (blank, or its folder exists), or ``None`` when it can."""
    try:
        slug = derive_slug(name)
    except ValidationError as exc:
        return str(exc)
    if (root / slug).exists():
        return f"A folder named {slug!r} already exists here. Choose another name."
    return path_refusal(root, slug)


def mode_for_choice(choice: str | None, *, fill_gaps: bool) -> SourceMode:
    """The Source mode for the answer to "Do you have Scopus access?" (the CLI's rule).

    Raises:
        ValidationError: If the question has not been answered; it has no default.
    """
    if choice not in SCOPUS_CHOICES:
        raise ValidationError("Answer whether you have Scopus access: Yes, No or Not sure.")
    return source_mode_from_answer(choice, fill_gaps=fill_gaps)


@dataclass(frozen=True)
class StepRow:
    """One stepper line: its state, what was set, and why it is read-only if it is."""

    key: str
    label: str
    done: bool
    current: bool
    lock: str | None
    detail: str


def _toml(project: Project) -> dict[str, dict[str, object]]:
    try:
        return tomllib.loads((project.root / "project.toml").read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def _has_discarded_openalex_run(project: Project) -> bool:
    for run in layer.search_runs(project):
        try:
            if json.loads((run / "progress.json").read_text(encoding="utf-8")).get("discarded"):
                return True
        except (OSError, ValueError, AttributeError):
            continue
    return False


def _detail(project: Project, key: str, done: bool) -> str:
    if key == "source_mode":
        text = f"Source mode: {project.source_mode}."
        if _has_discarded_openalex_run(project):
            text += " A discarded OpenAlex run is still on disk, so a change is still blocked."
        return text
    if not done:
        return "Not done yet."
    parsed = _toml(project)
    if key == "query":
        openalex = str(parsed.get("openalex_query", {}).get("search", "")).strip()
        return f"Query: {openalex}" if openalex else "A query is saved in project.toml."
    if key == "criteria":
        try:
            return f"Criteria version {project.criteria.version} is saved."
        except ConfigError:
            return "Criteria are saved."
    return "Done."


def stepper_rows(project: Project) -> list[StepRow]:
    """Every step in order, a function of the Project on disk only."""
    progress = layer.step_progress(project)
    current = layer.first_unfinished(progress)
    locks = layer.locks(project)
    return [
        StepRow(
            key,
            LABELS[key],
            done,
            key == current,
            locks.get(key),
            _detail(project, key, done),
        )
        for key, done in progress.items()
    ]
