"""The PRISMA 2020 flow diagram, rendered from :class:`FlowCounts` alone.

BUILD_PLAN §Stage 10, acceptance criterion S10-AC4: *"The generated PRISMA
diagram's numbers equal ``FlowCounts`` by test assertion."* That is the whole
contract of this module, and it is why nothing here computes a count. Every
number on the diagram is read from a field; the renderer's only job is
placing them.

**Why hand-written SVG rather than a plotting library.** Two reasons, and the
second is the load-bearing one.

The diagram is boxes, arrows and text -- a plotting library buys nothing for
that shape. More importantly, a rasterised figure is not reproducible across
machines: PNG output embeds font metrics and library versions, so the same
counts render to different bytes on a different box. Stage 11's acceptance
criterion is that *a clean clone on a different machine reproduces the
export*, and a figure that cannot survive that is a figure that has to be
excluded from it. Text SVG with explicit geometry has no such dependency: the
same ``FlowCounts`` produces the same bytes anywhere, which is the property a
citable artefact needs.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Final
from xml.sax.saxutils import escape

if TYPE_CHECKING:
    from collections.abc import Sequence

    from prismabib.prisma.flow import FlowCounts

#: Geometry, in user units. Fixed rather than computed from text length: a
#: layout that depended on the rendered width of a label would depend on the
#: font, and this file exists partly to have no font dependency.
_BOX_WIDTH = 300
_GAP_Y = 42
_LEFT_X = 40
_TOP_Y = 40
_LINE_HEIGHT = 17
_BOX_PADDING_Y = 12
_CANVAS_WIDTH = 760


def _box_height(line_count: int) -> int:
    """The height a box needs to hold ``line_count`` lines of text.

    Args:
        line_count: How many text lines the box will contain.

    Returns:
        The rect height, in user units.

    A box was a fixed 64 units tall, sized by eye for the two- and three-line
    boxes that existed when this file was written. ADR 0016 gave the
    ``after-automated`` box six lines, and the centring arithmetic then put the
    first baseline *above* the rect and the last *below* it: the heading and
    the only non-zero exclusion count were drawn outside the box, struck
    through by the arrows. Every test still passed, because they assert on the
    ``<text>`` elements' content and a byte comparison against a file
    regenerated from the same renderer -- both blind to geometry.

    Deriving the height from the line count means a box cannot overflow no
    matter how many lines a future reason adds.
    """
    return _LINE_HEIGHT * (line_count - 1) + 2 * _BOX_PADDING_Y + _LINE_HEIGHT


#: Reason-key -> figure label. Duplicated deliberately from ``cli.py``'s own
#: table: ``report/`` is importable without the CLI, and the figure's wording
#: is a publication surface that should not change because a terminal string
#: was reworded.
_AUTOMATED_REASON_LABELS: Final[Mapping[str, str]] = {
    "year": "by publication year",
    "subject_area": "by subject area",
    "doc_type": "by document type",
    "venue": "by conference whitelist",
}


def _automated_reason_lines(counts: FlowCounts) -> list[str]:
    """One line per automated-exclusion reason, in precedence order.

    Args:
        counts: The flow counts being drawn.

    Returns:
        A line per reason, including reasons that excluded nothing. A reason
        omitted at zero would make "we did not filter on subject area" and "we
        filtered and it excluded nothing" look identical in the published
        figure, and those are different methodological claims. Order is the
        attribution order (ADR 0016), not alphabetical: a record is charged to
        the first criterion it fails, so "by subject area" means *passed the
        year test and failed this one*, which only the order conveys.
    """
    return [
        f"  {_AUTOMATED_REASON_LABELS[reason]}: {count}"
        for reason, count in counts.excluded_automated_by_reason.items()
    ]


def _identification_note(counts: FlowCounts) -> str:
    """The parenthetical on the "records removed before screening" box.

    Args:
        counts: The flow counts being rendered.

    Returns:
        A breakdown naming both removal reasons, matching equation 1's terms
        so a reader can check the subtraction against the diagram itself.
    """
    return (
        f"duplicates across searches: {counts.duplicates_across_searches}; "
        f"other reasons: {counts.removed_other_reasons}"
    )


def _fulltext_exclusion_note(counts: FlowCounts) -> str:
    """The reason breakdown on the full-text exclusion box.

    PRISMA 2020 requires full-text exclusions to be reported *with reasons*,
    which is why ``excluded_fulltext`` is a mapping rather than an integer.
    Reasons are emitted in sorted order so the diagram is a fixed function of
    the counts -- dict ordering is insertion order, and the insertion order
    here comes from a SQL result set.

    Args:
        counts: The flow counts being rendered.

    Returns:
        ``"CODE: n; CODE: n"``, or a placeholder when no full-text
        exclusions have been recorded.
    """
    if not counts.excluded_fulltext:
        return "no full-text exclusions recorded"
    return "; ".join(f"{code}: {n}" for code, n in sorted(counts.excluded_fulltext.items()))


def _box(x: int, y: int, lines: Sequence[str], *, box_id: str) -> str:
    """One labelled box.

    Args:
        x: Left edge.
        y: Top edge.
        lines: Text lines, rendered top-down and XML-escaped.
        box_id: Stable ``id`` attribute, so a test (or a reader) can address
            a specific box rather than matching on prose.

    Returns:
        SVG markup for the box and its text.
    """
    height = _box_height(len(lines))
    parts = [
        f'<g id="{escape(box_id)}">',
        (
            f'<rect x="{x}" y="{y}" width="{_BOX_WIDTH}" height="{height}" '
            'fill="#ffffff" stroke="#333333" stroke-width="1.5"/>'
        ),
    ]
    first_baseline = y + (height - _LINE_HEIGHT * (len(lines) - 1)) // 2 + 4
    for index, line in enumerate(lines):
        parts.append(
            f'<text x="{x + _BOX_WIDTH // 2}" y="{first_baseline + index * _LINE_HEIGHT}" '
            'text-anchor="middle" font-family="Helvetica, Arial, sans-serif" '
            f'font-size="12">{escape(line)}</text>'
        )
    parts.append("</g>")
    return "".join(parts)


def _arrow(x1: int, y1: int, x2: int, y2: int) -> str:
    """A single arrow between two points.

    Args:
        x1: Start x.
        y1: Start y.
        x2: End x.
        y2: End y.

    Returns:
        SVG markup for the line, with the shared arrowhead marker.
    """
    return (
        f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
        'stroke="#333333" stroke-width="1.5" marker-end="url(#arrow)"/>'
    )


def flow_diagram_svg(counts: FlowCounts, *, title: str) -> str:
    """Render ``counts`` as a PRISMA 2020 flow diagram.

    Args:
        counts: The counts to render. Every number on the diagram comes from
            one of its fields; this function computes none of them, which is
            what makes S10-AC4 assertable rather than aspirational.
        title: The project title, placed above the diagram.

    Returns:
        A complete SVG document as text. The same ``counts`` and ``title``
        always produce identical bytes -- no timestamps, no font metrics, no
        dictionary ordering (see :func:`_fulltext_exclusion_note`).
    """
    stages: list[tuple[str, list[str]]] = [
        (
            "identification",
            [f"Records identified from Scopus (n = {counts.identified})"],
        ),
        (
            "removed-before-screening",
            [
                (
                    "Records removed before screening (n = "
                    f"{counts.duplicates_across_searches + counts.removed_other_reasons})"
                ),
                _identification_note(counts),
            ],
        ),
        (
            "after-automated",
            [
                f"Records after automated filters (n = {counts.after_automated})",
                f"excluded by automated filters: {counts.excluded_automated}",
                *_automated_reason_lines(counts),
            ],
        ),
        (
            "after-language",
            [
                f"Records screened (n = {counts.after_language})",
                f"excluded by language: {counts.excluded_language}",
            ],
        ),
        (
            "title-abstract",
            [
                f"Reports sought for retrieval (n = {counts.retrieved_fulltext})",
                (
                    f"excluded at title/abstract: {counts.excluded_title_abstract}; "
                    f"unsure: {counts.unsure_title_abstract}"
                ),
            ],
        ),
        (
            "fulltext",
            [
                f"Studies included in review (n = {counts.included})",
                f"unsure at full text: {counts.unsure_fulltext}",
                _fulltext_exclusion_note(counts),
            ],
        ),
    ]

    body: list[str] = []
    y = _TOP_Y
    for index, (box_id, lines) in enumerate(stages):
        box_height = _box_height(len(lines))
        body.append(_box(_LEFT_X, y, lines, box_id=box_id))
        if index < len(stages) - 1:
            body.append(
                _arrow(
                    _LEFT_X + _BOX_WIDTH // 2,
                    y + box_height,
                    _LEFT_X + _BOX_WIDTH // 2,
                    y + box_height + _GAP_Y,
                )
            )
        y += box_height + _GAP_Y

    height = y + 20
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_CANVAS_WIDTH}" height="{height}" '
        f'viewBox="0 0 {_CANVAS_WIDTH} {height}">'
        "<defs>"
        '<marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" '
        'markerHeight="6" orient="auto-start-reverse">'
        '<path d="M 0 0 L 10 5 L 0 10 z" fill="#333333"/>'
        "</marker>"
        "</defs>"
        f"<title>{escape(title)} — PRISMA 2020 flow</title>"
        f'<text x="{_LEFT_X}" y="24" font-family="Helvetica, Arial, sans-serif" '
        f'font-size="14" font-weight="bold">{escape(title)}</text>'
        f"{''.join(body)}"
        "</svg>\n"
    )


__all__ = ["flow_diagram_svg"]
