"""The weekly mutation scope is the prisma package only (ADR 0045).

The workflow cannot run here, so this reads `[tool.mutmut]` and the workflow header as data,
as `test_update_behind_workflow.py` does. It shows the files say the right thing; only a
manual run of the workflow shows it works.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

_ROOT = Path(__file__).parent.parent.parent
_WF = _ROOT / ".github" / "workflows" / "weekly-mutation.yml"
# ADR 0029 moved the append-only log out of prisma/; ADR 0010's note keeps it in scope.
_MOVED_FROM_PRISMA = "src/prismabib/log.py"


def _header() -> str:
    lines = _WF.read_text(encoding="utf-8").splitlines()
    header = []
    for line in lines:
        if not line.startswith("#"):
            break
        header.append(line)
    return " ".join(ln.lstrip("# ") for ln in header)


def test_mutmut_scope__in_configuration__is_the_prisma_package_only() -> None:
    cfg = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mutmut"]
    for entry in cfg["only_mutate"]:
        assert entry == _MOVED_FROM_PRISMA or entry.startswith("src/prismabib/prisma/")
    assert "src/prismabib/prisma/*" in cfg["only_mutate"]
    assert not [e for e in cfg["only_mutate"] if "taxonomy" in e]
    assert not [t for t in cfg["pytest_add_cli_args_test_selection"] if "taxonomy" in t]


def test_mutmut_scope__in_workflow_header__states_prisma_only_and_names_no_taxonomy() -> None:
    header = _header()
    assert "prisma package only" in header
    assert "taxonomy" not in header.lower()
