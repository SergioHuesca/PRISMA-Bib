"""The app page: the tabs (Home, Settings) and the one Project this window has open.

Home routes to New Project and to the stepper. Opening a Project in the stepper takes its write
lock for this window (one :class:`~prismabib.gui.layer.Session`); the lock is released on "Back to
Projects" and when the browser page goes away, but not while a task started here is still running
on the Project, and every lock is released when the app stops.
"""

from __future__ import annotations

from pathlib import Path

import panel as pn  # widget layer only

from prismabib.gui import home_model, layer, tasks
from prismabib.gui.home import build_home
from prismabib.gui.new_project import build_new_project, build_stepper
from prismabib.gui.settings import build_settings
from prismabib.project import Project


def build_app(root: Path | None = None) -> pn.viewable.Viewable:
    """One page per browser session. ``root`` is the Projects folder, else the remembered one.

    Home (which carries the Stop the app control) is a tab that routes to New Project and the
    stepper; Settings is the other tab.
    """
    page = pn.Column(sizing_mode="stretch_width")
    held: list[layer.Session] = []  # this window's hold on the Project open in the stepper

    def release() -> str | None:
        """Let go of the open Project's lock; a running task keeps it until the task ends.

        Returns a sentence saying so when a task is still running.
        """
        if not held:
            return None
        session = held.pop()
        runner = tasks.runner_for(session.project)
        status = runner.status()
        runner.when_idle(session.close)
        if status.running:
            return (
                f"{status.name} is still running on `{session.project.slug}`, so the Project "
                "stays locked until it ends."
            )
        return None

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

    def leave() -> None:
        note = release()
        home()
        if note:
            page.insert(0, pn.pane.Markdown(f"**{note}**"))

    def new() -> None:
        folder = home_model.resolve_folder(root).path
        folder.mkdir(parents=True, exist_ok=True)
        back = pn.widgets.Button(name="Back to Projects")
        back.on_click(lambda _: leave())
        page[:] = [back, build_new_project(folder, on_created=stepper)]

    def stepper(project: Project) -> None:
        release()
        session = layer.Session(project)
        held.append(session)
        back = pn.widgets.Button(name="Back to Projects")
        back.on_click(lambda _: leave())
        page[:] = [back, build_stepper(project, session)]

    home()
    if pn.state.curdoc is not None:  # a closed tab frees its Project (a headless build has none)

        def page_closed(_context: object) -> None:
            release()

        pn.state.on_session_destroyed(page_closed)
    return pn.Tabs(("Home", page), ("Settings", build_settings()))
