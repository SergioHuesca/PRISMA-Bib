"""Static invariants of .github/workflows/nightly.yml's live-contract failure alert (#364).

The workflow cannot run here, so this reads it as data. A job-level ``permissions`` block
*replaces* the workflow-level one, so the ``live`` job's issue steps need ``issues: write`` on
the job itself; the workflow header alone does not reach them (run 43844563299 died with
"Resource not accessible by integration (createIssue)").
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

_WF = Path(__file__).parent.parent.parent / ".github" / "workflows" / "nightly.yml"


def _load() -> dict[Any, Any]:
    wf: dict[Any, Any] = yaml.safe_load(_WF.read_text(encoding="utf-8"))
    return wf


def test_nightly__live_job_can_write_issues() -> None:
    job = _load()["jobs"]["live"]
    # Job-level permissions override the workflow's: only this block counts for the live job.
    assert job["permissions"].get("issues") == "write"


def test_nightly__issue_steps_run_in_the_live_job() -> None:
    steps = _load()["jobs"]["live"]["steps"]
    issue_runs = [s["run"] for s in steps if "gh issue create" in s.get("run", "")]
    assert len(issue_runs) == 1


def test_nightly__forced_failure_is_manual_dispatch_only_and_off_by_default() -> None:
    wf = _load()
    on = wf.get("on", wf.get(True))
    force = on["workflow_dispatch"]["inputs"]["force_failure"]
    assert force["default"] is False
    steps = wf["jobs"]["live"]["steps"]
    forced = [s for s in steps if s.get("run", "").strip() == "exit 1"]
    assert len(forced) == 1
    cond = forced[0]["if"]
    assert "github.event_name == 'workflow_dispatch'" in cond
    assert "inputs.force_failure" in cond
    # It must come before the alert steps, so their `failure()` sees it.
    names = [s.get("name") for s in steps]
    assert names.index(forced[0]["name"]) < names.index(
        "Check for existing open live-contract issue"
    )
