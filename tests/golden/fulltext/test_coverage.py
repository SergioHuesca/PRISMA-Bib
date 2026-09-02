"""Golden test: an Elsevier-only corpus must make its own skew visible (ADR 0019).

BUILD_PLAN's Tests table states the point plainly: "A fixture where 100% of
resolved text is Elsevier produces a report that says so plainly. The point
is that the bias appears in output, not in a footnote." This test renders
the coverage-by-publisher table through every one of Stage 10's three
renderers (:mod:`prismabib.report.tables`) and asserts the number "100"
appears in each rendering next to "Elsevier" -- not merely that the
underlying :class:`~prismabib.report.tables.Table` object holds the right
row, which a broken renderer could still contradict.
"""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb
import pytest

from prismabib.fulltext.coverage import coverage_by_publisher_table
from prismabib.report.tables import to_csv, to_latex, to_markdown
from tests.store_helpers import create_schema

_RETRIEVED_AT = datetime(2026, 1, 15, 9, 0, 0, tzinfo=UTC)

#: Pairwise-distinct DOI suffixes across four Elsevier records -- a
#: degenerate one-record fixture could pass this test by accident (a table
#: that always prints "100.0%" regardless of its input would still show
#: "100" for a single row); four distinct records resolved through two
#: different resolvers is what makes the 100%-Elsevier *aggregate* meaningful.
_RECORDS = [
    ("scopus:2-s2.0-85200000001", "10.1016/j.knosys.2026.100001", "sciencedirect"),
    ("scopus:2-s2.0-85200000002", "10.1016/j.neucom.2026.100002", "sciencedirect"),
    ("scopus:2-s2.0-85200000003", "10.1016/j.ins.2026.100003", "sciencedirect"),
    ("scopus:2-s2.0-85200000004", "10.1016/j.patcog.2026.100004", "openaccess"),
]


def _elsevier_only_store() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    create_schema(connection)
    for record_id, doi, resolver_name in _RECORDS:
        connection.execute(
            "INSERT INTO records (record_id, run_id, doi) VALUES (?, 'run-1', ?)",
            [record_id, doi],
        )
        connection.execute(
            """
            INSERT INTO fulltext_assets
              (record_id, resolver_name, media_type, path, retrieved_at, entitled)
            VALUES (?, ?, 'xml', ?, ?, TRUE)
            """,
            [record_id, resolver_name, f"/fulltext/{resolver_name}/{record_id}.xml", _RETRIEVED_AT],
        )
    return connection


@pytest.mark.golden
def test_coverage_report__elsevier_only_corpus__coverage_skew_is_visible() -> None:
    connection = _elsevier_only_store()
    try:
        table = coverage_by_publisher_table(connection)
    finally:
        connection.close()

    # Publisher, Records, Resolved, Refused, Coverage (%). "Records" is the
    # denominator that makes the percentage mean anything: without it a reader
    # cannot tell 4-of-4 from 4-of-400.
    assert table.rows == (("Elsevier", 4, 4, 0, 100.0),)

    csv_text = to_csv(table)
    markdown_text = to_markdown(table)
    latex_text = to_latex(table)

    for rendering in (csv_text, markdown_text, latex_text):
        assert "Elsevier" in rendering
        assert "100" in rendering
