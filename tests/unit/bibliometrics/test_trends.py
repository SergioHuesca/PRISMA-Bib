"""Annual counts and CAGR (BUILD_PLAN Stage 7, ADR 0022 Decisions 2-3)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from prismabib.bibliometrics.base import build_provenance
from prismabib.bibliometrics.trends import (
    _annual_counts_frame,
    _cagr_bounds,
    _cagr_from_series,
    _mark_partial_years,
    annual_counts,
    cagr,
)
from prismabib.errors import AnalysisError
from prismabib.stage import PrismaStage
from tests.bibliometrics_helpers import (
    BibCorpusSpec,
    BibRecordSpec,
    build_bib_project,
    include_everything,
    open_corpus,
)


def _records_frame(years: list[int | None]) -> pl.DataFrame:
    """A hand-built ``Corpus.records``-shaped frame, ``record_id``/``year`` only."""
    return pl.DataFrame(
        {"record_id": [f"r{i}" for i in range(len(years))], "year": years},
        schema={"record_id": pl.Utf8, "year": pl.Int64},
    )


# ---------------------------------------------------------------------------
# `_annual_counts_frame` -- pure, hand-built inputs (`records.year` can never
# actually be NULL through a real capture; see BibRecordSpec's docstring).
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_annual_counts_frame__empty_input__empty_output_with_schema() -> None:
    frame = _annual_counts_frame(_records_frame([]))

    assert frame.height == 0
    assert frame.schema == {"year": pl.Int64, "count": pl.Int64}


@pytest.mark.unit
def test_annual_counts_frame__single_record__one_row_count_one() -> None:
    frame = _annual_counts_frame(_records_frame([2021]))

    assert frame.to_dicts() == [{"year": 2021, "count": 1}]


@pytest.mark.unit
def test_annual_counts_frame__null_years__excluded_from_every_row() -> None:
    """A phantom year is a worse dishonesty than an undercount -- see the module docstring."""
    frame = _annual_counts_frame(_records_frame([2020, None, 2020, None]))

    assert frame.to_dicts() == [{"year": 2020, "count": 2}]


@pytest.mark.unit
def test_annual_counts_frame__all_null_years__empty_output() -> None:
    frame = _annual_counts_frame(_records_frame([None, None]))

    assert frame.height == 0


# ---------------------------------------------------------------------------
# `_cagr_from_series` -- the formula alone, against an analytically known value.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.acceptance("S07-AC2")
def test_cagr__known_geometric_series__matches_analytic_value() -> None:
    """A series constructed as exactly 30% compound growth returns 0.30 to 1e-9."""
    v_start = 1_000.0
    span_years = 5
    v_end = v_start * (1.30**span_years)

    rate = _cagr_from_series(v_start, v_end, span_years)

    assert rate == pytest.approx(0.30, abs=1e-9)


@pytest.mark.unit
def test_cagr_from_series__decline__matches_analytic_value() -> None:
    """A second, independent analytic point: exactly -20% compound decline."""
    v_start = 500.0
    span_years = 3
    v_end = v_start * (0.80**span_years)

    rate = _cagr_from_series(v_start, v_end, span_years)

    assert rate == pytest.approx(-0.20, abs=1e-9)


# ---------------------------------------------------------------------------
# `cagr` over a real store -- partial-year exclusion, degenerate inputs.
# ---------------------------------------------------------------------------


def _corpus_with_years(tmp_path: Path, years: list[int], *, run_started_at: datetime) -> object:
    """A built, unscreened project's :class:`~prismabib.store.load.Corpus` (``RAW`` stage)."""
    records = [BibRecordSpec(number=i, year=year) for i, year in enumerate(years, start=1)]
    project = build_bib_project(
        tmp_path, BibCorpusSpec(records=records, run_started_ats=(run_started_at,))
    )
    return open_corpus(project)


@pytest.mark.integration
def test_cagr__partial_final_year__is_excluded_by_default(tmp_path: Path) -> None:
    """The single most consequential default in the module (BUILD_PLAN's own words)."""
    # Retrieved 2023-03-01: 2023 is partial (Decision 2), 2020-2022 are complete.
    corpus = _corpus_with_years(
        tmp_path,
        [2020, 2020, 2021, 2022, 2022, 2022, 2023],
        run_started_at=datetime(2023, 3, 1, tzinfo=UTC),
    )

    result = cagr(corpus, stage=PrismaStage.RAW)

    row = result.data.row(0, named=True)
    assert row["year_end"] == 2022, "2023 should have been excluded as partial"
    assert row["partial_years_excluded"] == 1
    assert row["v_end"] == 3  # the 2022 count, not 2023's
    assert result.params["include_partial_final_year"] is False


@pytest.mark.integration
def test_cagr__partial_final_year_forced_in__result_is_lower_and_flagged(tmp_path: Path) -> None:
    """The override exists, is marked in ``params``, and materially changes the number."""
    corpus = _corpus_with_years(
        tmp_path,
        [2020, 2020, 2021, 2022, 2022, 2022, 2023],
        run_started_at=datetime(2023, 3, 1, tzinfo=UTC),
    )

    excluded = cagr(corpus, stage=PrismaStage.RAW)
    included = cagr(corpus, stage=PrismaStage.RAW, include_partial_final_year=True)

    assert included.params["include_partial_final_year"] is True
    assert included.data.item(0, "year_end") == 2023
    assert included.data.item(0, "partial_years_excluded") == 0
    # 2023 has only 1 record against 2022's 3 -- including the part-year must
    # pull the growth rate down, not merely change it.
    assert included.data.item(0, "cagr") < excluded.data.item(0, "cagr")


@pytest.mark.unit
def test_cagr_bounds__zero_start_value__raises_rather_than_returning_inf() -> None:
    """ADR 0022 Decision 3's degenerate-input table, exercised directly.

    A real capture can never produce a derived series whose first row has
    ``count == 0`` (:func:`_annual_counts_frame` only ever emits a year that
    at least one record actually has), so this is checked against a
    hand-built ``series`` rather than through :func:`cagr` end-to-end --
    the same reason ``_annual_counts_frame``'s null-year cases are hand-built
    above.
    """
    series = pl.DataFrame(
        {"year": [2020, 2021], "count": [0, 5]}, schema={"year": pl.Int64, "count": pl.Int64}
    )

    with pytest.raises(AnalysisError):
        _cagr_bounds(series)


@pytest.mark.unit
def test_cagr_from_series__zero_start__is_not_guarded_by_the_formula_itself() -> None:
    """The pure formula does not refuse a zero start -- ``_cagr_bounds`` is what does.

    Python raises ``ZeroDivisionError`` here rather than producing a
    silent ``inf``, which is exactly why the guard lives in
    ``_cagr_bounds``: a caller must never reach this function with
    ``v_start == 0`` in the first place.
    """
    with pytest.raises(ZeroDivisionError):
        _cagr_from_series(0.0, 10.0, 3)


@pytest.mark.integration
def test_cagr__result__exposes_v_start_v_end_and_span(tmp_path: Path) -> None:
    """The transparency requirement: a bare growth rate is not checkable."""
    corpus = _corpus_with_years(
        tmp_path,
        [2018, 2018, 2019, 2020, 2020, 2020],
        run_started_at=datetime(2021, 6, 1, tzinfo=UTC),
    )

    result = cagr(corpus, stage=PrismaStage.RAW)

    row = result.data.row(0, named=True)
    assert row["v_start"] == 2
    assert row["v_end"] == 3
    assert row["year_start"] == 2018
    assert row["year_end"] == 2020
    assert row["span_years"] == 2
    assert row["cagr"] == pytest.approx((3 / 2) ** (1 / 2) - 1, abs=1e-9)


@pytest.mark.integration
def test_cagr__caption__states_the_anchor_a_bare_rate_is_not_checkable(tmp_path: Path) -> None:
    """ADR 0022 Decision 3: a reader must see v_start/v_end/year_start/year_end/span_years."""
    corpus = _corpus_with_years(
        tmp_path,
        [2018, 2018, 2019, 2020, 2020, 2020],
        run_started_at=datetime(2021, 6, 1, tzinfo=UTC),
    )

    result = cagr(corpus, stage=PrismaStage.RAW)
    caption = result.caption()

    assert "v_start=2" in caption
    assert "v_end=3" in caption
    assert "year_start=2018" in caption
    assert "year_end=2020" in caption
    assert "span_years=2" in caption


@pytest.mark.integration
def test_cagr__fewer_than_two_complete_years__raises(tmp_path: Path) -> None:
    """A corpus retrieved in January: one complete year, nothing to compute a rate over."""
    corpus = _corpus_with_years(tmp_path, [2025], run_started_at=datetime(2026, 1, 5, tzinfo=UTC))

    with pytest.raises(AnalysisError):
        cagr(corpus, stage=PrismaStage.RAW)


@pytest.mark.integration
def test_cagr__empty_corpus__raises_rather_than_returning_a_number(tmp_path: Path) -> None:
    """ADR 0022 Decision 10's stated exception: unlike every other metric, CAGR raises on ``n=0``."""
    project = build_bib_project(tmp_path, BibCorpusSpec(records=[]))
    corpus = open_corpus(project)

    with pytest.raises(AnalysisError):
        cagr(corpus, stage=PrismaStage.INCLUDED)


@pytest.mark.integration
def test_annual_counts__empty_corpus__empty_data_but_captioned_n_zero(tmp_path: Path) -> None:
    project = build_bib_project(tmp_path, BibCorpusSpec(records=[]))
    corpus = open_corpus(project)

    result = annual_counts(corpus, stage=PrismaStage.INCLUDED)

    assert result.data.height == 0
    assert "n = 0" in result.caption()


@pytest.mark.integration
def test_annual_counts__single_record__matches_corpus_size(tmp_path: Path) -> None:
    # Run started 2025-06-15 (BibCorpusSpec's default), so 2020 is a complete
    # year -- `is_partial` must be False here, not merely present.
    project = build_bib_project(
        tmp_path, BibCorpusSpec(records=[BibRecordSpec(number=1, year=2020)])
    )
    corpus = open_corpus(project)

    result = annual_counts(corpus, stage=PrismaStage.RAW)

    assert result.data.to_dicts() == [{"year": 2020, "count": 1, "is_partial": False}]
    assert result.provenance.corpus_size == 1


# ---------------------------------------------------------------------------
# `_mark_partial_years` -- pure, boundary supplied by the caller rather than
# derived from a real corpus (ADR 0022 Decision 3b: "a value that is
# excluded from one number because it is incomplete must be marked as
# incomplete everywhere else it is shown").
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_mark_partial_years__boundary_known_independently__marks_at_and_after_it() -> None:
    """The boundary year (2025) is given directly, not derived from the corpus/clock."""
    frame = _annual_counts_frame(_records_frame([2023, 2024, 2025, 2026, 2027]))

    marked = _mark_partial_years(frame, boundary=2025)

    assert marked.sort("year").to_dicts() == [
        {"year": 2023, "count": 1, "is_partial": False},
        {"year": 2024, "count": 1, "is_partial": False},
        {"year": 2025, "count": 1, "is_partial": True},
        {"year": 2026, "count": 1, "is_partial": True},
        {"year": 2027, "count": 1, "is_partial": True},
    ]


@pytest.mark.unit
def test_mark_partial_years__no_boundary__nothing_is_partial() -> None:
    """No sealed run at all: there is no boundary to derive, so nothing is flagged."""
    frame = _annual_counts_frame(_records_frame([2020, 2021]))

    marked = _mark_partial_years(frame, boundary=None)

    assert all(row["is_partial"] is False for row in marked.to_dicts())


@pytest.mark.unit
def test_mark_partial_years__empty_frame__empty_output_with_column() -> None:
    frame = _annual_counts_frame(_records_frame([]))

    marked = _mark_partial_years(frame, boundary=2025)

    assert marked.height == 0
    assert "is_partial" in marked.columns


@pytest.mark.integration
def test_annual_counts__live_corpus_shape__tail_years_marked_partial(tmp_path: Path) -> None:
    """ADR 0022 Decision 3b's own worked example: a captured-mid-2026 corpus.

    Mirrors the live `Baseball-CVPR` measurement that motivated the ADR
    amendment -- 2025 complete, 2026 (retrieval year) and 2027
    (ahead-of-print) both partial -- but built from a fixture whose boundary
    is known by construction, not read off the live store.
    """
    records = [
        BibRecordSpec(number=1, year=2025),
        BibRecordSpec(number=2, year=2025),
        BibRecordSpec(number=3, year=2026),
        BibRecordSpec(number=4, year=2027),
    ]
    project = build_bib_project(
        tmp_path,
        BibCorpusSpec(records=records, run_started_ats=(datetime(2026, 9, 2, tzinfo=UTC),)),
    )
    corpus = open_corpus(project)

    result = annual_counts(corpus, stage=PrismaStage.RAW)

    by_year = {row["year"]: row["is_partial"] for row in result.data.to_dicts()}
    assert by_year == {2025: False, 2026: True, 2027: True}
    assert result.params["first_incomplete_year"] == 2026
    assert "first_incomplete_year=2026" in result.caption()


@pytest.mark.integration
def test_annual_counts__default_included_stage__hand_computed_value_matches(
    tmp_path: Path,
) -> None:
    """`PrismaStage.INCLUDED` is every analysis function's default -- exercised here explicitly.

    Every other `annual_counts`/`cagr` test in this module passes
    `stage=PrismaStage.RAW`, which reads Layer 1 directly. `INCLUDED`
    instead delegates through `Corpus._prisma_stage_record_ids` to the
    Stage 4 PRISMA engine and the decision log -- a code path no prior test
    in this module exercised against a non-empty, value-checked result.
    """
    records = [
        BibRecordSpec(number=1, year=2020),
        BibRecordSpec(number=2, year=2020),
        BibRecordSpec(number=3, year=2021),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    include_everything(project)
    corpus = open_corpus(project)

    result = annual_counts(corpus)  # default stage=PrismaStage.INCLUDED

    by_year = {row["year"]: row["count"] for row in result.data.to_dicts()}
    assert by_year == {2020: 2, 2021: 1}
    assert result.provenance.corpus_size == 3
    assert result.provenance.stage is PrismaStage.INCLUDED


@pytest.mark.integration
def test_build_provenance__run_ids_and_criteria_versions__reflect_the_contributing_run(
    tmp_path: Path,
) -> None:
    """Cross-checks `Provenance` against the store directly -- not against its own output."""
    project = build_bib_project(
        tmp_path, BibCorpusSpec(records=[BibRecordSpec(number=1, year=2020)])
    )
    corpus = open_corpus(project)
    records = corpus.records(PrismaStage.RAW)

    provenance = build_provenance(corpus, stage=PrismaStage.RAW, records=records)

    assert provenance.run_ids == tuple(sorted(records.get_column("run_id").unique().to_list()))
    assert provenance.criteria_versions == ("1.0.0",)
