"""The app page (ticket #318); the home screen is its first screen (ticket #322)."""

from __future__ import annotations

import panel as pn  # widget layer only

from prismabib.gui.home import build_home
from prismabib.gui.settings import build_settings


def build_app() -> pn.viewable.Viewable:
    """One page per browser session."""
    return pn.Tabs(("Home", build_home()), ("Settings", build_settings()))
