"""``init`` and ``doctor`` tell an OpenAlex project the OpenAlex next steps (ADR 0042, PR 5).

Asserts on our own output lines only, never on Rich-rendered help.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from prismabib.cli import app

pytestmark = pytest.mark.integration

runner = CliRunner()


@pytest.fixture(autouse=True)
def _no_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no real .env
    for name in ("OPENALEX_API_KEY", "SCOPUS_API_KEY"):
        monkeypatch.delenv(name, raising=False)


def init(tmp_path: Path, mode: str, slug: str = "demo") -> str:
    result = runner.invoke(
        app, ["init", slug, "--sources", mode, "--actor", "t", "--root", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    return result.stdout


def test_init__mode_openalex__prints_the_openalex_next_steps(tmp_path: Path) -> None:
    out = init(tmp_path, "openalex")

    assert "`prismabib openalex-search` cannot run until you edit these two files" in out
    assert "[openalex_query]" in out
    assert "prismabib credentials set openalex" in out  # the key is missing
    assert "prismabib openalex-search demo --budget 30 --dry-run" in out
    assert "prismabib build demo --rebuild" in out
    assert "Scopus quota" not in out
    assert "prismabib search demo" not in out
    assert "[query] terms" not in out


def test_init__mode_openalex_with_key__does_not_ask_for_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "dummy-sentinel")  # pragma: allowlist secret

    out = init(tmp_path, "openalex")

    assert "credentials set openalex" not in out
    assert "dummy-sentinel" not in out  # pragma: allowlist secret
    assert "prismabib openalex-search demo --budget 30 --dry-run" in out


def test_init__reusing_an_openalex_project__prints_the_openalex_steps(tmp_path: Path) -> None:
    init(tmp_path, "openalex")

    out = init(tmp_path, "openalex")

    assert "Reused project" in out
    assert "[openalex_query]" in out
    assert "prismabib openalex-search demo --budget 30 --dry-run" in out
    assert "Scopus quota" not in out


@pytest.mark.parametrize("mode", ["scopus", "scopus+openalex"])
def test_init__scopus_modes__keep_the_scopus_next_steps(tmp_path: Path, mode: str) -> None:
    out = init(tmp_path, mode)

    assert "`prismabib search` cannot run until you edit these two files, in this order:" in out
    assert "Fill in [query] terms / compound_terms" in out
    assert "prismabib search demo   # spends Scopus quota; resumable" in out
    assert "openalex-search" not in out
    assert "[openalex_query]" not in out


def test_doctor__mode_openalex__points_to_openalex_search(tmp_path: Path) -> None:
    init(tmp_path, "openalex")

    out = runner.invoke(app, ["doctor", "demo", "--root", str(tmp_path)]).stdout

    assert "no capture command" not in out
    assert "prismabib credentials set openalex" in out
    assert "next: edit [openalex_query] search in project.toml, then" in out
    assert "prismabib openalex-search demo --budget 30 --dry-run" in out
    assert "prismabib search demo" not in out


def test_doctor__scopus_mode__still_points_to_search(tmp_path: Path) -> None:
    init(tmp_path, "scopus")

    out = runner.invoke(app, ["doctor", "demo", "--root", str(tmp_path)]).stdout

    assert "next: prismabib search demo" in out
    assert (
        "prismabib openalex-search" not in out
    )  # the mode lists it as refused, not as a next step


def test_sources_set_openalex__hints_the_query_section_and_other_modes_do_not(
    tmp_path: Path,
) -> None:
    init(tmp_path, "scopus")

    to_oa = runner.invoke(
        app, ["sources", "demo", "--set", "openalex", "--actor", "t", "--root", str(tmp_path)]
    )
    back = runner.invoke(
        app, ["sources", "demo", "--set", "scopus", "--actor", "t", "--root", str(tmp_path)]
    )

    assert to_oa.exit_code == 0, to_oa.output
    assert "next: add an [openalex_query] section" in to_oa.stdout
    assert "openalex-search demo --budget 30 --dry-run" in to_oa.stdout
    assert "[openalex_query]" not in back.stdout
