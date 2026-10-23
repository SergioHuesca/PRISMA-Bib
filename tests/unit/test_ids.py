"""``prismabib.ids`` is the home of the id factories; ``prisma.events`` re-exports them."""

from __future__ import annotations

import pytest

from prismabib import ids
from prismabib.prisma import events


@pytest.mark.unit
def test_ids__both_import_paths__give_the_same_objects() -> None:
    assert events.IdFactory is ids.IdFactory
    assert events.MonotonicUlidFactory is ids.MonotonicUlidFactory
