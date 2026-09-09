"""The documents that say what is decided must not contradict the repository.

Two records carry claims a reader acts on without being able to check them:
an ADR's ``Status`` says whether a decision is in force, and the changelog's
link definitions say where a release's diff is. Both had drifted --- five
ADRs described shipped, released decisions while still reading ``Proposed``,
and the link block stopped nine releases back --- and neither drift is
visible to anything else in the suite. Rendering the docs does not catch
them: a stale status renders perfectly, and an undefined reference renders as
plain bracketed text.
"""

from __future__ import annotations

import re
from itertools import pairwise
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
ADR_DIR = REPO_ROOT / "docs" / "architecture" / "adr"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"

REPOSITORY_URL = "https://github.com/SergioHuesca/PRISMA-Bib"

#: A decision recorded on ``main`` is one the merge enacted. "Proposed" is a
#: state a branch is in, not a state the merged history can be in, so it is
#: deliberately absent here rather than listed and excluded.
IN_FORCE_STATUSES = frozenset({"Accepted", "Superseded", "Rejected", "Deprecated"})


def _adr_files() -> list[Path]:
    """Every ADR, in number order (helper, not a test)."""
    return sorted(ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md"))


def _status_word(adr: Path) -> str:
    """The first word under an ADR's `## Status` heading (helper, not a test).

    Leading `**` is tolerated because ADR 0006 emphasises its status; a
    missing heading yields `""`, which no status set contains.
    """
    text = adr.read_text(encoding="utf-8")
    found = re.findall(r"^## Status\s*\n\s*\n\**([A-Za-z]+)", text, re.MULTILINE)
    return "".join(found[:1])


def _changelog_versions() -> list[str]:
    """Release headings, newest first (helper, not a test)."""
    return re.findall(r"^## \[([^\]]+)\] —", CHANGELOG.read_text(encoding="utf-8"), re.MULTILINE)


def _changelog_definitions() -> dict[str, str]:
    """Every `[version]: url` definition (helper, not a test)."""
    text = CHANGELOG.read_text(encoding="utf-8")
    pairs = re.findall(r"^\[([^\]]+)\]: (\S+)$", text, re.MULTILINE)
    return dict(pairs)


def _expected_definitions() -> dict[str, str]:
    """What the definitions must be, derived from the headings (helper, not a test)."""
    versions = _changelog_versions()
    expected = {
        current: f"{REPOSITORY_URL}/compare/v{previous}...v{current}"
        for current, previous in pairwise(versions)
    }
    expected[versions[-1]] = f"{REPOSITORY_URL}/releases/tag/v{versions[-1]}"
    return expected


@pytest.mark.unit
def test_adrs__are_discovered__at_all() -> None:
    """Guard the guard: an empty glob would make every ADR test vacuous.

    The path is a literal, so a directory rename or a change to the file
    naming scheme would silently reduce the parametrised tests below to zero
    cases and report green.
    """
    assert len(_adr_files()) >= 27


@pytest.mark.unit
@pytest.mark.parametrize("adr", _adr_files(), ids=lambda p: p.name)
def test_adr__on_main__is_not_still_proposed(adr: Path) -> None:
    """No ADR on `main` may read `Proposed`.

    `main` is the merged state: an ADR reaches it because a pull request
    enacting it was merged. Five ADRs shipped their decisions, were released,
    and still said `Proposed` --- so a reader had no way to tell an in-force
    decision from a suggestion, on documents that govern how the numbers in
    this tool are computed.
    """
    assert _status_word(adr) in IN_FORCE_STATUSES


@pytest.mark.unit
def test_changelog__every_release_heading__has_a_link_definition() -> None:
    """A `## [0.20.0]` heading with no definition renders as literal brackets.

    Markdown does not error on an undefined reference; it prints the source
    text. Nine release headings resolved to nothing and the rendered page
    looked deliberate.
    """
    assert set(_changelog_definitions()) == set(_changelog_versions())


@pytest.mark.unit
def test_changelog__every_definition__spans_from_the_previous_release() -> None:
    """Each release's link must compare against the release before it.

    Asserted against the headings rather than against a list typed here: a
    hand-maintained copy of the version order would agree with itself while
    both drifted. `[Unreleased]` pointed at `v0.12.0...HEAD` through eleven
    subsequent releases, which is how this was found.
    """
    assert _changelog_definitions() == _expected_definitions()
