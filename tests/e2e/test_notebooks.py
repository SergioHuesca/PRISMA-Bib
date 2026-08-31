"""BUILD_PLAN's Stage 5 notebook test: the shipped notebook actually runs.

The notebook is the documented entry point -- `README` and
`docs/getting-started.md` both send a new researcher to it -- and it is the one
artefact in the repository that no other test exercises. `pytest` never imports
it, so a renamed keyword argument, a moved helper or a removed export breaks a
researcher's first five minutes with the tool while every other check stays
green.

BUILD_PLAN's Stage 5 table names this test, and `pyproject.toml` registers the
`notebook` marker for it, but it did not exist: the notebook was executed only
by the non-required `notebooks` CI job, which cannot fail a merge. This closes
that.

The notebook is executed with `nbclient` directly rather than under `--nbmake`,
because `--nbmake` is a collection plugin that turns notebook *files* into test
items, which cannot be a test item itself. The distinction is only mechanical:
both run every cell and fail on the first exception.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import nbformat
import pytest
from nbclient import NotebookClient

if TYPE_CHECKING:
    from collections.abc import Iterator

#: The frozen reference project the notebook screens.
FIXTURE = Path(__file__).parent.parent / "fixtures" / "projects" / "reference"

NOTEBOOK = Path(__file__).parent.parent.parent / "notebooks" / "01_screen_title_abstract.ipynb"


@pytest.fixture
def projects_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A scratch projects root holding a copy of the reference fixture.

    Copied rather than used in place, and asserted afterwards: executing the
    notebook builds a Layer 1 store and appends to a decision log, and the
    fixture is byte-frozen (BUILD_PLAN line 536). A test that quietly mutated
    it would corrupt every golden number in the suite -- §5 risk 11 by
    accident rather than by regeneration.
    """
    before = sorted(path.relative_to(FIXTURE) for path in FIXTURE.rglob("*"))
    root = tmp_path / "projects"
    root.mkdir()
    shutil.copytree(FIXTURE, root / "reference")
    monkeypatch.setenv("PRISMABIB_PROJECTS_ROOT", str(root))
    monkeypatch.delenv("SCOPUS_API_KEY", raising=False)

    yield root

    assert sorted(path.relative_to(FIXTURE) for path in FIXTURE.rglob("*")) == before


@pytest.mark.e2e
@pytest.mark.notebook
@pytest.mark.acceptance("S05-AC1")
def test_notebook__01_screen_title_abstract__executes(projects_root: Path) -> None:
    """Every cell runs against the reference project, with no Scopus key.

    No credential is set, deliberately: the notebook reads an archive that is
    already on disk and must make no network call, so a key it does not need
    is a key a reviewer could be told to supply.
    """
    notebook = nbformat.read(NOTEBOOK, as_version=4)

    NotebookClient(notebook, timeout=120, kernel_name="python3").execute()

    outputs = [output for cell in notebook.cells for output in cell.get("outputs", [])]
    assert [output for output in outputs if output.get("output_type") == "error"] == []
    assert any("screening reference" in str(output) for output in outputs), (
        "the notebook opened no project"
    )
