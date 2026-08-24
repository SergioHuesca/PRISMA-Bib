"""Layer 1 -- the normalised DuckDB store (BUILD_PLAN §Stage 3, lines 841-931).

``project.raw_dir`` (Layer 0, immutable) goes in; ``project.db_path``
(``store/corpus.duckdb``, disposable) comes out. See:

- :mod:`prismabib.store.load` -- :func:`~prismabib.store.load.build_store`
  (the one function Layer 1 must be reconstructible by, BUILD_PLAN §2.2)
  and :class:`~prismabib.store.load.Corpus`, the read-facing handle every
  later analysis module is meant to use.
- :mod:`prismabib.store.db` -- :func:`~prismabib.store.db.connect`, the raw
  DuckDB connection underneath both of the above.
- :mod:`prismabib.store.checksums` -- deterministic, engine-independent
  per-table checksums for the golden-snapshot acceptance test (S03-AC1).
- ``schema.sql`` -- the 9 tables of BUILD_PLAN lines 847-879, executed
  verbatim by :func:`~prismabib.store.load.build_store` rather than
  hand-duplicated as Python ``CREATE TABLE`` calls.

``PrismaStage`` -- the ``stage`` parameter of
:meth:`~prismabib.store.load.Corpus.records`/
:meth:`~prismabib.store.load.Corpus.keywords` -- deliberately does *not*
live in this package; see :mod:`prismabib.stage` for why.
"""

from __future__ import annotations

from prismabib.store.checksums import TABLE_NAMES, table_checksum, table_checksums
from prismabib.store.db import connect
from prismabib.store.load import Corpus, StoreStats, build_store

__all__ = [
    "TABLE_NAMES",
    "Corpus",
    "StoreStats",
    "build_store",
    "connect",
    "table_checksum",
    "table_checksums",
]
