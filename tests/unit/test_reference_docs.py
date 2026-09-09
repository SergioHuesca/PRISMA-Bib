"""Every package under `src/prismabib/` has a section in the reference docs.

§3.5's Definition of Done requires every public symbol to appear in the
mkdocstrings reference, and Stage 11's release checklist restates it as "no
orphan pages". Neither is enforced by `mkdocs build --strict`, which fails on
a broken link or a missing nav entry but not on a module nobody wrote a
section for -- `docs/reference/index.md`'s own preamble says so.

So the gap grew one stage at a time. Stages 7, 8 and 9 added three packages
and ~40 public callables while the `docs` job stayed green, and the only
record that anything was missing was a hand-maintained "Not yet built" list
that had itself gone stale in both directions: it named `fulltext/`, which
had had a section since Stage 6.

A convention nothing checks is the shape this project keeps rediscovering.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import prismabib

_PACKAGE_ROOT = Path(prismabib.__file__).parent
_REFERENCE = Path(__file__).resolve().parents[2] / "docs" / "reference" / "index.md"


def _packages() -> list[str]:
    """Every importable subpackage of `prismabib`, sorted.

    Returns:
        Directory names holding an `__init__.py`, which is what
        `mkdocstrings` can collect -- `viz/` had none until this test was
        written, so `::: prismabib.viz` failed to build.
    """
    return sorted(
        path.name
        for path in _PACKAGE_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    )


@pytest.mark.unit
@pytest.mark.parametrize("package", _packages())
@pytest.mark.acceptance("S11-AC3")
def test_reference_docs__every_package__has_a_section(package: str) -> None:
    """A package with no `::: prismabib.<pkg>` directive is invisible to readers.

    Parametrised per package so a gap names the package rather than failing
    one aggregate assertion.
    """
    reference = _REFERENCE.read_text(encoding="utf-8")

    assert f"::: prismabib.{package}\n" in reference, (
        f"`{package}` has no section in docs/reference/index.md. "
        "§3.5: every public symbol appears in the reference; `mkdocs --strict` cannot catch this."
    )


@pytest.mark.unit
def test_reference_docs__package_list__is_not_empty() -> None:
    """Guard the guard: an empty package list would make the sweep vacuous."""
    assert len(_packages()) >= 8
