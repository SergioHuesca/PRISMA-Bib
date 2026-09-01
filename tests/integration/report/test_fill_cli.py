"""``prismabib fill`` at the process boundary: it must *exit non-zero*.

S10-AC3 is about an exit code, not an exception. A manuscript build pipeline
reads ``$?``; a ``FillError`` that the CLI swallowed into a friendly message
and a zero exit would satisfy every unit test in
``tests/unit/report/test_fill.py`` and still let a wrong manuscript through.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from typer.testing import CliRunner

from prismabib.cli import app

if TYPE_CHECKING:
    from pathlib import Path

runner = CliRunner()


def write_pair(tmp_path: Path, manuscript: str, numbers: dict[str, object]) -> tuple[Path, Path]:
    """Write a manuscript and a numbers.json (helper, not a test)."""
    doc = tmp_path / "paper.md"
    doc.write_text(manuscript, encoding="utf-8")
    nums = tmp_path / "numbers.json"
    nums.write_text(json.dumps(numbers), encoding="utf-8")
    return doc, nums


@pytest.mark.integration
@pytest.mark.acceptance("S10-AC3")
def test_fill__unknown_key_in_manuscript__exits_nonzero(tmp_path: Path) -> None:
    """The criterion, asserted on the exit code a build pipeline reads."""
    doc, nums = write_pair(tmp_path, "We screened {{corpus.sizes}} records.\n", {"corpus.size": 96})

    result = runner.invoke(app, ["fill", str(doc), str(nums)])

    assert result.exit_code != 0
    assert "corpus.sizes" in result.output


@pytest.mark.integration
def test_fill__unused_key_in_numbers_json__exits_nonzero(tmp_path: Path) -> None:
    """The reverse drift, also a non-zero exit.

    BUILD_PLAN asks for both directions, and this is the one a build pipeline
    would otherwise never notice: the manuscript renders perfectly while a
    number it used to cite quietly stops being cited.
    """
    doc, nums = write_pair(
        tmp_path,
        "We screened {{corpus.size}} records.\n",
        {"corpus.size": 96, "geography.share.CHN": 0.31},
    )

    result = runner.invoke(app, ["fill", str(doc), str(nums)])

    assert result.exit_code != 0
    assert "geography.share.CHN" in result.output


@pytest.mark.integration
def test_fill__valid_manuscript__substitutes_every_placeholder(tmp_path: Path) -> None:
    """The positive control: zero exit, no surviving placeholder.

    Without it, a `fill` that exited non-zero unconditionally would satisfy
    both tests above.
    """
    doc, nums = write_pair(
        tmp_path,
        "We screened {{corpus.size}} records and included {{flow.included}}.\n",
        {"corpus.size": 96, "flow.included": 12},
    )
    out = tmp_path / "filled.md"

    result = runner.invoke(app, ["fill", str(doc), str(nums), "--output", str(out)])

    assert result.exit_code == 0
    assert out.read_text(encoding="utf-8") == "We screened 96 records and included 12.\n"
    assert "{{" not in out.read_text(encoding="utf-8")


@pytest.mark.integration
def test_fill__to_stdout__writes_the_document_byte_for_byte(tmp_path: Path) -> None:
    """`fill` is meant to be redirected into a build, so stdout is the document.

    A trailing newline added by the echo helper would corrupt a LaTeX file
    that deliberately ends without one.
    """
    doc, nums = write_pair(tmp_path, "Exactly {{k}}", {"k": 7})

    result = runner.invoke(app, ["fill", str(doc), str(nums)])

    assert result.exit_code == 0
    assert result.stdout == "Exactly 7"
