"""The weekly mutation scope and its limits (ADR 0045, issue #363).

The workflow cannot run here, so this reads `[tool.mutmut]` and the workflow as data, as
`test_update_behind_workflow.py` does: `breaches` returns what is wrong, and a table of
deliberate changes must each produce a breach. It shows the files say the right thing; only a
manual run of the workflow shows the narrowing works. Pragma spans are not asserted here.
"""

from __future__ import annotations

import copy
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml

_ROOT = Path(__file__).parent.parent.parent
_WF = _ROOT / ".github" / "workflows" / "weekly-mutation.yml"

# The final decision of #405. Change these together with pyproject.toml and the header.
EXPECTED_SOURCE = ["src/prismabib"]
# Runs 7 and 8 of #405: with taxonomy, or with log.py (which ADR 0029 moved out of prisma/),
# the 60-minute "Mutation run" step was cut off before a kill rate printed.
EXPECTED_SCOPE = ["src/prismabib/prisma/*"]
EXPECTED_SELECTION = [
    "tests/unit/prisma",
    "tests/integration/prisma",
    "tests/property/test_engine_invariants.py",
]
# The bisect (#407) located no single file, so no file is exempt.
EXPECTED_EXEMPT: list[str] = []
EXPECTED_MULTIPLIER = 3
EXPECTED_HEADER = "The scope is the prisma package (ADR 0045)"
JOB_LIMIT_MINUTES = 180
STEP_LIMIT_MINUTES = 60


def _header(text: str) -> str:
    header = []
    for line in text.splitlines():
        if not line.startswith("#"):
            break
        header.append(line)
    return " ".join(ln.lstrip("# ") for ln in header)


def breaches(cfg: dict[str, Any], header: str, workflow: dict[str, Any]) -> list[str]:
    found: list[str] = []
    if cfg.get("source_paths") != EXPECTED_SOURCE:
        found.append("source_paths is not the package root")
    if cfg.get("only_mutate") != EXPECTED_SCOPE:
        found.append("only_mutate is not the final scope")
    if cfg.get("pytest_add_cli_args_test_selection") != EXPECTED_SELECTION:
        found.append("the test selection is not the final selection")
    if cfg.get("do_not_mutate", []) != EXPECTED_EXEMPT:
        found.append("do_not_mutate is not exactly the bisect's file exemptions")
    if cfg.get("timeout_multiplier") != EXPECTED_MULTIPLIER:
        found.append("timeout_multiplier is not the measured value")
    if EXPECTED_HEADER not in header:
        found.append("the header does not state the final scope")
    if "and the log module" in header:
        found.append("the header puts the log module in scope")
    job = workflow["jobs"]["mutation"]
    if job.get("timeout-minutes") != JOB_LIMIT_MINUTES:
        found.append("the job limit is not 180 minutes")
    steps = [s for s in job["steps"] if s.get("name") == "Mutation run"]
    if len(steps) != 1:
        found.append("there is not exactly one 'Mutation run' step")
    elif steps[0].get("timeout-minutes") != STEP_LIMIT_MINUTES:
        found.append("the 'Mutation run' step limit is not 60 minutes")
    return found


def _load() -> tuple[dict[str, Any], str, dict[str, Any]]:
    cfg = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["mutmut"]
    text = _WF.read_text(encoding="utf-8")
    return cfg, _header(text), yaml.safe_load(text)


def _step(workflow: dict[str, Any]) -> dict[str, Any]:
    return next(s for s in workflow["jobs"]["mutation"]["steps"] if s.get("name") == "Mutation run")


def _drop_step_limit(workflow: dict[str, Any]) -> None:
    _step(workflow).pop("timeout-minutes", None)


# Each entry changes the parsed files in place and returns the (possibly changed) header.
MUTATIONS = {
    "wrong source": lambda c, h, w: c.update(source_paths=["src/prismabib/prisma"]) or h,
    "log module in scope": lambda c, h, w: (
        c.update(only_mutate=[*c["only_mutate"], "src/prismabib/log.py"]) or h
    ),
    "wrong scope": lambda c, h, w: c.update(only_mutate=["src/prismabib/log.py"]) or h,
    "taxonomy in scope": lambda c, h, w: (
        c.update(only_mutate=[*c["only_mutate"], "src/prismabib/taxonomy/*"]) or h
    ),
    "wrong selection": lambda c, h, w: (
        c.update(pytest_add_cli_args_test_selection=["tests/unit/prisma"]) or h
    ),
    "changed glob": lambda c, h, w: c.update(do_not_mutate=["src/prismabib/prisma/flow.py"]) or h,
    "changed multiplier": lambda c, h, w: c.update(timeout_multiplier=15) or h,
    "header adds the log module": lambda c, h, w: (
        h + " It also covers the prisma package and the log module."
    ),
    "header drops the scope": lambda c, h, w: h.replace(EXPECTED_HEADER, "something"),
    "changed job limit": lambda c, h, w: (
        w["jobs"]["mutation"].update({"timeout-minutes": 360}) or h
    ),
    "changed step limit": lambda c, h, w: _step(w).update({"timeout-minutes": 90}) or h,
    "no step limit": lambda c, h, w: _drop_step_limit(w) or h,
}


def test_real_files__have_no_breaches() -> None:
    assert breaches(*_load()) == []


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_mutation__is_caught(name: str) -> None:
    cfg, header, workflow = (copy.deepcopy(part) for part in _load())
    header = MUTATIONS[name](cfg, header, workflow)
    assert breaches(cfg, header, workflow), name
