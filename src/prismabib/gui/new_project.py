"""The New Project screen and the sidebar stepper (ticket #324)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import panel as pn  # widget layer only

from prismabib.gui import layer, palette
from prismabib.gui import new_project_model as npm
from prismabib.gui.build import build_build
from prismabib.gui.capture import build_capture_step
from prismabib.gui.criteria import build_criteria
from prismabib.gui.export import build_export
from prismabib.gui.flow import build_flow
from prismabib.gui.keys import build_keys
from prismabib.gui.query_form import build_query_form
from prismabib.gui.screening import build_screening
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


#: Controls with these name prefixes stay usable in a read-only window: they only read.
_READ_ONLY_KEEPS = ("Refresh",)
TAKE_OVER_TEXT = (
    "Taking over removes the lock file the closed window left behind and gives this window the "
    "right to change this Project. Do it only if you are sure no other window is open on it."
)


def _freeze(view: pn.viewable.Viewable) -> None:
    """Disable every writing control in ``view``, and keep it disabled when a screen refreshes."""

    def hold(event: Any) -> None:
        if not event.new:
            event.obj.disabled = True

    widgets: list[Any] = view.select(pn.widgets.Widget)
    for widget in widgets:
        if "disabled" in widget.param and not (widget.name or "").startswith(_READ_ONLY_KEEPS):
            widget.disabled = True
            widget.param.watch(hold, "disabled")


def _lock_banner(session: layer.Session, on_taken: Callable[[], None]) -> pn.Column:
    """Why this window is read-only and, for a crashed window's lock, a confirmed take-over."""
    text = pn.pane.Markdown("")
    note = pn.pane.Markdown(TAKE_OVER_TEXT, visible=False)
    ask = pn.widgets.Button(name="Take over this Project", visible=session.stale)
    yes = pn.widgets.Button(
        name="Yes, take over this Project",
        button_type="danger",
        visible=False,
        stylesheets=[palette.BUTTON_CSS],
    )
    no = pn.widgets.Button(name="Keep it read-only", visible=False)
    column = pn.Column(text, note, pn.Row(ask, yes, no), visible=session.read_only)

    def paint() -> None:
        text.object = f"**Read-only.** {session.reason} Nothing can be changed in this window."
        ask.visible = session.stale
        column.visible = session.read_only

    def confirming(open_: bool) -> None:
        note.visible = yes.visible = no.visible = open_
        ask.visible = session.stale and not open_

    def take(_: object) -> None:
        try:
            session.take_over(confirmed=True)
        except Exception as exc:  # noqa: BLE001 - shown as a sentence, never a traceback
            msg = layer.translate(exc)
            text.object = f"**{msg.sentence}** {msg.next_step}"
            confirming(False)
            return
        confirming(False)
        paint()
        if not session.read_only:
            on_taken()

    ask.on_click(lambda _: confirming(True))
    yes.on_click(take)
    no.on_click(lambda _: confirming(False))
    paint()
    return column


def build_stepper(project: Project, session: layer.Session | None = None) -> pn.Row:
    """The sidebar of steps (finished ones marked, read from disk) beside the chosen step.

    ``session`` is this window's hold on the Project. Every screen writes through it; when it is
    read-only the writing controls are disabled and a banner says why (and offers a confirmed
    take-over when the lock was left by a window that closed unexpectedly).
    """
    rows = npm.stepper_rows(project)
    detail = pn.pane.Markdown("")
    body = pn.Column(detail)
    buttons: list[pn.widgets.Button] = []
    shown: list[npm.StepRow] = [next((r for r in rows if r.current), rows[-1])]

    def show(row: npm.StepRow) -> None:
        shown[0] = row
        text = f"### {row.label}\n\n{row.detail}"
        if row.lock:
            text += f"\n\nRead-only. {row.lock}"
        detail.object = text
        body[:] = [
            detail,
            *([build_query_form(project, session)] if row.key == "query" else []),
            *([build_criteria(project, session)] if row.key == "criteria" else []),
            *([build_keys(project)] if row.key == "source_mode" else []),
            *(
                [build_capture_step(project, session, on_keys=lambda: show(rows[0]))]
                if row.key == "capture"
                else []
            ),
            *([build_build(project, session)] if row.key == "build" else []),
            *([build_flow(project, session)] if row.key == "flow" else []),
            *([build_screening(project, session)] if row.key == "screening" else []),
            *([build_export(project, session)] if row.key == "export" else []),
        ]
        if session is not None and session.read_only:
            _freeze(body)

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
    show(shown[0])  # resume at the first unfinished step
    banner = _lock_banner(session, lambda: show(shown[0])) if session is not None else pn.Column()
    return pn.Row(
        pn.Column(pn.pane.Markdown(f"**{project.title}**"), *buttons, width=300),
        pn.Column(banner, body, sizing_mode="stretch_width"),
        sizing_mode="stretch_width",
    )
