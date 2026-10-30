"""``AnalysisResult``/``Provenance`` and the caption generator (ADR 0022 Decisions 1, 8).

Every assertion here is against a :class:`~prismabib.bibliometrics.base.Provenance`
or :class:`~prismabib.bibliometrics.base.AnalysisResult` built by hand, never
against what :func:`~prismabib.bibliometrics.base.build_provenance` itself
returned -- so a caption bug and the value it renders cannot agree with each
other for the wrong reason (BUILD_PLAN §3.7.3, "never against the function's
own prior output").
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from prismabib.bibliometrics.base import (
    AnalysisResult,
    Provenance,
    _max_datetime,
    _serialise,
    normalise_years,
    range_params,
    restrict_to_years,
    semi_join_records,
    years_label,
)
from prismabib.errors import AnalysisError
from prismabib.stage import PrismaStage

EMPTY_DATA = pl.DataFrame({"x": [1]})


def _provenance(**overrides: object) -> Provenance:
    """A minimal, valid :class:`Provenance`, overridable field by field."""
    defaults: dict[str, object] = {
        "corpus_size": 7,
        "stage": PrismaStage.INCLUDED,
        "retrieved_at": datetime(2026, 3, 4, tzinfo=UTC),
        "run_ids": ("run-a",),
        "criteria_versions": ("1.0.0",),
        "citation_snapshot": None,
        "citation_snapshot_is_uniform": True,
    }
    defaults.update(overrides)
    return Provenance(**defaults)  # type: ignore[arg-type]


@pytest.mark.unit
def test_caption__always__contains_corpus_size_and_stage() -> None:
    """S07's "``n`` and snapshot date" requirement, the ``n`` half."""
    result = AnalysisResult(data=EMPTY_DATA, params={}, provenance=_provenance(corpus_size=42))

    caption = result.caption()

    assert "42" in caption
    assert PrismaStage.INCLUDED.value in caption


@pytest.mark.unit
def test_caption__retrieved_at_present__contains_the_iso_date() -> None:
    """The "snapshot date" half -- the corpus's own as-at date, not a citation date."""
    provenance = _provenance(retrieved_at=datetime(2026, 9, 2, 11, 30, tzinfo=UTC))
    result = AnalysisResult(data=EMPTY_DATA, params={}, provenance=provenance)

    assert "2026-09-02" in result.caption()


@pytest.mark.unit
def test_caption__retrieved_at_none__states_unknown_rather_than_omitting() -> None:
    """No sealed run at all: still a stated fact, not a silently missing clause."""
    result = AnalysisResult(data=EMPTY_DATA, params={}, provenance=_provenance(retrieved_at=None))

    assert "unknown" in result.caption()


@pytest.mark.unit
def test_caption__no_citations_read__omits_a_citation_clause() -> None:
    """ADR 0022 Decision 1: a result that never read citations must not claim a snapshot."""
    result = AnalysisResult(
        data=EMPTY_DATA, params={}, provenance=_provenance(citation_snapshot=None)
    )

    assert "citation" not in result.caption()


@pytest.mark.unit
def test_caption__uniform_citation_snapshot__states_a_single_date() -> None:
    provenance = _provenance(
        citation_snapshot=datetime(2026, 5, 1, tzinfo=UTC), citation_snapshot_is_uniform=True
    )
    result = AnalysisResult(data=EMPTY_DATA, params={}, provenance=provenance)

    caption = result.caption()

    assert "citations as at 2026-05-01" in caption
    assert "latest citation snapshot per record" not in caption


@pytest.mark.unit
def test_caption__non_uniform_citation_snapshot__states_the_messier_sentence() -> None:
    """ADR 0022 Decision 1: the wording changes, not just the data, when records disagree."""
    provenance = _provenance(
        citation_snapshot=datetime(2026, 5, 1, tzinfo=UTC), citation_snapshot_is_uniform=False
    )
    result = AnalysisResult(data=EMPTY_DATA, params={}, provenance=provenance)

    caption = result.caption()

    assert "latest citation snapshot per record, most recent 2026-05-01" in caption


@pytest.mark.unit
def test_caption__scalar_params__are_rendered() -> None:
    """S07-AC4's generic half: any scalar param appears in the caption text."""
    result = AnalysisResult(
        data=EMPTY_DATA,
        params={"min_occurrence": 3, "method": "fractional"},
        provenance=_provenance(),
    )

    caption = result.caption()

    assert "min_occurrence=3" in caption
    assert "method=fractional" in caption


@pytest.mark.unit
def test_caption__non_scalar_param__is_not_rendered_but_still_present_in_params() -> None:
    """A community assignment belongs in ``params`` (and the bit-identical check) but not a sentence."""
    result = AnalysisResult(
        data=EMPTY_DATA, params={"communities": {"a": 0, "b": 1}}, provenance=_provenance()
    )

    assert "communities" not in result.caption()
    assert result.params["communities"] == {"a": 0, "b": 1}


@pytest.mark.unit
def test_caption__changing_min_occurrence__changes_the_caption() -> None:
    """S07-AC4, stated as a diff: two otherwise-identical results, one param changed."""
    low = AnalysisResult(data=EMPTY_DATA, params={"min_occurrence": 1}, provenance=_provenance())
    high = AnalysisResult(data=EMPTY_DATA, params={"min_occurrence": 5}, provenance=_provenance())

    assert low.caption() != high.caption()


@pytest.mark.unit
def test_max_datetime__empty_series__is_none() -> None:
    assert _max_datetime(pl.Series("x", [], dtype=pl.Datetime)) is None


@pytest.mark.unit
def test_max_datetime__several_values__returns_the_maximum() -> None:
    values = [
        datetime(2020, 1, 1, tzinfo=UTC),
        datetime(2026, 6, 15, tzinfo=UTC),
        datetime(2022, 1, 1, tzinfo=UTC),
    ]
    series = pl.Series("x", values)

    assert _max_datetime(series) == datetime(2026, 6, 15, tzinfo=UTC)


@pytest.mark.unit
def test_max_datetime__non_datetime_series__raises_analysis_error() -> None:
    with pytest.raises(AnalysisError):
        _max_datetime(pl.Series("x", [1, 2, 3]))


@pytest.mark.unit
def test_serialise__two_dicts_with_different_insertion_order__are_identical_bytes() -> None:
    """ADR 0022 Decision 8: canonical JSON, sorted keys -- insertion order must not leak through."""
    provenance = _provenance()
    first = AnalysisResult(
        data=pl.DataFrame({"a": [1], "b": [2]}), params={"x": 1, "y": 2}, provenance=provenance
    )
    second = AnalysisResult(
        data=pl.DataFrame({"a": [1], "b": [2]}), params={"y": 2, "x": 1}, provenance=provenance
    )

    assert _serialise(first) == _serialise(second)


@pytest.mark.unit
def test_serialise__different_data__is_different_bytes() -> None:
    """The comparison is not vacuously true for any two results."""
    provenance = _provenance()
    first = AnalysisResult(data=pl.DataFrame({"a": [1]}), params={}, provenance=provenance)
    second = AnalysisResult(data=pl.DataFrame({"a": [2]}), params={}, provenance=provenance)

    assert _serialise(first) != _serialise(second)


# --- ADR 0040: restrict_to_years and the year-range caption rule ---------------------------

#: Years 2019..2022 once each, plus two null-year rows; ``record_id`` is the row key.
_YEARS_FRAME = pl.DataFrame(
    {"record_id": ["a", "b", "c", "d", "e", "f"], "year": [2020, None, 2019, 2022, None, 2021]},
    schema={"record_id": pl.String, "year": pl.Int64},
)


@pytest.mark.unit
@pytest.mark.parametrize("years", [None, (None, None)])
def test_restrict_to_years__no_range__returns_the_same_frame_and_zero(
    years: tuple[int | None, int | None] | None,
) -> None:
    frame, dropped = restrict_to_years(_YEARS_FRAME, years)

    assert frame is _YEARS_FRAME
    assert dropped == 0


@pytest.mark.unit
def test_restrict_to_years__both_bounds__are_inclusive_and_keep_row_order() -> None:
    frame, _ = restrict_to_years(_YEARS_FRAME, (2020, 2021))

    assert frame.get_column("record_id").to_list() == ["a", "f"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("years", "expected"),
    [((2021, None), ["d", "f"]), ((None, 2020), ["a", "c"])],
    ids=["open-end", "open-start"],
)
def test_restrict_to_years__open_bound__is_unbounded_on_that_side(
    years: tuple[int | None, int | None], expected: list[str]
) -> None:
    frame, _ = restrict_to_years(_YEARS_FRAME, years)

    assert frame.get_column("record_id").to_list() == expected


@pytest.mark.unit
@pytest.mark.parametrize("years", [(2020, 2021), (2021, None), (None, 2020)])
def test_restrict_to_years__any_bound_set__drops_null_years_and_counts_them(
    years: tuple[int | None, int | None],
) -> None:
    frame, dropped = restrict_to_years(_YEARS_FRAME, years)

    assert dropped == 2
    assert frame.get_column("year").null_count() == 0


@pytest.mark.unit
def test_restrict_to_years__start_after_end__raises_analysis_error() -> None:
    with pytest.raises(AnalysisError, match=r"2022.*2020"):
        restrict_to_years(_YEARS_FRAME, (2022, 2020))


@pytest.mark.unit
def test_restrict_to_years__equal_bounds__is_a_single_year() -> None:
    frame, _ = restrict_to_years(_YEARS_FRAME, (2022, 2022))

    assert frame.get_column("record_id").to_list() == ["d"]


@pytest.mark.unit
@pytest.mark.parametrize("bad", [("2020", None), (None, 2020.5), (True, None)])
def test_restrict_to_years__non_int_bound__raises_analysis_error(
    bad: tuple[object, object],
) -> None:
    with pytest.raises(AnalysisError, match="int"):
        restrict_to_years(_YEARS_FRAME, bad)  # type: ignore[arg-type]


@pytest.mark.unit
def test_restrict_to_years__empty_frame__keeps_the_schema() -> None:
    empty = _YEARS_FRAME.clear()

    frame, dropped = restrict_to_years(empty, (2020, 2021))

    assert frame.height == 0
    assert frame.schema == _YEARS_FRAME.schema
    assert dropped == 0


@pytest.mark.unit
def test_restrict_to_years__range_outside_the_data__is_empty_with_the_schema() -> None:
    frame, dropped = restrict_to_years(_YEARS_FRAME, (1990, 1991))

    assert frame.height == 0
    assert frame.schema == _YEARS_FRAME.schema
    assert dropped == 2


_SNAPSHOT = datetime(2026, 9, 2, tzinfo=UTC)
_CAVEAT = "older papers have had longer to accumulate citations"


@pytest.mark.unit
def test_caption__years_param__is_a_clause_and_not_a_key_value_pair() -> None:
    result = AnalysisResult(
        data=EMPTY_DATA,
        params={"years": "2020-2021", "method": "full", "n_in_range": 640, "n_no_year_dropped": 3},
        provenance=_provenance(corpus_size=1293, retrieved_at=_SNAPSHOT),
    )

    assert result.caption() == (
        "n = 1293 (included); corpus as at 2026-09-02; papers published in 2020-2021; "
        "method=full, n_in_range=640, n_no_year_dropped=3."
    )


@pytest.mark.unit
@pytest.mark.parametrize("years", ["2020-...", "...-2021"])
def test_caption__open_ended_years__are_stated_verbatim(years: str) -> None:
    result = AnalysisResult(data=EMPTY_DATA, params={"years": years}, provenance=_provenance())

    assert f"papers published in {years}" in result.caption()
    assert "years=" not in result.caption()


@pytest.mark.unit
def test_caption__years_with_citation_snapshot__adds_the_age_caveat_after_the_citation_clause() -> (
    None
):
    result = AnalysisResult(
        data=EMPTY_DATA,
        params={"years": "2020-2021", "n_in_range": 5},
        provenance=_provenance(retrieved_at=_SNAPSHOT, citation_snapshot=_SNAPSHOT),
    )

    assert result.caption() == (
        "n = 7 (included); corpus as at 2026-09-02; papers published in 2020-2021; "
        f"citations as at 2026-09-02; {_CAVEAT}; n_in_range=5."
    )


@pytest.mark.unit
def test_caption__years_with_non_uniform_snapshot__still_adds_the_caveat() -> None:
    result = AnalysisResult(
        data=EMPTY_DATA,
        params={"years": "2020-2021"},
        provenance=_provenance(citation_snapshot=_SNAPSHOT, citation_snapshot_is_uniform=False),
    )

    caption = result.caption()
    assert "latest citation snapshot per record" in caption
    assert _CAVEAT in caption


@pytest.mark.unit
def test_caption__years_without_citation_snapshot__has_no_caveat() -> None:
    result = AnalysisResult(
        data=EMPTY_DATA, params={"years": "2020-2021"}, provenance=_provenance()
    )

    assert _CAVEAT not in result.caption()


@pytest.mark.unit
def test_caption__citation_snapshot_without_years__has_no_caveat() -> None:
    result = AnalysisResult(
        data=EMPTY_DATA, params={"top_n": 3}, provenance=_provenance(citation_snapshot=_SNAPSHOT)
    )

    assert result.caption() == (
        "n = 7 (included); corpus as at 2026-03-04; citations as at 2026-09-02; top_n=3."
    )


@pytest.mark.unit
@pytest.mark.parametrize("years", [None, (None, None)])
def test_normalise_years__no_range__is_none(years: tuple[int | None, int | None] | None) -> None:
    assert normalise_years(years) is None


@pytest.mark.unit
@pytest.mark.parametrize("years", [(2020, 2021), (2020, None), (None, 2021), (2020, 2020)])
def test_normalise_years__a_range__is_returned_unchanged(
    years: tuple[int | None, int | None],
) -> None:
    assert normalise_years(years) == years


@pytest.mark.unit
@pytest.mark.parametrize("bad", [(2022, 2020), ("2020", None), (None, 2020.5), (True, None)])
def test_normalise_years__invalid__raises_analysis_error(bad: tuple[object, object]) -> None:
    with pytest.raises(AnalysisError):
        normalise_years(bad)  # type: ignore[arg-type]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("years", "label"),
    [
        (None, None),
        ((None, None), None),
        ((2020, 2021), "2020-2021"),
        ((2020, None), "2020-..."),
        ((None, 2021), "...-2021"),
        ((2020, 2020), "2020-2020"),
    ],
)
def test_years_label__each_form__is_the_documented_string(
    years: tuple[int | None, int | None] | None, label: str | None
) -> None:
    assert years_label(years) == label


@pytest.mark.unit
def test_years_label__start_after_end__raises_analysis_error() -> None:
    with pytest.raises(AnalysisError):
        years_label((2022, 2020))


@pytest.mark.unit
@pytest.mark.parametrize("years", [(2020, 2021), (2020, None), (None, 2021)])
def test_years_label__matches_the_dashboards_year_range_text(
    years: tuple[int | None, int | None],
) -> None:
    dashboard = pytest.importorskip("prismabib.viz.dashboard")

    state = dashboard.FilterState(year_start=years[0], year_end=years[1])

    assert years_label(years) == dashboard._year_range_text(state)


@pytest.mark.unit
def test_range_params__only_added_when_a_range_is_set() -> None:
    assert range_params({"a": 1}, None, 5, 1) == {"a": 1}
    assert range_params({"a": 1}, (None, None), 5, 1) == {"a": 1}
    assert range_params({"a": 1}, (2020, None), 5, 1) == {
        "a": 1,
        "years": "2020-...",
        "n_in_range": 5,
        "n_no_year_dropped": 1,
    }
    with pytest.raises(AnalysisError):
        range_params({}, (2021, 2020), 0, 0)


@pytest.mark.unit
def test_semi_join_records__keeps_matching_ids_and_passes_empty_frames_through() -> None:
    records = pl.DataFrame({"record_id": ["a", "c"]})
    frame = pl.DataFrame({"record_id": ["a", "b", "c", "a"], "v": [1, 2, 3, 4]})

    assert semi_join_records(frame, records).get_column("v").to_list() == [1, 3, 4]
    empty = pl.DataFrame({"record_id": [], "v": []})  # Null dtypes, as Corpus._query yields
    assert semi_join_records(empty, records) is empty
