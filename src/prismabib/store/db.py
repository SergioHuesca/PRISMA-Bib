"""The Layer 1 DuckDB connection (BUILD_PLAN §Stage 3 contract, line 892).

A single, deliberately tiny module: everything that decides *what* is in
the store lives in :mod:`prismabib.store.load`; this module only knows
*where* it is (``project.db_path``, BUILD_PLAN §2.3) and how to open it.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from prismabib.errors import StoreError
from prismabib.project import Project
from prismabib.store.checksums import TABLE_NAMES


def connect(project: Project, read_only: bool = True) -> duckdb.DuckDBPyConnection:
    """Open a connection to ``project``'s Layer 1 DuckDB store.

    Args:
        project: The project whose ``store/corpus.duckdb`` (``project.db_path``)
            is opened.
        read_only: When ``True`` (the default), open the store read-only --
            the mode every analysis module (BUILD_PLAN line 894, "used by
            every analysis module") should use, since nothing outside
            :func:`prismabib.store.load.build_store` is meant to write here.
            When ``False``, open for read/write; :func:`prismabib.store.load.build_store`
            is the only intended caller of that mode.

    Returns:
        An open DuckDB connection onto ``project.db_path``.

    Raises:
        StoreError: If ``read_only`` is ``True`` and ``project.db_path``
            does not exist yet (DuckDB itself raises an IO error opening a
            missing file read-only; this wraps it with a message pointing at
            :func:`prismabib.store.load.build_store` rather than a bare
            DuckDB I/O exception, since a missing store is a prismabib
            usage error, not a filesystem anomaly worth debugging). Also
            raised if ``project.db_path``'s parent directory cannot be
            created for a read/write connection, or if DuckDB refuses the
            file for any other reason (e.g. it exists but is not a DuckDB
            database).
    """
    db_path = project.db_path
    if read_only and not db_path.is_file():
        raise StoreError(
            f"No Layer 1 store at {db_path}. Run "
            "prismabib.store.load.build_store(project) first -- Layer 1 is "
            "always derived from Layer 0, never hand-created (BUILD_PLAN §2.2)."
        )
    if not read_only:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        connection = duckdb.connect(str(db_path), read_only=read_only)
    except duckdb.Error as exc:
        raise StoreError(f"Could not open the Layer 1 store at {db_path}: {exc}") from exc
    if read_only:
        _refuse_stale_schema(connection, db_path)
    return connection


def _refuse_stale_schema(connection: duckdb.DuckDBPyConnection, db_path: Path) -> None:
    """Refuse a store built before a table this build expects existed.

    Args:
        connection: The freshly opened read-only connection.
        db_path: The store's path, named in the error.

    Raises:
        StoreError: If *some but not all* tables in
            :data:`~prismabib.store.checksums.TABLE_NAMES` are absent. A
            database missing every one of them was never a Layer 1 store at
            all, which the downstream "does not look like a Layer 1 store"
            check reports far more usefully than a list of fifteen names.

    A store is a build artefact, not a migrated database: ``schema.sql`` is
    applied once at creation and ADR 0012, ADR 0013 and ADR 0018 have each
    added a table since. A store predating one of them is missing it, and the
    first query against that table raises a raw DuckDB ``CatalogException``
    with a "Did you mean ...?" suggestion -- an internal error surfaced to a
    researcher who did nothing wrong and has no way to read it as "rebuild
    your store".

    Checked only on read-only connections. ``build_store`` opens read/write
    precisely to create or replace the schema, so guarding there would refuse
    the one operation that fixes this.
    """
    present = {
        str(row[0])
        for row in connection.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    missing = sorted(set(TABLE_NAMES) - present)
    if not missing:
        return
    if len(missing) == len(TABLE_NAMES):
        # Not a stale prismabib store -- a database that was never one. The
        # existing "does not look like a Layer 1 store" check downstream says
        # that far better than "missing 15 tables" would, so defer to it.
        return
    connection.close()
    # pragma: no mutate start  -- diagnostic prose; see [tool.mutmut] in pyproject.toml
    raise StoreError(
        f"The Layer 1 store at {db_path} was built by an older version of prismabib "
        f"and is missing {len(missing)} table(s) this version expects: "
        f"{', '.join(missing)}.\n\n"
        "A store is a build artefact, not a migrated database -- it is rebuilt from "
        "Layer 0, which is untouched and still holds everything. Run:\n\n"
        "    prismabib build <slug> --rebuild\n\n"
        "Nothing is re-fetched and no API quota is spent: rebuilding reads only the "
        "sealed runs already on disk."
    )
    # pragma: no mutate end


__all__ = ["connect"]
