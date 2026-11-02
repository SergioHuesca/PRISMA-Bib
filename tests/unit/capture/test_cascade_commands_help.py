"""``prismabib cascade --help`` states that the Scopus budget cap is approximate."""

from __future__ import annotations

import re

import pytest
from typer.testing import CliRunner

from prismabib.cli import app


@pytest.mark.unit
def test_cascade_help__scopus_budget__says_approximate() -> None:
    out = CliRunner().invoke(app, ["cascade", "--help"], env={"COLUMNS": "200"}).output
    flat = re.sub(r"[\s│|]+", " ", out)
    assert "--scopus-budget" in flat
    assert "approximate" in flat
