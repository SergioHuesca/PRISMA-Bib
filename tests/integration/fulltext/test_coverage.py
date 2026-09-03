"""Integration tests for the Stage 6 coverage report (S06-AC3, ADR 0019).

Real DuckDB (an in-memory store built from the checked-in ``schema.sql``),
no network.

**On "pairwise-distinct throughout" (amended after review).** An earlier
version of this fixture made that claim while giving ScienceDirect a
*Refused* count of 1 and a *Not-found* count of 1 -- an accidentally equal
pair, for the one resolver where every other count also happened to collide
(``openaccess``/``manual`` were both ``(_, 0, 0)``). Swapping the two SQL
``FILTER`` expressions in ``coverage_by_resolver_table`` -- refused counted as
not-found and vice versa -- left every one of those tuples unchanged and
passed this test regardless, which falsifies "would be caught here" for
exactly the distinction ADR 0019 exists to preserve. ScienceDirect now has **2** refusals and **1** not-found -- an
``entitled=False``/``entitled=NULL`` imbalance that survives a swap of the two
``FILTER`` expressions, verified by actually swapping them and confirming
``test_coverage_report__mixed_publishers__counts_by_resolver_and_publisher``
below fails against the swap (and passes once it is reverted).
"""

from __future__ import annotations

from datetime import UTC, datetime

import duckdb
import pytest

from prismabib.fulltext.coverage import coverage_by_publisher_table, coverage_by_resolver_table
from tests.store_helpers import create_schema

_RETRIEVED_AT = datetime(2026, 1, 15, 9, 0, 0, tzinfo=UTC)


def _store() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect(":memory:")
    create_schema(connection)
    return connection


def _insert_record(connection: duckdb.DuckDBPyConnection, record_id: str, doi: str | None) -> None:
    connection.execute(
        "INSERT INTO records (record_id, run_id, doi) VALUES (?, 'run-1', ?)",
        [record_id, doi],
    )


def _insert_asset(
    connection: duckdb.DuckDBPyConnection,
    *,
    record_id: str,
    resolver_name: str,
    entitled: bool | None,
    resolved: bool,
) -> None:
    connection.execute(
        """
        INSERT INTO fulltext_assets
          (record_id, resolver_name, media_type, path, retrieved_at, entitled)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [
            record_id,
            resolver_name,
            "xml" if resolved else None,
            f"/fulltext/{resolver_name}/{record_id}.xml" if resolved else None,
            _RETRIEVED_AT,
            entitled,
        ],
    )


def _build_mixed_publisher_store() -> duckdb.DuckDBPyConnection:
    """Ten records, three resolvers, four publishers.

    - 5 records resolved by ScienceDirect, all Elsevier DOIs (``10.1016``).
    - 2 records resolved by open access: one MDPI (``10.3390``), one with
      no DOI at all (``UNKNOWN_PUBLISHER``).
    - 1 record resolved by manual drop, an IEEE DOI (``10.1109``) that
      ScienceDirect had earlier refused (``entitled=False``) -- the
      resolver-level "refused" count this table exists to surface.
    - 1 record refused by ScienceDirect and never resolved by anything --
      together with the one above, ScienceDirect's *Refused* count is 2,
      deliberately different from its *Not-found* count of 1 below (see the
      module docstring on why the two must differ).
    - 1 record fully unresolved: ScienceDirect not-found (``entitled=NULL``).
    """
    connection = _store()

    elsevier_records = [f"scopus:2-s2.0-8510000{index:04d}" for index in range(5)]
    for index, record_id in enumerate(elsevier_records):
        _insert_record(connection, record_id, f"10.1016/j.example.{index}")
        _insert_asset(
            connection,
            record_id=record_id,
            resolver_name="sciencedirect",
            entitled=True,
            resolved=True,
        )

    mdpi_record = "scopus:2-s2.0-85100001000"
    _insert_record(connection, mdpi_record, "10.3390/s2026010001")
    _insert_asset(
        connection, record_id=mdpi_record, resolver_name="openaccess", entitled=True, resolved=True
    )

    no_doi_record = "scopus:2-s2.0-85100001001"
    _insert_record(connection, no_doi_record, None)
    _insert_asset(
        connection,
        record_id=no_doi_record,
        resolver_name="openaccess",
        entitled=True,
        resolved=True,
    )

    ieee_record = "scopus:2-s2.0-85100001002"
    _insert_record(connection, ieee_record, "10.1109/tpami.2026.100002")
    _insert_asset(
        connection,
        record_id=ieee_record,
        resolver_name="sciencedirect",
        entitled=False,
        resolved=False,
    )
    _insert_asset(
        connection, record_id=ieee_record, resolver_name="manual", entitled=True, resolved=True
    )

    ieee_refused_only_record = "scopus:2-s2.0-85100001004"
    _insert_record(connection, ieee_refused_only_record, "10.1109/taffco.2026.100004")
    _insert_asset(
        connection,
        record_id=ieee_refused_only_record,
        resolver_name="sciencedirect",
        entitled=False,
        resolved=False,
    )

    unresolved_record = "scopus:2-s2.0-85100001003"
    _insert_record(connection, unresolved_record, "10.1007/s99999-026-00001-x")
    _insert_asset(
        connection,
        record_id=unresolved_record,
        resolver_name="sciencedirect",
        entitled=None,
        resolved=False,
    )

    return connection


@pytest.mark.integration
@pytest.mark.acceptance("S06-AC3")
def test_coverage_report__mixed_publishers__counts_by_resolver_and_publisher() -> None:
    connection = _build_mixed_publisher_store()
    try:
        by_resolver = coverage_by_resolver_table(connection)
        by_publisher = coverage_by_publisher_table(connection)
    finally:
        connection.close()

    resolver_rows = {row[0]: row[1:] for row in by_resolver.rows}
    # (resolved, refused, not_found). ScienceDirect's refused (2) and not-found
    # (1) are deliberately different -- see the module docstring: with the
    # earlier fixture's 1-and-1, swapping the two SQL FILTER expressions was
    # undetectable by this assertion.
    assert resolver_rows == {
        "sciencedirect": (5, 2, 1),
        "openaccess": (2, 0, 0),
        "manual": (1, 0, 0),
    }

    # (records, resolved, refused, coverage%) per publisher. The population is
    # every record the chain *attempted*, not every record it resolved -- see
    # `coverage_by_publisher_table`. Written out as literals rather than derived
    # from the fixture builder, which would restate the thing under test.
    publisher_rows = {row[0]: row[1:] for row in by_publisher.rows}
    assert publisher_rows == {
        "Elsevier": (5, 5, 0, 100.0),
        "IEEE": (2, 1, 2, 50.0),
        "MDPI": (1, 1, 0, 100.0),
        "Springer": (1, 0, 0, 0.0),
        "unknown": (1, 1, 0, 100.0),
    }
    # The Springer row is the reason this table lists attempts rather than
    # successes. Nothing was resolved for it, so a table of resolved records
    # would omit the publisher entirely and the reader would never learn it had
    # been tried and missed -- they would see "Elsevier, 100%" and take it for
    # coverage rather than for composition.
    assert publisher_rows["Springer"] == (1, 0, 0, 0.0)
    # Records attempted are accounted for exactly once, by exactly one publisher.
    assert sum(row[0] for row in publisher_rows.values()) == 10
    # ... and the resolved column still reconciles with the by-resolver table.
    assert sum(row[1] for row in publisher_rows.values()) == 8
