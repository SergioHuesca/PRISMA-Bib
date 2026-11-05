"""First-run wording fixes for mode ``openalex`` (templates, build summary, help text)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prismabib.cli import _print_store_stats, app
from prismabib.project import Project
from prismabib.store.load import build_store

pytestmark = pytest.mark.integration

runner = CliRunner()


def _init(tmp_path: Path, mode: str) -> Path:
    result = runner.invoke(
        app, ["init", "demo", "--sources", mode, "--actor", "t", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    return tmp_path / "demo"


def test_init__mode_openalex__project_toml_says_query_table_is_unused(tmp_path: Path) -> None:
    text = (_init(tmp_path, "openalex") / "project.toml").read_text(encoding="utf-8")

    assert "Fill these in before running" not in text
    assert "NOT used in this mode" in text


def test_init__mode_openalex__criteria_comments_are_accurate(tmp_path: Path) -> None:
    text = (_init(tmp_path, "openalex") / "criteria.yaml").read_text(encoding="utf-8")

    assert "NOT YET ENFORCEABLE" not in text
    assert "Scopus's own language string" not in text
    assert "primary-topic" in text
    assert "never carry a language" in text


@pytest.mark.parametrize("mode", ["scopus", "scopus+openalex"])
def test_init__scopus_modes__keep_scopus_wording(tmp_path: Path, mode: str) -> None:
    root = _init(tmp_path, mode)

    assert "NOT YET ENFORCEABLE" in (root / "criteria.yaml").read_text(encoding="utf-8")
    assert "Fill these in before running" in (root / "project.toml").read_text(encoding="utf-8")


def test_print_store_stats__openalex_works__labels_them_and_omits_venues(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = Project.init("demo", title="t", root=tmp_path)
    stats = build_store(project, rebuild=True)
    _print_store_stats(
        stats.model_copy(update={"openalex_records_loaded": 108}),
        slug="demo",
        db_path=project.db_path,
    )
    out = capsys.readouterr().out

    assert "records (OpenAlex works)" in out
    assert "108" in out
    assert "  venues " not in out


def test_openalex_search__help__first_line_keeps_bracketed_table_name() -> None:
    result = runner.invoke(app, ["openalex-search", "--help"], env={"COLUMNS": "200"})

    plain = re.sub(chr(27) + r"\[[0-9;]*m", "", result.output)
    assert "for the [openalex_query] search of a project" in plain
