"""Stage 5 screening: the queue (pure logic) and the Panel view.

BUILD_PLAN §Stage 5 splits this package hard: ``queue.py`` is pure logic and
fully tested, ``ui.py`` is asserted on its view model, its pace arithmetic and
its key map, and left uncovered where it is only widget wiring (§3.7.6 gates
it at 60% and says why).

**Only the queue is re-exported here, deliberately.** ``ui.py`` imports
``panel``, which lives in the optional ``viz`` extra; re-exporting
:func:`~prismabib.screening.ui.screener` would make ``import
prismabib.screening`` -- and therefore every test of the ordering rule, and
the PRISMA engine's own imports -- fail without a UI dependency they never
use. Import the view explicitly::

    from prismabib.screening.ui import screener
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
