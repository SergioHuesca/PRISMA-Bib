"""The Screening step: hosts the library's Screener inside the stepper."""

from __future__ import annotations

from collections.abc import Callable

import panel as pn  # widget layer only

from prismabib.gui import layer
from prismabib.gui import screening_layer as sl
from prismabib.project import Project
from prismabib.stage import PrismaStage

POLL_MS = 250
STAGES = {"Title and abstract": PrismaStage.TITLE_ABSTRACT, "Full text": PrismaStage.FULLTEXT}


class ScreeningStep(pn.Column):
    """The step; ``poll`` shows the Screener once its queue has loaded (a served page calls it)."""

    poll: Callable[[], None]


def build_screening(project: Project, session: layer.Session | None = None) -> ScreeningStep:
    """Reviewer, stage and blind mode, then the Screener; says what is missing when opened early.

    ``session`` is the window's held write lock; every Decision uses it, else takes a short one.
    """
    gaps = sl.missing(project)
    if gaps:
        early = ScreeningStep(
            pn.pane.Markdown(
                "## Screening\n\nScreening needs:\n\n" + "\n".join(f"- {g}" for g in gaps)
            )
        )
        early.poll = lambda: None
        return early
    reviewer = pn.widgets.TextInput(name=sl.REVIEWER_LABEL, value=sl.creation_reviewer(project))
    stage = pn.widgets.RadioButtonGroup(
        name="Stage", options=list(STAGES), value="Title and abstract"
    )
    blind = pn.widgets.Checkbox(
        name="Blind mode: hide author names, citation counts and other reviewers' Decisions",
        value=True,
    )
    start = pn.widgets.Button(name="Start screening", button_type="primary")
    message = pn.pane.Markdown("")
    host = pn.Column(sizing_mode="stretch_width")
    waiting = False

    def poll(*_: object) -> None:
        nonlocal waiting
        if not waiting:
            return
        try:
            screener = sl.finish_loading(project, blind=blind.value)
        except Exception as exc:  # noqa: BLE001 - every failure is shown as a sentence
            waiting = False
            msg = layer.translate(exc)
            message.object = f"**{msg.sentence}** {msg.next_step}"
            return
        if screener is not None:
            waiting = False
            message.object = ""
            host[:] = [screener.view()]

    def on_start(_: object) -> None:
        nonlocal waiting
        message.object = "Loading the Records."
        try:
            sl.start_loading(project, STAGES[stage.value], reviewer.value or "", session)
            waiting = True
        except Exception as exc:  # noqa: BLE001 - every failure is shown as a sentence
            msg = layer.translate(exc)
            message.object = f"**{msg.sentence}** {msg.next_step}"
        poll()

    start.on_click(on_start)
    if pn.state.curdoc is not None:  # a headless build has no event loop; tests call ``poll``
        pn.state.add_periodic_callback(poll, period=POLL_MS)
    step = ScreeningStep(
        pn.pane.Markdown("## Screening"),
        reviewer,
        pn.pane.Markdown(f"_{sl.ACTOR_LABEL}._"),
        stage,
        blind,
        start,
        message,
        host,
        sizing_mode="stretch_width",
    )
    step.poll = poll
    return step
