"""Progress events and cancel checkpoints shared by the build and the export.

A long operation names its *safe points*: places where stopping leaves no partial
output. At each one it reports a :class:`ProgressEvent` and then looks at the cancel
signal, raising :class:`~prismabib.errors.OperationCancelled` if it is set. A caller
on another thread can equally inject its own exception at one of those points (for
example from the progress callback); the build's cleanup handles any exception.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from prismabib.errors import OperationCancelled

if TYPE_CHECKING:
    import threading


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    """One step of a long operation.

    Attributes:
        phase: The safe point's name, one of the operation's declared safe points.
        done: Units finished in this phase (``0`` when the phase starts).
        total: Units in this phase, or ``None`` when not known in advance.
    """

    phase: str
    done: int = 0
    total: int | None = None


ProgressCallback = Callable[[ProgressEvent], None]


def checkpoint(
    cancel: threading.Event | None,
    on_progress: ProgressCallback | None,
    phase: str,
    done: int = 0,
    total: int | None = None,
) -> None:
    """Report progress at a safe point, then stop if cancelled.

    Raises:
        OperationCancelled: If ``cancel`` is set.
    """
    if on_progress is not None:
        on_progress(ProgressEvent(phase, done, total))
    if cancel is not None and cancel.is_set():
        raise OperationCancelled(f"cancelled at {phase}")
