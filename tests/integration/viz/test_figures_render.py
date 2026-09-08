"""Every required figure renders on both backends (BUILD_PLAN §Stage 9, S09-AC1).

One project, built once (module-scoped) with real bibliometrics *and*
taxonomy content, feeds every one of the nine required figures -- the same
"one ``Corpus`` handle" spirit BUILD_PLAN's dashboard acceptance criterion
states for the dashboard tabs (ADR 0025 Decision 7), exercised here at the
figure layer.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path

import plotly.graph_objects as go
import pytest
from matplotlib.figure import Figure as MplFigure

from prismabib.bibliometrics import citations, geography, keywords, network, trends, venues
from prismabib.prisma.flow import compute_flow_counts
from prismabib.stage import PrismaStage
from prismabib.taxonomy.analysis import dimension_distribution, dimension_evolution
from prismabib.taxonomy.coder import CountingUnit, code
from prismabib.taxonomy.schema import load_dimensions
from prismabib.viz import figures
from tests.bibliometrics_helpers import (
    AffiliationSpec,
    AuthorSpec,
    BibCorpusSpec,
    BibRecordSpec,
    build_bib_project,
    include_everything,
    open_corpus,
)
from tests.taxonomy_helpers import load_rule_files, write_dimensions, write_rule_file

#: Taxonomy dimensions/rules written *onto* the bibliometrics fixture below,
#: matched against the exact keyword vocabulary
#: `tests.bibliometrics_helpers.BibRecordSpec.author_keywords` already uses
#: (`"vision"`, `"audio"`, `"baseball"`) -- one project, one corpus, every
#: figure (BUILD_PLAN figure 7/8 included), rather than three unrelated
#: fixtures.
_DIMENSIONS_YAML = """\
dimensions:
  - id: modality
    multi_label: false
    categories: [vision, audio]
"""

_MODALITY_RULES = """\
version: 1.0.0
dimension: modality
counting_unit: papers
categories:
  - id: vision
    any:
      - {field: author_keywords, pattern: 'vision'}
  - id: audio
    any:
      - {field: author_keywords, pattern: 'audio'}
"""


@pytest.fixture(scope="module")
def rich_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A bibliometrics + taxonomy project exercising all nine figures."""
    tmp_path = tmp_path_factory.mktemp("viz-fixtures")
    records = [
        BibRecordSpec(
            number=i,
            year=2019 + (i % 4),
            venue_name="ICML" if i % 2 else "Journal of Testing",
            cited_by_count=i,
            author_keywords=("baseball", "vision") if i % 2 else ("baseball", "audio"),
            affiliations=(AffiliationSpec(afid=f"AF{i}", country="USA" if i % 2 else "JPN"),),
            authors=(
                AuthorSpec(author_id=f"A{i % 3}", surname=f"Surname{i % 3}"),
                AuthorSpec(author_id=f"A{(i + 1) % 3}", surname=f"Surname{(i + 1) % 3}"),
            ),
        )
        for i in range(1, 13)
    ]
    project = build_bib_project(
        tmp_path,
        BibCorpusSpec(records=records, run_started_ats=(datetime(2025, 6, 15, tzinfo=UTC),)),
        slug="viz-rich",
    )
    include_everything(project)
    write_dimensions(project, _DIMENSIONS_YAML)
    write_rule_file(project, "modality", _MODALITY_RULES)
    return project


@pytest.mark.integration
@pytest.mark.acceptance("S09-AC1")
def test_figures__every_function__renders_both_backends(rich_project: Path) -> None:
    corpus = open_corpus(rich_project)
    schema = load_dimensions(rich_project)
    (rule_file,) = load_rule_files(rich_project, schema, "modality")
    coding_result = code(corpus, [rule_file], [])

    pairs: list[figures.FigurePair] = [
        figures.trend_figure(trends.annual_counts(corpus)),
        figures.geography_figure(geography.country_counts(corpus)),
        figures.venues_figure(venues.top_venues(corpus)),
        figures.keywords_figure(keywords.keyword_evolution(corpus, min_occurrence=1)),
        figures.network_figure(network.keyword_cooccurrence_network(corpus, min_occurrence=1)),
        figures.citation_distribution_figure(citations.citation_distribution(corpus)),
        figures.taxonomy_distribution_figure(
            [dimension_distribution(corpus, coding_result, schema, "modality", CountingUnit.PAPERS)]
        ),
        figures.taxonomy_evolution_figure(
            [dimension_evolution(corpus, coding_result, schema, "modality", CountingUnit.PAPERS)]
        ),
    ]

    for plotly_figure, mpl_figure in pairs:
        assert isinstance(plotly_figure, go.Figure)
        assert isinstance(mpl_figure, MplFigure)
        # Rendering to SVG bytes (in memory, never to disk) is the real
        # "does this actually draw" check -- a `go.Figure`/`MplFigure`
        # instance can exist with a layout that would raise on render.
        buffer = io.BytesIO()
        mpl_figure.savefig(buffer, format="svg")
        assert buffer.getvalue().startswith(b"<?xml")

    counts = compute_flow_counts(rich_project)
    svg, flow_mpl_figure = figures.prisma_flow_figure(counts, rich_project)
    assert "<svg" in svg
    assert isinstance(flow_mpl_figure, MplFigure)


@pytest.mark.integration
def test_figures__every_function__renders_on_an_empty_corpus(tmp_path: Path) -> None:
    """S09's own zero-row requirement: `C` is empty on the live corpus today."""
    project = build_bib_project(tmp_path, BibCorpusSpec(records=[]), slug="viz-empty")
    corpus = open_corpus(project)

    pairs: list[figures.FigurePair] = [
        figures.trend_figure(trends.annual_counts(corpus)),
        figures.geography_figure(geography.country_counts(corpus)),
        figures.venues_figure(venues.top_venues(corpus)),
        figures.keywords_figure(keywords.keyword_evolution(corpus, min_occurrence=1)),
        figures.network_figure(network.keyword_cooccurrence_network(corpus, min_occurrence=1)),
        figures.citation_distribution_figure(citations.citation_distribution(corpus)),
        figures.taxonomy_distribution_figure([]),
        figures.taxonomy_evolution_figure([]),
    ]

    for plotly_figure, mpl_figure in pairs:
        assert isinstance(plotly_figure, go.Figure)
        assert isinstance(mpl_figure, MplFigure)


@pytest.mark.integration
@pytest.mark.acceptance("S09-AC2")
def test_caption__empty_stage__still_states_the_criteria_version(tmp_path: Path) -> None:
    """S09-AC2 on the corpus state a reader hits first, not only on a populated one.

    Every figure defaults to `PrismaStage.INCLUDED`, and a corpus that has
    not been screened has none. The golden claiming this criterion runs at
    `RAW`, so it could never reach the branch where `criteria_versions` was
    empty -- and on the live corpus today, all six `AnalysisResult`-backed
    captions shipped with no criteria version at all.

    Asserting on the *empty* stage is the whole point of this test; a
    populated fixture reproduces the passing case and proves nothing.
    """
    project = build_bib_project(
        tmp_path,
        BibCorpusSpec(records=[BibRecordSpec(number=1, year=2020)]),
        slug="emptycap",
    )
    corpus = open_corpus(project)  # screening never run -> INCLUDED is empty

    caption = figures._caption(trends.annual_counts(corpus, stage=PrismaStage.INCLUDED))

    assert "n = 0 (included)" in caption
    assert "criteria version" in caption
