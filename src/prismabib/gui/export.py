"""The export screen: one button, progress, cancel, summary and the git warning."""

from __future__ import annotations

import panel as pn  # widget layer only

from prismabib.gui import export_layer as el
from prismabib.gui import layer
from prismabib.project import Project

EXPORT_POLL_MS = 1000
NOTE = (
    "Export writes the citable bundle (figures, tables, numbers and a manifest) into the "
    "Project's exports folder. It never commits or pushes anything."
)


def build_export(project: Project, session: layer.Session | None = None) -> pn.Column:
    """The export button and the state of the export of ``project``.

    ``session`` is the window's held write lock; a bare screen takes one per click.
    """
    start = pn.widgets.Button(name="Export the citable bundle", button_type="primary")
    cancel = pn.widgets.Button(name="Cancel the export")
    state = pn.pane.Markdown("")
    message = pn.pane.Markdown("")
    warning = pn.pane.Markdown("")
    bar = pn.indicators.Progress(name="Export progress", active=True, visible=False)
    git_text = el.current_git_warnings()  # read once: git is not asked on every poll

    def show(*_: object) -> None:
        s = el.export_status(project)
        start.disabled = s.running
        cancel.disabled = not s.running or s.cancelling
        bar.visible = s.running
        shown = s.result.warnings if s.outcome == "done" else git_text
        warning.object = "\n\n".join(f"**Warning.** {w}" for w in shown)
        if s.running:
            state.object = f"**Exporting.** {el.progress_text(s.progress)}"
        elif s.outcome == "done":
            r = s.result
            state.object = (
                f"**Exported.** The bundle is in `{r.folder}`: {r.figures} figure files, "
                f"{r.tables} table files and {r.numbers} numbers."
            )
            message.object = ""
        elif s.outcome == "cancelled":
            state.object = "**Stopped as you asked.** Nothing was changed in the bundle."
        elif s.outcome == "failed" and s.error is not None:
            msg = layer.translate(s.error)
            state.object = f"**The export failed.** {msg.sentence} {msg.next_step}"

    def on_start(_: object) -> None:
        message.object = ""
        try:
            with layer.session_for(project, session) as active:
                el.start_export(active)
        except Exception as exc:  # noqa: BLE001 - every failure is shown as a sentence
            msg = layer.translate(exc)
            message.object = f"**{msg.sentence}** {msg.next_step}"
        show()

    def on_cancel(_: object) -> None:
        el.cancel_export(project)
        show()

    start.on_click(on_start)
    cancel.on_click(on_cancel)
    show()
    if pn.state.curdoc is not None:  # a served page polls; a headless build has no event loop
        pn.state.add_periodic_callback(show, period=EXPORT_POLL_MS)
    return pn.Column(
        pn.pane.Markdown("## Export the citable bundle"),
        pn.pane.Markdown(NOTE),
        warning,
        state,
        bar,
        message,
        pn.Row(start, cancel),
        sizing_mode="stretch_width",
    )
