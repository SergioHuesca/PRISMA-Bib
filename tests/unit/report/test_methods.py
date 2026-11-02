"""Methods-text snippets (ADR 0040 Decision 12)."""

from __future__ import annotations

from prismabib.report.methods import venue_unnamed_sentence


def test_venue_unnamed_sentence__some_unnamed__states_count_and_handling() -> None:
    assert venue_unnamed_sentence(3, 96) == (
        "3 of 96 records have no venue name and are not counted among the venues."
    )


def test_venue_unnamed_sentence__none_unnamed__is_empty() -> None:
    assert venue_unnamed_sentence(0, 96) == ""


def _cascade_numbers() -> dict[str, int]:
    numbers = {
        f"methods.cascade.{field}.{suffix}": 0
        for field in ("abstract", "authors", "affiliations", "keywords")
        for suffix in ("level1", "level2", "level3", "level4", "manual", "unavailable", "conflicts")
    }
    numbers["methods.cascade_conflicts_open"] = 0
    numbers["methods.cascade_conflicts_resolved"] = 0
    return numbers


def test_cascade_sentence__counts__names_each_level_and_the_conflict_totals() -> None:
    from prismabib.report.methods import cascade_sentence

    numbers = _cascade_numbers()
    numbers.update(
        {
            "methods.cascade.abstract.level1": 90,
            "methods.cascade.abstract.level4": 20,
            "methods.cascade.abstract.manual": 4,
            "methods.cascade.abstract.unavailable": 6,
            "methods.cascade.abstract.conflicts": 3,
            "methods.cascade.affiliations.level2": 7,
            "methods.cascade.keywords.level4": 11,
            "methods.cascade_conflicts_open": 2,
            "methods.cascade_conflicts_resolved": 1,
        }
    )

    text = cascade_sentence(numbers)

    assert (
        "Abstracts were taken from Scopus COMPLETE for 90 records, from OpenAlex for 20 "
        "and entered manually for 4; 6 have no abstract." in text
    )
    assert "3 abstract values differ between Scopus and OpenAlex." in text
    assert "Scopus STANDARD for 7" in text
    assert "OpenAlex topics for 11" in text
    assert text.endswith("2 conflicts remain open and 1 were resolved by a reviewer.")


def test_cascade_sentence__all_zero__is_still_a_sentence() -> None:
    from prismabib.report.methods import cascade_sentence

    assert cascade_sentence(_cascade_numbers()).endswith(
        "0 conflicts remain open and 0 were resolved by a reviewer."
    )
