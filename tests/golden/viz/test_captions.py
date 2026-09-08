"""Every figure's caption, snapshotted (BUILD_PLAN §Stage 9, S09-AC2).

BUILD_PLAN: *"Every figure's caption is auto-generated and contains n,
criteria version, and citation-snapshot date where applicable."* This test
snapshots the caption text itself -- a caption that silently stopped
changing (or silently dropped a fact) would pass a test that only checked
"contains a substring" against the wrong substring. The companion test,
``test_figures__caption__changes_when_underlying_params_change``, is the
fixture that can fail: it reds if two different ``min_occurrence`` values
ever produced the same caption.

Never regenerated to make a failing test pass (§5 risk 11) -- the checked-in
file is plain text, one caption per line, hand-reviewable.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from prismabib.bibliometrics import citations, geography, keywords, network, trends, venues
from prismabib.stage import PrismaStage
from prismabib.viz.figures import _caption
from tests.bibliometrics_helpers import (
    AffiliationSpec,
    BibCorpusSpec,
    BibRecordSpec,
    build_bib_project,
    open_corpus,
)

_SNAPSHOT_PATH = Path(__file__).parent / "__snapshots__" / "captions.txt"

_RECORDS = [
    BibRecordSpec(
        number=1,
        year=2020,
        cited_by_count=5,
        author_keywords=("baseball", "vision"),
        affiliations=(AffiliationSpec(afid="AF1", country="USA"),),
    ),
    BibRecordSpec(
        number=2,
        year=2021,
        cited_by_count=10,
        author_keywords=("baseball", "audio"),
        affiliations=(AffiliationSpec(afid="AF2", country="JPN"),),
    ),
]


def _captions(tmp_path: Path, *, min_occurrence: int = 1) -> list[str]:
    project = build_bib_project(
        tmp_path,
        BibCorpusSpec(records=_RECORDS, run_started_ats=(datetime(2025, 6, 15, tzinfo=UTC),)),
        slug="caption-golden",
    )
    corpus = open_corpus(project)

    return [
        _caption(trends.annual_counts(corpus, stage=PrismaStage.RAW)),
        _caption(geography.country_counts(corpus, stage=PrismaStage.RAW)),
        _caption(venues.top_venues(corpus, stage=PrismaStage.RAW)),
        _caption(
            keywords.keyword_evolution(corpus, stage=PrismaStage.RAW, min_occurrence=min_occurrence)
        ),
        _caption(
            network.keyword_cooccurrence_network(
                corpus, stage=PrismaStage.RAW, min_occurrence=min_occurrence
            )
        ),
        _caption(citations.citation_distribution(corpus, stage=PrismaStage.RAW)),
    ]


@pytest.mark.golden
@pytest.mark.acceptance("S09-AC2")
def test_figures__caption__contains_n_criteria_version_and_snapshot(tmp_path: Path) -> None:
    captions = _captions(tmp_path)

    expected = _SNAPSHOT_PATH.read_text(encoding="utf-8").splitlines()
    assert captions == expected

    for caption in captions:
        assert "n = 2" in caption
        assert "criteria version(s): 1.0.0" in caption


@pytest.mark.unit
def test_figures__caption__changes_when_underlying_params_change(tmp_path: Path) -> None:
    """A caption that never changes is a caption that is lying."""
    default_captions = _captions(tmp_path / "a", min_occurrence=1)
    changed_captions = _captions(tmp_path / "b", min_occurrence=2)

    # Only the two params-sensitive captions (keyword evolution, network)
    # are expected to move; comparing the whole list would pass even if one
    # of the two silently stopped reflecting `min_occurrence`.
    assert default_captions[3] != changed_captions[3]
    assert default_captions[4] != changed_captions[4]
