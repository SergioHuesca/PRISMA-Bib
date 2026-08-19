"""Project scaffolding and the machine-readable eligibility criteria.

BUILD_PLAN §2.3 (lines 168-177) defines the ``projects/<slug>/`` on-disk
layout; §3.1 (lines 318-368) defines the ``project.toml`` and
``criteria.yaml`` formats. This module owns both: the :class:`Project`
handle that resolves paths and creates the skeleton, and the
:class:`Criteria` model parsed from ``criteria.yaml``.

The on-disk shape created by :meth:`Project.init` is pinned by
``tests/conftest.py``'s ``tmp_project`` fixture (Stage 0), which documents
itself as the fixed point Stage 1 builds on:

```
<root>/raw/
<root>/store/
<root>/decisions/
<root>/taxonomy/rules/
<root>/fulltext/
<root>/exports/
<root>/project.toml
<root>/criteria.yaml
<root>/decisions/decisions.jsonl
```
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, field_validator
from pydantic import ValidationError as PydanticValidationError

from prismabib.config import Settings
from prismabib.errors import ConfigError

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([-][0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$")


class TemporalCriteria(BaseModel):
    """The inclusive year window of the criteria.yaml ``temporal`` block."""

    year_start: int
    year_end: int


class DocTypeCriteria(BaseModel):
    """The ``doc_types`` block of ``criteria.yaml``."""

    include: list[str]
    conference_whitelist: list[str] = []


class ManualScreeningCriteria(BaseModel):
    """A manual-screening block (``manual_abstract`` or ``manual_fulltext``)."""

    exclude_reason_codes: list[str]


class Criteria(BaseModel):
    """The machine-readable Inclusion/Exclusion Matrix (BUILD_PLAN §3.1, lines 342-366).

    ``version`` is required and semantically versioned. Any change to
    inclusion logic bumps it; every decision event later records the
    ``criteria_version`` under which it was made, which is what makes
    protocol amendments auditable rather than silently retroactive.
    """

    version: str
    temporal: TemporalCriteria
    subject_areas: list[str]
    doc_types: DocTypeCriteria
    languages: list[str]
    manual_abstract: ManualScreeningCriteria
    manual_fulltext: ManualScreeningCriteria

    @field_validator("version")
    @classmethod
    def _require_semantic_version(cls, value: str) -> str:
        """Reject a ``version`` that is not ``MAJOR.MINOR.PATCH``-shaped.

        Args:
            value: The raw ``version`` string from ``criteria.yaml``.

        Returns:
            ``value`` unchanged, once validated.

        Raises:
            ValueError: If ``value`` is not a syntactically valid semantic
                version. Pydantic wraps this into a ``ValidationError``
                (missing ``version`` entirely is already rejected by
                Pydantic itself, since the field has no default).
        """
        if _SEMVER_RE.match(value) is None:
            raise ValueError(
                f"version {value!r} is not a semantic version (expected MAJOR.MINOR.PATCH)"
            )
        return value


_DEFAULT_CRITERIA_YAML = """\
version: 0.1.0
temporal:
  year_start: 1900
  year_end: {year}
subject_areas: []
doc_types:
  include: []
  conference_whitelist: []
languages: []
manual_abstract:
  exclude_reason_codes: []
manual_fulltext:
  exclude_reason_codes: []
"""


def _default_criteria_yaml() -> str:
    """Render the default ``criteria.yaml`` content written by :meth:`Project.init`.

    Returns:
        YAML text for a syntactically valid, minimally-populated
        :class:`Criteria` -- a real project is expected to edit this
        immediately, but ``Project.criteria`` must not fail on a
        freshly-initialised project.
    """
    return _DEFAULT_CRITERIA_YAML.format(year=datetime.now(UTC).year)


def _default_project_toml(slug: str, title: str) -> str:
    """Render the default ``project.toml`` content written by :meth:`Project.init`.

    Args:
        slug: The project slug.
        title: The human-readable project title.

    Returns:
        TOML text matching the BUILD_PLAN §3.1 ``[project]`` table shape.
    """
    created = datetime.now(UTC).date().isoformat()
    escaped_title = title.replace('"', '\\"')
    return (
        "[project]\n"
        f'slug = "{slug}"\n'
        f'title = "{escaped_title}"\n'
        f"created = {created}\n"
        "track_decisions = true\n"
    )


def _resolve_projects_root(root: Path | None) -> Path:
    """Resolve the projects root directory that ``<slug>`` is created under.

    Args:
        root: An explicit override, or ``None`` to read
            ``PRISMABIB_PROJECTS_ROOT`` from :class:`~prismabib.config.Settings`
            (BUILD_PLAN §3.1 line 315, default ``./projects``).

    Returns:
        The resolved projects root directory. Not required to exist yet.

    Raises:
        ConfigError: If ``root`` is ``None`` and environment configuration
            cannot be loaded (see :class:`~prismabib.config.Settings`).
    """
    if root is not None:
        return root
    return Settings().prismabib_projects_root


def _read_toml(path: Path) -> dict[str, Any]:
    """Parse a TOML file, translating a malformed file into a :class:`ConfigError`.

    Args:
        path: Path to the TOML file.

    Returns:
        The parsed TOML document.

    Raises:
        ConfigError: If ``path`` does not contain valid TOML.
    """
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path} is not valid TOML: {exc}") from exc


@dataclass
class Project:
    """A handle onto a single ``projects/<slug>/`` directory tree.

    Attributes:
        slug: The project's slug, also its directory name under the
            projects root.
        root: The resolved ``projects/<slug>/`` directory.
    """

    slug: str
    root: Path

    @classmethod
    def init(cls, slug: str, *, title: str, root: Path | None = None) -> Project:
        """Create (or reuse) the full §2.3 project skeleton.

        Idempotent: calling this twice for the same ``slug``/``root`` never
        raises, never duplicates a directory, and never truncates or
        overwrites an existing ``project.toml``, ``criteria.yaml``, or
        ``decisions.jsonl`` -- those are hand-edited methodology and human
        labour (BUILD_PLAN §2.5) and must survive a re-run untouched.

        Args:
            slug: The project slug (also the directory name).
            title: The human-readable project title, written into a fresh
                ``project.toml``. Ignored if ``project.toml`` already
                exists.
            root: The projects root directory to create ``<slug>`` under.
                Defaults to ``PRISMABIB_PROJECTS_ROOT`` from
                :class:`~prismabib.config.Settings` when omitted.

        Returns:
            A :class:`Project` handle onto the (now-existing) skeleton.

        Raises:
            ConfigError: If ``root`` is ``None`` and environment
                configuration cannot be loaded.
        """
        projects_root = _resolve_projects_root(root)
        project_root = projects_root / slug

        for relative in ("raw", "store", "decisions", "taxonomy/rules", "fulltext", "exports"):
            (project_root / relative).mkdir(parents=True, exist_ok=True)

        project_toml_path = project_root / "project.toml"
        if not project_toml_path.exists():
            project_toml_path.write_text(_default_project_toml(slug, title), encoding="utf-8")

        criteria_yaml_path = project_root / "criteria.yaml"
        if not criteria_yaml_path.exists():
            criteria_yaml_path.write_text(_default_criteria_yaml(), encoding="utf-8")

        decisions_jsonl_path = project_root / "decisions" / "decisions.jsonl"
        decisions_jsonl_path.touch(exist_ok=True)

        return cls(slug=slug, root=project_root)

    @classmethod
    def open(cls, slug: str, *, root: Path | None = None) -> Project:
        """Open an already-initialised project.

        Args:
            slug: The project slug.
            root: The projects root directory ``<slug>`` lives under.
                Defaults to ``PRISMABIB_PROJECTS_ROOT`` from
                :class:`~prismabib.config.Settings` when omitted.

        Returns:
            A :class:`Project` handle onto the existing directory.

        Raises:
            ConfigError: If no project directory exists at the resolved
                path, or it exists but is missing ``project.toml`` or has an
                unparseable one. The message names the expected path.
        """
        projects_root = _resolve_projects_root(root)
        project_root = projects_root / slug

        if not project_root.is_dir():
            raise ConfigError(
                f"No prismabib project named {slug!r} found: expected a directory "
                f"at {project_root} created by Project.init({slug!r}, ...)."
            )

        project_toml_path = project_root / "project.toml"
        if not project_toml_path.is_file():
            raise ConfigError(
                f"Project directory {project_root} is missing its project.toml "
                f"(expected at {project_toml_path})."
            )
        _read_toml(project_toml_path)

        return cls(slug=slug, root=project_root)

    @property
    def criteria(self) -> Criteria:
        """Parse and return this project's ``criteria.yaml``.

        Re-reads the file on every access rather than caching, so an
        amendment to ``criteria.yaml`` is always reflected immediately --
        the log-fold PRISMA model (§2.2) depends on the current version
        being authoritative, not a value captured at ``Project.open`` time.

        Returns:
            The parsed :class:`Criteria`.

        Raises:
            ConfigError: If ``criteria.yaml`` does not exist, is not valid
                YAML, or does not validate as :class:`Criteria` (including a
                missing or non-semantic ``version``).
        """
        path = self.root / "criteria.yaml"
        if not path.is_file():
            raise ConfigError(
                f"criteria.yaml not found at {path}; run Project.init({self.slug!r}, ...) first."
            )
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(f"{path} is not valid YAML: {exc}") from exc

        try:
            return Criteria.model_validate(raw or {})
        except PydanticValidationError as exc:
            raise ConfigError(f"{path} does not satisfy the criteria.yaml schema: {exc}") from exc

    @property
    def raw_dir(self) -> Path:
        """The Layer 0 raw-capture directory, ``<root>/raw``.

        Returns:
            The path, which is not guaranteed to exist unless
            :meth:`Project.init` has been called.
        """
        return self.root / "raw"

    @property
    def db_path(self) -> Path:
        """The Layer 1 DuckDB store file, ``<root>/store/corpus.duckdb``.

        Returns:
            The path. The file itself is created later, by the Stage 2
            store-build step, not by :meth:`Project.init`.
        """
        return self.root / "store" / "corpus.duckdb"

    @property
    def decisions_path(self) -> Path:
        """The Layer 2 append-only decision log, ``<root>/decisions/decisions.jsonl``.

        Returns:
            The path, created empty by :meth:`Project.init`.
        """
        return self.root / "decisions" / "decisions.jsonl"
