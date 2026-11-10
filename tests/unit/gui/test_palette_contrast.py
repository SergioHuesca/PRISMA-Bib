"""WCAG 2.1 contrast of every colour pair the GUI paints (ticket #338).

Each pair is a foreground and background the screens render: the overrides in
``prismabib.gui.palette``, plus the Panel and Bokeh defaults the screens rely on. Text needs
4.5:1 (1.4.3); non-text parts such as borders and focus indicators need 3:1 (1.4.11). A default
is checked against the installed package file that declares it, so an upgrade that moves a
default fails here.

Not proved: what a browser paints (each pair is also checked by hand, see the audit note),
anti-aliasing, and the browser focus ring. Disabled controls are exempt (1.4.3).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

pytest.importorskip("panel")

import bokeh
import panel

from prismabib.gui import palette

pytestmark = pytest.mark.unit

TEXT, NON_TEXT = 4.5, 3.0
WHITE = "#ffffff"
# Browser defaults that no installed package declares.
LINK_BLUE = "#0000ee"
CHECKBOX_EDGE = "#767676"
# Panel 1.9 Native design defaults (panel/theme/css/default.css).
PANEL_TEXT, PANEL_SURFACE, PANEL_ON_SURFACE = "#212529", "#f3f3f3", "#1a1d21"
PANEL_PRIMARY, PANEL_ON_PRIMARY = "#0072b5", "#ffffff"
# Bokeh 3.9 default theme (bokeh/server/static/js/bokeh.js).
BOKEH_TEXT, BOKEH_BORDER, BOKEH_OUTLINE = "#18191D", "#BBBBBD", "#BBBBBD"
BOKEH_FOCUS = "#65A2CB"
# Panel progress bar (panel/dist/css/progress.css).
PROGRESS_FILL, PROGRESS_TRACK = "#4169e1", "#f5f5f5"


def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _luminance(colour: str) -> float:
    """WCAG 2.1 relative luminance of a six-digit hex colour."""
    hex_ = colour.lstrip("#")
    red, green, blue = (int(hex_[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _channel(red) + 0.7152 * _channel(green) + 0.0722 * _channel(blue)


def contrast_ratio(one: str, other: str) -> float:
    """WCAG 2.1 contrast ratio, from 1 to 21."""
    lighter, darker = sorted((_luminance(one), _luminance(other)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@dataclass(frozen=True)
class Pair:
    role: str
    fg: str
    bg: str
    minimum: float
    source: str
    follow_up: str | None = None  # set while the pair fails: the test is an xfail until fixed


PAIRS: tuple[Pair, ...] = (
    Pair("page text", PANEL_TEXT, WHITE, TEXT, "Panel --panel-on-background-color on page"),
    Pair("tab label, input text", BOKEH_TEXT, WHITE, TEXT, "Bokeh --color on --background-color"),
    Pair("link text", LINK_BLUE, WHITE, TEXT, "browser default, GUI sets no link colour"),
    Pair("primary button label", PANEL_ON_PRIMARY, PANEL_PRIMARY, TEXT, "Panel solid primary"),
    Pair("primary button fill", PANEL_PRIMARY, WHITE, NON_TEXT, "Panel solid primary"),
    Pair("default button label", PANEL_ON_SURFACE, PANEL_SURFACE, TEXT, "Panel solid default"),
    Pair(
        "default button edge",
        PANEL_SURFACE,
        WHITE,
        NON_TEXT,
        "Panel solid default fill",
        follow_up="F4: default button edge (1.4.11; the label carries the meaning)",
    ),
    Pair("success button label", WHITE, palette.SUCCESS_FILL, TEXT, "palette override"),
    Pair("success button label, hover", WHITE, palette.SUCCESS_HOVER, TEXT, "palette override"),
    Pair("success button fill", palette.SUCCESS_FILL, WHITE, NON_TEXT, "palette override"),
    Pair("danger button label", WHITE, palette.DANGER_FILL, TEXT, "palette override"),
    Pair("danger button label, hover", WHITE, palette.DANGER_HOVER, TEXT, "palette override"),
    Pair("danger button fill", palette.DANGER_FILL, WHITE, NON_TEXT, "palette override"),
    Pair("chip label", palette.CHIP_TEXT, palette.CHIP_FILL, TEXT, "palette override"),
    Pair("chip label, highlighted", palette.CHIP_TEXT, palette.CHIP_HIGHLIGHT, TEXT, "palette"),
    Pair("chip edge", palette.CHIP_TEXT, WHITE, NON_TEXT, "palette override"),
    Pair("placeholder text", palette.PLACEHOLDER, WHITE, TEXT, "palette override"),
    Pair(
        "input border",
        BOKEH_BORDER,
        WHITE,
        NON_TEXT,
        "Bokeh --border-color on inputs",
        follow_up="F1: input borders (1.4.11)",
    ),
    Pair(
        "input focus border",
        BOKEH_FOCUS,
        WHITE,
        NON_TEXT,
        "Bokeh --input-focus-border-color",
        follow_up="F2: focus indicator on inputs (2.4.7, 1.4.11)",
    ),
    Pair(
        "button and tab focus outline",
        BOKEH_OUTLINE,
        WHITE,
        NON_TEXT,
        "Bokeh --outline (dotted 1px)",
        follow_up="F3: focus indicator on buttons and tabs (2.4.7, 1.4.11)",
    ),
    Pair("progress fill", PROGRESS_FILL, PROGRESS_TRACK, NON_TEXT, "Panel progress.css"),
    Pair("checkbox edge", CHECKBOX_EDGE, WHITE, NON_TEXT, "browser default checkbox"),
)

# Each default as the installed package declares it (whitespace and case ignored).
DEFAULT_FILES: tuple[tuple[Path, tuple[str, ...]], ...] = (
    (
        Path(bokeh.__file__).parent / "server/static/js/bokeh.js",
        (
            "--color:#18191d",
            "--background-color:#fff",
            "--border-color:#bbbbbd",
            "--outline-color:#bbbbbd",
            "--input-focus-border-color:#65a2cb",
            "--placeholder-color:#9a9b9d",
            "--inverted-color:#fff",
            "--success:#5cb85c",
            "--danger:#d9534f",
        ),
    ),
    (
        Path(panel.__file__).parent / "theme/css/default.css",
        (
            "--panel-background-color:#ffffff",
            "--panel-on-background-color:#212529",
            "--panel-primary-color:#0072b5",
            "--panel-on-primary-color:#ffffff",
            "--panel-surface-color:#f3f3f3",
            "--panel-on-surface-color:#1a1d21",
        ),
    ),
    (
        Path(panel.__file__).parent / "dist/css/progress.css",
        ("color:royalblue", "background-color:whitesmoke"),
    ),
)


def _param(pair: Pair) -> object:
    marks = [pytest.mark.xfail(strict=True, reason=pair.follow_up)] if pair.follow_up else []
    return pytest.param(pair, id=pair.role, marks=marks)


def test_contrast_ratio__known_values() -> None:
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0)
    assert contrast_ratio("#767676", "#ffffff") == pytest.approx(4.54, abs=0.01)


@pytest.mark.parametrize("pair", [_param(p) for p in PAIRS])
def test_pair__meets_the_wcag_minimum(pair: Pair) -> None:
    assert contrast_ratio(pair.fg, pair.bg) >= pair.minimum, pair


def test_palette_constants__each_one_is_in_a_pair() -> None:
    used = {colour.lower() for p in PAIRS for colour in (p.fg, p.bg)}
    declared = {
        value.lower()
        for name, value in vars(palette).items()
        if name.isupper() and isinstance(value, str) and value.startswith("#")
    }
    assert declared <= used, declared - used


@pytest.mark.parametrize("path, tokens", DEFAULT_FILES, ids=lambda v: getattr(v, "name", None))
def test_default_tokens__are_declared_by_the_installed_package(
    path: Path, tokens: tuple[str, ...]
) -> None:
    squashed = "".join(path.read_text(encoding="utf-8", errors="replace").split()).lower()
    assert [t for t in tokens if t not in squashed] == [], path
