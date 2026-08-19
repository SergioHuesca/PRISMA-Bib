"""Integration tests for ``src/prismabib/project.py`` (BUILD_PLAN Stage 1 Tests table, lines 731-744).

Real filesystem, real temp directories -- no network, no mocking of
``prismabib.*`` internals (§3.7.2/§3.7.3 rule 1). ``root=`` is always passed
explicitly so these never touch ``PRISMABIB_PROJECTS_ROOT``/``Settings``,
*except* the ``_resolve_projects_root`` tests below, whose entire point is
that resolution path -- there ``monkeypatch`` controls the process
environment ``Settings`` reads from, which is not a ``prismabib.*``
internal.

Coverage follow-up (orchestrator correction): BUILD_PLAN lines 706-722 freeze
``raw_dir``/``db_path``/``decisions_path``/``criteria``/``open`` as
``Project``'s public contract, and ``_resolve_projects_root``/``_read_toml``
are load-bearing private helpers with no prior test. All were previously
0%-covered; none of the tests below merely execute a line -- each pins a
behaviour a later stage depends on (Stage 3's store path, Stage 4's decision
log, an amended ``criteria.yaml`` mid-session).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from prismabib.errors import ConfigError
from prismabib.project import (
    Criteria,
    DocTypeCriteria,
    ManualScreeningCriteria,
    Project,
    TemporalCriteria,
    _read_toml,
    _resolve_projects_root,
)

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

# BUILD_PLAN §3.1 lines 342-366, transcribed verbatim as the fixture body the
# orchestrator's correction asks for.
_CRITERIA_YAML_TEXT = """\
version: 1.0.0
temporal:
  year_start: 2016
  year_end: 2026
subject_areas: [COMP, ENGI]
doc_types:
  include: [ar, cp]
  conference_whitelist: [CVPR, ICCV, ECCV, WACV, AAAI]
languages: [English]
manual_abstract:
  exclude_reason_codes:
    - REVIEW_OR_SURVEY
    - BIBLIOMETRIC_STUDY
    - NOT_VIDEO_BASED
    - NOT_PRIMARY_RESEARCH
    - VENUE_NOT_ELIGIBLE
    - OFF_TOPIC
manual_fulltext:
  exclude_reason_codes:
    - INACCESSIBLE
    - NOT_PRIMARY_RESEARCH
"""


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


@pytest.mark.integration
def test_project_open__existing_project__returns_matching_project(tmp_path: Path) -> None:
    Project.init("demo", title="Demo Project", root=tmp_path)

    opened = Project.open("demo", root=tmp_path)

    assert (opened.slug, opened.root) == ("demo", tmp_path / "demo")


@pytest.mark.integration
def test_project_open__missing_project_toml__raises_config_error(tmp_path: Path) -> None:
    project_dir = tmp_path / "demo"
    project_dir.mkdir()
    expected_path = project_dir / "project.toml"

    with pytest.raises(ConfigError, match=re.escape(str(expected_path))):
        Project.open("demo", root=tmp_path)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("accessor", "relative_path"),
    [
        (lambda project: project.raw_dir, "raw"),
        (lambda project: project.db_path, "store/corpus.duckdb"),
        (lambda project: project.decisions_path, "decisions/decisions.jsonl"),
    ],
    ids=["raw_dir", "db_path", "decisions_path"],
)
def test_project__path_properties__resolve_under_root(
    tmp_path: Path, accessor: Any, relative_path: str
) -> None:
    project = Project.init("demo", title="Demo Project", root=tmp_path)

    resolved = accessor(project)

    assert resolved == project.root / relative_path


@pytest.mark.integration
def test_project__criteria__parses_real_criteria_yaml(tmp_path: Path) -> None:
    project = Project.init("demo", title="Demo Project", root=tmp_path)
    (project.root / "criteria.yaml").write_text(_CRITERIA_YAML_TEXT, encoding="utf-8")

    expected = Criteria(
        version="1.0.0",
        temporal=TemporalCriteria(year_start=2016, year_end=2026),
        subject_areas=["COMP", "ENGI"],
        doc_types=DocTypeCriteria(
            include=["ar", "cp"],
            conference_whitelist=["CVPR", "ICCV", "ECCV", "WACV", "AAAI"],
        ),
        languages=["English"],
        manual_abstract=ManualScreeningCriteria(
            exclude_reason_codes=[
                "REVIEW_OR_SURVEY",
                "BIBLIOMETRIC_STUDY",
                "NOT_VIDEO_BASED",
                "NOT_PRIMARY_RESEARCH",
                "VENUE_NOT_ELIGIBLE",
                "OFF_TOPIC",
            ]
        ),
        manual_fulltext=ManualScreeningCriteria(
            exclude_reason_codes=["INACCESSIBLE", "NOT_PRIMARY_RESEARCH"]
        ),
    )

    assert project.criteria == expected


@pytest.mark.integration
def test_project__criteria__amended_on_disk__is_reread_not_cached(tmp_path: Path) -> None:
    project = Project.init("demo", title="Demo Project", root=tmp_path)
    (project.root / "criteria.yaml").write_text(_CRITERIA_YAML_TEXT, encoding="utf-8")

    before = project.criteria.version
    (project.root / "criteria.yaml").write_text(
        _CRITERIA_YAML_TEXT.replace("version: 1.0.0", "version: 2.0.0"), encoding="utf-8"
    )
    after = project.criteria.version

    assert (before, after) == ("1.0.0", "2.0.0")


@pytest.mark.integration
def test_project__criteria__missing_file__raises_config_error(tmp_path: Path) -> None:
    project = Project.init("demo", title="Demo Project", root=tmp_path)
    criteria_path = project.root / "criteria.yaml"
    criteria_path.unlink()

    with pytest.raises(ConfigError, match=re.escape(str(criteria_path))):
        _ = project.criteria


@pytest.mark.integration
def test_project__criteria__malformed_yaml__raises_config_error(tmp_path: Path) -> None:
    project = Project.init("demo", title="Demo Project", root=tmp_path)
    (project.root / "criteria.yaml").write_text("version: [unterminated", encoding="utf-8")

    with pytest.raises(ConfigError, match="not valid YAML"):
        _ = project.criteria


@pytest.mark.integration
def test_project__criteria__invalid_schema__raises_config_error(tmp_path: Path) -> None:
    project = Project.init("demo", title="Demo Project", root=tmp_path)
    (project.root / "criteria.yaml").write_text("version: 1.0.0\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=re.escape("does not satisfy the criteria.yaml schema")):
        _ = project.criteria


@pytest.mark.integration
def test_resolve_projects_root__none__resolves_via_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "irrelevant-for-this-test")
    monkeypatch.setenv("PRISMABIB_PROJECTS_ROOT", str(tmp_path))

    resolved = _resolve_projects_root(None)

    assert resolved == tmp_path


@pytest.mark.integration
def test_resolve_projects_root__explicit_root__overrides_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "irrelevant-for-this-test")
    monkeypatch.setenv("PRISMABIB_PROJECTS_ROOT", str(tmp_path / "from-env"))
    explicit_root = tmp_path / "explicit"

    resolved = _resolve_projects_root(explicit_root)

    assert resolved == explicit_root


@pytest.mark.integration
def test_read_toml__malformed_file__raises_config_error_naming_the_path(tmp_path: Path) -> None:
    malformed_toml = tmp_path / "project.toml"
    malformed_toml.write_text("not = [valid toml", encoding="utf-8")

    with pytest.raises(ConfigError, match=re.escape(str(malformed_toml))):
        _read_toml(malformed_toml)
