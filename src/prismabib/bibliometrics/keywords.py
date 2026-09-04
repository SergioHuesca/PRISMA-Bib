"""Keyword frequency and year-by-year evolution (BUILD_PLAN Stage 7, ADR 0022 Decision 6).

Counting is over ``term_norm`` (the already-normalised form
:mod:`prismabib.store.load` writes to ``keywords``), full counting -- every
record that carries a term counts once toward that term, with no weighting
by how many keywords the record carries overall.

The stopword list is project data, never a literal in this module (ADR 0022
Decision 6): a default ships as a package data file
(``bibliometrics/data/stopwords.txt``), and a caller may pass a project's own
override -- conventionally ``<project>/config/stopwords.txt`` -- via
``stopwords_path``. This module resolves no project path itself: it has no
access to a :class:`~prismabib.project.Project` (only a
:class:`~prismabib.store.load.Corpus`, ADR 0022's own accessor surface), so
the caller (a notebook, a CLI command, or :mod:`prismabib.report`) is what
decides whether a project override exists and passes its path in.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from prismabib.bibliometrics.base import AnalysisResult, build_provenance
from prismabib.errors import AnalysisError
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus

#: The default list ships beside this module, not inside it -- see the
#: module docstring.
DEFAULT_STOPWORDS_PATH = Path(__file__).parent / "data" / "stopwords.txt"

_EMPTY_FREQUENCY_SCHEMA = {"term": pl.Utf8, "count": pl.Int64}
_EMPTY_EVOLUTION_SCHEMA = {"year": pl.Int64, "term": pl.Utf8, "count": pl.Int64}


def _load_stopwords(path: Path | None) -> frozenset[str]:
    """Read a stopword list from disk.

    Args:
        path: A stopword file, one casefolded term per line, ``#`` comments
            and blank lines ignored. ``None`` resolves to
            :data:`DEFAULT_STOPWORDS_PATH`.

    Returns:
        The casefolded term set. Empty (not an error) when an *explicit*
        override names a file that does not exist -- a caller naming a list
        the project has not created yet degrades to "no stopwords" rather
        than crashing an analysis.

    Raises:
        AnalysisError: If the *packaged default* is missing. The asymmetry
            with the override is deliberate (ADR 0022 Decision 6): losing
            the shipped file would silently readmit ``human``, ``learning``,
            ``network`` and ``model`` to every keyword table -- a wrong
            frequency table with no error anywhere -- whereas an override
            the caller named and did not provide is the caller's own
            business. A packaging regression must be loud.
    """
    resolved = path if path is not None else DEFAULT_STOPWORDS_PATH
    if not resolved.exists():
        if path is None:
            raise AnalysisError(
                f"the packaged default stopword list is missing: {resolved}. "
                "This is a packaging defect, not a configuration one -- without it every "
                "keyword table would silently include generic terms."
            )
        return frozenset()
    terms: set[str] = set()
    for line in resolved.read_text(encoding="utf-8").splitlines():
        term = line.strip()
        if term and not term.startswith("#"):
            terms.add(term.casefold())
    return frozenset(terms)


def keyword_frequency(
    corpus: Corpus,
    *,
    stage: PrismaStage = PrismaStage.INCLUDED,
    kind: str = "author",
    min_occurrence: int = 1,
    stopwords_path: Path | None = None,
) -> AnalysisResult:
    """How many records each keyword appears on.

    Args:
        corpus: The corpus to read.
        stage: Which PRISMA-flow set to count over.
        kind: ``"author"`` or ``"index"``, forwarded to
            :meth:`~prismabib.store.load.Corpus.keywords`.
        min_occurrence: Drop a term appearing on fewer than this many
            records. Recorded in ``params`` and therefore the caption
            (S07-AC4): changing it changes both.
        stopwords_path: See :func:`_load_stopwords`.

    Returns:
        An :class:`~prismabib.bibliometrics.base.AnalysisResult` whose
        ``data`` is ``term``, ``count``, sorted by ``count`` descending then
        ``term`` ascending, restricted to ``count >= min_occurrence``.
    """
    records = corpus.records(stage)
    keywords = corpus.keywords(kind, stage)
    stopwords = _load_stopwords(stopwords_path)

    if keywords.height == 0:
        data = pl.DataFrame(schema=_EMPTY_FREQUENCY_SCHEMA)
    else:
        filtered = keywords
        if stopwords:
            filtered = filtered.filter(~pl.col("term_norm").is_in(sorted(stopwords)))
        data = (
            filtered.group_by("term_norm")
            .agg(pl.len().alias("count"))
            .rename({"term_norm": "term"})
            .filter(pl.col("count") >= min_occurrence)
            .sort(["count", "term"], descending=[True, False])
            .with_columns(pl.col("count").cast(pl.Int64))
        )

    provenance = build_provenance(corpus, stage=stage, records=records)
    params = {"kind": kind, "min_occurrence": min_occurrence}
    return AnalysisResult(data=data, params=params, provenance=provenance)


def keyword_evolution(
    corpus: Corpus,
    *,
    stage: PrismaStage = PrismaStage.INCLUDED,
    kind: str = "author",
    min_occurrence: int = 1,
    stopwords_path: Path | None = None,
) -> AnalysisResult:
    """Keyword frequency broken down by publication year.

    Args:
        corpus: The corpus to read.
        stage: Which PRISMA-flow set to count over.
        kind: ``"author"`` or ``"index"``.
        min_occurrence: A term must appear on at least this many records
            *overall* (summed across every year) to appear in the result --
            the same threshold :func:`keyword_frequency` applies, so the two
            report on the same term set whenever every record carries a year (they can
    differ otherwise: `keyword_evolution` applies `min_occurrence` to the
    year-joined subset, so a term carried only by year-less records is
    absent from it. Layer 1 requires a parseable year, so this is a
    statement about precision rather than a live divergence).
        stopwords_path: See :func:`_load_stopwords`.

    Returns:
        An :class:`~prismabib.bibliometrics.base.AnalysisResult` whose
        ``data`` is ``year``, ``term``, ``count``, sorted by ``year``
        ascending, then ``count`` descending, then ``term`` ascending. A
        record with no ``year`` contributes to no row (see
        :func:`~prismabib.bibliometrics.trends.annual_counts`'s docstring
        for the same judgement made there).
    """
    records = corpus.records(stage)
    keywords = corpus.keywords(kind, stage)
    stopwords = _load_stopwords(stopwords_path)

    dated = records.select(["record_id", "year"]).filter(pl.col("year").is_not_null())
    if keywords.height == 0 or dated.height == 0:
        # `Corpus.keywords`/`Corpus.records` both build their frame from
        # `fetchall()` with no rows to infer a dtype from when nothing
        # matches, so an inner join against an empty side would hit
        # `SchemaError` rather than correctly producing zero rows -- see
        # `bibliometrics/geography.py::_record_country_membership`'s
        # docstring for the same trap.
        joined = pl.DataFrame(
            schema={
                "record_id": pl.Utf8,
                "keyword_id": pl.Utf8,
                "term_raw": pl.Utf8,
                "term_norm": pl.Utf8,
                "kind": pl.Utf8,
                "year": pl.Int64,
            }
        )
    else:
        joined = keywords.join(dated, on="record_id", how="inner")
    if stopwords:
        joined = joined.filter(~pl.col("term_norm").is_in(sorted(stopwords)))

    if joined.height == 0:
        data = pl.DataFrame(schema=_EMPTY_EVOLUTION_SCHEMA)
    else:
        totals = joined.group_by("term_norm").agg(pl.len().alias("_total"))
        keep = totals.filter(pl.col("_total") >= min_occurrence).select("term_norm")
        restricted = joined.join(keep, on="term_norm", how="inner")
        data = (
            restricted.group_by(["year", "term_norm"])
            .agg(pl.len().alias("count"))
            .rename({"term_norm": "term"})
            .with_columns(pl.col("year").cast(pl.Int64), pl.col("count").cast(pl.Int64))
            .sort(["year", "count", "term"], descending=[False, True, False])
        )

    provenance = build_provenance(corpus, stage=stage, records=records)
    params = {"kind": kind, "min_occurrence": min_occurrence}
    return AnalysisResult(data=data, params=params, provenance=provenance)


__all__ = ["DEFAULT_STOPWORDS_PATH", "keyword_evolution", "keyword_frequency"]
