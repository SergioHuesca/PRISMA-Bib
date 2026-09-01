"""One export bundle must not contradict itself.

A manuscript quotes `numbers.json` in its prose and ships `tables/` as its
figures. If the two disagree about the same quantity, the paper contains two
different answers to one question -- which is BUILD_PLAN §1.4 arriving through
the side door, without any single number being wrong in isolation.

That is not hypothetical here. `numbers.py` grouped venues by `name` while
`tables.py` grouped by `(name, venue_type)`, so a venue Scopus indexes under
two aggregation types appeared as one venue in the prose and two rows in the
table. And `venues.total` counted venue *rows* -- one per record in this
schema -- so the reference corpus reported "120 venues" for 22.

`citation_statistics_table` was written from the start to read `numbers`
rather than re-query, precisely to make this impossible. These tests extend
that principle to the tables that do query.
"""

from __future__ import annotations

import csv
import io
from typing import TYPE_CHECKING

import pytest

from prismabib.report.numbers import numbers_map
from prismabib.report.tables import build_tables, to_csv
from prismabib.store.db import connect
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project

if TYPE_CHECKING:
    from pathlib import Path

    from prismabib.project import Project

CORPUS = CorpusSpec(
    records=[RecordSpec(number=n, cited_by_count=n) for n in range(1, 16)],
    criteria=CriteriaSpec(abstract_reason_codes=("OFF_TOPIC",)),
)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A freshly loaded project."""
    return build_project(tmp_path, CORPUS, slug="bundle")


def venue_table_rows(project: Project, numbers: dict[str, object]) -> list[list[str]]:
    """The rendered top-venues table, parsed back (helper, not a test)."""
    table = next(t for t in build_tables(project, numbers) if t.slug == "top_venues")
    return list(csv.reader(io.StringIO(to_csv(table))))[1:]


@pytest.mark.integration
def test_bundle__venues_total__counts_venues_not_venue_rows(project: Project) -> None:
    """``venues.total`` is a count of venues, which is what the key says.

    The store writes one ``venues`` row per record, so ``count(*)`` returns the
    record count -- a number that looks entirely plausible beside the others
    and is wrong. Asserted against ``count(DISTINCT name)`` computed here, so
    the test does not simply restate the implementation's own query.
    """
    numbers = numbers_map(project)

    connection = connect(project, read_only=True)
    try:
        distinct = connection.execute("SELECT count(DISTINCT name) FROM venues").fetchone()
        rows = connection.execute("SELECT count(*) FROM venues").fetchone()
    finally:
        connection.close()

    assert numbers["venues.total"] == int(distinct[0])
    assert numbers["venues.total"] != int(rows[0]), (
        "this corpus cannot tell a venue count from a row count; the assertion above "
        "would pass on the defect"
    )


@pytest.mark.integration
def test_bundle__top_venue__agrees_between_numbers_json_and_the_table(project: Project) -> None:
    """The prose and the table must name the same top venue with the same count.

    A reader meets `{{venues.top1.count}}` in a sentence and the same venue in
    Table 2. Two groupings of "a venue" put different numbers in those two
    places while every individual test still passed.
    """
    numbers = numbers_map(project)

    rows = venue_table_rows(project, numbers)

    assert rows, "the top-venues table is empty"
    name, _venue_type, count = rows[0]
    assert name == numbers["venues.top1.name"]
    assert int(count) == numbers["venues.top1.count"]


@pytest.mark.integration
def test_bundle__venue_split_across_types__is_one_venue_in_both_places(
    project: Project,
) -> None:
    """One venue indexed under two aggregation types stays one venue.

    Real Scopus data does this: the same journal under several ``source-id``
    values with differing ``prism:aggregationType``. Retyping half this
    corpus's rows reproduces it. Under the old grouping the table split into
    two rows summing to the prose's single figure -- so the caption said "Top
    5 venues" while covering four, and no row matched the sentence.
    """
    connection = connect(project, read_only=False)
    try:
        top = connection.execute(
            """
            SELECT v.name FROM records r JOIN venues v ON r.venue_id = v.venue_id
            GROUP BY v.name ORDER BY count(*) DESC, v.name ASC LIMIT 1
            """
        ).fetchone()
        assert top is not None
        connection.execute(
            """
            UPDATE venues SET venue_type = 'conference'
            WHERE name = ? AND venue_id IN (
                SELECT venue_id FROM venues WHERE name = ? ORDER BY venue_id LIMIT 1
            )
            """,
            [top[0], top[0]],
        )
    finally:
        connection.close()

    numbers = numbers_map(project)
    rows = venue_table_rows(project, numbers)

    names = [row[0] for row in rows]
    assert len(names) == len(set(names)), f"a venue is split across table rows: {names}"
    assert int(rows[0][2]) == numbers["venues.top1.count"]
