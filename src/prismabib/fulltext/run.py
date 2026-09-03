"""Orchestrate the Stage 6 resolver chain over a project (ADR 0019, Decision 0).

:func:`run_fulltext_resolution` is what ``prismabib fulltext`` (see
:mod:`prismabib.cli`) actually calls: resolve which records to target, look up
their DOIs from Layer 1 (read-only), then hand the resolver chain and the
not-already-resolved subset to :func:`~prismabib.fulltext.capture.capture_fulltext`,
which writes and seals a Layer 0 run.

**This module opens no read/write Layer 1 connection at all.** Earlier, it did
-- a documented exception to :mod:`prismabib.store.db`'s "write connections are
for ``build_store`` alone" convention -- and wrote ``fulltext_assets``/
``fulltext_sections`` directly. That made those two tables the first Layer 1
tables that were not a function of Layer 0: measured, ``build_store(rebuild=True)``
after a resolution run silently discarded every asset and every recorded
refusal, falsifying S03-AC3. Full-text resolution is now a Layer 0 capture like
any other (:mod:`prismabib.fulltext.capture`); ``fulltext_assets``/
``fulltext_sections`` are rebuilt from its sealed runs by
:mod:`prismabib.store.load`, exactly as ``abstract_runs``/
``record_subject_area_coverage`` are rebuilt from sealed abstract runs (ADR
0018). A caller who wants the results reflected in Layer 1 runs
``prismabib build --rebuild`` afterward -- the same two-step shape
``prismabib enrich`` already has.

The one Layer 1 access this module still performs is a **read-only** DOI
lookup for the targeted records, via :func:`prismabib.store.db.connect`
(``read_only=True``). That incidentally closes a second gap: opening
read-only is what makes :mod:`prismabib.store.db`'s stale-schema guard run at
all (it is a no-op on a read/write connection, by that module's own
docstring) -- so a store built before ``fulltext_assets``/``fulltext_sections``
existed (pre-v0.16) now fails with the guard's actionable
``prismabib build <slug> --rebuild`` message instead of a raw
``duckdb.CatalogException``, on every path through this function, including an
explicit ``record_ids`` call that never touches
:func:`~prismabib.prisma.engine.manual_abstract_set` at all.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from prismabib.config import FullTextSettings, Settings
from prismabib.errors import ValidationError
from prismabib.fulltext.capture import already_resolved_record_ids, capture_fulltext
from prismabib.fulltext.resolve import default_chain
from prismabib.prisma.engine import manual_abstract_set
from prismabib.store.db import connect

if TYPE_CHECKING:
    from prismabib.project import Project

__all__ = ["FullTextRunSummary", "run_fulltext_resolution"]


class FullTextRunSummary(BaseModel):
    """What one ``run_fulltext_resolution`` call did, for the CLI to report.

    Attributes:
        records_considered: How many records were targeted in total
            (before excluding already-resolved ones).
        records_already_resolved: How many of ``records_considered`` already
            had full text from an earlier sealed run and were skipped without
            re-spending anything. **Measured, not derived.**
            ``records_considered - records_attempted`` looks like the same
            number and is not: a ``budget``-capped or resumed call also
            attempts fewer than it considers, so the subtraction reports
            records as already having full text that have never been fetched.
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
            chain was exhausted with no asset and no unhandled failure --
            candidates for a human to review and, only after confirming no
            institutional route exists, mark ``INACCESSIBLE`` during
            full-text screening. This list is not itself a decision; see
            the module docstring of :mod:`prismabib.fulltext.resolve`.
        failed_record_ids: Records attempted this call for which a resolver
            raised something other than an entitlement refusal partway
            through the chain (an upstream outage, a network timeout, ...).
            Distinct from ``unresolved_record_ids``: whatever the chain
            learned before the failure (e.g. an earlier resolver's refusal)
            is still recorded, but later resolvers were never tried for
            this record, and a later call re-attempts it from the start.
        sealed: Whether the underlying Layer 0 run finished this call
            (``True`` -- every pending record was attempted or exhausted,
            and ``manifest.json`` was written) or was left unsealed because
            ``budget`` stopped it short. ``True`` (trivially) when nothing
            was pending at all.
    """

    model_config = ConfigDict(frozen=True)

    records_considered: int
    records_already_resolved: int
    records_attempted: int
    records_resolved: int
    resolved_by_resolver: dict[str, int]
    refused_by_resolver: dict[str, int]
    unresolved_record_ids: tuple[str, ...]
    failed_record_ids: tuple[str, ...]
    sealed: bool


def run_fulltext_resolution(
    project: Project,
    *,
    record_ids: Sequence[str] | None = None,
    budget: int | None = None,
    settings: Settings | FullTextSettings | None = None,
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
            from. Defaults to ``FullTextSettings()`` (via
            :func:`~prismabib.fulltext.resolve.default_chain`) when
            omitted. Exposed primarily so a test can inject one without
            touching the real environment.

    Returns:
        A :class:`FullTextRunSummary` of what this call did. Nothing is
        written to Layer 1 by this call -- run ``prismabib build --rebuild``
        afterward to fold the sealed run into ``fulltext_assets``/
        ``fulltext_sections`` (see the module docstring).

    Raises:
        ValidationError: If ``budget`` is not strictly positive, or if
            there is nothing to resolve (an explicitly empty ``record_ids``,
            or an empty ``manual_abstract_set``).
        ConfigError: If ``project.criteria.yaml`` cannot be read, or if
            :class:`~prismabib.config.FullTextSettings` cannot be read.
            It declares no required secret -- resolving full text needs an
            Elsevier key, an Unpaywall contact, or neither (the chain then
            degrades to the manual drop), and never needs ``SCOPUS_API_KEY``.
            Requiring the Scopus key here once made ``prismabib fulltext``
            fail for a reviewer with their own PDFs and no subscription.
        StoreError: If no Layer 1 store exists yet for ``project``, or if
            one exists but predates ``fulltext_assets``/``fulltext_sections``
            (a pre-v0.16 store) -- the actionable
            ``prismabib build <slug> --rebuild`` message, not a raw
            ``duckdb.CatalogException``.
        LogError: If the decision log fails to load while computing
            ``manual_abstract_set``.
    """
    if budget is not None and budget < 1:
        raise ValidationError(f"budget must be a positive number of records, got {budget!r}")

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

    # Read-only, and always performed -- even for an explicit `record_ids` call
    # that never touches `manual_abstract_set` -- so a pre-v0.16 store is refused
    # here, with an actionable message, before any resolver runs. See the module
    # docstring.
    connection = connect(project, read_only=True)
    try:
        rows = connection.execute(
            "SELECT record_id, doi FROM records WHERE record_id = ANY(?)",
            [target_ids],
        ).fetchall()
        doi_by_record_id = {str(record_id): doi for record_id, doi in rows}
    finally:
        connection.close()

    already_resolved = already_resolved_record_ids(project.fulltext_dir)
    pending_ids = [record_id for record_id in target_ids if record_id not in already_resolved]

    if not pending_ids:
        return FullTextRunSummary(
            records_considered=len(target_ids),
            records_already_resolved=len(target_ids),
            records_attempted=0,
            records_resolved=0,
            resolved_by_resolver={},
            refused_by_resolver={},
            unresolved_record_ids=(),
            failed_record_ids=(),
            sealed=True,
        )

    with default_chain(project, settings) as resolvers:
        outcome = capture_fulltext(
            project,
            pending_ids=pending_ids,
            doi_by_record_id=doi_by_record_id,
            resolvers=resolvers,
            budget=budget,
        )

    return FullTextRunSummary(
        records_considered=len(target_ids),
        # `len(target_ids) - len(pending_ids)`, computed above: the records a
        # sealed run already resolved. Not `considered - attempted`, which a
        # `budget` cap or a resumed run also shrinks.
        records_already_resolved=len(target_ids) - len(pending_ids),
        records_attempted=outcome.attempted,
        records_resolved=outcome.resolved,
        resolved_by_resolver=outcome.resolved_by_resolver,
        refused_by_resolver=outcome.refused_by_resolver,
        unresolved_record_ids=outcome.unresolved_record_ids,
        failed_record_ids=outcome.failed_record_ids,
        sealed=outcome.sealed,
    )
