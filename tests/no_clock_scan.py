"""Shared AST machinery for "this module may never read the wall clock" guards.

Extracted from ``tests/unit/bibliometrics/test_no_clock.py`` (ADR 0022
Decision 2) so that a second package with the same reproducibility
requirement -- Stage 8's pure ``taxonomy/coder.py``/``review.py`` (ADR 0023:
"no ``hash()``... no clock, no unseeded ``random``") -- gets the identical
scan rather than a second, potentially-looser reimplementation.
``tests/unit/taxonomy/test_no_clock.py`` is the second user.
"""

from __future__ import annotations

import ast
from pathlib import Path

#: Every attribute name that reads a clock, matched on the *name* rather
#: than on a dotted suffix -- see the module this was extracted from for why
#: a suffix match cannot work (``datetime.today`` vs. ``date.today``).
FORBIDDEN_ATTRIBUTES = frozenset(
    {"now", "utcnow", "today", "time", "monotonic", "perf_counter", "time_ns", "monotonic_ns"}
)

#: Modules whose names may not be imported *from*, because ``from datetime
#: import datetime as dt`` then ``dt.now()`` reduces the call to a bare
#: attribute on a local alias that no attribute-name rule can trace back.
CLOCK_MODULES = frozenset({"datetime", "time"})

#: Names that may be imported from those modules without flagging the
#: import: the *types*, needed for annotation and construction, which
#: cannot themselves read a clock.
SAFE_IMPORTED_NAMES = frozenset({"datetime", "date", "timedelta", "timezone", "UTC", "tzinfo"})


def clock_calls(source: str, path: Path) -> list[str]:
    """Every forbidden clock call or clock-function import found in ``source``.

    Args:
        source: The module's source text.
        path: Only used to make a parse failure's message actionable.

    Returns:
        One entry per offending call site or import, e.g. ``"datetime.now"``
        or ``"from time import time"``.
    """
    tree = ast.parse(source, filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        found.extend(_offence(node))
    return found


def _offence(node: ast.AST) -> list[str]:
    """Any clock offence ``node`` itself constitutes, as reportable text.

    Args:
        node: Any AST node.

    Returns:
        Zero or one entry; a list keeps the caller's ``extend`` uniform.
    """
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
        return [ast.unparse(node.func)] if node.func.attr in FORBIDDEN_ATTRIBUTES else []
    if isinstance(node, ast.ImportFrom) and node.module in CLOCK_MODULES:
        return [
            f"from {node.module} import {alias.name}"
            for alias in node.names
            if alias.name not in SAFE_IMPORTED_NAMES or alias.asname is not None
        ]
    return []


#: Every spelling that reads a clock, as a source snippet a scan built on
#: this module must reject. Rows 2-7 were all passing a naive dotted-suffix
#: scan when this table was first written for the bibliometrics guard.
PLANTED_VIOLATIONS = [
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

#: Source snippets a scan built on this module must *permit* -- constructing
#: or annotating with the ``datetime``/``timedelta`` types is not reading
#: the clock.
PERMITTED_SNIPPETS = [
    "from datetime import datetime\ndef f(x: datetime) -> datetime:\n    return x\n",
    "from datetime import UTC, datetime\nx = datetime(2026, 1, 1, tzinfo=UTC)\n",
    "from datetime import timedelta\nx = timedelta(days=1)\n",
]

__all__ = [
    "CLOCK_MODULES",
    "FORBIDDEN_ATTRIBUTES",
    "PERMITTED_SNIPPETS",
    "PLANTED_VIOLATIONS",
    "SAFE_IMPORTED_NAMES",
    "clock_calls",
]
