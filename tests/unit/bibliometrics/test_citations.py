"""Citation statistics, h-index, and by-year averages (BUILD_PLAN Stage 7)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st

from prismabib.bibliometrics.citations import _h_index, citation_statistics, citations_by_year
from prismabib.stage import PrismaStage
from tests.bibliometrics_helpers import (
    BibCorpusSpec,
    BibRecordSpec,
    build_bib_project,
    include_everything,
    open_corpus,
)

# ---------------------------------------------------------------------------
# `_h_index` -- pure, hand-computed fixtures.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.acceptance("S07-AC3")
def test_hindex__hand_computed_fixture__matches() -> None:
    """[10, 8, 5, 4, 3]: the textbook example. By hand: 4 papers with >= 4 citations, a 5th has only 3."""
    assert _h_index([10, 8, 5, 4, 3]) == 4


@pytest.mark.unit
@pytest.mark.parametrize(
    ("counts", "expected"),
    [
        ([], 0),
        ([0], 0),
        ([1], 1),
        ([5], 1),
        ([3, 3, 3], 3),
        ([100, 0, 0, 0, 0], 1),
        ([7, 6, 5, 4, 3, 2, 1], 4),
    ],
    ids=[
        "empty",
        "single-zero",
        "single-one",
        "single-five",
        "three-threes",
        "one-big-outlier",
        "staircase",
    ],
)
def test_hindex__parametrised_hand_computed_fixtures__matches(
    counts: list[int], expected: int
) -> None:
    assert _h_index(counts) == expected


@pytest.mark.unit
def test_hindex__all_zero_citations__is_zero() -> None:
    assert _h_index([0, 0, 0, 0]) == 0


@pytest.mark.property
@given(
    counts=st.lists(st.integers(min_value=0, max_value=1000), min_size=0, max_size=30),
    extra=st.integers(min_value=1, max_value=1000),
)
def test_hindex__appending_a_cited_paper__never_decreases_h(counts: list[int], extra: int) -> None:
    """Adding a newly-cited paper never decreases h.

    A generic (non-restated) property: neither side of the assertion is
    derived from ``_h_index``'s own implementation, only from the counts
    the test builds.
    """
    assert _h_index([*counts, extra]) >= _h_index(counts)


@pytest.mark.property
@given(
    counts=st.lists(st.integers(min_value=0, max_value=1000), min_size=1, max_size=30),
    extra=st.integers(min_value=1, max_value=1000),
)
def test_hindex__citing_an_existing_paper__never_decreases_h(counts: list[int], extra: int) -> None:
    """Adding citations to a paper already in the corpus never decreases h.

    The second half of the monotonicity property, as its own test with
    ``min_size=1`` rather than an ``if counts:`` inside the first
    (BUILD_PLAN §3.7.3 rule 9: an ``if`` in a test means it is two tests).
    As a guarded branch it was skipped on every ``counts=[]`` draw, so the
    half of the property that exercises a *non-empty* corpus was the half
    least reliably reached.
    """
    bumped = [counts[0] + extra, *counts[1:]]

    assert _h_index(bumped) >= _h_index(counts)


# ---------------------------------------------------------------------------
# `citation_statistics`/`citations_by_year` over a real store.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_citation_statistics__hand_computed_counts__matches(tmp_path: Path) -> None:
    """[10, 8, 5, 4, 3, 0]: total, mean, median, max, h-index and zero-share all by hand."""
    counts = [10, 8, 5, 4, 3, 0]
    records = [BibRecordSpec(number=i, cited_by_count=c) for i, c in enumerate(counts, start=1)]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    corpus = open_corpus(project)

    result = citation_statistics(corpus, stage=PrismaStage.RAW)

    row = result.data.row(0, named=True)
    assert row["records_with_a_snapshot"] == 6
    assert row["total"] == 30
    assert row["mean"] == pytest.approx(5.0)
    assert row["median"] == pytest.approx(4.5)
    assert row["max"] == 10
    assert row["h_index"] == 4
    assert row["zero_cited_share"] == pytest.approx(1 / 6)


@pytest.mark.integration
def test_citation_statistics__default_included_stage__hand_computed_value_matches(
    tmp_path: Path,
) -> None:
    """`Corpus.records(INCLUDED)` value-checked, not just type/shape-checked (see test_geography.py)."""
    counts = [10, 5, 3]
    records = [BibRecordSpec(number=i, cited_by_count=c) for i, c in enumerate(counts, start=1)]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    include_everything(project)
    corpus = open_corpus(project)

    result = citation_statistics(corpus)  # default stage=PrismaStage.INCLUDED

    row = result.data.row(0, named=True)
    assert row["total"] == 18
    assert row["mean"] == pytest.approx(6.0)
    assert row["max"] == 10
    assert row["h_index"] == 3  # [10, 5, 3]: all three have >= 3 citations
    assert result.provenance.stage is PrismaStage.INCLUDED


@pytest.mark.integration
def test_citation_statistics__empty_corpus__all_zero_not_a_crash(tmp_path: Path) -> None:
    """ADR 0022 Decision 10: an all-zero citation table is a real, reportable state."""
    project = build_bib_project(tmp_path, BibCorpusSpec(records=[]))
    corpus = open_corpus(project)

    result = citation_statistics(corpus, stage=PrismaStage.INCLUDED)

    row = result.data.row(0, named=True)
    assert row == {
        "records_with_a_snapshot": 0,
        "total": 0,
        "mean": 0.0,
        "median": 0.0,
        "max": 0,
        "h_index": 0,
        "zero_cited_share": 0.0,
    }


@pytest.mark.integration
def test_citations__figure__carries_its_snapshot_date(tmp_path: Path) -> None:
    """The reproducibility trap from BUILD_PLAN §5 risk 6: a snapshot date on the provenance, always."""
    project = build_bib_project(
        tmp_path,
        BibCorpusSpec(records=[BibRecordSpec(number=1, cited_by_count=5)]),
    )
    corpus = open_corpus(project)

    result = citation_statistics(corpus, stage=PrismaStage.RAW)

    assert result.provenance.citation_snapshot == datetime(2025, 6, 15)  # noqa: DTZ001 -- DuckDB stores naive UTC (store/load.py::_as_naive_utc)
    assert result.provenance.citation_snapshot_is_uniform is True
    assert "citations as at 2025-06-15" in result.caption()


@pytest.mark.integration
def test_citations__two_runs_with_different_dates__snapshot_is_not_uniform(tmp_path: Path) -> None:
    """Two sealed runs, two distinct `retrieved_at` values -- the messier caption sentence."""
    early = datetime(2024, 1, 1, tzinfo=UTC)
    late = datetime(2025, 6, 15, tzinfo=UTC)
    spec = BibCorpusSpec(
        records=[
            BibRecordSpec(number=1, cited_by_count=2),
            BibRecordSpec(number=2, cited_by_count=3),
        ],
        run_started_ats=(early, late),
    )
    project = build_bib_project(tmp_path, spec)
    corpus = open_corpus(project)

    result = citation_statistics(corpus, stage=PrismaStage.RAW)

    assert result.provenance.citation_snapshot_is_uniform is False
    assert result.provenance.citation_snapshot == late.replace(tzinfo=None)
    assert "latest citation snapshot per record, most recent 2025-06-15" in result.caption()


@pytest.mark.integration
def test_citations_by_year__hand_computed_means__matches(tmp_path: Path) -> None:
    records = [
        BibRecordSpec(number=1, year=2020, cited_by_count=10),
        BibRecordSpec(number=2, year=2020, cited_by_count=20),
        BibRecordSpec(number=3, year=2021, cited_by_count=5),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    corpus = open_corpus(project)

    result = citations_by_year(corpus, stage=PrismaStage.RAW)

    assert result.data.sort("year").to_dicts() == [
        {"year": 2020, "mean_citations": 15.0, "records": 2},
        {"year": 2021, "mean_citations": 5.0, "records": 1},
    ]


@pytest.mark.integration
def test_citations_by_year__single_record__one_row(tmp_path: Path) -> None:
    project = build_bib_project(
        tmp_path, BibCorpusSpec(records=[BibRecordSpec(number=1, year=2022, cited_by_count=7)])
    )
    corpus = open_corpus(project)

    result = citations_by_year(corpus, stage=PrismaStage.RAW)

    assert result.data.to_dicts() == [{"year": 2022, "mean_citations": 7.0, "records": 1}]
