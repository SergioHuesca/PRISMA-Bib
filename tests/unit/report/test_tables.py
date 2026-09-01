"""Table rendering: one truth, three renderings.

BUILD_PLAN §Stage 10 requires CSV, LaTeX ``booktabs`` and Markdown for every
table, and the golden test it names states the property that matters -- the
three files agree because they render one :class:`Table`, not because three
formatters happen to coincide today.
"""

from __future__ import annotations

import csv
import io
import re

import pytest

from prismabib.report.tables import (
    Table,
    _cell,
    latex_escape,
    to_csv,
    to_latex,
    to_markdown,
)

#: A table whose values exercise every rendering rule at once: a float (fixed
#: precision), a bool, an int, and a venue name carrying the characters LaTeX
#: would otherwise interpret. "Robotics & Automation" is not contrived -- it is
#: the shape of a real IEEE venue name.
SAMPLE = Table(
    slug="sample_table",
    caption="Venues & their share (%)",
    columns=("Venue", "Records", "Share", "Indexed"),
    rows=(
        ("Robotics & Automation", 96, 0.8125, True),
        ("Pattern_Analysis 100% Journal", 4, 0.0375, False),
    ),
)


def markdown_rows(rendered: str) -> list[list[str]]:
    """Parse a Markdown pipe table back into rows (helper, not a test)."""
    body = [
        line
        for line in rendered.splitlines()
        if line.startswith("|") and not set(line) <= set("| -")
    ]
    rows = [[cell.strip() for cell in line.strip("|").split("|")] for line in body]
    return rows[1:]


def latex_rows(rendered: str) -> list[list[str]]:
    """Parse a LaTeX tabular body back into rows (helper, not a test).

    Split on *unescaped* ``&`` only. A cell legitimately contains ``\\&`` --
    "Robotics \\& Automation" is the escaping this table has to get right --
    and splitting on it too would tear one cell into two and make the parser
    disagree with the renderer for the wrong reason.
    """
    body = rendered.split(r"\midrule")[1].split(r"\bottomrule")[0]
    return [
        [cell.strip() for cell in re.split(r"(?<!\\)&", line.replace(r"\\", ""))]
        for line in body.strip().splitlines()
        if line.strip()
    ]


@pytest.mark.unit
def test_tables__markdown_csv_and_latex__contain_identical_values() -> None:
    """Three renderings, one truth -- compared row by row, cell by cell.

    All three are parsed back and compared positionally. An earlier version
    asserted ``cell in markdown`` and ``escaped in latex`` -- substring
    presence -- which is the "appears somewhere" form this project's own
    diagram test warns against, and it is just as inert here: reversing the
    row order in ``to_markdown`` left it green, so a venue's record count
    could land beside the wrong venue name with the suite passing.
    """
    expected = [[_cell(value) for value in row] for row in SAMPLE.rows]

    parsed_csv = list(csv.reader(io.StringIO(to_csv(SAMPLE))))
    header, *csv_body = parsed_csv

    assert header == list(SAMPLE.columns)
    assert csv_body == expected
    assert markdown_rows(to_markdown(SAMPLE)) == expected
    assert latex_rows(to_latex(SAMPLE)) == [
        [latex_escape(cell) for cell in row] for row in expected
    ]


@pytest.mark.unit
def test_tables__row_order__is_identical_across_the_three_renderings() -> None:
    """The orderings must agree, not merely the multisets of values.

    Asserted separately because it is the specific failure a substring
    comparison cannot see: every value present in all three files, in three
    different orders, is three different tables.
    """
    assert [row[0] for row in markdown_rows(to_markdown(SAMPLE))] == [
        _cell(row[0]) for row in SAMPLE.rows
    ]
    assert [row[0] for row in latex_rows(to_latex(SAMPLE))] == [
        latex_escape(_cell(row[0])) for row in SAMPLE.rows
    ]


@pytest.mark.unit
def test_tables__latex__escapes_characters_that_would_not_compile() -> None:
    """An unescaped ``&`` or ``%`` is a LaTeX file that does not build.

    Asserted on the escaped output rather than by compiling, so the check runs
    everywhere; ``test_tables__latex__compiles_under_pdflatex_booktabs``
    covers the compile itself where TeX is available.
    """
    latex = to_latex(SAMPLE)

    assert r"Robotics \& Automation" in latex
    assert r"Pattern\_Analysis 100\% Journal" in latex
    assert "Robotics & Automation" not in latex


@pytest.mark.unit
def test_tables__latex__uses_booktabs_rules_and_no_vertical_lines() -> None:
    """``booktabs`` is what BUILD_PLAN names, and it forbids vertical rules."""
    latex = to_latex(SAMPLE)

    assert r"\toprule" in latex
    assert r"\midrule" in latex
    assert r"\bottomrule" in latex
    assert r"\hline" not in latex
    assert "|" not in latex.split(r"\begin{tabular}")[1].split("}")[0]


@pytest.mark.unit
def test_tables__csv__uses_unix_line_endings_on_every_platform() -> None:
    """``csv`` defaults to ``\\r\\n``, which would break byte reproducibility.

    Stage 11 requires a clean clone on a *different machine* to reproduce the
    export. A CSV whose line endings depend on the writing platform cannot
    satisfy that, and the failure would surface as an unexplained diff rather
    than as anything pointing at line endings.
    """
    rendered = to_csv(SAMPLE)

    assert "\r\n" not in rendered
    assert rendered.endswith("\n")


@pytest.mark.unit
def test_tables__float_cells__render_at_fixed_precision_everywhere() -> None:
    """A float must not render one way in CSV and another in LaTeX."""
    assert "0.81" in to_csv(SAMPLE)
    assert "0.81" in to_markdown(SAMPLE)
    assert "0.81" in to_latex(SAMPLE)
    assert "0.8125" not in to_csv(SAMPLE)


@pytest.mark.unit
def test_tables__empty_table__still_renders_a_header_in_each_format() -> None:
    """A corpus with no rows yet is a real state, not an error."""
    empty = Table(slug="empty", caption="Nothing yet", columns=("A", "B"), rows=())

    assert to_csv(empty) == "A,B\n"
    assert "| A | B |" in to_markdown(empty)
    assert r"\toprule" in to_latex(empty)
