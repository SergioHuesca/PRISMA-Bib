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

from prismabib.prisma.flow import FlowCounts, compute_flow_counts
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


#: Counts in which **no value can stand in for any other**: all fourteen are
#: pairwise distinct, and all four PRISMA identities close (asserted below, so
#: this is a fixture and not a fiction).
#:
#: This constant is the whole repair. The previous fixture was nine unscreened
#: records, which produces nine counts of ``0`` and four of ``9`` -- so every
#: count was interchangeable with several others, and a renderer that showed
#: ``unsure_fulltext`` under the ``included`` label passed the entire suite of
#: 741 tests. The published diagram then reads "Studies included in review
#: (n = 2)" for a review that included 5, which is BUILD_PLAN §1.4 exactly.
#:
#: Magnitude errors (a count rendered off by one) were already caught; what a
#: degenerate fixture cannot catch is an *identity* error, and identity is what
#: S10-AC4 is actually about.
DISTINCT_COUNTS = FlowCounts(
    identified=141,
    duplicates_across_searches=5,
    removed_other_reasons=8,
    excluded_automated=21,
    # Pairwise distinct, and distinct from every other value in this fixture,
    # for the same reason the integer counts are: a renderer that showed the
    # doc-type count under the subject-area label is an identity error, and
    # only distinct values can catch one. 1 + 2 + 6 + 12 == 21.
    excluded_automated_by_reason={"year": 12, "subject_area": 1, "doc_type": 6, "venue": 2},
    after_automated=107,
    excluded_language=13,
    after_language=94,
    excluded_title_abstract=11,
    unsure_title_abstract=60,
    retrieved_fulltext=23,
    excluded_fulltext={"INACCESSIBLE": 3, "NOT_PRIMARY_RESEARCH": 7},
    unsure_fulltext=4,
    included=9,
)

#: For each ``FlowCounts`` field: the box it belongs in, and the label that
#: introduces it there.
#:
#: Every pattern terminates the number -- with ``\)`` or an explicit ``(?!\d)``.
#: Without that, ``r"unsure: 9"`` matches the rendered text ``unsure: 90``, so a
#: ten-fold error passed this test; verified by injection.
FIELD_TO_LABEL = {
    "identified": ("identification", r"identified from Scopus \(n = {value}\)"),
    "duplicates_across_searches": (
        "removed-before-screening",
        r"duplicates across searches: {value}(?!\d)",
    ),
    "removed_other_reasons": ("removed-before-screening", r"other reasons: {value}(?!\d)"),
    "excluded_automated": ("after-automated", r"by automated filters: {value}(?!\d)"),
    "after_automated": ("after-automated", r"after automated filters \(n = {value}\)"),
    "excluded_language": ("after-language", r"excluded by language: {value}(?!\d)"),
    "after_language": ("after-language", r"Records screened \(n = {value}\)"),
    "excluded_title_abstract": (
        "title-abstract",
        r"excluded at title/abstract: {value}(?!\d)",
    ),
    "unsure_title_abstract": ("title-abstract", r"unsure: {value}(?!\d)"),
    "retrieved_fulltext": ("title-abstract", r"sought for retrieval \(n = {value}\)"),
    "unsure_fulltext": ("fulltext", r"unsure at full text: {value}(?!\d)"),
    "included": ("fulltext", r"included in review \(n = {value}\)"),
}


#: Reason key -> the label that introduces its count in the ``after-automated``
#: box. Kept separate from :data:`FIELD_TO_LABEL` because these come from a
#: mapping field rather than an integer one, so the ``dataclasses.fields`` sweep
#: cannot reach them -- and an unreached line is an unchecked line.
REASON_TO_LABEL = {
    "year": r"by publication year: {value}(?!\d)",
    "subject_area": r"by subject area: {value}(?!\d)",
    "doc_type": r"by document type: {value}(?!\d)",
    "venue": r"by conference whitelist: {value}(?!\d)",
}


@pytest.mark.integration
@pytest.mark.acceptance("S10-AC4")
def test_flow_diagram__generated_numbers__equal_flowcounts() -> None:
    """Every count is rendered, in its own box, under its own label, exactly.

    Driven from ``dataclasses.fields`` so a count added to ``FlowCounts`` is
    automatically required to reach the diagram, and cross-checked against
    :data:`FIELD_TO_LABEL` so a field added and *not* placed fails here.

    Uses :data:`DISTINCT_COUNTS` rather than a built project: with pairwise
    distinct values, a renderer that puts the right *kind* of number in the
    wrong box fails, which a fixture full of zeros cannot detect. The
    integration path -- that these counts come from a real store and log -- is
    covered by ``test_flow_diagram__real_project__renders_its_own_counts``.
    """
    DISTINCT_COUNTS.assert_consistent()

    svg = flow_diagram_svg(DISTINCT_COUNTS, title="A Review")

    integer_fields = {
        field.name
        for field in dataclasses.fields(DISTINCT_COUNTS)
        if isinstance(getattr(DISTINCT_COUNTS, field.name), int)
    }
    assert integer_fields == set(FIELD_TO_LABEL), "a FlowCounts field has no label on the diagram"

    for field_name, (box_id, template) in FIELD_TO_LABEL.items():
        value = getattr(DISTINCT_COUNTS, field_name)
        text = box_text(svg, box_id)
        assert re.search(template.format(value=value), text), (
            f"{field_name}={value} is not shown under its label in the {box_id!r} box; "
            f"it reads {text!r}"
        )

    automated_text = box_text(svg, "after-automated")
    assert set(REASON_TO_LABEL) == set(DISTINCT_COUNTS.excluded_automated_by_reason), (
        "an automated-exclusion reason has no label on the diagram"
    )
    for reason, template in REASON_TO_LABEL.items():
        count = DISTINCT_COUNTS.excluded_automated_by_reason[reason]
        assert re.search(template.format(value=count), automated_text), (
            f"{reason}={count} is not shown under its label in the 'after-automated' "
            f"box; it reads {automated_text!r}"
        )


@pytest.mark.integration
@pytest.mark.acceptance("S10-AC4")
def test_flow_diagram__fulltext_exclusion_reasons__are_rendered_with_their_counts() -> None:
    """PRISMA 2020 requires full-text exclusions reported *with reasons*.

    ``excluded_fulltext`` is a mapping, so the ``isinstance(..., int)`` filter
    above skips it -- and it was skipped everywhere else too: the ordering test
    checks only ordering, and the export CSV check skips these rows outright.
    A renderer emitting ``n + 1`` for every reason therefore published wrong
    exclusion counts with the whole suite green.
    """
    svg = flow_diagram_svg(DISTINCT_COUNTS, title="A Review")

    text = box_text(svg, "fulltext")
    for code, count in DISTINCT_COUNTS.excluded_fulltext.items():
        assert re.search(rf"{re.escape(code)}: {count}(?!\d)", text), (
            f"{code}={count} is not shown with its count; the box reads {text!r}"
        )


@pytest.mark.integration
def test_flow_diagram__real_project__renders_its_own_counts(tmp_path: Path) -> None:
    """The wiring: counts drawn on the diagram come from a real store and log.

    :data:`DISTINCT_COUNTS` proves the renderer places numbers correctly; this
    proves the numbers it is handed are the project's own.
    """
    project = build_project(tmp_path, CORPUS, slug="diagram")
    counts = compute_flow_counts(project)

    svg = flow_diagram_svg(counts, title="A Review")

    assert re.search(rf"identified from Scopus \(n = {counts.identified}\)", svg)
    assert re.search(rf"Records screened \(n = {counts.after_language}\)", svg)


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


@pytest.mark.integration
def test_flow_diagram__every_text_line__is_drawn_inside_its_own_box() -> None:
    """No box's text escapes its rect, however many lines the box has.

    The box height was a fixed constant, sized by eye for the two- and
    three-line boxes that existed when the renderer was written. ADR 0016 gave
    the ``after-automated`` box six lines, and the centring arithmetic put the
    first baseline above the rect and the last below it -- the heading and the
    only non-zero exclusion count were drawn *outside* the box, struck through
    by the arrows, in the figure shipped in the docs and produced by
    ``prismabib export``.

    Nothing caught it: every other assertion in this module reads the
    ``<text>`` elements' *content*, and the docs-asset golden compares bytes
    against a file regenerated from the same broken renderer. Both are blind
    to where the text is drawn. This test is not.
    """
    svg = flow_diagram_svg(DISTINCT_COUNTS, title="A Review")

    groups = re.findall(r'<g id="([^"]+)">(.*?)</g>', svg, re.DOTALL)
    assert groups, "no boxes found in the rendered SVG"

    for box_id, group in groups:
        rect = re.search(r'<rect[^>]*\by="(-?\d+)"[^>]*\bheight="(\d+)"', group)
        assert rect, f"box {box_id!r} has no rect"
        top, height = int(rect.group(1)), int(rect.group(2))
        baselines = [int(y) for y in re.findall(r'<text[^>]*\by="(-?\d+)"', group)]
        assert baselines, f"box {box_id!r} has no text"
        outside = [y for y in baselines if not top <= y <= top + height]
        assert not outside, (
            f"box {box_id!r} spans y={top}..{top + height} but has text baselines at "
            f"{outside} -- that text is drawn outside its box, over the arrows"
        )
