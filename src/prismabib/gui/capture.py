"""The capture screen: confirm, run in the background, cancel, resume, discard (ticket #329)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import panel as pn  # widget layer only

from prismabib.gui import capture_layer as cl
from prismabib.gui import layer
from prismabib.gui import openalex_capture_layer as oal
from prismabib.gui.size_check import build_size_check
from prismabib.project import Project

CAPTURE_POLL_MS = 1000


def build_capture(project: Project, *, user_file: Path | None = None) -> pn.Column:
    """The capture controls and the state of the capture of ``project`` (OpenAlex or Scopus)."""
    openalex = project.source_mode == "openalex"
    status_of = oal.openalex_status if openalex else cl.capture_status
    texts = oal.STATE_TEXT if openalex else cl.STATE_TEXT
    budget = pn.widgets.IntInput(
        name="Page budget (pages of 100 works)", value=10, start=1, visible=openalex
    )
    again = pn.widgets.Button(name="Capture again (a new run)", visible=openalex)
    confirm = pn.widgets.Checkbox(name="I confirm: capture this search now")
    start = pn.widgets.Button(name="Start the capture", button_type="primary")
    cancel = pn.widgets.Button(name="Cancel the capture")
    refresh = pn.widgets.Button(name="Refresh the status")
    discard = pn.widgets.Button(name="Discard the unsealed run")
    discard_yes = pn.widgets.Button(name="Yes, discard it", button_type="danger", visible=False)
    discard_no = pn.widgets.Button(name="Keep it", visible=False)
    bar = pn.indicators.Progress(name="Capture progress", value=0, max=100, visible=False)
    state = pn.pane.Markdown("")
    message = pn.pane.Markdown("")

    def show(*_: object) -> None:
        s = status_of(project)
        active = s.state in (cl.CaptureState.RUNNING, cl.CaptureState.CANCELLING)
        text = f"**{s.state.value.capitalize()}.** {texts[s.state]}"
        if s.pages_done or s.records_seen:
            of = f" of {s.total_results:,}" if s.total_results is not None else ""
            text += f"\n\nPages done: {s.pages_done}. Records seen: {s.records_seen:,}{of}."
        state.object = text
        if s.message:
            message.object = f"**{s.message}**"
        bar.visible = active
        if active and s.total_results:
            bar.value = min(100, 100 * s.records_seen // s.total_results)
        busy = active or s.state is cl.CaptureState.SEALED
        start.disabled = busy
        start.name = (
            "Resume the capture"
            if s.state in (cl.CaptureState.UNSEALED, cl.CaptureState.NETWORK_STOPPED)
            else "Start the capture"
        )
        again.disabled = s.state is not cl.CaptureState.SEALED
        cancel.disabled = s.state is not cl.CaptureState.RUNNING
        discard.disabled = s.state is not cl.CaptureState.UNSEALED
        if active:
            discard_yes.visible = discard_no.visible = False

    def fail(exc: Exception) -> None:
        msg = layer.translate(exc)
        message.object = f"**{msg.sentence}** {msg.next_step}"

    def go(new_run: bool) -> None:
        message.object = ""
        try:
            with layer.Session(project) as session:
                if openalex:
                    oal.start_openalex_capture(
                        session,
                        budget=budget.value or 0,
                        confirmed=confirm.value,
                        refresh=new_run,
                        user_file=user_file,
                    )
                else:
                    cl.start_capture(session, confirmed=confirm.value, user_file=user_file)
        except Exception as exc:  # noqa: BLE001 - every failure is shown as a sentence
            fail(exc)
        confirm.value = False  # every start, a resume included, is confirmed again
        show()

    def on_start(_: object) -> None:
        go(False)

    def on_cancel(_: object) -> None:
        cl.cancel_capture(project)
        show()

    def ask_discard(yes: bool) -> None:
        discard_yes.visible = discard_no.visible = yes

    def on_discard(_: object) -> None:
        run = status_of(project).run_id
        try:
            with layer.Session(project) as session:
                cl.discard_capture(session, run or "", confirmed=True)
        except Exception as exc:  # noqa: BLE001
            fail(exc)
        ask_discard(False)
        show()

    start.on_click(on_start)
    again.on_click(lambda _: go(True))
    cancel.on_click(on_cancel)
    refresh.on_click(show)
    discard.on_click(lambda _: ask_discard(True))
    discard_yes.on_click(on_discard)
    discard_no.on_click(lambda _: ask_discard(False))
    show()
    if pn.state.curdoc is not None:  # a served page polls; a headless build has no event loop
        pn.state.add_periodic_callback(show, period=CAPTURE_POLL_MS)
    return pn.Column(
        pn.pane.Markdown("## Capture"),
        state,
        bar,
        message,
        confirm,
        budget,
        pn.Row(start, again, cancel, refresh),
        discard,
        pn.Row(discard_yes, discard_no),
        sizing_mode="stretch_width",
    )


def build_capture_step(project: Project, *, on_keys: Callable[[], None] | None = None) -> pn.Column:
    """The size check, and below it the capture, shown once the researcher continues."""
    panel = build_capture(project)
    status_of = oal.openalex_status if project.source_mode == "openalex" else cl.capture_status
    panel.visible = status_of(project).state is not cl.CaptureState.NOT_STARTED
    return pn.Column(
        build_size_check(
            project, on_keys=on_keys, on_continue=lambda: setattr(panel, "visible", True)
        ),
        panel,
        sizing_mode="stretch_width",
    )
