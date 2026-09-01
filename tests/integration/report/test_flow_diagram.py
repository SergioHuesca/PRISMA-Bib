"""S10-AC4: the diagram's numbers equal ``FlowCounts``.

The criterion BUILD_PLAN states as *"by test assertion"*, which is the whole
reason :mod:`~prismabib.report.flow_diagram` computes nothing: every number it
draws is read from a field, so this test can compare the rendered text against
the dataclass field by field rather than against a hand-written expectation.
"""

from __future__ import annotations

import dataclasses
import re
from typing import TYPE_CHECKING
from xml.etree import ElementTree

import pytest

from prismabib.prisma.flow import compute_flow_counts
from prismabib.report.flow_diagram import flow_diagram_svg
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project

if TYPE_CHECKING:
    from pathlib import Path

CORPUS = CorpusSpec(
    records=[RecordSpec(number=n) for n in range(1, 10)],
    criteria=CriteriaSpec(abstract_reason_codes=("OFF_TOPIC",)),
)


def box_text(svg: str, box_id: str) -> str:
    """Every text node inside one labelled box (helper, not a test).

    Parsed out by ``id`` rather than searched for across the whole document,
    and that distinction is the test. A first draft asserted only that each
    count appeared *somewhere* in the SVG -- which passes while a box shows
    the wrong number, because the document is full of other integers: other
    counts, and the ``x``/``y``/``width`` geometry. Verified by injecting
    ``after_language - 1`` into the renderer: the loose form stayed green.
    """
    root = ElementTree.fromstring(svg)
    namespace = "{http://www.w3.org/2000/svg}"
    for group in root.iter(f"{namespace}g"):
        if group.get("id") == box_id:
            return " ".join(node.text or "" for node in group.iter(f"{namespace}text"))
    raise AssertionError(f"no box with id {box_id!r} on the diagram")


#: For each ``FlowCounts`` field: the box it belongs in, and the label that
#: introduces it there. Both halves are needed, and the label is the half a
#: first draft lacked.
#:
#: Asserting only "the value appears in the right box" is not enough when a box
#: carries two counts. Verified by injection: with ``included`` rendered as
#: ``included + 1``, a bare search still passed, because the same box shows
#: ``unsure at full text: 0`` and the expected ``0`` was found there. Anchoring
#: to the label is what makes the assertion about *this* number.
FIELD_TO_LABEL = {
    "identified": ("identification", r"identified from Scopus \(n = {value}\)"),
    "duplicates_across_searches": (
        "removed-before-screening",
        r"duplicates across searches: {value}",
    ),
    "removed_other_reasons": ("removed-before-screening", r"other reasons: {value}"),
    "excluded_automated": ("after-automated", r"year/subject/doc-type: {value}"),
    "after_automated": ("after-automated", r"after automated filters \(n = {value}\)"),
    "excluded_language": ("after-language", r"excluded by language: {value}"),
    "after_language": ("after-language", r"Records screened \(n = {value}\)"),
    "excluded_title_abstract": ("title-abstract", r"excluded at title/abstract: {value}"),
    "unsure_title_abstract": ("title-abstract", r"unsure: {value}"),
    "retrieved_fulltext": ("title-abstract", r"sought for retrieval \(n = {value}\)"),
    "unsure_fulltext": ("fulltext", r"unsure at full text: {value}"),
    "included": ("fulltext", r"included in review \(n = {value}\)"),
}


@pytest.mark.integration
@pytest.mark.acceptance("S10-AC4")
def test_flow_diagram__generated_numbers__equal_flowcounts(tmp_path: Path) -> None:
    """Every integer field is rendered, under its own label, with its exact value.

    Driven from ``dataclasses.fields`` so a count added to ``FlowCounts`` is
    automatically required to reach the diagram, and cross-checked against
    :data:`FIELD_TO_LABEL` so a field that is added and *not* placed fails
    here rather than being silently omitted.
    """
    project = build_project(tmp_path, CORPUS, slug="diagram")
    counts = compute_flow_counts(project)

    svg = flow_diagram_svg(counts, title="A Review")

    integer_fields = {
        field.name
        for field in dataclasses.fields(counts)
        if isinstance(getattr(counts, field.name), int)
    }
    assert integer_fields == set(FIELD_TO_LABEL), "a FlowCounts field has no label on the diagram"

    for field_name, (box_id, template) in FIELD_TO_LABEL.items():
        value = getattr(counts, field_name)
        text = box_text(svg, box_id)
        pattern = template.format(value=value)
        assert re.search(pattern, text), (
            f"{field_name}={value} is not shown under its label in the {box_id!r} box; "
            f"it reads {text!r}"
        )


@pytest.mark.integration
def test_flow_diagram__same_counts__render_identical_bytes(tmp_path: Path) -> None:
    """The diagram is a fixed function of its counts.

    Stage 11 requires a clean clone on a *different machine* to reproduce the
    export. A renderer that embedded a timestamp, or that iterated a dict in
    insertion order, would pass every other test here and fail that one --
    months later, as an unexplained diff.
    """
    project = build_project(tmp_path, CORPUS, slug="diagram-stable")
    counts = compute_flow_counts(project)

    first = flow_diagram_svg(counts, title="A Review")
    second = flow_diagram_svg(counts, title="A Review")

    assert first == second
    assert not re.search(r"20\d\d-\d\d-\d\d", first), "a date leaked into the diagram"


@pytest.mark.integration
def test_flow_diagram__title_with_markup_characters__is_escaped(tmp_path: Path) -> None:
    """A project title is user input, and SVG is XML.

    A title containing ``&`` or ``<`` would otherwise produce a document no
    viewer can parse -- the figure fails to open rather than looking wrong,
    which is a worse failure for something bound for a manuscript.
    """
    project = build_project(tmp_path, CORPUS, slug="diagram-escape")
    counts = compute_flow_counts(project)

    svg = flow_diagram_svg(counts, title="Robotics & <Vision>")

    assert "Robotics &amp; &lt;Vision&gt;" in svg
    assert "Robotics & <Vision>" not in svg


@pytest.mark.integration
def test_flow_diagram__fulltext_reasons__are_rendered_in_sorted_order(tmp_path: Path) -> None:
    """PRISMA requires full-text exclusions reported *with reasons*.

    Sorted rather than in mapping order, because that mapping is built from a
    SQL result set -- and a diagram whose reason order depended on the
    database's row order would not be reproducible.
    """
    import dataclasses as dc

    project = build_project(tmp_path, CORPUS, slug="diagram-reasons")
    counts = compute_flow_counts(project)
    with_reasons = dc.replace(counts, excluded_fulltext={"WRONG_POPULATION": 2, "NO_FULL_TEXT": 1})

    svg = flow_diagram_svg(with_reasons, title="A Review")

    assert svg.index("NO_FULL_TEXT") < svg.index("WRONG_POPULATION")
