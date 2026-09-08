"""``trend_figure``'s partial-year shading, in the rendered SVG (BUILD_PLAN §Stage 9).

``test_partial_year__trend_figure__marks_the_final_year``: the shading is
present in the SVG when ``is_partial`` is ``True`` for the final year, and
absent when it is not -- read straight off ``annual_counts``'s own
``is_partial`` column (ADR 0022 Decision 3b), never recomputed here.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import pytest

from prismabib.bibliometrics import trends
from prismabib.stage import PrismaStage
from prismabib.viz.figures import trend_figure
from tests.bibliometrics_helpers import BibCorpusSpec, BibRecordSpec, build_bib_project, open_corpus

_PARTIAL_YEAR_NOTE = "partial year"


def _trend_svg(tmp_path: Path, *, years: list[int], run_started_at: datetime) -> str:
    records = [BibRecordSpec(number=index, year=year) for index, year in enumerate(years, start=1)]
    project = build_bib_project(
        tmp_path,
        BibCorpusSpec(records=records, run_started_ats=(run_started_at,)),
        slug="trend-golden",
    )
    corpus = open_corpus(project)
    result = trends.annual_counts(corpus, stage=PrismaStage.RAW)

    _plotly_figure, mpl_figure = trend_figure(result)
    buffer = io.BytesIO()
    mpl_figure.savefig(buffer, format="svg")
    return buffer.getvalue().decode("utf-8")


@pytest.mark.golden
def test_partial_year__trend_figure__marks_the_final_year(tmp_path: Path) -> None:
    # The run started in 2025, so 2025 is `first_incomplete_year` and is
    # marked `is_partial=True`; 2023 is a fully complete year.
    svg = _trend_svg(
        tmp_path / "partial", years=[2023, 2025], run_started_at=datetime(2025, 6, 15, tzinfo=UTC)
    )

    assert _PARTIAL_YEAR_NOTE in svg


@pytest.mark.golden
def test_partial_year__trend_figure__no_partial_year__no_shading_note(tmp_path: Path) -> None:
    # The run started in 2030; every year in the fixture (2023, 2024) is
    # fully in the past, so nothing is partial.
    svg = _trend_svg(
        tmp_path / "complete",
        years=[2023, 2024],
        run_started_at=datetime(2030, 1, 1, tzinfo=UTC),
    )

    assert _PARTIAL_YEAR_NOTE not in svg
