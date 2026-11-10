"""The Screening step: hosts the library's Screener inside the stepper (ticket #332)."""

from __future__ import annotations

import panel as pn  # widget layer only

from prismabib.gui import layer
from prismabib.gui import screening_layer as sl
from prismabib.project import Project
from prismabib.stage import PrismaStage

STAGES = {"Title and abstract": PrismaStage.TITLE_ABSTRACT, "Full text": PrismaStage.FULLTEXT}


def build_screening(project: Project) -> pn.Column:
    """Reviewer, stage and blind mode, then the Screener; says what is missing when opened early."""
    gaps = sl.missing(project)
    if gaps:
        return pn.Column(
            pn.pane.Markdown(
                "## Screening\n\nScreening needs:\n\n" + "\n".join(f"- {g}" for g in gaps)
            )
        )
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

    def on_start(_: object) -> None:
        message.object = ""
        try:
            host[:] = [
                sl.open_screening(
                    project, STAGES[stage.value], reviewer.value or "", blind=blind.value
                ).view()
            ]
        except Exception as exc:  # noqa: BLE001 - every failure is shown as a sentence
            msg = layer.translate(exc)
            message.object = f"**{msg.sentence}** {msg.next_step}"

    start.on_click(on_start)
    return pn.Column(
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
