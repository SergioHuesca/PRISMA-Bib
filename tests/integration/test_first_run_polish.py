"""First-run wording fixes for mode ``openalex`` (templates, build summary, help text)."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prismabib.cli import _print_store_stats, app
from prismabib.project import Project, _default_criteria_yaml, _default_project_toml
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
    assert not re.search(r"^  records +[0-9]", out, re.MULTILINE)


def test_openalex_search__help__first_line_keeps_bracketed_table_name() -> None:
    result = runner.invoke(app, ["openalex-search", "--help"], env={"COLUMNS": "200"})

    plain = re.sub(chr(27) + r"\[[0-9;]*m", "", result.output)
    assert "for the [openalex_query] search of a project" in plain


def test_init__next_steps_hint__languages_only_in_scopus_modes(tmp_path: Path) -> None:
    def hint(mode: str, slug: str) -> str:
        out = runner.invoke(
            app, ["init", slug, "--sources", mode, "--actor", "t", "--root", str(tmp_path)]
        ).output
        return " ".join(out.split())

    assert "doc_types.include and languages" not in hint("openalex", "oa")
    assert "doc_types.include" in hint("openalex", "oa2")
    assert "doc_types.include and languages" in hint("scopus", "sc")


def test_print_store_stats__openalex_works__shows_search_runs_not_scopus_runs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = Project.init("demo", title="t", root=tmp_path)
    stats = build_store(project, rebuild=True)
    update = {"openalex_records_loaded": 108, "openalex_search_runs_loaded": 2}
    _print_store_stats(stats.model_copy(update=update), slug="demo", db_path=project.db_path)
    out = capsys.readouterr().out

    assert "search runs loaded" in out
    assert "  runs loaded " not in out
    assert not re.search(r"^  records +[0-9]", out, re.MULTILINE)


_PINS = {
    "criteria": "cd39bb8632126d0c105eebf9dfb086e6ee4cdc27d8fd6be2ceee5caaf73a9167",  # pragma: allowlist secret
    None: "b7666dfec2e8621f2f3f40abc4f279e6fa6d21b29f66886effc5bb86d0f597c9",  # pragma: allowlist secret
    "scopus": "588a01bfb832283e79e7e35b032ab7ed9c4c917643fc7148a8843d6dd51669e9",  # pragma: allowlist secret
    "scopus+openalex": "1acb0a623958af3551b4205543b698a0ff2dcca2061303afbccb9550f90b5b22",  # pragma: allowlist secret
}  # sha256 of main's output, date and year masked


def _sha(text: str) -> str:
    now = datetime.now(UTC)
    masked = text.replace(now.date().isoformat(), "DATE").replace(str(now.year), "YYYY")
    return hashlib.sha256(masked.encode()).hexdigest()


@pytest.mark.parametrize("mode", [None, "scopus", "scopus+openalex"])
def test_templates__non_openalex_modes__are_byte_identical_to_main(mode: str | None) -> None:
    assert _sha(_default_criteria_yaml(mode)) == _PINS["criteria"]
    assert _sha(_default_project_toml("s", "t", mode)) == _PINS[mode]
