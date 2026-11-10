"""The Settings view: where things are kept, and the reviewer name (ticket #323)."""

from __future__ import annotations

from pathlib import Path

import panel as pn  # widget layer only

from prismabib.errors import PrismabibError
from prismabib.gui import settings_model as sm
from prismabib.gui.layer import translate


def build_settings(root: Path | None = None) -> pn.Column:
    """The Settings view. ``root`` is the explicit Projects folder option, when there is one."""
    where = sm.locations(root)
    reviewer = pn.widgets.TextInput(name="Reviewer name", value=sm.reviewer_name())
    save = pn.widgets.Button(name="Save reviewer name", button_type="primary")
    message = pn.pane.Markdown("")

    def on_save() -> None:
        try:
            message.object = f"Saved. Reviewer name: {sm.save_reviewer_name(reviewer.value or '')}"
        except PrismabibError as exc:
            message.object = f"**{translate(exc).sentence}**"

    save.on_click(lambda _: on_save())
    return pn.Column(
        pn.pane.Markdown(
            "# Settings\n\n"
            f"Projects folder: `{where.projects_folder}`\n\n"
            f"Per-user settings file: `{where.settings_file}`\n\n"
            "Uninstalling the app removes neither: delete them yourself if you want them gone."
        ),
        reviewer,
        save,
        message,
        sizing_mode="stretch_width",
    )
