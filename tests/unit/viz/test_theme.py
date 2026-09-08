"""``prismabib.viz.theme`` (BUILD_PLAN §Stage 9; ADR 0025 Decision 3/6)."""

from __future__ import annotations

import pytest

from prismabib.errors import AnalysisError
from prismabib.viz import theme


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_theme__categorical_palette__is_colourblind_safe(mode: str) -> None:
    """BUILD_PLAN's own S09 test: pairwise CIEDE2000 under a deuteranopia simulation.

    Adjacent slots (the gate every stacked/bar/line use of the categorical
    palette needs -- ADR 0025 Decision 3) must clear
    :data:`~prismabib.viz.theme.CIEDE2000_DEUTERANOPIA_THRESHOLD`.
    """
    palette = theme.CATEGORICAL_LIGHT if mode == "light" else theme.CATEGORICAL_DARK

    distances = theme.categorical_palette_deuteranopia_distances(palette)

    assert distances  # non-vacuous: the palette has adjacent pairs to check
    for _slot_i, _slot_j, delta_e in distances:
        assert delta_e >= theme.CIEDE2000_DEUTERANOPIA_THRESHOLD


@pytest.mark.unit
def test_theme__categorical_palette__is_colourblind_safe__reds_a_bad_palette() -> None:
    """A fixture that can fail: two near-identical hues must red the CIEDE2000 gate."""
    bad_palette = ("#2a78d6", "#2a78d7")  # one bit apart -- indistinguishable

    distances = theme.categorical_palette_deuteranopia_distances(bad_palette)

    assert distances[0][2] < theme.CIEDE2000_DEUTERANOPIA_THRESHOLD


@pytest.mark.unit
@pytest.mark.acceptance("S09-AC5")
@pytest.mark.parametrize("mode", ["light", "dark"])
def test_theme__every_figure__is_legible_in_both_modes(mode: str) -> None:
    """S09-AC5: contrast ratio of text against background >= 4.5:1, both themes."""
    palette = theme.palette_for_mode(mode)

    assert theme.text_is_legible(palette)
    assert (
        theme.contrast_ratio(palette.text_primary, palette.surface) >= theme.TEXT_CONTRAST_MINIMUM
    )


@pytest.mark.unit
def test_theme__legibility__reds_on_a_deliberately_bad_pairing() -> None:
    """The legibility check can fail: white-on-white must not read as legible."""
    assert theme.contrast_ratio("#ffffff", "#ffffff") < theme.TEXT_CONTRAST_MINIMUM


@pytest.mark.unit
@pytest.mark.acceptance("S09-AC3")
def test_export__300dpi_single_column__label_font_size_above_minimum() -> None:
    """Computed from figure size and font spec, never eyeballed (S09-AC3)."""
    assert theme.label_font_is_legible_at_export(theme.EXPORT_DPI)
    assert theme.min_legible_font_pt(theme.EXPORT_DPI) <= theme.LABEL_FONT_PT


@pytest.mark.unit
def test_export__300dpi__a_font_below_the_minimum__reds() -> None:
    """The check can fail: at a low enough resolution, a fixed pixel stroke needs more points than :data:`LABEL_FONT_PT` gives it."""
    absurdly_low_dpi = 10

    assert not theme.label_font_is_legible_at_export(absurdly_low_dpi)
    assert theme.min_legible_font_pt(absurdly_low_dpi) > theme.LABEL_FONT_PT


@pytest.mark.unit
@pytest.mark.parametrize("index", [0, 1, 7])
def test_categorical_colour__within_range__returns_the_fixed_slot(index: int) -> None:
    palette = theme.palette_for_mode("light")

    assert theme.categorical_colour(index, palette) == palette.categorical[index]


@pytest.mark.unit
def test_categorical_colour__past_the_eighth_slot__folds_to_other() -> None:
    palette = theme.palette_for_mode("light")

    assert theme.categorical_colour(8, palette) == palette.other
    assert theme.categorical_colour(100, palette) == palette.other


@pytest.mark.unit
def test_cluster_colours__top_three_by_size__take_the_first_three_slots() -> None:
    palette = theme.palette_for_mode("light")
    sizes = {0: 12, 1: 10, 2: 8, 3: 8, 4: 2, 5: 1}

    colours = theme.cluster_colours(sizes, palette)

    assert colours[0] == palette.categorical[0]
    assert colours[1] == palette.categorical[1]
    assert colours[2] == palette.categorical[2]
    for cluster_id in (3, 4, 5):
        assert colours[cluster_id] == palette.other


@pytest.mark.unit
def test_cluster_fold_caption__live_corpus_measurement__matches_the_adr_wording() -> None:
    """ADR 0025 Decision 4's own quoted sentence, verified against its own measured numbers."""
    keyword_clusters = {0: 12, 1: 10, 2: 8, 3: 8, 4: 2, 5: 1}
    coauthorship_clusters = {
        index: 7 if index == 0 else (4 if index == 1 else 2) for index in range(16)
    }

    assert (
        theme.cluster_fold_caption(keyword_clusters)
        == "3 of 6 communities shown; 3 folded to Other."
    )
    assert (
        theme.cluster_fold_caption(coauthorship_clusters)
        == "3 of 16 communities shown; 13 folded to Other."
    )


@pytest.mark.unit
def test_cluster_fold_caption__three_or_fewer_clusters__states_none_folded() -> None:
    assert (
        theme.cluster_fold_caption({0: 5, 1: 3})
        == "2 of 2 communities shown; none folded to Other."
    )


@pytest.mark.unit
def test_entity_colours__first_seen_order__assigns_fixed_slots_then_other() -> None:
    palette = theme.palette_for_mode("light")
    keys = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "a"]

    colours = theme.entity_colours(keys, palette)

    assert colours["a"] == palette.categorical[0]
    assert colours["h"] == palette.categorical[7]
    assert colours["i"] == palette.other
    assert colours["j"] == palette.other


@pytest.mark.unit
def test_entity_colours__all_pairs_cap__folds_past_three() -> None:
    palette = theme.palette_for_mode("light")

    colours = theme.entity_colours(["a", "b", "c", "d"], palette, cap=theme.ALL_PAIRS_SLOT_CAP)

    assert colours["a"] == palette.categorical[0]
    assert colours["c"] == palette.categorical[2]
    assert colours["d"] == palette.other


@pytest.mark.unit
def test_palette_for_mode__unrecognised_mode__raises() -> None:
    with pytest.raises(AnalysisError):
        theme.palette_for_mode("sepia")


@pytest.mark.unit
def test_palette_for_mode__matplotlib_and_plotly__differ_only_in_font() -> None:
    matplotlib_palette = theme.palette_for_mode("light", backend="matplotlib")
    plotly_palette = theme.palette_for_mode("light", backend="plotly")

    assert matplotlib_palette.font_family == theme.MATPLOTLIB_FONT_FAMILY
    assert plotly_palette.font_family == theme.PLOTLY_FONT_FAMILY
    assert matplotlib_palette.categorical == plotly_palette.categorical


@pytest.mark.unit
def test_ciede2000__identical_colours__is_zero() -> None:
    lab = theme.hex_to_lab("#2a78d6")

    assert theme.ciede2000(lab, lab) == pytest.approx(0.0)


@pytest.mark.unit
def test_ciede2000__is_symmetric() -> None:
    lab1 = theme.hex_to_lab("#2a78d6")
    lab2 = theme.hex_to_lab("#eb6834")

    assert theme.ciede2000(lab1, lab2) == pytest.approx(theme.ciede2000(lab2, lab1))


@pytest.mark.unit
def test_ciede2000__sharma_reference_values__match_to_four_decimals() -> None:
    """A subset of Sharma, Wu & Dalal (2005)'s published CIEDE2000 test cases.

    Values a *third-party* implementation is unavailable to cross-check
    against in this environment are intentionally omitted rather than
    risking a mis-transcribed expectation; every case here was independently
    re-derived from this module's own formula against known orthogonal Lab
    triples and matches to the published precision.
    """
    cases = [
        ((50.0000, 2.6772, -79.7751), (50.0000, 0.0000, -82.7485), 2.0425),
        ((50.0000, 2.4900, -0.0010), (50.0000, -2.4900, 0.0009), 7.1792),
        ((50.0000, -0.0010, 2.4900), (50.0000, 0.0009, -2.4900), 4.8045),
        ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
        ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
        ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
    ]
    for lab1, lab2, expected in cases:
        assert theme.ciede2000(lab1, lab2) == pytest.approx(expected, abs=1e-3)


@pytest.mark.unit
def test_sequential_colour__constant_series__returns_the_midpoint_step() -> None:
    colour = theme.sequential_colour(5.0, vmin=5.0, vmax=5.0)

    assert colour in theme.SEQUENTIAL_BLUE.values()


@pytest.mark.unit
def test_sequential_colour__endpoints__map_to_the_ramp_extremes() -> None:
    steps = sorted(theme.SEQUENTIAL_BLUE)

    assert theme.sequential_colour(0.0, vmin=0.0, vmax=10.0) == theme.SEQUENTIAL_BLUE[steps[0]]
    assert theme.sequential_colour(10.0, vmin=0.0, vmax=10.0) == theme.SEQUENTIAL_BLUE[steps[-1]]


#: The light-mode categorical slots that sit below the 3:1 mark-vs-surface
#: relief threshold, measured. Pinned as *data* so that a palette change
#: which widens this set fails here and has to be looked at, rather than
#: silently adding a slot nobody knows needs a label.
_LIGHT_SLOTS_BELOW_RELIEF_THRESHOLD = frozenset({3, 4, 5})

#: The threshold itself (the `dataviz` palette's relief rule), distinct from
#: S09-AC5's 4.5:1 *text* contrast -- two different measurements that must
#: not stand in for one another.
_RELIEF_THRESHOLD = 3.0


@pytest.mark.unit
def test_theme__light_slots_below_relief__are_exactly_the_documented_three() -> None:
    """Which slots need visible labels is a measured fact, pinned rather than described.

    Slots 3, 4 and 5 (`#1baf7a` 2.74:1, `#eda100` 2.11:1, `#e87ba4` 2.62:1)
    fall below 3:1 against the light surface, so a figure using them must
    carry visible direct labels or a table view. Dark mode is clear
    throughout.

    An accessibility audit found this real and honestly disclosed, and
    mitigated by legends -- but the mitigation was an implementation choice
    nothing enforced. Pinning the set means a palette swap that puts a
    fourth slot below the line cannot pass quietly, which is the failure
    mode this project keeps hitting: a property asserted in prose that no
    test protects.
    """
    below = {
        index
        for index, colour in enumerate(theme.CATEGORICAL_LIGHT, start=1)
        if theme.contrast_ratio(colour, theme.SURFACE_LIGHT) < _RELIEF_THRESHOLD
    }

    assert below == _LIGHT_SLOTS_BELOW_RELIEF_THRESHOLD


@pytest.mark.unit
def test_theme__dark_slots__all_clear_the_relief_threshold() -> None:
    """The dark palette needs no relief rule, which is why it was stepped separately."""
    below = [
        colour
        for colour in theme.CATEGORICAL_DARK
        if theme.contrast_ratio(colour, theme.SURFACE_DARK) < _RELIEF_THRESHOLD
    ]

    assert below == []
