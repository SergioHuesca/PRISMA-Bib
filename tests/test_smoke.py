"""BUILD_PLAN.md line 626: the package must import and expose its version."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import prismabib

PYPROJECT_PATH = Path(__file__).parent.parent / "pyproject.toml"


@pytest.mark.unit
@pytest.mark.acceptance("S00-AC2")
def test_package__imports__exposes_version() -> None:
    with PYPROJECT_PATH.open("rb") as handle:
        pyproject = tomllib.load(handle)

    expected_version = pyproject["project"]["version"]

    assert prismabib.__version__ == expected_version
