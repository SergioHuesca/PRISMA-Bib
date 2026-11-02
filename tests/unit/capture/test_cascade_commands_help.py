"""``prismabib cascade --help`` states that the Scopus budget cap is approximate."""

from __future__ import annotations

import pytest
import typer.main

from prismabib.cli import app


@pytest.mark.unit
def test_cascade_help__scopus_budget__says_approximate() -> None:
    # Asserted on the click parameter, not rendered help: rich output varies with
    # colour/width/env (see tests/integration/test_cli.py).
    command = typer.main.get_command(app).commands["cascade"]  # type: ignore[attr-defined]
    option = next(p for p in command.params if "--scopus-budget" in p.opts)
    assert "approximate" in " ".join((option.help or "").split())
