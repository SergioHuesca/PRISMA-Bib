"""Generated tables: one truth, three renderings.

BUILD_PLAN §Stage 10 requires every table as CSV, LaTeX ``booktabs`` and
Markdown, and the golden test
``test_tables__markdown_csv_and_latex__contain_identical_values`` states the
property that matters: the three files are renderings of one :class:`Table`,
never three formatters that happen to agree today.

**Scope (ADR 0015).** BUILD_PLAN also lists a taxonomy distribution, a
dataset/benchmark usage table and a research-gap table. Those read Stage 8 and
Stage 9 output, which does not exist -- the amended plan moved this stage ahead
of 6-9 on purpose. The four tables here are the ones Layer 1 and the decision
log can support today, and they are enough for every Stage 10 acceptance
criterion.

The renderers are module-level functions rather than methods on :class:`Table`.
That is deliberate: mutmut does not mutate the body of a decorated class, so a
method on a ``@dataclass`` generates no mutants at all -- the trap that hid
``FlowCounts.assert_consistent``'s mutants until Stage 10 went looking.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from prismabib.report.numbers import TOP_N
from prismabib.store.db import connect

if TYPE_CHECKING:
    import duckdb

    from prismabib.project import Project


@dataclass(frozen=True)
class Table:
    """One generated table, independent of how it is rendered.

    Attributes:
        slug: Filename stem, used for every rendering of this table.
        caption: One-line description, used as the LaTeX caption and the
            Markdown heading.
        columns: Column headers, in order.
        rows: Row values, each the same length as ``columns``. Values are
            scalars for the same reason ``numbers.json``'s are.
    """

    slug: str
    caption: str
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]


def _cell(value: Any) -> str:
    """Render one value as text, identically for every output format.

    Args:
        value: A scalar from a :class:`Table` row.

    Returns:
        Its string form. Floats are fixed to two decimals so that CSV,
        Markdown and LaTeX cannot disagree about precision -- the golden test
        compares the three, and a difference of formatting would read as a
        difference of value.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def to_csv(table: Table) -> str:
    """Render ``table`` as CSV.

    Args:
        table: The table to render.

    Returns:
        CSV text with a header row, ``\\n`` line endings on every platform
        (``csv`` defaults to ``\\r\\n``, which would make the exported bytes
        differ between a Windows and a Linux run and break Stage 11's
        reproducibility criterion).
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(table.columns)
    for row in table.rows:
        writer.writerow([_cell(value) for value in row])
    return buffer.getvalue()


def to_markdown(table: Table) -> str:
    """Render ``table`` as a Markdown pipe table.

    Args:
        table: The table to render.

    Returns:
        Markdown text, headed by the caption.
    """
    lines = [f"### {table.caption}", "", "| " + " | ".join(table.columns) + " |"]
    lines.append("| " + " | ".join("---" for _ in table.columns) + " |")
    for row in table.rows:
        lines.append("| " + " | ".join(_cell(value) for value in row) + " |")
    return "\n".join(lines) + "\n"


def latex_escape(text: str) -> str:
    """Escape the characters LaTeX would otherwise interpret.

    Args:
        text: A rendered cell.

    Returns:
        The same text, safe inside a tabular cell. Venue names really do
        contain ``&`` -- "Robotics & Automation" -- so an unescaped table is
        one that fails to compile on a realistic corpus rather than a
        contrived one.
    """
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in text)


def to_latex(table: Table) -> str:
    """Render ``table`` as a LaTeX ``booktabs`` table.

    Args:
        table: The table to render.

    Returns:
        A complete ``table`` environment. ``booktabs`` rules only -- no
        vertical lines -- which is what the package is for and what a journal
        template expects.
    """
    column_spec = "l" * len(table.columns)
    lines = [
        r"\begin{table}[htbp]",
        r"\centering",
        f"\\caption{{{latex_escape(table.caption)}}}",
        f"\\label{{tab:{table.slug.replace('_', '-')}}}",
        f"\\begin{{tabular}}{{{column_spec}}}",
        r"\toprule",
        " & ".join(latex_escape(column) for column in table.columns) + r" \\",
        r"\midrule",
    ]
    lines.extend(
        " & ".join(latex_escape(_cell(value)) for value in row) + r" \\" for row in table.rows
    )
    lines.extend([r"\bottomrule", r"\end{tabular}", r"\end{table}"])
    return "\n".join(lines) + "\n"


def eligibility_criteria_table(project: Project) -> Table:
    """The eligibility criteria as a reader-checkable matrix.

    PRISMA 2020 item 5 requires the eligibility criteria to be reported. This
    renders them from ``criteria.yaml`` rather than from prose, so the table
    in the manuscript cannot drift from the criteria the engine actually
    applied.

    Args:
        project: The project whose criteria to tabulate.

    Returns:
        A criterion/value table, one row per declared restriction.
    """
    criteria = project.criteria
    rows: list[tuple[Any, ...]] = [
        ("Criteria version", criteria.version),
        ("Year range", f"{criteria.temporal.year_start}-{criteria.temporal.year_end}"),
        ("Languages", ", ".join(criteria.languages) or "(unrestricted)"),
        ("Document types", ", ".join(criteria.doc_types.include) or "(unrestricted)"),
        ("Subject areas", ", ".join(criteria.subject_areas) or "(unrestricted)"),
        (
            "Conference whitelist",
            ", ".join(criteria.doc_types.conference_whitelist) or "(unrestricted)",
        ),
    ]
    return Table(
        slug="eligibility_criteria",
        caption="Eligibility criteria applied by the automated filters",
        columns=("Criterion", "Value"),
        rows=tuple(rows),
    )


def top_venues_table(connection: duckdb.DuckDBPyConnection) -> Table:
    """The most frequent publication venues.

    Args:
        connection: An open, read-only Layer 1 connection.

    Returns:
        Venue, type and record count, ordered by count then name so the
        ordering is total and does not depend on the engine's tie-breaking.
    """
    # Grouped by name alone, matching `numbers._venue_numbers`. Grouping by
    # `(name, venue_type)` splits one venue into several rows whenever Scopus
    # indexes it under more than one `prism:aggregationType` -- so this table
    # would show "Robotics & Automation, journal, 56" and "..., conference, 40"
    # beside a `{{venues.top1.count}}` of 96 in the prose. Two definitions of
    # "a venue" inside one export bundle is the drift this stage exists to
    # prevent, and `citation_statistics_table` already avoids it by reading
    # `numbers` rather than re-querying.
    #
    # `venue_type` is then reported per name: the single type when there is
    # one, "mixed" when Scopus disagrees with itself. Naming the disagreement
    # is more useful to a reader than silently picking one of the two.
    rows = connection.execute(
        """
        SELECT
          v.name,
          CASE
            WHEN count(DISTINCT COALESCE(v.venue_type, '')) > 1 THEN 'mixed'
            ELSE COALESCE(min(v.venue_type), '')
          END AS venue_type,
          count(*) AS n
        FROM records r JOIN venues v ON r.venue_id = v.venue_id
        GROUP BY v.name
        ORDER BY n DESC, v.name ASC
        LIMIT ?
        """,
        [TOP_N],
    ).fetchall()
    return Table(
        slug="top_venues",
        caption=f"Top {TOP_N} venues by record count",
        columns=("Venue", "Type", "Records"),
        rows=tuple((name, venue_type, int(count)) for name, venue_type, count in rows),
    )


def top_cited_table(connection: duckdb.DuckDBPyConnection) -> Table:
    """The most-cited records, by their latest citation snapshot.

    Args:
        connection: An open, read-only Layer 1 connection.

    Returns:
        Record id, year, title and citation count. The title is included
        because a table of ids is not something a reader can check.
    """
    rows = connection.execute(
        """
        SELECT r.record_id, COALESCE(r.year, 0), COALESCE(r.title, ''), s.cited_by_count
        FROM records r JOIN citation_snapshots s ON r.record_id = s.record_id
        WHERE s.retrieved_at = (
            SELECT max(retrieved_at) FROM citation_snapshots t WHERE t.record_id = r.record_id
        )
        ORDER BY s.cited_by_count DESC, r.record_id ASC
        LIMIT ?
        """,
        [TOP_N],
    ).fetchall()
    return Table(
        slug="top_cited",
        caption=f"Top {TOP_N} most-cited records",
        columns=("Record", "Year", "Title", "Cited by"),
        rows=tuple(
            (record_id, int(year), title, int(cited)) for record_id, year, title, cited in rows
        ),
    )


def citation_statistics_table(numbers: dict[str, Any]) -> Table:
    """Citation statistics, rendered from the numbers map.

    Built from ``numbers`` rather than from a second query on purpose: a
    table and a ``numbers.json`` entry that disagreed about the median would
    be exactly the drift this stage exists to prevent, and computing both
    from one source makes that disagreement impossible rather than unlikely.

    Args:
        numbers: The mapping from :func:`~prismabib.report.numbers.numbers_map`.

    Returns:
        A statistic/value table.
    """
    rows: list[tuple[Any, ...]] = [
        ("Records with a citation snapshot", numbers["citations.records_with_a_snapshot"]),
        ("Total citations", numbers["citations.total"]),
        ("Median citations", numbers["citations.median"]),
        ("Maximum citations", numbers["citations.max"]),
    ]
    return Table(
        slug="citation_statistics",
        caption="Citation statistics over the latest snapshot per record",
        columns=("Statistic", "Value"),
        rows=tuple(rows),
    )


def build_tables(project: Project, numbers: dict[str, Any]) -> tuple[Table, ...]:
    """Every table this stage can generate, in a stable order.

    Args:
        project: The project to tabulate.
        numbers: The mapping from :func:`~prismabib.report.numbers.numbers_map`.

    Returns:
        The tables, ordered as a reader would meet them: what was eligible,
        then where the corpus was published, then how it is cited.
    """
    connection = connect(project, read_only=True)
    try:
        return (
            eligibility_criteria_table(project),
            top_venues_table(connection),
            citation_statistics_table(numbers),
            top_cited_table(connection),
        )
    finally:
        connection.close()


__all__ = [
    "Table",
    "build_tables",
    "citation_statistics_table",
    "eligibility_criteria_table",
    "latex_escape",
    "to_csv",
    "to_latex",
    "to_markdown",
    "top_cited_table",
    "top_venues_table",
]
