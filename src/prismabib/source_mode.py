"""Source modes of a project (ADR 0042 Decisions 1 to 3).

A project's mode is the ``mode`` key of the ``[sources]`` table in ``project.toml``. Every change is
one event in ``decisions/source_modes.jsonl``, written through
:class:`~prismabib.log.AppendOnlyLog`. A project with no table has the legacy mode, which is what
keeps projects created before ADR 0042 byte-identical. This module also owns the capability table:
which modes may run which Scopus- or OpenAlex-touching function or command, and the refusal text.

It never imports :mod:`prismabib.project` at runtime (``project.py`` imports this module).
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, Literal, cast, get_args

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

from prismabib.capture.layout import (
    NON_RUN_DIRNAMES,
    OPENALEX_DIRNAME,
    OPENALEX_SEARCH_DIRNAME,
    atomic_write_bytes,
)
from prismabib.errors import ConfigError
from prismabib.ids import MonotonicUlidFactory
from prismabib.log import AppendOnlyLog

if TYPE_CHECKING:
    from prismabib.project import Project

SourceMode = Literal["scopus", "scopus+openalex", "openalex"]
SOURCE_MODES: Final[tuple[str, ...]] = get_args(SourceMode)
#: The effective mode of a project with no ``[sources]`` table: today's behaviour (ADR 0042 OQ-1).
LEGACY_MODE: Final[SourceMode] = "scopus+openalex"
SOURCE_MODES_FILENAME: Final = "source_modes.jsonl"
CURRENT_SOURCE_MODES_SCHEMA_VERSION: Final = 1

_ALL: Final[frozenset[str]] = frozenset(SOURCE_MODES)
_SCOPUS_SIDE: Final[frozenset[str]] = frozenset({"scopus", "scopus+openalex"})

#: Capability per call site (ADR 0042 Decision 3): the modes in which it may run.
CAPABILITY_MODES: Final[dict[str, frozenset[str]]] = {
    "scopus_search": _SCOPUS_SIDE,
    "scopus_abstract": _SCOPUS_SIDE,
    "scopus_cascade": frozenset({"scopus+openalex"}),
    "openalex_command": frozenset({"scopus+openalex"}),
    "openalex_lookup": frozenset({"scopus+openalex", "openalex"}),
    "openalex_search": frozenset({"openalex"}),
    "scopus_key_test": _SCOPUS_SIDE,
}
_CAPABILITY_WHY: Final[dict[str, str]] = {
    "scopus_search": "it searches Scopus",
    "scopus_abstract": "it retrieves abstracts from Scopus",
    "scopus_cascade": "it asks Scopus for missing fields",
    "openalex_command": "it enriches Scopus records, which this mode does not hold",
    "openalex_lookup": "it looks up Scopus-side records in OpenAlex",
    "openalex_search": "it searches OpenAlex as the primary source",
    "scopus_key_test": "it makes a Scopus request, and this mode does not use Scopus",
}
#: ``caller`` of ``capture_openalex`` -> its capability (ADR 0042 Decision 3).
CALLER_CAPABILITY: Final[dict[str, str]] = {
    "cascade": "openalex_lookup",
    "openalex_command": "openalex_command",
}

#: Every registered CLI command (or command group) and the modes it may run in (Decision 3 matrix).
COMMAND_MODES: Final[dict[str, frozenset[str]]] = {
    "init": _ALL,
    "search": _SCOPUS_SIDE,
    "enrich": _SCOPUS_SIDE,
    "cascade": frozenset({"scopus+openalex", "openalex"}),
    "openalex": frozenset({"scopus+openalex"}),
    "openalex-search": frozenset({"openalex"}),
    "register": frozenset({"scopus+openalex", "openalex"}),
    "registrations": _ALL,
    "reverse": _ALL,
    "candidates": _ALL,
    "manual-entry": _ALL,
    "manual-reverse": _ALL,
    "conflict-resolve": frozenset({"scopus+openalex"}),
    "conflict-reverse": _ALL,
    "build": _ALL,
    "flow": _ALL,
    "export": _ALL,
    "fill": _ALL,
    "fulltext": _ALL,
    "sources": _ALL,
    "doctor": _ALL,
    "credentials": _ALL,
    # The app opens in every mode; each step it offers enforces its own mode rules.
    "gui": _ALL,
}


# -- events ------------------------------------------------------------------------------------


class SourceModeEvent(BaseModel):
    """One change of a project's source mode (ADR 0042 Decision 1)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    schema_version: int = CURRENT_SOURCE_MODES_SCHEMA_VERSION
    action: Literal["set_mode"] = "set_mode"
    ts: datetime
    project: str
    actor: str
    from_mode: SourceMode | None
    to_mode: SourceMode
    note: str | None = None

    @field_validator("schema_version")
    @classmethod
    def _current_version(cls, value: int) -> int:
        if value != CURRENT_SOURCE_MODES_SCHEMA_VERSION:
            raise ValueError(f"unsupported schema_version {value}")
        return value

    @field_validator("event_id", "project", "actor")
    @classmethod
    def _nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("ts")
    @classmethod
    def _aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("ts must be timezone-aware")
        return value.astimezone(UTC)

    @field_serializer("ts", when_used="json")
    def _serialize_ts(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def source_modes_path(project: Project) -> Path:
    """``<root>/decisions/source_modes.jsonl``."""
    return project.root / "decisions" / SOURCE_MODES_FILENAME


def _log(path: Path) -> AppendOnlyLog[SourceModeEvent]:
    return AppendOnlyLog(
        path,
        model=SourceModeEvent,
        current_schema_version=CURRENT_SOURCE_MODES_SCHEMA_VERSION,
        event_id=lambda event: event.event_id,
        noun="source-mode",
        event_description="source-mode change",
        recovery_hint="prismabib sources --set",
        writer_name="the source-mode writer",
        labour="source-mode choice",
    )


def load_mode_events(project: Project) -> list[SourceModeEvent]:
    """Every event, sorted by ``event_id``; empty (and nothing created) when there is no file."""
    path = source_modes_path(project)
    if not path.is_file():
        return []
    return sorted(_log(path).load(), key=lambda event: event.event_id)


_IDS = MonotonicUlidFactory()


def write_mode_event(
    project: Project,
    *,
    actor: str,
    from_mode: SourceMode | None,
    to_mode: SourceMode,
    note: str | None = None,
) -> SourceModeEvent:
    """Append one ``set_mode`` event.

    Raises:
        ConfigError: If ``actor`` is blank.
    """
    if not actor.strip():
        raise ConfigError("a source-mode change needs a named actor (--actor NAME).")
    event = SourceModeEvent(
        event_id=_IDS(),
        ts=datetime.now(UTC),
        project=project.slug,
        actor=actor.strip(),
        from_mode=from_mode,
        to_mode=to_mode,
        note=note,
    )
    _log(source_modes_path(project)).write_event(event)
    return event


# -- reading the mode ----------------------------------------------------------------------------


def _toml_path(project: Project) -> Path:
    return project.root / "project.toml"


def table_mode(project: Project) -> SourceMode | None:
    """The ``[sources] mode`` in ``project.toml``, or ``None`` when there is no table.

    Raises:
        ConfigError: If the file is not TOML, the table has no valid ``mode``, or the value is not
            one of the three allowed strings.
    """
    path = _toml_path(project)
    try:
        parsed = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConfigError(f"{path} could not be read: {exc}") from exc
    if "sources" not in parsed:
        return None
    section = parsed["sources"]
    value = section.get("mode") if isinstance(section, dict) else None
    if value not in SOURCE_MODES:
        raise ConfigError(
            f"{path} has [sources] mode = {value!r}, which is not one of "
            f"{', '.join(repr(m) for m in SOURCE_MODES)}."
        )
    return cast("SourceMode", value)


def effective_mode(project: Project) -> SourceMode:
    """The table's mode, or :data:`LEGACY_MODE`; does not read the event log."""
    return table_mode(project) or LEGACY_MODE


def checked_mode(project: Project) -> SourceMode:
    """The project's mode, after checking that the event log agrees with ``project.toml``.

    Raises:
        ConfigError: On an invalid value, a table with no event, an event with no table, or a last
            event whose ``to_mode`` differs from the file. The message names both values.
    """
    in_file = table_mode(project)
    events = load_mode_events(project)
    if in_file is None and not events:
        return LEGACY_MODE
    last = events[-1].to_mode if events else None
    if in_file is None or last != in_file:
        raise ConfigError(
            f"{_toml_path(project)} says {in_file or 'no [sources] table'} but "
            f"{source_modes_path(project)} says {last or 'no event'}; the two must agree. "
            f"Record the mode you mean: prismabib sources {project.slug} --set <mode> --actor NAME."
        )
    return in_file


def keyword_kind(project: Project) -> str:
    """The keyword kind the analyses read by default (ADR 0042 Decision 6).

    ``openalex-topic`` in mode ``openalex``, where no Scopus keyword exists; ``author`` otherwise,
    which is what every reader used before the mode existed.
    """
    return "openalex-topic" if checked_mode(project) == "openalex" else "author"


# -- capability checks ---------------------------------------------------------------------------


def _refusal(project: Project, what: str, mode: str, why: str, allowed: frozenset[str]) -> str:
    fix = (
        f"run `prismabib sources {project.slug} --set scopus+openalex`"
        if mode == "scopus" and "scopus+openalex" in allowed
        else "create a new project with `prismabib init --sources "
        f"{min(allowed)}` (allowed modes: {', '.join(sorted(allowed))})"
    )
    return (
        f"{what} is not available in sources mode {mode!r} "
        f"({_toml_path(project)}, [sources] mode). {why[0].upper()}{why[1:]}. "
        f"To use it, {fix}."
    )


def require_capability(project: Project, capability: str) -> str:
    """Refuse unless ``capability`` is allowed in the project's mode.

    Args:
        project: The project.
        capability: A key of :data:`CAPABILITY_MODES`; anything else is refused, so a call site
            cannot skip the check by inventing a name.

    Returns:
        The project's mode.

    Raises:
        ConfigError: If the capability is unknown or the mode does not allow it.
    """
    if capability not in CAPABILITY_MODES:
        raise ConfigError(f"unknown source-mode capability {capability!r}")
    mode = checked_mode(project)
    allowed = CAPABILITY_MODES[capability]
    if mode not in allowed:
        raise ConfigError(_refusal(project, capability, mode, _CAPABILITY_WHY[capability], allowed))
    return mode


def require_command(project: Project, command: str) -> str:
    """Refuse unless the CLI ``command`` is allowed in the project's mode (Decision 3 matrix)."""
    if command not in COMMAND_MODES:
        raise ConfigError(f"unknown command {command!r} in the source-mode table")
    mode = checked_mode(project)
    allowed = COMMAND_MODES[command]
    if mode not in allowed:
        why = "it has nothing to act on in this mode"
        raise ConfigError(_refusal(project, f"`{command}`", mode, why, allowed))
    return mode


def command_refusal(project: Project, command: str) -> str | None:
    """The refusal :func:`require_command` would raise, or ``None``; the GUI's disabled reason."""
    try:
        require_command(project, command)
    except ConfigError as exc:
        return str(exc)
    return None


def require_caller(project: Project, caller: str | None) -> None:
    """The check ``capture_openalex`` runs: ``caller`` must name a known call site."""
    if not caller or caller not in CALLER_CAPABILITY:
        raise ConfigError(
            f"capture_openalex needs caller= one of {sorted(CALLER_CAPABILITY)}, got {caller!r}."
        )
    require_capability(project, CALLER_CAPABILITY[caller])


# -- transitions ---------------------------------------------------------------------------------

_NON_SCOPUS_SEARCH_DIRS: Final = NON_RUN_DIRNAMES  # one source of truth (layout.py)


def _subdirs(path: Path) -> list[Path]:
    return sorted(p for p in path.iterdir() if p.is_dir()) if path.is_dir() else []


def _scopus_search_runs(project: Project) -> list[Path]:
    return [p for p in _subdirs(project.raw_dir) if p.name not in _NON_SCOPUS_SEARCH_DIRS]


def check_transition(project: Project, from_mode: str, to_mode: str) -> None:
    """Refuse a mode change the table of ADR 0042 Decision 1 does not allow.

    Raises:
        ConfigError: Naming the blocking decision or run, and pointing to a new project.
    """
    if from_mode == to_mode:
        return
    decisions = project.decisions_path
    if decisions.is_file() and decisions.read_bytes().strip():
        raise ConfigError(
            f"cannot change the sources mode from {from_mode!r} to {to_mode!r}: screening "
            f"decisions exist in {decisions}. To change the mode, create a new project instead."
        )
    blocking: list[Path] = []
    what = "a run directory"
    if to_mode == "openalex":
        blocking = _scopus_search_runs(project)
    elif from_mode == "openalex":
        blocking = _subdirs(project.raw_dir / OPENALEX_SEARCH_DIRNAME)
    elif to_mode == "scopus":  # from scopus+openalex (Q-B)
        blocking = _subdirs(project.raw_dir / OPENALEX_DIRNAME)
        if not blocking:
            blocking = _active_registration_marker(project)
            what = "an active registration in"
    if blocking:
        raise ConfigError(
            f"cannot change the sources mode from {from_mode!r} to {to_mode!r}: "
            f"{what} {blocking[0]} blocks it (a run of any state blocks a change, and a "
            "discarded run is kept as an audit trail). To change the mode, create a new project instead."
        )


def _active_registration_marker(project: Project) -> list[Path]:
    # Imported here: capture.registrations imports project, which imports this module.
    from prismabib.capture.registrations import (
        REGISTRATIONS_FILENAME,
        active_registrations,
        load_registration_events,
    )

    path = project.root / "decisions" / REGISTRATIONS_FILENAME
    return [path] if active_registrations(load_registration_events(path)) else []


def set_mode(
    project: Project, to_mode: str, *, actor: str, note: str | None = None
) -> SourceModeEvent | None:
    """Change (or record) the project's mode: one event, and only the ``[sources]`` table edited.

    Returns:
        The event, or ``None`` when the table and the log already say ``to_mode``.

    Raises:
        ConfigError: If ``to_mode`` is invalid, the transition is refused, or ``actor`` is blank.
    """
    if to_mode not in SOURCE_MODES:
        raise ConfigError(f"{to_mode!r} is not one of {', '.join(SOURCE_MODES)}.")
    target = cast("SourceMode", to_mode)
    in_file = table_mode(project)
    events = load_mode_events(project)
    current = events[-1].to_mode if events else (in_file or LEGACY_MODE)
    if in_file == to_mode and events and events[-1].to_mode == to_mode:
        return None
    check_transition(project, current, to_mode)
    new_text = _new_toml_text(project, to_mode)  # validated before anything is written
    event = write_mode_event(
        project,
        actor=actor,
        from_mode=current,
        to_mode=target,
        note=note,
    )
    atomic_write_bytes(_toml_path(project), new_text.encode("utf-8"))
    return event


def sources_table_text(mode: str) -> str:
    """The ``[sources]`` table as written to ``project.toml``."""
    return f'[sources]\nmode = "{mode}"\n'


_HEADER_RE: Final = re.compile(r"\s*\[\s*sources\s*\]\s*(#.*)?$")


def _new_toml_text(project: Project, mode: str) -> str:
    """``project.toml`` with its ``[sources]`` table set to ``mode``; every other byte stays.

    Raises:
        ConfigError: If the result is not valid TOML or does not say ``mode``; nothing is written.
    """
    path = _toml_path(project)
    lines = path.read_bytes().decode("utf-8").splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if _HEADER_RE.match(line.rstrip("\r\n"))), None)
    body = f'mode = "{mode}"\n'
    if start is None:
        tail = lines[-1] if lines else "\n"
        new = (
            "".join(lines) + ("" if tail.endswith("\n") else "\n") + "\n" + sources_table_text(mode)
        )
    else:
        end = start + 1
        while end < len(lines) and not lines[end].lstrip().startswith("["):
            end += 1
        # Blank and comment lines right before the next table belong to that table.
        while end - 1 > start and (not lines[end - 1].strip() or lines[end - 1].lstrip()[0] == "#"):
            end -= 1
        header = lines[start] if lines[start].endswith("\n") else lines[start] + "\n"
        new = "".join(lines[:start]) + header + body + "".join(lines[end:])
    try:
        parsed: dict[str, Any] = tomllib.loads(new)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(
            f"could not edit the [sources] table of {path} ({exc}); edit it by hand."
        ) from exc
    if parsed.get("sources", {}).get("mode") != mode:
        raise ConfigError(f"could not edit the [sources] table of {path}; edit it by hand.")
    return new


def git_user_name() -> str | None:
    """``git config user.name`` in the working directory, or ``None``."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    name = result.stdout.strip()
    return name or None
