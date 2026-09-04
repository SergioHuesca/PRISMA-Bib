"""No module under ``bibliometrics/`` may read the wall clock (ADR 0022 Decision 2).

``datetime.now()`` is the obvious implementation of "is the final year
partial" and is a Stage 11 reproducibility defect that passes every local
test -- see ADR 0022's own worked example. A source-scan test is what
catches it, because the defect is invisible to any test that runs on one
machine at one moment: this project has already shipped three defects of
exactly this class (CLAUDE.md, "watch for machine-dependence").
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import prismabib.bibliometrics as bibliometrics_package

#: Every attribute name that reads a clock, matched on the *name* rather
#: than on a dotted suffix.
#:
#: A suffix match cannot work: ``"datetime.today"`` does not end with
#: ``"date.today"`` (the character before is ``e``, not ``.``), so the
#: previous version of this scan let through ``datetime.today()`` and
#: ``datetime.utcnow()`` -- the two most likely alternatives to
#: ``datetime.now()`` for exactly the "which year is it" question ADR 0022
#: Decision 2 forbids. Matching the attribute alone is broader than
#: necessary (it would also flag a hypothetical ``foo.now()``), which is the
#: correct direction to err at a boundary whose job is to bound scope.
_FORBIDDEN_ATTRIBUTES = frozenset(
    {"now", "utcnow", "today", "time", "monotonic", "perf_counter", "time_ns", "monotonic_ns"}
)

#: Modules whose names may not be imported *from*, because ``from datetime
#: import datetime as dt`` then ``dt.now()`` reduces the call to a bare
#: attribute on a local alias that no attribute-name rule can trace back.
#: Flagging the import itself is what closes the aliasing hole -- including
#: ``from time import time``, where the call is a bare ``Name``, not an
#: ``Attribute``, and the walker below would never see it.
_CLOCK_MODULES = frozenset({"datetime", "time"})

#: Names that may be imported from those modules without flagging the
#: import: the *types*, which are needed for annotations and construction
#: (``datetime(2026, 1, 1)``) and cannot themselves read a clock.
_SAFE_IMPORTED_NAMES = frozenset({"datetime", "date", "timedelta", "timezone", "UTC", "tzinfo"})


def _clock_calls(source: str, path: Path) -> list[str]:
    """Every forbidden clock call found in ``source``, as its dotted call text.

    Args:
        source: The module's source text.
        path: Only used to make a parse failure's message actionable.

    Returns:
        One entry per offending call site, e.g. ``"datetime.now"`` or
        ``"datetime.datetime.now"``.
    """
    tree = ast.parse(source, filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        found.extend(_offence(node))
    return found


def _offence(node: ast.AST) -> list[str]:
    """Any clock offence ``node`` itself constitutes, as reportable text.

    Two shapes, because one rule cannot catch both. A *call* on a forbidden
    attribute (``datetime.utcnow()``) is caught by name. An *import* that
    binds a clock function to a local alias (``from time import time``,
    ``from datetime import datetime as dt``) is caught at the import,
    because after it the call site is a bare ``Name`` or an attribute on an
    alias -- neither traceable by walking calls alone.

    Args:
        node: Any AST node.

    Returns:
        Zero or one entry; a list keeps the caller's ``extend`` uniform.
    """
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return [ast.unparse(node.func)] if node.func.attr in _FORBIDDEN_ATTRIBUTES else []
    if isinstance(node, ast.ImportFrom) and node.module in _CLOCK_MODULES:
        return [
            f"from {node.module} import {alias.name}"
            for alias in node.names
            if alias.name not in _SAFE_IMPORTED_NAMES or alias.asname is not None
        ]
    return []


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
    calls = _clock_calls(path.read_text(encoding="utf-8"), path)
    assert not calls, f"{path} calls the wall clock: {calls} -- see ADR 0022 Decision 2"


#: Every spelling that reads a clock, as a source snippet the scan must
#: reject. Parametrised rather than a single planted call: the previous
#: self-test planted only ``datetime.now``, which proved the walker ran but
#: not that the match set was adequate -- and it was not. Rows 2-7 were all
#: passing the scan when this table was written.
_PLANTED_VIOLATIONS = [
    ("datetime.now", "from datetime import datetime\nx = datetime.now().year\n"),
    ("datetime.utcnow", "from datetime import datetime\nx = datetime.utcnow().year\n"),
    ("datetime.today", "from datetime import datetime\nx = datetime.today().year\n"),
    ("date.today", "from datetime import date\nx = date.today().year\n"),
    ("aliased datetime", "from datetime import datetime as dt\nx = dt.now().year\n"),
    ("bare time", "from time import time\nx = time()\n"),
    ("module time.time", "import time\nx = time.time()\n"),
    ("time.monotonic", "import time\nx = time.monotonic()\n"),
    ("time.perf_counter", "import time\nx = time.perf_counter()\n"),
    ("datetime.datetime.now", "import datetime\nx = datetime.datetime.now().year\n"),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("label", "source"), _PLANTED_VIOLATIONS, ids=[label for label, _ in _PLANTED_VIOLATIONS]
)
def test_no_clock_scan__detects_a_planted_violation(label: str, source: str) -> None:
    """The scanner is not vacuous: every spelling that reads a clock must be caught.

    Without this table, an AST rule that matched only one spelling would
    make every file above pass for the wrong reason -- which is exactly what
    happened: a dotted-suffix match let `datetime.utcnow()` and
    `datetime.today()` straight through.
    """
    assert _clock_calls(source, Path("<planted>")), label


@pytest.mark.unit
@pytest.mark.parametrize(
    "source",
    [
        "from datetime import datetime\ndef f(x: datetime) -> datetime:\n    return x\n",
        "from datetime import UTC, datetime\nx = datetime(2026, 1, 1, tzinfo=UTC)\n",
        "from datetime import timedelta\nx = timedelta(days=1)\n",
    ],
    ids=["annotation", "construction", "timedelta"],
)
def test_no_clock_scan__permits_constructing_and_annotating_a_datetime(source: str) -> None:
    """The rule bounds *reading the clock*, not the `datetime` type itself.

    A scan that also rejected `from datetime import datetime` would be
    unusable -- `base.py` annotates `Provenance.retrieved_at` with it -- and
    an unusable rule gets loosened rather than obeyed.
    """
    assert _clock_calls(source, Path("<planted>")) == []
