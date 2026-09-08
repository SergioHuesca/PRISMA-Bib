"""Shared AST machinery for "this module may not compute" guards (BUILD_PLAN §Stage 9).

``viz/figures.py``'s own layering rule, stated in as many words: *"Figure
functions must not compute anything. If a figure function contains
arithmetic, that arithmetic belongs in Stage 7."* (ADR 0025 Decision 1). This
module is the mechanical enforcement -- ``test_figures__no_module_performs_arithmetic``
in ``tests/unit/viz/test_figures.py`` is a thin wrapper around
:func:`arithmetic_offences`, following the same extraction pattern
``tests/no_clock_scan.py`` already used for the wall-clock guard (built for
a second package with an identical shape of rule).

Two families of offence, matching BUILD_PLAN's own wording:

- **Arithmetic operators** -- ``+ - * / // % **`` (and their augmented-assign
  forms), anywhere in the module, with no "it's just formatting" exception:
  a figure computing a percentage for a label is exactly the defect this
  guard exists to catch, not a case to wave through.
- **Aggregation calls** -- a fixed name/method list (``sum``, ``len``,
  ``mean``, ``.agg(``, ``.group_by(``, ...) that summarise several values
  into one, whether called as a bare name or as a method on any object
  (``ast`` cannot resolve which *class* a `.sum()` call binds to without
  running the code, so this deliberately flags the method name on any
  receiver -- a false positive on an unrelated ``.sum()`` is a smaller cost
  than a real one this scan would otherwise miss).

**The scan must be able to fail (ADR 0025 Decision 1).** :data:`PLANTED_VIOLATIONS`
supplies exactly the two BUILD_PLAN names as its required plants -- a
``sum(...)`` call and an ``a / b`` division -- plus a few more of the same
two families, each pinned by ``test_no_arithmetic_scan__planted_violation__is_caught``.
"""

from __future__ import annotations

import ast
from pathlib import Path

#: Every arithmetic binary/augmented-assign operator this scan forbids, kept
#: as an AST-node-type -> human-readable-symbol map so an offence string
#: names the operator, not just the node type's Python class name.
FORBIDDEN_BINOPS: dict[type[ast.operator], str] = {
    ast.Add: "+",
    ast.Sub: "-",
    ast.Mult: "*",
    ast.Div: "/",
    ast.FloorDiv: "//",
    ast.Mod: "%",
    ast.Pow: "**",
    ast.MatMult: "@",
}

#: Aggregation builtins that summarise a collection into one value, called
#: bare (``sum(x)``, not ``obj.sum()`` -- see :data:`FORBIDDEN_AGGREGATE_METHODS`
#: for the attribute form). Deliberately **not** ``len`` -- BUILD_PLAN's own
#: two required plants are ``sum(...)`` and ``a / b``, and ``len()`` over a
#: sequence of *figures* (subplot counts, panel-per-dimension layout) is
#: presentation bookkeeping, not a quantitative finding recomputed from
#: ``AnalysisResult.data`` -- an unconditional ban would make a multi-panel
#: figure function unwritable for no safety benefit, since every *value*
#: plotted still comes from ``data``/``params``, never from a manual count.
FORBIDDEN_AGGREGATE_NAMES = frozenset({"sum", "min", "max", "abs", "round", "pow", "divmod", "len"})

#: The exact ``len(...)`` call shapes a figure module may still use, matched
#: on the unparsed argument.
#:
#: ``len`` was previously exempt outright, justified for "a sequence of
#: *figures* (subplot counts)". The justification held for two call sites and
#: the ban lifted was total, so ``len([row for row in rows if row["flag"]])``
#: -- an arbitrary filtered count over analysis data, rendered straight into
#: a caption -- passed the scan. A review demonstrated exactly that.
#:
#: So the exemption is now a whitelist of what the justification actually
#: covers: counting the *results* handed in, never counting a comprehension
#: or a filtered frame.
PERMITTED_LEN_ARGUMENTS = frozenset({"results", "self._results"})

#: Aggregation/summarisation methods, matched on attribute name alone
#: (see this module's docstring for why -- ``ast`` has no type information).
#: Covers polars/pandas/statistics-shaped aggregation surfaces this
#: package's own analysis modules use, since a figure re-deriving one of
#: these from raw data is exactly "arithmetic that belongs in Stage 7".
FORBIDDEN_AGGREGATE_METHODS = frozenset(
    {
        "sum",
        "mean",
        "median",
        "std",
        "var",
        "agg",
        "aggregate",
        "count",
        "cumsum",
        "corr",
        "value_counts",
        "quantile",
        "n_unique",
        "group_by",
        "groupby",
        "apply",
        "map_elements",
        "reduce",
        # Spellings a review found the first version missing. Each is a way
        # of writing an aggregation the list above already forbids, so
        # omitting them made the ban a ban on one vocabulary rather than on
        # the operation.
        "fmean",
        "prod",
        "fsum",
        "cum_sum",  # polars' current name; only the legacy `cumsum` was listed
        "cumprod",
        "cum_prod",
        "accumulate",
        "truediv",
        "floordiv",
        "add",
        "sub",
        "mul",
        "divide",
        "subtract",
        "multiply",
        "nlargest",
        "nsmallest",
        "rank",
        "pct_change",
    }
)

#: Bare names that aggregate, beyond the builtins: anything imported from
#: ``statistics``/``functools``/``itertools``/``operator`` and called
#: directly. ``from statistics import mean; mean(xs)`` is an
#: :class:`ast.Name` call, so the attribute list above never sees it.
FORBIDDEN_AGGREGATE_IMPORTED_NAMES = frozenset(
    {
        "mean",
        "median",
        "fmean",
        "fsum",
        "prod",
        "reduce",
        "accumulate",
        "add",
        "sub",
        "mul",
        "truediv",
        "floordiv",
        "stdev",
        "variance",
        "quantiles",
    }
)


def _is_sorted_call(node: ast.expr) -> bool:
    """Whether ``node`` is a direct ``sorted(...)`` call.

    Args:
        node: The expression being subscripted.

    Returns:
        ``True`` for ``sorted(xs)``, so the caller can reject ``sorted(xs)[0]``
        while leaving a bare ``sorted(xs)`` alone.
    """
    return (
        isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "sorted"
    )


def arithmetic_offences(source: str, path: Path) -> list[str]:
    """Every forbidden arithmetic operator or aggregation call found in ``source``.

    Args:
        source: The module's source text.
        path: Only used to make a parse failure's message actionable.

    Returns:
        One entry per offending node, as its unparsed source text -- empty
        when ``source`` computes nothing.
    """
    tree = ast.parse(source, filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        found.extend(_offence(node))
    return found


def _offence(node: ast.AST) -> list[str]:
    """Any arithmetic/aggregation offence ``node`` itself constitutes.

    Args:
        node: Any AST node.

    Returns:
        Zero or one entry; a list keeps the caller's ``extend`` uniform.
    """
    if isinstance(node, ast.BinOp) and type(node.op) in FORBIDDEN_BINOPS:
        symbol = FORBIDDEN_BINOPS[type(node.op)]
        return [f"arithmetic '{symbol}': {ast.unparse(node)}"]
    if isinstance(node, ast.AugAssign) and type(node.op) in FORBIDDEN_BINOPS:
        symbol = FORBIDDEN_BINOPS[type(node.op)]
        return [f"augmented arithmetic '{symbol}=': {ast.unparse(node)}"]
    if isinstance(node, ast.Subscript) and _is_sorted_call(node.value):
        # `sorted(xs)[-1]` is `max(xs)` with extra steps -- a review found it
        # passing. Bare `sorted(...)` is *ordering*, not arithmetic, and
        # banning it outright would forbid `sorted(paths)`; only the
        # subscripted form extracts a value.
        return [f"aggregation via sorted()[...]: {ast.unparse(node)}"]
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Name) and func.id == "len":
            argument = ast.unparse(node.args[0]) if node.args else ""
            if argument in PERMITTED_LEN_ARGUMENTS:
                return []
            return [f"aggregation call len(): {ast.unparse(node)}"]
        if isinstance(func, ast.Name) and (
            func.id in FORBIDDEN_AGGREGATE_NAMES or func.id in FORBIDDEN_AGGREGATE_IMPORTED_NAMES
        ):
            return [f"aggregation call {func.id}(): {ast.unparse(node)}"]
        if isinstance(func, ast.Attribute) and func.attr in FORBIDDEN_AGGREGATE_METHODS:
            return [f"aggregation method .{func.attr}(): {ast.unparse(node)}"]
    return []


#: The two required plants (BUILD_PLAN's own wording: "a planted sum(...)
#: and a planted a / b"), plus a few more of the same two families -- a scan
#: that only catches the exact two named spellings would itself be the
#: under-tested guard shape this project has shipped before.
PLANTED_VIOLATIONS: list[tuple[str, str]] = [
    ("sum call", "def f(data):\n    total = sum(data)\n    return total\n"),
    ("division", "def f(a, b):\n    return a / b\n"),
    ("addition", "def f(a, b):\n    return a + b\n"),
    ("aggregate method", "def f(frame):\n    return frame['x'].mean()\n"),
    ("augmented assign", "def f(a, b):\n    a -= b\n    return a\n"),
    ("min call", "def f(items):\n    return min(items)\n"),
    ("group_by call", "def f(frame):\n    return frame.group_by('x')\n"),
    # Every spelling a review found the first version missing. All 17 of
    # these passed the scan; the `len(comprehension)` one was demonstrated
    # end to end, rendering two undeclared derived numbers into a caption
    # with the whole 1484-test suite green.
    ("len of a comprehension", 'def f(rows):\n    return len([r for r in rows if r["flag"]])\n'),
    ("len of a filtered frame", 'def f(frame):\n    return len(frame.filter(frame["x"]))\n'),
    ("statistics.mean", "from statistics import mean\n\ndef f(xs):\n    return mean(xs)\n"),
    ("functools.reduce", "from functools import reduce\n\ndef f(xs):\n    return reduce(f, xs)\n"),
    ("operator.add bare", "from operator import add\n\ndef f(a, b):\n    return add(a, b)\n"),
    (
        "operator.truediv attr",
        "import operator\n\ndef f(a, b):\n    return operator.truediv(a, b)\n",
    ),
    ("math.fsum", "import math\n\ndef f(xs):\n    return math.fsum(xs)\n"),
    ("math.prod", "import math\n\ndef f(xs):\n    return math.prod(xs)\n"),
    ("polars cum_sum", 'def f(frame):\n    return frame["x"].cum_sum()\n'),
    ("polars truediv method", "def f(a, b):\n    return a.truediv(b)\n"),
    ("numpy divide", "import numpy as np\n\ndef f(a, b):\n    return np.divide(a, b)\n"),
    (
        "itertools.accumulate",
        "import itertools\n\ndef f(xs):\n    return itertools.accumulate(xs)\n",
    ),
    ("sorted indexing", "def f(xs):\n    return sorted(xs)[-1]\n"),
]

#: Source snippets a scan built on this module must *permit* -- reading,
#: filtering, slicing and formatting an already-computed
#: :class:`~prismabib.bibliometrics.base.AnalysisResult` performs no
#: arithmetic of its own.
PERMITTED_SNIPPETS: list[str] = [
    "def f(data):\n    return data.head(5)\n",
    "def f(items):\n    for index, item in enumerate(items, start=1):\n        pass\n",
    "def f(data):\n    return data.filter(data['is_partial'])\n",
    'def f(result):\n    return f"n = {result.provenance.corpus_size}"\n',
    "def f(parts):\n    return ', '.join(parts)\n",
    "def f(results):\n    return len(results)\n",
]

__all__ = [
    "FORBIDDEN_AGGREGATE_IMPORTED_NAMES",
    "FORBIDDEN_AGGREGATE_METHODS",
    "FORBIDDEN_AGGREGATE_NAMES",
    "FORBIDDEN_BINOPS",
    "PERMITTED_LEN_ARGUMENTS",
    "PERMITTED_SNIPPETS",
    "PLANTED_VIOLATIONS",
    "arithmetic_offences",
]
