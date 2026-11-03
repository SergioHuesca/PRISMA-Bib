"""The store stamp: ``store/build_stamp.json`` (ADR 0042 Decision 1).

A sidecar beside the DuckDB file that records the source mode and :data:`STORE_SCHEMA` the store was
built under. :func:`~prismabib.store.db.connect` and ``build_store`` refuse a store whose stamp
disagrees with the project, because a store read under another mode gives wrong tables without an
error.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Final

from prismabib.capture.layout import atomic_write_bytes
from prismabib.errors import StoreError
from prismabib.source_mode import LEGACY_MODE, checked_mode

if TYPE_CHECKING:
    from prismabib.project import Project

#: Bumped when the store's tables change shape. 2: ``openalex_records`` and
#: ``openalex_search_runs`` (ADR 0042 Decision 5, PR 4a).
STORE_SCHEMA: Final = 2
STAMP_FILENAME: Final = "build_stamp.json"


def stamp_path(db_path: Path) -> Path:
    """The stamp beside the store at ``db_path``."""
    return db_path.with_name(STAMP_FILENAME)


def stamp_bytes(source_mode: str) -> bytes:
    """The stamp's content: stable keys, stable order."""
    payload = {"schema": STORE_SCHEMA, "source_mode": source_mode}
    return (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")


def write_stamp(db_path: Path, source_mode: str) -> None:
    """Write the stamp for the store at ``db_path``, atomically."""
    atomic_write_bytes(stamp_path(db_path), stamp_bytes(source_mode))


def _read_stamp(db_path: Path) -> tuple[str, int] | None:
    path = stamp_path(db_path)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return str(data["source_mode"]), int(data["schema"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise StoreError(
            f"{path} is not a readable store stamp ({exc}). "
            "Run `prismabib build <slug> --rebuild` to write a new one."
        ) from exc


def check_stamp(project: Project) -> None:
    """Refuse a store built under another source mode or schema.

    A store with no stamp was built before the stamp existed, or was interrupted between the
    replace and the stamp write (ADR 0042 Decision 1). From PR 4a it is refused in every mode,
    because it also lacks the OpenAlex tables: it is never read as zero.

    Raises:
        StoreError: Naming both modes (or schemas) and saying to run ``build --rebuild``.
        ConfigError: If the project's mode is invalid or inconsistent with its event log.
    """
    mode = checked_mode(project)
    stamp = _read_stamp(project.db_path)
    stored_mode, stored_schema = stamp if stamp is not None else (LEGACY_MODE, 0)
    if stamp is not None and stored_mode == mode and stored_schema == STORE_SCHEMA:
        return
    built = "has no stamp (built before source modes existed)" if stamp is None else "was built"
    raise StoreError(
        f"The Layer 1 store at {project.db_path} {built} under sources mode {stored_mode!r}, "
        f"schema {stored_schema}; this project is in mode {mode!r}, schema {STORE_SCHEMA}. "
        "Run `prismabib build <slug> --rebuild` (nothing is re-fetched; the store is rebuilt "
        "from Layer 0)."
    )
