"""Integration tests for ``src/prismabib/project.py`` (BUILD_PLAN Stage 1 Tests table, lines 731-744).

Real filesystem, real temp directories -- no network, no mocking of
``prismabib.*`` internals (§3.7.2/§3.7.3 rule 1). ``root=`` is always passed
explicitly so these never touch ``PRISMABIB_PROJECTS_ROOT``/``Settings``.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from prismabib.errors import ConfigError
from prismabib.project import Project

_SKELETON_RELATIVE_PATHS = (
    "raw",
    "store",
    "decisions",
    "taxonomy/rules",
    "fulltext",
    "exports",
    "project.toml",
    "criteria.yaml",
    "decisions/decisions.jsonl",
)


@pytest.mark.integration
@pytest.mark.acceptance("S01-AC1")
def test_project_init__fresh_dir__creates_full_skeleton(tmp_path: Path) -> None:
    project = Project.init("demo", title="Demo Project", root=tmp_path)

    expected_paths = [project.root / relative for relative in _SKELETON_RELATIVE_PATHS]

    assert all(path.exists() for path in expected_paths)


@pytest.mark.integration
@pytest.mark.acceptance("S01-AC1")
def test_project_init__called_twice__is_idempotent(tmp_path: Path) -> None:
    sentinel_criteria = "version: 9.9.9\ncustom: marker\n"
    first = Project.init("demo", title="Demo Project", root=tmp_path)
    (first.root / "criteria.yaml").write_text(sentinel_criteria, encoding="utf-8")

    second = Project.init("demo", title="A Different Title", root=tmp_path)

    assert second.root == first.root
    assert list(tmp_path.iterdir()) == [first.root]
    assert (second.root / "criteria.yaml").read_text(encoding="utf-8") == sentinel_criteria


@pytest.mark.integration
def test_project_open__missing_project__raises_config_error(tmp_path: Path) -> None:
    expected_path = tmp_path / "ghost"

    with pytest.raises(ConfigError, match=re.escape(str(expected_path))):
        Project.open("ghost", root=tmp_path)

    assert not expected_path.exists()
