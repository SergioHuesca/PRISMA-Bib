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
  the manifest itself) first calls :func:`~prismabib.capture.layout.guard_writable`, which raises
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
``run_id``) rather than attempting -- and having :func:`~prismabib.capture.layout.guard_writable`
refuse -- to write into the old one. Resumption continues from the cursor
``cursor.json`` recorded alongside the last page actually written, passed to
:meth:`ScopusClient.search` as ``start_cursor``, so pages already in Layer 0
are never requested again. Relying on ``raw/_cache/`` for that instead is not
equivalent: the cache is gitignored and disposable, so on a cold cache a run
interrupted at page 40 of 71 would re-request all 40 against a weekly quota
(BUILD_PLAN line 768; §5 risk 2). Only a sidecar written before the cursor
was ever stored falls back to replaying from ``cursor=*`` and skipping by
index. A page that fails to parse (BUILD_PLAN line 821) raises
out of ``ScopusClient.search`` *before* this module ever attempts to create
that page's file, so every page file already written for that run is left
completely untouched -- there is no delete, no truncate, and no rewrite of
prior pages anywhere in this module's write path.

**On-disk shape of a page.** Each page is written as *two* files:
``page-NNNN.jsonl`` holding one Scopus entry per line, and
``page-NNNN.meta.json`` holding the response envelope with ``entry`` removed
(``opensearch:totalResults``, ``cursor``, ``link``, ...). Together they
reconstruct the response exactly. The split exists so a record's line index
identifies *that record*: the store carries ``payload_file``/``payload_line``
per record (BUILD_PLAN line 856) and :class:`~prismabib.models.PayloadRef` is
specified as "Layer 0 file + line offset" (line 696). Writing the whole
envelope as one line -- which this module originally did -- pinned
``payload_line`` at ``0`` for every record, so the offset addressed the page
and never the record, and per-record provenance silently did not exist.

**On ``total_results``.** Recorded from the first page this attempt sees --
every page carries ``opensearch:totalResults``, so a run resumed mid-way
records it without re-fetching page 0. It is read from the server's own
``opensearch:totalResults`` via
:func:`~prismabib.sources.scopus.extract_total_results` and is the **only**
value written to :class:`~prismabib.capture.manifest.RunManifest.total_results`
-- BUILD_PLAN S02-AC5 requires this be the sole source of the PRISMA
"records identified" count; nothing here derives it from
``len(payload_files)`` or a count of parsed entries.

**Where the shared vocabulary lives.** ``manifest.json`` as the seal,
``_cache`` as a non-run directory, the sealed-write guard, the atomic write,
and the run-id format are not this module's private business -- they are the
Layer 0 contract, and :mod:`prismabib.capture.enrich` and
:mod:`prismabib.store.load` have to agree with them exactly. They live in
:mod:`prismabib.capture.layout`; this module imports them and re-exports
:func:`~prismabib.capture.layout.is_sealed` and
:class:`~prismabib.capture.layout.SealedRunError` so that its own public
surface is unchanged.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import structlog
from pydantic import BaseModel

from prismabib import __version__ as _CLIENT_VERSION
from prismabib.capture.layout import (
    CACHE_DIRNAME,
    NON_RUN_DIRNAMES,
    RUN_MANIFEST_FILENAME,
    SealedRunError,
    atomic_write_bytes,
    guard_writable,
    is_sealed,
    new_run_id,
)
from prismabib.capture.manifest import RunManifest
from prismabib.config import Settings
from prismabib.errors import ValidationError
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

_META_SUFFIX = ".meta.json"
_CURSOR_FILENAME = "cursor.json"


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
    guard_writable(run_dir)
    atomic_write_bytes(
        run_dir / _CURSOR_FILENAME,
        state.model_dump_json(indent=2).encode("utf-8") + b"\n",
    )


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
        entry is one of the non-run directories under ``raw/``
        (:data:`~prismabib.capture.layout.NON_RUN_DIRNAMES` -- the shared
        HTTP cache and the abstract-enrichment tree, neither of which is
        ever a search run directory) or a sealed run (never resumed; see
        the module docstring).
    """
    if not raw_dir.is_dir():
        return None

    candidates: list[Path] = []
    for entry in raw_dir.iterdir():
        if not entry.is_dir() or entry.name in NON_RUN_DIRNAMES:
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
    """Write one page as true JSON Lines -- one record per line -- plus its envelope.

    The page is split into two files:

    - ``page-NNNN.jsonl``: one Scopus entry per line, so a record's line index
      identifies *that record*.
    - ``page-NNNN.meta.json``: the response envelope with ``entry`` removed
      (``opensearch:totalResults``, ``cursor``, ``link``, ...), so the original
      response remains reconstructible exactly.

    Why the split. The schema carries ``payload_file``/``payload_line`` per record
    (BUILD_PLAN line 856) and :class:`~prismabib.models.PayloadRef` is specified as
    "Layer 0 file + line offset" (line 696). Writing the whole envelope as a single
    line -- which this function used to do -- made ``payload_line`` always ``0``:
    it addressed the page, never the record, so the offset carried no information
    and per-record provenance did not exist. Stage 3's S03-AC2 ("every
    ``payload_file``/``payload_line`` pair resolves to a valid raw JSON object")
    would still have passed, because line 0 *is* valid JSON -- a green test over
    dead provenance, which is exactly the §1.4 failure this architecture exists to
    prevent.

    Encoding stays canonical (``sort_keys``, compact separators) rather than raw
    bytes: a warm-cache re-run must reproduce a byte-identical ``payload_sha256``
    (S02-AC2), and canonical encoding is what guarantees that independently of
    dict ordering.

    Args:
        run_dir: The run directory.
        filename: The page's filename, e.g. ``"page-0000.jsonl"``.
        page: The parsed Scopus search response for this page.

    Raises:
        SealedRunError: If ``run_dir`` is already sealed.
    """
    guard_writable(run_dir)

    results = page.get("search-results", {})
    entries = results.get("entry", []) if isinstance(results, dict) else []
    if not isinstance(entries, list):
        entries = [entries]

    lines = "".join(
        json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for entry in entries
    )
    atomic_write_bytes(run_dir / filename, lines.encode("utf-8"))

    envelope = {
        "search-results": {
            key: value
            for key, value in (results.items() if isinstance(results, dict) else ())
            if key != "entry"
        }
    }
    meta_name = filename.removesuffix(".jsonl") + _META_SUFFIX
    encoded_meta = (
        json.dumps(envelope, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
    )
    atomic_write_bytes(run_dir / meta_name, encoded_meta.encode("utf-8"))


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
        run_id = new_run_id()
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
    cache = HttpCache(raw_dir / CACHE_DIRNAME)
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

    guard_writable(run_dir)
    atomic_write_bytes(
        run_dir / RUN_MANIFEST_FILENAME,
        manifest.model_dump_json(indent=2).encode("utf-8") + b"\n",
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
