"""The network figure's cluster fold, on both backends (ADR 0025 Decision 4).

Split from `test_figures.py` because the fold is the one place a figure adds
a sentence of its own, and it is the sentence the ADR calls "the point".
"""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from prismabib.bibliometrics.base import AnalysisResult, Provenance
from prismabib.stage import PrismaStage
from prismabib.viz import figures


def _network_result(*, cluster_count: int) -> AnalysisResult:
    """A network result whose drawn subgraph has exactly ``cluster_count`` clusters.

    Args:
        cluster_count: How many distinct communities the node rows carry.

    Returns:
        A long-format result in `network.py`'s own shape (`row_kind` of
        ``node``/``edge``), with two nodes per cluster and one edge joining
        them, so the drawn graph is connected within a cluster and the
        cluster count is exactly what was asked for -- the caption's
        denominator is then a property of the fixture, not of the code
        under test.
    """
    nodes = [
        {
            "row_kind": "node",
            "id": f"n{cluster}{side}",
            "label": f"term {cluster}{side}",
            "frequency": 10 + cluster,
            "cluster": cluster,
            "cluster_size": 2,
            "node_a": None,
            "node_a_label": None,
            "node_b": None,
            "node_b_label": None,
            "weight": None,
        }
        for cluster in range(cluster_count)
        for side in ("a", "b")
    ]
    edges = [
        {
            "row_kind": "edge",
            "id": None,
            "label": None,
            "frequency": None,
            "cluster": None,
            "cluster_size": None,
            "node_a": f"n{cluster}a",
            "node_a_label": f"term {cluster}a",
            "node_b": f"n{cluster}b",
            "node_b_label": f"term {cluster}b",
            "weight": 2,
        }
        for cluster in range(cluster_count)
    ]
    return AnalysisResult(
        data=pl.DataFrame(nodes + edges),
        params={"seed": 0, "top_n": 50, "min_occurrence": 1, "resolution": 1.0},
        provenance=Provenance(
            corpus_size=cluster_count * 2,
            stage=PrismaStage.RAW,
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            run_ids=("run-1",),
            criteria_versions=("1.0.0",),
        ),
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("cluster_count", "expected"),
    [
        (16, "3 of 16 communities shown; 13 folded to Other."),
        (6, "3 of 6 communities shown; 3 folded to Other."),
        (2, "2 of 2 communities shown; none folded to Other."),
    ],
    ids=["sixteen", "six", "under-the-cap"],
)
def test_network_figure__both_backends__state_the_cluster_fold(
    cluster_count: int, expected: str
) -> None:
    """ADR 0025 Decision 4's sentence must reach the surface a reader looks at.

    It reached matplotlib only. The dashboard and the notebook both render
    the *Plotly* figure, so on a sixteen-community graph the rendered
    picture showed three colours plus grey and said nothing -- exactly the
    misreading Decision 4 exists to prevent: "a reader seeing three colours
    on a 16-community graph must be told, or they will read three
    communities."

    Asserted on both backends in one test, because the defect was that the
    two disagreed. The under-the-cap row is here so the test cannot pass by
    always printing a fold: at two clusters nothing is folded and the
    sentence must say so.
    """
    plotly_figure, mpl_figure = figures.network_figure(_network_result(cluster_count=cluster_count))

    assert expected in plotly_figure.layout.title.text
    assert expected in mpl_figure.axes[0].get_title()
