"""The one palette, one typography scale, applied to both Plotly and Matplotlib (BUILD_PLAN §Stage 9).

ADR 0025 Decision 3: the palette is **adopted whole** from the ``dataviz``
skill's reference instance, not chosen by taste and not re-derived. Every
hex value below is transcribed verbatim from that reference; nothing here
re-steps a ramp or invents a ninth categorical hue. Four rules the ADR
states as non-negotiable, enforced by the functions in this module rather
than left to a figure author's judgement:

- **Categorical hues in fixed order, never cycled**, assigned to the
  *entity*, never to its row index (:func:`categorical_colour`).
- **All-pairs forms cap at three categorical slots** -- a network, a
  scatter, small multiples (:data:`ALL_PAIRS_SLOT_CAP`,
  :func:`cluster_colours`).
- **Sequential is one hue, light -> dark**; the choropleth takes the blue
  ramp, never the categorical palette (:func:`sequential_colour`).
- **No dual-axis chart, ever** -- there is deliberately no helper here for
  a second y-axis; ``viz/figures.py`` has nothing to import for one.

**Two colour-blindness metrics, both real (ADR 0025 Decision 6).** BUILD_PLAN's
own test names CIEDE2000; the ``dataviz`` skill's validator (and this
palette's own recorded provenance in the ADR) uses OKLab ΔE. Rather than
silently pick one, :func:`ciede2000` and the deuteranopia simulation in
:func:`simulate_deuteranopia` implement the frozen spec's metric
independently of the skill script, using the same published simulation
model (Machado, Oliveira & Fernandes 2009, severity 1.0) the skill's own
validator cites -- so a palette that passes one metric and fails the other
is a finding this module can actually surface, not a coincidence of
whichever library happened to be imported.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from prismabib.errors import AnalysisError

# ---------------------------------------------------------------------------
# Palette (verbatim from the `dataviz` skill's reference instance)
# ---------------------------------------------------------------------------

#: Eight-slot categorical palette, light mode. Fixed order is the
#: CVD-safety mechanism -- never re-sorted, never cycled past index 7.
CATEGORICAL_LIGHT: tuple[str, ...] = (
    "#2a78d6",  # 1 blue
    "#eb6834",  # 2 orange
    "#1baf7a",  # 3 aqua
    "#eda100",  # 4 yellow
    "#e87ba4",  # 5 magenta
    "#008300",  # 6 green
    "#4a3aa7",  # 7 violet
    "#e34948",  # 8 red
)

#: The same eight hues, stepped for the dark surface -- a selected set,
#: never a programmatic inversion of the light column (ADR 0025 Decision 3).
CATEGORICAL_DARK: tuple[str, ...] = (
    "#3987e5",
    "#d95926",
    "#199e70",
    "#c98500",
    "#d55181",
    "#008300",
    "#9085e9",
    "#e66767",
)

#: Only the first three slots clear the *all-pairs* CVD/normal-vision gate
#: (ADR 0025 Decision 3, measured on the live corpus in the ADR's Context
#: table). A network, scatter, choropleth or small-multiples figure -- any
#: form where two marks can be adjacent regardless of draw order -- must
#: never assign a fourth categorical slot; fold the remainder to
#: :data:`OTHER_COLOUR` instead (:func:`cluster_colours`).
ALL_PAIRS_SLOT_CAP = 3

#: The neutral "Other" bucket colour (ADR 0025 Decision 4): the chart
#: chrome's own muted/axis-label grey, deliberately outside the eight
#: categorical slots so "Other" never impersonates a real series.
OTHER_COLOUR_LIGHT = "#898781"
OTHER_COLOUR_DARK = "#898781"

#: Sequential blue ramp, light -> dark, keyed by step (ADR 0025 Decision 3:
#: "sequential is one hue, light -> dark"). Used for magnitude encodings --
#: the choropleth takes this ramp, never the categorical palette.
SEQUENTIAL_BLUE: dict[int, str] = {
    100: "#cde2fb",
    150: "#b7d3f6",
    200: "#9ec5f4",
    250: "#86b6ef",
    300: "#6da7ec",
    350: "#5598e7",
    400: "#3987e5",
    450: "#2a78d6",
    500: "#256abf",
    550: "#1c5cab",
    600: "#184f95",
    650: "#104281",
    700: "#0d366b",
}

#: The diverging pair's two poles plus its neutral midpoint (light/dark).
DIVERGING_BLUE = SEQUENTIAL_BLUE[450]
DIVERGING_RED_LIGHT = "#e34948"
DIVERGING_RED_DARK = "#e66767"
DIVERGING_MIDPOINT_LIGHT = "#f0efec"
DIVERGING_MIDPOINT_DARK = "#383835"

#: Status palette -- fixed, never themed (same four hexes in both modes).
STATUS_GOOD = "#0ca30c"
STATUS_WARNING = "#fab219"
STATUS_SERIOUS = "#ec835a"
STATUS_CRITICAL = "#d03b3b"

#: Chart chrome and ink, by mode.
SURFACE_LIGHT = "#fcfcfb"
SURFACE_DARK = "#1a1a19"
PAGE_LIGHT = "#f9f9f7"
PAGE_DARK = "#0d0d0d"
TEXT_PRIMARY_LIGHT = "#0b0b0b"
TEXT_PRIMARY_DARK = "#ffffff"
TEXT_SECONDARY_LIGHT = "#52514e"
TEXT_SECONDARY_DARK = "#c3c2b7"
MUTED = "#898781"
GRIDLINE_LIGHT = "#e1e0d9"
GRIDLINE_DARK = "#2c2c2a"
BASELINE_LIGHT = "#c3c2b7"
BASELINE_DARK = "#383835"

#: The system sans stack the skill's typography rule names, for Plotly
#: (browser-rendered, so a font stack is meaningful). Matplotlib has no
#: browser font-stack fallback -- see :data:`MATPLOTLIB_FONT_FAMILY`.
PLOTLY_FONT_FAMILY = 'system-ui, -apple-system, "Segoe UI", sans-serif'

#: Matplotlib ships DejaVu Sans itself, on every platform this project's CI
#: runs (including `full-windows`) -- the one sans-serif family guaranteed
#: present with no OS font-store lookup, and therefore the one choice that
#: keeps an exported SVG byte-stable across machines (this module's whole
#: reason for existing, per ADR 0025's SVG-goldens decision). Naming
#: "Helvetica, Arial" the way `report/flow_diagram.py` does would silently
#: substitute on a machine without either installed, which is a
#: machine-dependent rendering this project has been bitten by before
#: (CLAUDE.md: "watch for machine-dependence").
MATPLOTLIB_FONT_FAMILY = "DejaVu Sans"


@dataclass(frozen=True)
class Palette:
    """One mode's resolved colour set -- what ``viz/figures.py`` actually reads.

    Never constructed with ad hoc hexes; always via :func:`palette_for_mode`.
    """

    mode: str
    categorical: tuple[str, ...]
    other: str
    surface: str
    page: str
    text_primary: str
    text_secondary: str
    muted: str
    gridline: str
    baseline: str
    font_family: str


def palette_for_mode(mode: str, *, backend: str = "matplotlib") -> Palette:
    """Resolve the full themed colour set for one mode.

    Args:
        mode: ``"light"`` or ``"dark"``.
        backend: ``"matplotlib"`` or ``"plotly"`` -- only changes
            :attr:`Palette.font_family` (see :data:`MATPLOTLIB_FONT_FAMILY`).

    Returns:
        The resolved :class:`Palette`.

    Raises:
        AnalysisError: ``mode`` or ``backend`` is not a recognised value --
            a themed figure with an unrecognised mode is a defect, not a
            case to degrade silently on.
    """
    if mode not in ("light", "dark"):
        raise AnalysisError(f"theme mode must be 'light' or 'dark', got {mode!r}")
    if backend not in ("matplotlib", "plotly"):
        raise AnalysisError(f"backend must be 'matplotlib' or 'plotly', got {backend!r}")
    font = MATPLOTLIB_FONT_FAMILY if backend == "matplotlib" else PLOTLY_FONT_FAMILY
    if mode == "light":
        return Palette(
            mode="light",
            categorical=CATEGORICAL_LIGHT,
            other=OTHER_COLOUR_LIGHT,
            surface=SURFACE_LIGHT,
            page=PAGE_LIGHT,
            text_primary=TEXT_PRIMARY_LIGHT,
            text_secondary=TEXT_SECONDARY_LIGHT,
            muted=MUTED,
            gridline=GRIDLINE_LIGHT,
            baseline=BASELINE_LIGHT,
            font_family=font,
        )
    return Palette(
        mode="dark",
        categorical=CATEGORICAL_DARK,
        other=OTHER_COLOUR_DARK,
        surface=SURFACE_DARK,
        page=PAGE_DARK,
        text_primary=TEXT_PRIMARY_DARK,
        text_secondary=TEXT_SECONDARY_DARK,
        muted=MUTED,
        gridline=GRIDLINE_DARK,
        baseline=BASELINE_DARK,
        font_family=font,
    )


def categorical_colour(index: int, palette: Palette) -> str:
    """The fixed-order categorical slot for entity position ``index``.

    Args:
        index: The entity's fixed slot position (0-based) -- assigned once,
            by the entity, never recomputed by rank or filter state (ADR
            0025 Decision 3: "a chart whose colours mean something
            different after a filter change is a chart that lied").
        palette: The resolved :class:`Palette`.

    Returns:
        ``palette.other`` past the eighth slot, rather than cycling or
        generating a ninth hue (the skill's own "Cycling / generating hues
        past 8" anti-pattern).
    """
    if 0 <= index < len(palette.categorical):
        return palette.categorical[index]
    return palette.other


def cluster_colours(cluster_sizes: dict[int, int], palette: Palette) -> dict[int, str]:
    """Cluster -> colour, top-3 by size plus ``"Other"`` (ADR 0025 Decision 4).

    Args:
        cluster_sizes: ``cluster_id -> member count``, over the *drawn*
            subgraph only (the caller passes counts already restricted to
            what a figure actually places -- this function assigns colour,
            it does not decide which nodes are drawn).
        palette: The resolved :class:`Palette`.

    Returns:
        Every cluster id in ``cluster_sizes`` mapped to one of the first
        :data:`ALL_PAIRS_SLOT_CAP` categorical slots (largest three
        clusters, ties broken by cluster id ascending for reproducibility)
        or ``palette.other`` for every other cluster.
    """
    ordered = sorted(cluster_sizes.items(), key=lambda item: (-item[1], item[0]))
    top = {cluster_id for cluster_id, _size in ordered[:ALL_PAIRS_SLOT_CAP]}
    colours: dict[int, str] = {}
    for position, (cluster_id, _size) in enumerate(item for item in ordered if item[0] in top):
        colours[cluster_id] = categorical_colour(position, palette)
    for cluster_id, _size in ordered:
        if cluster_id not in colours:
            colours[cluster_id] = palette.other
    return colours


def cluster_fold_caption(cluster_sizes: dict[int, int]) -> str:
    """ADR 0025 Decision 4's caption sentence: how many communities are shown vs. folded.

    Args:
        cluster_sizes: ``cluster_id -> member count``, over the drawn
            subgraph.

    Returns:
        E.g. ``"3 of 16 communities shown; 13 folded to Other."`` -- the
        exact shape the ADR quotes. For ``<= ALL_PAIRS_SLOT_CAP`` clusters,
        states that nothing was folded, rather than omitting the sentence
        (a caption that is silent about folding on a small graph is not
        distinguishable from one that forgot to check).
    """
    total = len(cluster_sizes)
    shown = min(total, ALL_PAIRS_SLOT_CAP)
    folded = total - shown
    if folded <= 0:
        return f"{shown} of {total} communities shown; none folded to Other."
    return f"{shown} of {total} communities shown; {folded} folded to Other."


def entity_colours(keys: list[str], palette: Palette, *, cap: int | None = None) -> dict[str, str]:
    """Assign every distinct key in ``keys`` a fixed categorical slot, by first appearance.

    The generalisation of :func:`cluster_colours` for figures whose
    category set is not clusters -- figure 4's keyword-evolution stream is
    the caller (BUILD_PLAN figure 4). ``keys`` must already be in the
    engine's own deterministic total order (e.g.
    :func:`prismabib.bibliometrics.keywords.keyword_evolution`'s ``year``,
    ``count`` descending, ``term`` ascending sort) -- this function never
    reorders or ranks its input, only assigns colour by encounter order, so
    a figure calling it performs no aggregation of its own.

    Args:
        keys: Category keys, in the order a caller encountered them (e.g.
            one :class:`~prismabib.bibliometrics.base.AnalysisResult`'s
            ``data`` column, row order preserved).
        palette: The resolved :class:`Palette`.
        cap: How many distinct keys may take a real categorical slot before
            the rest fold to ``palette.other``. Defaults to every slot the
            palette has (adjacent-pair forms -- bars, stacks, lines); pass
            :data:`ALL_PAIRS_SLOT_CAP` for a form where any two marks can be
            adjacent (ADR 0025 Decision 3).

    Returns:
        ``key -> colour`` for every distinct key in ``keys``.
    """
    limit = cap if cap is not None else len(palette.categorical)
    colours: dict[str, str] = {}
    for key in keys:
        if key in colours:
            continue
        if len(colours) < limit:
            colours[key] = categorical_colour(len(colours), palette)
        else:
            colours[key] = palette.other
    return colours


#: The sequential blue ramp as a Plotly ``colorscale`` -- ``[[position, hex],
#: ...]`` with positions spread evenly over ``[0, 1]``, computed once here
#: (module import time, not a figure body) so a choropleth in
#: ``viz/figures.py`` (BUILD_PLAN figure 2) passes this literal straight to
#: ``go.Choropleth(colorscale=...)`` and lets Plotly compute the per-value
#: normalisation against the data's own min/max -- the same "let the
#: rendering library do the numeric work" precedent a histogram's binning
#: already sets, rather than a figure computing ``min()``/``max()`` itself.
_SEQUENTIAL_STEPS = sorted(SEQUENTIAL_BLUE)
SEQUENTIAL_BLUE_COLORSCALE: list[list[object]] = [
    [index / (len(_SEQUENTIAL_STEPS) - 1), SEQUENTIAL_BLUE[step]]
    for index, step in enumerate(_SEQUENTIAL_STEPS)
]


def sequential_colour(value: float, *, vmin: float, vmax: float) -> str:
    """A step of :data:`SEQUENTIAL_BLUE` for one magnitude value (ADR 0025 Decision 3).

    Args:
        value: The magnitude to encode.
        vmin: The series minimum (maps to the lightest step).
        vmax: The series maximum (maps to the darkest step).

    Returns:
        The nearest ramp step's hex. ``vmin == vmax`` (a constant series)
        returns the ramp's midpoint step -- there is no "low" or "high" to
        distinguish.
    """
    steps = sorted(SEQUENTIAL_BLUE)
    if vmax <= vmin:
        return SEQUENTIAL_BLUE[steps[len(steps) // 2]]
    fraction = max(0.0, min(1.0, (value - vmin) / (vmax - vmin)))
    index = round(fraction * (len(steps) - 1))
    return SEQUENTIAL_BLUE[steps[index]]


# ---------------------------------------------------------------------------
# WCAG contrast (S09-AC5: text legibility, both modes)
# ---------------------------------------------------------------------------


def _srgb_to_linear_channel(channel: float) -> float:
    if channel <= 0.04045:
        return channel / 12.92
    return float(((channel + 0.055) / 1.055) ** 2.4)


def hex_to_srgb(hex_colour: str) -> tuple[float, float, float]:
    """``"#rrggbb"`` -> ``(r, g, b)`` in ``[0, 1]``.

    Args:
        hex_colour: A six-hex-digit colour, with or without a leading ``#``.

    Returns:
        The three channels, each in ``[0, 1]``.
    """
    text = hex_colour.strip().lstrip("#")
    return tuple(int(text[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]


def relative_luminance(hex_colour: str) -> float:
    """WCAG relative luminance of one sRGB hex colour."""
    r, g, b = hex_to_srgb(hex_colour)
    lr, lg, lb = (_srgb_to_linear_channel(c) for c in (r, g, b))
    return 0.2126 * lr + 0.7152 * lg + 0.0722 * lb


def contrast_ratio(foreground: str, background: str) -> float:
    """WCAG contrast ratio between two sRGB hex colours, always ``>= 1.0``.

    Args:
        foreground: The text/mark colour.
        background: The surface it sits on.

    Returns:
        ``(L1 + 0.05) / (L2 + 0.05)``, ``L1`` the lighter of the two.
    """
    l_fore = relative_luminance(foreground)
    l_back = relative_luminance(background)
    lighter, darker = max(l_fore, l_back), min(l_fore, l_back)
    return (lighter + 0.05) / (darker + 0.05)


#: BUILD_PLAN's own S09-AC5 threshold: "contrast ratio of text against
#: background >= 4.5:1", for both themes.
TEXT_CONTRAST_MINIMUM = 4.5


def text_is_legible(palette: Palette) -> bool:
    """Whether ``palette``'s primary text colour clears :data:`TEXT_CONTRAST_MINIMUM`.

    Args:
        palette: The resolved :class:`Palette`.

    Returns:
        ``True`` iff ``contrast_ratio(text_primary, surface) >= 4.5``.
    """
    return contrast_ratio(palette.text_primary, palette.surface) >= TEXT_CONTRAST_MINIMUM


# ---------------------------------------------------------------------------
# 300 dpi legibility (S09-AC3): computed from figure size and font spec.
# ---------------------------------------------------------------------------

#: Single-column journal width, a fixed physical constant (not derived from
#: anything computed elsewhere) -- the export target BUILD_PLAN's S09-AC3
#: names ("legible labels at single-column journal width").
SINGLE_COLUMN_WIDTH_IN = 3.5

#: A wider figure for panels that need it (a two-panel figure, a network
#: canvas). A fixed literal, not ``SINGLE_COLUMN_WIDTH_IN * 2`` -- that
#: multiplication would itself be arithmetic if written in
#: ``viz/figures.py``, which is exactly the boundary this constant exists
#: to keep on the theme side of (``viz/figures.py``'s AST scan forbids
#: arithmetic operators outright, not only aggregation).
DOUBLE_COLUMN_WIDTH_IN = 7.0

#: The height every figure in this theme uses -- one fixed value, so no
#: figure has to compute an aspect ratio.
FIGURE_HEIGHT_IN = 4.0

#: Export resolution S09-AC3 names.
EXPORT_DPI = 300

#: The label font size this theme actually uses, in points -- the value
#: `test_export__300dpi_single_column__label_font_size_above_minimum`
#: checks against :func:`min_legible_font_pt`.
LABEL_FONT_PT = 8.0

#: A glyph's stroke must resolve to at least this many *pixels* at the
#: export resolution to stay legible after anti-aliasing -- a commonly
#: cited print-legibility floor for fine detail (hairline rules use the
#: same floor in `report/flow_diagram.py`-adjacent print contexts).
_MIN_STROKE_PX = 2.0

#: A regular-weight sans-serif's stroke width as a fraction of its em size
#: (an approximation from typeface design conventions, not measured from
#: DejaVu Sans specifically -- documented here rather than presented as
#: more precise than it is).
_STROKE_TO_EM_RATIO = 1 / 14


def min_legible_font_pt(dpi: int = EXPORT_DPI) -> float:
    """The smallest point size whose stroke width still resolves at ``dpi``.

    Args:
        dpi: The export resolution.

    Returns:
        ``(min_stroke_px / dpi) / stroke_to_em_ratio * 72`` -- a stroke of
        :data:`_MIN_STROKE_PX` pixels, expressed in points, scaled up by the
        assumed stroke-to-em ratio. Computed, not eyeballed (S09-AC3):
        the underlying constants are documented assumptions, but the
        function is what a caller runs, not a number typed once and
        forgotten.
    """
    stroke_in = _MIN_STROKE_PX / dpi
    em_in = stroke_in / _STROKE_TO_EM_RATIO
    return em_in * 72


def label_font_is_legible_at_export(dpi: int = EXPORT_DPI) -> bool:
    """Whether :data:`LABEL_FONT_PT` clears :func:`min_legible_font_pt` at ``dpi``."""
    return min_legible_font_pt(dpi) <= LABEL_FONT_PT


# ---------------------------------------------------------------------------
# CIEDE2000 under a simulated deuteranopia (BUILD_PLAN's own S09 test)
# ---------------------------------------------------------------------------

#: Machado, Oliveira & Fernandes (2009) deuteranopia transform, severity
#: 1.0, applied in *linear* sRGB -- the same published matrix the `dataviz`
#: skill's own validator uses for its (differently-metric'd) CVD gate; see
#: this module's docstring for why both metrics are computed independently
#: rather than one standing in for the other.
_DEUTAN_MATRIX = (
    (0.367322, 0.860646, -0.227968),
    (0.280085, 0.672501, 0.047413),
    (-0.011820, 0.042940, 0.968881),
)

_D65_WHITE = (0.95047, 1.0, 1.08883)


def simulate_deuteranopia(hex_colour: str) -> str:
    """The Machado-Oliveira-Fernandes (2009) deuteranopia simulation of one colour.

    Args:
        hex_colour: A six-hex-digit sRGB colour.

    Returns:
        The simulated colour, as a hex string.
    """
    r, g, b = hex_to_srgb(hex_colour)
    lr, lg, lb = (_srgb_to_linear_channel(c) for c in (r, g, b))
    sim_linear = tuple(
        sum(_DEUTAN_MATRIX[row][col] * (lr, lg, lb)[col] for col in range(3)) for row in range(3)
    )
    clamped = tuple(max(0.0, min(1.0, channel)) for channel in sim_linear)
    srgb = tuple(_linear_to_srgb_channel(channel) for channel in clamped)
    return "#" + "".join(f"{round(channel * 255):02x}" for channel in srgb)


def _linear_to_srgb_channel(channel: float) -> float:
    channel = max(0.0, min(1.0, channel))
    if channel <= 0.0031308:
        return 12.92 * channel
    return float(1.055 * channel ** (1 / 2.4) - 0.055)


def _srgb_to_xyz(hex_colour: str) -> tuple[float, float, float]:
    r, g, b = hex_to_srgb(hex_colour)
    lr, lg, lb = (_srgb_to_linear_channel(c) for c in (r, g, b))
    x = 0.4124564 * lr + 0.3575761 * lg + 0.1804375 * lb
    y = 0.2126729 * lr + 0.7151522 * lg + 0.0721750 * lb
    z = 0.0193339 * lr + 0.1191920 * lg + 0.9503041 * lb
    return x, y, z


def _f_lab(t: float) -> float:
    delta = 6 / 29
    if t > delta**3:
        return float(t ** (1 / 3))
    return t / (3 * delta**2) + 4 / 29


def hex_to_lab(hex_colour: str) -> tuple[float, float, float]:
    """CIE L*a*b* (D65 white point) of one sRGB hex colour."""
    x, y, z = _srgb_to_xyz(hex_colour)
    xn, yn, zn = _D65_WHITE
    fx, fy, fz = _f_lab(x / xn), _f_lab(y / yn), _f_lab(z / zn)
    lightness = 116 * fy - 16
    a_axis = 500 * (fx - fy)
    b_axis = 200 * (fy - fz)
    return lightness, a_axis, b_axis


def ciede2000(lab1: tuple[float, float, float], lab2: tuple[float, float, float]) -> float:
    """The CIEDE2000 colour-difference formula (Sharma, Wu & Dalal 2005).

    Args:
        lab1: The first colour's ``(L*, a*, b*)``.
        lab2: The second colour's ``(L*, a*, b*)``.

    Returns:
        The CIEDE2000 ``ΔE00`` distance, ``>= 0``.
    """
    l1, a1, b1 = lab1
    l2, a2, b2 = lab2

    c1 = math.hypot(a1, b1)
    c2 = math.hypot(a2, b2)
    c_bar = (c1 + c2) / 2

    g = 0.5 * (1 - math.sqrt(c_bar**7 / (c_bar**7 + 25**7))) if c_bar else 0.0
    a1_prime = (1 + g) * a1
    a2_prime = (1 + g) * a2

    c1_prime = math.hypot(a1_prime, b1)
    c2_prime = math.hypot(a2_prime, b2)

    def _hue_angle(a_prime: float, b: float) -> float:
        if a_prime == 0 and b == 0:
            return 0.0
        return math.degrees(math.atan2(b, a_prime)) % 360

    h1_prime = _hue_angle(a1_prime, b1)
    h2_prime = _hue_angle(a2_prime, b2)

    delta_l_prime = l2 - l1
    delta_c_prime = c2_prime - c1_prime

    if c1_prime * c2_prime == 0:
        delta_h_prime_deg = 0.0
    else:
        diff = h2_prime - h1_prime
        if diff > 180:
            diff -= 360
        elif diff < -180:
            diff += 360
        delta_h_prime_deg = diff
    delta_h_prime = (
        2 * math.sqrt(c1_prime * c2_prime) * math.sin(math.radians(delta_h_prime_deg) / 2)
    )

    l_bar_prime = (l1 + l2) / 2
    c_bar_prime = (c1_prime + c2_prime) / 2

    if c1_prime * c2_prime == 0:
        h_bar_prime = h1_prime + h2_prime
    else:
        diff = abs(h1_prime - h2_prime)
        total = h1_prime + h2_prime
        if diff <= 180:
            h_bar_prime = total / 2
        elif total < 360:
            h_bar_prime = (total + 360) / 2
        else:
            h_bar_prime = (total - 360) / 2

    t = (
        1
        - 0.17 * math.cos(math.radians(h_bar_prime - 30))
        + 0.24 * math.cos(math.radians(2 * h_bar_prime))
        + 0.32 * math.cos(math.radians(3 * h_bar_prime + 6))
        - 0.20 * math.cos(math.radians(4 * h_bar_prime - 63))
    )

    delta_theta = 30 * math.exp(-(((h_bar_prime - 275) / 25) ** 2))
    r_c = 2 * math.sqrt(c_bar_prime**7 / (c_bar_prime**7 + 25**7)) if c_bar_prime else 0.0
    s_l = 1 + (0.015 * (l_bar_prime - 50) ** 2) / math.sqrt(20 + (l_bar_prime - 50) ** 2)
    s_c = 1 + 0.045 * c_bar_prime
    s_h = 1 + 0.015 * c_bar_prime * t
    r_t = -math.sin(math.radians(2 * delta_theta)) * r_c

    k_l = k_c = k_h = 1.0
    term_l = delta_l_prime / (k_l * s_l)
    term_c = delta_c_prime / (k_c * s_c)
    term_h = delta_h_prime / (k_h * s_h)

    return math.sqrt(term_l**2 + term_c**2 + term_h**2 + r_t * term_c * term_h)


#: BUILD_PLAN's own wording: "pairwise CIEDE2000 distance under a
#: deuteranopia simulation exceeds a threshold". 8.0 mirrors the `dataviz`
#: skill's own OKLab CVD target (ADR 0025 Decision 6 records both metrics
#: rather than picking one) -- CIEDE2000 and OKLab ΔE are on different
#: numeric scales in general, but both were designed so that "~1 unit" is
#: near a just-noticeable difference, which is why the same target is a
#: defensible reading of "a threshold" absent a BUILD_PLAN-specified number.
CIEDE2000_DEUTERANOPIA_THRESHOLD = 8.0


def categorical_palette_deuteranopia_distances(
    palette: tuple[str, ...] = CATEGORICAL_LIGHT,
) -> list[tuple[int, int, float]]:
    """Adjacent-pair CIEDE2000 distances under simulated deuteranopia.

    Args:
        palette: The categorical hues to check, in slot order.

    Returns:
        ``(slot_i, slot_j, delta_e00)`` for every adjacent pair
        ``(0, 1), (1, 2), ...`` -- adjacent, not all-pairs, matching every
        stacked/bar/line use of the categorical palette (ADR 0025 Decision
        3; the all-pairs cap in :data:`ALL_PAIRS_SLOT_CAP` is the separate,
        stricter rule for network/scatter/choropleth forms).
    """
    simulated = [hex_to_lab(simulate_deuteranopia(colour)) for colour in palette]
    return [
        (index, index + 1, ciede2000(simulated[index], simulated[index + 1]))
        for index in range(len(simulated) - 1)
    ]


__all__ = [
    "ALL_PAIRS_SLOT_CAP",
    "CATEGORICAL_DARK",
    "CATEGORICAL_LIGHT",
    "CIEDE2000_DEUTERANOPIA_THRESHOLD",
    "DIVERGING_BLUE",
    "DIVERGING_MIDPOINT_DARK",
    "DIVERGING_MIDPOINT_LIGHT",
    "DIVERGING_RED_DARK",
    "DIVERGING_RED_LIGHT",
    "DOUBLE_COLUMN_WIDTH_IN",
    "EXPORT_DPI",
    "FIGURE_HEIGHT_IN",
    "LABEL_FONT_PT",
    "MATPLOTLIB_FONT_FAMILY",
    "OTHER_COLOUR_DARK",
    "OTHER_COLOUR_LIGHT",
    "PLOTLY_FONT_FAMILY",
    "SEQUENTIAL_BLUE",
    "SEQUENTIAL_BLUE_COLORSCALE",
    "SINGLE_COLUMN_WIDTH_IN",
    "STATUS_CRITICAL",
    "STATUS_GOOD",
    "STATUS_SERIOUS",
    "STATUS_WARNING",
    "TEXT_CONTRAST_MINIMUM",
    "Palette",
    "categorical_colour",
    "categorical_palette_deuteranopia_distances",
    "ciede2000",
    "cluster_colours",
    "cluster_fold_caption",
    "contrast_ratio",
    "entity_colours",
    "hex_to_lab",
    "hex_to_srgb",
    "label_font_is_legible_at_export",
    "min_legible_font_pt",
    "palette_for_mode",
    "relative_luminance",
    "sequential_colour",
    "simulate_deuteranopia",
    "text_is_legible",
]
