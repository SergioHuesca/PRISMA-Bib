"""Figure 9 (the PRISMA flow diagram, wrapped) is pinned to ``FlowCounts`` (BUILD_PLAN §Stage 9).

``test_flow_diagram__svg__contains_exactly_the_flowcounts_numbers``: parses
every ``<text>`` node in the SVG :func:`~prismabib.viz.figures.prisma_flow_figure`
returns, extracts every integer substring, and checks it against
``FlowCounts``'s own field values.

The comparison is "every value ``FlowCounts`` computed is somewhere in the
rendered text" (a subset check) rather than strict set equality: the
``removed-before-screening`` box legitimately shows one *sum* of two
``FlowCounts`` fields as its own ``n = ...`` (``report/flow_diagram.py``
computes nothing else) -- see that module for why. The much stricter
per-box, per-label check (``test_flow_diagram__generated_numbers__equal_flowcounts``,
S10-AC4) already exists in ``tests/integration/report/test_flow_diagram.py``
and is reused here rather than a second fixture invented for this stage --
:data:`~tests.integration.report.test_flow_diagram.DISTINCT_COUNTS`'s whole
point is that no two fields share a value, which is exactly what makes an
identity error ("the doc-type count rendered as duplicates") detectable at
all.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING
from xml.etree import ElementTree

import pytest

from prismabib.viz.figures import prisma_flow_figure
from tests.integration.report.test_flow_diagram import DISTINCT_COUNTS
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project

if TYPE_CHECKING:
    from prismabib.prisma.flow import FlowCounts

_INTEGER_RE = re.compile(r"\d+")


def _every_text_node_integer(svg: str) -> set[int]:
    """Every integer substring appearing in any ``<text>`` element's content."""
    root = ElementTree.fromstring(svg)
    namespace = "{http://www.w3.org/2000/svg}"
    found: set[int] = set()
    for node in root.iter(f"{namespace}text"):
        text = node.text or ""
        found.update(int(match) for match in _INTEGER_RE.findall(text))
    return found


def _every_flowcounts_value(counts: FlowCounts) -> set[int]:
    """Every integer ``FlowCounts`` itself carries -- top-level fields and dict values."""
    values: set[int] = set()
    for field in (
        counts.identified,
        counts.duplicates_across_searches,
        counts.removed_other_reasons,
        counts.excluded_automated,
        counts.after_automated,
        counts.excluded_language,
        counts.after_language,
        counts.excluded_title_abstract,
        counts.unsure_title_abstract,
        counts.retrieved_fulltext,
        counts.unsure_fulltext,
        counts.included,
    ):
        values.add(field)
    values.update(counts.excluded_automated_by_reason.values())
    values.update(counts.excluded_fulltext.values())
    return values


@pytest.mark.golden
def test_flow_diagram__svg__contains_exactly_the_flowcounts_numbers(tmp_path: Path) -> None:
    DISTINCT_COUNTS.assert_consistent()
    # A real project, for its `.title`/`.criteria.version` only -- its own
    # (unrelated) five-record corpus is never read by `prisma_flow_figure`,
    # which draws exclusively from the `DISTINCT_COUNTS` argument.
    project = build_project(
        tmp_path,
        CorpusSpec(
            records=[RecordSpec(number=n) for n in range(1, 6)],
            criteria=CriteriaSpec(abstract_reason_codes=("OFF_TOPIC",)),
        ),
    )

    svg, _mpl_figure = prisma_flow_figure(DISTINCT_COUNTS, project)

    extracted = _every_text_node_integer(svg)
    expected = _every_flowcounts_value(DISTINCT_COUNTS)

    missing = expected - extracted
    assert not missing, f"FlowCounts values never rendered anywhere in the SVG: {missing}"


@pytest.mark.unit
def test_every_flowcounts_value__missing_from_svg__is_detected() -> None:
    """The check itself can fail: an SVG missing one of ``FlowCounts``'s numbers is caught."""
    expected = _every_flowcounts_value(DISTINCT_COUNTS)
    incomplete_svg = "<svg><text>nothing relevant here: 999999</text></svg>"

    extracted = _every_text_node_integer(incomplete_svg)

    assert expected - extracted == expected
