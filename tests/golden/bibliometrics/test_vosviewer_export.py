"""VOSviewer ``map.txt``/``network.txt`` file format, pinned (BUILD_PLAN Stage 7, ADR 0022 Decision 7).

``test_network__vosviewer_export__round_trips_node_and_edge_counts`` (in
``tests/unit/bibliometrics/test_network.py``) checks the counts are right
independently of the exact bytes; this test pins the bytes themselves, so a
change to the export format -- a reordered column, a different id base, a
``\\r\\n`` line ending -- shows as a reviewable diff against a checked-in
file rather than only a passing count.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.bibliometrics.network import _export_vosviewer, keyword_cooccurrence_network
from prismabib.stage import PrismaStage
from tests.bibliometrics_helpers import BibCorpusSpec, BibRecordSpec, build_bib_project, open_corpus

_SNAPSHOTS = Path(__file__).parent / "__snapshots__"

#: The same six-record, hand-countable fixture
#: ``tests/unit/bibliometrics/test_network.py`` uses for its edge-weight
#: assertion -- one definition of the fixture's *shape*, reused here for its
#: exact *bytes*, is deliberate: a discrepancy between the two tests would
#: mean this file was pinned against a different graph than the one the
#: edge-weight test verified by hand.
_SIX_RECORD_KEYWORD_FIXTURE = [
    BibRecordSpec(number=1, author_keywords=("baseball", "vision")),
    BibRecordSpec(number=2, author_keywords=("baseball", "vision")),
    BibRecordSpec(number=3, author_keywords=("baseball", "robotics")),
    BibRecordSpec(number=4, author_keywords=("vision", "robotics")),
    BibRecordSpec(number=5, author_keywords=("baseball",)),
    BibRecordSpec(number=6, author_keywords=("baseball", "vision", "robotics")),
]


@pytest.mark.golden
def test_network__vosviewer_export__round_trips_node_and_edge_counts(tmp_path: Path) -> None:
    project = build_bib_project(
        tmp_path, BibCorpusSpec(records=_SIX_RECORD_KEYWORD_FIXTURE), slug="golden"
    )
    corpus = open_corpus(project)
    result = keyword_cooccurrence_network(corpus, stage=PrismaStage.RAW, min_occurrence=1, seed=0)

    map_path, network_path = _export_vosviewer(result, tmp_path / "vos")

    expected_map = (_SNAPSHOTS / "vosviewer_map.txt").read_bytes()
    expected_network = (_SNAPSHOTS / "vosviewer_network.txt").read_bytes()
    assert map_path.read_bytes() == expected_map
    assert network_path.read_bytes() == expected_network
