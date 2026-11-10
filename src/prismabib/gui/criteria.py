"""The Criteria form (ticket #326): guided entry that writes the Project's criteria.yaml."""

from __future__ import annotations

import panel as pn  # widget layer only

from prismabib.gui import criteria_model as cm
from prismabib.gui import layer
from prismabib.project import Project

_LABELS = {
    "version": "Criteria version (MAJOR.MINOR.PATCH)",
    "temporal.year_start": "Include from year",
    "temporal.year_end": "Include up to year",
    "subject_areas": "Include subject areas (four-letter codes, one per line; empty = any)",
    "doc_types.include": "Include document types (codes such as ar, re, cp; empty = any)",
    "doc_types.conference_whitelist": "Conference proceedings to keep (name fragments)",
    "languages": "Include languages (empty = any)",
    "manual_abstract.exclude_reason_codes": "Reason codes for excluding at abstract screening",
    "manual_fulltext.exclude_reason_codes": "Reason codes for excluding at full-text screening",
}


def build_criteria(project: Project, session: layer.Session | None = None) -> pn.Column:
    """Edit the Project's Criteria; read-only with the reason once a Decision is logged.

    ``session`` is the window's held write lock; a bare screen takes one per save.
    """
    title = pn.pane.Markdown("## Criteria")
    status = pn.pane.Markdown("")
    try:
        values = cm.form_from_criteria(project.criteria)
    except Exception as exc:  # noqa: BLE001 - a hand edit gone wrong is shown as a sentence
        msg = layer.translate(exc)
        status.object = f"**{msg.sentence}** Fix criteria.yaml, then reopen this step."
        return pn.Column(title, status)
    lock = layer.locks(project)["criteria"]
    widgets: dict[str, pn.widgets.Widget] = {}
    errors: dict[str, pn.pane.Markdown] = {}
    rows: list[pn.viewable.Viewable] = [title]
    if lock:
        rows.append(pn.pane.Markdown(f"**Read-only.** {lock}"))
    for key in cm.FIELDS:
        cls = pn.widgets.TextAreaInput if key in cm.LIST_FIELDS else pn.widgets.TextInput
        widgets[key] = cls(name=_LABELS[key], value=values[key], disabled=bool(lock))
        errors[key] = pn.pane.Markdown("")
        rows += [widgets[key], errors[key]]
    save = pn.widgets.Button(name="Save Criteria", button_type="primary", disabled=bool(lock))

    def submit(_: object) -> None:
        for pane in errors.values():
            pane.object = ""
        status.object = ""
        try:
            criteria = cm.criteria_from_form({k: str(w.value or "") for k, w in widgets.items()})
            with layer.session_for(project, session) as active:
                layer.save_criteria(active, criteria)
        except cm.CriteriaFormError as exc:
            for key, message in exc.fields.items():
                (errors.get(key) or status).object = f"**{message}**"
            status.object = status.object or "**Some entries need correcting.**"
            return
        except Exception as exc:  # noqa: BLE001 - every failure is shown as a sentence
            msg = layer.translate(exc)
            status.object = f"**{msg.sentence}** {msg.next_step}"
            return
        status.object = f"Criteria version {criteria.version} saved to criteria.yaml."

    save.on_click(submit)
    return pn.Column(*rows, save, status, sizing_mode="stretch_width")
