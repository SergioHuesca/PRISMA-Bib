"""Stage 5 screening: the queue (pure logic) and, later, the Panel view.

BUILD_PLAN §Stage 5 splits this package hard: ``queue.py`` is pure logic and
fully tested, ``ui.py`` is smoke-tested only. Only the queue exists so far,
and it is deliberately importable without ``panel`` -- a notebook that only
wants the ordering rule, and every test of it, must not pay for a UI
dependency.
"""

from prismabib.screening.queue import (
    ORDERING_NAMESPACE,
    ScreeningQueue,
    eligible_record_ids,
    ordered_record_ids,
    screening_queue,
)

__all__ = [
    "ORDERING_NAMESPACE",
    "ScreeningQueue",
    "eligible_record_ids",
    "ordered_record_ids",
    "screening_queue",
]
