"""The colours the GUI overrides, and the CSS that applies them (ticket #338).

Every other colour the screens paint is a Panel or Bokeh default, and the contrast test reads
those defaults from the installed packages. A Panel widget renders inside its own shadow DOM, so
page-level CSS does not reach it: each override is handed to the widget as a stylesheet.
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
