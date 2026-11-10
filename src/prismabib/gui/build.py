"""The build screen: one button, progress, cancel, and the summary (ticket #331)."""

from __future__ import annotations

import panel as pn  # widget layer only

from prismabib.gui import build_layer as bl
from prismabib.gui import layer
from prismabib.gui.delete_store import build_delete_store
from prismabib.project import Project

BUILD_POLL_MS = 1000
NOTE = (
    "Building rebuilds the store from your captured searches and replaces it only when it is "
    "complete. Your captured data and the Decision log are never touched by a rebuild."
)


def build_build(project: Project, session: layer.Session | None = None) -> pn.Column:
    """The build button and the state of the build of ``project``.

    ``session`` is the window's held write lock; a bare screen takes one per click.
    """
    start = pn.widgets.Button(name="Build the store", button_type="primary")
    cancel = pn.widgets.Button(name="Cancel the build")
    state = pn.pane.Markdown("")
    message = pn.pane.Markdown("")
    bar = pn.indicators.Progress(name="Build progress", active=True, visible=False)
    delete, refresh_delete = build_delete_store(project, session, after=lambda: show())

    def show(*_: object) -> None:
        refresh_delete()
        s = bl.build_status(project)
        start.name = "Rebuild the store" if project.db_path.is_file() else "Build the store"
        start.disabled = s.running
        cancel.disabled = not s.running or s.cancelling
        bar.visible = s.running
        if s.running:
            state.object = f"**Building.** {bl.progress_text(s.progress)}"
        elif s.outcome == "done":
            r = s.result
            state.object = (
                f"**Built.** The store holds {r.records:,} Records. "
                f"{r.duplicates_removed:,} duplicates across searches were removed."
            )
            message.object = ""
        elif s.outcome == "cancelled":
            state.object = "**Stopped as you asked.** The previous store was kept."
        elif s.outcome == "failed" and s.error is not None:
            msg = layer.translate(s.error)
            state.object = f"**The build failed.** {msg.sentence} {msg.next_step}"

    def on_start(_: object) -> None:
        message.object = ""
        try:
            with layer.session_for(project, session) as active:
                bl.start_build(active)
        except Exception as exc:  # noqa: BLE001 - every failure is shown as a sentence
            msg = layer.translate(exc)
            message.object = f"**{msg.sentence}** {msg.next_step}"
        show()

    def on_cancel(_: object) -> None:
        bl.cancel_build(project)
        show()

    start.on_click(on_start)
    cancel.on_click(on_cancel)
    show()
    if pn.state.curdoc is not None:  # a served page polls; a headless build has no event loop
        pn.state.add_periodic_callback(show, period=BUILD_POLL_MS)
    return pn.Column(
        pn.pane.Markdown("## Build the store"),
        pn.pane.Markdown(NOTE),
        state,
        bar,
        message,
        pn.Row(start, cancel),
        delete,
        sizing_mode="stretch_width",
    )
