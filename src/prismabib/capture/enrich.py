"""Layer 0 enrichment: fetch Scopus Abstract Retrieval records for a captured corpus.

**Why this module exists at all.** ``criteria.yaml`` has a ``subject_areas``
filter, and until now nothing could evaluate it. The Scopus **Search** API does
not return subject-area codes -- not even at ``view=COMPLETE``, which is the
richest view the plan permits and the only one
:func:`~prismabib.capture.writer.capture_search` ever asks for. Measured
against a real 651-record corpus, **0 of 125 sampled entries carried a
``subject-area`` key**; the pinned half of that measurement is
``test_contract__search_complete_response__carries_no_subject_areas``. The
codes live in a different API -- Abstract Retrieval, one call per record -- and
this module is the run that fetches them.

That is the whole of this module's job. It writes Layer 0 and stops. Nothing
here loads a store, evaluates a criterion, or changes a published number; the
loader still writes zero ``subject_areas`` rows and
:func:`~prismabib.prisma.engine.automated_set` still refuses a declared subject
filter, exactly as before. A separate change consumes this data. See ADR 0011.

**Why the payloads are Layer 0 and not a cache.** BUILD_PLAN §2.2 requires that
Layer 1 be reconstructible from Layer 0 by running one function. If the
Abstract Retrieval responses lived only in ``raw/_cache/`` -- which is
gitignored, disposable, and documented as such -- then a
``build_store(rebuild=True)`` after a routine cleanup would silently rebuild a
corpus with no subject areas in it, or re-spend ~1,800 API calls against a
weekly quota to get them back. Neither is a rebuild. So the responses are
persisted, verbatim, under ``raw/``.

**Why they get their own run directory.** ``raw/abstracts/<run_id>/`` is nested
one level below the search runs, not a sibling of them, for two independent
reasons:

1. A sealed search run is **immutable** (§2.2). The records these abstracts
   describe were captured by runs that are already sealed; there is no writable
   place inside them, and creating one would mean unsealing, which no operation
   in this codebase does.
2. Anything scanning ``raw/`` for search runs
   (:func:`prismabib.store.load._sealed_run_dirs`,
   :func:`prismabib.capture.writer._find_resumable_run`) would otherwise find
   an abstract run, read its ``manifest.json`` as a
   :class:`~prismabib.capture.manifest.RunManifest`, and try to parse Abstract
   Retrieval payloads as search entries -- dying on a missing
   ``prism:coverDate``. Nesting makes them invisible to those scans, and
   :data:`~prismabib.capture.layout.NON_RUN_DIRNAMES` makes that invisibility a
   stated rule rather than a happy accident of where ``manifest.json`` happens
   to sit.

**Why an abstract run is never a row in ``runs``.** ``runs.total_results`` is
the only sanctioned source of the PRISMA "records identified" count (S02-AC5).
An abstract run identifies nothing -- it re-describes records some search run
already identified -- so a row for it would either need a fabricated
``total_results`` or would double-count real records into the identification
number. :class:`~prismabib.capture.manifest.AbstractRunManifest` therefore has
no ``total_results`` field at all, and this module writes no ``runs`` row.

**On-disk shape.**

.. code-block:: text

    raw/abstracts/<run_id>/
    ├── abstracts-0000.jsonl   # verbatim responses, one per line, 100 records/file
    ├── progress.json          # resumption sidecar; deleted on seal; NEVER hashed
    └── manifest.json          # the seal

Payload lines are the **verbatim** Abstract Retrieval responses -- not wrapped
in a ``{"record_id": ..., "response": ...}`` envelope. Record identity is
already inside the response, at
``abstracts-retrieval-response.coredata`` (``eid``, and ``dc:identifier``),
which is exactly where :func:`prismabib.store.load._record_id_from_entry`
recovers it from for a search entry. An envelope would add a prismabib-shaped
layer over a payload whose entire value is being untouched upstream data, and a
future reader would have to know about it to read the file.

Encoding is canonical (``sort_keys``, compact separators) for the same reason
:func:`prismabib.capture.writer._write_page` does it: ``payload_sha256`` must
be byte-stable across a warm-cache re-run, independently of dict ordering.
Records are iterated in ``sorted(record_id)`` order and batched by **position
in that sorted list**, so an interrupted run resumes to byte-identical files:
``abstracts-0000.jsonl`` always covers ``records[0:100]``, whatever happened
in between.

**Sealing and resumption**, deliberately mirroring
:func:`~prismabib.capture.writer.capture_search` one-for-one: find-or-create a
run directory, load or initialise ``progress.json``, iterate, write a batch,
persist progress, seal. A run directory is unsealed (``progress.json``, no
``manifest.json``) or sealed (``manifest.json``, no ``progress.json``), and
every write goes through :func:`~prismabib.capture.layout.guard_writable`
first. ``progress.json`` never contributes to ``payload_sha256``.

Progress is persisted at **batch boundaries only**, and a partially fetched
batch is never written. That is what keeps payload files byte-identical to an
uninterrupted run's. The alternative -- flushing a short file and appending to
it later -- makes ``abstracts-0000.jsonl`` depend on where the interruption
fell, which is precisely the machine-dependence this project's reproducibility
argument cannot afford.

**What that costs, stated as a quota number.** An interruption resumes without
re-spending quota *only at a batch boundary, or with a warm cache*. Since
:data:`BATCH_SIZE` is 100 and only completed batches are durable, an
interruption in the middle of a batch discards up to **99 records' worth of
already-paid requests**, and they are recoverable only from ``raw/_cache/`` --
which is gitignored, disposable, and therefore no guarantee at all on a
machine where it has been cleaned or was never written. The same arithmetic
applies to ``budget``: a budget that is not a multiple of 100 spends its
remainder on records that are not written and must be requested again, so
``budget=150`` durably advances the run by 100 records, not 150. Passing a
multiple of :data:`BATCH_SIZE` is the way to spend a quota slice and keep all
of it.

**The rate limiter is fresh, not shared.** Scopus quotas are per-API. A
:class:`~prismabib.sources.ratelimit.RateLimiter` carried over from a search
run would arrive with the search API's consumed bucket and its
``X-RateLimit-Reset``, throttling (or, worse, parking) an enrichment run
against a quota it does not draw on. The HTTP cache *is* shared, rooted at the
same ``raw/_cache/``: it is keyed on ``(url, params)``, so the two APIs' entries
cannot collide, and sharing it is what makes a warm-cache re-run free.

**Entitlement, and why the first record of a fresh run is special.** Abstract
Retrieval is a *different* entitlement from Search ``view=COMPLETE``; a key
entitled for one is commonly not entitled for the other, and the failure is a
flat 403 on every record. Discovering that on record 1,800 costs a weekly quota
to learn nothing. So a 403 on the **first record attempted of a run that has
written nothing yet** -- ``records_done == 0`` *and* no earlier attempt in this
invocation -- is treated as a missing Abstract Retrieval entitlement: it
re-raises with a message naming that API specifically and distinguishing it
from a Search entitlement failure, after exactly one call, leaving the run
unsealed. A 403 anywhere else is a per-record embargo and is recorded as
:class:`~prismabib.capture.manifest.AbstractUnavailable` with reason
``"not_entitled"``, and the run continues.

The ``records_done == 0`` half of that condition is not decoration. Scoping the
probe to "first attempt of this *invocation*" instead would re-arm it on every
resume, and a resumed run starts at whatever offset the interruption left --
so an individually embargoed record sitting on a batch boundary would abort a
run with thousands of abstracts already on disk, and abort it with a message
asserting the key lacks an entitlement it demonstrably has. Worse, the run
would then be permanently stuck through ``capture_abstracts(project)``: every
later call re-attempts that same record first, and passing an explicit
``record_ids`` list does not rescue it, because :func:`_records_digest` is the
resume key and a different record set starts a *new* run from record 0.

The cost of the rule as it stands: if the first record in sorted order happens
to be individually embargoed on a fresh run, the run refuses instead of
continuing. That case really does write nothing, so refusing loudly is the
cheap error, and the alternative -- probing a second record to disambiguate --
buys it at the price of making "1,800 wasted calls" reachable again through a
different door.

**The view never degrades.** ``view=FULL`` throughout. ``view=META`` is cheaper
and *does* carry subject areas, which is what makes it tempting; it is refused
for the same reason ``view=STANDARD`` is refused on the search side (§5 risk 1).
A corpus whose subject areas came from two different views is not one filter,
and a partially degraded run is indistinguishable, after the fact, from a clean
one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
from pydantic import BaseModel

from prismabib import __version__ as _CLIENT_VERSION
from prismabib.capture.layout import (
    ABSTRACTS_DIRNAME,
    CACHE_DIRNAME,
    NON_RUN_DIRNAMES,
    RUN_MANIFEST_FILENAME,
    atomic_write_bytes,
    guard_writable,
    is_sealed,
    new_run_id,
)
from prismabib.capture.manifest import AbstractRunManifest, AbstractUnavailable, RunManifest
from prismabib.config import Settings
from prismabib.errors import EntitlementError, ValidationError
from prismabib.project import Project
from prismabib.sources.cache import HttpCache
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.scopus import JsonDict, RecordNotFoundError, ScopusClient

logger = structlog.get_logger(__name__)

PROGRESS_FILENAME = "progress.json"
"""The unsealed run's resumption sidecar. Deleted on seal; never hashed."""

ABSTRACT_VIEW = "FULL"
"""The only view this module ever requests. See the module docstring on ``META``."""

BATCH_SIZE = 100
"""Records per payload file, counted by position in the sorted record list.

Batching by position rather than by "number of successful responses so far" is
what makes ``abstracts-NNNN.jsonl`` a pure function of the record list: file
``N`` always covers ``records[N * BATCH_SIZE : (N + 1) * BATCH_SIZE]``,
however many of those Scopus actually served, and whatever happened to the run
in between.
"""

_PAYLOAD_TEMPLATE = "abstracts-{index:04d}.jsonl"


class _AbstractProgress(BaseModel):
    """The resumable ``progress.json`` sidecar for one in-progress abstract run.

    Kept deliberately separate from
    :class:`~prismabib.capture.manifest.AbstractRunManifest` and never part of
    ``payload_sha256`` -- the same split, for the same reasons, as
    ``cursor.json`` versus ``RunManifest`` on the search side.
    """

    endpoint: str
    view: str
    started_at: datetime
    source_run_ids: list[str]

    records_digest: str
    """SHA-256 over the sorted record ids this run was asked to fetch.

    The resume key. Matching on the digest rather than on the list itself keeps
    the sidecar small for a 1,800-record corpus while still refusing to resume
    a run whose record set has changed -- which would otherwise silently
    produce payload files covering two different corpora at different offsets.
    """

    records_requested: int

    missing_source_payload_files: list[str] = []
    """Layer 0 page files a search run's seal names that were not on disk.

    Carried in the sidecar, not just recomputed, for the same reason
    ``source_run_ids`` is: on a resume the seal must report what the run
    actually enriched, and the record set was resolved once, at the start.
    Defaulted so that a ``progress.json`` written before this field existed
    still parses rather than stranding an unsealed run.
    """

    records_done: int
    """How far into the sorted record list is durably covered by ``payload_files``.

    Always a multiple of :data:`BATCH_SIZE` except at the end of the list,
    because a partial batch is never written. This is the resume point.
    """

    records_fetched: int
    payload_files: list[str] = []
    unavailable: list[AbstractUnavailable] = []


def _records_digest(record_ids: Sequence[str]) -> str:
    """Hash the exact record set a run was asked to fetch.

    Args:
        record_ids: The record ids, already sorted and deduplicated.

    Returns:
        A hex SHA-256 over the newline-joined ids -- stable across processes
        and machines, which is what a resume key has to be.
    """
    digest = hashlib.sha256()
    for record_id in record_ids:
        digest.update(record_id.encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _sealed_search_run_dirs(raw_dir: Path) -> list[Path]:
    """List every sealed *search* run directory under ``raw_dir``, oldest first.

    Deliberately a local, four-line scan rather than an import of
    :func:`prismabib.store.load._sealed_run_dirs`: this is Layer 0 reading
    Layer 0, and taking it from the Layer 1 loader would make ``capture``
    depend on ``store``, inverting the dependency the whole architecture is
    arranged around. Both use the same
    :data:`~prismabib.capture.layout.NON_RUN_DIRNAMES` and the same
    :func:`~prismabib.capture.layout.is_sealed`, which is the part that has to
    agree.

    Args:
        raw_dir: A project's ``raw/`` directory.

    Returns:
        Sealed search run directories, sorted by run id (chronological by
        construction). ``[]`` if ``raw_dir`` does not exist.
    """
    if not raw_dir.is_dir():
        return []
    candidates = [
        entry
        for entry in raw_dir.iterdir()
        if entry.is_dir() and entry.name not in NON_RUN_DIRNAMES and is_sealed(entry)
    ]
    return sorted(candidates, key=lambda path: path.name)


def _record_ids_from_layer0(raw_dir: Path) -> tuple[list[str], list[str], list[str]]:
    """Collect every record id captured by the sealed search runs under ``raw_dir``.

    Reads each run's ``manifest.json`` for its ``payload_files`` rather than
    globbing, so a page file left behind by an interrupted attempt and not
    named by the seal is never enriched.

    A payload file the seal names but that is not on disk is **skipped, and
    reported**. Skipping is deliberate -- a damaged capture should not cost the
    other 1,799 records their subject areas -- but skipping *silently* is not:
    the Layer 1 loader is not lenient about the same input (``_load_run``
    raises ``FileNotFoundError``), so ``raw/`` copied between machines with one
    ``page-NNNN.jsonl`` missing would make ``build_store`` fail loudly and this
    run seal quietly, with ``records_requested`` reduced,
    ``records_fetched == records_requested`` and ``unavailable == []``. Every
    record from that page would then have no subject areas and no entry saying
    why -- the exact ambiguity
    :class:`~prismabib.capture.manifest.AbstractRunManifest` exists to resolve.
    So the third return value carries the shortfall into the seal.

    Args:
        raw_dir: A project's ``raw/`` directory.

    Returns:
        ``(source_run_ids, record_ids, missing_payload_files)`` -- all three
        sorted; ``record_ids`` deduplicated across runs, since a paper matched
        by two captures is one record and needs one Abstract Retrieval call.
        ``missing_payload_files`` holds ``"<run_id>/<filename>"`` for every
        file a seal names that is absent from disk, and is empty for an intact
        Layer 0.
    """
    source_run_ids: list[str] = []
    record_ids: set[str] = set()
    missing_payload_files: list[str] = []

    for run_dir in _sealed_search_run_dirs(raw_dir):
        manifest = RunManifest.model_validate_json(
            (run_dir / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        source_run_ids.append(manifest.run_id)
        for filename in manifest.payload_files:
            page_path = run_dir / filename
            if not page_path.is_file():
                missing_payload_files.append(f"{manifest.run_id}/{filename}")
                logger.warning(
                    "capture.abstracts.source_payload_missing",
                    run_id=manifest.run_id,
                    payload_file=filename,
                )
                continue
            with page_path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    eid = entry.get("eid") if isinstance(entry, dict) else None
                    if isinstance(eid, str) and eid:
                        record_ids.add(f"scopus:{eid}")

    return sorted(source_run_ids), sorted(record_ids), sorted(missing_payload_files)


def _subject_area_entries(response: Mapping[str, Any]) -> list[Any]:
    """Extract the ``subject-area`` list from one Abstract Retrieval response.

    Scopus emits this field as a list when a record has several areas and as a
    lone mapping when it has exactly one -- the same scalar-vs-list
    inconsistency the search-side fixtures exist to pin -- so both shapes are
    normalised to a list here.

    Args:
        response: One parsed Abstract Retrieval response.

    Returns:
        The subject-area entries, or ``[]`` if the response carries none (or
        does not have the expected envelope at all). Never raises: a response
        this module cannot read subject areas out of is recorded as
        ``"no_subject_areas"``, not as a crash, because the payload itself has
        already been persisted verbatim and stays available to a later reader
        that understands more than this one does.
    """
    retrieval = response.get("abstracts-retrieval-response")
    if not isinstance(retrieval, Mapping):
        return []
    areas = retrieval.get("subject-areas")
    if not isinstance(areas, Mapping):
        return []
    entries = areas.get("subject-area")
    if isinstance(entries, Mapping):
        return [entries]
    if isinstance(entries, list):
        return entries
    return []


def _has_subject_areas(response: Mapping[str, Any]) -> bool:
    """Whether a response carries at least one subject area with a usable code.

    Args:
        response: One parsed Abstract Retrieval response.

    Returns:
        ``True`` if any ``subject-area`` entry has a non-empty ``@code``. An
        entry without a code cannot be matched against ``criteria.yaml``'s
        ``subject_areas`` list, so it is not evidence that the record has
        codes.
    """
    return any(
        isinstance(entry, Mapping) and entry.get("@code")
        for entry in _subject_area_entries(response)
    )


def _load_progress(progress_path: Path) -> _AbstractProgress:
    """Parse a ``progress.json`` sidecar.

    Args:
        progress_path: Path to the ``progress.json`` file.

    Returns:
        The parsed :class:`_AbstractProgress`.

    Raises:
        OSError: If ``progress_path`` cannot be read.
        ValueError: If its contents are not valid JSON matching the expected
            schema (Pydantic's ``ValidationError`` subclasses ``ValueError``).
    """
    return _AbstractProgress.model_validate_json(progress_path.read_text(encoding="utf-8"))


def _save_progress(run_dir: Path, state: _AbstractProgress) -> None:
    """Persist ``progress.json`` for an in-progress run, guarding against a sealed run.

    Args:
        run_dir: The abstract run directory.
        state: The resumption state to write.

    Raises:
        SealedRunError: If ``run_dir`` is already sealed.
    """
    guard_writable(run_dir)
    atomic_write_bytes(
        run_dir / PROGRESS_FILENAME,
        state.model_dump_json(indent=2).encode("utf-8") + b"\n",
    )


def _find_resumable_run(
    abstracts_root: Path, *, endpoint: str, view: str, records_digest: str
) -> Path | None:
    """Find an unsealed abstract run whose recorded request matches exactly.

    Args:
        abstracts_root: The project's ``raw/abstracts/`` directory.
        endpoint: The endpoint template this call was asked to use.
        view: The view this call was asked to use.
        records_digest: :func:`_records_digest` of the record set this call was
            asked to fetch.

    Returns:
        The most recent (by run id, which sorts by time) unsealed run directory
        whose ``progress.json`` matches all three, or ``None`` when there is no
        such directory -- including when ``abstracts_root`` does not exist yet,
        or a candidate is sealed (never resumed) or carries an unreadable
        sidecar (left alone, never deleted).
    """
    if not abstracts_root.is_dir():
        return None

    candidates: list[Path] = []
    for entry in abstracts_root.iterdir():
        if not entry.is_dir() or is_sealed(entry):
            continue
        progress_path = entry / PROGRESS_FILENAME
        if not progress_path.is_file():
            continue
        try:
            state = _load_progress(progress_path)
        except (OSError, ValueError):
            continue
        if (
            state.endpoint == endpoint
            and state.view == view
            and state.records_digest == records_digest
        ):
            candidates.append(entry)

    if not candidates:
        return None
    return max(candidates, key=lambda path: path.name)


def _write_batch(run_dir: Path, filename: str, responses: Sequence[JsonDict]) -> None:
    """Write one batch of verbatim Abstract Retrieval responses as JSON Lines.

    One response per line, canonically encoded (``sort_keys``, compact
    separators) so that ``payload_sha256`` is byte-stable across a warm-cache
    re-run regardless of dict ordering -- the same guarantee, obtained the same
    way, as :func:`prismabib.capture.writer._write_page`.

    Args:
        run_dir: The abstract run directory.
        filename: The payload filename, e.g. ``"abstracts-0000.jsonl"``.
        responses: The batch's responses, in sorted-record-id order.

    Raises:
        SealedRunError: If ``run_dir`` is already sealed.
    """
    guard_writable(run_dir)
    lines = "".join(
        json.dumps(response, sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n"
        for response in responses
    )
    atomic_write_bytes(run_dir / filename, lines.encode("utf-8"))


def _payload_sha256(run_dir: Path, payload_files: Sequence[str]) -> str:
    """Hash the concatenation of every payload file, in fetch order.

    Args:
        run_dir: The abstract run directory.
        payload_files: Payload filenames, in fetch (and therefore
            concatenation) order.

    Returns:
        The hex-encoded SHA-256 digest over the concatenated bytes of every
        file named, read from disk in order. ``progress.json`` is not among
        them, by construction.
    """
    digest = hashlib.sha256()
    for filename in payload_files:
        digest.update((run_dir / filename).read_bytes())
    return digest.hexdigest()


def _abstract_entitlement_error(record_id: str, cause: EntitlementError) -> EntitlementError:
    """Build the message for a missing *Abstract Retrieval* entitlement.

    The client's own 403 message is written for the Search API and tells the
    reader that ``view=COMPLETE`` is the thing they lack. Reusing it here sends
    an operator who already has a working Search entitlement to check the one
    thing that is demonstrably fine, so the two failures are named apart.

    Args:
        record_id: The first record attempted, whose 403 triggered this.
        cause: The client's original :class:`~prismabib.errors.EntitlementError`.

    Returns:
        A new :class:`~prismabib.errors.EntitlementError` to raise ``from``
        ``cause``.
    """
    return EntitlementError(
        f"Scopus denied access (HTTP 403) to the Abstract Retrieval API on the very "
        f"first record ({record_id}), so no abstract was fetched and this run wrote "
        "nothing.\n"
        "\n"
        "This is a DIFFERENT entitlement from the Search API's view=COMPLETE. A key\n"
        "that captures your corpus perfectly well can be denied Abstract Retrieval:\n"
        "they are licensed separately, and a working capture proves nothing about\n"
        "this one. If your search runs succeed, the Search entitlement is not the\n"
        "problem and re-checking it will not help.\n"
        "\n"
        "What to do:\n"
        "  1. Run from your institution's network, which is often sufficient.\n"
        "  2. Off campus, ask your library for a Scopus institutional token and set\n"
        "     SCOPUS_INSTTOKEN alongside SCOPUS_API_KEY in your .env. If it is\n"
        "     already set, ask whether the subscription covers Abstract Retrieval\n"
        "     ('full text / abstract retrieval' in Elsevier's terms), naming that\n"
        "     API rather than Scopus generally.\n"
        "  3. Without it, subject-area codes cannot be obtained: set subject_areas\n"
        "     to [] in criteria.yaml and record the limitation in your protocol.\n"
        "\n"
        "prismabib will not fall back to view=META to work around this. META does\n"
        "carry subject areas, which is exactly what makes it tempting -- and a\n"
        "corpus whose areas came from two different views is not one filter.\n"
        "\n"
        f"The client reported: {cause}"
    )


def capture_abstracts(
    project: Project,
    *,
    record_ids: Iterable[str] | None = None,
    budget: int | None = None,
) -> AbstractRunManifest:
    """Run (or resume) one Abstract Retrieval enrichment and seal it into Layer 0.

    Fetches one Abstract Retrieval record per record id via
    :meth:`prismabib.sources.scopus.ScopusClient.abstract` (always
    ``view=FULL``; never degrades), writing them verbatim to
    ``raw/abstracts/<run_id>/abstracts-NNNN.jsonl`` and finishing with
    ``raw/abstracts/<run_id>/manifest.json``. See the module docstring for how
    placement, sealing, resumption, and the entitlement probe fit together.

    Args:
        project: The project to enrich; results are written under
            ``project.raw_dir / "abstracts"``.
        record_ids: The records to fetch. When ``None`` (the default), every
            record id captured by the project's *sealed* search runs is used,
            deduplicated across them -- so the default is "enrich the corpus I
            have". Ids are canonical prismabib record ids
            (``scopus:2-s2.0-XXXXXXXXXXX``); a bare Scopus id also works, since
            :meth:`ScopusClient.abstract` strips the namespace prefix either
            way.
        budget: The maximum number of records this invocation will fetch, or
            ``None`` for no limit. Intended for spending a known slice of a
            weekly quota. It bounds *attempts*, so a record served from
            ``raw/_cache/`` consumes budget too even though it costs no quota:
            the client does not report whether a call was a cache hit, and
            over-counting can only make an invocation stop early, never
            overspend.

            **Pass a multiple of** :data:`BATCH_SIZE`. Only completed batches
            are durable, so a budget's remainder below a batch boundary is
            fetched, discarded unwritten, and requested again on the next call
            -- recoverable in between only from the disposable
            ``raw/_cache/``. ``budget=150`` therefore advances the run by 100
            records, not 150. The same holds for an interruption: up to 99
            records of paid quota are lost when the cache is cold.

    Returns:
        The run's :class:`~prismabib.capture.manifest.AbstractRunManifest`.

        **When ``budget`` stops the run short, this value is returned but not
        written to disk**, and no ``manifest.json`` appears: the run stays
        unsealed and a later call resumes it. The absence of the file, not the
        return value, is what says "unfinished" -- so a caller that wants to
        know should test ``payload_files``/``records_fetched`` against
        ``records_requested`` rather than assume a returned manifest was
        sealed.

    Raises:
        ConfigError: If ``project.criteria`` cannot be read.
        ValidationError: If ``budget`` is not strictly positive, or if there is
            nothing to fetch -- an explicitly empty ``record_ids``, or no
            sealed search run to draw ids from. Failing loudly beats sealing an
            empty run that a later reader cannot distinguish from "asked, and
            Scopus had nothing".
        EntitlementError: On HTTP 403 for the first record attempted of a run
            that has written nothing yet -- a missing Abstract Retrieval
            entitlement (see the module docstring). Raised without sealing,
            leaving a resumable run. A 403 on a resumed run, or on any later
            record, is a per-record embargo instead and does not raise.
        AuthError: On HTTP 401 from Scopus; never retried.
        QuotaExceededError: On HTTP 429 indicating the weekly quota is
            exhausted. Raised without sealing: every completed batch stays on
            disk and ``progress.json`` names exactly those, so a later call
            resumes rather than restarting.
        RateLimitError: On HTTP 429 exhausting the retry budget.
        UpstreamError: On HTTP 5xx exhausting the retry budget, or any other
            unexpected status.
        SealedRunError: Only if an internal invariant is violated (a resumed
            run directory turns out to already be sealed); unreachable through
            this function's own code paths, since :func:`_find_resumable_run`
            never selects a sealed directory.
    """
    if budget is not None and budget < 1:
        raise ValidationError(f"budget must be a positive number of records, got {budget!r}")

    raw_dir = project.raw_dir
    raw_dir.mkdir(parents=True, exist_ok=True)
    abstracts_root = raw_dir / ABSTRACTS_DIRNAME

    if record_ids is None:
        source_run_ids, resolved_ids, missing_payload_files = _record_ids_from_layer0(raw_dir)
    else:
        source_run_ids = []
        missing_payload_files = []
        resolved_ids = sorted(set(record_ids))

    if not resolved_ids:
        raise ValidationError(
            f"No records to enrich under {raw_dir}. Either pass record_ids explicitly, "
            "or run capture_search() first so there is a sealed run to draw ids from."
        )

    endpoint = ScopusClient.ABSTRACT_ENDPOINT_TEMPLATE
    digest = _records_digest(resolved_ids)

    resumed_dir = _find_resumable_run(
        abstracts_root, endpoint=endpoint, view=ABSTRACT_VIEW, records_digest=digest
    )
    if resumed_dir is not None:
        run_dir = resumed_dir
        state = _load_progress(run_dir / PROGRESS_FILENAME)
        source_run_ids = list(state.source_run_ids)
        missing_payload_files = list(state.missing_source_payload_files)
        logger.info(
            "capture.abstracts.run_resumed",
            run_id=run_dir.name,
            endpoint=endpoint,
            records_requested=state.records_requested,
            records_already_done=state.records_done,
        )
    else:
        run_dir = abstracts_root / new_run_id()
        run_dir.mkdir(parents=True, exist_ok=True)
        state = _AbstractProgress(
            endpoint=endpoint,
            view=ABSTRACT_VIEW,
            started_at=datetime.now(UTC),
            source_run_ids=source_run_ids,
            records_digest=digest,
            records_requested=len(resolved_ids),
            missing_source_payload_files=missing_payload_files,
            records_done=0,
            records_fetched=0,
        )
        _save_progress(run_dir, state)
        logger.info(
            "capture.abstracts.run_started",
            run_id=run_dir.name,
            endpoint=endpoint,
            records_requested=len(resolved_ids),
            source_run_ids=source_run_ids,
        )

    run_id = run_dir.name
    settings = Settings()
    cache = HttpCache(raw_dir / CACHE_DIRNAME)
    attempted = 0
    budget_exhausted = False

    # A fresh limiter, never one carried over from a search run: Scopus quotas
    # are per-API, so an inherited bucket throttles this run against a quota it
    # does not spend. See the module docstring.
    with ScopusClient(settings, cache=cache, rate_limiter=RateLimiter()) as client:
        while state.records_done < len(resolved_ids):
            start = state.records_done
            batch_ids = resolved_ids[start : start + BATCH_SIZE]

            # Accumulated per batch and merged into `state` only once the batch
            # file is durably written. A partial batch is discarded, so a
            # resumed run cannot double-count an `unavailable` entry it already
            # recorded for a batch that never landed.
            batch_responses: list[JsonDict] = []
            batch_unavailable: list[AbstractUnavailable] = []

            for record_id in batch_ids:
                if budget is not None and attempted >= budget:
                    budget_exhausted = True
                    break

                # The probe fires only on a genuinely fresh run: the first
                # record of this invocation AND nothing durably written yet
                # (`start == 0` is `state.records_done == 0` for this batch).
                # Gating on `attempted` alone would re-arm it on every resume,
                # so an embargoed record that happened to sit at a batch
                # boundary would abort a run that had already written
                # thousands of abstracts -- and would do it with a message
                # blaming the key's entitlement rather than that one record.
                # See the module docstring.
                is_entitlement_probe = start == 0 and attempted == 0
                attempted += 1
                try:
                    response = client.abstract(record_id)
                except EntitlementError as exc:
                    if is_entitlement_probe:
                        # One call has told us the key lacks the Abstract
                        # Retrieval entitlement; 1,799 more would tell us the
                        # same thing at the price of a weekly quota.
                        logger.warning(
                            "capture.abstracts.entitlement_probe_failed",
                            run_id=run_id,
                            endpoint=endpoint,
                            record_id=record_id,
                        )
                        raise _abstract_entitlement_error(record_id, exc) from exc
                    batch_unavailable.append(
                        AbstractUnavailable(
                            record_id=record_id, http_status=403, reason="not_entitled"
                        )
                    )
                    logger.info(
                        "capture.abstracts.record_unavailable",
                        run_id=run_id,
                        record_id=record_id,
                        reason="not_entitled",
                    )
                    continue
                except RecordNotFoundError:
                    # A withdrawn or merged record. Recording it keeps the
                    # distinction a later reader needs -- "Scopus has no such
                    # record" is not "we never asked" and not "Scopus assigns no
                    # subject areas" -- and lets the run finish, which retrying
                    # a permanent 404 never would.
                    batch_unavailable.append(
                        AbstractUnavailable(
                            record_id=record_id, http_status=404, reason="not_found"
                        )
                    )
                    logger.info(
                        "capture.abstracts.record_unavailable",
                        run_id=run_id,
                        record_id=record_id,
                        reason="not_found",
                    )
                    continue

                batch_responses.append(response)
                if not _has_subject_areas(response):
                    batch_unavailable.append(
                        AbstractUnavailable(
                            record_id=record_id, http_status=200, reason="no_subject_areas"
                        )
                    )

            if budget_exhausted:
                # Nothing is written for a partial batch, on purpose: file N
                # must always cover records[N*BATCH_SIZE:(N+1)*BATCH_SIZE], so
                # that a resumed run's bytes match an uninterrupted one's.
                break

            filename = _PAYLOAD_TEMPLATE.format(index=start // BATCH_SIZE)
            _write_batch(run_dir, filename, batch_responses)
            state = state.model_copy(
                update={
                    "records_done": start + len(batch_ids),
                    "records_fetched": state.records_fetched + len(batch_responses),
                    "payload_files": [*state.payload_files, filename],
                    "unavailable": [*state.unavailable, *batch_unavailable],
                }
            )
            _save_progress(run_dir, state)
            logger.info(
                "capture.abstracts.batch_written",
                run_id=run_id,
                endpoint=endpoint,
                payload_file=filename,
                records_done=state.records_done,
                records_requested=state.records_requested,
            )

    manifest = AbstractRunManifest(
        run_id=run_id,
        started_at=state.started_at,
        finished_at=datetime.now(UTC),
        endpoint=endpoint,
        view=ABSTRACT_VIEW,
        source_run_ids=source_run_ids,
        missing_source_payload_files=missing_payload_files,
        records_requested=state.records_requested,
        records_fetched=state.records_fetched,
        unavailable=list(state.unavailable),
        payload_files=list(state.payload_files),
        payload_sha256=_payload_sha256(run_dir, state.payload_files),
        client_version=_CLIENT_VERSION,
        criteria_version=project.criteria.version,
    )

    if budget_exhausted:
        logger.info(
            "capture.abstracts.budget_exhausted",
            run_id=run_id,
            endpoint=endpoint,
            budget=budget,
            records_done=state.records_done,
            records_requested=state.records_requested,
        )
        return manifest

    guard_writable(run_dir)
    atomic_write_bytes(
        run_dir / RUN_MANIFEST_FILENAME,
        manifest.model_dump_json(indent=2).encode("utf-8") + b"\n",
    )

    progress_path = run_dir / PROGRESS_FILENAME
    if progress_path.is_file():
        # Not part of the sealed payload -- removed once sealing succeeds,
        # since there is nothing left to resume.
        progress_path.unlink()

    logger.info(
        "capture.abstracts.run_sealed",
        run_id=run_id,
        endpoint=endpoint,
        records_requested=manifest.records_requested,
        records_fetched=manifest.records_fetched,
        unavailable=len(manifest.unavailable),
        payload_sha256=manifest.payload_sha256,
    )
    return manifest


__all__ = ["ABSTRACT_VIEW", "BATCH_SIZE", "PROGRESS_FILENAME", "capture_abstracts"]
