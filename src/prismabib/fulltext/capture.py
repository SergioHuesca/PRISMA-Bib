"""Seal the Stage 6 resolver chain's output into a Layer 0 run (ADR 0019, Decision 0).

**Why this module exists.** Full-text resolution spends real quota (Elsevier
Article Retrieval calls, Unpaywall lookups and downloads) and observes real
facts about the world ("we were refused this record", "no open-access copy
exists") that must not vanish the moment someone runs
``prismabib build --rebuild`` -- the routine, documented, recommended command
whose own output says "deleting it and running this again loses nothing"
(S03-AC3). So, exactly like :func:`prismabib.capture.writer.capture_search` and
:func:`prismabib.capture.enrich.capture_abstracts`, this module is a **capture**:
it writes Layer 0, seals it, and is the only place bytes obtained by the
resolver chain (:mod:`prismabib.fulltext.resolve`) ever touch a disk.

**On-disk shape.**

.. code-block:: text

    projects/<slug>/fulltext/runs/<run_id>/
    |-- attempts.jsonl   # one JSON object per resolver attempt, in order made
    |-- assets/          # fetched bytes, named by their own SHA-256 digest
    `-- manifest.json    # the seal

Nested under ``project.fulltext_dir``, not ``raw/``: fetched full text is
licensed publisher content and BUILD_PLAN/ADR 0019 both require it stay out of
the Layer 0 archive proper. ``fulltext/manual/<record_id>.pdf`` (BUILD_PLAN,
:data:`~prismabib.fulltext.resolve.MANUAL_DROP_DIRNAME`) is a sibling of
``fulltext/runs/``, not inside it -- an operator drop-box, not a run; a run
that consumes one *copies* its bytes into its own sealed ``assets/`` rather
than recording a reference to that mutable path (see
:class:`~prismabib.fulltext.resolve.ManualDropResolver`), so a later edit or
deletion at the drop-box cannot retroactively change what an already-sealed
run says it observed.

**``attempts.jsonl``, one JSON object per line, in the order attempts were
made:**

.. code-block:: json

    {"record_id": "scopus:2-s2.0-...", "resolver_name": "sciencedirect",
     "media_type": "xml", "asset_file": "assets/<sha256>.xml",
     "retrieved_at": "2026-09-02T12:00:00+00:00", "entitled": true}

``asset_file`` (a path relative to the run directory) and ``media_type`` are
both ``null`` for an attempt that produced no asset -- a refusal
(``entitled: false``) or a miss (``entitled: null``) -- exactly ADR 0019's
three-valued ``entitled`` column, now on disk rather than in a Layer 1 row
written directly by this module.

**Durability granularity: per record, not per batch.** Unlike
:mod:`prismabib.capture.enrich`'s ``BATCH_SIZE``-grouped payload files (chosen
so a byte-identical resumed run's files match an uninterrupted one's --
BUILD_PLAN's Stage 3 reproducibility argument, which does not apply here: no
project has full-text goldens, ADR 0019 consequence 5), this module rewrites
``attempts.jsonl`` in full after **every** record and updates ``progress.json``
to match, immediately. That trades the throughput of batched writes for the
stronger guarantee ADR 0019's hard rule 1 actually needs: a process killed
between any two records loses at most the one record in flight, never a whole
in-progress batch of already-paid-for Elsevier or Unpaywall calls. For the
corpus sizes this stage runs over (``M_abs``, typically hundreds of records,
never Stage 2's full search result count), rewriting a few-hundred-line text
file once per record costs microseconds and is not a bottleneck; it would not
scale to Stage 2's page-fetch volumes, which is exactly why that module does
not do it this way.

**Resumption is seal-based, like every other Layer 0 writer.** A record with a
resolved (``asset_file`` non-null) row in *any* sealed run under
``fulltext/runs/`` is never a member of a later run's target set at all
(:func:`already_resolved_record_ids`) -- quota already spent to resolve it is
never spent again. An *unresolved* record's most recent outcome does not stop
it from being attempted again by a later invocation: a refusal or a miss may
no longer hold (a fresh institutional token, a newly-dropped manual PDF), and
nothing here can tell without asking. Within one still-open run, a resumed
call picks up a matching unsealed run directory by its target-set digest
(:func:`_find_resumable_run`), exactly the pattern
:func:`prismabib.capture.enrich._find_resumable_run` already established.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import structlog
from pydantic import BaseModel, ConfigDict

from prismabib import __version__ as _CLIENT_VERSION
from prismabib.capture.layout import (
    RUN_MANIFEST_FILENAME,
    atomic_write_bytes,
    guard_writable,
    is_sealed,
    new_run_id,
)
from prismabib.capture.manifest import FullTextRunManifest
from prismabib.fulltext.resolve import (
    FullTextAttempt,
    FullTextResolutionError,
    FullTextResolver,
    resolve_fulltext,
)

if TYPE_CHECKING:
    from prismabib.project import Project

logger = structlog.get_logger(__name__)

#: Every full-text resolution run lives under ``project.fulltext_dir / RUNS_DIRNAME``,
#: a sibling of :data:`~prismabib.fulltext.resolve.MANUAL_DROP_DIRNAME` -- the two
#: never collide by name (``"runs"`` vs. ``"manual"``), so a scan for one can never
#: mistake the other for a run.
RUNS_DIRNAME = "runs"

#: One run's fetched bytes, named by their own SHA-256 digest -- content-addressed,
#: so re-resolving to identical bytes (a resumed call re-fetching after an
#: interruption, a warm HTTP cache) writes the same file twice rather than growing
#: the run without bound.
ASSETS_DIRNAME = "assets"

#: One JSON object per resolver attempt, in the order attempts were made. See the
#: module docstring.
ATTEMPTS_FILENAME = "attempts.jsonl"

#: The unsealed run's resumption sidecar. Deleted on seal; never hashed -- same
#: convention as :data:`prismabib.capture.enrich.PROGRESS_FILENAME`.
PROGRESS_FILENAME = "progress.json"


class _FullTextProgress(BaseModel):
    """The resumable ``progress.json`` sidecar for one in-progress full-text run.

    Kept deliberately separate from :class:`~prismabib.capture.manifest.FullTextRunManifest`
    and never part of ``attempts_sha256`` -- the same split, for the same reasons,
    as ``progress.json`` versus ``AbstractRunManifest`` on the abstracts side.
    """

    started_at: datetime

    records_digest: str
    """SHA-256 over the sorted, not-already-resolved record ids this run was asked
    to attempt. The resume key: a later call whose pending set has changed (a
    record resolved by some other means, a different explicit ``record_ids``) does
    not resume this directory -- see :func:`_find_resumable_run`."""

    pending_record_ids: list[str]
    """The exact, sorted target list this run was created for. Fixed at run
    creation and never recomputed on resume, so ``records_done`` (an index into
    this list) stays meaningful across calls."""

    records_done: int = 0
    """How many of ``pending_record_ids``, from the front, have been durably
    attempted and written to ``attempts.jsonl`` -- the resume point."""

    resolved_by_resolver: dict[str, int] = {}
    refused_by_resolver: dict[str, int] = {}
    unresolved_record_ids: list[str] = []
    failed_record_ids: list[str] = []


class FullTextCaptureOutcome(BaseModel):
    """What one :func:`capture_fulltext` call did, for
    :func:`prismabib.fulltext.run.run_fulltext_resolution` to report.

    Distinct from :class:`~prismabib.capture.manifest.FullTextRunManifest`
    (which is cumulative over the whole run's lifetime, and is only written to
    disk once the run seals): the fields below are scoped to *this call*,
    matching what BUILD_PLAN's "resumable" contract promises a caller --
    ``attempted``/``resolved``/etc. never double-count work an earlier call
    into the same run already reported.

    Attributes:
        manifest: The run's manifest as it stands after this call -- written
            to disk only when the run finishes (``sealed``); returned either
            way, mirroring :func:`prismabib.capture.enrich.capture_abstracts`.
        sealed: Whether this call finished the run (every pending record
            attempted or exhausted, ``manifest.json`` written) or left it
            unsealed because ``budget`` stopped it short.
        attempted: How many records this call attempted (bounded by
            ``budget``); ``0`` when every pending record was already covered
            by an earlier call into the same run, or when there was nothing
            pending at all.
        resolved: How many of ``attempted`` obtained an asset this call.
        resolved_by_resolver: ``resolved``, broken down by resolver.
        refused_by_resolver: How many entitlement refusals each resolver
            produced this call.
        unresolved_record_ids: Records this call attempted whose chain was
            exhausted with no asset and no unhandled failure.
        failed_record_ids: Records this call attempted for which a resolver
            raised something other than an entitlement refusal partway
            through the chain (see
            :class:`~prismabib.fulltext.resolve.FullTextResolutionError`).
    """

    model_config = ConfigDict(frozen=True)

    manifest: FullTextRunManifest
    sealed: bool
    attempted: int
    resolved: int
    resolved_by_resolver: dict[str, int]
    refused_by_resolver: dict[str, int]
    unresolved_record_ids: tuple[str, ...]
    failed_record_ids: tuple[str, ...]


def _records_digest(record_ids: Sequence[str]) -> str:
    """Hash the exact, sorted record set a run was created to attempt.

    Args:
        record_ids: The record ids, already sorted and deduplicated.

    Returns:
        A hex SHA-256 over the newline-joined ids -- stable across processes
        and machines, matching :func:`prismabib.capture.enrich._records_digest`.
    """
    digest = hashlib.sha256()
    for record_id in record_ids:
        digest.update(record_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def sealed_fulltext_run_dirs(fulltext_dir: Path) -> list[Path]:
    """List every sealed Layer 0 full-text run directory, oldest first.

    Args:
        fulltext_dir: A project's ``fulltext/`` directory (``project.fulltext_dir``).

    Returns:
        Sealed run directories under ``fulltext_dir / "runs"`` (those carrying
        ``manifest.json`` -- :func:`~prismabib.capture.layout.is_sealed` answers
        the question here exactly as it does for a search or abstract run),
        sorted by directory name, which sorts chronologically by construction
        (:func:`~prismabib.capture.layout.new_run_id`). An unsealed
        (in-progress or interrupted) run is skipped. ``[]`` if
        ``fulltext_dir / "runs"`` does not exist.
    """
    runs_root = fulltext_dir / RUNS_DIRNAME
    if not runs_root.is_dir():
        return []
    candidates = [entry for entry in runs_root.iterdir() if entry.is_dir() and is_sealed(entry)]
    return sorted(candidates, key=lambda path: path.name)


def _iter_attempt_rows(attempts_path: Path) -> list[dict[str, Any]]:
    """Parse every JSON line of one run's ``attempts.jsonl``.

    Args:
        attempts_path: Path to a run's ``attempts.jsonl``.

    Returns:
        One dict per non-blank line, in file order. ``[]`` if the file does
        not exist (an interrupted run that never durably wrote a first
        record, or a resumed-but-corrupted directory this function chooses
        not to fail on).
    """
    if not attempts_path.is_file():
        return []
    rows: list[dict[str, Any]] = []
    with attempts_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            # Same tolerance as the Layer 1 loader: a damaged line must not
            # break resumption and force the whole run to be re-paid for.
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                rows.append(parsed)
    return rows


def already_resolved_record_ids(fulltext_dir: Path) -> frozenset[str]:
    """Record ids with a resolved attempt in some *sealed* full-text run.

    Args:
        fulltext_dir: A project's ``fulltext/`` directory.

    Returns:
        Every ``record_id`` for which at least one sealed run's
        ``attempts.jsonl`` carries an attempt with a non-null ``asset_file`` --
        the resumption set BUILD_PLAN's "resumable" requirement needs: quota
        already spent resolving a record is never spent again. Reads only
        *sealed* runs -- an unsealed run's in-progress work is not yet a fact
        this function commits to, the same discipline
        :func:`prismabib.store.load._sealed_run_dirs` applies to search runs.
    """
    resolved: set[str] = set()
    for run_dir in sealed_fulltext_run_dirs(fulltext_dir):
        for row in _iter_attempt_rows(run_dir / ATTEMPTS_FILENAME):
            if row.get("asset_file") is not None:
                record_id = row.get("record_id")
                if isinstance(record_id, str) and record_id:
                    resolved.add(record_id)
    return frozenset(resolved)


def _load_progress(progress_path: Path) -> _FullTextProgress:
    """Parse a ``progress.json`` sidecar.

    Args:
        progress_path: Path to the ``progress.json`` file.

    Returns:
        The parsed :class:`_FullTextProgress`.

    Raises:
        OSError: If ``progress_path`` cannot be read.
        ValueError: If its contents are not valid JSON matching the expected
            schema.
    """
    return _FullTextProgress.model_validate_json(progress_path.read_text(encoding="utf-8"))


def _save_progress(run_dir: Path, state: _FullTextProgress) -> None:
    """Persist ``progress.json`` for an in-progress run, guarding against a sealed run.

    Args:
        run_dir: The full-text run directory.
        state: The resumption state to write.

    Raises:
        SealedRunError: If ``run_dir`` is already sealed.
    """
    guard_writable(run_dir)
    atomic_write_bytes(
        run_dir / PROGRESS_FILENAME,
        state.model_dump_json(indent=2).encode("utf-8") + b"\n",
    )


def _find_resumable_run(runs_root: Path, *, records_digest: str) -> Path | None:
    """Find an unsealed full-text run whose target set matches exactly.

    Args:
        runs_root: The project's ``fulltext/runs/`` directory.
        records_digest: :func:`_records_digest` of the pending record set this
            call was asked to attempt.

    Returns:
        The most recent (by run id, which sorts by time) unsealed run
        directory whose ``progress.json`` matches, or ``None`` -- including
        when ``runs_root`` does not exist yet, or a candidate is sealed
        (never resumed) or carries an unreadable sidecar (left alone, never
        deleted).
    """
    if not runs_root.is_dir():
        return None

    candidates: list[Path] = []
    for entry in runs_root.iterdir():
        if not entry.is_dir() or is_sealed(entry):
            continue
        progress_path = entry / PROGRESS_FILENAME
        if not progress_path.is_file():
            continue
        try:
            state = _load_progress(progress_path)
        except (OSError, ValueError):
            continue
        if state.records_digest == records_digest:
            candidates.append(entry)

    if not candidates:
        return None
    return max(candidates, key=lambda path: path.name)


def _write_asset(run_dir: Path, *, content: bytes, media_type: str) -> str:
    """Write one attempt's fetched bytes into the run's content-addressed ``assets/``.

    Args:
        run_dir: The full-text run directory.
        content: The raw bytes to write.
        media_type: ``"xml"`` or ``"pdf"`` -- determines the file extension.

    Returns:
        The asset's path, relative to ``run_dir`` (e.g. ``"assets/<sha>.pdf"``),
        exactly what is stored as ``attempts.jsonl``'s ``asset_file``.
    """
    digest = hashlib.sha256(content).hexdigest()
    extension = "xml" if media_type == "xml" else "pdf"
    relative = f"{ASSETS_DIRNAME}/{digest}.{extension}"
    guard_writable(run_dir)
    atomic_write_bytes(run_dir / relative, content)
    return relative


def _serialise_attempt(attempt: FullTextAttempt, *, asset_file: str | None) -> str:
    """Render one :class:`~prismabib.fulltext.resolve.FullTextAttempt` as one JSON line.

    Args:
        attempt: The attempt to serialise. Its own ``content`` is not written
            here -- ``asset_file`` (already placed by :func:`_write_asset`, or
            ``None``) is what this line records instead.
        asset_file: Where this attempt's bytes were written (relative to the
            run directory), or ``None`` for an attempt with no asset.

    Returns:
        One compact, canonically-encoded JSON object, no trailing newline.
    """
    return json.dumps(
        {
            "record_id": attempt.record_id,
            "resolver_name": attempt.resolver_name,
            "media_type": attempt.media_type,
            "asset_file": asset_file,
            "retrieved_at": attempt.retrieved_at.astimezone(UTC).isoformat(),
            "entitled": attempt.entitled,
        },
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _attempts_sha256(attempts_path: Path) -> str:
    """Hash ``attempts.jsonl``'s current bytes.

    Args:
        attempts_path: Path to the run's ``attempts.jsonl``.

    Returns:
        The hex SHA-256 digest, or the digest of the empty string if the file
        does not exist (a run that attempted nothing at all -- unreachable
        through :func:`capture_fulltext`, which is never called with an empty
        pending set, but kept total rather than partial).
    """
    content = attempts_path.read_bytes() if attempts_path.is_file() else b""
    return hashlib.sha256(content).hexdigest()


def capture_fulltext(
    project: Project,
    *,
    pending_ids: Sequence[str],
    doi_by_record_id: Mapping[str, str | None],
    resolvers: Sequence[FullTextResolver],
    budget: int | None = None,
) -> FullTextCaptureOutcome:
    """Run (or resume) the resolver chain over ``pending_ids`` and seal a Layer 0 run.

    Args:
        project: The project to capture full text for; the run is written
            under ``project.fulltext_dir / "runs"``.
        pending_ids: The **not-already-resolved** record ids this run should
            attempt, in any order (sorted and deduplicated here). Callers
            (:func:`prismabib.fulltext.run.run_fulltext_resolution`) filter out
            anything :func:`already_resolved_record_ids` already covers
            *before* calling this -- this function has no opinion about
            "already resolved" and simply attempts everything it is given.
            Must be non-empty; an empty pending set is a no-op the caller is
            expected to short-circuit around, not something this function
            handles specially.
        doi_by_record_id: Every targeted record's DOI (``None`` for a record
            with none), read from Layer 1 by the caller. This function never
            opens a database connection of its own -- see the module
            docstring and ADR 0019 Decision 0.
        resolvers: The chain to run, in order (see
            :func:`~prismabib.fulltext.resolve.default_chain`).
        budget: The maximum number of records this call will attempt.
            ``None`` for no limit.

    Returns:
        A :class:`FullTextCaptureOutcome` describing this call's contribution.

    Raises:
        SealedRunError: Only if an internal invariant is violated (a resumed
            run directory turns out to already be sealed); unreachable through
            this function's own code paths.
    """
    sorted_pending = sorted(set(pending_ids))
    fulltext_dir = project.fulltext_dir
    runs_root = fulltext_dir / RUNS_DIRNAME
    runs_root.mkdir(parents=True, exist_ok=True)

    digest = _records_digest(sorted_pending)
    resumed_dir = _find_resumable_run(runs_root, records_digest=digest)
    if resumed_dir is not None:
        run_dir = resumed_dir
        state = _load_progress(run_dir / PROGRESS_FILENAME)
        logger.info(
            "fulltext.capture.run_resumed",
            run_id=run_dir.name,
            records_requested=len(state.pending_record_ids),
            records_already_done=state.records_done,
        )
    else:
        run_dir = runs_root / new_run_id()
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / ASSETS_DIRNAME).mkdir(parents=True, exist_ok=True)
        state = _FullTextProgress(
            started_at=datetime.now(UTC),
            records_digest=digest,
            pending_record_ids=sorted_pending,
        )
        _save_progress(run_dir, state)
        logger.info(
            "fulltext.capture.run_started",
            run_id=run_dir.name,
            records_requested=len(sorted_pending),
        )

    run_id = run_dir.name
    attempts_path = run_dir / ATTEMPTS_FILENAME
    attempts_lines = [
        json.dumps(row, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        for row in _iter_attempt_rows(attempts_path)
    ]

    call_attempted = 0
    call_resolved_by_resolver: dict[str, int] = {}
    call_refused_by_resolver: dict[str, int] = {}
    call_unresolved: list[str] = []
    call_failed: list[str] = []

    while state.records_done < len(state.pending_record_ids):
        if budget is not None and call_attempted >= budget:
            break

        record_id = state.pending_record_ids[state.records_done]
        call_attempted += 1
        doi = doi_by_record_id.get(record_id)

        try:
            asset, attempts = resolve_fulltext(record_id=record_id, doi=doi, resolvers=resolvers)
        except FullTextResolutionError as exc:
            asset = None
            attempts = exc.attempts
            outcome_is_failure = True
        else:
            outcome_is_failure = False

        for attempt in attempts:
            asset_file = (
                _write_asset(run_dir, content=attempt.content, media_type=attempt.media_type)
                if attempt.content is not None and attempt.media_type is not None
                else None
            )
            attempts_lines.append(_serialise_attempt(attempt, asset_file=asset_file))
            if attempt.entitled is False:
                state.refused_by_resolver[attempt.resolver_name] = (
                    state.refused_by_resolver.get(attempt.resolver_name, 0) + 1
                )
                call_refused_by_resolver[attempt.resolver_name] = (
                    call_refused_by_resolver.get(attempt.resolver_name, 0) + 1
                )

        guard_writable(run_dir)
        body = ("\n".join(attempts_lines) + "\n").encode("utf-8") if attempts_lines else b""
        atomic_write_bytes(attempts_path, body)

        if outcome_is_failure:
            state.failed_record_ids.append(record_id)
            call_failed.append(record_id)
            logger.warning("fulltext.capture.record_failed", run_id=run_id, record_id=record_id)
        elif asset is None:
            state.unresolved_record_ids.append(record_id)
            call_unresolved.append(record_id)
        else:
            state.resolved_by_resolver[asset.resolver_name] = (
                state.resolved_by_resolver.get(asset.resolver_name, 0) + 1
            )
            call_resolved_by_resolver[asset.resolver_name] = (
                call_resolved_by_resolver.get(asset.resolver_name, 0) + 1
            )

        state.records_done += 1
        _save_progress(run_dir, state)

    sealed = state.records_done >= len(state.pending_record_ids)
    manifest = FullTextRunManifest(
        run_id=run_id,
        started_at=state.started_at,
        finished_at=datetime.now(UTC),
        records_requested=len(state.pending_record_ids),
        records_attempted=call_attempted,
        records_resolved=sum(state.resolved_by_resolver.values()),
        resolved_by_resolver=dict(state.resolved_by_resolver),
        refused_by_resolver=dict(state.refused_by_resolver),
        unresolved_record_ids=list(state.unresolved_record_ids),
        failed_record_ids=list(state.failed_record_ids),
        attempts_file=ATTEMPTS_FILENAME,
        attempts_sha256=_attempts_sha256(attempts_path),
        client_version=_CLIENT_VERSION,
        criteria_version=project.criteria.version,
    )

    if sealed:
        guard_writable(run_dir)
        atomic_write_bytes(
            run_dir / RUN_MANIFEST_FILENAME,
            manifest.model_dump_json(indent=2).encode("utf-8") + b"\n",
        )
        progress_path = run_dir / PROGRESS_FILENAME
        if progress_path.is_file():
            progress_path.unlink()
        logger.info(
            "fulltext.capture.run_sealed",
            run_id=run_id,
            records_requested=manifest.records_requested,
            records_resolved=manifest.records_resolved,
            attempts_sha256=manifest.attempts_sha256,
        )
    else:
        logger.info(
            "fulltext.capture.budget_exhausted",
            run_id=run_id,
            budget=budget,
            records_done=state.records_done,
            records_requested=len(state.pending_record_ids),
        )

    return FullTextCaptureOutcome(
        manifest=manifest,
        sealed=sealed,
        attempted=call_attempted,
        resolved=sum(call_resolved_by_resolver.values()),
        resolved_by_resolver=call_resolved_by_resolver,
        refused_by_resolver=call_refused_by_resolver,
        unresolved_record_ids=tuple(call_unresolved),
        failed_record_ids=tuple(call_failed),
    )


__all__ = [
    "ASSETS_DIRNAME",
    "ATTEMPTS_FILENAME",
    "PROGRESS_FILENAME",
    "RUNS_DIRNAME",
    "FullTextCaptureOutcome",
    "already_resolved_record_ids",
    "capture_fulltext",
    "sealed_fulltext_run_dirs",
]
