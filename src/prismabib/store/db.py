"""The Layer 1 DuckDB connection (BUILD_PLAN §Stage 3 contract, line 892).

A single, deliberately tiny module: everything that decides *what* is in
the store lives in :mod:`prismabib.store.load`; this module only knows
*where* it is (``project.db_path``, BUILD_PLAN §2.3) and how to open it.
"""

from __future__ import annotations

import duckdb

from prismabib.errors import StoreError
from prismabib.project import Project


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
        return duckdb.connect(str(db_path), read_only=read_only)
    except duckdb.Error as exc:
        raise StoreError(f"Could not open the Layer 1 store at {db_path}: {exc}") from exc


__all__ = ["connect"]
