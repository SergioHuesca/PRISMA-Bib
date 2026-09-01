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

import difflib
import re
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, field_validator, model_validator
from pydantic import ValidationError as PydanticValidationError

from prismabib.asjc import KNOWN_ABBREVS
from prismabib.config import ProjectsRootSettings
from prismabib.errors import ConfigError

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+([-][0-9A-Za-z.-]+)?([+][0-9A-Za-z.-]+)?$")


class TemporalCriteria(BaseModel):
    """The inclusive year window of the criteria.yaml ``temporal`` block."""

    model_config = ConfigDict(extra="forbid")

    year_start: int
    year_end: int

    @model_validator(mode="after")
    def _require_ordered_window(self) -> TemporalCriteria:
        """Reject a window whose end precedes its start.

        An inverted window is not merely odd, it is undetectable downstream:
        ``_passes_temporal`` is an inclusive between-test, so ``year_start
        2026`` with ``year_end 2015`` excludes every record ever published
        and yields an empty set ``A``. The PRISMA diagram then reports that
        the automated filter removed the entire corpus, which is a coherent
        and completely wrong story about the search. Transposing two numbers
        in a YAML file is an easy mistake; silently publishing it should not
        be possible.

        Returns:
            ``self`` unchanged, once validated.

        Raises:
            ValueError: If ``year_end`` precedes ``year_start``. Pydantic
                wraps this into a ``ValidationError``, which
                :func:`_criteria_config_error` renders as a ``ConfigError``.
        """
        if self.year_end < self.year_start:
            raise ValueError(
                f"temporal.year_end ({self.year_end}) precedes temporal.year_start "
                f"({self.year_start}). The window is inclusive and would match no "
                "record at all, emptying the corpus rather than filtering it -- "
                "check whether the two values are transposed."
            )
        return self


class DocTypeCriteria(BaseModel):
    """The ``doc_types`` block of ``criteria.yaml``."""

    model_config = ConfigDict(extra="forbid")

    include: list[str]
    conference_whitelist: list[str] = []


class ManualScreeningCriteria(BaseModel):
    """A manual-screening block (``manual_abstract`` or ``manual_fulltext``)."""

    model_config = ConfigDict(extra="forbid")

    exclude_reason_codes: list[str]


class Criteria(BaseModel):
    """The machine-readable Inclusion/Exclusion Matrix (BUILD_PLAN §3.1, lines 342-366).

    ``version`` is required and semantically versioned. Any change to
    inclusion logic bumps it; every decision event later records the
    ``criteria_version`` under which it was made, which is what makes
    protocol amendments auditable rather than silently retroactive.
    """

    model_config = ConfigDict(extra="forbid")

    version: str
    temporal: TemporalCriteria
    subject_areas: list[str]
    doc_types: DocTypeCriteria
    languages: list[str]
    manual_abstract: ManualScreeningCriteria
    manual_fulltext: ManualScreeningCriteria

    @field_validator("subject_areas")
    @classmethod
    def _require_known_subject_areas(cls, value: list[str]) -> list[str]:
        """Reject a subject-area code ASJC does not define.

        Args:
            value: The raw ``subject_areas`` list from ``criteria.yaml``.

        Returns:
            ``value`` unchanged, once every entry is a known grouping.

        Raises:
            ValueError: If any entry is not one of ASJC's 27 four-letter
                groupings. An unknown code cannot match any record, so it
                would silently narrow a review to nothing while looking like
                a deliberate restriction -- the reviewer would see
                "by subject area: <everything>" and no error. Pydantic wraps
                this into a ``ValidationError``.
        """
        unknown = sorted({code for code in value if code.strip().upper() not in KNOWN_ABBREVS})
        if unknown:
            raise ValueError(
                f"unknown subject-area code(s): {', '.join(unknown)}. Use ASJC's "
                f"four-letter groupings: {', '.join(sorted(KNOWN_ABBREVS))}. A code "
                "this list does not contain matches no record, so it would narrow "
                "the review to nothing while looking like a deliberate restriction. "
                "A four-digit ASJC number such as 1702 is refused for the opposite "
                "reason: it names one category ('Artificial Intelligence') but can "
                "only be matched at its grouping, so it would silently widen to all "
                "of COMP -- write the grouping you mean."
            )
        return value

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
# Eligibility criteria for this review -- the machine-readable half of your
# protocol. Every automated filter prismabib applies is defined here, and every
# screening decision records the `version` below, so amending this file mid-review
# stays auditable instead of silently retroactive.
#
# Bump `version` whenever you change anything here, and commit the change: prior
# versions are resolved from git history alone, so an uncommitted amendment is
# invisible to `engine.replay()`.
#
# An empty list means NO RESTRICTION on that dimension, not "exclude everything".
# Unknown keys are refused rather than ignored -- a dropped key would be an
# eligibility rule that silently did not apply.
version: 0.1.0

# Inclusive on both ends. Publication year, from the record's cover date.
temporal:
  year_start: 1900
  year_end: {year}

# Scopus ASJC subject-area groupings, as four-letter codes: COMP, ENGI, MATH,
# MEDI, MULT, ... An unknown code is refused rather than silently matching
# nothing.
#
# REQUIRES ENRICHMENT. The Search API's view=COMPLETE does not return
# subject-area codes, so a project that has not run `prismabib enrich` has no
# data here and this filter cannot exclude anything -- your PRISMA diagram will
# report "by subject area: 0" whatever you list. Run `prismabib enrich <slug>`
# (one Abstract Retrieval call per record, against a separate weekly quota)
# BEFORE screening begins, since it changes which records reach the queue.
#
# Filtering here rather than with SUBJAREA(...) in your project.toml query is
# what makes the exclusion *reportable*: records excluded here are identified,
# counted, and shown in the flow diagram with their reason. Records excluded by
# a server-side SUBJAREA never reach you, so they can never be reported.
subject_areas: []

doc_types:
  # Scopus subtype codes. Common ones: ar (article), cp (conference paper),
  # re (review), ch (book chapter), bk (book). Empty = accept every type.
  include: []
  # Applies ONLY to records whose venue is a conference proceeding; journal
  # articles are never excluded by it. Matching is case-insensitive SUBSTRING,
  # so a short token like "AI" matches almost any venue name. Prefer a
  # distinctive fragment, e.g. "Computer Vision and Pattern Recognition".
  conference_whitelist: []

# Matched against Scopus's own language string, case-insensitively and EXACTLY:
# use "English", not "en" or "eng". A record with no language recorded is kept.
languages: []

# Reason codes you may cite when excluding a record. prismabib refuses an
# exclusion whose reason code is not listed here, so that the PRISMA diagram's
# exclusion breakdown is drawn from a closed, pre-registered vocabulary rather
# than free text invented mid-screening.
#
# These starters are PRISMA-conventional. EDIT THEM for your protocol -- delete
# what does not apply and add what does; the codes should be the reasons your
# own review actually distinguishes.
manual_abstract:
  exclude_reason_codes:
    - OFF_TOPIC
    - REVIEW_OR_SURVEY
    - NOT_PRIMARY_RESEARCH
    - WRONG_POPULATION
    - WRONG_OUTCOME
    - NOT_PEER_REVIEWED
manual_fulltext:
  exclude_reason_codes:
    - NO_FULL_TEXT
    - WRONG_POPULATION
    - WRONG_OUTCOME
    - WRONG_STUDY_DESIGN
    - INSUFFICIENT_DATA
    - DUPLICATE_REPORT
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

    The ``[query]`` table is scaffolded empty rather than omitted. BUILD_PLAN §3.1
    (lines 318-340) shows it as part of ``project.toml``, and ``capture_search``
    reads it: without a placeholder here, every freshly-initialised project failed
    at capture time complaining about a section the template had never offered.
    An operator edits these terms; the file is never rewritten by a later ``init``.

    Args:
        slug: The project slug.
        title: The human-readable project title.

    Returns:
        TOML text matching the BUILD_PLAN §3.1 ``project.toml`` shape.
    """
    created = datetime.now(UTC).date().isoformat()
    escaped_title = title.replace('"', '\\"')
    return (
        "[project]\n"
        f'slug = "{slug}"\n'
        f'title = "{escaped_title}"\n'
        f"created = {created}\n"
        "track_decisions = true\n"
        "\n"
        "# The Phase 1 Boolean query (BUILD_PLAN §3.1). Fill these in before running\n"
        "# `prismabib search`; an empty query is rejected rather than silently\n"
        "# returning the whole database.\n"
        "#\n"
        '# terms          -- each rendered as FIELD("term"), OR-ed together\n'
        "# compound_terms -- each { all = [...] } becomes an AND-ed group\n"
        "[query]\n"
        "terms = []\n"
        "compound_terms = []\n"
        'fields = ["TITLE-ABS-KEY"]\n'
    )


def _resolve_projects_root(root: Path | None) -> Path:
    """Resolve the projects root directory that ``<slug>`` is created under.

    Args:
        root: An explicit override, or ``None`` to read
            ``PRISMABIB_PROJECTS_ROOT`` from :class:`~prismabib.config.ProjectsRootSettings`
            (BUILD_PLAN §3.1 line 315, default ``./projects``).

    Returns:
        The resolved projects root directory. Not required to exist yet.

    Raises:
        ConfigError: If ``root`` is ``None`` and environment configuration
            cannot be loaded (see :class:`~prismabib.config.ProjectsRootSettings`).
    """
    if root is not None:
        return root
    return ProjectsRootSettings().prismabib_projects_root


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
            raise _criteria_config_error(path, exc) from exc

    @property
    def title(self) -> str:
        """This project's human-readable title, from ``project.toml``.

        Written by :meth:`init` and never read until Stage 10 needed it: a
        PRISMA diagram captioned with a slug (``baseball-cv-2026``) is not
        something a manuscript can carry.

        Re-read on every access, for the same reason :attr:`criteria` is --
        an edited ``project.toml`` should be reflected immediately rather
        than at ``Project.open`` time.

        Returns:
            The ``[project] title`` value, or the slug when ``project.toml``
            is missing, unreadable, or has no title. Falling back rather
            than raising is deliberate: a missing title is a cosmetic
            problem, and an export that refused to run over one would fail
            a whole review for a caption.
        """
        path = self.root / "project.toml"
        if not path.is_file():
            return self.slug
        try:
            parsed = tomllib.loads(path.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            return self.slug
        section = parsed.get("project")
        if not isinstance(section, dict):
            return self.slug
        title = section.get("title")
        return title if isinstance(title, str) and title.strip() else self.slug

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


def _criteria_config_error(path: Path, exc: PydanticValidationError) -> ConfigError:
    """Turn a Pydantic failure on ``criteria.yaml`` into an actionable ``ConfigError``.

    Unknown keys get their own treatment because they are the failure most
    likely to produce a *wrong corpus rather than an error*. Until these
    models forbade extras, ``criteria.yaml`` silently dropped anything it
    did not recognise: a misspelled ``language:`` or a plausible-but-
    unsupported ``study_designs:`` left screening running as though that
    dimension were unrestricted, and nothing anywhere said so. Since this
    file is the entire machine-readable methodology surface, a typo in it
    is a methodology change nobody consented to.

    Rejecting the key is therefore the fix, but rejecting it usefully means
    naming what *was* expected -- a researcher who wrote ``language`` needs
    to see ``languages``, not a schema dump.

    Args:
        path: The ``criteria.yaml`` that failed to validate.
        exc: Pydantic's error for that document.

    Returns:
        A :class:`~prismabib.errors.ConfigError` naming each unknown key,
        the block it appeared in, and the closest valid alternative where
        one is recognisable.
    """
    unknown: list[str] = []
    for error in exc.errors():
        if error["type"] != "extra_forbidden":
            continue
        location = [str(part) for part in error["loc"]]
        key = location[-1]
        block = ".".join(location[:-1])
        valid = sorted(_valid_keys_at(block))
        suggestion = difflib.get_close_matches(key, valid, n=1, cutoff=0.6)
        where = f"{block}.{key}" if block else key
        hint = f"; did you mean {suggestion[0]!r}?" if suggestion else ""
        unknown.append(f"  - {where} is not a criteria.yaml key{hint}\n    valid here: {valid}")

    if not unknown:
        return ConfigError(f"{path} does not satisfy the criteria.yaml schema: {exc}")

    listed = "\n".join(unknown)
    return ConfigError(
        f"{path} contains {len(unknown)} key(s) prismabib does not understand:\n"
        f"{listed}\n"
        "\nUnknown keys are refused rather than ignored on purpose. criteria.yaml is "
        "the whole machine-readable definition of who is eligible for this review, so a "
        "key that is silently dropped is an eligibility rule that silently did not "
        "apply -- and the resulting corpus looks entirely plausible.\n"
        "If you need a criterion prismabib cannot express, record it in your protocol "
        "and apply it during title/abstract screening, where it becomes a logged human "
        "decision with a reason code instead of an invisible one."
    )


def _valid_keys_at(block: str) -> set[str]:
    """The field names valid inside one ``criteria.yaml`` block.

    Args:
        block: Dotted path of the containing block (``""`` for the top
            level, e.g. ``"temporal"`` or ``"doc_types"``).

    Returns:
        The set of accepted field names, or an empty set for a block this
        function does not know about (in which case no suggestion is made
        rather than a misleading one).
    """
    models: dict[str, type[BaseModel]] = {
        "": Criteria,
        "temporal": TemporalCriteria,
        "doc_types": DocTypeCriteria,
        "manual_abstract": ManualScreeningCriteria,
        "manual_fulltext": ManualScreeningCriteria,
    }
    model = models.get(block)
    return set(model.model_fields) if model is not None else set()
