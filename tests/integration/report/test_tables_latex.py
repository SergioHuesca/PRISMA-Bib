"""The LaTeX rendering must actually compile.

BUILD_PLAN §Stage 10 marks this ``skipif`` when TeX is absent locally and
required in CI. That split is the point: escaping rules are the kind of thing
that looks right in a string comparison and fails in a build, so the string
assertions in ``tests/unit/report/test_tables.py`` are not a substitute for
running ``pdflatex`` over the output at least somewhere.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from prismabib.report.tables import Table, to_latex

pytestmark = pytest.mark.skipif(
    shutil.which("pdflatex") is None,
    reason="pdflatex is not installed; this test is required in CI, optional locally",
)

#: The characters that break a naive LaTeX table, in the shapes real corpora
#: produce them: an ampersand in an IEEE venue name, an underscore in a
#: database identifier, a percent sign in a share.
HOSTILE = Table(
    slug="hostile",
    caption="Venues & shares (%) for record_ids",
    columns=("Venue", "Share"),
    rows=(
        ("Robotics & Automation", "31%"),
        ("Pattern_Analysis", "12%"),
        ("Cost ~$100 ^2", "5%"),
    ),
)


@pytest.mark.integration
def test_tables__latex__compiles_under_pdflatex_booktabs(tmp_path: Path) -> None:
    """A generated table must build inside a minimal booktabs document."""
    document = (
        "\\documentclass{article}\n"
        "\\usepackage{booktabs}\n"
        "\\begin{document}\n" + to_latex(HOSTILE) + "\\end{document}\n"
    )
    source = tmp_path / "doc.tex"
    source.write_text(document, encoding="utf-8")

    result = subprocess.run(
        ["pdflatex", "-interaction=nonstopmode", "-halt-on-error", source.name],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )

    assert result.returncode == 0, result.stdout[-2000:]
    assert (tmp_path / "doc.pdf").is_file()
