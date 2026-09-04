"""Keyword frequency and evolution (BUILD_PLAN Stage 7, ADR 0022 Decision 6)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.bibliometrics.keywords import (
    DEFAULT_STOPWORDS_PATH,
    _load_stopwords,
    keyword_evolution,
    keyword_frequency,
)
from prismabib.stage import PrismaStage
from tests.bibliometrics_helpers import (
    BibCorpusSpec,
    BibRecordSpec,
    build_bib_project,
    include_everything,
    open_corpus,
)


@pytest.mark.unit
def test_stopwords__come_from_project_data_not_source() -> None:
    """The module holds no stopword literal -- a hardcoded default reds this test.

    Scans the module's own source text for a handful of the default list's
    terms; a future author who "simplifies" the loader by inlining the list
    back into Python makes this fail.
    """
    import prismabib.bibliometrics.keywords as keywords_module

    source = Path(keywords_module.__file__).read_text(encoding="utf-8")
    default_terms = [
        line.strip()
        for line in DEFAULT_STOPWORDS_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    assert default_terms, "the default stopword file itself must not be empty"

    present = [term for term in default_terms if f'"{term}"' in source or f"'{term}'" in source]
    assert not present, f"stopword terms appear as source literals: {present}"


@pytest.mark.unit
def test_load_stopwords__default_path__is_a_real_data_file() -> None:
    stopwords = _load_stopwords(None)

    assert "human" in stopwords
    assert "baseball" not in stopwords


@pytest.mark.unit
def test_load_stopwords__missing_override_path__degrades_to_empty(tmp_path: Path) -> None:
    stopwords = _load_stopwords(tmp_path / "does-not-exist.txt")

    assert stopwords == frozenset()


@pytest.mark.unit
def test_load_stopwords__project_override__is_used_instead_of_default(tmp_path: Path) -> None:
    override = tmp_path / "stopwords.txt"
    override.write_text("Baseball\n# a comment\n\ndeep learning\n", encoding="utf-8")

    stopwords = _load_stopwords(override)

    assert stopwords == frozenset({"baseball", "deep learning"})
    assert "human" not in stopwords  # the default list's terms are not merged in


@pytest.mark.integration
def test_keyword_frequency__hand_counted__matches(tmp_path: Path) -> None:
    """Three records: "baseball" on all three, "vision" on two, "human" (a stopword) on one."""
    records = [
        BibRecordSpec(number=1, author_keywords=("baseball", "vision")),
        BibRecordSpec(number=2, author_keywords=("baseball", "vision", "human")),
        BibRecordSpec(number=3, author_keywords=("baseball",)),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    corpus = open_corpus(project)

    result = keyword_frequency(corpus, stage=PrismaStage.RAW)

    assert result.data.to_dicts() == [
        {"term": "baseball", "count": 3},
        {"term": "vision", "count": 2},
    ]


@pytest.mark.integration
@pytest.mark.acceptance("S07-AC4")
def test_keywords__min_occurrence_change__changes_params_and_caption(tmp_path: Path) -> None:
    records = [
        BibRecordSpec(number=1, author_keywords=("baseball", "vision")),
        BibRecordSpec(number=2, author_keywords=("baseball",)),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    corpus = open_corpus(project)

    low = keyword_frequency(corpus, stage=PrismaStage.RAW, min_occurrence=1)
    high = keyword_frequency(corpus, stage=PrismaStage.RAW, min_occurrence=2)

    assert low.params["min_occurrence"] == 1
    assert high.params["min_occurrence"] == 2
    assert low.caption() != high.caption()
    assert low.data.height == 2  # baseball, vision
    assert high.data.height == 1  # baseball only


@pytest.mark.integration
def test_keyword_frequency__a_keyword_appearing_once__is_reported(tmp_path: Path) -> None:
    """The boundary case named explicitly in the task brief."""
    project = build_bib_project(
        tmp_path, BibCorpusSpec(records=[BibRecordSpec(number=1, author_keywords=("lonesome",))])
    )
    corpus = open_corpus(project)

    result = keyword_frequency(corpus, stage=PrismaStage.RAW)

    assert result.data.to_dicts() == [{"term": "lonesome", "count": 1}]


@pytest.mark.integration
def test_keyword_frequency__no_keywords_at_all__empty_data(tmp_path: Path) -> None:
    project = build_bib_project(
        tmp_path, BibCorpusSpec(records=[BibRecordSpec(number=1, author_keywords=())])
    )
    corpus = open_corpus(project)

    result = keyword_frequency(corpus, stage=PrismaStage.RAW)

    assert result.data.height == 0


@pytest.mark.integration
def test_keyword_frequency__default_included_stage__hand_computed_value_matches(
    tmp_path: Path,
) -> None:
    """`Corpus.keywords(INCLUDED)` value-checked, not just type/shape-checked (see test_geography.py)."""
    records = [
        BibRecordSpec(number=1, author_keywords=("baseball",)),
        BibRecordSpec(number=2, author_keywords=("baseball", "vision")),
        BibRecordSpec(number=3, author_keywords=("vision",)),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    include_everything(project)
    corpus = open_corpus(project)

    result = keyword_frequency(corpus)  # default stage=PrismaStage.INCLUDED

    assert result.data.to_dicts() == [
        {"term": "baseball", "count": 2},
        {"term": "vision", "count": 2},
    ]
    assert result.provenance.stage is PrismaStage.INCLUDED


@pytest.mark.integration
def test_keyword_evolution__hand_counted_by_year__matches(tmp_path: Path) -> None:
    records = [
        BibRecordSpec(number=1, year=2020, author_keywords=("baseball",)),
        BibRecordSpec(number=2, year=2020, author_keywords=("baseball",)),
        BibRecordSpec(number=3, year=2021, author_keywords=("baseball",)),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    corpus = open_corpus(project)

    result = keyword_evolution(corpus, stage=PrismaStage.RAW)

    assert result.data.to_dicts() == [
        {"year": 2020, "term": "baseball", "count": 2},
        {"year": 2021, "term": "baseball", "count": 1},
    ]
