"""The Layer 0 run manifests (BUILD_PLAN §Stage 2 contract, lines 787-800).

Every acquisition run writes exactly one ``manifest.json`` alongside its
payload files, and that manifest is the sole source of truth for the run.

Two manifest schemas live here, and the difference between them is
load-bearing rather than cosmetic:

- :class:`RunManifest` describes a **Scopus Search** run
  (``raw/<run_id>/``). Its ``total_results`` is the **only** sanctioned
  source of the PRISMA "records identified" count (BUILD_PLAN S02-AC5) --
  never a count of rows written or records parsed, either of which could
  silently disagree with what the server actually reported if a page were
  ever missed or duplicated. This model is frozen by the plan and is not
  touched by anything below.
- :class:`AbstractRunManifest` describes an **Abstract Retrieval**
  enrichment run (``raw/abstracts/<run_id>/``). It deliberately has **no**
  ``total_results`` field, because an abstract run identifies no records: it
  re-describes records a search run already identified. Giving it one would
  create a second, unsanctioned candidate for the PRISMA identification
  number, which is exactly the failure S02-AC5 exists to prevent -- and it
  is the reason an abstract run is never written as a row in the ``runs``
  table either. See ADR 0011.

:class:`AbstractUnavailable` is the part of :class:`AbstractRunManifest`
that has to survive longest. A record with no subject-area codes in Layer 1
is ambiguous on its own -- it could mean "Scopus assigns this record no
subject areas" or "we never asked about this record". Only the manifest can
tell those apart, so the outcome of every record that could not be
enriched is recorded here, in run metadata, rather than being inferable
from the absence of a payload line.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class RunManifest(BaseModel):
    """The manifest for one Scopus acquisition run (frozen model, lines 787-800).

    Written to ``raw/<run_id>/manifest.json`` once a run completes; its
    presence is also what BUILD_PLAN §2.2's Layer 0 immutability invariant
    keys off of -- see :mod:`prismabib.capture.writer` for how a second
    write to a sealed run is refused.

    Attributes:
        run_id: The run's identifier; also the name of its directory under
            ``raw/``.
        started_at: When the run began (UTC).
        finished_at: When the run completed (UTC).
        endpoint: The API endpoint queried, e.g.
            ``ScopusClient.SEARCH_ENDPOINT``.
        query: The exact Boolean query string used.
        view: The Scopus response view used (must be ``"COMPLETE"`` for a
            real acquisition; see BUILD_PLAN §5 risk 1).
        total_results: The server-reported total match count
            (``opensearch:totalResults`` on the first page). The **only**
            sanctioned source of the PRISMA "records identified" number.
        pages_fetched: The number of pages retrieved (and written).
        payload_files: The page filenames written, in fetch order, relative
            to the run directory (e.g. ``["page-0000.jsonl", ...]``).
        payload_sha256: A SHA-256 digest over the concatenation, in
            ``payload_files`` order, of the exact bytes written to each
            page file.
        client_version: The prismabib package version that performed the
            run.
        criteria_version: The ``criteria.yaml`` version in effect when the
            run was made, so a later protocol amendment cannot silently be
            read back onto an old capture.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str
    started_at: datetime
    finished_at: datetime
    endpoint: str
    query: str
    view: str
    total_results: int
    pages_fetched: int
    payload_files: list[str]
    payload_sha256: str
    client_version: str
    criteria_version: str


AbstractUnavailableReason = Literal["not_found", "not_entitled", "no_subject_areas"]
"""Why one record contributes no subject-area codes to a Layer 1 rebuild.

- ``"not_entitled"``: HTTP 403 for that specific record -- a per-record
  embargo. A 403 on the first record of a run that has written nothing yet is
  not this; it is a missing Abstract Retrieval entitlement and aborts the run
  (:func:`prismabib.capture.enrich.capture_abstracts`). A *resumed* run never
  triggers that check, so a 403 at a batch boundary is recorded here rather
  than aborting a run with abstracts already on disk.
- ``"no_subject_areas"``: HTTP 200, a real Abstract Retrieval record, and
  Scopus assigns it no ``subject-areas.subject-area`` entries. The payload
  line **is** written for these; the record appears here as well, so that a
  later reader knows the empty set was observed rather than assumed.
- ``"not_found"``: HTTP 404 -- Scopus has no record at that identifier.
  Scopus withdraws and merges records, so an identifier captured in an
  earlier search run can stop resolving later.
  :class:`~prismabib.sources.scopus.RecordNotFoundError` is raised outside
  the retry set and the record is recorded here, so one withdrawn record
  cannot abort an 1,800-record run that retrying could never have fixed.
"""


class AbstractUnavailable(BaseModel):
    """One record that will contribute no subject-area codes, and why.

    Attributes:
        record_id: The canonical prismabib record id
            (``scopus:2-s2.0-XXXXXXXXXXX``, BUILD_PLAN §3.2).
        http_status: The HTTP status that produced this outcome, or
            ``None`` if it did not arise from a response status. It is
            ``200`` for ``"no_subject_areas"``: the request succeeded and
            the answer was "none", which is a fact about the record, not a
            failure.
        reason: Which of the three outcomes this is; see
            :data:`AbstractUnavailableReason`.
    """

    model_config = ConfigDict(frozen=True)

    record_id: str
    http_status: int | None
    reason: AbstractUnavailableReason


class AbstractRunManifest(BaseModel):
    """The manifest for one Scopus Abstract Retrieval enrichment run.

    Written to ``raw/abstracts/<run_id>/manifest.json`` once a run completes.
    Its presence is the seal, exactly as for :class:`RunManifest`
    (:func:`prismabib.capture.layout.is_sealed` answers the question for
    either without knowing which kind it is looking at) -- but the schema is
    different, which is why abstract runs are nested under ``abstracts/``
    rather than sitting beside search runs: a scan that found this file at
    ``raw/<run_id>/manifest.json`` would parse it as a search run and fail.

    Attributes:
        run_id: The run's identifier; also the name of its directory under
            ``raw/abstracts/``.
        started_at: When the run began (UTC). Preserved across resumption,
            so it dates the first attempt, not the last.
        finished_at: When the run completed (UTC).
        endpoint: The Abstract Retrieval endpoint template used
            (``ScopusClient.ABSTRACT_ENDPOINT_TEMPLATE``) -- a template
            rather than a URL, since each record has its own.
        view: The Scopus response view used. Always ``"FULL"``; see
            :mod:`prismabib.capture.enrich` for why it never degrades to
            ``META`` even though ``META`` also carries subject areas.
        source_run_ids: The sealed search runs whose records this run was
            asked to enrich, sorted. Empty when the caller passed an
            explicit ``record_ids`` list, since then the record set did not
            come from Layer 0 at all.
        missing_source_payload_files: ``"<run_id>/<filename>"`` for every
            page file a source run's seal names that was **not on disk**
            when the record set was resolved, sorted; ``[]`` for an intact
            Layer 0. Enrichment skips such a file rather than refusing to
            run -- a damaged capture should not cost the rest of the corpus
            its subject areas -- but skipping it shrinks
            ``records_requested`` with nothing else to show for it: the run
            still seals with ``records_fetched == records_requested`` and
            an empty ``unavailable``, and every record from the absent page
            ends up with no subject areas and no entry explaining why. This
            field is what keeps "the corpus was incomplete before we
            started" readable from the seal. (The Layer 1 loader is *not*
            lenient about the same input, so the two layers can otherwise
            disagree about whether a capture is whole.)
        records_requested: How many distinct record ids this run set out to
            fetch.
        records_fetched: How many payload lines were written -- one per
            record whose Abstract Retrieval response was persisted. A
            record counted here may still appear in ``unavailable`` with
            reason ``"no_subject_areas"``.
        unavailable: Every record that will contribute no subject-area
            codes, with the reason. Run metadata, deliberately in the
            manifest rather than in a payload file: see the module
            docstring.
        payload_files: The payload filenames written, in fetch (and
            therefore concatenation) order, relative to the run directory
            (e.g. ``["abstracts-0000.jsonl", ...]``).
        payload_sha256: A SHA-256 digest over the concatenation, in
            ``payload_files`` order, of the exact bytes written to each
            payload file. The ``progress.json`` resumption sidecar is
            **not** part of it -- it is bookkeeping, and it is deleted when
            the run seals.
        client_version: The prismabib package version that performed the
            run.
        criteria_version: The ``criteria.yaml`` version in effect when the
            run was made, so a later protocol amendment cannot silently be
            read back onto an old capture.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str
    started_at: datetime
    finished_at: datetime
    endpoint: str
    view: str
    source_run_ids: list[str]
    missing_source_payload_files: list[str] = []
    records_requested: int
    records_fetched: int
    unavailable: list[AbstractUnavailable]
    payload_files: list[str]
    payload_sha256: str
    client_version: str
    criteria_version: str


class FullTextRunManifest(BaseModel):
    """The manifest for one Stage 6 full-text resolution run (ADR 0019, Decision 0).

    Written to ``fulltext/runs/<run_id>/manifest.json`` once a run completes; its
    presence is the seal, exactly as for :class:`RunManifest`/:class:`AbstractRunManifest`
    (:func:`prismabib.capture.layout.is_sealed` answers the question for any of the
    three without knowing which kind it is looking at). Nested under
    ``projects/<slug>/fulltext/``, not ``raw/``: full-text resolution writes licensed
    publisher content, which BUILD_PLAN and ADR 0019 both require stays out of the
    Layer 0 archive entirely -- ``fulltext/`` is guard-blocked from ``git`` on its
    own, and is scanned by :mod:`prismabib.store.load` as a second, independent tree
    of sealed runs, the same way ``raw/abstracts/`` is.

    Unlike :class:`RunManifest`, this identifies no record and unlike
    :class:`AbstractRunManifest`, it re-describes no Layer 0 payload byte for byte --
    a PDF's extracted sections are not stable across ``pdfplumber``/``pdfminer``
    versions, so ``build_store`` reruns extraction from the sealed ``assets/`` bytes
    on every rebuild rather than trusting a previously-computed result. What this
    manifest records is what the *run itself* did: how many records it attempted,
    resolved, refused and left unresolved, broken down by resolver -- everything
    :class:`~prismabib.fulltext.run.FullTextRunSummary` needs without re-parsing
    ``attempts.jsonl``.

    Attributes:
        run_id: The run's identifier; also its directory name under
            ``fulltext/runs/``.
        started_at: When the run began (UTC). Preserved across resumption, so it
            dates the first attempt, not the last.
        finished_at: When the run completed (UTC) -- i.e. when it sealed.
        records_requested: How many distinct, not-already-resolved record ids this
            run was asked to attempt (BUILD_PLAN "resumable": a record with an
            already-resolved row in an *earlier* sealed run is never a member of
            this set at all -- see :func:`prismabib.fulltext.capture.already_resolved_record_ids`).
        records_attempted: How many of ``records_requested`` this specific call to
            :func:`~prismabib.fulltext.capture.capture_fulltext` actually ran
            through the chain -- ``0`` when a resumed/budget-bounded call attempted
            nothing further, and always ``<= records_requested``.
        records_resolved: How many records this run obtained an asset for, summed
            over the whole run's lifetime (i.e. including earlier calls that
            resumed into this same unsealed run directory), not just this call.
        resolved_by_resolver: ``records_resolved``, broken down by which resolver
            produced the asset, over the whole run's lifetime.
        refused_by_resolver: How many :class:`~prismabib.errors.EntitlementError`
            refusals (``entitled=false``) each resolver produced, over the whole
            run's lifetime -- the anti-bias number ADR 0019 exists to surface.
        unresolved_record_ids: Records whose chain was exhausted with no asset and
            no unhandled resolver failure -- candidates for a human to review and,
            only after confirming no institutional route exists, mark
            ``INACCESSIBLE`` during full-text screening. Never itself a decision;
            see :mod:`prismabib.fulltext.resolve`.
        failed_record_ids: Records for which a resolver raised something other than
            :class:`~prismabib.errors.EntitlementError` partway through the chain
            (an upstream 5xx exhausting retries, a network timeout, ...) --
            distinct from ``unresolved_record_ids``: a *failure* means the chain
            did not run to completion for that record, so attempts made by any
            resolver reached before the failure are still recorded in
            ``attempts.jsonl``, but later resolvers in the chain were never tried
            for it. A later call re-attempts it from resolver 1, exactly like a
            record that was never attempted at all.
        attempts_file: The payload filename, always ``"attempts.jsonl"``.
        attempts_sha256: SHA-256 digest of the exact bytes written to
            ``attempts_file``.
        client_version: The prismabib package version that performed the run.
        criteria_version: The ``criteria.yaml`` version in effect when the run was
            made.
    """

    model_config = ConfigDict(frozen=True)

    run_id: str
    started_at: datetime
    finished_at: datetime
    records_requested: int
    records_attempted: int
    records_resolved: int
    resolved_by_resolver: dict[str, int]
    refused_by_resolver: dict[str, int]
    unresolved_record_ids: list[str]
    failed_record_ids: list[str]
    attempts_file: str
    attempts_sha256: str
    client_version: str
    criteria_version: str
