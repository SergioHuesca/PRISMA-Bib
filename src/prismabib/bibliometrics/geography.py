"""Country counts and per-country citation impact (BUILD_PLAN Stage 7, ADR 0022 Decision 4).

Two counting methods, always named in ``params`` and therefore in the
caption (ADR 0022 Decision 4):

- ``"full"`` (default): each distinct country on a record counts once for
  that record. A record with three distinct affiliation countries
  contributes to all three -- shares are over records, so they sum to
  *more* than 100% on a heavily co-authored corpus, and that is the
  documented, intended behaviour, not a bug to be normalised away.
- ``"fractional"``: each record contributes exactly ``1.0``, split
  ``1/k`` over its ``k`` distinct countries -- shares sum to ``1.0`` within
  float tolerance.

Records with no country-bearing affiliation at all go to an explicit
``"UNK"`` bucket. Both modes account for every record in ``stage``: dropping
the unknowns would make the denominator quietly smaller than the corpus and
every other share quietly larger.
"""

from __future__ import annotations

from datetime import datetime

import polars as pl

from prismabib.bibliometrics.base import AnalysisResult, build_provenance
from prismabib.errors import AnalysisError
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus

#: ADR 0022 Decision 4's two counting methods. A third would need its own
#: ADR the same way a fourth ``Corpus`` accessor would (Decision 9).
COUNTING_METHODS = ("full", "fractional")

_UNKNOWN_COUNTRY = "UNK"

_EMPTY_MEMBERSHIP_SCHEMA = {"record_id": pl.Utf8, "country_iso3": pl.Utf8}
_EMPTY_COUNTRY_SCHEMA = {"country": pl.Utf8, "count": pl.Float64, "share": pl.Float64}
_EMPTY_IMPACT_SCHEMA = {
    "country": pl.Utf8,
    "n_records": pl.Float64,
    "total_citations": pl.Float64,
    "mean_citations": pl.Float64,
}


def _require_valid_method(method: str) -> None:
    if method not in COUNTING_METHODS:
        raise AnalysisError(
            f"geography counting method must be one of {COUNTING_METHODS!r}, got {method!r}"
        )


def _record_country_membership(records: pl.DataFrame, affiliations: pl.DataFrame) -> pl.DataFrame:
    """``record_id``, ``country_iso3`` -- one row per (record, distinct known country).

    A record with no known country at all (no affiliation data, or every
    affiliation's ``country_iso3`` is ``null``) gets exactly one row here,
    with country ``"UNK"`` -- see this module's docstring.

    Args:
        records: A ``Corpus.records(stage)``-shaped frame; only
            ``record_id`` is read.
        affiliations: A ``Corpus.affiliations(stage)``-shaped frame.

    Returns:
        Every ``record_id`` in ``records`` appears at least once.
    """
    if records.height == 0:
        return pl.DataFrame(schema=_EMPTY_MEMBERSHIP_SCHEMA)
    all_ids = records.select("record_id").unique()
    if affiliations.height == 0:
        # `Corpus._query` builds a frame from `fetchall()` with no rows to
        # infer a dtype from, so a *totally* empty `affiliations` (no record
        # in `stage` carries any affiliation row at all -- an entirely
        # ordinary corpus, not a pathological one) comes back with `Null`
        # columns rather than `Utf8`. Joining that against `all_ids` below
        # would raise `SchemaError: str does not match null` instead of
        # correctly producing "every record is UNK" -- which is exactly what
        # every record with no known country is, so short-circuit here
        # rather than let a join discover it.
        return all_ids.with_columns(pl.lit(_UNKNOWN_COUNTRY).alias("country_iso3"))
    known = (
        affiliations.filter(pl.col("country_iso3").is_not_null())
        .select(["record_id", "country_iso3"])
        .unique()
        # A non-empty, ordinary `affiliations` frame whose `country_iso3` is
        # `null` on *every* row (every affiliation exists, none maps to a
        # known country -- exactly the shape `build_store`'s
        # `unmapped_country_values` log anticipates) hits the same
        # `infer_schema_length=None`-scans-every-row trap as the zero-row
        # case above: `country_iso3` comes back typed `Null` rather than
        # `Utf8`, and filtering it to zero rows here does not change that.
        # `pl.concat` below would then raise `SchemaError: type String is
        # incompatible with expected type Null` against `unknown`'s `Utf8`
        # column instead of correctly producing "every record is UNK" -- so
        # cast explicitly rather than let a concat discover it.
        .with_columns(pl.col("country_iso3").cast(pl.Utf8))
    )
    covered_ids = known.select("record_id").unique()
    unknown = all_ids.join(covered_ids, on="record_id", how="anti").with_columns(
        pl.lit(_UNKNOWN_COUNTRY).alias("country_iso3")
    )
    return pl.concat([known, unknown], how="vertical")


def _weighted_membership(membership: pl.DataFrame, method: str) -> pl.DataFrame:
    """``membership`` with a ``weight`` column applying ``method``.

    Args:
        membership: A :func:`_record_country_membership`-shaped frame.
        method: One of :data:`COUNTING_METHODS`, already validated.

    Returns:
        ``full``: every row weighs ``1.0``. ``fractional``: each record's
        rows sum to ``1.0``, split evenly over that record's distinct
        countries (``"UNK"`` counts as one country for a record with no
        known one).
    """
    if method == "full":
        return membership.with_columns(pl.lit(1.0).alias("weight"))
    return membership.with_columns((1.0 / pl.len().over("record_id")).alias("weight"))


def country_counts(
    corpus: Corpus, *, stage: PrismaStage = PrismaStage.INCLUDED, method: str = "full"
) -> AnalysisResult:
    """Publication counts and shares by country.

    Args:
        corpus: The corpus to read.
        stage: Which PRISMA-flow set to count over.
        method: ``"full"`` or ``"fractional"``; see this module's docstring.

    Returns:
        An :class:`~prismabib.bibliometrics.base.AnalysisResult` whose
        ``data`` is ``country``, ``count``, ``share``, sorted by ``count``
        descending then ``country`` ascending (a total order). Every record
        in ``stage`` is represented, in ``"UNK"`` when it carries no known
        country.

    Raises:
        AnalysisError: ``method`` is not one of :data:`COUNTING_METHODS`.
    """
    _require_valid_method(method)
    records = corpus.records(stage)
    affiliations = corpus.affiliations(stage)
    membership = _record_country_membership(records, affiliations)

    if records.height == 0:
        data = pl.DataFrame(schema=_EMPTY_COUNTRY_SCHEMA)
    else:
        weighted = _weighted_membership(membership, method)
        data = (
            weighted.group_by("country_iso3")
            .agg(pl.col("weight").sum().alias("count"))
            .rename({"country_iso3": "country"})
            .with_columns((pl.col("count") / records.height).alias("share"))
            .sort(["count", "country"], descending=[True, False])
            .select(["country", "count", "share"])
        )

    provenance = build_provenance(corpus, stage=stage, records=records)
    return AnalysisResult(data=data, params={"method": method}, provenance=provenance)


def citation_impact_by_country(
    corpus: Corpus,
    *,
    stage: PrismaStage = PrismaStage.INCLUDED,
    method: str = "full",
    at: datetime | None = None,
) -> AnalysisResult:
    """Per-paper citation impact by country.

    Args:
        corpus: The corpus to read.
        stage: Which PRISMA-flow set to read.
        method: ``"full"`` or ``"fractional"``; see this module's docstring.
            Applies to citations the same way it applies to
            :func:`country_counts`: a record's citation count is split
            ``1/k`` over its ``k`` countries under ``"fractional"``, counted
            once per country under ``"full"``.
        at: Forwarded to :meth:`~prismabib.store.load.Corpus.citations`;
            ``None`` uses the latest snapshot per record.

    Returns:
        An :class:`~prismabib.bibliometrics.base.AnalysisResult` whose
        ``data`` is ``country``, ``n_records`` (the same weighted count
        :func:`country_counts` reports), ``total_citations``,
        ``mean_citations``, sorted by ``total_citations`` descending then
        ``country`` ascending. A record with no citation snapshot
        contributes ``0`` citations (not a dropped record) -- excluding it
        would shrink the denominator below every other count this module
        reports for the same record set.

    Raises:
        AnalysisError: ``method`` is not one of :data:`COUNTING_METHODS`.
    """
    _require_valid_method(method)
    records = corpus.records(stage)
    affiliations = corpus.affiliations(stage)
    citations = corpus.citations(at)
    if records.height and "record_id" in records.columns:
        record_ids = set(records.get_column("record_id").to_list())
        citations = citations.filter(pl.col("record_id").is_in(record_ids))
    membership = _record_country_membership(records, affiliations)

    if records.height == 0:
        data = pl.DataFrame(schema=_EMPTY_IMPACT_SCHEMA)
    else:
        weighted = _weighted_membership(membership, method)
        if citations.height == 0:
            # See `_record_country_membership`'s docstring: a `citations`
            # frame with zero matching rows is an ordinary outcome (no
            # record in `stage` has a snapshot yet), not a pathological one,
            # and joining it here would hit the same `Corpus._query`
            # empty-result `Null`-dtype trap.
            joined = weighted.with_columns(pl.lit(0).alias("cited_by_count"))
        else:
            joined = weighted.join(
                citations.select(["record_id", "cited_by_count"]), on="record_id", how="left"
            ).with_columns(pl.col("cited_by_count").fill_null(0))
        data = (
            joined.group_by("country_iso3")
            .agg(
                [
                    pl.col("weight").sum().alias("n_records"),
                    (pl.col("weight") * pl.col("cited_by_count")).sum().alias("total_citations"),
                ]
            )
            .rename({"country_iso3": "country"})
            .with_columns(
                pl.when(pl.col("n_records") > 0)
                .then(pl.col("total_citations") / pl.col("n_records"))
                .otherwise(0.0)
                .alias("mean_citations")
            )
            .sort(["total_citations", "country"], descending=[True, False])
            .select(["country", "n_records", "total_citations", "mean_citations"])
        )

    provenance = build_provenance(corpus, stage=stage, records=records, citations=citations)
    return AnalysisResult(data=data, params={"method": method}, provenance=provenance)


__all__ = ["COUNTING_METHODS", "citation_impact_by_country", "country_counts"]
