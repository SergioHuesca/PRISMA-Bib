"""BUILD_PLAN.md line 626: the package must import and expose its version.

And -- since the version it exposes is stamped into every sealed Layer 0
manifest as ``RunManifest.client_version`` -- BUILD_PLAN §3.6.4: the Release
tag and the exported version must agree, "that is what makes an exported
number traceable to a published state of the code".

**Why the old test here did not check that.** It compared
``prismabib.__version__`` against ``pyproject.toml``'s ``project.version``,
which is a tautology: ``__version__`` reads the installed distribution's
metadata, and that metadata is built *from* ``project.version``. The test was
green for the whole of tags ``v0.2.0`` through ``v0.5.0`` while the value it
compared was ``0.1.0`` on both sides, and every capture made in that window
sealed ``"client_version": "0.1.0"`` into a run directory that, by design, can
never be rewritten. A test that compares a number to itself cannot detect that
the number is wrong.

The tests below compare against git instead -- the one authority that does not
derive from ``pyproject.toml`` -- and handle a checkout with no tags, or no
``.git`` at all, by asserting what a *correct* degraded value looks like
rather than skipping. A skip here would have been indistinguishable from the
failure it is meant to catch.

Deliberately claims no acceptance criterion (it previously claimed S00-AC2,
which it cannot assert; that is now claimed by
``tests/live/test_github_governance.py``).
"""

from __future__ import annotations

import subprocess
import tomllib
from dataclasses import dataclass
from pathlib import Path

import pytest

import prismabib

REPO_ROOT = Path(__file__).parent.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

#: The value ``[tool.hatch.version] fallback-version`` is configured with, for a
#: tree with no ``.git`` at all (a tarball, a ``git archive`` export). It has to
#: be *obviously* not a release: a fallback of, say, ``0.1.0`` would put a
#: plausible release number into a sealed manifest that no commit corresponds to.
_NO_GIT_VERSION = "0+unknown"

#: The shortest abbreviated commit hash any of this is willing to treat as
#: identifying a commit. Guards against an empty local segment matching every
#: SHA by accident (``"abc".startswith("")`` is ``True``).
_MIN_ABBREV = 7


@dataclass(frozen=True)
class _Provenance:
    """What a version string claims about where it came from.

    Compared instead of the raw string so these tests pin the *provenance*
    rather than setuptools-scm's exact spelling of a development version --
    the spelling is a detail of a build-time dependency, while "which release
    is this descended from" and "does it pin the commit" are the properties
    §3.6.4 actually requires.

    Attributes:
        release: The released version this build descends from, with no
            ``.postN.devN`` suffix; ``"0"`` when no tag is reachable.
        identifies_commit: Whether the version's local segment pins the
            commit the package was built from.
    """

    release: str
    identifies_commit: bool


def _git(*args: str) -> str | None:
    """Run one ``git`` command in the repo, or return ``None`` if it fails.

    Args:
        *args: Arguments after ``git``.

    Returns:
        Stripped stdout, or ``None`` when git is unavailable or the command
        failed (e.g. ``describe`` in a clone with no tags).
    """
    try:
        completed = subprocess.run(
            ["git", *args],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:  # pragma: no cover -- only when git is not installed at all
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def _provenance_of_version(version: str, *, head: str | None) -> _Provenance:
    """Read a PEP 440 version string as a provenance claim.

    Args:
        version: The version to read, e.g. ``0.5.0.post1.dev2+g696a64c4e.d20260826``.
        head: The full SHA the package should have been built from, or ``None``
            when there is no git to ask.

    Returns:
        The claim ``version`` makes.
    """
    public, _, local = version.partition("+")
    release = public.split(".post")[0].split(".dev")[0]
    identifies = any(
        part.startswith("g")
        and len(part) > _MIN_ABBREV
        and head is not None
        and head.startswith(part[1:])
        for part in local.split(".")
    )
    return _Provenance(release=release, identifies_commit=identifies)


def _provenance_of_checkout() -> _Provenance:
    """The provenance claim this checkout requires a correctly derived version to make.

    Returns:
        For a checkout with a reachable tag, that tag (minus its ``v``) and
        whether the commit must be pinned (it must whenever HEAD is not exactly
        that tag, or the tree is dirty). For a tagless checkout -- a shallow CI
        clone, which is what ``actions/checkout`` produces by default --
        ``"0"`` and a pinned commit, because a version that cannot name a
        release must not invent one. With no ``.git``, ``"0"`` and no commit.
    """
    if not (REPO_ROOT / ".git").exists():
        return _Provenance(release="0", identifies_commit=False)

    described = _git("describe", "--tags", "--long", "--match", "*[0-9]*")
    if described is None:
        return _Provenance(release="0", identifies_commit=True)

    tag, distance, _node = described.rsplit("-", 2)
    dirty = bool(_git("status", "--porcelain"))
    return _Provenance(
        release=tag.removeprefix("v"),
        identifies_commit=int(distance) > 0 or dirty,
    )


@pytest.mark.unit
def test_package__imports__exposes_version() -> None:
    assert isinstance(prismabib.__version__, str)
    assert prismabib.__version__ != ""


@pytest.mark.integration
def test_version__installed_package__matches_the_git_checkout_it_was_built_from() -> None:
    head = _git("rev-parse", "HEAD")

    actual = _provenance_of_version(prismabib.__version__, head=head)

    assert actual == _provenance_of_checkout(), (
        f"prismabib.__version__ is {prismabib.__version__!r}, which does not describe "
        f"this checkout (HEAD {head}). The version is derived from git at build time "
        "([tool.hatch.version] in pyproject.toml); if you are running the suite from an "
        "environment installed before your last commit or tag, re-run it under `uv run`, "
        "which rebuilds the package when the commit or tags change."
    )


@pytest.mark.unit
def test_version__no_git_available__falls_back_to_an_obviously_unreleased_value() -> None:
    with PYPROJECT_PATH.open("rb") as handle:
        pyproject = tomllib.load(handle)

    fallback = pyproject["tool"]["hatch"]["version"]["fallback-version"]

    assert fallback == _NO_GIT_VERSION
    assert _provenance_of_version(fallback, head="0" * 40) == _Provenance(
        release="0", identifies_commit=False
    )


@pytest.mark.unit
def test_pyproject__version__is_derived_from_git_and_never_a_literal() -> None:
    with PYPROJECT_PATH.open("rb") as handle:
        pyproject = tomllib.load(handle)

    assert "version" not in pyproject["project"], (
        "pyproject.toml carries a literal project.version again. It must stay in "
        "`dynamic` and be derived from the git tag: nothing in CONTRIBUTING.md's "
        "Definition of Done or BUILD_PLAN §3.6.4 asks anyone to bump it, so a literal "
        "here goes stale silently -- and it is stamped into sealed, unrewritable Layer 0 "
        "manifests as client_version."
    )
    assert "version" in pyproject["project"]["dynamic"]
    assert pyproject["tool"]["hatch"]["version"]["source"] == "vcs"
