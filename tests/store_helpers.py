"""Shared test-only helpers for Stage 3's ``store/`` suite (BUILD_PLAN §Stage 3).

Kept out of ``tests/builders.py`` deliberately: ``SyntheticCorpus`` builds
:class:`~prismabib.models.Record` objects (Stage 1's domain layer), while
everything here builds *Layer 0 on-disk fixtures* -- raw ``page-NNNN.jsonl``
files and ``manifest.json`` -- which is a different concern (BUILD_PLAN §2.2
shape) that only Stage 3's loader tests need.

Nothing here imports from ``tests.builders`` or vice versa; the two modules
compose (a test could feed a ``SyntheticCorpus``-built ``Record``'s fields
into :func:`make_entry`) without depending on each other.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

import prismabib
from prismabib.capture.manifest import RunManifest
from prismabib.project import Project

#: The frozen reference fixture project (BUILD_PLAN §3.7.5, line 536).
#: Read-only: callers that need to run ``build_store`` against it must go
#: through :func:`copy_reference_project`, never point ``build_store`` at
#: this path directly -- that would write ``store/corpus.duckdb`` into a
#: checked-in, version-controlled directory.
REFERENCE_PROJECT_DIR = Path(__file__).parent / "fixtures" / "projects" / "reference"

#: The real, checked-in Layer 1 schema DDL -- not a private
#: ``prismabib.store.load`` symbol, so this helper does not depend on that
#: module's internal naming.
SCHEMA_SQL_PATH = Path(prismabib.__file__).parent / "store" / "schema.sql"


def reference_run_dir() -> Path:
    """Return the reference fixture's single sealed Layer 0 run directory.

    Returns:
        The one directory under ``REFERENCE_PROJECT_DIR / "raw"`` (found by
        listing rather than hard-coding the run id, so a future regeneration
        of the fixture under a different run id does not break every caller).
    """
    (run_dir,) = sorted(path for path in (REFERENCE_PROJECT_DIR / "raw").iterdir() if path.is_dir())
    return run_dir


def read_reference_entry(payload_file: str, line: int) -> dict[str, Any]:
    """Read one raw Scopus entry from the reference fixture, by page and line.

    Args:
        payload_file: A page filename under :func:`reference_run_dir`, e.g.
            ``"page-0000.jsonl"``.
        line: The 0-based line index within that file (BUILD_PLAN's Stage 3
            README location table, ``tests/fixtures/projects/reference/README.md``).

    Returns:
        The parsed entry at that line.
    """
    path = reference_run_dir() / payload_file
    with path.open("r", encoding="utf-8") as handle:
        raw_line = next(text for index, text in enumerate(handle) if index == line)
    return dict(json.loads(raw_line))


def copy_reference_project(tmp_path: Path) -> Project:
    """Build a fresh, writable :class:`Project` whose ``raw/`` is the reference fixture.

    Args:
        tmp_path: A pytest ``tmp_path`` (or equivalent) to build the project
            under.

    Returns:
        A :class:`Project` with a full §2.3 skeleton (``Project.init``) whose
        ``raw/`` directory contents are copied from
        :data:`REFERENCE_PROJECT_DIR`'s ``raw/`` -- never the checked-in
        fixture directory itself, so a test calling ``build_store`` against
        the result never writes ``store/corpus.duckdb`` under version
        control.
    """
    project = Project.init("reference", title="Reference fixture (test copy)", root=tmp_path)
    shutil.copytree(REFERENCE_PROJECT_DIR / "raw", project.raw_dir, dirs_exist_ok=True)
    return project


def create_schema(connection: duckdb.DuckDBPyConnection) -> None:
    """Execute the real, checked-in ``schema.sql`` against ``connection``.

    Args:
        connection: An open, writable DuckDB connection with no Layer 1
            tables yet (typically a fresh ``duckdb.connect(":memory:")``).
    """
    connection.execute(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))


def write_sealed_run(
    raw_dir: Path,
    run_id: str,
    entries: list[dict[str, Any]],
    *,
    started_at: datetime,
    query: str = 'TITLE-ABS-KEY("test query")',
    total_results: int | None = None,
    criteria_version: str = "1.0.0",
) -> Path:
    """Write one sealed Layer 0 run directory by hand, new true-JSONL format.

    A minimal, from-scratch stand-in for
    :func:`prismabib.capture.writer.capture_search` -- everything Stage 3's
    loader needs (``page-0000.jsonl`` as true JSON Lines, plus
    ``manifest.json``), nothing it does not (no ``page-NNNN.meta.json``, no
    ``cursor.json``: the loader never reads either).

    Args:
        raw_dir: The project's ``raw/`` directory (``project.raw_dir``).
        run_id: The run's directory name. Must sort correctly relative to
            any other run written into the same ``raw_dir`` if traversal
            order matters to the calling test (BUILD_PLAN's "sorted run_id,
            oldest first" ordering -- see ``store/load.py``'s module
            docstring).
        entries: The Scopus entries for this run's one page, one per line,
            in the order written (BUILD_PLAN §2.2: true JSON Lines).
        started_at: The run's ``RunManifest.started_at`` -- also the
            ``retrieved_at`` every entry's citation snapshot is stamped
            with (see ``store/load.py``'s "Citation snapshots" section).
        query: The recorded query string; content is irrelevant to the
            loader beyond being stored verbatim in ``runs.query``.
        total_results: ``RunManifest.total_results``; defaults to
            ``len(entries)`` when omitted.
        criteria_version: ``RunManifest.criteria_version``.

    Returns:
        The written run directory.
    """
    run_dir = raw_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    payload_file = "page-0000.jsonl"
    lines = [json.dumps(entry, sort_keys=True, separators=(",", ":")) for entry in entries]
    text = "".join(f"{line}\n" for line in lines)
    (run_dir / payload_file).write_text(text, encoding="utf-8")

    payload_sha256 = hashlib.sha256((run_dir / payload_file).read_bytes()).hexdigest()
    manifest = RunManifest(
        run_id=run_id,
        started_at=started_at,
        finished_at=started_at,
        endpoint="https://api.elsevier.com/content/search/scopus",
        query=query,
        view="COMPLETE",
        total_results=total_results if total_results is not None else len(entries),
        pages_fetched=1,
        payload_files=[payload_file],
        payload_sha256=payload_sha256,
        client_version="0.1.0",
        criteria_version=criteria_version,
    )
    (run_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
    return run_dir


def make_entry(
    *,
    eid: str,
    title: str = "A Synthetic Study",
    cover_date: str = "2020-01-01",
    doi: str | None = None,
    dc_identifier: str | None = None,
    authkeywords: str | None = None,
    language: str | None = "English",
    citedby_count: int = 0,
    description: str | None = "A synthetic abstract.",
    affiliation: Any = None,
    author: Any = None,
    subtype_description: str = "Article",
    source_id: str = "1000001",
    publication_name: str = "Journal of Synthetic Testing",
) -> dict[str, Any]:
    """Build one minimal, hand-authored Scopus Search API entry.

    Every field ``store/load.py`` treats as required (``eid``, ``dc:title``,
    ``prism:coverDate``) is always present; everything else is present only
    when the caller supplies it, matching Scopus's own "absent, not null"
    convention for optional fields (see ``store/load.py``'s module
    docstring on ``authkeywords``/``dc:description``).

    Args:
        eid: The Scopus EID, e.g. ``"2-s2.0-800000000001"``.
        title: ``dc:title``.
        cover_date: ``prism:coverDate``, ISO ``YYYY-MM-DD``.
        doi: ``prism:doi``, omitted entirely when ``None``.
        dc_identifier: ``dc:identifier``; defaults to
            ``f"SCOPUS_ID:{eid.rsplit('-', 1)[-1]}"`` when omitted, matching
            the reference fixture's own convention.
        authkeywords: The raw ``"term | term | ..."`` string, omitted
            entirely when ``None`` (never an empty string -- BUILD_PLAN's
            "no phantom keyword" case needs the key itself absent).
        language: ``language``, omitted entirely when ``None``.
        citedby_count: ``citedby-count`` (int; entry stores it as a string,
            matching the real wire format).
        description: ``dc:description``, omitted entirely when ``None``.
        affiliation: ``affiliation``, omitted entirely when ``None``.
        author: ``author``, omitted entirely when ``None``.
        subtype_description: ``subtypeDescription``.
        source_id: ``source-id``.
        publication_name: ``prism:publicationName``.

    Returns:
        The entry dict.
    """
    entry: dict[str, Any] = {
        "eid": eid,
        "dc:identifier": dc_identifier or f"SCOPUS_ID:{eid.rsplit('-', 1)[-1]}",
        "dc:title": title,
        "prism:coverDate": cover_date,
        "subtypeDescription": subtype_description,
        "source-id": source_id,
        "prism:publicationName": publication_name,
        "prism:aggregationType": "Journal",
        "citedby-count": str(citedby_count),
        "openaccess": "0",
    }
    if doi is not None:
        entry["prism:doi"] = doi
    if authkeywords is not None:
        entry["authkeywords"] = authkeywords
    if language is not None:
        entry["language"] = language
    if description is not None:
        entry["dc:description"] = description
    if affiliation is not None:
        entry["affiliation"] = affiliation
    if author is not None:
        entry["author"] = author
    return entry


__all__ = [
    "REFERENCE_PROJECT_DIR",
    "SCHEMA_SQL_PATH",
    "copy_reference_project",
    "create_schema",
    "make_entry",
    "read_reference_entry",
    "reference_run_dir",
    "write_sealed_run",
]
