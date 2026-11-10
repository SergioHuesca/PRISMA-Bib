"""What New Project and the stepper decide, without widgets (ticket #324)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.errors import ValidationError
from prismabib.gui import layer
from prismabib.gui.new_project_model import (
    derive_slug,
    folder_refusal,
    mode_for_choice,
    stepper_rows,
)
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR


@pytest.fixture(autouse=True)
def _user_file(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path_factory.mktemp("u") / "settings.env"))


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Baseball CV 2026", "baseball-cv-2026"),
        ("  Café -- Étude!  ", "cafe-etude"),
        ("../../evil", "evil"),
        ("a/b\\c", "a-b-c"),
    ],
)
def test_derive_slug__name__is_a_safe_folder_name(name: str, slug: str) -> None:
    assert derive_slug(name) == slug


@pytest.mark.parametrize("name", ["", "   ", "!!!"])
def test_derive_slug__nothing_usable__refused(name: str) -> None:
    with pytest.raises(ValidationError):
        derive_slug(name)


def test_folder_refusal__existing_folder__asks_for_another_name(tmp_path: Path) -> None:
    (tmp_path / "demo").mkdir()
    refusal = folder_refusal("Demo", tmp_path)
    assert refusal is not None and "demo" in refusal and "another name" in refusal
    assert folder_refusal("Other", tmp_path) is None


@pytest.mark.parametrize(
    ("choice", "fill", "mode"),
    [
        ("Yes", True, "scopus+openalex"),
        ("Yes", False, "scopus"),
        ("No", True, "openalex"),
        ("Not sure", False, "openalex"),
    ],
)
def test_mode_for_choice__answer__matches_the_cli_rule(choice: str, fill: bool, mode: str) -> None:
    assert mode_for_choice(choice, fill_gaps=fill) == mode


def test_mode_for_choice__no_answer__refused() -> None:
    with pytest.raises(ValidationError):
        mode_for_choice(None, fill_gaps=True)


def test_stepper_rows__new_project__resumes_at_the_query(tmp_path: Path) -> None:
    project = layer.create_project(
        "demo", title="Demo", root=tmp_path, source_mode="openalex", reviewer="Ada"
    )
    rows = stepper_rows(project)
    assert [r.key for r in rows] == list(layer.STEPS)
    assert [r.key for r in rows if r.current] == ["query"]
    assert [r.key for r in rows if r.done] == ["source_mode"]
