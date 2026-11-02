"""``prismabib candidates add``: store Google Scholar rows the user pasted (ADR 0041, PR5).

Pure intake into the candidate log: no HTTP, no Scholar contact, and no ``records`` row.
DOI enrichment and registration are separate, later steps.
"""

from __future__ import annotations

import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from prismabib.capture.candidates import CandidateEvent, CandidateLog, parse_pasted_rows
from prismabib.errors import ValidationError
from prismabib.project import Project

candidates_app = typer.Typer(
    name="candidates",
    help="Candidates pasted from Google Scholar (no network).",
    no_args_is_help=True,
)


@candidates_app.command("add")
def add(
    slug: Annotated[str, typer.Argument(help="Project slug.")],
    reviewer: Annotated[str, typer.Option("--reviewer", help="The named reviewer.")],
    file: Annotated[
        Path | None,
        typer.Option("--file", help="Read the pasted rows from this file instead of stdin."),
    ] = None,
    root: Annotated[Path | None, typer.Option("--root", "-r", show_default=False)] = None,
) -> None:
    """Add pasted ``title<TAB|,>year[<TAB|,>doi]`` rows as candidates (no network).

    Nothing is written unless every row parses.
    """
    # Imported here: cli.py imports this module to register the group.
    from prismabib.cli import _echo, _reporting_errors

    with _reporting_errors():
        try:
            text = file.read_text(encoding="utf-8") if file else sys.stdin.read()
        except (OSError, UnicodeDecodeError) as exc:
            raise ValidationError(f"cannot read the pasted rows from {file}: {exc}") from exc
        rows = parse_pasted_rows(text)
        if not rows:
            raise ValidationError("no rows to add: paste at least one 'title, year' line")
        project = Project.open(slug, root=root)
        log = CandidateLog(project.root / "decisions" / "candidates.jsonl")
        now = datetime.now(UTC)
        results = Counter(
            log.add(CandidateEvent.from_row(row, reviewer=reviewer, pasted_at=now)) for row in rows
        )
        _echo(
            f"added {results['added']}, enriched {results['enriched']}, "
            f"already present {results['already_present']}, conflict {results['conflict']}"
        )
