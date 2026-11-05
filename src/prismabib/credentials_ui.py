"""The "Connect Scopus" panel (ADR 0042 Decision 8): a password input over the credentials writer.

It shows only "set" or "not set", writes through :func:`~prismabib.credentials.write_env` (the
one ``.env`` writer the CLI uses), never echoes the value, and clears the input after every
attempt, so the value does not outlive the click in the widget state that a notebook can save.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

import panel as pn  # widget layer only

from prismabib.credentials import ENV_FILENAME, status_lines, write_env
from prismabib.errors import PrismabibError
from prismabib.project import Project
from prismabib.source_mode import CAPABILITY_MODES, effective_mode
from prismabib.sources.scopus import register_secret_for_redaction

KEY = "SCOPUS_API_KEY"


class ConnectScopusPanel:
    """Set the Scopus key. Handlers are plain methods so tests drive them without a browser."""

    def __init__(self, project: Project | None = None, *, env_file: Path | None = None) -> None:
        self.env_file = env_file or Path.cwd() / ENV_FILENAME
        mode = effective_mode(project) if project else None
        self.unused = mode is not None and mode not in CAPABILITY_MODES["scopus_search"]
        title = "Connect Scopus (unused in this mode)" if self.unused else "Connect Scopus"
        self.title = pn.pane.Markdown(f"## {title}", name="Title")
        self.status = pn.pane.Markdown(name="Scopus key status")
        self.key_input = pn.widgets.PasswordInput(label="Scopus API key")
        self.save_button = pn.widgets.Button(label="Save key")
        self.alert = pn.pane.HTML(name="Alert", sizing_mode="stretch_width")
        self.save_button.on_click(self._on_save)
        self.reload()

    def widgets(self) -> list[Any]:
        """Every control, for the labelled-widget test."""
        return [self.title, self.status, self.key_input, self.save_button, self.alert]

    def reload(self) -> None:
        """Show "set" or "not set" and the file checked, never the value or the file's content."""
        try:
            line = next(ln for ln in status_lines(self.env_file) if ln.startswith(f"{KEY}:"))
        except PrismabibError:  # unreadable or not UTF-8: say so without the reason's detail
            state = "unknown (the file could not be read)"
        else:
            state = "not set" if line.endswith("not set") else "set"
        self.status.object = f"Scopus key: {state}. File checked: {self.env_file}"

    def _on_save(self, _event: Any) -> None:
        self.save()

    def save(self) -> str:
        """Write the key through the credentials writer; clear the input whatever happens."""
        value = (self.key_input.value or "").strip()
        self.key_input.value = ""
        self.key_input.value_input = ""
        try:
            if value:  # before the write, as `credentials set` does
                register_secret_for_redaction(value)
            write_env(self.env_file, {KEY: value})
        except PrismabibError as exc:
            message = f"Refused: {exc}"
        else:
            message = "Saved."
        self.reload()
        self.alert.object = f'<div role="alert">{html.escape(message)}</div>'
        return message

    def view(self) -> pn.Column:
        """The panel, in reading order."""
        return pn.Column(self.title, self.status, self.key_input, self.save_button, self.alert)


def connect_scopus_view(project: Project | None = None) -> pn.viewable.Viewable:
    """Return the Connect Scopus panel."""
    pn.extension()
    return ConnectScopusPanel(project).view().servable()


__all__ = ["ConnectScopusPanel", "connect_scopus_view"]
