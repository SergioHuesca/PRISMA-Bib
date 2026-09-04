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

from prismabib.bibliometrics.base import AnalysisResult, Provenance, _max_datetime, _serialise
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
