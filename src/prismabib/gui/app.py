"""The empty app page (ticket #318). Later tickets add screens here."""

from __future__ import annotations

import panel as pn  # widget layer only


def build_app() -> pn.viewable.Viewable:
    """One page per browser session."""
    return pn.Column(pn.pane.Markdown("# prismabib\n\nNo project is open yet."))
