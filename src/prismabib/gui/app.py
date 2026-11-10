"""The app page (ticket #318): home, New Project and the stepper (tickets #322, #324)."""

from __future__ import annotations

from pathlib import Path

import panel as pn  # widget layer only

from prismabib.gui import home_model, layer
from prismabib.gui.home import build_home
from prismabib.gui.new_project import build_new_project, build_stepper
from prismabib.project import Project


def build_app(root: Path | None = None) -> pn.viewable.Viewable:
    """One page per browser session. ``root`` is the Projects folder, else the remembered one."""
    page = pn.Column(sizing_mode="stretch_width")

    def open_(folder: Path, slug: str) -> None:
        try:
            project = layer.open_project(slug, root=folder)
        except Exception as exc:  # noqa: BLE001 - shown as a sentence, never a traceback
            msg = layer.translate(exc)
            page.insert(0, pn.pane.Markdown(f"**{msg.sentence}** {msg.next_step}"))
            return
        stepper(project)

    def home() -> None:
        page[:] = [build_home(root, on_new_project=lambda: new(), on_open=open_)]

    def new() -> None:
        folder = home_model.resolve_folder(root).path
        folder.mkdir(parents=True, exist_ok=True)
        back = pn.widgets.Button(name="Back to Projects")
        back.on_click(lambda _: home())
        page[:] = [back, build_new_project(folder, on_created=stepper)]

    def stepper(project: Project) -> None:
        back = pn.widgets.Button(name="Back to Projects")
        back.on_click(lambda _: home())
        page[:] = [back, build_stepper(project)]

    home()
    return page
