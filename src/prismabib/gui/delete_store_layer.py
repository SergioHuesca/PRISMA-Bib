"""Delete the built store of a Project (GUI spec #312, ticket #337).

STORE ONLY, by owner decision: the app removes the built store (the database file, its write-ahead
log and its stamp) and nothing else. The Project stays listed and openable, and the build step
rebuilds the store from Layer 0. No function here touches ``raw/``, the Decision log, ``project.toml``
or ``criteria.yaml``, and no other app operation deletes them either.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Final

from prismabib.errors import ValidationError
from prismabib.gui import tasks
from prismabib.gui.layer import Session, write_operation
from prismabib.project import Project
from prismabib.store.stamp import stamp_path


@dataclass(frozen=True)
class DeleteNotice:
    """What the confirmation says: what goes, what stays, and that the app never deletes it."""

    goes: str
    stays: tuple[str, ...]
    never_deleted: str


NOTICE: Final = DeleteNotice(
    goes="The built store: the database the app built from your captured searches.",
    stays=(
        "Your captured searches (the raw data) and every Sealed run",
        "The Decision log",
        "The Project's settings file and its Criteria file",
    ),
    never_deleted="The app never deletes any of these, whatever you choose here.",
)


def _store_files(project: Project) -> list[Path]:
    db = project.db_path
    return [db, db.with_name(db.name + ".wal"), stamp_path(db)]


@write_operation
def delete_store(session: Session, *, confirmed: bool) -> None:
    """Delete the built store after a confirmation; the Project stays and can be rebuilt.

    Raises:
        ProjectLockedError: If the session is read-only (another window holds the Project).
        ValidationError: Not confirmed, no store, a task running, or the store is in use.
    """
    project = session.require_writable()
    if not confirmed:
        raise ValidationError("Confirm that you want to delete the built store.")
    if not project.db_path.is_file():
        raise ValidationError("There is no built store to delete.")
    if tasks.runner_for(project).status().running:
        raise ValidationError("A task is running. Wait for it or cancel it before deleting.")
    try:
        for path in _store_files(project):
            path.unlink(missing_ok=True)
    except OSError as exc:
        raise ValidationError(
            "The store could not be deleted, probably because another program has it open. "
            "Close it and try again."
        ) from exc
