"""Deterministic, engine-independent per-table checksums (BUILD_PLAN S03-AC1).

BUILD_PLAN's Stage 3 acceptance criterion is "a byte-stable set of table
checksums" (line 901), and its Tests table names the mechanism explicitly:
"row counts and per-table checksums (sorted by PK) against a committed
snapshot" (line 910). §5 risk 11 warns against exactly the failure mode a
naive implementation invites: a checksum that legitimately churns across
DuckDB versions, or that depends on physical row order rather than logical
content, trains contributors to "fix" a red golden test by regenerating it
rather than by asking why the number moved.

This module is therefore deliberately *not* a hash of DuckDB's on-disk
bytes, an ``EXPORT DATABASE`` artefact, or anything else whose stability
depends on the storage engine. It hashes the **logical content** of each
table: every row, sorted by that table's natural key (or, for the two
columnless-key junction tables, its full column tuple) so insertion order
never matters, with every cell rendered through one fixed, explicit
text-canonicalisation rule so a ``TIMESTAMP`` or ``BOOLEAN`` cell hashes the
same way regardless of DuckDB's own ``str()``/driver formatting in whatever
version happens to be installed.

Both :func:`table_checksum` and :func:`table_checksums` are public and
imported by ``tests/`` (the golden-snapshot test owns this module rather
than reimplementing it) precisely so there is exactly one definition of
"the checksum" for anyone comparing a computed value against the committed
golden snapshot.
"""

from __future__ import annotations

import hashlib
from datetime import date, datetime
from typing import Final

import duckdb

#: Every table's row-identity columns, in the order rows are sorted before
#: hashing. Tables with a declared ``PRIMARY KEY`` in ``schema.sql`` sort by
#: it; the four junction tables that declare no primary key there
#: (``record_authors``, ``record_affiliations``, ``record_keywords``,
#: ``subject_areas``) sort by their full column tuple instead, which is
#: sufficient because :mod:`prismabib.store.load` never inserts a literal
#: duplicate row into any of them. ``malformed_entries`` (ADR 0012) is
#: checksummed like every other table: a rebuild that skips a different set of
#: entries than the committed snapshot records is a change to what the corpus
#: contains, and must show up as a moved checksum rather than only in a log.
#: ``abstract_runs``/``record_subject_area_coverage`` (ADR 0018) follow the
#: same rule as every table above them: sort by the declared primary key.
#: ``fulltext_assets``/``fulltext_sections`` (ADR 0019) do too.
_TABLE_SORT_KEYS: Final[dict[str, tuple[str, ...]]] = {
    "runs": ("run_id",),
    "records": ("record_id",),
    "venues": ("venue_id",),
    "authors": ("author_id",),
    "record_authors": ("record_id", "author_id", "position"),
    "affiliations": ("afid",),
    "record_affiliations": ("record_id", "afid"),
    "keywords": ("keyword_id",),
    "record_keywords": ("record_id", "keyword_id", "kind"),
    "subject_areas": ("record_id", "area_code"),
    "citation_snapshots": ("record_id", "retrieved_at"),
    "malformed_entries": ("payload_file", "payload_line"),
    "run_duplicates": ("run_id",),
    "abstract_runs": ("run_id",),
    "record_subject_area_coverage": ("record_id", "run_id"),
    "fulltext_assets": ("record_id", "resolver_name"),
    "fulltext_sections": ("record_id", "position"),
}

#: The full set of Layer 1 table names, in the order they appear in
#: ``schema.sql``. Exposed so callers do not need to hard-code the list a
#: second time.
TABLE_NAMES: Final[tuple[str, ...]] = tuple(_TABLE_SORT_KEYS)

_FIELD_SEP = "\x1f"  # ASCII unit separator: never appears in prismabib data.
_ROW_SEP = "\x1e"  # ASCII record separator.
_NULL_SENTINEL = "\x00NULL\x00"


def _canonicalise_cell(value: object) -> str:
    """Render one DuckDB cell to a fixed, engine-version-independent text form.

    Args:
        value: One column value as returned by ``duckdb``'s Python API
            (``None``, ``bool``, ``int``, ``float``, ``str``, ``date``, or
            ``datetime`` for every column type this schema uses).

    Returns:
        A string that depends only on the logical value, never on DuckDB's
        own formatting: ``None`` becomes a sentinel distinct from any real
        string (so ``NULL`` and the literal empty string never collide),
        booleans become ``"1"``/``"0"``, dates and timestamps use
        ``isoformat()`` (fixed width, no locale), and everything else uses
        ``str()``.
    """
    if value is None:
        return _NULL_SENTINEL
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, datetime | date):
        return value.isoformat()
    return str(value)


def table_checksum(connection: duckdb.DuckDBPyConnection, table: str) -> str:
    """Compute one table's deterministic, PK-sorted content checksum.

    Args:
        connection: An open connection onto a Layer 1 store.
        table: One of :data:`TABLE_NAMES`.

    Returns:
        The hex-encoded SHA-256 digest of every row in ``table``, sorted by
        its natural key (see :data:`_TABLE_SORT_KEYS`), with each row's
        cells canonicalised by :func:`_canonicalise_cell` and joined by a
        fixed field/row separator. Two connections onto content-identical
        tables always produce the same digest, regardless of physical
        insertion order or DuckDB version. An empty table hashes to the
        SHA-256 of the empty byte string.

    Raises:
        KeyError: If ``table`` is not one of :data:`TABLE_NAMES`.
        duckdb.Error: If ``table`` does not exist in ``connection``'s
            catalogue.
    """
    sort_key = _TABLE_SORT_KEYS[table]
    order_by = ", ".join(f'"{column}"' for column in sort_key)
    rows = connection.execute(f'SELECT * FROM "{table}" ORDER BY {order_by}').fetchall()

    digest = hashlib.sha256()
    for row in rows:
        line = _FIELD_SEP.join(_canonicalise_cell(cell) for cell in row) + _ROW_SEP
        digest.update(line.encode("utf-8"))
    return digest.hexdigest()


def table_checksums(
    connection: duckdb.DuckDBPyConnection, tables: tuple[str, ...] = TABLE_NAMES
) -> dict[str, str]:
    """Compute :func:`table_checksum` for every one of ``tables``.

    Args:
        connection: An open connection onto a Layer 1 store.
        tables: Which tables to checksum. Defaults to every Layer 1 table,
            :data:`TABLE_NAMES`.

    Returns:
        A mapping from table name to its checksum, in ``tables`` order.
    """
    return {table: table_checksum(connection, table) for table in tables}


__all__ = ["TABLE_NAMES", "table_checksum", "table_checksums"]
