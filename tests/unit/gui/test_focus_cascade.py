"""Cascade guard for the focus ring and the default button edge (spec #351, review fix).

The installed Panel and Bokeh stylesheets are read, the rules that set the same property on the
same part are found, and CSS specificity is compared with our selector. Who wins a tie depends on
the order of the stylesheets in a component's shadow root (panel/reactive.py): Bokeh's own CSS
comes first, then ``pn.config.raw_css`` (our PAGE_CSS), then Panel's css files such as
button.css, then the per-widget stylesheets. So a Bokeh rule must be strictly higher than ours
to beat it, but a Panel rule wins at EQUAL specificity as well; the two sources are handled
separately below.

What this proves: on the installed versions no library rule that sets the border of a default
button beats ours (strictly higher, or equal for Panel's files), and nothing we set makes a tab ring point outwards (where the header's
``overflow:hidden`` would clip it). What it does not prove: anything a browser paints, rules
the parser below does not model (``!important``, inline styles, rules added by JavaScript), or
rules in versions other than the installed ones. The owner's manual pass (#353) is the gate.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("panel")

import bokeh
import panel

from prismabib.gui import palette

pytestmark = pytest.mark.unit

BOKEH_JS = Path(bokeh.__file__).parent / "server/static/js/bokeh.js"
BUTTON_CSS = Path(panel.__file__).parent / "dist/css/button.css"

Specificity = tuple[int, int, int]
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_PARTS = re.compile(
    r"""
    (?P<id>\#[\w-]+)
  | (?P<host>:host\()
  | (?P<not>:(?:not|is|where|has)\()
  | (?P<element_pseudo>::[\w-]+)
  | (?P<pseudo>:[\w-]+)
  | (?P<attr>\[[^\]]*\])
  | (?P<cls>\.[\w-]+)
  | (?P<tag>[a-zA-Z][\w-]*)
    """,
    re.VERBOSE,
)


def specificity(selector: str) -> Specificity:
    """Specificity of one selector (no commas). ``:host(X)`` counts as a pseudo-class plus X."""
    ids = classes = tags = 0
    pos = 0
    while pos < len(selector):
        m = _PARTS.match(selector, pos)
        if m is None:
            pos += 1
            continue
        kind = m.lastgroup
        pos = m.end()
        if kind in ("host", "not"):
            # The argument is part of the same selector text, so it is counted as it is read;
            # :where() would count 0 and is not modelled (no such rule is used here).
            classes += kind == "host"
            continue
        ids += kind == "id"
        classes += kind in ("pseudo", "attr", "cls")
        tags += kind in ("element_pseudo", "tag")
    return ids, classes, tags


def rules(text: str) -> list[tuple[str, dict[str, str]]]:
    """(selector, declarations) for each flat ``selector{body}``; one entry per comma part."""
    found: list[tuple[str, dict[str, str]]] = []
    for sel, body in _RULE.findall(text):
        decls = {
            k.strip(): v.strip()
            for k, _, v in (d.partition(":") for d in body.split(";"))
            if k.strip()
        }
        found += [(s.strip(), decls) for s in sel.split(",") if s.strip()]
    return found


def _region(marker: str) -> str:
    """The template string of bokeh.js that holds ``marker`` (CSS is embedded in backticks)."""
    js = BOKEH_JS.read_text(encoding="utf-8", errors="replace")
    start = js.index(marker)
    return js[js.rfind("`", 0, start) + 1 : js.index("`", start)]


def _last_compound(selector: str) -> str:
    return re.split(r"[\s>+~]+", selector.strip())[-1]


# (selector, specificity, comes_after_ours): Panel's css files come after PAGE_CSS in the shadow
# root, so they win ties; Bokeh's own CSS comes before it, so it needs a strictly higher one.
LibraryRule = tuple[str, Specificity, bool]


def beating(ours: Specificity, library: list[LibraryRule]) -> list[str]:
    """The library rules that win over a rule of ours with specificity ``ours``."""
    return [
        f"{sel} {spec}" for sel, spec, after in library if spec > ours or (after and spec == ours)
    ]


def _library_default_button_border_rules() -> list[LibraryRule]:
    out: list[LibraryRule] = []
    for css, after_ours in (
        (BUTTON_CSS.read_text(encoding="utf-8"), True),
        (_region(".bk-btn-default"), False),
    ):
        for sel, decls in rules(css):
            last = _last_compound(sel)
            sets_border = decls.keys() & {"border", "border-color"}
            other_kind = re.search(r"\.bk-btn-(?!default|group)", last)
            applies = (".bk-btn-default" in last or last.startswith(".bk-btn")) and not other_kind
            if sets_border and applies and ".outline" not in sel and "disabled" not in sel:
                out.append((sel, specificity(sel), after_ours))
    return out


def _our_rules() -> list[tuple[str, dict[str, str]]]:
    return rules(palette.PAGE_CSS)


def test_default_button_edge__outranks_every_library_border_rule() -> None:
    ours = [
        specificity(sel)
        for sel, decls in _our_rules()
        if decls.get("border-color") == palette.DEFAULT_EDGE and ".bk-btn-default" in sel
    ]
    assert ours, "PAGE_CSS has no rule that sets the default button edge"
    library = _library_default_button_border_rules()
    assert any(after and ".bk-btn" in sel for sel, _, after in library), "Panel's rule not found"
    assert any(not after for _, _, after in library), "Bokeh's rules were not found"
    ours_spec = min(ours)
    losing = beating(ours_spec, library)
    assert losing == [], f"our edge {ours_spec} loses to the library rule(s): {losing}"


def test_beating__a_tie_loses_only_to_a_rule_that_comes_after_ours() -> None:
    ours = (0, 4, 0)
    library: list[LibraryRule] = [
        ("panel tie", (0, 4, 0), True),
        ("bokeh tie", (0, 4, 0), False),
        ("panel lower", (0, 3, 0), True),
        ("bokeh higher", (0, 5, 0), False),
        ("panel higher id", (1, 0, 0), True),
    ]
    assert beating(ours, library) == [
        "panel tie (0, 4, 0)",
        "bokeh higher (0, 5, 0)",
        "panel higher id (1, 0, 0)",
    ]


def test_tab_ring__is_not_pushed_outside_the_tab() -> None:
    tabs = rules(_region("--close-icon-space-away-from-tab-text"))
    lib_offset = [sel for sel, d in tabs if d.get("outline-offset") == "var(--outline-offset)"]
    assert lib_offset == [".bk-tab"], "the library tab rule moved: " + str(lib_offset)
    lib_default = next(d["--outline-offset"] for s, d in tabs if "--outline-offset" in d)
    assert lib_default.startswith("-"), "the library default for tabs is no longer inward"

    def positive(value: str) -> bool:
        return bool(re.match(r"\d", value.strip())) and not value.strip().startswith("0")

    bad = [
        f"{sel}{{{k}:{v}}}"
        for sel, decls in _our_rules()
        for k, v in decls.items()
        if k in ("--outline-offset", "outline-offset")
        and positive(v)
        and (k == "--outline-offset" or ".bk-tab" in sel)
    ]
    assert bad == [], (
        "these push the tab ring outside the tab, where the header's overflow:hidden clips it "
        f"(library rule: .bk-tab{{outline-offset:var(--outline-offset)}}, default {lib_default}): "
        f"{bad}"
    )


def test_radio_button__focused_one_paints_above_its_neighbours() -> None:
    focus = [d for s, d in rules(palette.FIELD_CSS) if s == ".bk-btn-group .bk-btn:focus"]
    assert focus, "no focus rule for the radio buttons"
    assert focus[0].get("position") == "relative" and int(focus[0].get("z-index", "0")) > 0


@pytest.mark.parametrize(
    "selector, expected",
    [
        (".a", (0, 1, 0)),
        (":host(.solid) .bk-btn", (0, 3, 0)),
        (":host(.solid) .bk-btn.bk-btn-default", (0, 4, 0)),
        (".bk-btn:focus, ", (0, 2, 0)),
        ("a#x.b::before", (1, 1, 2)),
        ("input[type=checkbox]:focus", (0, 2, 1)),
    ],
)
def test_specificity__known_values(selector: str, expected: Specificity) -> None:
    assert specificity(selector.strip(" ,")) == expected
