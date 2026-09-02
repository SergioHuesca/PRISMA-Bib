"""Static enforcement of S06-AC4 (ADR 0019): only ``screening/`` may write ``INACCESSIBLE``.

BUILD_PLAN: "``INACCESSIBLE`` may only be logged by a human, after the chain
has been exhausted and the reviewer has confirmed no institutional route
exists. It is a screening decision, never an automatic one." A docstring
cannot enforce that -- this test walks the source AST of every module under
``src/prismabib`` and fails if any module outside ``screening/`` constructs
a call carrying a ``reason_code="INACCESSIBLE"`` keyword argument. An
architectural rule enforced as a test is worth more than the same rule in a
comment (BUILD_PLAN's own words for this exact test, line 1164).

Static rather than a runtime hook, deliberately: a runtime check could only
ever prove the specific call paths a test exercises never write it. Walking
every module's AST for the literal keyword/value pair proves something
stronger -- that **no** code path anywhere outside ``screening/`` can, by
construction, whatever future call graph is added.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SRC_ROOT = Path(__file__).parent.parent.parent / "src" / "prismabib"

_REASON_CODE_KEYWORD = "reason_code"
_INACCESSIBLE = "INACCESSIBLE"


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
        deliberately not resolving variables or f-strings, since the
        construct this test forbids is a call site that *spells the code
        out*, and any indirection sophisticated enough to defeat that
        static match is a code-review problem this test was never going to
        catch anyway.
    """
    offenders: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg == _REASON_CODE_KEYWORD and _is_inaccessible_literal(keyword.value):
                offenders.append(node.lineno)
    return offenders


@pytest.mark.unit
@pytest.mark.acceptance("S06-AC4")
def test_inaccessible__no_code_path_can_write_it() -> None:
    source_files = _source_files()
    assert source_files, "guard the guard: an empty file list would make this vacuously true"

    offenders: dict[str, list[int]] = {}
    for path in source_files:
        relative = path.relative_to(_SRC_ROOT)
        if relative.parts[0] == "screening":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        lines = _reason_code_inaccessible_lines(tree)
        if lines:
            offenders[str(relative)] = lines

    assert offenders == {}, (
        f"reason_code={_INACCESSIBLE!r} constructed outside screening/: {offenders}. "
        "ADR 0019: INACCESSIBLE may only be logged by a human, during screening."
    )


@pytest.mark.unit
def test_inaccessible__the_guard_itself__detects_a_planted_violation() -> None:
    """Prove the detector is not vacuously green (§3.7.3's own discipline, applied to itself).

    Parses a synthetic module string containing exactly the construct the
    real test forbids, outside any ``screening/`` exemption -- if this
    fails, ``test_inaccessible__no_code_path_can_write_it`` passing proves
    nothing.
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
