"""The app page (ticket #318); the home screen is its first screen (ticket #322)."""

from __future__ import annotations

import panel as pn  # widget layer only

from prismabib.gui.home import build_home


def build_app() -> pn.viewable.Viewable:
    """One page per browser session."""
    return build_home()
