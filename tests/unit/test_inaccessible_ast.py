"""Static enforcement of S06-AC4 (ADR 0019): only ``screening/``/``cli.py`` may write a decision.

BUILD_PLAN: "``INACCESSIBLE`` may only be logged by a human, after the chain
has been exhausted and the reviewer has confirmed no institutional route
exists. It is a screening decision, never an automatic one." A docstring
cannot enforce that -- this test walks the source AST of every module under
``src/prismabib`` and fails if any module outside ``screening/`` constructs a
call carrying a ``reason_code="INACCESSIBLE"`` keyword argument, matching
BUILD_PLAN's own description of this test almost verbatim (line 1164).

**What this guard actually proves, stated plainly.** A prior version of this
module's docstring, ADR 0019 and the CHANGELOG all claimed "no code path can,
by construction" -- which is stronger than what a purely syntactic AST match
can support. This test (like the second one below it) is defeated by one line
of indirection: a module-level constant (``_CODE = "INACCESSIBLE"``, then
``reason_code=_CODE``), string concatenation
(``reason_code="INACCE" + "SSIBLE"``), ``**kwargs`` forwarding, or a helper
function that itself takes ``reason_code`` as a parameter all pass this test
today while still writing the same event. What it actually guarantees is
narrower and still worth having: **no module outside the exempted set spells
the literal construct out at the call site.** That catches exactly the
failure mode this stage has actually shipped -- a resolver author reaching for
the obvious, direct way to mark a chain-exhausted record inaccessible -- and
a reviewer auditing a diff for the literal string ``"INACCESSIBLE"`` will
always find every real call site, since nothing here has a reason to obscure
one. It does not, and cannot, close every path a determined author could
construct; that boundary is code review's job, not this test's. See
:func:`test_inaccessible__the_guard_itself__detects_a_planted_violation` for
what the check *does* catch, and read it as the concrete, non-hypothetical
proof of that scope rather than of a stronger one.

**A second, narrower version of the same class of defeat.** Even
``reason_code="INACCESSIBLE"`` reaching :class:`~prismabib.prisma.events.DecisionEvent`
is not the only way to write a decision at all: any caller could construct
and append an *arbitrary* :class:`~prismabib.prisma.events.DecisionEvent` and
hand it straight to :meth:`~prismabib.prisma.log.DecisionLog.append_event`,
bypassing the first check's literal-string match entirely by building the
reason code some other way and never writing the literal keyword/value pair
this module walks the AST for. So a second, independent check
(:func:`_decisionlog_write_lines`) forbids calling
:meth:`~prismabib.prisma.log.DecisionLog.append` or
:meth:`~prismabib.prisma.log.DecisionLog.append_event` at all outside
``screening/`` and ``cli.py`` (the two places a decision may legitimately be
written -- screening's own queue, and a future CLI command built on it) --
narrowing the reachable surface for *any* decision write, not just the one
literal string.

Static rather than a runtime hook, deliberately: a runtime check could only
ever prove the specific call paths a test exercises never write it. Walking
every module's AST for the construct is a stronger, if still not absolute,
guarantee -- see the caveat above.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "prismabib"

_REASON_CODE_KEYWORD = "reason_code"
_INACCESSIBLE = "INACCESSIBLE"

#: Modules allowed to write a `reason_code="INACCESSIBLE"` literal, or to call
#: `DecisionLog.append`/`append_event` at all -- the two places a screening
#: decision may legitimately be written. `cli.py` carries no such call today
#: (screening has no CLI surface yet), but is exempted in anticipation of one,
#: per this test's own brief.
_EXEMPT_RELATIVE_PATHS = frozenset({"screening", "cli.py"})

#: `DecisionLog.append`'s required keyword-only parameters (BUILD_PLAN's own
#: decision-event shape). A call site passing all four as keywords -- which
#: `append` requires, since every one of them sits after the bare `*` in its
#: signature -- cannot be an ordinary `list.append(x)`/`set.append(...)` call
#: (neither accepts keyword arguments naming any of these at all, and no other
#: `.append(...)` call anywhere in this codebase does either -- confirmed by
#: grepping every `.append(` call site in `src/` at the time this check was
#: written). Matching this signature, rather than the bare attribute name
#: `"append"`, is what keeps this check from flagging the hundreds of
#: legitimate `list.append(...)` calls elsewhere in the codebase.
_DECISIONLOG_APPEND_KEYWORDS = frozenset({"stage", "record_id", "reviewer", "decision"})

#: `prisma/log.py` additionally exempted from the `DecisionLog.append`/
#: `append_event` check: it is where both methods are *defined*, and
#: `append`'s own body calls `self.append_event(event)` as its last line --
#: the implementation, not a second write path around it.
_DECISIONLOG_MODULE_RELATIVE_PATH = "prisma/log.py"


def _source_files() -> list[Path]:
    """Every ``.py`` file under ``src/prismabib``, in a stable order."""
    return sorted(_SRC_ROOT.rglob("*.py"))


def _is_inaccessible_literal(node: ast.expr) -> bool:
    """Whether an AST expression is exactly the string literal ``"INACCESSIBLE"``."""
    return isinstance(node, ast.Constant) and node.value == _INACCESSIBLE


def _reason_code_inaccessible_lines(tree: ast.AST) -> list[int]:
    """Line numbers of every call in ``tree`` that passes ``reason_code="INACCESSIBLE"``.

    Args:
        tree: A parsed module.

    Returns:
        One entry per offending :class:`ast.Call` node, so a failure names
        exactly where the violation is rather than only which file.
        Matches a keyword argument by name and a literal string value --
        deliberately not resolving variables or f-strings; see the module
        docstring for exactly what that scope does and does not prove.
    """
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == _REASON_CODE_KEYWORD and _is_inaccessible_literal(keyword.value):
                offenders.append(node.lineno)
    return offenders


def _decisionlog_write_lines(tree: ast.AST) -> list[int]:
    """Line numbers of every call in ``tree`` that looks like a ``DecisionLog`` write.

    Args:
        tree: A parsed module.

    Returns:
        One entry per offending :class:`ast.Call` node: a call to an
        attribute named ``append_event`` (a name no other class in this
        codebase defines, so any call site naming it is calling
        :meth:`~prismabib.prisma.log.DecisionLog.append_event`), or a call to
        an attribute named ``append`` whose keyword arguments are a superset
        of :data:`_DECISIONLOG_APPEND_KEYWORDS` -- the signature no ordinary
        ``list``/``set``/``dict`` mutation can match, since none of those
        accepts keyword arguments at all. See the module docstring for what
        this scope does and does not prove.
    """
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        if attr == "append_event":
            offenders.append(node.lineno)
        elif attr == "append":
            keyword_names = {keyword.arg for keyword in node.keywords}
            if keyword_names >= _DECISIONLOG_APPEND_KEYWORDS:
                offenders.append(node.lineno)
    return offenders


def _is_exempt(relative: Path) -> bool:
    """Whether ``relative`` (a path under ``src/prismabib``) is exempt from these checks."""
    return relative.parts[0] in _EXEMPT_RELATIVE_PATHS


@pytest.mark.unit
@pytest.mark.acceptance("S06-AC4")
def test_inaccessible__no_code_path_spells_the_literal_outside_screening_or_cli() -> None:
    source_files = _source_files()
    assert source_files, "guard the guard: an empty file list would make this vacuously true"

    offenders: dict[str, list[int]] = {}
    for path in source_files:
        relative = path.relative_to(_SRC_ROOT)
        if _is_exempt(relative):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines = _reason_code_inaccessible_lines(tree)
        if lines:
            offenders[str(relative)] = lines

    assert offenders == {}, (
        f"reason_code={_INACCESSIBLE!r} constructed outside screening/ or cli.py: {offenders}. "
        "ADR 0019: INACCESSIBLE may only be logged by a human, during screening."
    )


@pytest.mark.unit
def test_decisionlog_write__no_code_path_calls_it_outside_screening_or_cli() -> None:
    """``DecisionLog.append``/``append_event`` may only be called from ``screening/`` or ``cli.py``.

    Narrower defeat of the guard above: a caller could build an arbitrary
    :class:`~prismabib.prisma.events.DecisionEvent` (never spelling
    ``reason_code="INACCESSIBLE"`` as a literal anywhere) and hand it to
    :meth:`~prismabib.prisma.log.DecisionLog.append_event` directly. This
    check closes that specific path by forbidding the call itself, not just
    one literal argument to it.
    """
    source_files = _source_files()
    assert source_files, "guard the guard: an empty file list would make this vacuously true"

    offenders: dict[str, list[int]] = {}
    for path in source_files:
        relative = path.relative_to(_SRC_ROOT)
        if _is_exempt(relative) or str(relative) == _DECISIONLOG_MODULE_RELATIVE_PATH:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines = _decisionlog_write_lines(tree)
        if lines:
            offenders[str(relative)] = lines

    assert offenders == {}, (
        f"DecisionLog.append/append_event called outside screening/ or cli.py: {offenders}. "
        "ADR 0019/ADR 0003: a decision may only be written from screening."
    )


@pytest.mark.unit
def test_inaccessible__the_guard_itself__detects_a_planted_violation() -> None:
    """Prove the literal-string detector is not vacuously green (§3.7.3's own discipline).

    Parses a synthetic module string containing exactly the construct the
    real test forbids, outside any exemption -- if this fails,
    ``test_inaccessible__no_code_path_spells_the_literal_outside_screening_or_cli``
    passing proves nothing.
    """
    planted = (
        "from prismabib.prisma.events import DecisionEvent\n"
        "\n"
        "def make_bad_event():\n"
        "    return DecisionEvent(\n"
        "        event_id='x', ts=None, project='p', stage='fulltext',\n"
        "        record_id='r', reviewer='auto', decision='exclude',\n"
        "        reason_code='INACCESSIBLE', criteria_version='1.0.0',\n"
        "    )\n"
    )
    tree = ast.parse(planted, filename="<planted-violation>")

    assert _reason_code_inaccessible_lines(tree) != []


@pytest.mark.unit
def test_decisionlog_write__the_guard_itself__detects_a_planted_violation() -> None:
    """Prove the ``DecisionLog`` write detector is not vacuously green.

    Two planted call shapes, both outside any exemption: a direct
    ``.append_event(event)`` call, and an ``.append(...)`` call carrying every
    keyword :data:`_DECISIONLOG_APPEND_KEYWORDS` names -- if either fails to
    be caught, the corresponding half of
    ``test_decisionlog_write__no_code_path_calls_it_outside_screening_or_cli``
    passing proves nothing.
    """
    planted_append_event = "def sneak_it_in(log, event):\n    log.append_event(event)\n"
    planted_append = (
        "def sneak_it_in(log):\n"
        "    log.append(\n"
        "        stage=PrismaStage.FULLTEXT, record_id='r', reviewer='auto',\n"
        "        decision='exclude', reason_code='INACCESSIBLE',\n"
        "    )\n"
    )

    assert _decisionlog_write_lines(ast.parse(planted_append_event)) != []
    assert _decisionlog_write_lines(ast.parse(planted_append)) != []


@pytest.mark.unit
def test_decisionlog_write__ordinary_list_append__is_not_flagged() -> None:
    """Guard the guard's precision: a bare ``list.append(x)`` must never be flagged.

    Hundreds of legitimate calls of exactly this shape exist under
    ``src/prismabib`` (``attempts.append(...)``, ``rows.append(...)``, ...).
    If this test fails, the real check above is unusable -- it would flag the
    whole codebase.
    """
    ordinary = "def f(items):\n    items.append(1)\n    items.append(x=1)\n"
    assert _decisionlog_write_lines(ast.parse(ordinary)) == []
