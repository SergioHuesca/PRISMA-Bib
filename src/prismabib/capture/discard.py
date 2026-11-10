"""Explicit discard of an unsealed capture run (run status ``discarded``).

Discarding marks a run so it is never resumed and never sealed. It deletes nothing: every page
already in Layer 0 stays on disk as an audit trail, and a discarded OpenAlex run still blocks a
change of Source mode (see :func:`prismabib.source_mode.check_transition`). A Sealed run cannot be
discarded.
"""

from __future__ import annotations

from pathlib import Path

from prismabib.capture.layout import NON_RUN_DIRNAMES, OPENALEX_SEARCH_DIRNAME, guard_writable
from prismabib.capture.openalex_search import (  # pyright: ignore[reportPrivateUsage]
    _discard,
    _read_progress,
)
from prismabib.capture.writer import (  # pyright: ignore[reportPrivateUsage]
    _CURSOR_FILENAME,
    _load_cursor_state,
    _save_cursor_state,
)
from prismabib.errors import ValidationError
from prismabib.project import Project

#: The reason recorded on an OpenAlex run discarded by the caller.
DISCARD_REASON = "discarded by the user"


def discard_run(project: Project, run_id: str) -> None:
    """Mark the unsealed Scopus or OpenAlex search run ``run_id`` as discarded.

    Idempotent. Never deletes or rewrites a page file.

    Args:
        project: The project owning the run.
        run_id: The run directory name under ``raw/`` or ``raw/openalex-search/``.

    Raises:
        ValidationError: If no such unsealed capture run exists.
        SealedRunError: If the run is Sealed; a Sealed run is never altered.
    """
    if Path(run_id).name != run_id or run_id in NON_RUN_DIRNAMES or run_id in {"", ".", ".."}:
        raise ValidationError(f"{run_id!r} is not a run id.")
    openalex_dir = project.raw_dir / OPENALEX_SEARCH_DIRNAME / run_id
    scopus_dir = project.raw_dir / run_id
    if (progress := _read_progress(openalex_dir)) is not None:
        guard_writable(openalex_dir)
        if not progress.discarded:
            _discard(openalex_dir, progress, DISCARD_REASON)
        return
    if (scopus_dir / _CURSOR_FILENAME).is_file():
        state = _load_cursor_state(scopus_dir / _CURSOR_FILENAME)
        state.discarded = True
        _save_cursor_state(scopus_dir, state)  # refuses a Sealed run
        return
    for run_dir in (scopus_dir, openalex_dir):  # a Sealed run (no sidecar left) says so
        guard_writable(run_dir)
    raise ValidationError(f"no unsealed capture run {run_id!r} in {project.raw_dir}.")


__all__ = ["DISCARD_REASON", "discard_run"]
