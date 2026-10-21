"""Tests for candidate intake (ADR 0028 Decision 5)."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError as PydanticValidationError

from prismabib.capture import candidates
from prismabib.capture.candidates import (
    CandidateEvent,
    CandidateLog,
    CandidateRow,
    candidate_id,
    near_matches,
    normalise_doi,
    normalise_title,
    parse_pasted_rows,
    title_match,
)
from prismabib.errors import LogError, ValidationError

NOW = datetime(2026, 10, 21, 12, 0, tzinfo=UTC)


def _event(title: str = "A Study", year: int = 2020, doi: str | None = None) -> CandidateEvent:
    return CandidateEvent.from_row(CandidateRow(title, year, doi), reviewer="sh", pasted_at=NOW)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Hello,  World.", "hello world"),
        ("Café Society", "cafe society"),
        ("Well-being", "well being"),
        ("well‐being – a — study", "well being a study"),  # noqa: RUF001 - Unicode dashes on purpose
        ("Title ends here...", "title ends here"),
        ("  A\tB\nC  ", "a b c"),
        ("", ""),
    ],
)
def test_normalise_title__tricky_cases(raw: str, expected: str) -> None:
    assert normalise_title(raw) == expected


def test_normalise_title__different_words_stay_different() -> None:
    assert normalise_title("Cats") != normalise_title("Dogs")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://doi.org/10.1000/ABC", "10.1000/abc"),
        ("doi:10.1000/abc", "10.1000/abc"),
        ("  10.1000/ABC ", "10.1000/abc"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_normalise_doi(raw: str | None, expected: str | None) -> None:
    assert normalise_doi(raw) == expected


def test_parse__tab_comma_header_and_doi() -> None:
    text = (
        "Title\tYear\tDOI\nAlpha beta\t2019\nGamma, delta,2020\n"
        "Eps\t2021\tdoi:10.1/X\nZeta,2022,https://doi.org/10.2/Y\n"
    )
    assert parse_pasted_rows(text) == [
        CandidateRow("Alpha beta", 2019, None),
        CandidateRow("Gamma, delta", 2020, None),
        CandidateRow("Eps", 2021, "10.1/x"),
        CandidateRow("Zeta", 2022, "10.2/y"),
    ]


def test_parse__quoted_comma_title() -> None:
    assert parse_pasted_rows('"A, B",2018') == [CandidateRow("A, B", 2018, None)]


def test_parse__bad_rows_are_reported_with_line_numbers_never_dropped() -> None:
    text = "Good\t2020\n\t2021\nBad year\tabc\n\nNo year\nAlso good\t2019\n"
    with pytest.raises(ValidationError) as exc:
        parse_pasted_rows(text)
    message = str(exc.value)
    assert "line 2: missing title" in message
    assert "line 3: year 'abc' is not an integer" in message
    assert "line 5" in message
    assert "line 1" not in message
    assert "line 6" not in message


@pytest.mark.parametrize("bad", ["T\t2020.5", "T\t-2020", "T\t"])
def test_parse__non_integer_year_rejected(bad: str) -> None:
    with pytest.raises(ValidationError, match="line 1"):
        parse_pasted_rows(bad)


def test_parse__too_many_tab_columns_rejected() -> None:
    with pytest.raises(ValidationError, match="line 1"):
        parse_pasted_rows("a\t2020\t10.1/x\textra")


def test_candidate_id__deterministic_and_content_sensitive() -> None:
    assert _event().candidate_id == _event("a  study!").candidate_id
    assert _event().candidate_id != _event(year=2021).candidate_id
    assert _event().candidate_id != _event(doi="10.1/x").candidate_id
    assert _event(doi="DOI:10.1/X").candidate_id == _event(doi="10.1/x").candidate_id
    assert candidate_id("A Study", 2020, None) == _event().candidate_id


def test_event__forged_id_naive_time_and_blank_title_rejected() -> None:
    good = _event().model_dump()
    with pytest.raises(PydanticValidationError):
        CandidateEvent(**{**good, "candidate_id": "0" * 32})
    with pytest.raises(PydanticValidationError):
        CandidateEvent(**{**good, "pasted_at": datetime(2026, 1, 1)})  # noqa: DTZ001 - naive on purpose
    with pytest.raises(PydanticValidationError):
        CandidateEvent(**{**good, "title": "  "})


def test_log__append_only_roundtrip_and_repaste_detected(tmp_path: Path) -> None:
    log = CandidateLog(tmp_path / "decisions" / "candidates.jsonl")
    assert log.add(_event()) is True
    before = log.path.read_bytes()
    assert log.add(_event("A study.")) is False  # same normalised content
    assert log.path.read_bytes() == before  # nothing overwritten or appended
    assert log.add(_event("Other", 2021)) is True
    assert log.path.read_bytes().startswith(before)  # earlier bytes untouched
    assert [e.title for e in log.load()] == ["A Study", "Other"]


def test_log__tampering_is_detected(tmp_path: Path) -> None:
    log = CandidateLog(tmp_path / "c.jsonl")
    log.add(_event())
    log.path.write_bytes(log.path.read_bytes().replace(b"A Study", b"B Study"))
    with pytest.raises(LogError):
        log.load()


def test_title_match__exact_only() -> None:
    cand = _event("Well-being: A Study.", 2020)
    assert title_match(cand, "well being a study", 2020)
    assert not title_match(cand, "well being a study", 2021)
    assert not title_match(cand, "well being a study", None)
    assert not title_match(cand, "well being a study and more", 2020)
    assert not title_match(_event("...", 2020), "!!!", 2020)  # empty keys never match


def test_near_matches__lists_near_misses_not_exact_or_unrelated() -> None:
    cand = _event("Deep learning", 2020)
    records: list[tuple[str, int | None]] = [
        ("Deep Learning", 2020),  # exact: excluded
        ("Deep learning", 2019),  # year differs
        ("Deep learning: a survey", 2020),  # subtitle
        ("Deep learning: a survey", 2018),  # subtitle and year both off
        ("Deep learnings", 2020),  # not a whole-word prefix
        ("Shallow", 2020),
    ]
    assert near_matches(cand, records) == [
        ("Deep learning", 2019),
        ("Deep learning: a survey", 2020),
    ]
    assert not any(title_match(cand, t, y) for t, y in near_matches(cand, records))


def test_module__never_imports_or_calls_record_creation() -> None:
    tree = ast.parse(Path(candidates.__file__).read_text(encoding="utf-8"))
    imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names}
    assert "Record" not in imported
    assert not any(isinstance(n, ast.Name) and n.id == "Record" for n in ast.walk(tree))
    assert not any(n.startswith("register") for n in dir(candidates))
