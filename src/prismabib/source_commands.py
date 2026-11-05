"""``prismabib sources`` and ``prismabib doctor`` (ADR 0042 Decision 2).

``sources`` prints the project's mode and last change, or records a change. ``doctor`` prints the
mode and which keys are set. ``doctor`` checks the environment first; it makes no network request
unless ``--online`` or ``--check-scopus`` is given.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from prismabib.config import Settings
from prismabib.credentials import (
    ENV_FILENAME,
    openalex_key_missing,
    probe_scopus_key,
    status_lines,
)
from prismabib.doctor_checks import environment_report, openalex_reachable
from prismabib.errors import ConfigError
from prismabib.project import Project
from prismabib.source_mode import (
    COMMAND_MODES,
    LEGACY_MODE,
    SOURCE_MODES,
    checked_mode,
    git_user_name,
    load_mode_events,
    require_capability,
    set_mode,
    table_mode,
)


def register_source_commands(app: typer.Typer) -> None:
    """Add ``sources`` and ``doctor`` to ``app`` (called once from ``cli.py``)."""

    @app.command("sources")
    def sources(
        slug: Annotated[str, typer.Argument(help="Project slug.")],
        set_to: Annotated[
            str | None,
            typer.Option("--set", help=f"Change the mode to one of: {', '.join(SOURCE_MODES)}."),
        ] = None,
        actor: Annotated[
            str | None,
            typer.Option("--actor", help="Who makes the change (default: git config user.name)."),
        ] = None,
        note: Annotated[str | None, typer.Option("--note", help="Why.")] = None,
        root: Annotated[Path | None, typer.Option("--root", "-r", show_default=False)] = None,
    ) -> None:
        """Show the project's source mode, or change it with --set (an append-only event)."""
        # Imported here: cli.py imports this module while it is still being defined.
        from prismabib.cli import _echo, _reporting_errors

        with _reporting_errors():
            project = Project.open(slug, root=root)
            if set_to is None:
                _echo(f"mode: {checked_mode(project)}")
                events = load_mode_events(project)
                if events:
                    last = events[-1]
                    _echo(
                        f"last change: {last.ts.isoformat()} by {last.actor} "
                        f"({last.from_mode} -> {last.to_mode})"
                    )
                else:
                    _echo("last change: none (legacy default; no choice recorded)")
                return
            name = (actor or "").strip() or git_user_name()
            if not name:
                raise ConfigError("--set needs a named --actor NAME (or `git config user.name`).")
            event = set_mode(project, set_to, actor=name, note=note)
            if event is None:
                _echo(f"mode is already {set_to}; nothing recorded")
            else:
                _echo(f"mode: {event.from_mode} -> {event.to_mode} (event {event.event_id})")
                if event.to_mode == "openalex":
                    _echo(
                        "next: add an [openalex_query] section with search = '...' to "
                        f"project.toml, then prismabib openalex-search {slug} --budget 30 --dry-run"
                    )

    @app.command("doctor")
    def doctor(
        slug: Annotated[
            str | None,
            typer.Argument(help="Project slug. Without it, only the environment is checked."),
        ] = None,
        online: Annotated[
            bool,
            typer.Option(
                "--online", help="Also check that api.openalex.org answers (one free request)."
            ),
        ] = False,
        check_scopus: Annotated[
            bool,
            typer.Option(
                "--check-scopus",
                help="Make one Scopus request with the stored key (needs a Scopus-mode project).",
            ),
        ] = False,
        root: Annotated[Path | None, typer.Option("--root", "-r", show_default=False)] = None,
    ) -> None:
        """Check the environment (Python, git, projects root, .env), then the project's mode and keys.

        Makes no network request unless --online (one free OpenAlex request) or --check-scopus
        (one Scopus request) is given. Exits 1 only when a blocking check fails.
        """
        from prismabib.cli import _echo, _reporting_errors

        with _reporting_errors():
            if check_scopus and slug is None:
                raise ConfigError(
                    "--check-scopus needs a project, because the mode decides whether Scopus is used."
                )
            project = Project.open(slug, root=root) if slug is not None else None
            if project is not None and check_scopus:
                require_capability(project, "scopus_key_test")  # before any output or request
            lines, failed = environment_report(root)
            _echo("environment:")
            for line in lines:
                _echo(line)
            if online:
                key = Settings().openalex_api_key
                _echo(openalex_reachable(key.get_secret_value().strip() if key else None).line)
            if project is not None and slug is not None:
                _project_report(project, slug, _echo)
                if check_scopus:
                    result = probe_scopus_key(Settings())
                    _echo(f"scopus: {result.label}")
                    failed = failed or not result.ok
            if failed:
                raise typer.Exit(code=1)


def _project_report(project: Project, slug: str, _echo: Callable[[str], None]) -> None:
    """The per-project part of ``doctor``: mode, what it enables, key status, next command."""
    mode = checked_mode(project)
    legacy = table_mode(project) is None
    _echo(f"sources mode: {mode}" + (" (legacy default; no choice recorded)" if legacy else ""))
    if legacy:
        _echo(
            f"  record your choice: prismabib sources {slug} --set {LEGACY_MODE} "
            "--actor NAME  (or another mode)"
        )
    enabled = [c for c, modes in COMMAND_MODES.items() if mode in modes]
    refused = [c for c in COMMAND_MODES if c not in enabled]
    _echo(f"  enabled: {', '.join(enabled) or 'none'}")
    _echo(f"  not available in this mode: {', '.join(refused) or 'none'}")
    lines = status_lines(Path(ENV_FILENAME))
    for line in lines:
        _echo(line)
    if mode != "openalex" and lines[0].endswith("not set"):
        _echo("  store the Scopus key: prismabib credentials set scopus")
    _echo("  details and file safety: prismabib credentials status")
    if mode == "openalex":
        if openalex_key_missing():
            _echo("  store the OpenAlex key: prismabib credentials set openalex")
        _echo(
            "next: edit [openalex_query] search in project.toml, then "
            f"prismabib openalex-search {slug} --budget 30 --dry-run"
        )
    else:
        _echo(f"next: prismabib search {slug}")
