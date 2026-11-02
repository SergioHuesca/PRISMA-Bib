"""CLI for manual entry and conflict resolution (ADR 0041 PR 6): thin wrappers over the writers.

``manual-entry``, ``manual-reverse``, ``conflict-resolve`` and ``conflict-reverse`` call the same
functions as the panel, so there is one code path (ADR 0035). Each writes one Layer 2 event and
takes effect at the next ``prismabib build --rebuild``.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated

import typer

from prismabib.capture.conflicts import resolve_conflict, reverse_conflict
from prismabib.capture.manual import add_manual_entry, reverse_manual_entry
from prismabib.errors import ValidationError
from prismabib.project import Project

_SLUG = Annotated[str, typer.Argument(help="Project slug.")]
_REVIEWER = Annotated[str, typer.Option("--reviewer", help="The named reviewer.")]
_ROOT = Annotated[Path | None, typer.Option("--root", "-r", show_default=False)]
_NOTE = Annotated[str | None, typer.Option("--note", help="Required when the reason is 'other'.")]
_VALUE = Annotated[str | None, typer.Option("--value", help="The text; '-' reads stdin.")]


def _text(value: str | None) -> str | None:
    """``--value``, or stdin for ``-``; stdin is read as UTF-8 whatever the Windows code page."""
    return sys.stdin.buffer.read().decode("utf-8-sig") if value == "-" else value


def register_manual_commands(app: typer.Typer) -> None:
    """Add the four commands to ``app`` (called once from ``cli.py``)."""

    @app.command("manual-entry")
    def manual_entry(
        slug: _SLUG,
        record_id: Annotated[str, typer.Argument(help="An existing scopus: record id.")],
        field: Annotated[str, typer.Argument(help="abstract, authors or affiliations.")],
        reviewer: _REVIEWER,
        value: _VALUE = None,
        note: Annotated[str | None, typer.Option("--note")] = None,
        root: _ROOT = None,
    ) -> None:
        """Enter a missing field by hand (level 5). Authors: one 'Surname, Given' per line;
        affiliations: one 'Name' or 'Name; City; Country' per line."""
        # Imported here: cli.py imports this module while it is still being defined.
        from prismabib.cli import _echo, _reporting_errors

        with _reporting_errors():
            text = _text(value)
            if text is None:
                raise ValidationError("--value is required")
            event = add_manual_entry(
                Project.open(slug, root=root),
                record_id=record_id,
                field=field,
                value=text,
                reviewer=reviewer,
                note=note,
            )
            _echo(f"entered {field} of {record_id} (event {event.event_id})")

    @app.command("manual-reverse")
    def manual_reverse(
        slug: _SLUG,
        event_id: Annotated[str, typer.Argument(help="The manual_entry event to reverse.")],
        reviewer: _REVIEWER,
        reason: Annotated[
            str, typer.Option("--reason", help="wrong_value, wrong_field, duplicate_entry, other.")
        ],
        note: _NOTE = None,
        root: _ROOT = None,
    ) -> None:
        """Reverse a manual entry (append-only; the entry stays in the log)."""
        # Imported here: cli.py imports this module while it is still being defined.
        from prismabib.cli import _echo, _reporting_errors

        with _reporting_errors():
            event = reverse_manual_entry(
                Project.open(slug, root=root),
                reverses=event_id,
                reviewer=reviewer,
                reason=reason,
                note=note,
            )
            _echo(f"reversed {event.reverses} (event {event.event_id})")

    @app.command("conflict-resolve")
    def conflict_resolve(
        slug: _SLUG,
        record_id: Annotated[str, typer.Argument(help="The record of the open conflict.")],
        field: Annotated[str, typer.Argument(help="abstract, authors or affiliations.")],
        chosen_side: Annotated[str, typer.Argument(help="scopus, openalex or manual.")],
        reviewer: _REVIEWER,
        value: _VALUE = None,
        note: Annotated[str | None, typer.Option("--note")] = None,
        root: _ROOT = None,
    ) -> None:
        """Resolve an open conflict; 'manual' needs --value."""
        # Imported here: cli.py imports this module while it is still being defined.
        from prismabib.cli import _echo, _reporting_errors

        with _reporting_errors():
            event = resolve_conflict(
                Project.open(slug, root=root),
                record_id=record_id,
                field=field,
                chosen_side=chosen_side,
                reviewer=reviewer,
                value=_text(value),
                note=note,
            )
            _echo(f"resolved {field} of {record_id} as {chosen_side} (event {event.event_id})")

    @app.command("conflict-reverse")
    def conflict_reverse(
        slug: _SLUG,
        event_id: Annotated[str, typer.Argument(help="The conflict_resolution event to reverse.")],
        reviewer: _REVIEWER,
        reason: Annotated[str, typer.Option("--reason", help="wrong_side, wrong_value, other.")],
        note: _NOTE = None,
        root: _ROOT = None,
    ) -> None:
        """Reverse a conflict resolution; the conflict goes back to open."""
        # Imported here: cli.py imports this module while it is still being defined.
        from prismabib.cli import _echo, _reporting_errors

        with _reporting_errors():
            event = reverse_conflict(
                Project.open(slug, root=root),
                reverses=event_id,
                reviewer=reviewer,
                reason=reason,
                note=note,
            )
            _echo(f"reversed {event.reverses} (event {event.event_id})")
