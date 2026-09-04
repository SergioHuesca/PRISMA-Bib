#!/usr/bin/env -S uv run python
"""Assisted manual fetch: open DOI tabs, watch downloads, file what arrives (ADR 0020).

BUILD_PLAN §Stage 6's resolver chain and Crossref TDM
(:class:`~prismabib.fulltext.resolve.CrossrefTdmResolver`) get what is
legally and technically reachable without credentials. The residue --
paywalled content, and hosts that refuse automated clients -- has no
machine-readable route at all: a human must fetch it. ``fulltext_missing.py``
already turns that residue into a checklist; this script removes the
bookkeeping cost of acting on it.

**What this does, and does not, automate.** ADR 0020 draws the line at
retrieval itself: this script opens a ``doi.org`` tab per record in the
operator's own browser -- the same click a reviewer would make by hand,
through their own authenticated session and their own institution's access --
and then watches their download directory for what they save. It never
fetches anything itself. Identification and filing (which downloaded PDF
belongs to which record, and copying it into the drop-box
:class:`~prismabib.fulltext.resolve.ManualDropResolver` reads) are
:mod:`prismabib.fulltext.assist`'s job -- the tested half; this script is the
interactive driver, deliberately outside ``mypy --strict`` and the
``fulltext`` coverage gate (ADR 0020 Decision 4: a prompting command has no
place under ``mypy --strict`` scrutiny of exhaustiveness the way pure logic
does, and this is the one place in the project that legitimately blocks on a
human).

**Why this is not a ``prismabib`` subcommand.** ``cli.py``'s own help states
the posture: prismabib is the non-interactive half of the tool, and
"decisions are human events, and a CLI is the wrong place to make them."
Prompting contradicts that. This lives in ``scripts/`` alongside
``fulltext_missing.py`` instead.

Usage:
    uv run scripts/fetch_assist.py <slug> [--root projects] [--batch N]
        [--downloads DIR] [--poll-timeout SECONDS]
"""

from __future__ import annotations

import argparse
import sys
import time
import webbrowser
from collections.abc import Callable, Sequence
from pathlib import Path

import pdfplumber
import structlog

from prismabib.errors import PrismabibError
from prismabib.fulltext.assist import (
    Candidate,
    Identification,
    ManualDropFilingError,
    file_manual_drop,
    identify_pdf,
)
from prismabib.fulltext.capture import already_resolved_record_ids
from prismabib.prisma.engine import manual_abstract_set
from prismabib.project import Project
from prismabib.store.db import connect

logger = structlog.get_logger(__name__)

#: `webbrowser.open_new_tab` spawns a subprocess and never touches the
#: sockets `pytest-socket` intercepts, so nothing except an explicit
#: injection at this call site can stop a forgetful test from launching a
#: real browser (ADR 0020 Constraints). This module never calls
#: `webbrowser.open_new_tab` directly anywhere except as this default.
BrowserOpener = Callable[[str], bool]

#: A download that has not finished yet, in the shapes this project's own
#: measurement named: Chrome/Edge's `.crdownload`, Firefox's `.part`, and the
#: generic `.tmp` several download managers use. A file carrying one of these
#: as its *final* suffix is not yet a candidate PDF, no matter what the rest
#: of its name looks like.
_PARTIAL_SUFFIXES = frozenset({".crdownload", ".part", ".tmp"})

_DEFAULT_POLL_INTERVAL = 1.0
_DEFAULT_STABILITY_CHECKS = 2


def _looks_like_a_finished_pdf(path: Path) -> bool:
    """Whether ``path`` names a plausibly-finished PDF download.

    Args:
        path: A candidate file under the watched download directory.

    Returns:
        ``True`` iff ``path`` is a regular file whose suffix is ``.pdf``
        (case-insensitive) and not one of :data:`_PARTIAL_SUFFIXES` --
        a browser renames ``paper.pdf.crdownload``/``paper.pdf.part`` to
        ``paper.pdf`` only once the download completes, so this is "not
        still downloading", not yet "finished downloading": callers still
        wait for the size to stabilise (:func:`_wait_until_stable`) before
        treating it as ready, since the rename itself is not atomic with the
        last bytes landing on disk on every filesystem.
    """
    if not path.is_file():
        return False
    suffix = path.suffix.casefold()
    return suffix == ".pdf" and suffix not in _PARTIAL_SUFFIXES


def _poll_for_new_pdfs(
    download_dir: Path,
    already_seen: frozenset[Path],
    *,
    expected: int,
    timeout: float,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
    stability_checks: int = _DEFAULT_STABILITY_CHECKS,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> list[Path]:
    """Watch ``download_dir`` for new, finished PDFs, up to ``timeout`` seconds.

    A browser download is not atomic: a new path can appear (and grow) well
    before its content is complete, so a size read the instant a path first
    appears is not one worth trusting. This tracks each new candidate's size
    across successive polls and only accepts it once that size has stopped
    changing for ``stability_checks`` consecutive polls.

    Args:
        download_dir: The operator's browser download directory.
        already_seen: Every finished ``*.pdf`` present before this batch's
            tabs were opened -- an old download sitting in the folder from
            an unrelated session must never be picked up as this batch's
            result.
        expected: How many new PDFs this batch could plausibly still
            produce (the size of the still-unmatched candidate set). Polling
            stops early once this many have been found stable, rather than
            always waiting out the full timeout.
        timeout: The overall time budget, in seconds, from the first poll.
        poll_interval: Seconds between directory scans.
        stability_checks: How many consecutive equal, non-zero size readings
            count as "finished downloading".
        sleep: Injected sleep function, defaulting to ``time.sleep``.
        clock: Injected monotonic clock function, defaulting to
            ``time.monotonic``.

    Returns:
        Newly-appeared, size-stable PDF paths, in the order each one first
        became stable -- a temporal order, not a filesystem-listing order,
        so it reflects the sequence the operator actually finished saving
        files in.
    """
    deadline = clock() + timeout
    finished: list[Path] = []
    finished_set: set[Path] = set()
    last_size: dict[Path, int] = {}
    consecutive_stable: dict[Path, int] = {}

    while clock() < deadline and len(finished) < expected:
        current = {path for path in download_dir.glob("*") if _looks_like_a_finished_pdf(path)}
        candidates = sorted(current - already_seen - finished_set)

        for path in candidates:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if last_size.get(path) == size and size > 0:
                consecutive_stable[path] = consecutive_stable.get(path, 0) + 1
            else:
                consecutive_stable[path] = 0
            last_size[path] = size

            if consecutive_stable[path] >= stability_checks:
                finished.append(path)
                finished_set.add(path)

        if len(finished) < expected:
            sleep(poll_interval)

    return finished


def _extract_first_page_text(path: Path) -> str:
    """The extracted text of a PDF's first page only.

    Identification needs no more than page 1 -- the DOI and title both live
    there -- so this calls ``pdfplumber`` directly rather than
    :func:`prismabib.fulltext.extract.extract_pdf`, which walks every page
    for a different purpose (full section extraction) and would cost far
    more work for the one page this actually reads.

    Args:
        path: A local PDF file.

    Returns:
        The first page's extracted text, or ``""`` if the PDF has no pages
        or no extractable text layer on page 1.
    """
    with pdfplumber.open(path) as pdf:
        if not pdf.pages:
            return ""
        return pdf.pages[0].extract_text() or ""


def _prompt_for_match(
    path: Path,
    identification: Identification,
    candidates: Sequence[Candidate],
    *,
    prompt: Callable[[str], str],
) -> Candidate | None:
    """Ask the operator which record ``path`` belongs to, since identification was not confident.

    Args:
        path: The downloaded PDF being placed.
        identification: :func:`~prismabib.fulltext.assist.identify_pdf`'s
            (non-confident) result, shown as a hint, never acted on
            unattended -- ADR 0020 Constraints: no threshold may be set such
            that a best guess is filed without asking.
        candidates: The still-unmatched batch candidates to choose among.
        prompt: Injected input function (``input`` by default at the call
            site), so this can be exercised without blocking on a terminal.

    Returns:
        The chosen :class:`~prismabib.fulltext.assist.Candidate`, or
        ``None`` if the operator chose to skip this file.
    """
    print(f"\n{path.name}: no confident match.")
    print(
        f"  best guess: {identification.record_id!r} "
        f"(score={identification.score:.2f}, margin={identification.margin:.2f})"
    )
    for index, candidate in enumerate(candidates, start=1):
        hint = "  <-- best guess" if candidate.record_id == identification.record_id else ""
        print(f"  [{index}] {candidate.record_id} -- {candidate.title}{hint}")
    print("  [0] skip this file (do not file it anywhere)")

    while True:
        raw = prompt("Which record is this PDF for? ").strip()
        if raw == "0":
            return None
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(candidates):
                return candidates[choice - 1]
        print(f"  enter a number from 0 to {len(candidates)}.")


def run(
    *,
    slug: str,
    root: Path,
    batch_size: int,
    download_dir: Path,
    poll_timeout: float,
    opener: BrowserOpener = webbrowser.open_new_tab,
    prompt: Callable[[str], str] = input,
) -> None:
    """Run one assisted-fetch session.

    Args:
        slug: The project to fetch for.
        root: The projects root directory.
        batch_size: How many still-unresolved records to open tabs for.
        download_dir: The operator's browser download directory to watch.
        poll_timeout: How long to wait for downloads to appear and
            stabilise, in seconds, after opening every tab.
        opener: Opens one URL in the operator's browser. Defaults to
            :func:`webbrowser.open_new_tab` -- **never** call that function
            directly anywhere else in this module; every real invocation
            must go through this injectable seam (ADR 0020 Constraints).
        prompt: Reads one line of operator input. Defaults to the builtin
            ``input``.

    Raises:
        PrismabibError: Propagated from opening the project or its store --
            an unknown slug, a missing store, or a store predating the
            schema this reads, exactly the failure modes
            ``fulltext_missing.py`` already surfaces the same way.
    """
    project = Project.open(slug, root=root)
    sought = sorted(manual_abstract_set(project))
    resolved = already_resolved_record_ids(project.fulltext_dir, include_unsealed=True)
    missing = [record_id for record_id in sought if record_id not in resolved]
    batch = missing[:batch_size]

    if not batch:
        print(f"Nothing to fetch: {len(sought)} records sought, none still missing full text.")
        return

    connection = connect(project, read_only=True)
    try:
        placeholders = ", ".join("?" for _ in batch)
        rows = connection.execute(
            f"SELECT record_id, title, doi FROM records WHERE record_id IN ({placeholders})",
            batch,
        ).fetchall()
    finally:
        connection.close()

    metadata = {record_id: (title, doi) for record_id, title, doi in rows}
    candidates = [
        Candidate(record_id=record_id, title=metadata[record_id][0], doi=metadata[record_id][1])
        for record_id in batch
        if record_id in metadata
    ]

    print(f"Fetching {len(candidates)} of {len(missing)} still-missing records this session.\n")

    opened = 0
    for candidate in candidates:
        if not candidate.doi:
            print(f"  (no DOI recorded for {candidate.record_id}; not opening a tab)")
            continue
        url = f"https://doi.org/{candidate.doi}"
        print(f"  opening {url}  ({candidate.record_id})")
        opener(url)
        opened += 1

    if opened == 0:
        print("\nNo record in this batch has a DOI to open. Nothing more this script can do.")
        return

    download_dir.mkdir(parents=True, exist_ok=True)
    already_seen = frozenset(
        path for path in download_dir.glob("*.pdf") if _looks_like_a_finished_pdf(path)
    )
    print(f"\nWatching {download_dir} for up to {poll_timeout:.0f}s. Save each PDF when it opens.")

    new_pdfs = _poll_for_new_pdfs(
        download_dir, already_seen, expected=len(candidates), timeout=poll_timeout
    )

    remaining = list(candidates)
    filed: list[tuple[str, Path]] = []
    asked: list[tuple[str, Path]] = []
    unplaced: list[Path] = []

    for path in new_pdfs:
        if not remaining:
            unplaced.append(path)
            continue
        page_text = _extract_first_page_text(path)
        identification = identify_pdf(page_text, remaining)

        chosen: Candidate | None
        if identification.confident and identification.record_id is not None:
            chosen = next(c for c in remaining if c.record_id == identification.record_id)
        else:
            chosen = _prompt_for_match(path, identification, remaining, prompt=prompt)
            if chosen is not None:
                asked.append((chosen.record_id, path))

        if chosen is None:
            unplaced.append(path)
            continue

        try:
            destination = file_manual_drop(project.fulltext_dir, chosen.record_id, path)
        except ManualDropFilingError as error:
            print(f"  could not file {path.name}: {error}", file=sys.stderr)
            unplaced.append(path)
            continue

        filed.append((chosen.record_id, destination))
        remaining = [c for c in remaining if c.record_id != chosen.record_id]

    print("\n--- Session summary ---")
    if filed:
        print(f"Filed ({len(filed)}):")
        for record_id, destination in filed:
            asked_marker = " (confirmed by operator)" if record_id in {r for r, _ in asked} else ""
            print(f"  {record_id} -> {destination}{asked_marker}")
    if unplaced:
        print(f"Could not place ({len(unplaced)}):")
        for path in unplaced:
            print(f"  {path}")
    if remaining:
        print(f"Still missing, no PDF arrived ({len(remaining)}):")
        for candidate in remaining:
            print(f"  {candidate.record_id} -- {candidate.title}")


def main() -> None:
    """Parse arguments and run one session, translating a known error the CLI's way."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("slug")
    parser.add_argument("--root", type=Path, default=Path("projects"))
    parser.add_argument(
        "--batch", type=int, default=5, help="How many unresolved records to fetch this session."
    )
    parser.add_argument(
        "--downloads",
        type=Path,
        default=Path.home() / "Downloads",
        help="The directory the operator's browser saves downloads to.",
    )
    parser.add_argument(
        "--poll-timeout",
        type=float,
        default=600.0,
        help="How long to wait for downloads to appear and finish, in seconds.",
    )
    args = parser.parse_args()

    try:
        run(
            slug=args.slug,
            root=args.root,
            batch_size=args.batch,
            download_dir=args.downloads,
            poll_timeout=args.poll_timeout,
        )
    except PrismabibError as error:
        # Same contract `fulltext_missing.py` holds itself to
        # (`test_cli__known_error__exits_nonzero_without_a_traceback`): an
        # unknown slug, a missing store, or one predating this schema are all
        # ordinary operator situations, not bugs worth a traceback.
        print(f"prismabib: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
