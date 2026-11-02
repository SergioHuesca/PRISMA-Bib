"""Keyword co-occurrence and co-authorship networks (BUILD_PLAN Stage 7, ADR 0022 Decision 7).

ADR 0025 Decision 2: ``data`` is long-format with a ``row_kind`` column
(``"node"``/``"edge"``), so most assertions here go through
:func:`_edge_dicts`/:func:`_node_dicts`, which project back down to the
edge-only/node-only shape the pre-ADR-0025 tests asserted on -- keeping the
hand-counted fixtures' arithmetic legible while still exercising the real
schema.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from prismabib.bibliometrics.network import (
    _export_vosviewer,
    coauthorship_network,
    keyword_cooccurrence_network,
)
from prismabib.errors import AnalysisError
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus
from tests.bibliometrics_helpers import (
    AuthorSpec,
    BibCorpusSpec,
    BibRecordSpec,
    build_bib_project,
    include_everything,
    open_corpus,
)


def _edge_dicts(data: pl.DataFrame) -> list[dict[str, object]]:
    """``data``'s edge rows, projected down to the five edge columns."""
    return (
        data.filter(pl.col("row_kind") == "edge")
        .select(["node_a", "node_a_label", "node_b", "node_b_label", "weight"])
        .to_dicts()
    )


def _node_dicts(data: pl.DataFrame) -> list[dict[str, object]]:
    """``data``'s node rows, projected down to the five node columns."""
    return (
        data.filter(pl.col("row_kind") == "node")
        .select(["id", "label", "frequency", "cluster", "cluster_size"])
        .to_dicts()
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

    weights = {(row["node_a"], row["node_b"]): row["weight"] for row in _edge_dicts(result.data)}
    assert weights == {
        ("baseball", "vision"): 3,
        ("baseball", "robotics"): 2,
        ("robotics", "vision"): 2,
    }


@pytest.mark.integration
def test_network__cooccurrence__node_rows_carry_frequency_and_cluster(tmp_path: Path) -> None:
    """ADR 0025 Decision 2: node rows exist for exactly the drawn (edge-bearing) node set."""
    project = build_bib_project(tmp_path, BibCorpusSpec(records=_SIX_RECORD_KEYWORD_FIXTURE))
    corpus = open_corpus(project)

    result = keyword_cooccurrence_network(corpus, stage=PrismaStage.RAW, min_occurrence=1)

    nodes = {row["id"]: row for row in _node_dicts(result.data)}
    assert set(nodes) == {"baseball", "vision", "robotics"}
    # baseball: r1, r2, r3, r5, r6 = 5 records; vision: r1, r2, r4, r6 = 4;
    # robotics: r3, r4, r6 = 3.
    assert nodes["baseball"]["frequency"] == 5
    assert nodes["vision"]["frequency"] == 4
    assert nodes["robotics"]["frequency"] == 3
    for row in nodes.values():
        assert row["cluster"] == result.params["communities"][row["id"]]
    cluster_of = {row["id"]: row["cluster"] for row in nodes.values()}
    expected_size = {
        cluster_id: sum(1 for value in cluster_of.values() if value == cluster_id)
        for cluster_id in set(cluster_of.values())
    }
    for row in nodes.values():
        assert row["cluster_size"] == expected_size[row["cluster"]]


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

    edges = _edge_dicts(result.data)
    terms = {row["node_a"] for row in edges} | {row["node_b"] for row in edges}
    assert "rare" not in terms
    assert "rare" not in {row["id"] for row in _node_dicts(result.data)}
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

    assert _edge_dicts(result.data) == [
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

    assert _edge_dicts(result.data) == [
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


# --- ADR 0040: the `years` engine argument ------------------------------------------------

_NETWORKS: list[tuple[Any, dict[str, Any]]] = [
    (keyword_cooccurrence_network, {"min_occurrence": 1}),
    (coauthorship_network, {"min_occurrence": 1}),
]
_NET_IDS = ["keywords", "authors"]


def _au(*ids: str) -> tuple[AuthorSpec, ...]:
    return tuple(AuthorSpec(author_id=i, surname=i.upper()) for i in ids)


#: Out-of-range pair (x, y) twice in 2019; in-range pair (a, b) in 2020 and 2021;
#: (a, x) in 2022. Range 2020-2021 keeps only the (a, b) edge, weight 2.
_YEAR_NET_RECORDS = [
    BibRecordSpec(number=1, year=2019, author_keywords=("x", "y"), authors=_au("x", "y")),
    BibRecordSpec(number=2, year=2019, author_keywords=("x", "y"), authors=_au("x", "y")),
    BibRecordSpec(number=3, year=2020, author_keywords=("a", "b"), authors=_au("a", "b")),
    BibRecordSpec(number=4, year=2021, author_keywords=("a", "b"), authors=_au("a", "b")),
    BibRecordSpec(number=5, year=2022, author_keywords=("a", "x"), authors=_au("a", "x")),
]


def _year_net_corpus(tmp_path: Path) -> Corpus:
    return open_corpus(build_bib_project(tmp_path, BibCorpusSpec(records=_YEAR_NET_RECORDS)))


@pytest.mark.integration
@pytest.mark.parametrize(("fn", "kwargs"), _NETWORKS, ids=_NET_IDS)
def test_network__years__edges_only_from_in_range_papers(
    tmp_path: Path, fn: Any, kwargs: dict[str, Any]
) -> None:
    corpus = _year_net_corpus(tmp_path)

    result = fn(corpus, stage=PrismaStage.RAW, years=(2020, 2021), **kwargs)

    edges = _edge_dicts(result.data)
    assert [(e["node_a"], e["node_b"], e["weight"]) for e in edges] == [("a", "b", 2)]
    assert {n["id"]: n["frequency"] for n in _node_dicts(result.data)} == {"a": 2, "b": 2}


@pytest.mark.integration
@pytest.mark.parametrize(("fn", "kwargs"), _NETWORKS, ids=_NET_IDS)
def test_network__years__communities_recomputed_on_in_range_graph(
    tmp_path: Path, fn: Any, kwargs: dict[str, Any]
) -> None:
    corpus = _year_net_corpus(tmp_path)

    whole = fn(corpus, stage=PrismaStage.RAW, **kwargs)
    ranged = fn(corpus, stage=PrismaStage.RAW, years=(2020, 2021), **kwargs)

    assert set(whole.params["communities"]) == {"a", "b", "x", "y"}
    assert ranged.params["communities"] == {"a": 0, "b": 0}


@pytest.mark.integration
@pytest.mark.parametrize(("fn", "kwargs"), _NETWORKS, ids=_NET_IDS)
def test_network__years__top_n_and_threshold_apply_after_filtering(
    tmp_path: Path, fn: Any, kwargs: dict[str, Any]
) -> None:
    corpus = _year_net_corpus(tmp_path)

    # In 2020-2022 the edges are a-b (2) and a-x (1): top_n=1 keeps a-b and drops x.
    top1 = fn(corpus, stage=PrismaStage.RAW, years=(2020, 2022), **{**kwargs, "top_n": 1})
    assert {n["id"] for n in _node_dicts(top1.data)} == {"a", "b"}
    # min_occurrence=3: a appears on 3 papers overall but only 2 in 2020-2021.
    strict = {**kwargs, "min_occurrence": 3}
    assert _edge_dicts(fn(corpus, stage=PrismaStage.RAW, years=(2020, 2021), **strict).data) == []
    assert _node_dicts(fn(corpus, stage=PrismaStage.RAW, years=(2019, 2022), **strict).data) != []


@pytest.mark.integration
@pytest.mark.parametrize(("fn", "kwargs"), _NETWORKS, ids=_NET_IDS)
def test_network__years__params_provenance_and_caption(
    tmp_path: Path, fn: Any, kwargs: dict[str, Any]
) -> None:
    corpus = _year_net_corpus(tmp_path)
    plain = fn(corpus, stage=PrismaStage.RAW, **kwargs)

    ranged = fn(corpus, stage=PrismaStage.RAW, years=(2020, 2021), **kwargs)

    assert ranged.params["years"] == "2020-2021"
    assert ranged.params["n_in_range"] == 2
    assert ranged.params["n_no_year_dropped"] == 0
    assert ranged.provenance == plain.provenance  # unrestricted stage frame
    assert ranged.provenance.corpus_size == 5
    assert "papers published in 2020-2021" in ranged.caption()


@pytest.mark.integration
@pytest.mark.parametrize(("fn", "kwargs"), _NETWORKS, ids=_NET_IDS)
def test_network__years__range_with_no_papers__empty_frame_and_stage_provenance(
    tmp_path: Path, fn: Any, kwargs: dict[str, Any]
) -> None:
    corpus = _year_net_corpus(tmp_path)

    result = fn(corpus, stage=PrismaStage.RAW, years=(1900, 1901), **kwargs)

    assert result.data.height == 0
    assert dict(result.data.schema) == {
        "row_kind": pl.Utf8,
        "id": pl.Utf8,
        "label": pl.Utf8,
        "frequency": pl.Int64,
        "cluster": pl.Int64,
        "cluster_size": pl.Int64,
        "node_a": pl.Utf8,
        "node_a_label": pl.Utf8,
        "node_b": pl.Utf8,
        "node_b_label": pl.Utf8,
        "weight": pl.Int64,
    }
    assert result.params["years"] == "1900-1901"
    assert result.params["n_in_range"] == 0
    assert result.params["n_no_year_dropped"] == 0
    assert result.params["communities"] == {}
    assert result.provenance.corpus_size == 5  # stage-level, not range-level
    assert "papers published in 1900-1901" in result.caption()


@pytest.mark.integration
@pytest.mark.parametrize(("fn", "kwargs"), _NETWORKS, ids=_NET_IDS)
def test_network__no_years__params_have_no_years_keys_and_output_unchanged(
    tmp_path: Path, fn: Any, kwargs: dict[str, Any]
) -> None:
    corpus = _year_net_corpus(tmp_path)
    plain = fn(corpus, stage=PrismaStage.RAW, **kwargs)

    assert not {"years", "n_in_range", "n_no_year_dropped"} & set(plain.params)
    for years in (None, (None, None)):
        same = fn(corpus, stage=PrismaStage.RAW, years=years, **kwargs)
        assert same.params == plain.params
        assert same.data.equals(plain.data)
        assert same.caption() == plain.caption()


@pytest.mark.integration
@pytest.mark.parametrize(("fn", "kwargs"), _NETWORKS, ids=_NET_IDS)
def test_network__null_year__dropped_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fn: Any, kwargs: dict[str, Any]
) -> None:
    corpus = _year_net_corpus(tmp_path)
    original = corpus.records

    def with_null_year(stage: PrismaStage = PrismaStage.INCLUDED) -> pl.DataFrame:
        frame = original(stage)
        return frame.with_columns(
            pl.when(pl.col("year") == 2019).then(None).otherwise(pl.col("year")).alias("year")
        )

    monkeypatch.setattr(corpus, "records", with_null_year)

    result = fn(corpus, stage=PrismaStage.RAW, years=(2000, 2030), **kwargs)

    assert result.params["n_no_year_dropped"] == 2
    assert result.params["n_in_range"] == 3
    assert result.provenance.corpus_size == 5
    assert "y" not in result.params["communities"]  # the x-y papers dropped


@pytest.mark.unit
@pytest.mark.parametrize(("fn", "kwargs"), _NETWORKS, ids=_NET_IDS)
def test_network__inverted_range__raises_before_any_read(fn: Any, kwargs: dict[str, Any]) -> None:
    with pytest.raises(AnalysisError):
        fn(None, years=(2021, 2020), **kwargs)  # corpus=None: a read would AttributeError


@pytest.mark.integration
@pytest.mark.parametrize(("fn", "kwargs"), _NETWORKS, ids=_NET_IDS)
def test_network__years__result_independent_of_input_row_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fn: Any, kwargs: dict[str, Any]
) -> None:
    corpus = _year_net_corpus(tmp_path)
    baseline = fn(corpus, stage=PrismaStage.RAW, years=(2020, 2022), **kwargs)
    originals = {name: getattr(corpus, name) for name in ("records", "authors", "keywords")}

    def shuffled(original: Any, seed: int) -> Any:
        def read(*args: Any, **kw: Any) -> pl.DataFrame:
            return original(*args, **kw).sample(fraction=1.0, shuffle=True, seed=seed)

        return read

    for seed in (1, 2, 3):
        for name, original in originals.items():
            monkeypatch.setattr(corpus, name, shuffled(original, seed))
        again = fn(corpus, stage=PrismaStage.RAW, years=(2020, 2022), **kwargs)
        assert again.data.equals(baseline.data)
        assert again.params == baseline.params
