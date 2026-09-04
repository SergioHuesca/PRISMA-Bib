"""Annual publication counts and CAGR (BUILD_PLAN Stage 7, ADR 0022 Decisions 2-3)."""

from __future__ import annotations

from typing import Any

import polars as pl

from prismabib.bibliometrics.base import AnalysisResult, build_provenance, first_incomplete_year
from prismabib.errors import AnalysisError
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus

_EMPTY_ANNUAL_SCHEMA = {"year": pl.Int64, "count": pl.Int64}


def _annual_counts_frame(records: pl.DataFrame) -> pl.DataFrame:
    """``year``, ``count`` -- one row per publication year with at least one record.

    Args:
        records: A ``Corpus.records(...)``-shaped frame; must carry a
            nullable ``year`` column.

    Returns:
        Sorted by ``year`` ascending (a total order: ``year`` is the group
        key). Records with a ``null`` year (Layer 1 does not require one)
        are excluded -- see :func:`annual_counts`'s docstring for why a
        phantom year is not the alternative.
    """
    with_year = records.filter(pl.col("year").is_not_null())
    if with_year.height == 0:
        return pl.DataFrame(schema=_EMPTY_ANNUAL_SCHEMA)
    return (
        with_year.group_by("year")
        .agg(pl.len().alias("count"))
        .with_columns(pl.col("year").cast(pl.Int64), pl.col("count").cast(pl.Int64))
        .sort("year")
    )


def _mark_partial_years(frame: pl.DataFrame, boundary: int | None) -> pl.DataFrame:
    """Append an ``is_partial`` column: ``True`` for every ``year >= boundary``.

    ADR 0022 Decision 3b: a value excluded from one number (:func:`cagr`)
    because it is incomplete must be marked as incomplete everywhere else it
    is shown, not dropped. Kept separate from :func:`annual_counts` so the
    marking rule is testable against a ``boundary`` the caller states
    directly -- independent of :func:`first_incomplete_year` and therefore
    of any particular corpus or the wall clock, which is exactly the
    property BUILD_PLAN's "no test may derive its expectation from the
    implementation under test" requires here.

    Args:
        frame: A :func:`_annual_counts_frame`-shaped ``year``, ``count``
            frame.
        boundary: :func:`first_incomplete_year`'s result. ``None`` -- no
            sealed run at all -- marks nothing as partial: there is no
            known boundary to derive one from.

    Returns:
        ``frame`` with a ``Boolean`` ``is_partial`` column appended, never
        ``null`` (including on an empty ``frame``, so the schema is stable
        whether or not any row exists).
    """
    if boundary is None:
        return frame.with_columns(pl.lit(False).alias("is_partial"))
    return frame.with_columns((pl.col("year") >= boundary).alias("is_partial"))


def annual_counts(corpus: Corpus, *, stage: PrismaStage = PrismaStage.INCLUDED) -> AnalysisResult:
    """One row per publication year with a record, ordered by year.

    Args:
        corpus: The corpus to read.
        stage: Which PRISMA-flow set to count over.

    Returns:
        An :class:`~prismabib.bibliometrics.base.AnalysisResult` whose
        ``data`` is ``year``, ``count``, ``is_partial`` -- ``True`` for
        every ``year >= first_incomplete_year(corpus)`` (ADR 0022 Decision
        3b). This is the frame behind the publication-trend figure; Stage
        9's figure functions are forbidden to compute, so a partial year
        must be marked *here*, not dropped and not left for a caption
        nobody plots to explain. ``params["first_incomplete_year"]``
        carries the same boundary into the caption. A record with no
        ``year`` still counts toward ``n`` in the caption
        (``provenance.corpus_size``) but contributes no row here --
        reporting it under a made-up year would be a worse dishonesty than
        an undercount a reader can see is smaller than ``n``.
    """
    records = corpus.records(stage)
    boundary = first_incomplete_year(corpus)
    data = _mark_partial_years(_annual_counts_frame(records), boundary)
    provenance = build_provenance(corpus, stage=stage, records=records)
    return AnalysisResult(
        data=data, params={"first_incomplete_year": boundary}, provenance=provenance
    )


def _cagr_from_series(v_start: float, v_end: float, span_years: int) -> float:
    """The compound annual growth rate formula alone (ADR 0022 Decision 3), no I/O.

    Kept separate from :func:`cagr` so the "known geometric series" unit
    test can check the arithmetic against an exact analytic value without
    needing publication counts that happen to fall on an exact 30% curve.

    Args:
        v_start: The first year's value. Never ``0`` -- callers raise
            before reaching here.
        v_end: The last year's value.
        span_years: ``year_end - year_start``. Never ``0`` -- callers raise
            before reaching here.

    Returns:
        ``(v_end / v_start) ** (1 / span_years) - 1``.
    """
    return float((v_end / v_start) ** (1 / span_years) - 1)


def _cagr_bounds(series: pl.DataFrame) -> tuple[int, int, int, int, int]:
    """Extract and validate ``(year_start, year_end, v_start, v_end, span_years)``.

    Separate from :func:`cagr` so the degenerate-input table ADR 0022
    Decision 3 specifies is testable directly against a hand-built
    ``series`` -- in particular ``v_start == 0``, which
    :func:`_annual_counts_frame` can never itself produce (every row it
    emits comes from at least one real record, so the earliest year in a
    *derived* series always has ``count >= 1``). The check still belongs
    here, not removed, because BUILD_PLAN's table names it as a case any
    ``(year, count)`` series -- hand-built or derived -- must refuse.

    Args:
        series: A ``year``, ``count`` frame, already restricted to complete
            years, sorted by ``year`` ascending.

    Returns:
        ``(year_start, year_end, v_start, v_end, span_years)``.

    Raises:
        AnalysisError: Fewer than two rows (which also covers
            ``span_years == 0``: two *distinct* years can never span zero);
            or ``v_start == 0`` (the ratio would be infinite).
    """
    if series.height < 2:
        raise AnalysisError(
            f"cagr needs at least two distinct years of data after partial-year "
            f"exclusion; got {series.height}. A single year (or none) has no "
            "growth rate to compute."
        )
    year_start = int(series.item(0, "year"))
    year_end = int(series.item(series.height - 1, "year"))
    v_start = int(series.item(0, "count"))
    v_end = int(series.item(series.height - 1, "count"))
    if v_start == 0:
        raise AnalysisError(
            f"cagr's start value is 0 (year {year_start}); v_end / v_start is "
            "infinite, and 'infinite growth' is not a finding."
        )
    return year_start, year_end, v_start, v_end, year_end - year_start


def cagr(
    corpus: Corpus,
    *,
    stage: PrismaStage = PrismaStage.INCLUDED,
    include_partial_final_year: bool = False,
) -> AnalysisResult:
    """Compound annual growth rate of annual publication counts.

    ADR 0022 Decision 3: computed over complete years only by default --
    every year ``>= first_incomplete_year(corpus)`` (Decision 2) is
    excluded, because including a part-year understates growth silently.

    Args:
        corpus: The corpus to read.
        stage: Which PRISMA-flow set to count over.
        include_partial_final_year: Override the exclusion and include the
            corpus's own partial/ahead-of-print final year. Recorded in
            ``params`` (and therefore the caption) either way.

    Returns:
        An :class:`~prismabib.bibliometrics.base.AnalysisResult` whose
        ``data`` is one row: ``cagr``, ``v_start``, ``v_end``,
        ``year_start``, ``year_end``, ``span_years``,
        ``partial_years_excluded`` (a count, ``0`` when
        ``include_partial_final_year`` is set or nothing was excluded).

    Raises:
        AnalysisError: See :func:`_cagr_bounds`.
    """
    records = corpus.records(stage)
    series = _annual_counts_frame(records)
    boundary = first_incomplete_year(corpus)

    partial_years_excluded = 0
    if not include_partial_final_year and boundary is not None:
        complete = series.filter(pl.col("year") < boundary)
        partial_years_excluded = series.height - complete.height
        series = complete

    year_start, year_end, v_start, v_end, span_years = _cagr_bounds(series)
    rate = _cagr_from_series(float(v_start), float(v_end), span_years)
    data = pl.DataFrame(
        {
            "cagr": [rate],
            "v_start": [v_start],
            "v_end": [v_end],
            "year_start": [year_start],
            "year_end": [year_end],
            "span_years": [span_years],
            "partial_years_excluded": [partial_years_excluded],
        }
    )
    # ADR 0022 Decision 3: "a bare growth rate is not checkable" is not
    # satisfied by putting the anchor in `data` alone -- `caption()` only
    # ever renders `params`, so the anchor must be duplicated into `params`
    # for the sentence that actually travels into a manuscript to state it.
    params: dict[str, Any] = {
        "include_partial_final_year": include_partial_final_year,
        "year_start": year_start,
        "year_end": year_end,
        "span_years": span_years,
        "v_start": v_start,
        "v_end": v_end,
    }
    provenance = build_provenance(corpus, stage=stage, records=records)
    return AnalysisResult(data=data, params=params, provenance=provenance)


__all__ = ["annual_counts", "cagr"]
