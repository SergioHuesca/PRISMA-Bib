"""The ``prismabib`` command-line wrapper (BUILD_PLAN Stage 11, line 1455).

BUILD_PLAN names six non-interactive subcommands -- ``init | search | build |
flow | code | export``. Four of them are implemented here. ``code`` and
``export`` are **deliberately absent** until Stages 8 and 10 build the taxonomy
and reporting layers behind them: a subcommand that exists, accepts its
arguments, and then does nothing real is indistinguishable from a working one
at the point where it matters (a shell script, a methods section, a new user's
first hour), and that is precisely how ``README.md`` came to instruct people to
run a ``prismabib init`` that did not exist. An absent command fails with
"No such command", which is honest; a stub does not.

**Every command here is a thin wrapper.** No decision, no arithmetic, and no
filesystem layout knowledge lives in this module -- each subcommand resolves a
:class:`~prismabib.project.Project`, calls exactly one already-tested library
function, and renders the result. That is a deliberate constraint: anything
this module computed itself would be a second implementation of a number that
§1.4 requires to have exactly one, and it would be a copy that the notebook
path never exercises.

**Errors.** Every exception prismabib raises on purpose descends from
:class:`~prismabib.errors.PrismabibError` (``ConfigError``, ``StoreError``,
``LogError``, ``ValidationError``, the ``SourceError`` family including
``EntitlementError`` and ``QuotaExceededError``, and
:class:`~prismabib.capture.writer.SealedRunError`, which subclasses
``PrismabibError`` directly rather than sitting in the §3.3 tree). Those are
caught here and printed **verbatim** with a non-zero exit -- verbatim because
the library's messages are written to be acted on (which file, which key, which
variable, what to do next), and re-wrapping or truncating them into a tidy
one-liner would throw away the only part a researcher can use. Anything that is
*not* a ``PrismabibError`` is left to traceback: an unexpected exception is a
bug, and a bug that prints one polite line is a bug nobody can report.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Final, NoReturn

import structlog
import typer
from structlog.typing import EventDict, WrappedLogger

from prismabib import __version__
from prismabib.capture.manifest import RunManifest
from prismabib.capture.writer import capture_search
from prismabib.config import ProjectsRootSettings
from prismabib.errors import PrismabibError
from prismabib.prisma.flow import FlowCounts, compute_flow_counts
from prismabib.project import Project
from prismabib.store.load import StoreStats, build_store

#: Exit status for a *known* failure -- any :class:`PrismabibError`. One code
#: for all of them on purpose: the message is what tells a human which failure
#: this was, and a per-exception code table would be a second, unversioned
#: contract that nothing in BUILD_PLAN asks for.
_EXIT_KNOWN_ERROR = 1

#: Exit status for an interrupted ``search`` (128 + SIGINT, the shell
#: convention). Distinct from ``_EXIT_KNOWN_ERROR`` because an interrupted
#: capture is not a failed one: Layer 0 keeps every page already written.
_EXIT_INTERRUPTED = 130

app = typer.Typer(
    name="prismabib",
    help=(
        "Reproducible PRISMA + bibliometric review pipeline.\n\n"
        "Run the four non-interactive steps in order:\n\n"
        "  1. init   -- create projects/<slug>/ and the two files you edit\n"
        "  2. search -- capture Scopus into the immutable Layer 0 archive\n"
        "  3. build  -- (re)build the Layer 1 DuckDB store from Layer 0\n"
        "  4. flow   -- print the PRISMA 2020 flow counts\n\n"
        "Screening itself is notebook-only (BUILD_PLAN Stage 7): decisions are "
        "human events, and a CLI is the wrong place to make them."
    ),
    no_args_is_help=True,
    add_completion=False,
)

_ROOT_OPTION = typer.Option(
    "--root",
    "-r",
    help=(
        "Projects root to resolve <slug> under. Defaults to PRISMABIB_PROJECTS_ROOT "
        "from the environment or .env (itself defaulting to ./projects)."
    ),
    show_default=False,
)


def _echo(line: str = "") -> None:
    """Write one line to stdout -- the command's actual result.

    Args:
        line: The text to write. Never wrapped or re-flowed.
    """
    typer.echo(line)


def _echo_err(line: str = "") -> None:
    """Write one line to stderr -- progress, warnings, and errors.

    Kept off stdout so that ``prismabib flow demo > flow.txt`` captures the
    numbers and nothing else.

    Args:
        line: The text to write. Never wrapped or re-flowed.
    """
    typer.echo(line, err=True)


def _fail(exc: PrismabibError) -> NoReturn:
    """Report a known prismabib failure and exit non-zero, with no traceback.

    Args:
        exc: The exception to report. Its ``str()`` is printed **verbatim**,
            including newlines -- several of these messages are deliberately
            multi-line (``criteria.yaml``'s unknown-key report, for one) and
            re-wrapping them would destroy the structure that makes them
            readable.

    Raises:
        typer.Exit: Always, with status :data:`_EXIT_KNOWN_ERROR`.
    """
    # The class name is included because the taxonomy is meaningful to anyone
    # reading the docs or filing an issue -- an EntitlementError and a
    # QuotaExceededError call for completely different responses, and §3.3
    # exists so that distinction survives to the surface.
    _echo_err(f"prismabib: {type(exc).__name__}")
    _echo_err(str(exc))
    raise typer.Exit(code=_EXIT_KNOWN_ERROR)


@contextmanager
def _reporting_errors() -> Iterator[None]:
    """Turn any :class:`PrismabibError` raised inside into a clean CLI failure.

    Yields:
        ``None``; the block runs with prismabib's own exception types handled.

    Raises:
        typer.Exit: If the block raises a :class:`PrismabibError`. Every other
            exception propagates untouched, traceback and all -- see the module
            docstring.
    """
    try:
        yield
    except PrismabibError as exc:
        _fail(exc)


def _version_callback(value: bool) -> None:
    """Print the version and exit, for ``prismabib --version``.

    Args:
        value: Whether the flag was given.

    Raises:
        typer.Exit: If ``value`` is true, after printing.
    """
    if value:
        _echo(f"prismabib {__version__}")
        raise typer.Exit()


@app.callback()
def _app_callback(
    # The body never reads `version`; its eager callback does, and then exits. Ruff
    # does not flag it (a docstring-only body is exempt from ARG), so no noqa here.
    version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_version_callback,
            is_eager=True,
            help="Show the installed prismabib version and exit.",
        ),
    ] = False,
) -> None:
    """Root callback; exists only to hang ``--version`` off the app."""


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init(
    slug: Annotated[str, typer.Argument(help="Project slug; also the directory name.")],
    title: Annotated[
        str,
        typer.Option(
            "--title", "-t", help="Human-readable project title, written into project.toml."
        ),
    ] = "Untitled review",
    root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """Create projects/<slug>/ and tell you which two files to edit next.

    Idempotent: re-running never overwrites project.toml, criteria.yaml, or
    decisions.jsonl -- those hold hand-written methodology and human labour.
    """
    with _reporting_errors():
        # The root is resolved here and passed in explicitly, rather than letting
        # Project.init resolve `None` itself, so that "did this already exist?"
        # is asked about exactly the directory init then writes to. It is asked
        # *before* init because init is idempotent and silent about which
        # happened: telling a returning user "created" when nothing was created,
        # and pointing them at a template they filled in months ago, is the kind
        # of small lie that makes a tool untrustworthy.
        projects_root = root if root is not None else ProjectsRootSettings().prismabib_projects_root
        existed = (projects_root / slug / "project.toml").is_file()
        project = Project.init(slug, title=title, root=projects_root)

        _echo(f"{'Reused' if existed else 'Created'} project {slug!r} at {project.root}")
        _echo()
        if existed:
            _echo(
                "project.toml and criteria.yaml were already there and were left exactly "
                "as they are; any missing directory has been recreated."
            )
            _echo()
            _echo("The two files that define this review are, in the order they are used:")
        else:
            _echo("`prismabib search` cannot run until you edit these two files, in this order:")
        _echo()
        _echo(f"  1. {project.root / 'project.toml'}")
        _echo(
            "     Fill in [query] terms / compound_terms -- the Boolean search itself. They "
            "are scaffolded empty, and an empty query is refused rather than silently run "
            "against all of Scopus."
        )
        _echo(f"  2. {project.root / 'criteria.yaml'}")
        _echo(
            "     Set temporal.year_start / year_end, and whichever of doc_types.include and "
            "languages your protocol restricts; every list left empty means no restriction on "
            "that dimension, which is a real eligibility choice rather than a placeholder. "
            "Replace the starter exclusion reason codes with the ones your review actually "
            "distinguishes, and bump `version` whenever you change any of it. The file's own "
            "comments explain each block."
        )
        _echo()
        _echo("Then, in order:")
        _echo(f"  prismabib search {slug}   # spends Scopus quota; resumable")
        _echo(f"  prismabib build {slug}")
        _echo(f"  prismabib flow {slug}")


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class _CaptureProgress:
    """Renders ``capture.*`` log events as human progress lines.

    :func:`~prismabib.capture.writer.capture_search` already emits a structured
    event per page written; this turns that stream into progress a person can
    read, rather than the CLI inventing a second, unrelated progress
    abstraction (a spinner counting nothing, or a percentage guessed from page
    size). It is installed as a structlog processor for the duration of one
    ``search`` and removed afterwards -- see :func:`_capture_progress`.

    The four events it renders are dropped from the normal log stream so the
    same information is not printed twice; every other event passes through
    untouched.
    """

    def __init__(self, emit: Callable[[str], None]) -> None:
        """Args:
        emit: Where to write each progress line (stderr in practice; a
            list's ``append`` in tests).
        """
        self._emit = emit
        self._records = 0

    def __call__(
        self, _logger: WrappedLogger, _method_name: str, event_dict: EventDict
    ) -> EventDict:
        """Structlog processor entry point.

        Args:
            _logger: The wrapped logger; unused (structlog passes all three
                positionally, so the name is free to say so).
            _method_name: The log method called; unused.
            event_dict: The event being logged.

        Returns:
            ``event_dict`` unchanged, for any event this does not render.

        Raises:
            structlog.DropEvent: For an event rendered as a progress line, so
                it is not also printed in raw structured form.
        """
        event = event_dict.get("event")
        if event == "capture.run_started":
            self._emit(f"Run {event_dict.get('run_id')} started.")
        elif event == "capture.run_resumed":
            pages = event_dict.get("pages_already_written", 0)
            self._emit(
                f"Resuming run {event_dict.get('run_id')}: {pages} page(s) already in "
                "Layer 0, continuing from the saved cursor -- those pages are not "
                "re-fetched and cost no quota."
            )
        elif event == "capture.page_written":
            self._records += int(event_dict.get("result_count") or 0)
            total = event_dict.get("total_results")
            seen = f"{self._records:,}" + (f" of {int(total):,}" if total is not None else "")
            self._emit(
                f"  page {int(event_dict.get('page_index', 0)) + 1} written -- "
                f"{seen} records captured, cursor saved"
            )
        elif event == "capture.run_sealed":
            self._emit(f"Run {event_dict.get('run_id')} sealed; Layer 0 is now immutable.")
        else:
            return event_dict
        raise structlog.DropEvent


@contextmanager
def _capture_progress(emit: Callable[[str], None]) -> Iterator[None]:
    """Install :class:`_CaptureProgress` for the duration of the block.

    Args:
        emit: Where progress lines are written.

    Yields:
        ``None``, with the processor installed ahead of every other configured
        structlog processor.
    """
    previous = list(structlog.get_config()["processors"])
    structlog.configure(processors=[_CaptureProgress(emit), *previous])
    try:
        yield
    finally:
        # Restored even on failure: structlog's configuration is process-global,
        # and a CLI that left a stderr-printing processor behind would corrupt
        # the logging of anything sharing the interpreter (notably a test
        # session, where the leak would surface as an unrelated failure).
        structlog.configure(processors=previous)


@app.command()
def search(
    slug: Annotated[str, typer.Argument(help="Project slug to capture into.")],
    root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """Run (or resume) the Scopus capture into the immutable Layer 0 archive.

    Long-running and it spends your weekly Scopus quota. Interrupting is safe:
    every page is written to disk as it arrives and a cursor is saved after
    each one, so re-running this command resumes from the last page written
    instead of re-fetching it.
    """
    with _reporting_errors():
        project = Project.open(slug, root=root)

        _echo_err(f"Capturing Scopus for project {slug!r} into {project.raw_dir}")
        _echo_err(
            "This spends weekly Scopus API quota. Every page is persisted as it arrives "
            "and a cursor is saved after each one, so Ctrl-C loses nothing: re-run "
            f"`prismabib search {slug}` to resume from the last page written."
        )
        _echo_err()

        try:
            with _capture_progress(_echo_err):
                manifest = capture_search(project)
        except KeyboardInterrupt:
            _echo_err()
            _echo_err(
                "Interrupted. Every page already fetched is in Layer 0 and the cursor is "
                f"saved -- re-run `prismabib search {slug}` to continue from there."
            )
            raise typer.Exit(code=_EXIT_INTERRUPTED) from None

        _echo_err()
        _print_manifest(manifest, slug=slug, raw_dir=project.raw_dir)


def _print_manifest(manifest: RunManifest, *, slug: str, raw_dir: Path) -> None:
    """Print the sealed run's manifest as a short provenance summary.

    Args:
        manifest: The manifest :func:`capture_search` returned.
        slug: The project slug, used for the next-step hint.
        raw_dir: The project's Layer 0 directory.
    """
    _echo(f"Run {manifest.run_id} sealed at {raw_dir / manifest.run_id}")
    _echo(f"  query          {manifest.query}")
    _echo(f"  view           {manifest.view}")
    _echo(f"  total_results  {manifest.total_results:,}  (the PRISMA 'identified' count)")
    _echo(f"  pages          {manifest.pages_fetched:,}")
    _echo(f"  payload_sha256 {manifest.payload_sha256}")
    _echo(f"  client_version {manifest.client_version}")
    _echo(f"  criteria       {manifest.criteria_version}")
    _echo()
    _echo(f"Next: prismabib build {slug}")


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------


#: How many skipped-entry references ``prismabib build`` names before it says
#: "and N more". The full list is in the store's ``malformed_entries`` table.
_MAX_LISTED_MALFORMED_ENTRIES: Final = 5


@app.command()
def build(
    slug: Annotated[str, typer.Argument(help="Project slug whose store to build.")],
    rebuild: Annotated[
        bool,
        typer.Option(
            "--rebuild",
            help=(
                "Delete and rebuild the store from Layer 0. Without it, an existing "
                "store is reused as-is and no Layer 0 run captured since is loaded."
            ),
        ),
    ] = False,
    root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """(Re)build the Layer 1 DuckDB store from the Layer 0 archive.

    The store is derived data: deleting it and running this again loses
    nothing. Layer 0 is the archive of record.
    """
    with _reporting_errors():
        project = Project.open(slug, root=root)
        stats = build_store(project, rebuild=rebuild)
        _print_store_stats(stats, slug=slug, db_path=project.db_path)


def _print_store_stats(stats: StoreStats, *, slug: str, db_path: Path) -> None:
    """Print one :class:`StoreStats` as a readable summary.

    Every field of ``stats`` that a reader could act on is rendered here.
    ``malformed_entries_skipped`` in particular: it was reported only through
    a structlog warning that scrolls past above this summary, while
    ``unmapped_country_values`` -- which loses no record at all -- got a
    rendered line. A skipped entry is the more consequential of the two.

    Args:
        stats: The stats :func:`build_store` returned.
        slug: The project slug, used for the next-step hint.
        db_path: Where the store lives.
    """
    _echo(f"{'Rebuilt' if stats.rebuilt else 'Reused existing'} store at {db_path}")
    _echo()
    rows: tuple[tuple[str, int], ...] = (
        ("runs loaded", stats.runs_loaded),
        ("records", stats.records_loaded),
        ("authors", stats.authors_loaded),
        ("affiliations", stats.affiliations_loaded),
        ("venues", stats.venues_loaded),
        ("keywords", stats.keywords_loaded),
        ("record-keyword links", stats.record_keyword_links_loaded),
        ("subject-area links", stats.subject_area_links_loaded),
        ("citation snapshots", stats.citation_snapshots_loaded),
    )
    width = max(len(label) for label, _ in rows)
    for label, count in rows:
        _echo(f"  {label:<{width}}  {count:>9,}")

    _echo()
    _echo(
        f"  duplicate DOI groups: {stats.duplicate_doi_groups:,} "
        f"({stats.duplicate_records:,} records). Reported, never applied -- every one of "
        "those rows is still in the store; deduplication is a screening decision."
    )
    if stats.malformed_entries_skipped:
        skipped = stats.malformed_entries_skipped
        # Capped, not truncated to a count: the operator's next question is
        # always *which line*, and Layer 0 is immutable so they can go read
        # it. But a capture with thousands of skips would otherwise print
        # thousands of lines, so the rest are left in the `malformed_entries`
        # table, which is queryable and does not scroll.
        listed = ", ".join(skipped[:_MAX_LISTED_MALFORMED_ENTRIES])
        if len(skipped) > _MAX_LISTED_MALFORMED_ENTRIES:
            listed += f", ... and {len(skipped) - _MAX_LISTED_MALFORMED_ENTRIES:,} more"
        _echo(
            f"  {len(skipped):,} Layer 0 entry/entries could not be parsed into a record "
            "and were skipped, not loaded. That is a count of *entries*, not of records: "
            "one already loaded from an earlier run is still in the store. "
            f"Full list in the store's malformed_entries table. At {listed}"
        )
    if stats.unmapped_country_values:
        _echo(
            f"  {len(stats.unmapped_country_values):,} affiliation country value(s) did not "
            "map to an ISO 3166-1 alpha-3 code and are stored as the original text: "
            + ", ".join(repr(value) for value in stats.unmapped_country_values)
        )
    if not stats.rebuilt:
        _echo()
        _echo(
            "  Nothing was loaded: this reused the store that was already there. Run with "
            "--rebuild to fold in any Layer 0 run captured since it was built."
        )
    _echo()
    _echo(f"Next: prismabib flow {slug}")


# ---------------------------------------------------------------------------
# flow
# ---------------------------------------------------------------------------


@app.command()
def flow(
    slug: Annotated[str, typer.Argument(help="Project slug to report on.")],
    root: Annotated[Path | None, _ROOT_OPTION] = None,
) -> None:
    """Print the PRISMA 2020 flow counts, derived fresh from the store and log.

    Nothing here is cached or stored: every number is recomputed from Layer 1
    and decisions.jsonl on each run, so it always reflects screening as it
    stands right now.
    """
    with _reporting_errors():
        project = Project.open(slug, root=root)
        counts = compute_flow_counts(project)
        _print_flow(counts, slug=slug)
        _warn_if_inconsistent(counts)


def _print_flow(counts: FlowCounts, *, slug: str) -> None:
    """Render :class:`FlowCounts` as a readable PRISMA 2020 summary.

    Args:
        counts: The counts to render.
        slug: The project slug, for the heading.
    """

    def row(label: str, value: str) -> None:
        _echo(f"  {label:<46}{value:>9}")

    def minus(count: int) -> str:
        """A subtraction, rendered so that zero reads as zero and not ``-0``."""
        return f"-{count:,}" if count else "0"

    _echo(f"PRISMA 2020 flow -- project {slug!r}")
    _echo()
    _echo("Identification")
    row("records identified (Scopus total_results)", f"{counts.identified:,}")
    _echo()
    # PRISMA 2020 puts these before screening, not inside it: they are records
    # that never reached a screening decision at all. Rendered even when zero,
    # because a reader checking a published diagram needs to see that the line
    # was considered and came to nothing, not guess whether it was omitted.
    _echo("Removed before screening")
    row("duplicates across searches", minus(counts.duplicates_across_searches))
    row("other reasons (unreadable capture entries)", minus(counts.removed_other_reasons))
    _echo()
    _echo("Screening -- automated, from criteria.yaml")
    row("excluded by year / subject area / doc type", minus(counts.excluded_automated))
    row("remaining", f"{counts.after_automated:,}")
    row("excluded by language", minus(counts.excluded_language))
    row("remaining, to title/abstract screening", f"{counts.after_language:,}")
    _echo()
    _echo("Screening -- title/abstract, from logged human decisions")
    row("excluded", minus(counts.excluded_title_abstract))
    row("unsure or not yet screened", f"{counts.unsure_title_abstract:,}")
    _echo("      (unsure never resolves to inclusion; it stays in the queue)")
    row("sought for full-text retrieval", f"{counts.retrieved_fulltext:,}")
    _echo()
    _echo("Eligibility -- full text, from logged human decisions")
    row("excluded", minus(sum(counts.excluded_fulltext.values())))
    if counts.excluded_fulltext:
        # Iterated in sorted reason-code order, matching the order
        # `_excluded_by_reason` fixes so two machines print the same report
        # (§3.7.3 rule 3: never rely on set ordering).
        for reason, count in sorted(counts.excluded_fulltext.items()):
            row(f"    reason {reason}", f"{count:,}")
    else:
        _echo("      (no full-text exclusion reason codes logged)")
    row("unsure or not yet screened", f"{counts.unsure_fulltext:,}")
    _echo("      (unsure never resolves to inclusion; it stays in the queue)")
    _echo()
    _echo("Included")
    row("studies in the final corpus", f"{counts.included:,}")


def _warn_if_inconsistent(counts: FlowCounts) -> None:
    """Warn on stderr if the diagram's arithmetic does not close.

    :func:`~prismabib.prisma.flow.compute_flow_counts` deliberately does not
    call :meth:`FlowCounts.assert_consistent` itself, so that a genuine
    disagreement is returned for a caller to inspect rather than raised from a
    function whose job is only to compute. This is that caller: it reports the
    disagreement in full and still prints the numbers, and it exits ``0``.

    Exiting non-zero would be wrong here. The identity this function can
    realistically catch is equation 1, which compares Scopus's own
    ``total_results`` (summed over the project's distinct searches, less the
    records removed before screening) against rows actually in Layer 1, and an
    *incomplete but perfectly valid* capture (a run interrupted, a build not yet
    re-run) breaks it. That is a state a researcher needs to see
    described, in the middle of a report they can still read -- not one that
    should make a reporting command look like it crashed.

    Args:
        counts: The counts just printed.
    """
    try:
        counts.assert_consistent()
    except PrismabibError as exc:
        _echo_err()
        _echo_err("WARNING: these counts do not close into a consistent diagram.")
        _echo_err(str(exc))
        _echo_err(
            "Do not publish this diagram until the discrepancy is explained. The usual "
            "cause is a Layer 0 capture that is incomplete, or a store built before the "
            "last `prismabib search` finished -- re-run `prismabib build <slug> --rebuild`."
        )


def main() -> None:
    """Console-script entry point (``[project.scripts]`` in pyproject.toml)."""
    app()


if __name__ == "__main__":  # pragma: no cover
    main()
