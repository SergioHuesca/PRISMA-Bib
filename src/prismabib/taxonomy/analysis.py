"""Taxonomy results reshaped as :class:`~prismabib.bibliometrics.base.AnalysisResult` (ADR 0025).

BUILD_PLAN §Stage 9's figure 7 ("taxonomy distribution, one panel per
dimension, unit stated in caption") and figure 8 ("taxonomy evolution over
time") both need a taxonomy result in the same shape every bibliometric
figure already consumes. Neither existed before this module:
:func:`prismabib.taxonomy.coder.distribution` returns a
:class:`~prismabib.taxonomy.coder.Distribution`, which is the right shape
for its own sum-to-``|C|`` guard (ADR 0023 Decision 4) but is not what a
figure function takes, and *evolution over time* had no implementation at
all. ADR 0025 Decision 2 requires both, done here (Stage 8's own package)
rather than in ``viz/figures.py`` -- the rule that module's AST scan
enforces is "figures do not compute", and building a per-year breakdown
from :class:`~prismabib.taxonomy.coder.CodingResult` is exactly the kind of
computation that belongs behind an :class:`~prismabib.bibliometrics.base.AnalysisResult`
boundary, not inside a figure.

**Why this reaches into ``bibliometrics.base``.** :class:`~prismabib.bibliometrics.base.AnalysisResult`/
:func:`~prismabib.bibliometrics.base.build_provenance` are Stage 7's
contract, and Stage 8 (this package) is implemented after Stage 7 -- the
same downward direction :mod:`prismabib.report.numbers` already depends in
(``from prismabib.bibliometrics.citations import citation_statistics``),
so this is precedented, not a new dependency edge between packages that
otherwise do not know about each other.

Both functions here are scoped to **one dimension**, matching
:func:`~prismabib.taxonomy.coder.distribution`'s own signature 1:1 rather
than inventing a second, multi-dimension long format: a dimension's caption
states *its own* counting unit (ADR 0023 Decision 4), and merging several
dimensions into one frame would blur that. Figure 7/8, which each need
*every* declared dimension on one canvas, take a sequence of these results
-- one per dimension -- rather than a single merged one; see
``viz/figures.py`` for why that is a deliberate, disclosed exception to
"every figure function takes an AnalysisResult" (BUILD_PLAN's general
phrasing, stated for the eight figures where one function/one
:class:`~prismabib.bibliometrics.base.AnalysisResult` is unambiguous).
"""

from __future__ import annotations

from typing import Any

import polars as pl

from prismabib.bibliometrics.base import AnalysisResult, build_provenance
from prismabib.store.load import Corpus
from prismabib.taxonomy.coder import CodingResult, distribution
from prismabib.taxonomy.schema import CountingUnit, TaxonomySchema

_EMPTY_DISTRIBUTION_SCHEMA = {"category": pl.Utf8, "count": pl.Int64}
_EMPTY_EVOLUTION_SCHEMA = {"year": pl.Int64, "category": pl.Utf8, "count": pl.Int64}


def dimension_distribution(
    corpus: Corpus,
    result: CodingResult,
    schema: TaxonomySchema,
    dimension_id: str,
    counting_unit: CountingUnit,
) -> AnalysisResult:
    """One dimension's coded distribution, as an :class:`AnalysisResult` (BUILD_PLAN figure 7).

    Wraps :func:`~prismabib.taxonomy.coder.distribution` -- which still does
    every bit of counting and the sum-to-``|C|`` guard (ADR 0023 Decision 4)
    -- and carries its answer across the boundary a figure function can
    read without computing.

    Args:
        corpus: The corpus ``result`` was coded over (for provenance only;
            every count comes from ``result``/:func:`~prismabib.taxonomy.coder.distribution`).
        result: A :func:`~prismabib.taxonomy.coder.code` result.
        schema: The project's declared taxonomy dimensions.
        dimension_id: Which dimension to summarise.
        counting_unit: Forwarded to :func:`~prismabib.taxonomy.coder.distribution`.

    Returns:
        An :class:`AnalysisResult` whose ``data`` is ``category``, ``count``
        -- the declared category order :attr:`~prismabib.taxonomy.coder.Distribution.counts`
        is already built in (``dimension.categories`` order, then
        ``"uncoded"``, then ``"reviewed_none"``: a fixed, data-derived order,
        never a ``dict``/``set`` iteration artefact -- see
        :func:`~prismabib.taxonomy.coder.distribution`). ``params`` carries
        ``dimension``, ``counting_unit`` and ``multi_label``, so
        :meth:`~prismabib.bibliometrics.base.AnalysisResult.caption` states
        the unit BUILD_PLAN requires every taxonomy figure to state.

    Raises:
        ValidationError: See :func:`~prismabib.taxonomy.coder.distribution`.
    """
    dimension = schema.dimension(dimension_id)
    dist = distribution(result, schema, dimension_id, counting_unit)

    rows = list(dist.counts.items())
    if rows:
        data = pl.DataFrame(rows, schema=["category", "count"], orient="row").with_columns(
            pl.col("count").cast(pl.Int64)
        )
    else:
        data = pl.DataFrame(schema=_EMPTY_DISTRIBUTION_SCHEMA)

    records = corpus.records(result.stage)
    provenance = build_provenance(corpus, stage=result.stage, records=records)
    params: dict[str, Any] = {
        "dimension": dimension_id,
        "counting_unit": counting_unit.value,
        "multi_label": dimension.multi_label,
    }
    return AnalysisResult(data=data, params=params, provenance=provenance)


def dimension_evolution(
    corpus: Corpus,
    result: CodingResult,
    schema: TaxonomySchema,
    dimension_id: str,
    counting_unit: CountingUnit,
) -> AnalysisResult:
    """One dimension's coded category counts, broken down by publication year (BUILD_PLAN figure 8).

    Did not exist anywhere before this module (ADR 0025 Decision 2's
    Context table). A record with no ``year`` contributes to no row -- see
    :func:`prismabib.bibliometrics.trends.annual_counts`'s docstring for the
    same judgement made there. Deliberately excludes an
    ``"uncoded"``/``"reviewed_none"`` row per year: unlike
    :func:`~prismabib.taxonomy.coder.distribution`, whose job is the
    per-snapshot sum-to-``|C|`` guard (ADR 0023 Decision 4), this is a
    descriptive time series over the *declared* category set -- a reader
    wanting the uncoded share reads it from :func:`dimension_distribution`
    instead, which still runs that guard every time this is called (see
    below).

    Args:
        corpus: The corpus ``result`` was coded over.
        result: A :func:`~prismabib.taxonomy.coder.code` result.
        schema: The project's declared taxonomy dimensions.
        dimension_id: Which dimension to break down.
        counting_unit: Recorded in ``params``/the caption; not used to
            change the counting here (every effective category a record
            carries contributes one count for its year, which is exactly
            what ``counting_unit=assignments`` means -- a
            ``multi_label: false`` dimension counted this way is guarded
            against over-assignment by the :func:`dimension_distribution`
            call below, not re-derived here).

    Returns:
        An :class:`AnalysisResult` whose ``data`` is ``year``, ``category``,
        ``count``, sorted by ``year`` ascending, then ``count`` descending,
        then ``category`` ascending (a total order, mirroring
        :func:`prismabib.bibliometrics.keywords.keyword_evolution`'s own
        convention).

    Raises:
        KeyError: ``dimension_id`` names no declared dimension.
        ValidationError: A record's effective category is not in
            ``dimension_id``'s declared category set -- the same corruption
            :func:`~prismabib.taxonomy.coder.distribution` refuses to sum
            silently (ADR 0023 Decision 4b); triggered here via the
            :func:`dimension_distribution` call this function always makes
            first, so the same guard applies before any year is counted.
    """
    dimension = schema.dimension(dimension_id)
    # Runs `distribution()`'s sum-to-|C| guard before any year is counted --
    # a corrupted override (a category `dimensions.yaml` no longer declares)
    # must not silently appear in a time series any more than in a snapshot.
    dimension_distribution(corpus, result, schema, dimension_id, counting_unit)

    effective = result.effective_categories(dimension_id)
    records = corpus.records(result.stage)
    dated = records.select(["record_id", "year"]).filter(pl.col("year").is_not_null())
    year_by_record: dict[str, int] = dict(
        zip(
            dated.get_column("record_id").to_list(),
            dated.get_column("year").to_list(),
            strict=True,
        )
    )

    counts: dict[tuple[int, str], int] = {}
    for record_id in result.record_ids:
        year = year_by_record.get(record_id)
        if year is None:
            continue
        for category in effective.get(record_id, ()):
            key = (year, category)
            counts[key] = counts.get(key, 0) + 1

    if counts:
        rows = sorted(
            ((year, category, count) for (year, category), count in counts.items()),
            key=lambda row: (row[0], -row[2], row[1]),
        )
        data = pl.DataFrame(rows, schema=["year", "category", "count"], orient="row").with_columns(
            pl.col("year").cast(pl.Int64), pl.col("count").cast(pl.Int64)
        )
    else:
        data = pl.DataFrame(schema=_EMPTY_EVOLUTION_SCHEMA)

    provenance = build_provenance(corpus, stage=result.stage, records=records)
    params: dict[str, Any] = {
        "dimension": dimension_id,
        "counting_unit": counting_unit.value,
        "multi_label": dimension.multi_label,
    }
    return AnalysisResult(data=data, params=params, provenance=provenance)


__all__ = ["dimension_distribution", "dimension_evolution"]
