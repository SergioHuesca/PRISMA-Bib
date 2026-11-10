"""The Flow counts screen, and the form that registers a Record the search missed (ticket #334)."""

from __future__ import annotations

import html

import panel as pn  # widget layer only

from prismabib.capture.registration_ui import (
    RULES,
    NearConfirmation,
    ResponseOption,
    match_table,
    register_confirmation_text,
    response_options,
)
from prismabib.errors import ValidationError
from prismabib.gui import flow_layer as fl
from prismabib.gui import layer
from prismabib.gui.layer import UserMessage
from prismabib.project import Project
from prismabib.user_settings import REVIEWER_NAME_KEY, read_user_setting

NOTE = (
    "These numbers are worked out again from the store and the Decision log each time. "
    "A Registered record has its own line and is never added to Identified."
)
REBUILD_NOTE = (
    "It is counted after the next build: open the Build step and rebuild the store. "
    "Registering starts nothing else."
)


def _sentence(exc: Exception) -> str:
    msg: UserMessage = layer.translate(exc)
    return f"{msg.sentence} {msg.next_step}"


def _table(project: Project) -> str:
    counts = fl.flow_counts(project)
    cells = "".join(
        f"<tr><td>{'&nbsp;&nbsp;&nbsp;&nbsp;' if r.indent else ''}{html.escape(r.label)}</td>"
        f"<td>{'-' if r.subtract and r.value else ''}{r.value:,}</td></tr>"
        for r in fl.flow_rows(counts, mode=project.source_mode)
    )
    text = f"<table><tr><th>Step</th><th>Records</th></tr>{cells}</table>"
    pending = fl.pending_registrations(project, counts)
    if pending:
        text += f"<p>{pending} registration(s) are not counted yet. {html.escape(REBUILD_NOTE)}</p>"
    return text


def build_flow(project: Project, session: layer.Session | None = None) -> pn.Column:
    """The Flow counts of ``project`` and the Register form, in reading order.

    ``session`` is the window's held write lock; a bare screen takes one per click.
    """
    counts_pane = pn.pane.HTML("", name="Flow counts")
    counts_message = pn.pane.Markdown("")
    refresh = pn.widgets.Button(name="Refresh the Flow counts")

    reviewer = pn.widgets.TextInput(
        name="Reviewer name", value=read_user_setting(REVIEWER_NAME_KEY) or ""
    )
    paste = pn.widgets.TextAreaInput(
        name="Records the search missed: one line each, title then year then DOI (DOI optional)"
    )
    add = pn.widgets.Button(name="Add these as candidates")
    candidate = pn.widgets.Select(name="Candidate")
    rule = pn.widgets.Select(name="Match rule", options=list(RULES))
    payload = pn.widgets.Select(name="Captured OpenAlex response")
    evidence = pn.pane.HTML("", name="Match evidence")
    near = pn.widgets.Checkbox(name="I confirm this work is this candidate", visible=False)
    register = pn.widgets.Button(name="Register this Record", button_type="primary")
    message = pn.pane.Markdown("")
    options: dict[str, ResponseOption] = {}
    last: list[tuple[str, str, str] | None] = [None]

    def show_counts(*_: object) -> None:
        try:
            counts_pane.object, counts_message.object = _table(project), ""
        except Exception as exc:  # noqa: BLE001 - every failure is shown as a sentence
            counts_pane.object, counts_message.object = "", f"**{_sentence(exc)}**"

    def selection() -> tuple[str, str, str] | None:
        if not candidate.value or not payload.value:
            return None
        return (candidate.value, payload.value, rule.value)

    def sync(*_: object) -> None:
        if selection() != last[0]:  # any change of choice clears the confirmation
            last[0], near.value = selection(), False
        state = NearConfirmation().select(selection()).tick(bool(near.value))
        near.visible = state.offered
        found = options.get(payload.value or "")
        view = next(
            (v for v in fl.candidates(project) if v.event.candidate_id == candidate.value), None
        )
        if found is None or view is None:
            evidence.object = ""
        else:
            table = match_table(view, found.work, rule.value)
            evidence.object = f"<p>{html.escape(register_confirmation_text(table))}</p>"
        register.disabled = (
            selection() is None or (state.offered and not near.value) or refusal is not None
        )

    def refresh_options(*_: object) -> None:
        options.clear()
        view = next(
            (v for v in fl.candidates(project) if v.event.candidate_id == candidate.value), None
        )
        if view is not None:
            found = response_options(project.raw_dir, view, rule.value)
            options.update({o.value: o for o in found.options})
            payload.options = {f"{o.label} (HTTP {o.http_status})": o.value for o in found.options}
        else:
            payload.options = {}
        payload.value = None  # a response is the reviewer's choice, never preselected
        sync()

    def refresh_candidates() -> None:
        rows = fl.candidates(project)
        candidate.options = {
            f"{v.event.title} ({v.event.year}) {v.doi or 'no DOI'} [{v.event.candidate_id[:8]}]": (
                v.event.candidate_id
            )
            for v in rows
        }
        refresh_options()

    def on_add(_: object) -> None:
        try:
            with layer.session_for(project, session) as active:
                done = fl.add_candidates(active, paste.value or "", reviewer=reviewer.value or "")
        except Exception as exc:  # noqa: BLE001 - every failure is shown as a sentence
            message.object = f"**{_sentence(exc)}**"
            return
        message.object = "Candidates: " + ", ".join(
            f"{n} {k.replace('_', ' ')}" for k, n in done.items()
        )
        paste.value = ""
        refresh_candidates()

    def on_register(_: object) -> None:
        sel = selection()
        try:
            if sel is None:
                raise ValidationError("Choose a candidate and a captured response first.")
            confirmed = NearConfirmation().select(sel).tick(bool(near.value)).confirmed_near
            with layer.session_for(project, session) as active:
                event = fl.register_record(
                    active,
                    candidate_id=sel[0],
                    reviewer=reviewer.value or "",
                    payload_file=sel[1],
                    match_rule=sel[2],
                    confirmed_near=confirmed,
                )
        except Exception as exc:  # noqa: BLE001 - every failure is shown as a sentence
            message.object = f"**{_sentence(exc)}**"
        else:
            message.object = f"Registered {event.record_id}. {REBUILD_NOTE}"
        refresh_candidates()
        show_counts()

    refusal = fl.registration_refusal(project)
    if refusal:
        message.object = f"**{refusal}**"
    candidate.param.watch(refresh_options, "value")
    rule.param.watch(refresh_options, "value")
    payload.param.watch(sync, "value")
    near.param.watch(sync, "value")
    refresh.on_click(show_counts)
    add.on_click(on_add)
    register.on_click(on_register)
    show_counts()
    refresh_candidates()
    return pn.Column(
        pn.pane.Markdown("## Flow counts"),
        pn.pane.Markdown(NOTE),
        counts_message,
        counts_pane,
        refresh,
        pn.pane.Markdown("## Register a Record the search missed"),
        reviewer,
        paste,
        add,
        candidate,
        rule,
        payload,
        evidence,
        near,
        register,
        message,
        sizing_mode="stretch_width",
    )
