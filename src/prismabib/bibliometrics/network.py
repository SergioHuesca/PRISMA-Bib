"""Keyword co-occurrence and co-authorship networks (BUILD_PLAN Stage 7, ADR 0022 Decision 7).

Edge weight is the number of records in which both terms/authors appear --
never a similarity score. Clustering is Louvain
(``networkx.algorithms.community.louvain_communities``), which is randomised
and therefore always seeded; the seed is recorded in ``params`` alongside
``min_occurrence``, ``top_n`` and ``resolution`` (Decision 7). Leiden is not
used: it requires ``igraph``, which is not a project dependency, and the
supply-chain surface of adding one is not worth a marginally better
modularity score here.

**Community-label determinism.** ``louvain_communities`` returns a list of
Python ``set``\\ s, and ``PYTHONHASHSEED`` (which ``pytest-randomly`` varies)
changes ``set`` iteration order, not the algorithm's own seeded randomness.
Reading community *membership* straight off that iteration order would
assign different integer labels to the same partition on different test
runs -- so :func:`_label_communities` re-sorts every community by its
lexicographically smallest member before numbering them, which is
independent of any hash seed.

The functions here that compute a network (:func:`keyword_cooccurrence_network`,
:func:`coauthorship_network`) return an
:class:`~prismabib.bibliometrics.base.AnalysisResult`, per this package's
contract. :func:`_export_vosviewer`, whose own return value is two file
paths rather than a quantitative finding, is deliberately private -- see
``bibliometrics/base.py``'s module docstring for why that is not a deviation
from ADR 0022's return-type constraint.
"""

from __future__ import annotations

import itertools
from pathlib import Path
from typing import Any

import networkx as nx
import polars as pl
from networkx.algorithms.community import louvain_communities

from prismabib.bibliometrics.base import AnalysisResult, build_provenance
from prismabib.bibliometrics.keywords import _load_stopwords
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus

_EMPTY_EDGE_SCHEMA = {
    "node_a": pl.Utf8,
    "node_a_label": pl.Utf8,
    "node_b": pl.Utf8,
    "node_b_label": pl.Utf8,
    "weight": pl.Int64,
}


def _edge_weights(memberships: dict[str, list[str]]) -> dict[tuple[str, str], int]:
    """Co-occurrence edge weights over a record -> node-id-list mapping.

    Args:
        memberships: ``record_id -> [node_id, ...]``, already restricted to
            node ids eligible to form an edge (``min_occurrence`` already
            applied by the caller); duplicates within one record's list are
            tolerated but not required.

    Returns:
        ``(node_a, node_b) -> weight`` -- the number of records in which
        both appear, ``node_a < node_b`` lexicographically. Iterated and
        inserted in a **fixed, hash-seed-independent order**
        (``sorted(memberships)`` for records, ``sorted(set(...))`` for the
        terms/authors within one record) -- a plain ``dict`` preserves
        insertion order regardless of ``PYTHONHASHSEED``, but the order
        *keys are inserted in* is only deterministic if the code that builds
        it never iterates a ``set`` without sorting first, which is exactly
        the trap ADR 0022's "float determinism"/ordering constraints name.
    """
    edge_weights: dict[tuple[str, str], int] = {}
    for record_id in sorted(memberships):
        nodes = sorted(set(memberships[record_id]))
        for node_a, node_b in itertools.combinations(nodes, 2):
            edge_weights[(node_a, node_b)] = edge_weights.get((node_a, node_b), 0) + 1
    return edge_weights


def _cooccurrence_edge_weights(
    keywords: pl.DataFrame, min_occurrence: int
) -> dict[tuple[str, str], int]:
    """Edge weights over ``keywords`` (a ``Corpus.keywords(...)``-shaped frame).

    Args:
        keywords: One row per (record, keyword) occurrence.
        min_occurrence: A term must appear on at least this many records to
            be eligible to form an edge at all.

    Returns:
        See :func:`_edge_weights`.
    """
    if keywords.height == 0:
        return {}
    term_counts = (
        keywords.select(["record_id", "term_norm"])
        .unique()
        .group_by("term_norm")
        .agg(pl.len().alias("count"))
    )
    eligible = frozenset(
        term_counts.filter(pl.col("count") >= min_occurrence).get_column("term_norm").to_list()
    )
    if not eligible:
        return {}
    per_record = (
        keywords.filter(pl.col("term_norm").is_in(eligible))
        .select(["record_id", "term_norm"])
        .unique()
        .group_by("record_id")
        .agg(pl.col("term_norm").alias("terms"))
    )
    memberships = dict(
        zip(
            per_record.get_column("record_id").to_list(),
            per_record.get_column("terms").to_list(),
            strict=True,
        )
    )
    return _edge_weights(memberships)


def _author_edge_weights(authors: pl.DataFrame, min_occurrence: int) -> dict[tuple[str, str], int]:
    """Edge weights over ``authors`` (a ``Corpus.authors(...)``-shaped frame).

    Args:
        authors: One row per (record, author) occurrence.
        min_occurrence: An author must appear on at least this many records
            to be eligible to form an edge at all.

    Returns:
        See :func:`_edge_weights`; nodes are ``author_id`` values.
    """
    if authors.height == 0:
        return {}
    author_counts = (
        authors.select(["record_id", "author_id"])
        .unique()
        .group_by("author_id")
        .agg(pl.len().alias("count"))
    )
    eligible = frozenset(
        author_counts.filter(pl.col("count") >= min_occurrence).get_column("author_id").to_list()
    )
    if not eligible:
        return {}
    per_record = (
        authors.filter(pl.col("author_id").is_in(eligible))
        .select(["record_id", "author_id"])
        .unique()
        .group_by("record_id")
        .agg(pl.col("author_id").alias("ids"))
    )
    memberships = dict(
        zip(
            per_record.get_column("record_id").to_list(),
            per_record.get_column("ids").to_list(),
            strict=True,
        )
    )
    return _edge_weights(memberships)


def _label_communities(
    edge_weights: dict[tuple[str, str], int], *, resolution: float, seed: int
) -> dict[str, int]:
    """Louvain communities over ``edge_weights``, labelled deterministically.

    Args:
        edge_weights: See :func:`_edge_weights`.
        resolution: Forwarded to ``louvain_communities``.
        seed: Forwarded to ``louvain_communities`` -- the seed that makes
            this reproducible (ADR 0022 Decision 7).

    Returns:
        ``node_id -> community_id`` (``0``-based). Community ids are
        assigned by sorting each community's members and then sorting the
        communities by their lexicographically smallest member -- see this
        module's docstring's "Community-label determinism" section.
    """
    graph = nx.Graph()
    graph.add_weighted_edges_from((a, b, w) for (a, b), w in edge_weights.items())
    if graph.number_of_nodes() == 0:
        return {}
    found = louvain_communities(graph, resolution=resolution, seed=seed)
    ordered = sorted((sorted(community) for community in found), key=lambda members: members[0])
    return {node: index for index, members in enumerate(ordered) for node in members}


def _edges_to_frame(
    edge_weights: dict[tuple[str, str], int], labels: dict[str, str], *, top_n: int
) -> pl.DataFrame:
    """``edge_weights`` as a sorted, top-N-truncated :class:`polars.DataFrame`.

    Args:
        edge_weights: See :func:`_edge_weights`.
        labels: ``node_id -> display label`` (identical to the id for
            keyword nodes; an author's surname for author nodes).
        top_n: How many edges to keep, by weight descending.

    Returns:
        ``node_a``, ``node_a_label``, ``node_b``, ``node_b_label``,
        ``weight``, sorted by ``weight`` descending then ``node_a``,
        ``node_b`` ascending (a total order), truncated to ``top_n`` rows.
    """
    if not edge_weights:
        return pl.DataFrame(schema=_EMPTY_EDGE_SCHEMA)
    rows = sorted(
        (
            (node_a, labels.get(node_a, node_a), node_b, labels.get(node_b, node_b), weight)
            for (node_a, node_b), weight in edge_weights.items()
        ),
        key=lambda row: (-row[4], row[0], row[2]),
    )[:top_n]
    return pl.DataFrame(
        rows,
        schema=["node_a", "node_a_label", "node_b", "node_b_label", "weight"],
        orient="row",
    ).with_columns(pl.col("weight").cast(pl.Int64))


def keyword_cooccurrence_network(
    corpus: Corpus,
    *,
    stage: PrismaStage = PrismaStage.INCLUDED,
    kind: str = "author",
    min_occurrence: int = 2,
    top_n: int = 50,
    resolution: float = 1.0,
    seed: int = 0,
    stopwords_path: Path | None = None,
) -> AnalysisResult:
    """The keyword co-occurrence graph, clustered by Louvain community.

    Args:
        corpus: The corpus to read.
        stage: Which PRISMA-flow set to build the graph over.
        kind: ``"author"`` or ``"index"``.
        min_occurrence: A term must appear on at least this many records to
            be eligible to form an edge.
        top_n: How many edges to return, by weight.
        resolution: Forwarded to ``louvain_communities``.
        seed: Forwarded to ``louvain_communities``; recorded in ``params``
            so a clustering is reproducible (ADR 0022 Decision 7).
        stopwords_path: See
            :func:`prismabib.bibliometrics.keywords._load_stopwords`.

    Returns:
        An :class:`~prismabib.bibliometrics.base.AnalysisResult` whose
        ``data`` is the edge list (see :func:`_edges_to_frame`) and whose
        ``params`` also carries ``"communities"``: ``node_id -> community_id``
        for every node in the (untruncated) graph -- see
        :func:`_label_communities`.
    """
    records = corpus.records(stage)
    keywords = corpus.keywords(kind, stage)
    stopwords = _load_stopwords(stopwords_path)
    if stopwords:
        keywords = keywords.filter(~pl.col("term_norm").is_in(sorted(stopwords)))

    edge_weights = _cooccurrence_edge_weights(keywords, min_occurrence)
    communities = _label_communities(edge_weights, resolution=resolution, seed=seed)
    labels = {node: node for node in communities}
    data = _edges_to_frame(edge_weights, labels, top_n=top_n)

    params: dict[str, Any] = {
        "kind": kind,
        "min_occurrence": min_occurrence,
        "top_n": top_n,
        "resolution": resolution,
        "seed": seed,
        "communities": dict(sorted(communities.items())),
    }
    provenance = build_provenance(corpus, stage=stage, records=records)
    return AnalysisResult(data=data, params=params, provenance=provenance)


def coauthorship_network(
    corpus: Corpus,
    *,
    stage: PrismaStage = PrismaStage.INCLUDED,
    min_occurrence: int = 1,
    top_n: int = 50,
    resolution: float = 1.0,
    seed: int = 0,
) -> AnalysisResult:
    """The co-authorship graph, clustered by Louvain community.

    Args:
        corpus: The corpus to read.
        stage: Which PRISMA-flow set to build the graph over.
        min_occurrence: An author must appear on at least this many records
            to be eligible to form an edge.
        top_n: How many edges to return, by weight.
        resolution: Forwarded to ``louvain_communities``.
        seed: Forwarded to ``louvain_communities``; recorded in ``params``.

    Returns:
        An :class:`~prismabib.bibliometrics.base.AnalysisResult` whose
        ``data`` is the edge list (node ids are Scopus ``author_id``
        values, labels are surnames) and whose ``params`` also carries
        ``"communities"``.
    """
    records = corpus.records(stage)
    authors = corpus.authors(stage)
    edge_weights = _author_edge_weights(authors, min_occurrence)
    communities = _label_communities(edge_weights, resolution=resolution, seed=seed)
    labels = (
        dict(
            zip(
                authors.get_column("author_id").to_list(),
                authors.get_column("surname").to_list(),
                strict=True,
            )
        )
        if authors.height
        else {}
    )
    data = _edges_to_frame(edge_weights, labels, top_n=top_n)

    params: dict[str, Any] = {
        "min_occurrence": min_occurrence,
        "top_n": top_n,
        "resolution": resolution,
        "seed": seed,
        "communities": dict(sorted(communities.items())),
    }
    provenance = build_provenance(corpus, stage=stage, records=records)
    return AnalysisResult(data=data, params=params, provenance=provenance)


def _export_vosviewer(result: AnalysisResult, directory: Path) -> tuple[Path, Path]:
    """Write VOSviewer ``map.txt``/``network.txt`` for a network's :class:`AnalysisResult`.

    ADR 0022 Decision 7. Tab-separated, ``\\n`` line endings on every
    platform (matching ``report/tables.py::to_csv``'s reasoning: the
    ``csv`` module's default ``\\r\\n`` would make the exported bytes differ
    between a Windows and a Linux run).

    Args:
        result: The output of :func:`keyword_cooccurrence_network` or
            :func:`coauthorship_network`.
        directory: Where to write ``map.txt``/``network.txt``; created if
            absent.

    Returns:
        ``(map_path, network_path)``.
    """
    directory.mkdir(parents=True, exist_ok=True)
    communities_raw = result.params.get("communities", {})
    communities: dict[str, int] = communities_raw if isinstance(communities_raw, dict) else {}

    node_labels: dict[str, str] = {}
    node_weight: dict[str, int] = {}
    for row in result.data.iter_rows(named=True):
        node_labels.setdefault(row["node_a"], row["node_a_label"])
        node_labels.setdefault(row["node_b"], row["node_b_label"])
        node_weight[row["node_a"]] = node_weight.get(row["node_a"], 0) + row["weight"]
        node_weight[row["node_b"]] = node_weight.get(row["node_b"], 0) + row["weight"]

    ordered_nodes = sorted(node_labels)
    node_index = {node_id: position + 1 for position, node_id in enumerate(ordered_nodes)}

    map_path = directory / "map.txt"
    with map_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("id\tlabel\tweight\tcluster\n")
        for node_id in ordered_nodes:
            # No `.get` default: `communities` covers every node in the
            # graph and `data`'s nodes are a subset, so a miss is a bug in
            # this module rather than a case to paper over. A default of 0
            # made an unknown node indistinguishable from a genuine
            # community 0 in the exported file.
            cluster = communities[node_id]
            handle.write(
                f"{node_index[node_id]}\t{node_labels[node_id]}\t{node_weight[node_id]}\t{cluster + 1}\n"
            )

    network_path = directory / "network.txt"
    with network_path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write("id1\tid2\tweight\n")
        for row in result.data.sort(["node_a", "node_b"]).iter_rows(named=True):
            handle.write(
                f"{node_index[row['node_a']]}\t{node_index[row['node_b']]}\t{row['weight']}\n"
            )

    return map_path, network_path


__all__ = ["coauthorship_network", "keyword_cooccurrence_network"]
