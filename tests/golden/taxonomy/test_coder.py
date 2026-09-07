"""Golden test for :meth:`prismabib.taxonomy.coder.Distribution.caption` (BUILD_PLAN §Stage 8).

Every taxonomy figure must state its counting unit in the auto-generated
caption (BUILD_PLAN); this pins the exact text for
:attr:`~prismabib.taxonomy.schema.CountingUnit.KEYWORD_MENTIONS`, which
carries the explicit "NOT a paper distribution" safeguard the source
manuscript's own taxonomy figure did not have.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.taxonomy.coder import CountingUnit, code, distribution
from prismabib.taxonomy.schema import load_dimensions
from tests.bibliometrics_helpers import include_everything, open_corpus
from tests.taxonomy_helpers import (
    LEARNING_PARADIGM_RULES_V1,
    TaxonomyRecordSpec,
    build_taxonomy_project,
    load_rule_files,
    write_dimensions,
    write_rule_file,
)


@pytest.mark.golden
def test_counting_unit__mentions_mode__caption_states_it_is_not_a_paper_distribution(
    tmp_path: Path,
) -> None:
    project = build_taxonomy_project(
        tmp_path, [TaxonomyRecordSpec(number=1, title="A AlphaMarker approach")], slug="golden"
    )
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)

    result = code(corpus, [rule_file], [])
    dist = distribution(result, schema, "learning_paradigm", CountingUnit.KEYWORD_MENTIONS)

    assert dist.caption() == (
        "learning_paradigm keyword-mention counts (n=1 records considered); "
        "raw term frequency, NOT a paper distribution."
    )


@pytest.mark.golden
def test_counting_unit__papers_mode__caption_states_the_unit_and_multi_label(
    tmp_path: Path,
) -> None:
    project = build_taxonomy_project(
        tmp_path, [TaxonomyRecordSpec(number=1, title="A AlphaMarker approach")], slug="golden2"
    )
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)

    result = code(corpus, [rule_file], [])
    dist = distribution(result, schema, "learning_paradigm", CountingUnit.PAPERS)

    assert (
        dist.caption()
        == "learning_paradigm paper distribution, counting_unit=papers (n=1); multi_label=false."
    )


@pytest.mark.golden
def test_counting_unit__assignments_mode__caption_states_the_unit(tmp_path: Path) -> None:
    from tests.taxonomy_helpers import ARCHITECTURE_RULES_V1

    project = build_taxonomy_project(
        tmp_path,
        [TaxonomyRecordSpec(number=1, title="x", author_keywords=("DeltaMarker",))],
        slug="golden3",
    )
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "architecture", ARCHITECTURE_RULES_V1)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "architecture")
    corpus = open_corpus(project)

    result = code(corpus, [rule_file], [])
    dist = distribution(result, schema, "architecture", CountingUnit.ASSIGNMENTS)

    assert dist.caption() == (
        "architecture assignment counts, counting_unit=assignments "
        "(n=1; a record may contribute to more than one category)."
    )
