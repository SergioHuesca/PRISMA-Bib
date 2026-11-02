"""Methods-text snippets (ADR 0040 Decision 12)."""

from __future__ import annotations

from prismabib.report.methods import venue_unnamed_sentence


def test_venue_unnamed_sentence__some_unnamed__states_count_and_handling() -> None:
    assert venue_unnamed_sentence(3, 96) == (
        "3 of 96 records have no venue name and are not counted among the venues."
    )


def test_venue_unnamed_sentence__none_unnamed__is_empty() -> None:
    assert venue_unnamed_sentence(0, 96) == ""
