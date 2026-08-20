"""The Layer 0 acquisition run: fetch, persist, and seal one Scopus search (BUILD_PLAN §Stage 2).

:func:`capture_search` is the frozen contract at BUILD_PLAN line 785. It
drives :class:`~prismabib.sources.scopus.ScopusClient` end to end for one
Boolean query and writes ``raw/<run_id>/page-0000.jsonl``,
``page-0001.jsonl``, ... plus ``raw/<run_id>/manifest.json`` (S02-AC1).

**How "persisted verbatim before parsing" (§2.2 line 102) is actually
satisfied.** :meth:`ScopusClient.search` already returns *parsed* Python
dicts -- it never hands this module raw bytes. Verbatim, pre-parse
persistence therefore does not happen in this module; it happens one layer
down, in :class:`~prismabib.sources.cache.HttpCache`, whose ``store`` call
inside ``ScopusClient._get`` runs on the exact response bytes *before*
``_parse_json`` is ever called. This module is the reason that cache exists
and is never optional here: :func:`capture_search` always constructs its
``ScopusClient`` with an :class:`HttpCache` rooted at
``raw/_cache/`` -- a directory that lives under the immutable ``raw/`` tree
(so it is gitignored exactly like everything else under §2.5) but is
deliberately *not* a run directory: its name starts with ``_`` precisely so
the run-directory scan in :func:`_find_resumable_run` skips it, and it
never receives a ``manifest.json`` of its own, so it is never "sealed" and
remains writable across every run, past and future. Because the cache is
keyed on ``(url, params)`` (BUILD_PLAN line 772), a warm-cache re-run
returns byte-for-byte the same response bodies, which is what makes
S02-AC2 ("re-running with the cache warm produces a byte-identical payload
hash") hold: the JSONL bytes this module writes are a deterministic
re-encoding (sorted keys, compact separators) of a parsed page that is
itself a pure function of those cached bytes.

**Sealing and resumption -- the subtle part.** A run directory is in
exactly one of two states, distinguished *only* by the presence of
``manifest.json``:

- **Unsealed** (no ``manifest.json``): still in progress, or was
  interrupted. It carries a ``cursor.json`` sidecar recording the query,
  view, endpoint, start time, and the page filenames already durably
  written. ``cursor.json`` is explicitly **not** part of the sealed
  payload -- it is resumption bookkeeping, deleted once the run seals, and
  never contributes to ``payload_sha256``.
- **Sealed** (``manifest.json`` present): finished, immutable, and -- per
  BUILD_PLAN §2.2 -- never written to again, in code, not just by
  convention. Every write in this module (a page file, ``cursor.json``, or
  the manifest itself) first calls :func:`_guard_writable`, which raises
  :class:`SealedRunError` if ``manifest.json`` is already there. A run
  directory becomes sealed exactly once, at the very end of
  :func:`capture_search`, by writing ``manifest.json`` -- there is no
  operation that un-seals it.

The two states interact like this: :func:`capture_search` never accepts an
explicit run id (it is not part of the frozen contract). Instead, on every
call it looks for an *unsealed* run directory under ``raw/`` whose
``cursor.json`` matches the requested query/view/endpoint exactly
(:func:`_find_resumable_run`) and, if found, resumes it; a *sealed*
directory is never a resume candidate, so calling :func:`capture_search`
again after a completed run always starts a brand-new run directory (a new
``run_id``) rather than attempting -- and having :func:`_guard_writable`
refuse -- to write into the old one. Resumption itself replays
``ScopusClient.search`` from its own beginning (``cursor=*``) every time,
because :meth:`ScopusClient.search` does not accept an injected starting
cursor; pages already recorded in ``cursor.json`` are walked again but
never rewritten (each is a cache hit -- no quota spent, no duplicate
records), and writing resumes at the first page index not yet present in
``payload_files``. A page that fails to parse (BUILD_PLAN line 821) raises
out of ``ScopusClient.search`` *before* this module ever attempts to create
that page's file, so every page file already written for that run is left
completely untouched -- there is no delete, no truncate, and no rewrite of
prior pages anywhere in this module's write path.

**On ``total_results``.** Recorded once, from page index 0 of the current
attempt's replay (page 0 is visited on every call, resumed or not, since
replay always starts at ``cursor=*``). It is read from the server's own
``opensearch:totalResults`` via
:func:`~prismabib.sources.scopus.extract_total_results` and is the **only**
value written to :class:`~prismabib.capture.manifest.RunManifest.total_results`
-- BUILD_PLAN S02-AC5 requires this be the sole source of the PRISMA
"records identified" count; nothing here derives it from
``len(payload_files)`` or a count of parsed entries.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import BaseModel

from prismabib import __version__ as _CLIENT_VERSION
from prismabib.capture.manifest import RunManifest
from prismabib.config import Settings
from prismabib.errors import PrismabibError, ValidationError
from prismabib.project import Project
from prismabib.query import build_query_for_project
from prismabib.sources.cache import HttpCache
from prismabib.sources.scopus import (
    JsonDict,
    ScopusClient,
    extract_next_cursor,
    extract_total_results,
)

logger = structlog.get_logger(__name__)

_MANIFEST_FILENAME = "manifest.json"
_CURSOR_FILENAME = "cursor.json"
_CACHE_DIRNAME = "_cache"


class SealedRunError(PrismabibError):
    """A write was attempted against a Layer 0 run directory that is already sealed.

    Not one of the named leaves in the BUILD_PLAN §3.3 error tree (that
    tree predates this module), but a direct subclass of
    :class:`~prismabib.errors.PrismabibError` for the Layer 0 immutability
    invariant §2.2 assigns to :mod:`prismabib.capture.writer`: a run
    directory carrying a ``manifest.json`` must never be written to again,
    enforced here in code (see :func:`_guard_writable`), not left to
    convention or filesystem permissions (the latter do not survive this
    project's NTFS working copy and are not portable in any case).
    """


class _CursorState(BaseModel):
    """The resumable ``cursor.json`` sidecar for one in-progress (unsealed) run.

    See the module docstring's "Sealing and resumption" section for why
    this is deliberately kept separate from
    :class:`~prismabib.capture.manifest.RunManifest` and never contributes
    to ``payload_sha256``.
    """

    query: str
    view: str
    endpoint: str
    started_at: datetime
    payload_files: list[str] = []

    #: The cursor to resume from -- the ``@next`` of the last page actually
    #: written. Without it, a resumed run replays from ``cursor="*"`` and only
    #: avoids re-fetching because ``raw/_cache/`` happens to be warm. That cache
    #: is gitignored and disposable, so on a cold cache a run interrupted at page
    #: 40 of 71 re-requests all 40 pages against a weekly quota, while Layer 0
    #: already holds them on disk. BUILD_PLAN line 768 requires resuming "without
    #: re-fetching", and §5 risk 2 says "never re-fetch what Layer 0 already
    #: holds". ``None`` means the run has no page written yet.
    next_cursor: str | None = None


def is_sealed(run_dir: Path) -> bool:
    """Whether ``run_dir`` is a finished, immutable Layer 0 run directory.

    Args:
        run_dir: A candidate ``raw/<run_id>/`` directory.

    Returns:
        ``True`` if and only if ``run_dir/manifest.json`` exists -- the
        sole signal, by design, of "sealed" (BUILD_PLAN §2.2).
    """
    return (run_dir / _MANIFEST_FILENAME).is_file()


def _guard_writable(run_dir: Path) -> None:
    """Refuse any write into an already-sealed run directory.

    Args:
        run_dir: The run directory a write is about to target.

    Raises:
        SealedRunError: If ``run_dir`` already carries a ``manifest.json``.
    """
    if is_sealed(run_dir):
        raise SealedRunError(
            f"{run_dir} already carries a {_MANIFEST_FILENAME} and is sealed. "
            "Layer 0 run directories are immutable once sealed (BUILD_PLAN "
            "§2.2, lines 99-102): nothing may write here again. Call "
            "capture_search() again to start a fresh run instead."
        )


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (write-to-temp, then rename).

    A process killed mid-write can never leave a torn/partial file at
    ``path`` -- it either never appears, or appears complete -- which is
    what lets a resumed run trust every entry it finds in ``cursor.json``'s
    ``payload_files``.

    Args:
        path: The destination path.
        data: The exact bytes to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(path)


def _load_cursor_state(cursor_path: Path) -> _CursorState:
    """Parse a ``cursor.json`` sidecar.

    Args:
        cursor_path: Path to the ``cursor.json`` file.

    Returns:
        The parsed :class:`_CursorState`.

    Raises:
        OSError: If ``cursor_path`` cannot be read.
        ValueError: If its contents are not valid JSON matching the
            expected schema (Pydantic's ``ValidationError`` subclasses
            ``ValueError``).
    """
    return _CursorState.model_validate_json(cursor_path.read_text(encoding="utf-8"))


def _save_cursor_state(run_dir: Path, state: _CursorState) -> None:
    """Persist ``cursor.json`` for an in-progress run, guarding against a sealed run.

    Args:
        run_dir: The run directory.
        state: The resumption state to write.

    Raises:
        SealedRunError: If ``run_dir`` is already sealed.
    """
    _guard_writable(run_dir)
    _atomic_write_bytes(
        run_dir / _CURSOR_FILENAME,
        state.model_dump_json(indent=2).encode("utf-8"),
    )


def _new_run_id() -> str:
    """Generate a fresh, sortable, collision-safe run id.

    Returns:
        ``<UTC timestamp>-<8 hex chars>``, e.g.
        ``20260115T090000Z-3f9a2c11`` -- sortable so that
        :func:`_find_resumable_run` can deterministically pick the most
        recent match when more than one unsealed run directory qualifies.
    """
    return f"{datetime.now(UTC):%Y%m%dT%H%M%S}Z-{uuid.uuid4().hex[:8]}"


def _find_resumable_run(raw_dir: Path, *, query: str, view: str, endpoint: str) -> Path | None:
    """Find an unsealed run directory whose recorded request matches exactly.

    Args:
        raw_dir: The project's ``raw/`` directory.
        query: The Boolean query string this call was asked to run.
        view: The Scopus response view this call was asked to use.
        endpoint: The Scopus endpoint this call was asked to use.

    Returns:
        The most recent (by run id, which sorts by time) unsealed run
        directory whose ``cursor.json`` matches ``query``, ``view``, and
        ``endpoint`` exactly, or ``None`` if there is no such directory --
        including when ``raw_dir`` does not exist yet, or when a candidate
        entry is the shared HTTP cache directory (:data:`_CACHE_DIRNAME`,
        which is never a run directory) or a sealed run (never resumed; see
        the module docstring).
    """
    if not raw_dir.is_dir():
        return None

    candidates: list[Path] = []
    for entry in raw_dir.iterdir():
        if not entry.is_dir() or entry.name == _CACHE_DIRNAME:
            continue
        if is_sealed(entry):
            continue
        cursor_path = entry / _CURSOR_FILENAME
        if not cursor_path.is_file():
            continue
        try:
            state = _load_cursor_state(cursor_path)
        except (OSError, ValueError):
            # An unreadable/corrupt sidecar is not a resume candidate; it is
            # simply left alone (not deleted -- see the "never truncates or
            # deletes" rule this module follows throughout).
            continue
        if state.query == query and state.view == view and state.endpoint == endpoint:
            candidates.append(entry)

    if not candidates:
        return None
    return max(candidates, key=lambda path: path.name)


def _write_page(run_dir: Path, filename: str, page: JsonDict) -> None:
    """Write one page's deterministic JSONL re-encoding, guarding against a sealed run.

    Args:
        run_dir: The run directory.
        filename: The page's filename, e.g. ``"page-0000.jsonl"``.
        page: The parsed Scopus search response for this page.

    Raises:
        SealedRunError: If ``run_dir`` is already sealed.
    """
    _guard_writable(run_dir)
    encoded = json.dumps(page, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    _atomic_write_bytes(run_dir / filename, encoded.encode("utf-8"))


def _payload_sha256(run_dir: Path, payload_files: list[str]) -> str:
    """Hash the concatenation of every page file, in fetch order.

    Args:
        run_dir: The run directory.
        payload_files: Page filenames, in fetch (and therefore
            concatenation) order.

    Returns:
        The hex-encoded SHA-256 digest over the concatenated bytes of every
        file in ``payload_files``, read from disk in order.
    """
    digest = hashlib.sha256()
    for filename in payload_files:
        digest.update((run_dir / filename).read_bytes())
    return digest.hexdigest()


def _entry_count(page: JsonDict) -> int | None:
    """Best-effort count of the ``entry`` records on a page, for logging only.

    Args:
        page: A parsed Scopus search response.

    Returns:
        The number of entries, or ``None`` if the page does not have the
        expected shape. Must never raise -- it exists purely for an
        observability log line (BUILD_PLAN §3.4); real shape validation of
        the page happens via
        :func:`~prismabib.sources.scopus.extract_total_results`.
    """
    results = page.get("search-results")
    if not isinstance(results, dict):
        return None
    entries = results.get("entry")
    return len(entries) if isinstance(entries, list) else None


def capture_search(project: Project, *, query: str | None = None) -> RunManifest:
    """Run (or resume) one Scopus acquisition and seal it into a Layer 0 run directory.

    Fetches every page of ``query`` via :class:`~prismabib.sources.scopus.ScopusClient`
    (always ``view=COMPLETE``, per BUILD_PLAN line 763 -- never degrades),
    writing each as ``raw/<run_id>/page-NNNN.jsonl`` and finishing with
    ``raw/<run_id>/manifest.json`` (S02-AC1). See the module docstring for
    exactly how verbatim pre-parse persistence, sealing, and resumption fit
    together.

    Args:
        project: The project to acquire into; results are written under
            ``project.raw_dir``.
        query: The Scopus Boolean query string to run. When ``None``
            (the default), it is built from ``project``'s ``project.toml``
            ``[query]`` table via
            :func:`~prismabib.query.build_query_for_project`.

    Returns:
        The sealed run's :class:`~prismabib.capture.manifest.RunManifest`.

    Raises:
        ConfigError: If ``query`` is ``None`` and ``project.toml`` cannot
            be read or parsed (see
            :func:`~prismabib.query.build_query_for_project`), or if
            ``project.criteria`` cannot be read.
        ValidationError: If ``query`` is ``None`` and the ``[query]`` table
            has nothing to search for; if a fetched page is not a
            well-formed Scopus search response (BUILD_PLAN line 821 --
            every page file already written for this run is left
            untouched); or if the search yields no pages at all, so no
            ``total_results`` could ever be determined.
        AuthError: On HTTP 401 from Scopus; never retried.
        EntitlementError: On HTTP 403 for ``view=COMPLETE``; never retried,
            never degrades to ``STANDARD`` (BUILD_PLAN §5 risk 1).
        QuotaExceededError: On HTTP 429 indicating the weekly quota is
            exhausted.
        RateLimitError: On HTTP 429 exhausting the retry budget.
        UpstreamError: On HTTP 5xx exhausting the retry budget.
        SealedRunError: Only if an internal invariant is violated (a
            resumed run directory turns out to already be sealed); should
            never occur through this function's own code paths, since
            :func:`_find_resumable_run` never selects a sealed directory.
    """
    resolved_query = query if query is not None else build_query_for_project(project)
    view = "COMPLETE"
    endpoint = ScopusClient.SEARCH_ENDPOINT

    raw_dir = project.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)

    resumed_dir = _find_resumable_run(raw_dir, query=resolved_query, view=view, endpoint=endpoint)
    if resumed_dir is not None:
        run_dir = resumed_dir
        run_id = run_dir.name
        state = _load_cursor_state(run_dir / _CURSOR_FILENAME)
        started_at = state.started_at
        payload_files = list(state.payload_files)
        resume_cursor = state.next_cursor
        logger.info(
            "capture.run_resumed",
            run_id=run_id,
            endpoint=endpoint,
            pages_already_written=len(payload_files),
            resuming_from_cursor=resume_cursor is not None,
        )
    else:
        run_id = _new_run_id()
        run_dir = raw_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now(UTC)
        payload_files = []
        resume_cursor = None
        _save_cursor_state(
            run_dir,
            _CursorState(
                query=resolved_query,
                view=view,
                endpoint=endpoint,
                started_at=started_at,
                payload_files=payload_files,
            ),
        )
        logger.info("capture.run_started", run_id=run_id, endpoint=endpoint, query=resolved_query)

    resume_from = len(payload_files)
    settings = Settings()
    cache = HttpCache(raw_dir / _CACHE_DIRNAME)
    total_results: int | None = None

    # A persisted cursor resumes AT the first unwritten page, so nothing already
    # in Layer 0 is requested again. Without one (a run interrupted before its
    # first page landed, or a pre-existing sidecar) fall back to replaying from
    # the start and skipping by index -- correct, but it costs quota on a cold
    # cache, which is exactly what the persisted cursor exists to avoid.
    start_cursor = resume_cursor if resume_cursor is not None else "*"
    index_offset = resume_from if resume_cursor is not None else 0

    with ScopusClient(settings, cache=cache) as client:
        pages = client.search(resolved_query, view=view, start_cursor=start_cursor)
        for position, page in enumerate(pages):
            index = index_offset + position

            # Every page carries opensearch:totalResults, not just page 0 -- so a
            # resumed run still records it (S02-AC5) without re-fetching page 0.
            if total_results is None:
                total_results = extract_total_results(page)

            if index < resume_from:
                # Only reachable on the replay-from-start fallback above: this page
                # was already durably written by a prior attempt. Walking past it
                # touches no file and creates no duplicate.
                continue

            filename = f"page-{index:04d}.jsonl"
            _write_page(run_dir, filename, page)
            payload_files.append(filename)
            _save_cursor_state(
                run_dir,
                _CursorState(
                    query=resolved_query,
                    view=view,
                    endpoint=endpoint,
                    started_at=started_at,
                    payload_files=payload_files,
                    next_cursor=extract_next_cursor(page),
                ),
            )
            logger.info(
                "capture.page_written",
                run_id=run_id,
                endpoint=endpoint,
                page_index=index,
                result_count=_entry_count(page),
                total_results=total_results,
            )

    if total_results is None:
        raise ValidationError(
            f"Scopus search for query={resolved_query!r} yielded no pages at all; "
            "cannot determine total_results (BUILD_PLAN S02-AC5)."
        )

    finished_at = datetime.now(UTC)
    payload_sha256 = _payload_sha256(run_dir, payload_files)
    manifest = RunManifest(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        endpoint=endpoint,
        query=resolved_query,
        view=view,
        total_results=total_results,
        pages_fetched=len(payload_files),
        payload_files=payload_files,
        payload_sha256=payload_sha256,
        client_version=_CLIENT_VERSION,
        criteria_version=project.criteria.version,
    )

    _guard_writable(run_dir)
    _atomic_write_bytes(
        run_dir / _MANIFEST_FILENAME,
        manifest.model_dump_json(indent=2).encode("utf-8"),
    )

    cursor_path = run_dir / _CURSOR_FILENAME
    if cursor_path.is_file():
        # Not part of the sealed payload (see module docstring) -- removed
        # once sealing succeeds, since there is nothing left to resume.
        cursor_path.unlink()

    logger.info(
        "capture.run_sealed",
        run_id=run_id,
        endpoint=endpoint,
        total_results=total_results,
        pages_fetched=len(payload_files),
        payload_sha256=payload_sha256,
    )
    return manifest


__all__ = ["SealedRunError", "capture_search", "is_sealed"]
