"""The Stage 6 full-text coverage report (S06-AC3, ADR 0019).

BUILD_PLAN: "The Stage 9 report must include a full-text coverage table by
resolver and by publisher, so the bias is visible in the output rather than
hidden in the method." This module is that table -- two of them, in fact,
because "by resolver" and "by publisher" answer different questions
(:func:`coverage_by_resolver_table`, :func:`coverage_by_publisher_table`),
composed by :func:`coverage_tables` for a caller that wants both.

**Why the resolver table also reports refusals.** ADR 0019's whole point is
that "we were refused 41 IEEE papers" and "41 IEEE papers were unavailable"
are different facts, and only one of them is a bias this project introduced.
:func:`coverage_by_resolver_table` therefore breaks each resolver's attempts
into *resolved* (an asset was obtained), *refused* (``entitled = false`` --
an entitlement gap), and *not found* (``entitled IS NULL`` and no asset --
not an entitlement question at all) rather than collapsing the latter two
into one "failed" count that would erase exactly the distinction ADR 0019
exists to preserve.

**Why publisher comes from the DOI, not from ``resolver_name``.** See
:mod:`prismabib.publishers`'s module docstring: deriving publisher from the
resolver that succeeded is circular and makes every resolved paper Elsevier
by construction, which can never show a gap.

Both tables reuse :class:`prismabib.report.tables.Table` so they render as
CSV, Markdown and LaTeX exactly like every other Stage 10 table -- one
definition of "a table", never a bespoke formatter for this stage.
"""

from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING

from prismabib.publishers import publisher_from_doi
from prismabib.report.tables import Table

if TYPE_CHECKING:
    import duckdb


def _resolved_dois(connection: duckdb.DuckDBPyConnection) -> list[str | None]:
    """The DOI (possibly ``None``) of every record with at least one resolved asset.

    Args:
        connection: An open Layer 1 connection.

    Returns:
        One entry per **record** that has at least one ``fulltext_assets``
        row with a non-``NULL`` ``media_type`` (i.e. an asset was actually
        obtained, by whichever resolver reached it first) -- a record
        resolved by two attempts within one run cannot happen (BUILD_PLAN's
        "first hit wins"), but this still counts by distinct record to be
        explicit about it.
    """
    rows = connection.execute(
        """
        SELECT DISTINCT fa.record_id, r.doi
        FROM fulltext_assets fa
        LEFT JOIN records r ON r.record_id = fa.record_id
        WHERE fa.media_type IS NOT NULL
        """
    ).fetchall()
    return [doi for _, doi in rows]


def coverage_by_resolver_table(connection: duckdb.DuckDBPyConnection) -> Table:
    """Full-text attempts broken down by resolver and outcome.

    Args:
        connection: An open, read-only Layer 1 connection.

    Returns:
        One row per resolver name present in ``fulltext_assets``, ordered by
        records resolved (descending) then resolver name -- columns
        "Resolver", "Resolved", "Refused (entitlement gap)", "Not found".
        A resolver that was never attempted (no row in ``fulltext_assets``
        at all -- e.g. no project has been run through it yet) does not
        appear; an empty coverage table is honest about "no attempts were
        made", not padded with zero rows for resolvers that never ran.
    """
    rows = connection.execute(
        """
        SELECT
          resolver_name,
          count(*) FILTER (WHERE media_type IS NOT NULL) AS resolved,
          count(*) FILTER (WHERE entitled = FALSE) AS refused,
          count(*) FILTER (WHERE media_type IS NULL AND entitled IS NULL) AS not_found
        FROM fulltext_assets
        GROUP BY resolver_name
        ORDER BY resolved DESC, resolver_name ASC
        """
    ).fetchall()
    return Table(
        slug="fulltext_coverage_by_resolver",
        caption="Full-text resolution attempts, by resolver",
        columns=("Resolver", "Resolved", "Refused (entitlement gap)", "Not found"),
        rows=tuple(
            (resolver_name, int(resolved), int(refused), int(not_found))
            for resolver_name, resolved, refused, not_found in rows
        ),
    )


def coverage_by_publisher_table(connection: duckdb.DuckDBPyConnection) -> Table:
    """Resolved full text broken down by publisher, derived from the DOI prefix.

    This is the table that makes a resolver's coverage bias visible in the
    output: because :mod:`prismabib.publishers` derives publisher from the
    DOI rather than from which resolver succeeded, a corpus resolved
    entirely through :data:`~prismabib.fulltext.resolve.SCIENCEDIRECT` shows
    up here as one publisher at 100% of resolved records, in a numeric
    column a reader sees directly -- not a caveat in a methods paragraph.

    Args:
        connection: An open, read-only Layer 1 connection.

    Returns:
        One row per publisher **the chain attempted**, ordered by record count
        (descending) then publisher name -- columns "Publisher", "Records",
        "Resolved", "Refused", "Coverage (%)". A record with no DOI is counted
        under :data:`prismabib.publishers.UNKNOWN_PUBLISHER` ("unknown"),
        never dropped. Empty (``()`` rows) before anything has been attempted.

    The population is every record with at least one attempt, not every record
    that was *resolved*. A publisher we were refused across the board -- 41
    IEEE papers, all 403 -- has no resolved records, and a table listing only
    publishers with a resolved record would omit it entirely. The reader would
    see "Elsevier, 100%" and have no way to tell whether IEEE was zero of
    three or zero of three hundred, which is the difference between hinting at
    a coverage bias and measuring one. "Records" is the denominator that makes
    "Coverage (%)" mean something, and "Refused" separates an entitlement gap
    from a paper that does not exist.
    """
    attempted = connection.execute(
        """
        SELECT r.doi,
               MAX(CASE WHEN a.path IS NOT NULL THEN 1 ELSE 0 END) AS resolved,
               MAX(CASE WHEN a.entitled IS FALSE THEN 1 ELSE 0 END) AS refused
        FROM fulltext_assets a
        JOIN records r ON r.record_id = a.record_id
        GROUP BY r.record_id, r.doi
        """
    ).fetchall()

    attempted_by_publisher: Counter[str] = Counter()
    resolved_by_publisher: Counter[str] = Counter()
    refused_by_publisher: Counter[str] = Counter()
    for doi, resolved, refused in attempted:
        publisher, _matched = publisher_from_doi(doi)
        attempted_by_publisher[publisher] += 1
        resolved_by_publisher[publisher] += int(resolved)
        refused_by_publisher[publisher] += int(refused)

    ordered = sorted(
        attempted_by_publisher,
        key=lambda publisher: (-attempted_by_publisher[publisher], publisher),
    )
    rows = tuple(
        (
            publisher,
            attempted_by_publisher[publisher],
            resolved_by_publisher[publisher],
            refused_by_publisher[publisher],
            round(resolved_by_publisher[publisher] / attempted_by_publisher[publisher] * 100.0, 2),
        )
        for publisher in ordered
    )
    return Table(
        slug="fulltext_coverage_by_publisher",
        caption="Full-text coverage by publisher (publisher from the DOI registrant prefix)",
        columns=("Publisher", "Records", "Resolved", "Refused", "Coverage (%)"),
        rows=rows,
    )


def coverage_tables(connection: duckdb.DuckDBPyConnection) -> tuple[Table, Table]:
    """Both Stage 6 coverage tables, in the order a reader would want them.

    Args:
        connection: An open, read-only Layer 1 connection.

    Returns:
        ``(coverage_by_resolver_table(connection), coverage_by_publisher_table(connection))``.
    """
    return coverage_by_resolver_table(connection), coverage_by_publisher_table(connection)


__all__ = ["coverage_by_publisher_table", "coverage_by_resolver_table", "coverage_tables"]
