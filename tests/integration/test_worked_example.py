"""The shipped worked example must actually run (BUILD_PLAN §1.4's standard, applied to docs).

An example that has quietly stopped working is worse than no example: it is
the first thing a new researcher runs, and if it fails they conclude the tool
is broken rather than that the example rotted. It exercises real library API
across four modules, so it breaks on exactly the kind of signature change that
looks harmless in a diff.

Run as a subprocess rather than by importing ``main()``: the example's whole
claim is that ``uv run python examples/worked_example.py`` works from a clean
shell, and importing it would silently supply an interpreter state and a
working directory the reader does not have.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_EXAMPLE = Path(__file__).resolve().parents[2] / "examples" / "worked_example.py"


@pytest.mark.integration
def test_worked_example__runs_clean_without_any_scopus_credential() -> None:
    """The example must run for someone who has not yet obtained an API key.

    That is its entire purpose -- it exists so a researcher can see the
    pipeline work *before* deciding whether to request Scopus access.
    """
    environment = {k: v for k, v in os.environ.items() if k != "SCOPUS_API_KEY"}
    environment["PRISMABIB_PROJECTS_ROOT"] = "./projects"

    result = subprocess.run(
        [sys.executable, str(_EXAMPLE)],
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
        env=environment,
        cwd=_EXAMPLE.parent.parent,
    )

    assert result.returncode == 0, result.stderr
    # The funnel must actually narrow. An example whose automated filter
    # excludes nothing demonstrates the mechanism without exercising it, and
    # would leave a reader unsure whether the filter ran at all.
    assert "identified                      120" in result.stdout
    assert "INCLUDED" in result.stdout
    assert "assert_consistent() passed" in result.stdout
