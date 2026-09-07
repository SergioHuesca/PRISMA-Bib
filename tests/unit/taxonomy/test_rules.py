"""Unit tests for :mod:`prismabib.taxonomy.rules` (BUILD_PLAN §Stage 8, ADR 0023 Decision 6).

Every test here asserts a load-time *validation* rule -- a malformed regex,
an unknown field, an undeclared category, a missing version -- never that a
particular pattern matches a particular string (§3.7.3, ADR 0023 Decision 7:
"no test asserts that a particular regex matches a particular string").
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from prismabib.errors import ConfigError
from prismabib.taxonomy.rules import load_rule_file
from prismabib.taxonomy.schema import TaxonomySchema

_SCHEMA = TaxonomySchema.model_validate(
    {
        "dimensions": [
            {
                "id": "learning_paradigm",
                "multi_label": False,
                "categories": ["supervised", "unsupervised"],
            }
        ]
    }
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


@pytest.mark.unit
@pytest.mark.acceptance("S08-AC1")
def test_rules__malformed_regex__raises_at_load_not_at_match(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "learning_paradigm.yaml",
        """\
version: 1.0.0
dimension: learning_paradigm
counting_unit: papers
categories:
  - id: supervised
    any:
      - {field: title, pattern: '(unclosed'}
""",
    )

    with pytest.raises(ConfigError, match="not a valid regular expression"):
        load_rule_file(path, schema=_SCHEMA)


@pytest.mark.unit
def test_rules__unknown_field_reference__raises_at_load(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "learning_paradigm.yaml",
        """\
version: 1.0.0
dimension: learning_paradigm
counting_unit: papers
categories:
  - id: supervised
    any:
      - {field: abstrct, pattern: 'x'}
""",
    )

    with pytest.raises(ConfigError, match="does not satisfy the taxonomy rule-file schema"):
        load_rule_file(path, schema=_SCHEMA)


@pytest.mark.unit
def test_rules__category_not_in_dimension_schema__raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "learning_paradigm.yaml",
        """\
version: 1.0.0
dimension: learning_paradigm
counting_unit: papers
categories:
  - id: not_a_declared_category
    any:
      - {field: title, pattern: 'x'}
""",
    )

    with pytest.raises(ConfigError, match="not declared for dimension"):
        load_rule_file(path, schema=_SCHEMA)


@pytest.mark.unit
def test_rules__missing_version__raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "learning_paradigm.yaml",
        """\
dimension: learning_paradigm
counting_unit: papers
categories:
  - id: supervised
    any:
      - {field: title, pattern: 'x'}
""",
    )

    with pytest.raises(ConfigError, match="does not satisfy the taxonomy rule-file schema"):
        load_rule_file(path, schema=_SCHEMA)


@pytest.mark.unit
def test_rules__missing_counting_unit__raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "learning_paradigm.yaml",
        """\
version: 1.0.0
dimension: learning_paradigm
categories:
  - id: supervised
    any:
      - {field: title, pattern: 'x'}
""",
    )

    with pytest.raises(ConfigError, match="does not satisfy the taxonomy rule-file schema"):
        load_rule_file(path, schema=_SCHEMA)


@pytest.mark.unit
def test_rules__dimension_not_declared_by_schema__raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "no_such_dimension.yaml",
        """\
version: 1.0.0
dimension: no_such_dimension
counting_unit: papers
categories:
  - id: supervised
    any:
      - {field: title, pattern: 'x'}
""",
    )

    with pytest.raises(ConfigError, match="is not declared in this project's"):
        load_rule_file(path, schema=_SCHEMA)


@pytest.mark.unit
def test_rules__unknown_top_level_key__raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "learning_paradigm.yaml",
        """\
version: 1.0.0
dimension: learning_paradigm
counting_unit: papers
unexpected_key: true
categories:
  - id: supervised
    any:
      - {field: title, pattern: 'x'}
""",
    )

    with pytest.raises(ConfigError, match="does not satisfy the taxonomy rule-file schema"):
        load_rule_file(path, schema=_SCHEMA)


@pytest.mark.unit
def test_rules__file_not_found__raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_rule_file(tmp_path / "missing.yaml", schema=_SCHEMA)


@pytest.mark.unit
def test_rules__category_with_no_any_clause__raises(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "learning_paradigm.yaml",
        """\
version: 1.0.0
dimension: learning_paradigm
counting_unit: papers
categories:
  - id: supervised
    any: []
""",
    )

    with pytest.raises(ConfigError, match="does not satisfy the taxonomy rule-file schema"):
        load_rule_file(path, schema=_SCHEMA)


@pytest.mark.unit
def test_rules__valid_file__compiles_every_pattern(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "learning_paradigm.yaml",
        """\
version: 1.2.3
dimension: learning_paradigm
counting_unit: papers
categories:
  - id: supervised
    any:
      - {field: title, pattern: 'SupervisedMarker'}
    none:
      - {field: abstract, pattern: 'SupervisedMarkerExcluded'}
    confidence: 0.8
""",
    )

    compiled = load_rule_file(path, schema=_SCHEMA)

    # Asserts that load-time *compilation* happened -- a real `re.Pattern`
    # object, with the source text preserved -- not that any pattern matches
    # any string (ADR 0023 Decision 7 forbids that kind of assertion; rule
    # *content* is validated by the audit sample, not by this suite).
    assert compiled.version == "1.2.3"
    assert compiled.dimension == "learning_paradigm"
    assert compiled.category_ids() == frozenset({"supervised"})
    assert compiled.categories[0].confidence == 0.8
    any_pattern = compiled.categories[0].any[0]
    none_pattern = compiled.categories[0].none[0]
    assert isinstance(any_pattern.pattern, re.Pattern)
    assert (any_pattern.field, any_pattern.pattern.pattern) == ("title", "SupervisedMarker")
    assert (none_pattern.field, none_pattern.pattern.pattern) == (
        "abstract",
        "SupervisedMarkerExcluded",
    )
