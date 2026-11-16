"""Taxonomy analyses recompute byte-identically (S07-AC5).

`test_analysis_result_contract.py`'s sweep leaves `taxonomy.analysis` out (its
functions need a coding result, not just a loaded project), so this file gives them the
same check: two separately opened read-only handles, byte-equal serialisation.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.bibliometrics.base import _serialise
from prismabib.store.load import Corpus
from prismabib.taxonomy.analysis import dimension_distribution, dimension_evolution
from prismabib.taxonomy.coder import CountingUnit, code
from prismabib.taxonomy.schema import load_dimensions
from tests.bibliometrics_helpers import include_everything
from tests.taxonomy_helpers import (
    LEARNING_PARADIGM_RULES_V1,
    TEST_DIMENSIONS_YAML,
    TaxonomyRecordSpec,
    build_taxonomy_project,
    load_rule_files,
    write_dimensions,
    write_rule_file,
)

_DIMENSION = "learning_paradigm"


def _run(project, function):
    """Open a fresh read-only handle, code it, and run ``function`` on it."""
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, _DIMENSION)
    corpus = Corpus.open(project, read_only=True)
    result = code(corpus, [rule_file], [])
    return function(corpus, result, schema, _DIMENSION, CountingUnit.PAPERS)


@pytest.fixture
def tied_project(tmp_path: Path):
    """Two categories tied on count in 2020 and in 2021.

    The unsupervised record comes first in each year, so insertion order is the
    reverse of ascending category order: only a real tie-break puts them right.
    """
    records = [
        TaxonomyRecordSpec(number=1, title="A BetaMarker approach", year=2020),
        TaxonomyRecordSpec(number=2, title="An AlphaMarker approach", year=2020),
        TaxonomyRecordSpec(number=3, title="A BetaMarker study", year=2021),
        TaxonomyRecordSpec(number=4, title="An AlphaMarker study", year=2021),
    ]
    project = build_taxonomy_project(tmp_path, records)
    include_everything(project)
    write_dimensions(project, TEST_DIMENSIONS_YAML)
    write_rule_file(project, _DIMENSION, LEARNING_PARADIGM_RULES_V1)
    return project


@pytest.mark.integration
@pytest.mark.acceptance("S07-AC5")
@pytest.mark.parametrize("function", [dimension_distribution, dimension_evolution])
def test_taxonomy_analyses__recomputed_twice__is_bit_identical(tied_project, function) -> None:
    first = _serialise(_run(tied_project, function))
    second = _serialise(_run(tied_project, function))

    assert first == second


@pytest.mark.integration
@pytest.mark.acceptance("S07-AC5")
def test_dimension_evolution__tied_counts_in_a_year__ascending_category_order(tied_project) -> None:
    rows = _run(tied_project, dimension_evolution).data.to_dicts()

    assert [(r["year"], r["category"], r["count"]) for r in rows] == [
        (2020, "supervised", 1),
        (2020, "unsupervised", 1),
        (2021, "supervised", 1),
        (2021, "unsupervised", 1),
    ]
