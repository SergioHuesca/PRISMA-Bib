"""Run the Stage 6 resolver chain over a project and persist to Layer 1 (ADR 0019).

:func:`run_fulltext_resolution` is what ``prismabib fulltext`` (see
:mod:`prismabib.cli`) actually calls: for each targeted record, run
:func:`~prismabib.fulltext.resolve.resolve_fulltext`, write every
:class:`~prismabib.fulltext.resolve.FullTextAttempt` to ``fulltext_assets``,
and -- when an asset was obtained -- extract and write its
:class:`~prismabib.fulltext.extract.Section`\\ s to ``fulltext_sections``.

**On writing to Layer 1 outside ``build_store``.**
:mod:`prismabib.store.db`'s own docstring states the established convention
plainly: a read/write connection is meant for
:func:`prismabib.store.load.build_store` alone, and every analysis module
opens read-only. This module is a deliberate, reported exception, not an
oversight -- see the project report for the full reasoning; summarised:

1. Unlike ``abstract_runs``/``record_subject_area_coverage`` (ADR 0018),
   ADR 0019 does not define a Layer 0 sealed-run scheme for full text --
   fetched bytes live under ``project.fulltext_dir``, not ``raw/``, and are
   never re-derivable byte-for-byte in the way a JSON API response is (a
   PDF's extracted text is not stable across ``pdfplumber``/``pdfminer``
   versions the way a JSON re-parse is). "Layer 1 reconstructible from
   Layer 0 by running one function" cannot be a clean guarantee for these
   two tables regardless of how they are written.
2. ``fulltext_assets``' primary key, ``(record_id, resolver_name)``, is
   already the natural resumption key BUILD_PLAN's "resumable" requirement
   needs (:func:`already_resolved_record_ids`) -- unlike Scopus Search's
   page-ordered pagination, there is no batch-boundary reproducibility
   concern a file-based Layer 0 run would exist to solve.

No golden value depends on this: no project has full-text assets yet
(BUILD_PLAN, ADR 0019 consequence 4), so this module has no reproducibility
obligation to a committed snapshot to violate.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

import duckdb
import structlog
from pydantic import BaseModel, ConfigDict

from prismabib.config import Settings
from prismabib.errors import ValidationError
from prismabib.fulltext.extract import Section, extract_pdf, extract_sciencedirect_xml
from prismabib.fulltext.resolve import (
    FullTextAsset,
    FullTextAttempt,
    default_chain,
    resolve_fulltext,
)
from prismabib.prisma.engine import manual_abstract_set
from prismabib.store.db import connect

if TYPE_CHECKING:
    from prismabib.project import Project

logger = structlog.get_logger(__name__)


class FullTextRunSummary(BaseModel):
    """What one ``run_fulltext_resolution`` call did, for the CLI to report.

    Attributes:
        records_considered: How many records were targeted in total
            (before excluding already-resolved ones).
        records_attempted: How many of those were actually run through the
            chain this call -- ``0`` when every targeted record was already
            resolved, or bounded by ``budget``.
        records_resolved: How many of ``records_attempted`` obtained an
            asset.
        resolved_by_resolver: ``records_resolved``, broken down by which
            resolver produced the asset.
        refused_by_resolver: How many :class:`~prismabib.errors.EntitlementError`
            refusals (``entitled=false``) each resolver produced this call
            -- the anti-bias number ADR 0019 exists to surface.
        unresolved_record_ids: Records attempted this call for which the
            chain was exhausted with no asset -- candidates for a human to
            review and, only after confirming no institutional route
            exists, mark ``INACCESSIBLE`` during full-text screening. This
            list is not itself a decision; see the module docstring of
            :mod:`prismabib.fulltext.resolve`.
    """

    model_config = ConfigDict(frozen=True)

    records_considered: int
    records_attempted: int
    records_resolved: int
    resolved_by_resolver: dict[str, int]
    refused_by_resolver: dict[str, int]
    unresolved_record_ids: tuple[str, ...]


def already_resolved_record_ids(connection: duckdb.DuckDBPyConnection) -> frozenset[str]:
    """Record ids that already have a resolved ``fulltext_assets`` row.

    Args:
        connection: An open Layer 1 connection.

    Returns:
        Every ``record_id`` with at least one ``fulltext_assets`` row whose
        ``media_type`` is not ``NULL`` -- the resumption set: a record
        already resolved is never re-attempted (BUILD_PLAN "resumable").
    """
    rows = connection.execute(
        "SELECT DISTINCT record_id FROM fulltext_assets WHERE media_type IS NOT NULL"
    ).fetchall()
    return frozenset(str(record_id) for (record_id,) in rows)


def record_fulltext_attempt(
    connection: duckdb.DuckDBPyConnection, attempt: FullTextAttempt
) -> None:
    """Persist one :class:`~prismabib.fulltext.resolve.FullTextAttempt` row.

    Args:
        connection: An open, read/write Layer 1 connection.
        attempt: The attempt to persist.

    ``INSERT OR REPLACE`` rather than a bare ``INSERT``: a record that was
    previously refused (``entitled=false``) and is now resolved (a new
    institutional token, a fresh manual drop) must overwrite its old row
    rather than violate the ``(record_id, resolver_name)`` primary key.
    """
    connection.execute(
        """
        INSERT OR REPLACE INTO fulltext_assets
          (record_id, resolver_name, media_type, path, retrieved_at, entitled)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            attempt.record_id,
            attempt.resolver_name,
            attempt.media_type,
            str(attempt.path) if attempt.path is not None else None,
            attempt.retrieved_at,
            attempt.entitled,
        ],
    )


def record_fulltext_sections(
    connection: duckdb.DuckDBPyConnection, record_id: str, sections: Sequence[Section]
) -> None:
    """Persist one record's extracted sections, replacing any it already had.

    Args:
        connection: An open, read/write Layer 1 connection.
        record_id: The record these sections belong to.
        sections: The sections to persist, in the order they should be
            stored -- their own ``position`` field, not insertion order, is
            what a reader relies on, but writing them in order keeps the
            two agreeing.

    A delete-then-insert rather than an upsert: a re-resolution can produce
    a different number of sections than a previous one, and stale trailing
    rows from a longer previous extraction must not survive.
    """
    connection.execute("DELETE FROM fulltext_sections WHERE record_id = ?", [record_id])
    for section in sections:
        connection.execute(
            """
            INSERT INTO fulltext_sections (record_id, position, section_name, text, low_confidence)
            VALUES (?, ?, ?, ?, ?)
            """,
            [
                record_id,
                section.position,
                section.section_name,
                section.text,
                section.low_confidence,
            ],
        )


def _extract_sections(asset: FullTextAsset) -> tuple[Section, ...]:
    """Dispatch extraction by an asset's media type.

    Args:
        asset: A resolved asset.

    Returns:
        Its extracted sections -- :func:`~prismabib.fulltext.extract.extract_sciencedirect_xml`
        for ``media_type == "xml"``, :func:`~prismabib.fulltext.extract.extract_pdf`
        for ``"pdf"``. An unrecognised media type (unreachable through this
        module's own resolvers, which only ever produce these two) yields
        no sections rather than raising, so a future resolver's new media
        type degrades to "nothing extracted yet" instead of aborting a run.

        A file that resolved successfully (the resolver obtained bytes and
        wrote them to disk) but does not actually parse -- a corrupted
        download, a manual drop that turns out not to be a real PDF -- also
        yields no sections rather than raising. The asset row (with its
        real ``resolver_name``/``entitled``) is still persisted either way:
        an unparseable file is a fact about that one file, not a reason to
        abort a run that may have hundreds of other records left to
        process, and BUILD_PLAN's "no OCR, a human reads it" applies just
        as much to a file this extractor cannot open at all.
    """
    try:
        if asset.media_type == "xml":
            return extract_sciencedirect_xml(asset.path.read_bytes())
        if asset.media_type == "pdf":
            return extract_pdf(asset.path)
    except Exception:
        logger.warning(
            "fulltext.extract.failed",
            record_id=asset.record_id,
            resolver=asset.resolver_name,
            media_type=asset.media_type,
            path=str(asset.path),
            exc_info=True,
        )
        return ()
    logger.warning("fulltext.extract.unknown_media_type", media_type=asset.media_type)
    return ()


def run_fulltext_resolution(
    project: Project,
    *,
    record_ids: Sequence[str] | None = None,
    budget: int | None = None,
    settings: Settings | None = None,
) -> FullTextRunSummary:
    """Run (or resume) the Stage 6 resolver chain over a project.

    Args:
        project: The project to resolve full text for.
        record_ids: The records to target. When ``None`` (the default),
            every record in
            :func:`prismabib.prisma.engine.manual_abstract_set` -- ``M_abs``,
            "sought for full-text retrieval" in PRISMA terms -- is used.
            Passing an explicit set is for a reviewer who wants to target a
            specific record (retry after a manual drop) without waiting for
            a whole-corpus scan.
        budget: The maximum number of **not-already-resolved** records this
            invocation will attempt. ``None`` (the default) means no limit.
            Already-resolved records do not count against it -- resuming a
            large run should never look like it is making no progress
            because the budget was spent re-confirming old successes.
        settings: The environment configuration to build the resolver chain
            from. Defaults to ``Settings()`` (via
            :func:`~prismabib.fulltext.resolve.default_chain`) when
            omitted. Exposed primarily so a test can inject one without
            touching the real environment, exactly as
            :class:`~prismabib.sources.scopus.ScopusClient` and
            :class:`~prismabib.capture.enrich.capture_abstracts` already do.

    Returns:
        A :class:`FullTextRunSummary` of what this call did.

    Raises:
        ValidationError: If ``budget`` is not strictly positive, or if
            there is nothing to resolve (an explicitly empty ``record_ids``,
            or an empty ``manual_abstract_set``).
        ConfigError: If ``project.criteria.yaml`` cannot be read, or if
            ``Settings()`` cannot be constructed (``SCOPUS_API_KEY`` missing
            -- required unconditionally by
            :class:`~prismabib.config.Settings`, even though this function
            itself never calls Scopus).
        StoreError: If no Layer 1 store exists yet for ``project``.
        LogError: If the decision log fails to load while computing
            ``manual_abstract_set``.
    """
    if budget is not None and budget < 1:
        raise ValidationError(f"budget must be a positive number of records, got {budget!r}")

    # Resolved *before* opening a read/write connection below: DuckDB refuses a
    # second connection to the same file from one process when the two
    # disagree about configuration (see prismabib.store.load.Corpus.records),
    # and manual_abstract_set opens (and closes) its own read-only one.
    if record_ids is None:
        target_ids = sorted(manual_abstract_set(project))
    else:
        target_ids = sorted(set(record_ids))

    if not target_ids:
        raise ValidationError(
            f"No records to resolve full text for under {project.root}. Either pass "
            "record_ids explicitly, or screen at least one record to 'include' at "
            "title/abstract screening first, so manual_abstract_set has something to "
            "draw ids from."
        )

    resolved_by_resolver: dict[str, int] = {}
    refused_by_resolver: dict[str, int] = {}
    records_resolved = 0
    attempted = 0
    unresolved: list[str] = []

    with default_chain(project, settings) as resolvers:
        connection = connect(project, read_only=False)
        try:
            rows = connection.execute(
                "SELECT record_id, doi FROM records WHERE record_id = ANY(?)",
                [target_ids],
            ).fetchall()
            doi_by_record_id = {str(record_id): doi for record_id, doi in rows}

            already_resolved = already_resolved_record_ids(connection)
            pending = [record_id for record_id in target_ids if record_id not in already_resolved]

            for record_id in pending:
                if budget is not None and attempted >= budget:
                    break
                attempted += 1

                doi = doi_by_record_id.get(record_id)
                asset, attempts = resolve_fulltext(
                    record_id=record_id, doi=doi, resolvers=resolvers
                )

                for attempt in attempts:
                    record_fulltext_attempt(connection, attempt)
                    if attempt.entitled is False:
                        refused_by_resolver[attempt.resolver_name] = (
                            refused_by_resolver.get(attempt.resolver_name, 0) + 1
                        )

                if asset is None:
                    unresolved.append(record_id)
                    continue

                records_resolved += 1
                resolved_by_resolver[asset.resolver_name] = (
                    resolved_by_resolver.get(asset.resolver_name, 0) + 1
                )
                record_fulltext_sections(connection, record_id, _extract_sections(asset))

            logger.info(
                "fulltext.run.complete",
                records_considered=len(target_ids),
                records_attempted=attempted,
                records_resolved=records_resolved,
                unresolved=len(unresolved),
            )
        finally:
            connection.close()

    return FullTextRunSummary(
        records_considered=len(target_ids),
        records_attempted=attempted,
        records_resolved=records_resolved,
        resolved_by_resolver=resolved_by_resolver,
        refused_by_resolver=refused_by_resolver,
        unresolved_record_ids=tuple(unresolved),
    )


__all__ = [
    "FullTextRunSummary",
    "already_resolved_record_ids",
    "record_fulltext_attempt",
    "record_fulltext_sections",
    "run_fulltext_resolution",
]
