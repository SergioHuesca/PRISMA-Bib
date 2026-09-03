"""Golden test: an Elsevier skew must make itself visible against other publishers (ADR 0019).

BUILD_PLAN's Tests table states the point plainly: "A fixture where 100% of
resolved text is Elsevier produces a report that says so plainly. The point
is that the bias appears in output, not in a footnote." This test renders
the coverage-by-publisher table through every one of Stage 10's three
renderers (:mod:`prismabib.report.tables`) and checks both the Elsevier row
and a second, non-Elsevier row it must be distinguishable from.

**Amended after review.** The original fixture had every record resolved and
every record Elsevier: "Records" and "Resolved" were identical columns for
its one row, so a query or a renderer that silently swapped the two (or
confused "resolved" with "attempted") would still print "Elsevier, 4, 4, ...,
100" and this test would not notice -- the same class of defect
``test_coverage_report__mixed_publishers__counts_by_resolver_and_publisher``
in ``tests/integration/fulltext/test_coverage.py`` was fixed against. This
fixture now also attempts, and refuses, two IEEE records: "100% of resolved
text is Elsevier" stays true (Elsevier is still the only publisher with any
resolved records), but Records and Resolved now differ for *both* rows, and
a reader is shown the IEEE refusal alongside the Elsevier success rather than
being shown Elsevier in isolation. Asserting ``"100" in rendering`` alone was
also too weak -- true of "100" appearing anywhere, including a record id or
an unrelated number -- so the assertions below parse each rendering back into
rows and check exact cell values instead.
"""

from __future__ import annotations

import csv
import io
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
_RESOLVED_RECORDS = [
    ("scopus:2-s2.0-85200000001", "10.1016/j.knosys.2026.100001", "sciencedirect"),
    ("scopus:2-s2.0-85200000002", "10.1016/j.neucom.2026.100002", "sciencedirect"),
    ("scopus:2-s2.0-85200000003", "10.1016/j.ins.2026.100003", "sciencedirect"),
    ("scopus:2-s2.0-85200000004", "10.1016/j.patcog.2026.100004", "openaccess"),
]

#: Two IEEE records ScienceDirect was refused for and nothing else resolved --
#: attempted, not resolved, so the "Records" column for IEEE (2) differs from
#: its "Resolved" column (0), the same distinguishing property the Elsevier
#: row alone could not provide.
_REFUSED_RECORDS = [
    ("scopus:2-s2.0-85200000005", "10.1109/tpami.2026.100005"),
    ("scopus:2-s2.0-85200000006", "10.1109/taffco.2026.100006"),
]


def _mixed_store() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    create_schema(connection)
    for record_id, doi, resolver_name in _RESOLVED_RECORDS:
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
    for record_id, doi in _REFUSED_RECORDS:
        connection.execute(
            "INSERT INTO records (record_id, run_id, doi) VALUES (?, 'run-1', ?)",
            [record_id, doi],
        )
        connection.execute(
            """
            INSERT INTO fulltext_assets
              (record_id, resolver_name, media_type, path, retrieved_at, entitled)
            VALUES (?, 'sciencedirect', NULL, NULL, ?, FALSE)
            """,
            [record_id, _RETRIEVED_AT],
        )
    return connection


@pytest.mark.golden
def test_coverage_report__elsevier_skew__is_visible_against_a_refused_publisher() -> None:
    connection = _mixed_store()
    try:
        table = coverage_by_publisher_table(connection)
    finally:
        connection.close()

    # Publisher, Records, Resolved, Refused, Coverage (%). "Records" is the
    # denominator that makes the percentage mean anything: without it a reader
    # cannot tell 4-of-4 from 4-of-400. Ordered by record count descending, so
    # Elsevier (4 records) precedes IEEE (2).
    assert table.rows == (
        ("Elsevier", 4, 4, 0, 100.0),
        ("IEEE", 2, 0, 2, 0.0),
    )

    csv_rows = list(csv.reader(io.StringIO(to_csv(table))))[1:]
    assert csv_rows == [
        ["Elsevier", "4", "4", "0", "100.00"],
        ["IEEE", "2", "0", "2", "0.00"],
    ]

    markdown_text = to_markdown(table)
    assert "| Elsevier | 4 | 4 | 0 | 100.00 |" in markdown_text
    assert "| IEEE | 2 | 0 | 2 | 0.00 |" in markdown_text

    latex_text = to_latex(table)
    assert "Elsevier & 4 & 4 & 0 & 100.00" in latex_text
    assert "IEEE & 2 & 0 & 2 & 0.00" in latex_text
