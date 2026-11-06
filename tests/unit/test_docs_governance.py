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
    """Every bracketed heading, `Unreleased` included, newest first (helper, not a test)."""
    return re.findall(r"^## \[([^\]]+)\]", CHANGELOG.read_text(encoding="utf-8"), re.MULTILINE)


def _changelog_definitions() -> dict[str, str]:
    """Every `[version]: url` definition (helper, not a test)."""
    text = CHANGELOG.read_text(encoding="utf-8")
    pairs = re.findall(r"^\[([^\]]+)\]: (\S+)$", text, re.MULTILINE)
    return dict(pairs)


def _expected_definitions() -> dict[str, str]:
    """What the definitions must be, derived from the headings (helper, not a test)."""
    headings = _changelog_versions()
    released = [version for version in headings if version != "Unreleased"]
    expected = {
        current: f"{REPOSITORY_URL}/compare/v{previous}...v{current}"
        for current, previous in pairwise(released)
    }
    expected[released[-1]] = f"{REPOSITORY_URL}/releases/tag/v{released[-1]}"
    if "Unreleased" in headings:
        expected["Unreleased"] = f"{REPOSITORY_URL}/compare/v{released[0]}...HEAD"
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


@pytest.mark.unit
def test_changelog__bare_unreleased_heading__is_checked_against_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `## [Unreleased]` heading has no date, so it must still be checked.

    The heading pattern once required a date, which hid this heading from both
    tests: its link could point at any range. Its definition must compare the
    newest release with `HEAD`.
    """
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(
        "## [Unreleased]\n\n## [1.0.0] — 2026-09-09\n\n"
        f"[Unreleased]: {REPOSITORY_URL}/compare/v1.0.0...HEAD\n"
        f"[1.0.0]: {REPOSITORY_URL}/releases/tag/v1.0.0\n",
        encoding="utf-8",
    )
    monkeypatch.setitem(globals(), "CHANGELOG", changelog)

    assert _changelog_definitions() == _expected_definitions()
    assert _expected_definitions()["Unreleased"] == f"{REPOSITORY_URL}/compare/v1.0.0...HEAD"


#: Wording that denied the opt-in field-level cascade (ADR 0041 Decision 10).
#: ``no OpenAlex,`` (with the comma) is the "no OpenAlex, no Crossref" list;
#: "has no OpenAlex id" and "no OpenAlex topics" are true statements and stay.
#: Matching is case-insensitive over whitespace-normalised text, so a line
#: break inside a phrase does not hide it.
STALE_PHRASES = (
    "never degrades",
    "refuses to degrade",
    "will not degrade",
    "no openalex,",
    "scopus only",
)


def test_public_text__denying_the_field_level_cascade__is_absent() -> None:
    """Docs and source do not use the old wording about the field-level cascade.

    The frozen ADRs and BUILD_PLAN.md quote the old wording and are not scanned.
    """
    docs = list((REPO_ROOT / "docs").rglob("*.md"))
    sources = list((REPO_ROOT / "src" / "prismabib").rglob("*.py"))
    assert docs
    assert sources
    hits = [
        f"{path.relative_to(REPO_ROOT)}: {phrase!r}"
        for path in (*docs, *sources)
        if ADR_DIR not in path.parents
        for text in [" ".join(path.read_text(encoding="utf-8").lower().split())]
        for phrase in STALE_PHRASES
        if phrase in text
    ]
    assert hits == []


#: Prose says "records" or "included studies"; "corpus" names the code handle
#: and so lives only in code. These are the literal names that are not prose:
#: (page, line text stripped) -> why the word stays.
CORPUS_ALLOWED: dict[tuple[str, str], str] = {
    (
        "docs/evaluation/run-card.md",
        (
            "**T7.** Copy the flow counts you have now. Delete only the file store\\corpus.duckdb"
            " in your project folder. Rebuild the"
        ),
    ): "stored file name, exactly as the software names it",
    (
        "docs/evaluation/tasks.md",
        (
            "> Copy the flow counts you have now. Delete only the file store\\corpus.duckdb"
            " in your project folder. Rebuild the store,"
        ),
    ): "stored file name, exactly as the software names it",
    (
        "docs/how-to/figure-gallery.md",
        "Venues, Keywords, Network, Taxonomy, Corpus browser) all read the same",
    ): "interface tab name, exactly as the interface shows it",
}

_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_INLINE_CODE = re.compile(r"(`+).+?\1")
_LINK_TARGET = re.compile(r"\]\([^)]*\)")


def _prose_pages(root: Path) -> list[Path]:
    """README and the docs pages the word is banned from (helper, not a test).

    ADRs and graph reports are exempt; the Glossary and Changelog sit at the
    repository root, so they are never scanned.
    """
    docs = sorted(
        p
        for p in (root / "docs").rglob("*.md")
        if root / "docs" / "architecture" / "adr" not in p.parents and "graphify-out" not in p.parts
    )
    return [root / "README.md", *docs]


def _corpus_problems(root: Path, allowed: dict[tuple[str, str], str]) -> list[str]:
    """Every misuse of "corpus" in prose, as one message each (helper, not a test).

    Fenced code, inline code and link targets are stripped first; indented
    text, admonition titles and HTML comments stay prose. An allow-list entry
    that excused nothing is itself a problem.
    """
    problems: list[str] = []
    used: set[tuple[str, str]] = set()
    for page in _prose_pages(root):
        name = page.relative_to(root).as_posix()
        fence = ""
        for number, line in enumerate(page.read_text(encoding="utf-8").splitlines(), 1):
            opener = _FENCE.match(line)
            if fence:
                if opener and opener[1][0] == fence[0] and len(opener[1]) >= len(fence):
                    fence = ""
                continue
            if opener:
                fence = opener[1]
                continue
            prose = _LINK_TARGET.sub("]", _INLINE_CODE.sub("", line))
            if re.search("corpus", prose, re.IGNORECASE):
                if (name, line.strip()) in allowed:
                    used.add((name, line.strip()))
                else:
                    problems.append(f"{name}:{number}: {line.strip()!r}")
        if fence:
            problems.append(f"{name}: unclosed code fence {fence!r}")
    problems += [f"stale allow-list entry: {key!r}" for key in allowed if key not in used]
    return problems


@pytest.mark.unit
def test_docs_prose__uses_the_word_corpus__only_where_allowed() -> None:
    """Prose says "records" or "included studies", not "corpus" (the Glossary's rule)."""
    assert _corpus_problems(REPO_ROOT, CORPUS_ALLOWED) == []


@pytest.mark.unit
def test_corpus_check__on_a_small_tree__reports_each_failure_mode(tmp_path: Path) -> None:
    """A violation names page, line and text; a stray fence names the page; a stale entry fails."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "README.md").write_text(
        "Fine `corpus` and [x](corpus.md)\n```\ncorpus\n```\nthe Corpus here\n", encoding="utf-8"
    )
    (tmp_path / "docs" / "open.md").write_text("ok\n~~~\ncorpus\n", encoding="utf-8")
    (tmp_path / "GLOSSARY.md").write_text("corpus\n", encoding="utf-8")

    problems = _corpus_problems(tmp_path, {("README.md", "gone"): "stale"})

    assert problems == [
        "README.md:5: 'the Corpus here'",
        "docs/open.md: unclosed code fence '~~~'",
        "stale allow-list entry: ('README.md', 'gone')",
    ]
