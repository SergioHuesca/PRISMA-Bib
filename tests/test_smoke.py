"""BUILD_PLAN.md line 626: the package must import and expose its version."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

import prismabib

PYPROJECT_PATH = Path(__file__).parent.parent / "pyproject.toml"


# Deliberately claims no acceptance criterion. It previously claimed S00-AC2
# ("green from a clean clone taken from GitHub"), which it cannot assert: it compares
# __version__ against pyproject.toml and says nothing about a fresh clone installing
# or the suite passing. A criterion claimed by a test that does not check it is the
# §5 risk-12 failure mode -- coverage without assertion -- and it makes
# --acceptance-report lie. S00-AC2 is now claimed by
# tests/live/test_github_governance.py::test_clean_clone__from_github__syncs_and_passes_the_default_suite.
@pytest.mark.unit
def test_package__imports__exposes_version() -> None:
    with PYPROJECT_PATH.open("rb") as handle:
        pyproject = tomllib.load(handle)

    expected_version = pyproject["project"]["version"]

    assert prismabib.__version__ == expected_version
