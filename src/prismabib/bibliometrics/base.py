"""The ``AnalysisResult``/``Provenance`` contract (ADR 0022, BUILD_PLAN Stage 7).

Every public function in :mod:`prismabib.bibliometrics`'s six analysis
modules (``trends``, ``geography``, ``venues``, ``citations``, ``keywords``,
``network``) returns an :class:`AnalysisResult` -- never a bare
:class:`polars.DataFrame` or scalar. That is what BUILD_PLAN's Stage 7
acceptance criteria and ADR 0022's Constraints mean by "every public
function in ``bibliometrics/``": the functions that compute one of "every
quantitative finding" the stage's goal names. This module's *own* public
surface (:class:`AnalysisResult`, :class:`Provenance`, and
:func:`build_provenance`/:func:`first_incomplete_year`, which every analysis
module calls to build its result rather than deriving provenance by hand) is
the contract those functions are built *from*, not one of them -- neither
dataclass claims to be a quantitative finding, and the introspective sweep
test (``test_all_analyses__return_type__is_analysis_result``) is scoped to
the six analysis modules' own ``__all__`` accordingly. Helpers that cannot
sensibly return an ``AnalysisResult`` at all (string normalisation, file
export) are named with a leading underscore throughout this package, kept
out of every module's ``__all__``, and tested directly by importing them by
name -- the same convention this codebase already uses for
``report/numbers.py::_venue_numbers``.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import polars as pl

from prismabib.errors import AnalysisError
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus


def _max_datetime(series: pl.Series) -> datetime | None:
    """The maximum value of a ``Datetime``-typed :class:`polars.Series`, safely typed.

    :meth:`polars.Series.max` is typed to return the union of every scalar
    type polars can hold, since the method itself has no way to know a
    caller restricted ``series`` to a ``Datetime`` column. Every caller here
    does, so this narrows the result explicitly rather than letting
    :class:`typing.Any` leak into :class:`Provenance` -- exactly the
    unchecked-value shape ADR 0022 exists to keep out of a published number.

    Args:
        series: A polars Series of dtype ``Datetime``.

    Returns:
        The maximum value, or ``None`` for an empty series.

    Raises:
        AnalysisError: ``series`` is not empty but its maximum is not a
            :class:`datetime.datetime` -- a Layer 1 schema violation
            (``runs.started_at``/``citation_snapshots.retrieved_at`` are
            both declared ``TIMESTAMP``), not a case any caller here should
            silently coerce.
    """
    value = series.max()
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise AnalysisError(
            f"expected a datetime column, got {type(value).__name__} -- "
            f"{series.name!r} should be TIMESTAMP in schema.sql"
        )
    return value


@dataclass(frozen=True)
class Provenance:
    """The corpus and the knobs that produced one :class:`AnalysisResult` (ADR 0022 Decision 1).

    Attributes:
        corpus_size: ``n``, the number of records in ``stage`` that the
            analysis actually read (``records.height``, not a separate
            count -- so this can never disagree with the frame it describes).
        stage: Which named PRISMA-flow set ``corpus_size`` is over.
        retrieved_at: ``max(runs.started_at)`` across every sealed search
            run the project has -- the corpus's own as-at date (ADR 0022
            Decision 2), independent of ``stage``: a PRISMA subset does not
            change when the underlying capture happened. ``None`` only when
            the project has no sealed run at all.
        run_ids: Every sealed search run that contributed at least one
            record to this particular result, sorted. Plural because a
            corpus routinely has more than one (ADR 0022 Decision 1) -- a
            singular field would have to name one run as the source of a
            number computed over several.
        criteria_versions: The distinct ``criteria_version`` values recorded
            against ``run_ids``, sorted.
        citation_snapshot: The latest ``retrieved_at`` among the citation
            rows this analysis actually read, or ``None`` for an analysis
            that never called :meth:`~prismabib.store.load.Corpus.citations`
            at all (ADR 0022 Decision 1) -- a keyword table must not claim a
            citation snapshot it never read.
        citation_snapshot_is_uniform: ``False`` when the citation rows this
            analysis read disagree about ``retrieved_at`` -- changes the
            caption's wording, not just its data (ADR 0022 Decision 1).
            Meaningless (and left ``True``) when ``citation_snapshot`` is
            ``None``.
    """

    corpus_size: int
    stage: PrismaStage
    retrieved_at: datetime | None
    run_ids: tuple[str, ...]
    criteria_versions: tuple[str, ...]
    citation_snapshot: datetime | None = None
    citation_snapshot_is_uniform: bool = True


def _format_param(value: Any) -> str:
    """Render one ``params`` value for the caption, not for JSON.

    Args:
        value: A scalar from :attr:`AnalysisResult.params`.

    Returns:
        ``"true"``/``"false"`` for a bool (matching
        ``report/tables.py::_cell``'s convention, which a caption sits
        beside), a fixed-precision float, or ``str(value)`` otherwise.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.6g}"
    return str(value)


@dataclass(frozen=True)
class AnalysisResult:
    """One computed bibliometric finding: its data, its knobs, its provenance.

    Attributes:
        data: The figure-ready result, one :class:`polars.DataFrame` per
            finding. Every column ordering is total (ADR 0022 Decision 8) --
            no analysis module leaves a tie for the query engine's group-by
            order to break.
        params: Every knob that affected ``data`` -- counting method,
            ``min_occurrence``, ``top_n``, clustering ``resolution`` and
            ``seed``, wherever the analysis has one. Not restricted to JSON
            scalars the way ``report/numbers.py``'s ``numbers_map`` is: a
            network's community assignment is itself a legitimate ``params``
            entry (ADR 0022 Decision 7) and has no sensible scalar form.
            :meth:`caption` renders only the scalar entries -- a caption is
            a sentence, not a dump of a community assignment -- but every
            entry, scalar or not, is part of what
            ``test_all_analyses__recomputed_twice__is_bit_identical``
            compares.
        provenance: See :class:`Provenance`.
    """

    data: pl.DataFrame
    params: Mapping[str, Any]
    provenance: Provenance

    def caption(self) -> str:
        """A one-sentence, auto-generated caption -- the manuscript's honesty mechanism.

        BUILD_PLAN: "``caption()`` is what makes the manuscript honest:
        figure captions are generated, not typed." Always states ``n`` and
        the stage it is over, the corpus's own as-at date (ADR 0022
        Decision 2), a citation snapshot clause when (and only when) this
        result actually read citations (Decision 1), and every scalar
        ``params`` entry -- which is what makes
        ``test_keywords__min_occurrence_change__changes_params_and_caption``
        (S07-AC4) true for every analysis generically, rather than each
        module re-deriving its own caption wording.

        Returns:
            The caption text, ending in a period.
        """
        provenance = self.provenance
        parts = [f"n = {provenance.corpus_size} ({provenance.stage.value})"]
        if provenance.retrieved_at is not None:
            parts.append(f"corpus as at {provenance.retrieved_at.date().isoformat()}")
        else:
            parts.append("corpus as at an unknown date (no sealed search runs)")
        if provenance.citation_snapshot is not None:
            snapshot_date = provenance.citation_snapshot.date().isoformat()
            if provenance.citation_snapshot_is_uniform:
                parts.append(f"citations as at {snapshot_date}")
            else:
                parts.append(f"latest citation snapshot per record, most recent {snapshot_date}")
        scalar_params = {
            key: value
            for key, value in self.params.items()
            if isinstance(value, str | int | float | bool)
        }
        if scalar_params:
            parts.append(
                ", ".join(
                    f"{key}={_format_param(scalar_params[key])}" for key in sorted(scalar_params)
                )
            )
        return "; ".join(parts) + "."


def _runs_table(corpus: Corpus) -> pl.DataFrame:
    """Read ``runs.run_id``/``started_at``/``criteria_version``, unfiltered by PRISMA stage.

    ``runs`` is a Layer 0 provenance catalogue, not a PRISMA-flow record
    set -- it carries no per-record membership to delegate to
    :meth:`~prismabib.store.load.Corpus._prisma_stage_record_ids`, so
    reading it directly here does not duplicate that method's
    one-implementation guarantee (ADR 0022 Decision 9 is about
    ``records``/``keywords``-shaped *stage filtering*, which this is not).
    :class:`~prismabib.store.load.Corpus` deliberately gains no fourth
    *public* accessor for it (Decision 9's own Consequence 3: "``Corpus`` is
    three methods wider" stays true of the public contract), so the read
    lives on :meth:`~prismabib.store.load.Corpus._runs` -- a private method
    on the class that owns the connection, rather than this module reaching
    across a package boundary into ``corpus._connection``.

    An earlier version of this function did reach in, justified by "the same
    boundary ``report/tables.py``/``report/numbers.py`` already cross with
    their own raw SQL". That justification was stale within its own commit:
    ADR 0022 Decision 5 wires both of those modules *onto* ``Corpus`` for
    venues and citations, so they are now the precedent against reaching in,
    not for it.

    Args:
        corpus: The corpus to read.

    Returns:
        Every row of ``runs``, unordered (every use here is an aggregate --
        max, distinct, filter -- that does not depend on row order).
    """
    return corpus._runs()


def build_provenance(
    corpus: Corpus,
    *,
    stage: PrismaStage,
    records: pl.DataFrame,
    citations: pl.DataFrame | None = None,
) -> Provenance:
    """Assemble the :class:`Provenance` for one analysis over ``records``.

    Args:
        corpus: The corpus the analysis read.
        stage: The PRISMA-flow set ``records`` was drawn from.
        records: The record-level frame the analysis computed over --
            typically ``corpus.records(stage)`` or a frame derived from it.
            Must carry a ``record_id`` column; ``corpus_size`` is
            ``records.height``. A ``run_id`` column (present on every
            ``Corpus.records()`` frame) narrows ``run_ids``/
            ``criteria_versions`` to the runs that actually contributed a
            record here, rather than every run the project has ever sealed.
        citations: The citation frame the analysis read, if any. ``None`` --
            not an empty frame -- for an analysis that never called
            :meth:`~prismabib.store.load.Corpus.citations` at all (ADR 0022
            Decision 1): a keyword table must not claim a citation snapshot
            it never read. An empty, non-``None`` frame (an analysis that
            did read citations but got no matching snapshot rows) yields
            ``citation_snapshot=None`` too -- there is no snapshot date to
            report either way, and the two cases are indistinguishable to a
            caption once there is no date.

    Returns:
        The assembled :class:`Provenance`.
    """
    runs = _runs_table(corpus)
    retrieved_at = _max_datetime(runs.get_column("started_at")) if runs.height else None

    if records.height and "run_id" in records.columns:
        run_ids = tuple(
            sorted(
                value
                for value in records.get_column("run_id").unique().to_list()
                if value is not None
            )
        )
    else:
        run_ids = ()

    if run_ids and runs.height:
        relevant = runs.filter(pl.col("run_id").is_in(run_ids))
        criteria_versions = tuple(
            sorted(
                value
                for value in relevant.get_column("criteria_version").unique().to_list()
                if value is not None
            )
        )
    else:
        criteria_versions = ()

    citation_snapshot: datetime | None = None
    citation_snapshot_is_uniform = True
    if citations is not None and citations.height:
        snapshots = citations.get_column("retrieved_at")
        citation_snapshot = _max_datetime(snapshots)
        citation_snapshot_is_uniform = snapshots.n_unique() <= 1

    return Provenance(
        corpus_size=records.height,
        stage=stage,
        retrieved_at=retrieved_at,
        run_ids=run_ids,
        criteria_versions=criteria_versions,
        citation_snapshot=citation_snapshot,
        citation_snapshot_is_uniform=citation_snapshot_is_uniform,
    )


def first_incomplete_year(corpus: Corpus) -> int | None:
    """The first publication year this corpus's capture cannot have observed in full.

    ADR 0022 Decision 2: ``year(max(runs.started_at))``, *never* the wall
    clock -- a year is complete for this corpus only if the whole year had
    elapsed when the corpus was retrieved, and "when the corpus was
    retrieved" is a fact about ``runs``, not about whenever this function
    happens to run. This is the one boundary every partial-year exclusion or
    marking (:func:`prismabib.bibliometrics.trends.cagr`'s exclusion,
    :func:`prismabib.bibliometrics.trends.annual_counts`'s ``is_partial``
    column -- ADR 0022 Decision 3b) must derive from; no module under
    ``bibliometrics/`` may call ``datetime.now()``, ``date.today()`` or
    ``time.time()`` (enforced by a source-scan test).

    Args:
        corpus: The corpus to read.

    Returns:
        The boundary year, or ``None`` when the project has no sealed run
        at all (nothing to derive a boundary from).
    """
    runs = _runs_table(corpus)
    if not runs.height:
        return None
    max_started = _max_datetime(runs.get_column("started_at"))
    if max_started is None:
        return None
    return int(max_started.year)


def _serialise(result: AnalysisResult) -> tuple[bytes, bytes, bytes]:
    """The bytes two independently-computed :class:`AnalysisResult`\\ s must agree on.

    ADR 0022 Decision 8: "bit-identical" means the *serialised* form, not
    two in-process :class:`polars.DataFrame` objects compared for equality
    -- that would pass while the CSV a reader actually receives differed in
    float formatting or row order.

    Args:
        result: The result to serialise.

    Returns:
        ``(data_csv_bytes, params_json_bytes, caption_bytes)`` -- ``data``
        via polars' own CSV writer (the same bytes
        ``report/tables.py::to_csv`` would eventually emit for a table built
        from this frame), ``params`` as canonical JSON (sorted keys), so two
        dicts built in different insertion orders serialise identically, and
        the rendered ``caption()``.

        The caption is included because it is the artefact a reader
        actually receives, and nothing else here covers it: ``provenance``
        is not serialised, so a non-deterministic ``run_ids`` or
        ``criteria_versions`` ordering would produce two different captions
        while ``data`` and ``params`` compared equal. Both are
        ``tuple(sorted(...))`` today, so the invariant holds -- this makes
        it *checked* rather than merely true.
    """
    data_bytes = result.data.write_csv().encode("utf-8")
    params_bytes = json.dumps(dict(result.params), sort_keys=True, default=str).encode("utf-8")
    return data_bytes, params_bytes, result.caption().encode("utf-8")


__all__ = ["AnalysisResult", "Provenance", "build_provenance", "first_incomplete_year"]
