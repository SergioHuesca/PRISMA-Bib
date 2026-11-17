"""The colours the GUI overrides, and the CSS that applies them (ticket #338).

The constants below are the colours the GUI paints itself: the button, chip, placeholder, focus
ring, default button edge and field border colours. Any colour not named here is a Panel or
Bokeh default, and the contrast test reads those defaults from the installed packages. A Panel
widget renders inside its own shadow DOM, so page-level CSS does not reach it: each override is
handed to the widget as a stylesheet (``FIELD_CSS`` for the field controls, ticket #344).
The exception is ``PAGE_CSS``: Panel injects ``pn.config.raw_css`` into every component's own
shadow root, so one setting (installed by ``server.install_page_css``) reaches every button and
tab (ticket #343). Whether a rule here beats the library's own is checked by specificity in
``tests/unit/gui/test_focus_cascade.py``; how it looks is the owner's manual pass (#353).
"""

from __future__ import annotations

# Bokeh success (#5cb85c) and danger (#d9534f) give 2.5:1 and 4.0:1 with white text; these
# darker hues of the same families clear 4.5:1 (WCAG 2.1 1.4.3).
SUCCESS_FILL = "#198754"
SUCCESS_HOVER = "#146c43"
DANGER_FILL = "#b02a37"
DANGER_HOVER = "#842029"
# Choices.js chips (the Query step terms and subject areas) default to white on #00bcd4: 2.3:1.
CHIP_FILL = "#cfe2ff"
CHIP_HIGHLIGHT = "#e7f1ff"
CHIP_TEXT = "#0a58ca"
# Bokeh placeholder grey (#9a9b9d) is 2.8:1 on white.
PLACEHOLDER = "#6c757d"

# Bokeh focus outline (#BBBBBD, 1px dotted) is 1.92:1 on white; default button edge (#f3f3f3)
# is 1.11:1. #111111 is about 18:1, #767676 is 4.5:1 (WCAG 2.1 2.4.7, 1.4.11).
FOCUS_OUTLINE = "#111111"
DEFAULT_EDGE = "#767676"
# Bokeh field border (#BBBBBD) is 1.92:1 on white (1.4.11); the same grey as the button edge.
FIELD_BORDER = "#767676"

BUTTON_CSS = (
    f".bk-btn-success{{background-color:{SUCCESS_FILL};border-color:{SUCCESS_FILL}}}"
    f".bk-btn-success:hover,.bk-active.bk-btn-success"
    f"{{background-color:{SUCCESS_HOVER};border-color:{SUCCESS_HOVER}}}"
    f".bk-btn-danger{{background-color:{DANGER_FILL};border-color:{DANGER_FILL}}}"
    f".bk-btn-danger:hover,.bk-active.bk-btn-danger"
    f"{{background-color:{DANGER_HOVER};border-color:{DANGER_HOVER}}}"
)
# The library's `.choices__inner .choices__item.solid` (3 classes) paints chips; this has 4.
_CHIP = ".choices__inner .choices__list--multiple .choices__item.choices__item"
CHIP_CSS = (
    f"{_CHIP}"
    f"{{background-color:{CHIP_FILL};border-color:{CHIP_TEXT};color:{CHIP_TEXT}}}"
    f"{_CHIP}.is-highlighted"
    f"{{background-color:{CHIP_HIGHLIGHT}}}"
)
PLACEHOLDER_CSS = f".bk-input::placeholder{{color:{PLACEHOLDER};opacity:1}}"

# The outline is built from these variables on each component's :host, so they are set there.
# The gap is not set as a variable: Bokeh's tabs read it, and a positive one would put the ring
# outside the tab, where the header clips it. Tabs keep their inward offset (-5px, named here
# so it cannot change unseen); filled buttons get a positive gap on their own rule.
RING_WIDTH, RING_STYLE, RING_GAP, TAB_GAP = "2px", "solid", "2px", "-5px"
# Panel's ``:host(.solid) .bk-btn`` (3 classes) sets a transparent border on every button, so the
# default button edge repeats that prefix to outrank it (tests/unit/gui/test_focus_cascade.py).
PAGE_CSS = (
    f":host{{--outline-color:{FOCUS_OUTLINE};--outline-width:{RING_WIDTH};"
    f"--outline-style:{RING_STYLE}}}"
    f".bk-btn:focus,.bk-btn:active{{outline-offset:{RING_GAP}}}"
    f".bk-tab:focus,.bk-tab:active{{outline-offset:{TAB_GAP}}}"
    f":host(.solid) .bk-btn.bk-btn-default{{border-color:{DEFAULT_EDGE}}}"
)

# Field controls (ticket #344): text, password, text area, number, select, multi-choice, checkbox
# and the radio buttons. Bokeh declares a focus colour for inputs but no rule reading it was
# found, so the ring is drawn here: the border and the same outline as PAGE_CSS.
_RING = f"{RING_WIDTH} {RING_STYLE} {FOCUS_OUTLINE}"
FIELD_CSS = (
    f".bk-input{{border-color:{FIELD_BORDER}}}"
    f".bk-input:focus{{border-color:{FOCUS_OUTLINE};outline:{_RING};outline-offset:{RING_GAP}}}"
    f".choices__inner{{border-color:{FIELD_BORDER}}}"
    f".choices:focus-within .choices__inner"
    # The dropdown (z-index 10000 in Panel's stylesheet, below the box or flipped above it) covers
    # the ring's bottom or top edge: the focused box paints one step above it. position:relative
    # moves nothing.
    f"{{border-color:{FOCUS_OUTLINE};outline:{_RING};outline-offset:{RING_GAP};"
    f"position:relative;z-index:10001}}"
    f"input[type=checkbox]:focus{{outline:{_RING};outline-offset:{RING_GAP}}}"
    # Radio buttons overlap by 1px (margin-left:-1px): the focused one paints above its
    # neighbours so the ring is not covered; position:relative moves nothing.
    f".bk-btn-group .bk-btn:focus,.bk-btn-group .bk-btn:active"
    f"{{outline:{_RING};outline-offset:{RING_GAP};position:relative;z-index:1}}"
)
