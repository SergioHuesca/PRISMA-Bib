"""Integration tests for ``build_query_for_project`` (BUILD_PLAN §3.1, lines 318-340).

``build_query_for_project`` reads ``project.toml`` from disk, so it is an
integration test rather than a unit one.

Why this file exists at all: ``capture_search`` calls this function whenever
``query is None``, which is the ordinary path, and it was previously executed by
no test. That matters more here than coverage arithmetic usually does. This
module already shipped a defect that rendered a *silently wrong* Boolean query --
given §3.1's real ``compound_terms = [{ all = [...] }]`` it emitted
``TITLE-ABS-KEY("all")`` and raised nothing. A wrong query produces a wrong
corpus, and every count, figure, and cited number downstream is then wrong with
no signal anywhere. BUILD_PLAN §1.4 names that failure class as the reason this
architecture exists.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.errors import ConfigError, ValidationError
from prismabib.project import Project
from prismabib.query import build_query_for_project

# BUILD_PLAN §3.1 lines 327-336, verbatim.
_SPEC_QUERY_TABLE = """
[query]
terms = [
  "video anomaly detection",
  "surveillance anomaly detection",
]
compound_terms = [
  { all = ["abnormal event detection", "video"] },
]
fields = ["TITLE-ABS-KEY"]
"""

# BUILD_PLAN line 776, verbatim.
_SPEC_QUERY_STRING = (
    'TITLE-ABS-KEY("video anomaly detection") '
    'OR TITLE-ABS-KEY("surveillance anomaly detection") '
    'OR (TITLE-ABS-KEY("abnormal event detection") AND TITLE-ABS-KEY("video"))'
)


def _project_with_query(tmp_path: Path, query_table: str) -> Project:
    """Create a project whose ``project.toml`` carries ``query_table``."""
    project = Project.init("demo", title="Demo", root=tmp_path)
    toml_path = project.root / "project.toml"
    body = toml_path.read_text(encoding="utf-8").split("[query]")[0]
    toml_path.write_text(body + query_table.lstrip("\n"), encoding="utf-8")
    return project


@pytest.mark.integration
def test_build_query_for_project__spec_example__renders_the_frozen_string(
    tmp_path: Path,
) -> None:
    project = _project_with_query(tmp_path, _SPEC_QUERY_TABLE)

    rendered = build_query_for_project(project)

    assert rendered == _SPEC_QUERY_STRING


@pytest.mark.integration
def test_build_query_for_project__compound_group__renders_as_an_and_group(
    tmp_path: Path,
) -> None:
    """The production path through the bare-sequence branch of the coercer.

    ``build_query_for_project`` passes ``[group.all for group in ...]`` -- plain
    lists of strings, not the ``{"all": [...]}`` mappings that appear in the TOML.
    So production always takes the sequence branch while a unit test passing the
    mapping form exercises the other one. Both need covering; this is the half
    that actually runs.
    """
    project = _project_with_query(
        tmp_path,
        '[query]\nterms = []\ncompound_terms = [{ all = ["a", "b", "c"] }]\nfields = ["TITLE-ABS-KEY"]\n',
    )

    rendered = build_query_for_project(project)

    assert rendered == ('(TITLE-ABS-KEY("a") AND TITLE-ABS-KEY("b") AND TITLE-ABS-KEY("c"))')


@pytest.mark.integration
def test_build_query_for_project__no_query_table__raises_config_error(
    tmp_path: Path,
) -> None:
    project = Project.init("demo", title="Demo", root=tmp_path)
    toml_path = project.root / "project.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8").split("[query]")[0], encoding="utf-8"
    )

    with pytest.raises(ConfigError) as excinfo:
        build_query_for_project(project)

    assert "[query]" in str(excinfo.value)


@pytest.mark.integration
def test_build_query_for_project__empty_terms__raises_rather_than_matching_everything(
    tmp_path: Path,
) -> None:
    """An empty query must fail loudly, not quietly return the whole database.

    A freshly scaffolded ``project.toml`` has empty ``terms``. Rendering that as
    an empty string would send an unbounded search and silently define the corpus
    as "everything", which no reviewer would ever catch from the output.
    """
    project = _project_with_query(
        tmp_path,
        '[query]\nterms = []\ncompound_terms = []\nfields = ["TITLE-ABS-KEY"]\n',
    )

    with pytest.raises(ValidationError):
        build_query_for_project(project)


@pytest.mark.integration
def test_build_query_for_project__malformed_toml__raises_config_error_naming_the_path(
    tmp_path: Path,
) -> None:
    project = Project.init("demo", title="Demo", root=tmp_path)
    (project.root / "project.toml").write_text("[query\nterms = [", encoding="utf-8")

    with pytest.raises(ConfigError) as excinfo:
        build_query_for_project(project)

    assert "project.toml" in str(excinfo.value)


@pytest.mark.integration
def test_build_query_for_project__unknown_compound_key__raises_config_error(
    tmp_path: Path,
) -> None:
    """``{any = [...]}`` must be rejected, never silently dropped.

    Dropping it would narrow the corpus without a word of warning -- the operator
    would get a smaller result set and no reason to doubt it.
    """
    project = _project_with_query(
        tmp_path,
        '[query]\nterms = ["x"]\ncompound_terms = [{ any = ["a", "b"] }]\nfields = ["TITLE-ABS-KEY"]\n',
    )

    with pytest.raises(ConfigError):
        build_query_for_project(project)


@pytest.mark.integration
def test_build_query_for_project__missing_project_toml__raises_config_error(
    tmp_path: Path,
) -> None:
    """A project directory without its ``project.toml`` names the expected path."""
    project = Project.init("demo", title="Demo", root=tmp_path)
    (project.root / "project.toml").unlink()

    with pytest.raises(ConfigError) as excinfo:
        build_query_for_project(project)

    assert str(project.root / "project.toml") in str(excinfo.value)
