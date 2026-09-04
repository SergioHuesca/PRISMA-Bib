"""Citation statistics, including h-index, and by-year averages (BUILD_PLAN Stage 7).

``Corpus.citations`` (Stage 3) carries no ``stage`` parameter -- it answers
over every record the store holds, at every snapshot -- so every function
here reads :meth:`~prismabib.store.load.Corpus.records` for the requested
``stage`` first and restricts the citation rows to that record set. This is
also why every result here carries a citation snapshot in its
:class:`~prismabib.bibliometrics.base.Provenance` (ADR 0022 Decision 1):
these are exactly the functions that read citations.

``report/numbers.py::_citation_numbers`` delegates to
:func:`citation_statistics` rather than re-querying ``citation_snapshots``
itself -- see that module for the re-pointing.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from datetime import datetime

import polars as pl

from prismabib.bibliometrics.base import AnalysisResult, build_provenance
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus


def _h_index(counts: Sequence[int]) -> int:
    """The largest ``h`` such that ``h`` records each have at least ``h`` citations.

    Args:
        counts: Citation counts, any order.

    Returns:
        ``0`` for an empty or all-zero input. Monotone non-decreasing under
        adding a citation to any record, or adding another cited record --
        the classic h-index property, checked directly by
        ``test_hindex__monotone_under_added_citations``.
    """
    ranked = sorted(counts, reverse=True)
    h = 0
    for position, count in enumerate(ranked, start=1):
        if count >= position:
            h = position
        else:
            break
    return h


def _citations_for_stage(
    corpus: Corpus, *, stage: PrismaStage, at: datetime | None
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """``records(stage)`` and the matching subset of ``citations(at)``.

    Args:
        corpus: The corpus to read.
        stage: Which PRISMA-flow set to restrict citations to.
        at: Forwarded to :meth:`~prismabib.store.load.Corpus.citations`.

    Returns:
        ``(records, citations)`` -- ``citations`` filtered to
        ``records``'s ``record_id`` set (``Corpus.citations`` itself carries
        no ``stage`` parameter, see this module's docstring).
    """
    records = corpus.records(stage)
    citations = corpus.citations(at)
    if records.height and "record_id" in records.columns:
        record_ids = set(records.get_column("record_id").to_list())
        citations = citations.filter(pl.col("record_id").is_in(record_ids))
    return records, citations


def citation_statistics(
    corpus: Corpus, *, stage: PrismaStage = PrismaStage.INCLUDED, at: datetime | None = None
) -> AnalysisResult:
    """Total, mean, median, max, h-index, and zero-cited share, over the latest snapshot.

    Args:
        corpus: The corpus to read.
        stage: Which PRISMA-flow set to read.
        at: Forwarded to :meth:`~prismabib.store.load.Corpus.citations`;
            ``None`` uses the latest snapshot per record.

    Returns:
        An :class:`~prismabib.bibliometrics.base.AnalysisResult` whose
        ``data`` is one row: ``records_with_a_snapshot``, ``total``,
        ``mean``, ``median``, ``max``, ``h_index``, ``zero_cited_share``.
        Every field is ``0``/``0.0`` when no record in ``stage`` has a
        citation snapshot -- see ADR 0022 Decision 10; unlike
        :func:`~prismabib.bibliometrics.trends.cagr`, an all-zero citation
        table is a real, reportable state, not a degenerate one.
    """
    records, citations = _citations_for_stage(corpus, stage=stage, at=at)
    counts = citations.get_column("cited_by_count").to_list() if citations.height else []

    if counts:
        total = sum(counts)
        n_with_snapshot = len(counts)
        mean = total / n_with_snapshot
        median = float(statistics.median(counts))
        maximum = max(counts)
        h_index = _h_index(counts)
        zero_cited_share = sum(1 for count in counts if count == 0) / n_with_snapshot
    else:
        total, n_with_snapshot, mean, median, maximum, h_index, zero_cited_share = (
            0,
            0,
            0.0,
            0.0,
            0,
            0,
            0.0,
        )

    data = pl.DataFrame(
        {
            "records_with_a_snapshot": [n_with_snapshot],
            "total": [total],
            "mean": [mean],
            "median": [median],
            "max": [maximum],
            "h_index": [h_index],
            "zero_cited_share": [zero_cited_share],
        }
    )
    provenance = build_provenance(corpus, stage=stage, records=records, citations=citations)
    return AnalysisResult(data=data, params={}, provenance=provenance)


def citations_by_year(
    corpus: Corpus, *, stage: PrismaStage = PrismaStage.INCLUDED, at: datetime | None = None
) -> AnalysisResult:
    """Mean citations per record, by publication year.

    Args:
        corpus: The corpus to read.
        stage: Which PRISMA-flow set to read.
        at: Forwarded to :meth:`~prismabib.store.load.Corpus.citations`;
            ``None`` uses the latest snapshot per record.

    Returns:
        An :class:`~prismabib.bibliometrics.base.AnalysisResult` whose
        ``data`` is ``year``, ``mean_citations``, ``records``, sorted by
        ``year`` ascending. A record with no ``year`` or no citation
        snapshot contributes to neither -- see
        :func:`~prismabib.bibliometrics.trends.annual_counts`'s docstring
        for the same judgement made there.
    """
    records, citations = _citations_for_stage(corpus, stage=stage, at=at)
    dated = records.select(["record_id", "year"]).filter(pl.col("year").is_not_null())
    if dated.height == 0 or citations.height == 0:
        # `Corpus.records`/`Corpus.citations` both build their frame from
        # `fetchall()` with no rows to infer a dtype from when nothing
        # matches, so an inner join against an empty side would hit
        # `SchemaError` rather than correctly producing zero rows -- see
        # `bibliometrics/geography.py::_record_country_membership`'s
        # docstring for the same trap. An inner join against either side
        # empty is zero rows regardless, so short-circuit instead.
        joined = pl.DataFrame(
            schema={"record_id": pl.Utf8, "year": pl.Int64, "cited_by_count": pl.Int64}
        )
    else:
        joined = dated.join(
            citations.select(["record_id", "cited_by_count"]), on="record_id", how="inner"
        )

    if joined.height == 0:
        data = pl.DataFrame(
            schema={"year": pl.Int64, "mean_citations": pl.Float64, "records": pl.Int64}
        )
    else:
        data = (
            joined.group_by("year")
            .agg(
                [
                    pl.col("cited_by_count").mean().alias("mean_citations"),
                    pl.len().alias("records"),
                ]
            )
            .with_columns(pl.col("year").cast(pl.Int64), pl.col("records").cast(pl.Int64))
            .sort("year")
        )

    provenance = build_provenance(corpus, stage=stage, records=records, citations=citations)
    return AnalysisResult(data=data, params={}, provenance=provenance)


__all__ = ["citation_statistics", "citations_by_year"]
