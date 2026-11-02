"""CLI tests for ``prismabib openalex`` (ADR 0041 Decision 8): one OpenAlex path, own report."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import typer.main
from typer.testing import CliRunner

from prismabib.capture import cascade_commands
from prismabib.capture.openalex import held
from prismabib.cli import app
from prismabib.project import Project
from tests.integration.capture import test_cascade_commands as _base
from tests.integration.capture.test_cascade_commands import BUILDS, Fake

# The fakes and the project (three incomplete records, one candidate) are shared fixtures.
fakes = _base.fakes
project = _base.project

runner = CliRunner()


def _openalex(root: Path, *args: str) -> tuple[int, str]:
    r = runner.invoke(app, ["openalex", "demo", "--root", str(root), *args])
    return r.exit_code, r.output


@pytest.mark.integration
def test_openalex__run__asks_only_openalex_and_reports_per_field(
    project: Project, fakes: dict[str, Fake]
) -> None:
    code, out = _openalex(project.root.parent)

    assert code == 0, out
    assert fakes["scopus"].requests == []
    assert len(fakes["openalex"].requests) == 1  # one DOI batch for a, b, c
    assert "identifiers: value 3, not_found 0, refused 0" in out
    assert "abstract: value 3, absent 0, not_found 0, refused 0" in out
    assert "keywords: value 3, absent 0, not_found 0, refused 0" in out
    assert "affiliations: value 0, absent 3, not_found 0, refused 0" in out  # fake has none


@pytest.mark.integration
def test_openalex__second_run__creates_no_run_and_refresh_asks_again(
    project: Project, fakes: dict[str, Fake]
) -> None:
    _openalex(project.root.parent)
    runs = sorted((project.raw_dir / "openalex").iterdir())

    code, out = _openalex(project.root.parent)
    assert code == 0, out
    assert sorted((project.raw_dir / "openalex").iterdir()) == runs
    assert len(fakes["openalex"].requests) == 1
    assert "nothing to request" in out

    _openalex(project.root.parent, "--refresh")
    assert len(fakes["openalex"].requests) == 2


@pytest.mark.integration
def test_openalex__dry_run__makes_no_call_and_writes_nothing(
    project: Project, fakes: dict[str, Fake]
) -> None:
    code, out = _openalex(project.root.parent, "--dry-run")

    assert code == 0, out
    assert "3 DOIs" in out
    assert fakes["openalex"].requests == []
    assert not (project.raw_dir / "openalex").exists()


@pytest.mark.integration
def test_openalex__refused_outside_opt_in__is_a_clean_failure(
    project: Project, fakes: dict[str, Fake]
) -> None:
    fakes["openalex"]._handler = lambda r: httpx.Response(403)

    code, out = _openalex(project.root.parent)

    assert code != 0
    assert "Traceback" not in out


@pytest.mark.integration
def test_openalex__does_not_rebuild(project: Project, fakes: dict[str, Fake]) -> None:
    _openalex(project.root.parent)

    assert BUILDS == []


@pytest.mark.unit
def test_openalex__declared_options() -> None:
    command = typer.main.get_command(app).commands["openalex"]  # type: ignore[attr-defined]
    opts = {o for p in command.params for o in p.opts}

    assert {"--opt-in", "--refresh", "--dry-run", "--root"} <= opts


@pytest.mark.unit
def test_held__is_public_and_empty_without_runs(tmp_path: Path) -> None:
    assert held(tmp_path / "nope") == set()
    assert not hasattr(cascade_commands, "_oa_held")
