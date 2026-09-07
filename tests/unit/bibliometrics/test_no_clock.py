"""No module under ``bibliometrics/`` may read the wall clock (ADR 0022 Decision 2).

``datetime.now()`` is the obvious implementation of "is the final year
partial" and is a Stage 11 reproducibility defect that passes every local
test -- see ADR 0022's own worked example. A source-scan test is what
catches it, because the defect is invisible to any test that runs on one
machine at one moment: this project has already shipped three defects of
exactly this class (CLAUDE.md, "watch for machine-dependence").

The scan itself lives in ``tests/no_clock_scan.py`` -- shared with Stage 8's
identical guard over ``taxonomy/``'s pure modules
(``tests/unit/taxonomy/test_no_clock.py``), so the two packages' "no wall
clock" requirements are checked by one definition, not two that could drift.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import prismabib.bibliometrics as bibliometrics_package
from tests.no_clock_scan import PERMITTED_SNIPPETS, PLANTED_VIOLATIONS, clock_calls


def _bibliometrics_source_files() -> list[Path]:
    """Every ``.py`` file under the installed ``prismabib.bibliometrics`` package."""
    package_dir = Path(bibliometrics_package.__file__).parent
    return sorted(package_dir.rglob("*.py"))


@pytest.mark.unit
@pytest.mark.parametrize("path", _bibliometrics_source_files(), ids=lambda path: path.name)
def test_bibliometrics_module__source__never_calls_the_wall_clock(path: Path) -> None:
    """Scans one ``bibliometrics/`` source file's AST for a forbidden clock call.

    Parametrised over every file rather than looped in one test body (no
    ``for``-with-branching in a test, BUILD_PLAN §3.7.3): a violation in a
    single file fails that file's own test node instead of being buried
    inside one aggregate pass/fail.
    """
    calls = clock_calls(path.read_text(encoding="utf-8"), path)
    assert not calls, f"{path} calls the wall clock: {calls} -- see ADR 0022 Decision 2"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("label", "source"), PLANTED_VIOLATIONS, ids=[label for label, _ in PLANTED_VIOLATIONS]
)
def test_no_clock_scan__detects_a_planted_violation(label: str, source: str) -> None:
    """The scanner is not vacuous: every spelling that reads a clock must be caught.

    Without this table, an AST rule that matched only one spelling would
    make every file above pass for the wrong reason -- which is exactly what
    happened: a dotted-suffix match let `datetime.utcnow()` and
    `datetime.today()` straight through.
    """
    assert clock_calls(source, Path("<planted>")), label


@pytest.mark.unit
@pytest.mark.parametrize(
    "source", PERMITTED_SNIPPETS, ids=["annotation", "construction", "timedelta"]
)
def test_no_clock_scan__permits_constructing_and_annotating_a_datetime(source: str) -> None:
    """The rule bounds *reading the clock*, not the `datetime` type itself.

    A scan that also rejected `from datetime import datetime` would be
    unusable -- `base.py` annotates `Provenance.retrieved_at` with it -- and
    an unusable rule gets loosened rather than obeyed.
    """
    assert clock_calls(source, Path("<planted>")) == []
