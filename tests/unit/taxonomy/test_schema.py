"""Unit tests for :mod:`prismabib.taxonomy.schema` (BUILD_PLAN §Stage 8, ADR 0023 Decision 2)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from prismabib.errors import ConfigError
from prismabib.project import Project
from prismabib.taxonomy.schema import (
    RESERVED_BUCKET_NAMES,
    CountingUnit,
    Dimension,
    TaxonomySchema,
    load_dimensions,
)
from tests.taxonomy_helpers import TEST_DIMENSIONS_YAML, write_dimensions


@pytest.mark.unit
def test_load_dimensions__missing_file__raises_naming_the_path(tmp_path: Path) -> None:
    project = Project.init("demo", title="Demo", root=tmp_path)

    with pytest.raises(ConfigError, match=r"dimensions\.yaml"):
        load_dimensions(project)


@pytest.mark.unit
def test_load_dimensions__valid_file__parses_every_dimension(tmp_path: Path) -> None:
    project = Project.init("demo", title="Demo", root=tmp_path)
    write_dimensions(project, TEST_DIMENSIONS_YAML)

    schema = load_dimensions(project)

    assert [dimension.id for dimension in schema.dimensions] == [
        "learning_paradigm",
        "architecture",
    ]
    assert schema.dimension("architecture").multi_label is True
    assert schema.dimension("learning_paradigm").multi_label is False
    assert "architecture" in schema
    assert "not_a_dimension" not in schema


@pytest.mark.unit
def test_load_dimensions__unknown_top_level_key__raises(tmp_path: Path) -> None:
    project = Project.init("demo", title="Demo", root=tmp_path)
    write_dimensions(project, "dimensions: []\nunexpected: true\n")

    with pytest.raises(ConfigError, match="does not satisfy"):
        load_dimensions(project)


@pytest.mark.unit
def test_load_dimensions__not_valid_yaml__raises(tmp_path: Path) -> None:
    project = Project.init("demo", title="Demo", root=tmp_path)
    write_dimensions(project, "dimensions: [unclosed\n")

    with pytest.raises(ConfigError, match="not valid YAML"):
        load_dimensions(project)


@pytest.mark.unit
def test_taxonomy_schema__duplicate_dimension_id__raises() -> None:
    with pytest.raises(Exception, match="duplicate dimension"):
        TaxonomySchema.model_validate(
            {
                "dimensions": [
                    {"id": "x", "multi_label": False, "categories": ["a"]},
                    {"id": "x", "multi_label": True, "categories": ["b"]},
                ]
            }
        )


@pytest.mark.unit
def test_taxonomy_schema__dimension_with_no_categories__raises() -> None:
    with pytest.raises(Exception, match="at least one category"):
        TaxonomySchema.model_validate(
            {"dimensions": [{"id": "x", "multi_label": False, "categories": []}]}
        )


@pytest.mark.unit
def test_taxonomy_schema__duplicate_category__raises() -> None:
    with pytest.raises(Exception, match="duplicate categories"):
        TaxonomySchema.model_validate(
            {"dimensions": [{"id": "x", "multi_label": False, "categories": ["a", "a"]}]}
        )


@pytest.mark.unit
def test_taxonomy_schema__dimension_lookup__unknown_id_raises_keyerror() -> None:
    schema = TaxonomySchema.model_validate(
        {"dimensions": [{"id": "x", "multi_label": False, "categories": ["a"]}]}
    )

    with pytest.raises(KeyError):
        schema.dimension("y")


@pytest.mark.unit
@pytest.mark.parametrize("unit", list(CountingUnit))
def test_counting_unit__every_member__round_trips_through_its_string_value(
    unit: CountingUnit,
) -> None:
    assert CountingUnit(unit.value) is unit


@pytest.mark.unit
@pytest.mark.parametrize(
    "reserved", sorted(RESERVED_BUCKET_NAMES), ids=sorted(RESERVED_BUCKET_NAMES)
)
def test_dimension__reserved_bucket_name_as_a_category__is_refused(reserved: str) -> None:
    """ADR 0023 Decision 4 says a rule file cannot name a distribution bucket; make it true.

    Unenforced, the failure is silent rather than loud: a dimension
    declaring `uncoded` puts records the rules genuinely coded and records
    nobody looked at into one integer, and because the total still equals
    `|C|` the counting guard never fires. A wrong number that survives the
    check written to catch wrong numbers.
    """
    with pytest.raises(ValidationError, match="reserved distribution bucket name"):
        Dimension(id="d", multi_label=False, categories=[reserved, "real"])
