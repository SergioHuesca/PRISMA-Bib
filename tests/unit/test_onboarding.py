"""The Source mode choice as library functions (GUI spec #313): values in, values out."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.errors import ConfigError, ValidationError
from prismabib.onboarding import (
    create_project,
    next_steps,
    not_sure_notes,
    parse_access_answer,
    source_mode_from_answer,
)
from prismabib.project import Project

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("answer", "fill_gaps", "mode"),
    [
        ("n", True, "openalex"),
        ("No", True, "openalex"),
        ("s", True, "openalex"),
        ("not sure", False, "openalex"),
        ("  NOT   Sure ", True, "openalex"),
        ("y", True, "scopus+openalex"),
        ("yes", True, "scopus+openalex"),
        ("y", False, "scopus"),
        ("YES", False, "scopus"),
    ],
)
def test_source_mode_from_answer__accepted_answer__one_mode(
    answer: str, fill_gaps: bool, mode: str
) -> None:
    assert source_mode_from_answer(answer, fill_gaps=fill_gaps) == mode


@pytest.mark.parametrize("answer", ["", "maybe", "yep", "ye s", "scopus", "1"])
def test_parse_access_answer__anything_else__rejected_with_message(answer: str) -> None:
    with pytest.raises(ValidationError, match="y, n or not sure"):
        parse_access_answer(answer)


def test_parse_access_answer__not_sure__is_its_own_answer() -> None:
    assert parse_access_answer("s") == parse_access_answer("not sure") == "not sure"


def test_create_project__mode_and_reviewer__recorded_without_prompt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = create_project(
        "demo", title="Demo", root=tmp_path, source_mode="scopus", reviewer="Ada"
    )

    assert project.source_mode == "scopus"
    assert Project.open("demo", root=tmp_path).source_mode == "scopus"
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


@pytest.mark.parametrize("reviewer", ["", "   "])
def test_create_project__no_reviewer__refused_and_nothing_written(
    tmp_path: Path, reviewer: str
) -> None:
    with pytest.raises(ConfigError, match="named actor"):
        create_project(
            "demo", title="Demo", root=tmp_path, source_mode="openalex", reviewer=reviewer
        )

    assert not (tmp_path / "demo").exists()


def test_not_sure_notes__names_the_slug() -> None:
    notes = not_sure_notes("demo")

    assert notes[0].startswith("Mode openalex was recorded")
    assert "`prismabib sources demo --set scopus --actor NAME`" in notes[2]


def test_next_steps__openalex_with_missing_key__asks_for_the_key(tmp_path: Path) -> None:
    lines = next_steps("demo", tmp_path / "demo", mode="openalex", existed=False, key_missing=True)

    assert "  prismabib credentials set openalex   # a free OpenAlex key is required" in lines
    assert lines[-1] == "  prismabib flow demo"


def test_next_steps__scopus_mode__searches_scopus(tmp_path: Path) -> None:
    lines = next_steps("demo", tmp_path / "demo", mode="scopus", existed=False, key_missing=True)

    assert "  prismabib search demo   # spends Scopus quota; resumable" in lines
    assert not any("credentials set openalex" in line for line in lines)
