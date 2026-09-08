"""``dimension_distribution``/``dimension_evolution`` (ADR 0025 Decision 2; BUILD_PLAN Stage 9).

Both wrap :mod:`prismabib.taxonomy.coder` machinery already tested for its
own arithmetic (``tests/unit/taxonomy/test_coder.py``); what is asserted
here is the reshape into an :class:`~prismabib.bibliometrics.base.AnalysisResult`
figure 7/8 can read without computing, and the year breakdown neither
existed before this stage.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.bibliometrics.base import AnalysisResult
from prismabib.errors import ValidationError
from prismabib.taxonomy.analysis import dimension_distribution, dimension_evolution
from prismabib.taxonomy.coder import CountingUnit, code
from prismabib.taxonomy.schema import load_dimensions
from tests.bibliometrics_helpers import include_everything, open_corpus
from tests.taxonomy_helpers import (
    LEARNING_PARADIGM_RULES_OVER_ASSIGNED,
    LEARNING_PARADIGM_RULES_V1,
    TEST_DIMENSIONS_YAML,
    TaxonomyRecordSpec,
    build_taxonomy_project,
    load_rule_files,
    write_dimensions,
    write_rule_file,
)


@pytest.fixture
def coded_project(tmp_path: Path):
    """Four records across two publication years, coded on ``learning_paradigm``."""
    records = [
        TaxonomyRecordSpec(number=1, title="A AlphaMarker approach", year=2020),
        TaxonomyRecordSpec(number=2, title="An BetaMarker approach", year=2020),
        TaxonomyRecordSpec(number=3, title="A GammaMarker approach", year=2021),
        TaxonomyRecordSpec(number=4, title="Plain title, no marker at all", year=2021),
    ]
    project = build_taxonomy_project(tmp_path, records)
    include_everything(project)
    write_dimensions(project, TEST_DIMENSIONS_YAML)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)
    result = code(corpus, [rule_file], [])
    return corpus, result, schema, records


@pytest.mark.unit
def test_dimension_distribution__returns_analysis_result(coded_project) -> None:
    corpus, result, schema, _records = coded_project

    analysis = dimension_distribution(
        corpus, result, schema, "learning_paradigm", CountingUnit.PAPERS
    )

    assert isinstance(analysis, AnalysisResult)
    counts = dict(
        zip(analysis.data["category"].to_list(), analysis.data["count"].to_list(), strict=True)
    )
    assert counts["supervised"] == 1
    assert counts["unsupervised"] == 1
    assert counts["self_supervised"] == 1
    assert counts["uncoded"] == 1
    assert analysis.data["category"].to_list() == [
        "supervised",
        "unsupervised",
        "self_supervised",
        "uncoded",
        "reviewed_none",
    ]


@pytest.mark.unit
def test_dimension_distribution__caption__states_counting_unit(coded_project) -> None:
    corpus, result, schema, _records = coded_project

    analysis = dimension_distribution(
        corpus, result, schema, "learning_paradigm", CountingUnit.PAPERS
    )

    assert "counting_unit=papers" in analysis.caption()
    assert f"n = {analysis.provenance.corpus_size}" in analysis.caption()


@pytest.mark.unit
def test_dimension_distribution__over_assigned_fixture__raises(tmp_path: Path) -> None:
    """The same guard `distribution()` enforces (ADR 0023 Decision 4) still fires through here."""
    records = [TaxonomyRecordSpec(number=1, title="An AmbiguousMarker approach")]
    project = build_taxonomy_project(tmp_path, records)
    include_everything(project)
    write_dimensions(project, TEST_DIMENSIONS_YAML)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_OVER_ASSIGNED)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)
    result = code(corpus, [rule_file], [])

    with pytest.raises(ValidationError):
        dimension_distribution(corpus, result, schema, "learning_paradigm", CountingUnit.PAPERS)


@pytest.mark.unit
def test_dimension_evolution__hand_counted_by_year__matches(coded_project) -> None:
    corpus, result, schema, _records = coded_project

    analysis = dimension_evolution(corpus, result, schema, "learning_paradigm", CountingUnit.PAPERS)

    rows = {(row["year"], row["category"]): row["count"] for row in analysis.data.to_dicts()}
    # r1 (2020, supervised), r2 (2020, unsupervised), r3 (2021, self_supervised);
    # r4 is uncoded and contributes no row at all.
    assert rows == {
        (2020, "supervised"): 1,
        (2020, "unsupervised"): 1,
        (2021, "self_supervised"): 1,
    }


@pytest.mark.unit
def test_dimension_evolution__caption__states_counting_unit(coded_project) -> None:
    corpus, result, schema, _records = coded_project

    analysis = dimension_evolution(
        corpus, result, schema, "learning_paradigm", CountingUnit.ASSIGNMENTS
    )

    assert "counting_unit=assignments" in analysis.caption()


@pytest.mark.unit
def test_dimension_evolution__no_years__empty_frame(tmp_path: Path) -> None:
    project = build_taxonomy_project(tmp_path, [])
    write_dimensions(project, TEST_DIMENSIONS_YAML)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)
    result = code(corpus, [rule_file], [])

    analysis = dimension_evolution(corpus, result, schema, "learning_paradigm", CountingUnit.PAPERS)

    assert analysis.data.height == 0
    assert analysis.data.schema.names() == ["year", "category", "count"]
