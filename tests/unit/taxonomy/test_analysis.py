"""``dimension_distribution``/``dimension_evolution`` (ADR 0025 Decision 2; BUILD_PLAN Stage 9).

Both wrap :mod:`prismabib.taxonomy.coder` machinery already tested for its
own arithmetic (``tests/unit/taxonomy/test_coder.py``); what is asserted
here is the reshape into an :class:`~prismabib.bibliometrics.base.AnalysisResult`
figure 7/8 can read without computing, and the year breakdown neither
existed before this stage.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from prismabib.bibliometrics.base import AnalysisResult
from prismabib.errors import AnalysisError, ValidationError
from prismabib.taxonomy import TAXONOMY_ENGINE_BEHAVIOUR
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
def test_dimension_distribution__params__carry_engine_behaviour(coded_project) -> None:
    corpus, result, schema, _records = coded_project

    analysis = dimension_distribution(
        corpus, result, schema, "learning_paradigm", CountingUnit.PAPERS
    )

    assert analysis.params["taxonomy_engine_behaviour"] == TAXONOMY_ENGINE_BEHAVIOUR
    assert f"taxonomy_engine_behaviour={TAXONOMY_ENGINE_BEHAVIOUR}" in analysis.caption()


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


def _counts(analysis: AnalysisResult) -> dict[str, int]:
    return dict(
        zip(analysis.data["category"].to_list(), analysis.data["count"].to_list(), strict=True)
    )


@pytest.mark.unit
def test_dimension_distribution__years__counts_and_denominators_are_in_range(
    coded_project,
) -> None:
    corpus, result, schema, _records = coded_project

    ranged = dimension_distribution(
        corpus, result, schema, "learning_paradigm", CountingUnit.PAPERS, years=(2021, None)
    )

    # 2021 holds r3 (self_supervised) and r4 (uncoded); r1/r2 are out of range.
    assert _counts(ranged) == {
        "supervised": 0,
        "unsupervised": 0,
        "self_supervised": 1,
        "uncoded": 1,
        "reviewed_none": 0,
    }
    assert ranged.params["years"] == "2021-..."
    assert ranged.params["n_in_range"] == 2
    assert ranged.params["n_no_year_dropped"] == 0
    assert "papers published in 2021-..." in ranged.caption()
    # Provenance describes the unrestricted stage frame.
    assert ranged.provenance.corpus_size == 4


@pytest.mark.unit
def test_dimension_distribution__years__null_year_dropped_and_counted(
    coded_project, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, result, schema, _records = coded_project
    original = corpus.records

    def with_null_year(stage):
        frame = original(stage)
        return frame.with_columns(
            pl.when(pl.col("record_id") == frame.item(0, "record_id"))
            .then(None)
            .otherwise(pl.col("year"))
            .alias("year")
        )

    monkeypatch.setattr(corpus, "records", with_null_year)

    ranged = dimension_distribution(
        corpus, result, schema, "learning_paradigm", CountingUnit.PAPERS, years=(2000, 2030)
    )

    assert ranged.params["n_no_year_dropped"] == 1
    assert ranged.params["n_in_range"] == 3
    assert sum(_counts(ranged).values()) == 3


@pytest.mark.unit
def test_dimension_distribution__years__empty_range_is_zero_rows(coded_project) -> None:
    corpus, result, schema, _records = coded_project

    ranged = dimension_distribution(
        corpus, result, schema, "learning_paradigm", CountingUnit.PAPERS, years=(1900, 1901)
    )

    assert ranged.params["n_in_range"] == 0
    assert ranged.data.height == 5
    assert set(_counts(ranged).values()) == {0}


@pytest.mark.unit
def test_dimension_distribution__no_years__params_and_output_unchanged(coded_project) -> None:
    corpus, result, schema, _records = coded_project
    plain = dimension_distribution(corpus, result, schema, "learning_paradigm", CountingUnit.PAPERS)

    for years in (None, (None, None)):
        same = dimension_distribution(
            corpus, result, schema, "learning_paradigm", CountingUnit.PAPERS, years=years
        )
        assert same.params == plain.params
        assert not {"years", "n_in_range", "n_no_year_dropped"} & set(same.params)
        assert same.data.equals(plain.data)
        assert same.caption() == plain.caption()


@pytest.mark.unit
def test_dimension_distribution__inverted_range__raises_before_any_read() -> None:
    with pytest.raises(AnalysisError):
        dimension_distribution(None, None, None, "x", CountingUnit.PAPERS, years=(2021, 2020))  # type: ignore[arg-type]


@pytest.mark.unit
def test_dimension_distribution__years__over_assigned_guard_scoped_to_range(
    tmp_path: Path,
) -> None:
    records = [
        TaxonomyRecordSpec(number=1, title="Plain title, no marker", year=2020),
        TaxonomyRecordSpec(number=2, title="An AmbiguousMarker approach", year=2021),
    ]
    project = build_taxonomy_project(tmp_path, records)
    include_everything(project)
    write_dimensions(project, TEST_DIMENSIONS_YAML)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_OVER_ASSIGNED)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)
    result = code(corpus, [rule_file], [])

    inside = dimension_distribution(
        corpus, result, schema, "learning_paradigm", CountingUnit.PAPERS, years=(2020, 2020)
    )
    assert inside.params["n_in_range"] == 1
    with pytest.raises(ValidationError):
        dimension_distribution(
            corpus, result, schema, "learning_paradigm", CountingUnit.PAPERS, years=(2021, 2021)
        )


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
def test_dimension_evolution__params__carry_engine_behaviour(coded_project) -> None:
    corpus, result, schema, _records = coded_project

    analysis = dimension_evolution(
        corpus, result, schema, "learning_paradigm", CountingUnit.ASSIGNMENTS
    )

    assert analysis.params["taxonomy_engine_behaviour"] == TAXONOMY_ENGINE_BEHAVIOUR
    assert f"taxonomy_engine_behaviour={TAXONOMY_ENGINE_BEHAVIOUR}" in analysis.caption()


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
