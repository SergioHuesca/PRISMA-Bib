"""The clean-install scenario of ``scripts/clean_install_scenario.py`` run under pytest.

A peer without Scopus access must be able to install and run the OpenAlex-only path from
``docs/getting-started.md``. The script is also its own CI job (``clean-install``); this wrapper
makes the ``e2e`` job exercise it too, with every credential variable removed from the child.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "clean_install_scenario.py"
CREDENTIAL_PREFIXES = ("SCOPUS_", "OPENALEX_", "ELSEVIER_", "UNPAYWALL_")


@pytest.mark.e2e
def test_clean_install_scenario__no_credentials__every_step_passes() -> None:
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith(CREDENTIAL_PREFIXES)}
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
        check=False,
        timeout=600,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "FAIL" not in proc.stdout
    assert "clean-install scenario: all steps passed" in proc.stdout
