"""CLI tests for ``candidates add`` (ADR 0041 PR5): a paste writes candidates, never records."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from prismabib.capture.candidates import CandidateLog
from prismabib.cli import app
from prismabib.project import Project

runner = CliRunner()

PASTE = "Title\tYear\nDeep learning for sleep staging\t2021\nAnother study of sleep\t2019\n"


def _add(root: Path, *args: str, input: str | None = None) -> tuple[int, str]:
    result = runner.invoke(
        app,
        ["candidates", "add", "demo", "--reviewer", "sh", "--root", str(root), *args],
        input=input,
    )
    return result.exit_code, result.output


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return Project.init("demo", title="Demo", root=tmp_path)


def _log(project: Project) -> CandidateLog:
    return CandidateLog(project.root / "decisions" / "candidates.jsonl")


@pytest.mark.integration
def test_candidates_add__stdin__writes_candidates_and_no_store(project: Project) -> None:
    code, out = _add(project.root.parent, input=PASTE)

    assert code == 0
    assert "added 2" in out
    assert sorted(v.event.year for v in _log(project).view()) == [2019, 2021]
    assert not project.db_path.exists()  # a paste creates no record


@pytest.mark.integration
def test_candidates_add__file_option__reads_the_file(project: Project, tmp_path: Path) -> None:
    paste = tmp_path / "paste.tsv"
    paste.write_text(PASTE, encoding="utf-8")

    code, out = _add(project.root.parent, "--file", str(paste))

    assert code == 0
    assert "added 2" in out
    assert len(_log(project).view()) == 2


@pytest.mark.integration
def test_candidates_add__repeat_paste__is_already_present(project: Project) -> None:
    _add(project.root.parent, input=PASTE)

    code, out = _add(project.root.parent, input=PASTE)

    assert code == 0
    assert "already present 2" in out
    assert len(_log(project).view()) == 2


@pytest.mark.integration
@pytest.mark.parametrize("text", ["", "  \n\n", "Title\tYear\n"])
def test_candidates_add__no_rows__fails_and_writes_nothing(project: Project, text: str) -> None:
    code, out = _add(project.root.parent, input=text)

    assert code == 1
    assert "ValidationError" in out
    assert "no rows" in out
    assert not (project.root / "decisions" / "candidates.jsonl").exists()


@pytest.mark.integration
def test_candidates_add__bad_row__fails_naming_the_line_and_writes_nothing(
    project: Project,
) -> None:
    code, out = _add(project.root.parent, input="Good title\t2020\nBad title\tabc\n")

    assert code == 1
    assert "line 2" in out
    assert "Traceback" not in out
    assert not (project.root / "decisions" / "candidates.jsonl").exists()


@pytest.mark.integration
def test_candidates_add__missing_file__fails_cleanly(project: Project, tmp_path: Path) -> None:
    code, out = _add(project.root.parent, "--file", str(tmp_path / "nope.tsv"))

    assert code == 1
    assert "Traceback" not in out
