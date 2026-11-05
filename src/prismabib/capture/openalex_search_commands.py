"""``prismabib openalex-search`` (ADR 0042 Decision 4): capture OpenAlex works by query.

A thin command over :func:`~prismabib.capture.openalex_search.capture_openalex_search`. It reads
the query from ``[openalex_query]``, refuses outside mode ``openalex`` before reading any key,
and prints what the call did. It never builds the store: ``prismabib build <slug> --rebuild``
loads a sealed run.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated

import structlog
import typer

from prismabib.capture.openalex_search import (
    OpenAlexSearchResult,
    capture_openalex_search,
    cost_bound_usd,
    query_key,
)
from prismabib.config import Settings
from prismabib.project import Project
from prismabib.query import build_openalex_query_for_project
from prismabib.source_mode import require_command
from prismabib.sources.openalex import (
    PAGES_PER_DAY,
    SEARCH_PAGE_PRICE_USD,
    SEARCH_PAGE_SIZE,
    SEARCH_SELECT,
)

#: Exit status when the run was discarded: nothing usable was sealed, so a script must notice.
EXIT_DISCARDED = 1


@contextmanager
def _logs_to_stderr() -> Iterator[None]:
    """Send structlog output to stderr for the block, so stdout holds only the summary."""
    previous = structlog.get_config()["logger_factory"]
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(sys.stderr))
    try:
        yield
    finally:
        structlog.configure(logger_factory=previous)


def _duration(seconds: float | None) -> str:
    """``5 h 12 min`` for a reset time, or a plain 00:00 UTC when the server gave none."""
    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "at 00:00 UTC"
    minutes = math.ceil(seconds / 60)
    hours, minutes = divmod(minutes, 60)
    return f"in {hours} h {minutes:02d} min (00:00 UTC)" if hours else f"in {minutes} min"


def _summary(slug: str, result: OpenAlexSearchResult, budget: int, secret: str) -> list[str]:
    """The lines that tell the user what ``result`` means and what to do next."""
    spent = f"{result.pages_fetched} page(s) this call, spent ${result.spent_usd:.3f}"
    match result.status:
        case "sealed":
            manifest = result.manifest
            assert manifest is not None  # a sealed result always carries its manifest
            return [
                (
                    f"run {result.run_id} sealed: {manifest.works_captured} works captured of "
                    f"{manifest.total_count} reported by OpenAlex ({spent})."
                ),
                f"Next: prismabib build {slug} --rebuild",
            ]
        case "already_sealed":
            return [
                (
                    f"a sealed run of this query already exists (run {result.run_id}); "
                    "no request was made."
                ),
                "Use --refresh to capture it again as a new run.",
            ]
        case "budget_stop":
            return [
                (
                    f"stopped: the page budget of {budget} is used "
                    f"(run {result.run_id}, {spent}). The run is not sealed."
                ),
                "Run the same command again to resume from the saved cursor.",
            ]
        case "daily_budget":
            reason = result.reason.replace(secret, "***") if secret else result.reason
            lines = [
                (
                    f"stopped: OpenAlex's daily budget is spent (run {result.run_id}, {spent}). "
                    f"It resets {_duration(result.resets_in_seconds)}. The run is not sealed."
                ),
                "Run the same command again after the reset to resume from the saved cursor.",
            ]
            return [*lines, f"OpenAlex said: {reason}"] if reason else lines
        case _:
            return [
                f"run {result.run_id} discarded: {result.message}.",
                (
                    "It stays on disk as an audit trail and is never resumed. "
                    "Run the same command again to start a new run (this spends quota again)."
                ),
            ]


def openalex_search(
    slug: Annotated[str, typer.Argument(help="Project slug.")],
    budget: Annotated[
        int,
        typer.Option(
            "--budget",
            min=1,
            help=(
                "Most pages to request in this call (one request each, 100 works a page, "
                f"about ${SEARCH_PAGE_PRICE_USD} a page). A free key allows about "
                f"{PAGES_PER_DAY:,} pages ({PAGES_PER_DAY * SEARCH_PAGE_SIZE:,} works) a day."
            ),
        ),
    ],
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Start a new run even if this query is sealed.")
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Print the query and the cost bound. No request, no write."),
    ] = False,
    root: Annotated[Path | None, typer.Option("--root", "-r", show_default=False)] = None,
) -> None:
    """Capture OpenAlex works for the \\[openalex_query] search of a project, into a sealed Layer 0 run.

    Needs OPENALEX_API_KEY (a free key; `prismabib credentials set openalex`) and sources mode
    `openalex`. Run `prismabib build <slug> --rebuild` afterwards to load a sealed run.
    """
    from prismabib.cli import _echo, _reporting_errors

    with _reporting_errors():
        project = Project.open(slug, root=root)
        require_command(project, "openalex-search")  # before the key is read
        query = build_openalex_query_for_project(project)
        settings = Settings()
        if dry_run:
            key = settings.openalex_api_key
            has_key = key is not None and bool(key.get_secret_value().strip())
            _echo("dry run: no request made, nothing written")
            _echo(f"query: {query}")
            _echo(f"query key: {query_key(query)}")
            _echo(f"select: {SEARCH_SELECT}")
            _echo(f"page budget: {budget} pages of {SEARCH_PAGE_SIZE} works")
            _echo(f"cost bound: at most ${cost_bound_usd(budget):.3f}")
            _echo(f"OpenAlex key: {'set' if has_key else 'NOT SET (a real run is refused)'}")
            return
        with _logs_to_stderr():
            result = capture_openalex_search(
                project, query=query, budget=budget, refresh=refresh, settings=settings
            )
        key = settings.openalex_api_key
        secret = key.get_secret_value() if key is not None else ""
        for line in _summary(slug, result, budget, secret):
            _echo(line)
        if result.status == "discarded":
            raise typer.Exit(code=EXIT_DISCARDED)


__all__ = ["openalex_search"]
