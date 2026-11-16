"""The colours the GUI overrides, and the CSS that applies them (ticket #338).

Every other colour the screens paint is a Panel or Bokeh default, and the contrast test reads
those defaults from the installed packages. A Panel widget renders inside its own shadow DOM, so
page-level CSS does not reach it: each override is handed to the widget as a stylesheet.
The exception is ``PAGE_CSS``: Panel injects ``pn.config.raw_css`` into every component's own
shadow root, so one setting (installed by ``server.install_page_css``) reaches every button and
tab (ticket #343).
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

BUTTON_CSS = (
    f".bk-btn-success{{background-color:{SUCCESS_FILL};border-color:{SUCCESS_FILL}}}"
    f".bk-btn-success:hover,.bk-active.bk-btn-success"
    f"{{background-color:{SUCCESS_HOVER};border-color:{SUCCESS_HOVER}}}"
    f".bk-btn-danger{{background-color:{DANGER_FILL};border-color:{DANGER_FILL}}}"
    f".bk-btn-danger:hover,.bk-active.bk-btn-danger"
    f"{{background-color:{DANGER_HOVER};border-color:{DANGER_HOVER}}}"
)
CHIP_CSS = (
    ".choices__list--multiple .choices__item"
    f"{{background-color:{CHIP_FILL};border-color:{CHIP_TEXT};color:{CHIP_TEXT}}}"
    ".choices__list--multiple .choices__item.is-highlighted"
    f"{{background-color:{CHIP_HIGHLIGHT}}}"
)
PLACEHOLDER_CSS = f".bk-input::placeholder{{color:{PLACEHOLDER};opacity:1}}"

# The outline is built from these variables on each component's :host, so they are set there;
# the gap is also named on the button rule because it is not known that Bokeh's rule reads it.
RING_WIDTH, RING_STYLE, RING_GAP = "2px", "solid", "2px"
PAGE_CSS = (
    f":host{{--outline-color:{FOCUS_OUTLINE};--outline-width:{RING_WIDTH};"
    f"--outline-style:{RING_STYLE};--outline-offset:{RING_GAP}}}"
    f".bk-btn:focus,.bk-btn:active{{outline-offset:{RING_GAP}}}"
    f".bk-btn-default{{border-color:{DEFAULT_EDGE}}}"
)
