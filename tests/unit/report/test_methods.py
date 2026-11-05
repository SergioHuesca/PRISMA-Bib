"""Methods-text snippets (ADR 0040 Decision 12)."""

from __future__ import annotations

import duckdb
import pytest

from prismabib.report.methods import source_sentence, venue_unnamed_sentence


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


def _store_with_runs(*statements: str) -> duckdb.DuckDBPyConnection:
    """A hand-built in-memory store holding only the two run tables the sentences read."""
    connection = duckdb.connect(":memory:")
    connection.execute(
        "CREATE TABLE runs (run_id TEXT, started_at TIMESTAMP, query TEXT, view TEXT, "
        "total_results INTEGER, payload_sha256 TEXT, criteria_version TEXT)"
    )
    connection.execute(
        "CREATE TABLE openalex_search_runs (run_id TEXT, query_key TEXT, query TEXT, "
        "total_count INTEGER, works_captured INTEGER)"
    )
    for statement in statements:
        connection.execute(statement)
    return connection


_OA_TAIL = (
    "Metadata, including abstracts rebuilt from OpenAlex's inverted index and subject areas "
    "taken from OpenAlex's primary topic, is OpenAlex's own (see Limitations)."
)


@pytest.mark.unit
def test_methods__openalex_mode__sentence_names_queries_and_run_dates() -> None:
    """ADR 0042 Verification 67: the openalex sentence quotes the stored queries and run dates."""
    connection = _store_with_runs(
        "INSERT INTO openalex_search_runs VALUES "
        "('20261103T100000Z-aaaaaaaa', 'ka', 'carbon capture', 3, 3), "
        "('20261104T100000Z-bbbbbbbb', 'kb', 'direct air capture', 3, 3), "
        "('20261105T100000Z-cccccccc', 'ka', 'carbon capture', 4, 4)"
    )

    sentence = source_sentence(connection, "openalex", identified=6, duplicates=2, registered=3)

    assert sentence == (
        "Records were identified by searching OpenAlex; no Scopus search was run. "
        "The searches were `carbon capture`; `direct air capture`, "
        "run on 2026-11-03, 2026-11-04. "
        "OpenAlex reported 6 works across 2 distinct queries, of which 2 duplicate works "
        "(works whose DOI or id was seen more than once) were counted once. "
        "3 records were registered by a reviewer from manual Google Scholar searches "
        f"(ADR 0029). {_OA_TAIL}"
    )


@pytest.mark.unit
def test_methods__openalex_mode__singular_counts_and_no_registered_clause() -> None:
    connection = _store_with_runs(
        "INSERT INTO openalex_search_runs VALUES ('20261103T100000Z-aaaaaaaa', 'ka', 'q', 1, 1)"
    )

    sentence = source_sentence(connection, "openalex", identified=1, duplicates=1, registered=0)

    assert sentence == (
        "Records were identified by searching OpenAlex; no Scopus search was run. "
        "The search was `q`, run on 2026-11-03. "
        "OpenAlex reported 1 work across 1 distinct query, of which 1 duplicate work "
        f"(a work whose DOI or id was seen more than once) was counted once. {_OA_TAIL}"
    )
    assert "registered" not in sentence


@pytest.mark.unit
def test_methods__openalex_mode__no_duplicates__no_duplicates_clause() -> None:
    connection = _store_with_runs(
        "INSERT INTO openalex_search_runs VALUES ('20261103T100000Z-aaaaaaaa', 'ka', 'q', 2, 2)"
    )

    sentence = source_sentence(connection, "openalex", identified=2, duplicates=0, registered=0)

    assert "OpenAlex reported 2 works across 1 distinct query. Metadata" in sentence
    assert "duplicate" not in sentence


@pytest.mark.unit
def test_methods__query_with_backticks_and_newlines__is_fenced_and_collapsed() -> None:
    connection = _store_with_runs(
        "INSERT INTO openalex_search_runs VALUES "
        "('20261103T100000Z-aaaaaaaa', 'ka', 'a ``b``' || chr(10) || '  c', 1, 1)"
    )

    sentence = source_sentence(connection, "openalex", identified=1, duplicates=0, registered=0)

    assert "The search was ```a ``b`` c```, run on" in sentence


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["openalex", "scopus"])
def test_methods__no_sealed_runs__sentence_is_empty(mode: str) -> None:
    connection = _store_with_runs()

    assert source_sentence(connection, mode, identified=0, duplicates=0, registered=0) == ""  # type: ignore[arg-type]


@pytest.mark.unit
def test_methods__scopus_mode__sentence_from_hand_built_store() -> None:
    """ADR 0042 Verification 68: names the Scopus search and nothing about OpenAlex."""
    connection = _store_with_runs(
        "INSERT INTO runs VALUES ('20261103T100000Z-aaaaaaaa', '2026-11-03 10:00:00', "
        "'TITLE-ABS-KEY(\"x\")', 'COMPLETE', 40, 'h', '1.0.0')"
    )

    sentence = source_sentence(connection, "scopus", identified=40, duplicates=0, registered=0)

    assert sentence == (
        "Records were identified by searching Scopus. The search was "
        '`TITLE-ABS-KEY("x")`, run on 2026-11-03. Scopus reported 40 records across 1 '
        "distinct query."
    )
    assert "OpenAlex" not in sentence


@pytest.mark.unit
def test_methods__scopus_mode__two_runs_of_one_query__earliest_run_date_wins() -> None:
    connection = _store_with_runs(
        "INSERT INTO runs VALUES "
        "('20261103T100000Z-aaaaaaaa', '2026-11-03 10:00:00', 'q', 'COMPLETE', 40, 'h', '1'), "
        "('20261105T100000Z-bbbbbbbb', '2026-11-05 10:00:00', 'q', 'COMPLETE', 41, 'h', '1')"
    )

    sentence = source_sentence(connection, "scopus", identified=40, duplicates=0, registered=0)

    assert "run on 2026-11-03." in sentence
    assert "2026-11-05" not in sentence


@pytest.mark.unit
def test_methods__scopus_plus_openalex_mode__adds_no_sentence() -> None:
    """OQ-14: a legacy or combined project's methods bytes do not change."""
    connection = _store_with_runs()

    sentence = source_sentence(
        connection, "scopus+openalex", identified=1, duplicates=0, registered=0
    )

    assert sentence == ""
