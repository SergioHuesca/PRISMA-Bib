"""The Layer 0 run manifest (BUILD_PLAN §Stage 2 contract, lines 787-800).

Every acquisition run writes exactly one ``manifest.json`` alongside its
page files, and that manifest is the sole source of truth for the run: its
``total_results`` is the **only** sanctioned source of the PRISMA "records
identified" count (BUILD_PLAN S02-AC5) -- never a count of rows written or
records parsed, either of which could silently disagree with what the
server actually reported if a page were ever missed or duplicated.
"""

from __future__ import annotations

from datetime import datetime

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
