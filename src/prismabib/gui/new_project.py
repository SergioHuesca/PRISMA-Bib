"""The New Project screen and the sidebar stepper (ticket #324)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import panel as pn  # widget layer only

from prismabib.gui import layer
from prismabib.gui import new_project_model as npm
from prismabib.gui.build import build_build
from prismabib.gui.capture import build_capture_step
from prismabib.gui.criteria import build_criteria
from prismabib.gui.flow import build_flow
from prismabib.gui.keys import build_keys
from prismabib.gui.query_form import build_query_form
from prismabib.project import Project
from prismabib.user_settings import REVIEWER_NAME_KEY, read_user_setting, write_user_settings


def build_new_project(root: Path, on_created: Callable[[Project], None] | None = None) -> pn.Column:
    """Ask for a name, the reviewer name and the Scopus answer, then create the Project in ``root``."""
    name = pn.widgets.TextInput(name="Project name")
    folder = pn.pane.Markdown("")
    reviewer = pn.widgets.TextInput(
        name="Reviewer name", value=read_user_setting(REVIEWER_NAME_KEY) or ""
    )
    access = pn.widgets.RadioButtonGroup(
        name="Do you have Scopus access?", options=list(npm.SCOPUS_CHOICES), value=None
    )
    fill = pn.widgets.Checkbox(
        name="Also fill missing fields of Scopus records from OpenAlex", value=True
    )
    create = pn.widgets.Button(name="Create Project", button_type="primary")
    message = pn.pane.Markdown("")

    def show_folder(*_: object) -> None:
        text = name.value_input or name.value or ""
        if not text.strip():
            folder.object = ""
            return
        refusal = npm.folder_refusal(text, root)
        folder.object = f"**{refusal}**" if refusal else f"Folder name: `{npm.derive_slug(text)}`"

    def submit(_: object) -> None:
        try:
            title = (name.value or "").strip()
            slug = npm.derive_slug(title)
            mode = npm.mode_for_choice(access.value, fill_gaps=fill.value)
            project = layer.create_project(
                slug,
                title=title,
                root=root,
                source_mode=mode,
                reviewer=reviewer.value or "",
            )
        except Exception as exc:  # noqa: BLE001 - every failure is shown as a sentence
            msg = layer.translate(exc)
            message.object = f"**{msg.sentence}** {msg.next_step}"
            return
        who = (reviewer.value or "").strip()
        if who != read_user_setting(REVIEWER_NAME_KEY):
            write_user_settings({REVIEWER_NAME_KEY: who})
        message.object = f"Project `{slug}` created with Source mode {mode}."
        if on_created is not None:
            on_created(project)

    name.param.watch(show_folder, ["value", "value_input"])
    create.on_click(submit)
    return pn.Column(
        pn.pane.Markdown("## New Project"),
        name,
        folder,
        reviewer,
        access,
        fill,
        create,
        message,
        sizing_mode="stretch_width",
    )


def build_stepper(project: Project) -> pn.Row:
    """The sidebar of steps (finished ones marked, read from disk) beside the chosen step."""
    rows = npm.stepper_rows(project)
    detail = pn.pane.Markdown("")
    body = pn.Column(detail)
    buttons: list[pn.widgets.Button] = []

    def show(row: npm.StepRow) -> None:
        text = f"### {row.label}\n\n{row.detail}"
        if row.lock:
            text += f"\n\nRead-only. {row.lock}"
        detail.object = text
        body[:] = [
            detail,
            *([build_query_form(project)] if row.key == "query" else []),
            *([build_criteria(project)] if row.key == "criteria" else []),
            *([build_keys(project)] if row.key == "source_mode" else []),
            *(
                [build_capture_step(project, on_keys=lambda: show(rows[0]))]
                if row.key == "capture"
                else []
            ),
            *([build_build(project)] if row.key == "build" else []),
            *([build_flow(project)] if row.key == "flow" else []),
        ]

    def _shower(row: npm.StepRow) -> Callable[[object], None]:
        return lambda _: show(row)

    for row in rows:
        state = "done" if row.done else "next" if row.current else "not done"
        button = pn.widgets.Button(
            name=f"{row.label} ({state}{', read-only' if row.lock else ''})",
            button_type="primary" if row.current else "default",
        )
        button.on_click(_shower(row))
        buttons.append(button)
    show(next((r for r in rows if r.current), rows[-1]))  # resume at the first unfinished step
    return pn.Row(
        pn.Column(pn.pane.Markdown(f"**{project.title}**"), *buttons, width=300),
        body,
        sizing_mode="stretch_width",
    )
