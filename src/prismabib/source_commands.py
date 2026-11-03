"""``prismabib sources`` and ``prismabib doctor`` (ADR 0042 Decision 2).

``sources`` prints the project's mode and last change, or records a change. ``doctor`` prints the
mode and which keys are set, and makes no network request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from prismabib.credentials import ENV_FILENAME, status_lines
from prismabib.errors import ConfigError
from prismabib.project import Project
from prismabib.source_mode import (
    LEGACY_MODE,
    SOURCE_MODES,
    checked_mode,
    git_user_name,
    load_mode_events,
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

    @app.command("doctor")
    def doctor(
        slug: Annotated[str, typer.Argument(help="Project slug.")],
        root: Annotated[Path | None, typer.Option("--root", "-r", show_default=False)] = None,
    ) -> None:
        """Print the source mode, key status and the next command. Makes no network request."""
        from prismabib.cli import _echo, _reporting_errors

        with _reporting_errors():
            project = Project.open(slug, root=root)
            mode = checked_mode(project)
            legacy = table_mode(project) is None
            _echo(
                f"sources mode: {mode}"
                + (" (legacy default; no choice recorded)" if legacy else "")
            )
            if legacy:
                _echo(
                    f"  record your choice: prismabib sources {slug} --set {LEGACY_MODE} "
                    "--actor NAME  (or another mode)"
                )
            lines = status_lines(Path(ENV_FILENAME))
            for line in lines:
                _echo(line)
            if mode != "openalex" and lines[0].endswith("not set"):
                _echo("  store the Scopus key: prismabib credentials set scopus")
            _echo("  details and file safety: prismabib credentials status")
            if mode == "openalex":
                _echo("next: this mode has no capture command in this release; see ADR 0042")
            else:
                _echo(f"next: prismabib search {slug}")
