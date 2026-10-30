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
    DoiConflictEvent,
    DoiEnrichmentEvent,
    candidate_id,
    near_matches,
    near_rule,
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
    assert candidate_id("A Study", 2020) == _event().candidate_id


def test_candidate_id__same_paper_with_and_without_doi_is_one_candidate(tmp_path: Path) -> None:
    bare, with_doi = _event(), _event(doi="DOI:10.1/X")
    assert bare.candidate_id == with_doi.candidate_id
    assert with_doi.doi == "10.1/x"  # still stored, normalised
    log = CandidateLog(tmp_path / "c.jsonl")
    assert log.add(bare) == "added"
    assert log.add(with_doi) == "enriched"
    assert len(log.load()) == 1


def test_candidate_id__different_titles_same_year_are_two_candidates(tmp_path: Path) -> None:
    a, b = _event("Alpha"), _event("Beta")
    assert a.candidate_id != b.candidate_id
    log = CandidateLog(tmp_path / "c.jsonl")
    assert log.add(a) == "added"
    assert log.add(b) == "added"
    assert len(log.load()) == 2


def test_enrichment__doi_less_candidate_repasted_with_doi_gets_one_event(tmp_path: Path) -> None:
    log = CandidateLog(tmp_path / "c.jsonl")
    cid = _event().candidate_id
    log.add(_event())
    assert log.effective_doi(cid) is None
    assert log.add(_event(doi="https://doi.org/10.1/X")) == "enriched"
    events = log.load_enrichments()
    assert [(e.candidate_id, e.doi) for e in events] == [(cid, "10.1/x")]
    assert log.effective_doi(cid) == "10.1/x"
    assert [(v.event.title, v.doi) for v in log.view()] == [("A Study", "10.1/x")]
    assert len(log.load()) == 1  # candidate events untouched


def test_enrichment__same_doi_again_is_already_present_and_writes_nothing(tmp_path: Path) -> None:
    log = CandidateLog(tmp_path / "c.jsonl")
    log.add(_event())
    log.add(_event(doi="10.1/x"))
    before = (log.path.read_bytes(), log.doi_path.read_bytes())
    assert log.add(_event(doi="10.1/x")) == "already_present"
    assert log.add(_event()) == "already_present"  # no DOI offered
    assert (log.path.read_bytes(), log.doi_path.read_bytes()) == before


def test_conflict__different_doi_on_enriched_candidate_first_stands(tmp_path: Path) -> None:
    log = CandidateLog(tmp_path / "c.jsonl")
    cid = _event().candidate_id
    log.add(_event())
    log.add(_event(doi="10.1/first"))
    assert log.add(_event(doi="10.1/second")) == "conflict"
    assert log.effective_doi(cid) == "10.1/first"
    assert len(log.load_enrichments()) == 1
    (conflict,) = log.conflicts(cid)
    assert (conflict.doi, conflict.stored_doi) == ("10.1/second", "10.1/first")
    assert log.view()[0].conflicting_dois == ("10.1/second",)


def test_conflict__different_doi_on_original_doi_first_stands(tmp_path: Path) -> None:
    log = CandidateLog(tmp_path / "c.jsonl")
    cid = _event().candidate_id
    assert log.add(_event(doi="10.1/first")) == "added"
    assert log.add(_event(doi="10.1/second")) == "conflict"
    assert log.effective_doi(cid) == "10.1/first"
    assert [c.doi for c in log.conflicts(cid)] == ["10.1/second"]
    assert log.load_enrichments() == []  # not an enrichment
    assert log.conflicts("other") == []


def test_conflict__same_conflicting_doi_again_writes_nothing_more(tmp_path: Path) -> None:
    log = CandidateLog(tmp_path / "c.jsonl")
    log.add(_event(doi="10.1/first"))
    log.add(_event(doi="10.1/second"))
    before = log.conflict_path.read_bytes()
    assert log.add(_event(doi="10.1/second")) == "conflict"
    assert log.conflict_path.read_bytes() == before
    assert log.add(_event(doi="10.1/third")) == "conflict"  # a new distinct one is recorded
    assert [c.doi for c in log.load_conflicts()] == ["10.1/second", "10.1/third"]


def test_conflict__matching_or_absent_doi_is_already_present_without_write(
    tmp_path: Path,
) -> None:
    log = CandidateLog(tmp_path / "c.jsonl")
    log.add(_event(doi="10.1/first"))
    assert log.add(_event(doi="DOI:10.1/FIRST")) == "already_present"
    assert log.add(_event()) == "already_present"
    assert log.load_conflicts() == []


def test_conflict__tampering_is_detected(tmp_path: Path) -> None:
    log = CandidateLog(tmp_path / "c.jsonl")
    log.add(_event(doi="10.1/first"))
    log.add(_event(doi="10.1/second"))
    log.conflict_path.write_bytes(log.conflict_path.read_bytes().replace(b"10.1/second", b"10.9/z"))
    with pytest.raises(LogError):
        log.load_conflicts()
    with pytest.raises(LogError):
        log.view()


def test_conflict__event_must_actually_differ() -> None:
    with pytest.raises(PydanticValidationError):
        DoiConflictEvent(
            candidate_id="x", doi="10.1/a", stored_doi="DOI:10.1/A", pasted_at=NOW, reviewer="sh"
        )


def test_enrichment__tampering_is_detected(tmp_path: Path) -> None:
    log = CandidateLog(tmp_path / "c.jsonl")
    log.add(_event())
    log.add(_event(doi="10.1/x"))
    log.doi_path.write_bytes(log.doi_path.read_bytes().replace(b"10.1/x", b"10.9/y"))
    with pytest.raises(LogError):
        log.load_enrichments()
    with pytest.raises(LogError):
        log.view()


def test_enrichment__blank_doi_rejected_and_unknown_candidate_has_no_doi(tmp_path: Path) -> None:
    with pytest.raises(PydanticValidationError):
        DoiEnrichmentEvent(candidate_id="x", doi="  ", pasted_at=NOW, reviewer="sh")
    assert CandidateLog(tmp_path / "c.jsonl").effective_doi("nope") is None


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
    assert log.add(_event()) == "added"
    before = log.path.read_bytes()
    assert log.add(_event("A study.")) == "already_present"  # same normalised content
    assert log.path.read_bytes() == before  # nothing overwritten or appended
    assert log.add(_event("Other", 2021)) == "added"
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


def test_near_rule__agrees_with_near_matches() -> None:
    cand = _event("Deep learning", 2020)
    table: list[tuple[str, int | None, str | None]] = [
        ("Deep Learning", 2020, None),  # exact
        ("Deep learning", 2019, "same_title_other_year"),
        ("Deep learning", None, "same_title_other_year"),
        ("Deep learning: a survey", 2020, "title_prefix"),
        ("A deep learning", 2020, None),  # candidate is a suffix, not a prefix
        ("Deep learning: a survey", 2018, None),
        ("Deep learnings", 2020, None),
        ("Shallow", 2020, None),
        ("!!!", 2020, None),
    ]
    records = [(t, y) for t, y, _ in table]
    assert [near_rule(cand, t, y) for t, y in records] == [r for _, _, r in table]
    assert near_matches(cand, records) == [(t, y) for t, y, r in table if r is not None]


def _references_record(source: str) -> bool:
    """True if ``source`` imports, names or attribute-accesses ``Record``, or star-imports."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom | ast.Import) and any(
            a.name in {"Record", "*"} for a in node.names
        ):
            return True
        if isinstance(node, ast.Name) and node.id == "Record":
            return True
        if isinstance(node, ast.Attribute) and node.attr == "Record":
            return True
    return False


@pytest.mark.parametrize(
    "source",
    [
        "from prismabib.models import Record",
        "from prismabib.models import Record as R",
        "import prismabib.models as m\nm.Record(x=1)",
        "import prismabib.models\nprismabib.models.Record",
        "from prismabib.models import *",
    ],
)
def test_record_guard__detects_each_form(source: str) -> None:
    assert _references_record(source)


def test_record_guard__passes_innocent_code() -> None:
    assert not _references_record("from prismabib.models import normalise_doi as n")


def test_module__never_imports_or_calls_record_creation() -> None:
    assert not _references_record(Path(candidates.__file__).read_text(encoding="utf-8"))
    assert not any(n.startswith("register") for n in dir(candidates))


def test_parse__header_with_doi_column_is_skipped_in_both_forms() -> None:
    assert parse_pasted_rows("Title,Year,DOI\nA,2020") == [CandidateRow("A", 2020, None)]
    assert parse_pasted_rows("Title\tYear\tDOI\nA\t2020") == [CandidateRow("A", 2020, None)]


def test_parse__header_only_skipped_when_first() -> None:
    with pytest.raises(ValidationError, match="line 2"):
        parse_pasted_rows("A,2020\nTitle,Year,DOI")


def test_parse__trailing_empty_doi_column_accepted_like_tab_form() -> None:
    expected = [CandidateRow("Deep learning", 2020, None)]
    assert parse_pasted_rows("Deep learning,2020,") == expected
    assert parse_pasted_rows("Deep learning\t2020\t") == expected
    assert parse_pasted_rows("A, B,2020,") == [CandidateRow("A, B", 2020, None)]


def test_event__schema_version_follows_single_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _event().schema_version == candidates.CURRENT_CANDIDATE_SCHEMA_VERSION
    monkeypatch.setattr(candidates, "CURRENT_CANDIDATE_SCHEMA_VERSION", 2)
    assert _event().schema_version == 2  # a bump must not break new events
    with pytest.raises(PydanticValidationError):
        CandidateEvent(**{**_event().model_dump(), "schema_version": 1})


@pytest.mark.parametrize("title", ["???", "!!!", "   ", "\u2014"])
def test_parse_pasted_rows__punctuation_only_title__rejected_with_reason(title: str) -> None:
    with pytest.raises(ValidationError, match=r"line 1: "):
        parse_pasted_rows(f"{title}\t2020")


def test_parse_pasted_rows__punctuation_only_title__reason_is_clear() -> None:
    with pytest.raises(ValidationError, match="no letters or digits"):
        parse_pasted_rows("???\t2020")


def test_candidate_event__punctuation_only_title__still_loads_from_log() -> None:
    event = _event("???", 2020)
    assert CandidateEvent.model_validate_json(event.model_dump_json()) == event


def test_candidate_id__trailing_punctuation_dropped_by_design() -> None:
    assert candidates.candidate_id("Quo?", 2020) == candidates.candidate_id("Quo!", 2020)


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Quo?", "93aea16c62632e827d8ae5a62807cf68"),
        ("Hello World", "86dd518b8fb88b5e815e8c42c9ed4095"),
        ("Caf\u00e9-au-lait", "ad11b2ccb4c97418657b26fcee034eec"),
        ("Well-being", "359766c6f7d8ad8cd60d12e2cb04becb"),
    ],
)
def test_candidate_id__golden_values_pinned(title: str, expected: str) -> None:
    assert candidates.candidate_id(title, 2020) == expected
