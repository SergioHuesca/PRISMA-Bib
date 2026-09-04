"""Keyword co-occurrence and co-authorship networks (BUILD_PLAN Stage 7, ADR 0022 Decision 7)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.bibliometrics.network import (
    _export_vosviewer,
    coauthorship_network,
    keyword_cooccurrence_network,
)
from prismabib.stage import PrismaStage
from tests.bibliometrics_helpers import (
    AuthorSpec,
    BibCorpusSpec,
    BibRecordSpec,
    build_bib_project,
    include_everything,
    open_corpus,
)

#: Six records, hand-countable by a reader. Keyword co-occurrence, one term
#: pair per record where both terms are present:
#:   r1: baseball, vision       -> (baseball, vision)
#:   r2: baseball, vision       -> (baseball, vision)
#:   r3: baseball, robotics     -> (baseball, robotics)
#:   r4: vision, robotics       -> (robotics, vision)
#:   r5: baseball               -> (none -- only one term)
#:   r6: baseball, vision, robotics -> all three pairs
#: By hand: weight(baseball, vision) = r1, r2, r6 = 3
#:          weight(baseball, robotics) = r3, r6 = 2
#:          weight(robotics, vision) = r4, r6 = 2
_SIX_RECORD_KEYWORD_FIXTURE = [
    BibRecordSpec(number=1, author_keywords=("baseball", "vision")),
    BibRecordSpec(number=2, author_keywords=("baseball", "vision")),
    BibRecordSpec(number=3, author_keywords=("baseball", "robotics")),
    BibRecordSpec(number=4, author_keywords=("vision", "robotics")),
    BibRecordSpec(number=5, author_keywords=("baseball",)),
    BibRecordSpec(number=6, author_keywords=("baseball", "vision", "robotics")),
]


@pytest.mark.integration
def test_network__cooccurrence__edge_weight_equals_manual_count(tmp_path: Path) -> None:
    project = build_bib_project(tmp_path, BibCorpusSpec(records=_SIX_RECORD_KEYWORD_FIXTURE))
    corpus = open_corpus(project)

    result = keyword_cooccurrence_network(corpus, stage=PrismaStage.RAW, min_occurrence=1)

    weights = {(row["node_a"], row["node_b"]): row["weight"] for row in result.data.to_dicts()}
    assert weights == {
        ("baseball", "vision"): 3,
        ("baseball", "robotics"): 2,
        ("robotics", "vision"): 2,
    }


@pytest.mark.integration
def test_network__cooccurrence__min_occurrence_excludes_a_rare_term(tmp_path: Path) -> None:
    """A term appearing on only one record is ineligible to form an edge under a higher threshold."""
    records = [
        BibRecordSpec(number=1, author_keywords=("common", "common2")),
        BibRecordSpec(number=2, author_keywords=("common", "common2")),
        BibRecordSpec(number=3, author_keywords=("common", "rare")),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    corpus = open_corpus(project)

    result = keyword_cooccurrence_network(corpus, stage=PrismaStage.RAW, min_occurrence=2)

    terms = {row["node_a"] for row in result.data.to_dicts()} | {
        row["node_b"] for row in result.data.to_dicts()
    }
    assert "rare" not in terms
    assert result.params["min_occurrence"] == 2


@pytest.mark.integration
def test_network__clustering__is_deterministic_under_fixed_seed(tmp_path: Path) -> None:
    """Louvain is randomised; the same seed must produce the same partition every time."""
    project = build_bib_project(tmp_path, BibCorpusSpec(records=_SIX_RECORD_KEYWORD_FIXTURE))
    corpus = open_corpus(project)

    first = keyword_cooccurrence_network(corpus, stage=PrismaStage.RAW, min_occurrence=1, seed=7)
    second = keyword_cooccurrence_network(corpus, stage=PrismaStage.RAW, min_occurrence=1, seed=7)

    assert first.params["communities"] == second.params["communities"]
    assert first.params["seed"] == 7
    assert "seed=7" in first.caption()


@pytest.mark.integration
def test_network__cooccurrence__empty_corpus__empty_edges(tmp_path: Path) -> None:
    project = build_bib_project(tmp_path, BibCorpusSpec(records=[]))
    corpus = open_corpus(project)

    result = keyword_cooccurrence_network(corpus, stage=PrismaStage.INCLUDED)

    assert result.data.height == 0
    assert result.params["communities"] == {}


@pytest.mark.integration
def test_coauthorship__hand_counted_shared_papers__matches(tmp_path: Path) -> None:
    """Two authors co-writing two papers together: edge weight 2, by hand."""
    records = [
        BibRecordSpec(
            number=1,
            authors=(
                AuthorSpec(author_id="A1", surname="Alpha"),
                AuthorSpec(author_id="A2", surname="Beta"),
            ),
        ),
        BibRecordSpec(
            number=2,
            authors=(
                AuthorSpec(author_id="A1", surname="Alpha"),
                AuthorSpec(author_id="A2", surname="Beta"),
            ),
        ),
        BibRecordSpec(number=3, authors=(AuthorSpec(author_id="A3", surname="Gamma"),)),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    corpus = open_corpus(project)

    result = coauthorship_network(corpus, stage=PrismaStage.RAW, min_occurrence=1)

    assert result.data.to_dicts() == [
        {
            "node_a": "A1",
            "node_a_label": "Alpha",
            "node_b": "A2",
            "node_b_label": "Beta",
            "weight": 2,
        }
    ]


@pytest.mark.integration
def test_coauthorship__default_included_stage__hand_computed_value_matches(tmp_path: Path) -> None:
    """`Corpus.authors(INCLUDED)` value-checked, not just type/shape-checked (see test_geography.py)."""
    records = [
        BibRecordSpec(
            number=1,
            authors=(
                AuthorSpec(author_id="A1", surname="Alpha"),
                AuthorSpec(author_id="A2", surname="Beta"),
            ),
        ),
        BibRecordSpec(
            number=2,
            authors=(
                AuthorSpec(author_id="A1", surname="Alpha"),
                AuthorSpec(author_id="A2", surname="Beta"),
            ),
        ),
        BibRecordSpec(number=3, authors=(AuthorSpec(author_id="A3", surname="Gamma"),)),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    include_everything(project)
    corpus = open_corpus(project)

    result = coauthorship_network(corpus, min_occurrence=1)  # default stage=PrismaStage.INCLUDED

    assert result.data.to_dicts() == [
        {
            "node_a": "A1",
            "node_a_label": "Alpha",
            "node_b": "A2",
            "node_b_label": "Beta",
            "weight": 2,
        }
    ]
    assert result.provenance.stage is PrismaStage.INCLUDED


@pytest.mark.integration
def test_vosviewer_export__round_trips_node_and_edge_counts(tmp_path: Path) -> None:
    project = build_bib_project(tmp_path, BibCorpusSpec(records=_SIX_RECORD_KEYWORD_FIXTURE))
    corpus = open_corpus(project)
    result = keyword_cooccurrence_network(corpus, stage=PrismaStage.RAW, min_occurrence=1)

    map_path, network_path = _export_vosviewer(result, tmp_path / "vosviewer")

    map_lines = map_path.read_text(encoding="utf-8").splitlines()
    network_lines = network_path.read_text(encoding="utf-8").splitlines()

    assert map_lines[0] == "id\tlabel\tweight\tcluster"
    assert network_lines[0] == "id1\tid2\tweight"
    # 3 distinct terms -> 3 map rows (+1 header); 3 edges -> 3 network rows (+1 header).
    assert len(map_lines) == 1 + 3
    assert len(network_lines) == 1 + 3

    node_ids = {int(line.split("\t")[0]) for line in map_lines[1:]}
    assert node_ids == {1, 2, 3}
    for line in network_lines[1:]:
        id1, id2, weight = line.split("\t")
        assert int(id1) in node_ids
        assert int(id2) in node_ids
        assert int(weight) > 0


@pytest.mark.integration
def test_vosviewer_export__creates_the_directory(tmp_path: Path) -> None:
    project = build_bib_project(tmp_path, BibCorpusSpec(records=_SIX_RECORD_KEYWORD_FIXTURE))
    corpus = open_corpus(project)
    result = keyword_cooccurrence_network(corpus, stage=PrismaStage.RAW, min_occurrence=1)
    target = tmp_path / "nested" / "export"

    map_path, network_path = _export_vosviewer(result, target)

    assert map_path.parent == target
    assert network_path.parent == target
