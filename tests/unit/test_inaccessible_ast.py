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

#: The modules permitted to call ``self._store.write_event(...)`` -- the two
#: that *own* an append-only log and therefore own its validation contract.
#:
#: Keyed on the module, deliberately, not on how the receiver is spelled. An
#: earlier version of this rule exempted any ``self._store`` receiver so that
#: ``overrides.py``'s single legitimate call would pass, and that carve-out
#: created a better bypass than either shape it was guarding: a three-line
#: ``class Sneaky(DecisionLog)`` inherits ``_store`` already bound to
#: ``decisions.jsonl`` with ``model=DecisionEvent``, spells ``self._store``,
#: and writes an unvalidated ``INACCESSIBLE`` decision. Before the carve-out
#: that source was caught.
#:
#: The right key is the thing that actually confers the right -- owning the
#: log in that module -- and it is strictly narrower: this list has two
#: entries, so exactly two lines in the codebase are permitted.
_LOG_OWNING_MODULE_RELATIVE_PATHS = frozenset({"prisma/log.py", "taxonomy/overrides.py"})


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
        ``append`` whose keyword arguments are a superset
        of :data:`_DECISIONLOG_APPEND_KEYWORDS` -- the signature no ordinary
        ``list``/``set``/``dict`` mutation can match, since none of those
        accepts keyword arguments at all.

        Also matches the two shapes ADR 0024 Decision 3 closed, which reach
        ``decisions.jsonl`` **without** passing
        :meth:`~prismabib.prisma.log.DecisionLog._validate_business_rules`:
        any ``.write_event(...)`` call on a ``_store`` attribute, and any
        construction of ``AppendOnlyLog`` with ``model=DecisionEvent``.
        Extracting the shared writer made both of these one attribute access
        away from a validated-looking, fsynced, sidecar-updated
        ``INACCESSIBLE`` event -- and unlike the evasions the module
        docstring already enumerates, neither requires any obfuscation.
        See the module docstring for what this scope does and does not prove.
    """
    offenders: list[int] = []
    decision_event_names = _decision_event_names(tree)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if _is_decision_event_log_construction(node, decision_event_names):
            offenders.append(node.lineno)
            continue
        if not isinstance(node.func, ast.Attribute):
            continue
        attr = node.func.attr
        if attr == "append_event" or (
            attr == "write_event" and _receiver_is_private_store(node.func)
        ):
            offenders.append(node.lineno)
        elif attr == "append":
            keyword_names = {keyword.arg for keyword in node.keywords}
            if keyword_names >= _DECISIONLOG_APPEND_KEYWORDS:
                offenders.append(node.lineno)
    return offenders


def _receiver_is_private_store(func: ast.Attribute) -> bool:
    r"""Whether a ``.write_event`` call's receiver is a ``_store`` attribute.

    Args:
        func: The call's ``func`` node, already known to be an
            :class:`ast.Attribute` named ``write_event``.

    Returns:
        ``True`` for any ``....\_store.write_event(...)``, however the
        receiver is spelled -- ``self``, a subclass's ``self``, a local, a
        parameter. The permission to make that call is granted by
        :data:`_LOG_OWNING_MODULE_RELATIVE_PATHS`, i.e. by *which module* the
        call is in, not by the receiver's name; see that constant for why
        scoping on ``self`` was wrong.

        What this catches is code borrowing a log's durability machinery to
        append an event that machinery never validated -- which before ADR
        0024 meant reimplementing locking and checksums by hand, and now
        means one attribute access.
    """
    receiver = func.value
    return isinstance(receiver, ast.Attribute) and receiver.attr == _PRIVATE_STORE_ATTRIBUTE


def _decision_event_names(tree: ast.AST) -> frozenset[str]:
    """Every local name in ``tree`` that refers to ``DecisionEvent``.

    Args:
        tree: A parsed module.

    Returns:
        ``{"DecisionEvent"}`` plus any ``as`` alias the module bound it to.
        Resolving the alias at the import is what closes
        ``from ... import DecisionEvent as DE`` -- after which the call site
        is ``model=DE`` and no amount of looking at the call alone can tell
        what it means. The same technique the wall-clock scan uses for
        ``from datetime import datetime as dt``.
    """
    names = {_DECISION_EVENT_CLASS}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        names.update(
            alias.asname or alias.name
            for alias in node.names
            if alias.name == _DECISION_EVENT_CLASS
        )
    return frozenset(names)


def _is_decision_event_log_construction(node: ast.Call, names: frozenset[str]) -> bool:
    """Whether ``node`` constructs an ``AppendOnlyLog`` over ``DecisionEvent``.

    Args:
        node: Any call node.

    Returns:
        ``True`` for ``AppendOnlyLog(..., model=DecisionEvent, ...)``.
        ``AppendOnlyLog`` is public API, so this is not a private-attribute
        reach-through: without this rule, a module can build its own writer
        over the decision-event model and append to ``decisions.jsonl`` with
        every durability guarantee and no business-rule validation at all.
    """
    name = node.func.id if isinstance(node.func, ast.Name) else getattr(node.func, "attr", None)
    if name != _SHARED_LOG_CLASS:
        return False
    # Matches the bare name, a dotted `events.DecisionEvent`, and any local
    # `as` alias `names` resolved from the module's imports. The first
    # version matched only the bare name; the other two spellings are not
    # evasions, they are how a module that imports the package rather than
    # the symbol, or that renames on import, would naturally write it.
    return any(
        keyword.arg == "model"
        and (
            ast.unparse(keyword.value).rsplit(".", maxsplit=1)[-1] in names
            or ast.unparse(keyword.value) in names
        )
        for keyword in node.keywords
    )


#: The attribute name holding a log's shared :class:`AppendOnlyLog` writer.
#: A ``.write_event`` call on it bypasses ``_validate_business_rules``.
_PRIVATE_STORE_ATTRIBUTE = "_store"

#: The shared append-only writer's class name (ADR 0024).
_SHARED_LOG_CLASS = "AppendOnlyLog"

#: The decision-event model name; constructing the writer over it is what
#: makes such a writer a decision-log writer.
_DECISION_EVENT_CLASS = "DecisionEvent"


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
        # `as_posix()`, not `str()`: `str(Path("prisma/log.py"))` renders as
        # `prisma\\log.py` on Windows, so the comparison never matched there and
        # `DecisionLog.append`'s own `self.append_event(event)` was reported as a
        # violation. Green on Linux, red on the `full-windows` job -- the
        # machine-dependence class CLAUDE.md names, in the guard itself.
        if _is_exempt(relative) or relative.as_posix() in _LOG_OWNING_MODULE_RELATIVE_PATHS:
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


#: Every shape that reaches `decisions.jsonl` without passing
#: `DecisionLog._validate_business_rules`, as source the guard must reject.
#:
#: Both rows below were **live bypasses** when ADR 0024's extraction landed:
#: each one wrote a loadable, fsynced, sidecar-valid `INACCESSIBLE` decision
#: event, and the guard as written saw neither. Unlike the evasions this
#: module's own docstring enumerates -- constant indirection, concatenation,
#: `**kwargs` -- neither needs any obfuscation. They are the obvious thing a
#: future author reaches for, which is why they are pinned here rather than
#: only fixed.
_PLANTED_WRITE_BYPASSES = [
    (
        "store-reach-through",
        "def f(log, event):\n    log._store.write_event(event)\n",
    ),
    (
        "subclass-inherits-the-store",
        (
            "class Sneaky(DecisionLog):\n"
            "    def go(self, event):\n"
            "        self._store.write_event(event)\n"
        ),
    ),
    (
        "dotted-model",
        "def f(path):\n    AppendOnlyLog(path, model=events.DecisionEvent).write_event(e)\n",
    ),
    (
        "aliased-model",
        (
            "from prismabib.prisma.events import DecisionEvent as DE\n"
            "def f(path):\n"
            "    AppendOnlyLog(path, model=DE).write_event(e)\n"
        ),
    ),
    (
        "own-writer-over-decision-event",
        (
            "def f(path):\n"
            "    writer = AppendOnlyLog(path, model=DecisionEvent, noun='decision')\n"
            "    writer.write_event(event)\n"
        ),
    ),
]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("label", "source"),
    _PLANTED_WRITE_BYPASSES,
    ids=[label for label, _ in _PLANTED_WRITE_BYPASSES],
)
def test_decisionlog_write_scan__detects_a_planted_bypass(label: str, source: str) -> None:
    """The scan is not vacuous: every unvalidated write path must be caught.

    Without this table the guard proves only that it catches the spelling
    that existed when it was written, which is a guard against the past.
    """
    assert _decisionlog_write_lines(ast.parse(source)), label


@pytest.mark.unit
def test_decisionlog_write_scan__flags_self_store_too__permission_is_by_module() -> None:
    """`self._store.write_event(...)` is a hit; the two modules that own a log are exempted.

    Scoping the rule to non-`self` receivers instead was wrong, and made
    things worse than not having the rule: a three-line
    `class Sneaky(DecisionLog)` inherits `_store` already bound to
    `decisions.jsonl` with `model=DecisionEvent`, spells `self._store`, and
    writes an unvalidated `INACCESSIBLE` decision -- a better bypass than
    either shape the guard was extended to catch, and one the pre-scoping
    version caught.

    Permission comes from `_LOG_OWNING_MODULE_RELATIVE_PATHS`, which is two
    entries long, so exactly two lines in the codebase may make this call.
    """
    own_store = "class L:\n    def append(self, event):\n        self._store.write_event(event)\n"
    subclass = (
        "class Sneaky(DecisionLog):\n"
        "    def go(self, event):\n"
        "        self._store.write_event(event)\n"
    )

    assert _decisionlog_write_lines(ast.parse(own_store)), "self._store is not special-cased"
    assert _decisionlog_write_lines(ast.parse(subclass)), "the subclass bypass must be caught"
