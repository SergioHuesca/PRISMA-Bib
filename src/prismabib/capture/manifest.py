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
  embargo. A 403 on the *first* record of an invocation is not this; it is a
  missing Abstract Retrieval entitlement and aborts the run
  (:func:`prismabib.capture.enrich.capture_abstracts`).
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
    records_requested: int
    records_fetched: int
    unavailable: list[AbstractUnavailable]
    payload_files: list[str]
    payload_sha256: str
    client_version: str
    criteria_version: str
