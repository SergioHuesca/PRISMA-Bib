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

import ast
import base64
import binascii
import re
from html.parser import HTMLParser
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


def _evidence_citations(adr: Path) -> list[tuple[str, str]]:
    """The `(path, test_name)` of each `- claim — path::name` line under `## Evidence`."""
    lines = adr.read_text(encoding="utf-8").splitlines()
    stripped = [line.strip() for line in lines]
    assert "## Evidence" in stripped, f"{adr.name} has no '## Evidence' heading"
    citations = []
    for line in lines[stripped.index("## Evidence") + 1 :]:
        if line.startswith("## "):
            break
        if not line.startswith("- "):
            continue
        cited = line.rsplit(" — ", 1)[-1].strip()
        rel, sep, name = cited.partition("::")
        assert sep, f"{adr.name} Evidence line has no 'path::test_name': {line!r}"
        assert name.startswith("test_"), f"{adr.name} Evidence cites a non-test name: {line!r}"
        citations.append((rel, name))
    return citations


@pytest.mark.unit
def test_adr_0046__every_evidence_test__exists() -> None:
    """Each test ADR 0046 cites as evidence is defined where the ADR says it is."""
    adr = ADR_DIR / "0046-undo-and-cancel-rules.md"
    citations = _evidence_citations(adr)
    assert citations, "ADR 0046 has an empty Evidence list"

    missing = []
    for rel, name in citations:
        path = REPO_ROOT / rel
        defined = (
            {
                n.name
                for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
                if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
            }
            if path.is_file()
            else set()
        )
        if name not in defined:
            missing.append(f"{rel}::{name}")
    assert not missing, "ADR 0046 cites tests that do not exist:\n" + "\n".join(missing)


GUIDE = REPO_ROOT / "docs" / "guide" / "prismabib-for-beginners.html"
GUIDE_LINK_PAGE = REPO_ROOT / "docs" / "beginner-guide.md"
GUIDE_SECTIONS = ["part1", "part2", "part3a-setup", "part3a-build", "part3b", "closing"]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: Labels the app builds from a variable: (text the guide shows, template in the source,
#: values filled in). The only place they are listed; nothing is resolved by default.
#: A template wrapped in ``**`` is the bold part of a longer string.
TEMPLATE_LABELS: tuple[tuple[str, str, dict[str, str]], ...] = (
    (
        "Check the size of the OpenAlex search",
        "Check the size of the {name} search",
        {"name": "OpenAlex"},
    ),
    (
        "Check the size of the Scopus search",
        "Check the size of the {name} search",
        {"name": "Scopus"},
    ),
    ("Built.", "**Built.**", {}),
    (
        "Records identified (OpenAlex meta.count)",
        "Records identified ({source})",
        {"source": "OpenAlex meta.count"},
    ),
    (
        "Records identified (Scopus total_results)",
        "Records identified ({source})",
        {"source": "Scopus total_results"},
    ),
    ("Unscreened:", "**Unscreened:**", {}),
    ("Unsure:", "**Unsure:**", {}),
    ("Records:", "**Records:**", {}),
)

_GUIDE_URL_ATTRS = frozenset({"src", "href", "srcset", "action", "formaction", "poster", "data"})
_CDN_TOKENS = ("cdn", "jsdelivr", "unpkg", "googleapis", "gstatic", "cloudflare", "fontawesome")
_GUIDE_VOID = frozenset({"meta", "link", "img", "br", "hr", "input", "source", "col", "area"})
_GUIDE_EXEMPT = frozenset({"strong", "code", "pre", "q", "script", "style"})
_GUIDE_AVOIDED = re.compile(
    r"\b(papers?|works?|entry|entries|corpus|completed run|finished run|vote)\b", re.IGNORECASE
)
_GUIDE_PERSONAL = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]|/home/|/Users/", re.IGNORECASE)


class _GuideParser(HTMLParser):
    """Collects what the guide governance test checks (helper, not a test)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, str | None, bool]] = []  # tag, own lang, prose-exempt
        self.bold: list[tuple[str | None, str]] = []
        self.commands: list[tuple[str | None, str]] = []
        self.images: list[tuple[str | None, dict[str, str | None]]] = []
        self.prose: list[tuple[str | None, str]] = []
        self.sections: dict[str | None, list[str]] = {}
        self.refs: list[str] = []
        self.code_text = ""
        self.bold_text: str | None = None
        self.html_attrs: dict[str, str | None] = {}
        self.has_switch_button = False

    def _lang(self) -> str | None:
        return next((lang for _, lang, _ in reversed(self.stack) if lang), None)

    def _in(self, *tags: str) -> bool:
        return any(tag in tags for tag, _, _ in self.stack)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if tag == "html":
            self.html_attrs = a
        lang = a.get("lang") if tag != "html" else None
        if tag == "button" and a.get("id") == "lang-switch" and a.get("type") == "button":
            self.has_switch_button = True
        for name, value in a.items():
            text = (value or "").strip().lower()
            if name in _GUIDE_URL_ATTRS and not text.startswith("data:"):
                if re.match(r"(https?:|ftp:|//)", text) or any(t in text for t in _CDN_TOKENS):
                    self.refs.append(f"{tag} {name}={text[:60]!r}")
            elif name == "style" and "url(" in text:
                self.refs.append(f"{tag} style={text[:60]!r}")
        if tag == "section" and a.get("id"):
            self.sections.setdefault(self._lang(), []).append(a["id"] or "")
        if tag == "img":
            self.images.append((self._lang(), a))
        if tag == "strong":
            self.bold_text = ""
        if tag not in _GUIDE_VOID:
            exempt = tag in _GUIDE_EXEMPT or "works-note" in (a.get("class") or "")
            self.stack.append((tag, lang, exempt))

    def handle_endtag(self, tag: str) -> None:
        if tag not in [t for t, _, _ in self.stack]:
            self.refs.append(f"unmatched </{tag}>")
            return
        lang = self._lang()
        while self.stack:
            popped = self.stack.pop()[0]
            if popped == "strong" and self.bold_text is not None:
                self.bold.append((lang, self.bold_text.strip()))
                self.bold_text = None
            if popped in {"pre", "code"} and not self._in("pre", "code"):
                self.commands += [
                    (lang, line.strip())
                    for line in self.code_text.splitlines()
                    if line.strip().startswith("prismabib ")
                ]
                self.code_text = ""
            if popped == tag:
                break

    def handle_data(self, data: str) -> None:
        if self.bold_text is not None:
            self.bold_text += data
        if self._in("pre", "code"):
            self.code_text += data
        if self._in("style") and re.search(r"@import|url\(", data):
            self.refs.append("style imports or url()")
        if self._in("script") and re.search(r"https?:|\bimport\s*\(|\bfetch\s*\(|XMLHttp", data):
            self.refs.append("script reaches out")
        if not any(exempt for _, _, exempt in self.stack) and data.strip():
            self.prose.append((self._lang(), data))


def _source_strings() -> tuple[set[str], set[str]]:
    """(exact string literals, every literal and f-string template) of the GUI and the screener."""
    package = REPO_ROOT / "src" / "prismabib"
    files = [*sorted((package / "gui").glob("*.py")), package / "screening" / "ui.py"]
    literals: set[str] = set()
    templates: set[str] = set()
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        pieces = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                templates.add(node.value)
                if id(node) not in pieces:  # a piece of an f-string is not a whole label
                    literals.add(node.value)
            elif isinstance(node, ast.JoinedStr):
                templates.add(
                    "".join(
                        v.value if isinstance(v, ast.Constant) else "{" + ast.unparse(v.value) + "}"
                        for v in node.values
                        if isinstance(v, ast.Constant | ast.FormattedValue)
                    )
                )
    return literals, templates


@pytest.mark.unit
def test_beginner_guide__against_the_app_and_itself__has_no_drift() -> None:
    """The guide's labels, commands, languages and images match the app and each other.

    One function collects every problem so a single run names all of them. It reads files
    as UTF-8 and sorts before comparing; it never imports the GUI library.
    """
    import typer.main

    from prismabib.cli import app

    text = GUIDE.read_text(encoding="utf-8")
    parser = _GuideParser()
    parser.feed(text)
    parser.close()
    problems: list[str] = [f"external reference: {ref}" for ref in parser.refs]
    if parser.stack:
        problems.append(f"unclosed tags: {[t for t, _, _ in parser.stack]}")
    if _GUIDE_PERSONAL.search(text):
        problems.append("a personal path (user folder or home folder) appears in the guide")
    if re.search(r"""["'][A-Za-z0-9+/]{64,}={0,2}["']""", text):
        problems.append("a bare base64 string in quotes (images are data-URI img tags only)")
    if parser.html_attrs.get("lang") != "en" or "es" in (parser.html_attrs.get("class") or ""):
        problems.append("English is not the default language")
    if not parser.has_switch_button:
        problems.append("no <button type=button id=lang-switch>")

    # Bold labels: exact equality with a source literal, or a row of the template table.
    literals, templates = _source_strings()
    resolved: set[str] = set()
    for shown, template, values in TEMPLATE_LABELS:
        if not any(template in t for t in templates):
            problems.append(f"template not in source: {template!r}")
        elif template.strip("*").format(**values) != shown:
            problems.append(f"template {template!r} does not resolve to {shown!r}")
        else:
            resolved.add(shown)
    problems += [
        f"bold label not found in the app source: {label!r} ({lang})"
        for lang, label in parser.bold
        if label not in literals and label not in resolved
    ]

    # Commands: each names a real command, walked down the command tree.
    node0 = typer.main.get_command(app)
    for lang, command in parser.commands:
        node = node0
        for token in command.split()[1:]:
            # Duck-typed: typer vendors click, so isinstance against click.Group is always False.
            subcommands = getattr(node, "commands", None)
            if token.startswith("-") or subcommands is None:
                continue  # ponytail: a root flag's value reads as a name; none is used yet
            if token not in subcommands:
                problems.append(f"command not in the command tree: {command!r} ({lang})")
                break
            node = subcommands[token]

    # Parity and structure of the two language blocks.
    for kind, items in (("bold labels", parser.bold), ("commands", parser.commands)):
        by_lang = {k: sorted({v for lang, v in items if lang == k}) for k in ("en", "es")}
        if by_lang["en"] != by_lang["es"]:
            problems.append(f"{kind} differ between English and Spanish: {by_lang}")
        if any(lang not in ("en", "es") for lang, _ in items):
            problems.append(f"{kind} outside a language block")
    for lang in ("en", "es"):
        found = [s.removesuffix(f"-{lang}") for s in parser.sections.get(lang, [])]
        if found != GUIDE_SECTIONS:
            problems.append(f"{lang} sections {found} != {GUIDE_SECTIONS}")

    # Images: a base64 PNG in a slot outside the language blocks.
    prefix = "data:image/png;base64,"
    for lang, attrs in parser.images:
        name = attrs.get("id") or ""
        src = attrs.get("src") or ""
        try:
            data = base64.b64decode(src.removeprefix(prefix), validate=True)
        except binascii.Error:
            data = b""
        if not re.fullmatch(r"img-[1-8]", name):
            problems.append(f"image id {name!r} is not img-1 to img-8")
        if not src.startswith(prefix) or not data.startswith(PNG_SIGNATURE) or len(data) <= 8:
            problems.append(f"image {name!r} is not a non-empty base64 PNG")
        if lang is not None:
            problems.append(f"image {name!r} sits inside a language block")

    # Glossary terms in prose (bold labels, code, quotes and the works note are exempt).
    problems += [
        f"avoided word {m[1]!r} in prose ({lang}): {chunk.strip()[:50]!r}"
        for lang, chunk in parser.prose
        for m in _GUIDE_AVOIDED.finditer(chunk)
    ]

    # The link page links to the guide, and every relative link on it resolves.
    page = GUIDE_LINK_PAGE.read_text(encoding="utf-8")
    targets = [t.split("#")[0] for t in re.findall(r"\]\(([^)\s]+)\)", page)]
    if "guide/prismabib-for-beginners.html" not in targets:
        problems.append("the link page does not link to guide/prismabib-for-beginners.html")
    problems += [
        f"link page target does not exist: {t}"
        for t in targets
        if t and not t.startswith(("http:", "https:")) and not (GUIDE_LINK_PAGE.parent / t).exists()
    ]

    # Discovery guard: an empty parse must not pass.
    problems += [
        f"the guide parsed to zero {kind}"
        for kind, count in (
            ("bold labels", len(parser.bold)),
            ("commands", len(parser.commands)),
            ("image slots", len(parser.images)),
        )
        if count == 0
    ]

    assert sorted(problems) == [], "\n" + "\n".join(sorted(problems))
