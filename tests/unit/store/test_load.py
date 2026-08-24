"""Unit tests for ``store/load.py`` internals (BUILD_PLAN Stage 3 Tests table, lines 914-922).

Pure-logic and in-memory-DuckDB behaviour only -- no real files, no
``build_store``/filesystem pipeline (that belongs to
``tests/integration/store/test_load.py`` and ``tests/golden/store/test_load.py``).
Private loader functions (``_record_from_entry``, ``_accumulate_keyword``,
``_stats_from_connection``, ...) are called directly, never monkeypatched
(§3.7.3 rule 1: calling is not mocking).

Several tests below read one specific entry out of the frozen reference
fixture's raw ``page-NNNN.jsonl`` (via :func:`tests.store_helpers.read_reference_entry`)
-- the exact edge case BUILD_PLAN's Tests table names as "Edge case from the
reference fixture" -- without ever running the fixture through
``build_store``, keeping them fast and dependency-free on DuckDB except
where the test is explicitly about a query DuckDB itself must answer
(dedup reporting, citation "latest snapshot" default).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from prismabib.errors import ValidationError
from prismabib.models import PayloadRef
from prismabib.store.load import (
    Corpus,
    _accumulate_keyword,
    _Accumulator,
    _affiliations_from_entry,
    _authors_from_entry,
    _cited_by_count_from_entry,
    _cover_date_from_entry,
    _doc_type_from_entry,
    _keyword_id,
    _normalise_keyword_term,
    _open_access_from_entry,
    _record_from_entry,
    _record_id_from_entry,
    _sealed_run_dirs,
    _stats_from_connection,
    _subject_areas_from_entry,
    _title_from_entry,
)
from tests.store_helpers import create_schema, read_reference_entry, reference_run_dir

# BUILD_PLAN README location table: no-abstract edge case.
_NO_ABSTRACT_PAYLOAD_FILE = "page-0000.jsonl"
_NO_ABSTRACT_LINE = 2

# BUILD_PLAN README location table: zero-keywords edge case.
_ZERO_KEYWORDS_PAYLOAD_FILE = "page-0002.jsonl"
_ZERO_KEYWORDS_LINE = 5

# BUILD_PLAN README location table: duplicate-DOI edge case, records A and B.
_DUPLICATE_DOI_A = ("page-0002.jsonl", 10)
_DUPLICATE_DOI_B = ("page-0003.jsonl", 15)


def _load_reference_entry(payload_file: str, line: int) -> tuple[dict[str, object], PayloadRef]:
    """Read and parse one reference-fixture entry, paired with its ``PayloadRef``."""
    entry = read_reference_entry(payload_file, line)
    payload_ref = PayloadRef(path=reference_run_dir() / payload_file, line=line)
    return entry, payload_ref


@pytest.mark.unit
def test_load__record_without_abstract__loads_with_null() -> None:
    entry, payload_ref = _load_reference_entry(_NO_ABSTRACT_PAYLOAD_FILE, _NO_ABSTRACT_LINE)

    record = _record_from_entry(
        entry, record_id=_record_id_from_entry(entry), payload_ref=payload_ref
    )

    assert record.abstract is None


@pytest.mark.unit
def test_load__record_with_zero_keywords__creates_no_keyword_rows() -> None:
    entry, payload_ref = _load_reference_entry(_ZERO_KEYWORDS_PAYLOAD_FILE, _ZERO_KEYWORDS_LINE)
    record = _record_from_entry(
        entry, record_id=_record_id_from_entry(entry), payload_ref=payload_ref
    )
    acc = _Accumulator()

    for term in record.author_keywords:
        _accumulate_keyword(acc, record_id=record.record_id, raw_term=term, kind="author")
    for term in record.index_keywords:
        _accumulate_keyword(acc, record_id=record.record_id, raw_term=term, kind="index")

    assert (record.author_keywords, record.index_keywords) == ([], [])
    assert (acc.keywords, acc.record_keywords) == ({}, set())


@pytest.mark.unit
def test_load__duplicate_doi__both_records_retained_and_flagged() -> None:
    entry_a, payload_ref_a = _load_reference_entry(*_DUPLICATE_DOI_A)
    entry_b, payload_ref_b = _load_reference_entry(*_DUPLICATE_DOI_B)
    record_a = _record_from_entry(
        entry_a, record_id=_record_id_from_entry(entry_a), payload_ref=payload_ref_a
    )
    record_b = _record_from_entry(
        entry_b, record_id=_record_id_from_entry(entry_b), payload_ref=payload_ref_b
    )

    connection = duckdb.connect(":memory:")
    create_schema(connection)
    for record in (record_a, record_b):
        connection.execute(
            'INSERT INTO "records" VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
            [
                record.record_id,
                "run-x",
                record.doi,
                record.title,
                record.abstract,
                record.year,
                record.cover_date,
                record.doc_type,
                record.language,
                "venue-x",
                record.open_access,
                "page-x.jsonl",
                0,
            ],
        )
    stats = _stats_from_connection(connection, rebuilt=True)
    connection.close()

    assert (record_a.doi, record_a.record_id == record_b.record_id) == (record_b.doi, False)
    assert (stats.records_loaded, stats.duplicate_doi_groups, stats.duplicate_records) == (2, 1, 2)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw_term", "expected_norm"),
    [
        ("Convolutional Neural Networks", "convolutional neural network"),
        ("  Domain   Adaptation  ", "domain adaptation"),
        ("Edge-Computing!!", "edge-computing"),
        ("Support Vector Machines", "support vector machine"),
        ("GANs", "gans"),
    ],
    ids=[
        "singularise-cnn",
        "whitespace-collapse",
        "punctuation-strip",
        "singularise-svm",
        "no-match",
    ],
)
def test_keywords__normalisation__collapses_case_and_plurals(
    raw_term: str, expected_norm: str
) -> None:
    acc = _Accumulator()

    _accumulate_keyword(acc, record_id="scopus:1", raw_term=raw_term, kind="author")

    keyword_id = _keyword_id(expected_norm)
    assert _normalise_keyword_term(raw_term) == expected_norm
    assert acc.keywords[keyword_id] == (keyword_id, raw_term, expected_norm)


@pytest.mark.unit
def test_citations__query_without_date__uses_latest_snapshot() -> None:
    connection = duckdb.connect(":memory:")
    create_schema(connection)
    connection.execute(
        "INSERT INTO citation_snapshots VALUES (?, ?, ?)",
        ["scopus:1", datetime(2024, 1, 1, tzinfo=UTC), 5],
    )
    connection.execute(
        "INSERT INTO citation_snapshots VALUES (?, ?, ?)",
        ["scopus:1", datetime(2024, 6, 1, tzinfo=UTC), 12],
    )
    corpus = Corpus(connection)

    result = corpus.citations()
    connection.close()

    assert result["record_id"].to_list() == ["scopus:1"]
    assert result["cited_by_count"].to_list() == [12]


@pytest.mark.unit
def test__sealed_run_dirs__nonexistent_directory__returns_empty_list(tmp_path: Path) -> None:
    missing = tmp_path / "raw-does-not-exist"

    result = _sealed_run_dirs(missing)

    assert result == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("entry", "expected_match"),
    [
        ({"dc:title": "Some Title"}, "missing 'prism:coverDate'"),
        (
            {"dc:title": "Some Title", "prism:coverDate": "not-a-date"},
            "unparseable prism:coverDate",
        ),
    ],
    ids=["missing-field", "unparseable-value"],
)
def test__cover_date_from_entry__invalid_input__raises_validation_error(
    entry: dict[str, object], expected_match: str
) -> None:
    payload_ref = PayloadRef(path=Path("page-0000.jsonl"), line=0)

    with pytest.raises(ValidationError, match=expected_match):
        _cover_date_from_entry(entry, payload_ref=payload_ref)


@pytest.mark.unit
def test__title_from_entry__missing_field__raises_validation_error() -> None:
    entry = {"prism:coverDate": "2020-01-01"}
    payload_ref = PayloadRef(path=Path("page-0000.jsonl"), line=0)

    with pytest.raises(ValidationError, match="missing 'dc:title'"):
        _title_from_entry(entry, payload_ref=payload_ref)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ({"subtypeDescription": "Article", "subtype": "ar"}, "Article"),
        ({"subtype": "cp"}, "cp"),
        ({}, "unknown"),
    ],
    ids=["description-present", "subtype-only", "neither-present"],
)
def test__doc_type_from_entry__fallback_chain__resolves_expected_value(
    entry: dict[str, object], expected: str
) -> None:
    assert _doc_type_from_entry(entry) == expected


@pytest.mark.unit
def test__open_access_from_entry__no_flag_or_string_present__returns_none() -> None:
    assert _open_access_from_entry({}) is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "entry",
    [
        {},
        {"citedby-count": "not-a-number"},
    ],
    ids=["missing-field", "unparseable-value"],
)
def test__cited_by_count_from_entry__missing_or_unparseable__returns_none(
    entry: dict[str, object],
) -> None:
    assert _cited_by_count_from_entry(entry) is None


@pytest.mark.unit
def test__authors_from_entry__non_dict_item_in_list__is_skipped() -> None:
    entry = {"author": [{"surname": "Valid", "authid": "111"}, "not-an-author-object"]}

    authors = _authors_from_entry(entry)

    assert [author.surname for author in authors] == ["Valid"]
    assert authors[0].author_id == "111"


@pytest.mark.unit
def test__authors_from_entry__no_author_list__falls_back_to_dc_creator() -> None:
    entry = {"dc:creator": "  Smith J.  "}

    authors = _authors_from_entry(entry)

    assert len(authors) == 1
    assert authors[0].author_id is None
    assert authors[0].surname == "Smith J."


@pytest.mark.unit
def test__affiliations_from_entry__bare_dict__coerced_to_single_item_list() -> None:
    entry = {
        "affiliation": {
            "afid": "60000123",
            "affilname": "Acme Institute",
            "affiliation-city": "Metropolis",
            "affiliation-country": "USA",
        }
    }

    affiliations = _affiliations_from_entry(entry)

    assert len(affiliations) == 1
    assert affiliations[0].afid == "60000123"
    assert affiliations[0].name == "Acme Institute"


@pytest.mark.unit
def test__affiliations_from_entry__non_dict_item_in_list__is_skipped() -> None:
    entry = {"affiliation": [{"afid": "1", "affilname": "Kept"}, "not-an-affiliation-object"]}

    affiliations = _affiliations_from_entry(entry)

    assert [affiliation.name for affiliation in affiliations] == ["Kept"]


@pytest.mark.unit
def test__subject_areas_from_entry__mixed_shapes__extracts_valid_codes_only() -> None:
    entry = {
        "subject-area": [
            {"@code": "1000"},
            {"$": "2000"},
            {},
            "3000",
            42,
        ]
    }

    codes = _subject_areas_from_entry(entry)

    assert codes == ["1000", "2000", "3000"]


@pytest.mark.unit
def test__accumulate_keyword__punctuation_only_term__creates_no_rows() -> None:
    acc = _Accumulator()

    _accumulate_keyword(acc, record_id="scopus:1", raw_term="!!!", kind="author")

    assert (acc.keywords, acc.record_keywords) == ({}, set())
