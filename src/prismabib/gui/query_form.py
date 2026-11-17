"""The Query step: search terms and compound terms as chips, years, subject areas."""

from __future__ import annotations

import panel as pn  # widget layer only

from prismabib.gui import palette, service
from prismabib.gui import query_form_model as model
from prismabib.gui.chip_tabstop import ChipTabstop
from prismabib.project import Project


def _chips(label: str, values: list[str], hint: str) -> tuple[pn.Column, pn.widgets.MultiChoice]:
    """Chips are a MultiChoice (each value shows with a remove button) fed by a text box."""
    shown = pn.widgets.MultiChoice(
        name=label,
        placeholder=label,  # unset, Choices names its input "null" (#382)
        options=list(values),
        value=list(values),
        stylesheets=[palette.CHIP_CSS, palette.FIELD_CSS],
    )
    entry = pn.widgets.TextInput(
        name=f"Add to: {label}",
        placeholder=hint,
        stylesheets=[palette.PLACEHOLDER_CSS, palette.FIELD_CSS],
    )
    add = pn.widgets.Button(name=f"Add to: {label} (button)")

    def push(_: object) -> None:
        text = (entry.value_input or entry.value or "").strip()
        if text and text not in shown.options:
            shown.options = [*shown.options, text]
            shown.value = [*shown.value, text]
        entry.value = ""

    entry.param.watch(push, "value")
    add.on_click(push)
    return pn.Column(shown, pn.Row(entry, add)), shown


def build_query_form(project: Project, session: service.Session | None = None) -> pn.Column:
    """The form for ``project``; ``session`` is the window's write lock, else one is taken per save."""
    inputs = model.load(project)
    mode = project.source_mode
    lock = service.locks(project)["query"]
    scopus = mode != "openalex"

    terms_box, terms = _chips("Search terms", inputs.terms, "a phrase, then Enter")
    compound_box, compound = _chips(
        "Compound terms",
        [model.group_to_chip(g) for g in inputs.compound_terms],
        "parts that must all appear, joined by ' + '",
    )
    first = pn.widgets.IntInput(
        stylesheets=[palette.FIELD_CSS], name="First year", value=inputs.year_start
    )
    last = pn.widgets.IntInput(
        stylesheets=[palette.FIELD_CSS], name="Last year", value=inputs.year_end
    )
    subjects = pn.widgets.MultiChoice(
        name="Subject areas",
        placeholder="Subject areas",  # unset, Choices names its input "null" (#382)
        options=list(model.SUBJECT_AREA_CHOICES),
        value=[] if scopus else list(inputs.subject_areas),
        disabled=scopus,
        stylesheets=[palette.CHIP_CSS, palette.FIELD_CSS],
    )
    save = pn.widgets.Button(name="Save query", button_type="primary")
    status = pn.pane.Markdown("")
    errors = {
        key: pn.pane.Markdown("") for key in ("terms", "compound_terms", "years", "subject_areas")
    }
    notes: list[pn.pane.Markdown] = []
    if inputs.load_problem:
        notes.append(
            pn.pane.Markdown(f"**The saved file could not be fully read.** {inputs.load_problem}")
        )
    if inputs.unparsed_search:
        notes.append(
            pn.pane.Markdown(
                "The saved search was written by hand and cannot be shown as chips: "
                f"`{inputs.unparsed_search}`. Saving here replaces it."
            )
        )
    if scopus:
        errors["subject_areas"].object = model.ENRICHMENT_REASON
    if lock:
        notes.append(pn.pane.Markdown(f"**Read-only.** {lock}"))
        for widget in (terms, compound, first, last, subjects, save):
            widget.disabled = True
        for box in (terms_box, compound_box):
            for widget in box.select(pn.widgets.Widget):
                widget.disabled = True

    def submit(_: object) -> None:
        for pane in errors.values():
            pane.object = ""
        if scopus:
            errors["subject_areas"].object = model.ENRICHMENT_REASON
        status.object = ""
        form = model.QueryInputs(
            terms=list(terms.value),
            compound_terms=[g for g in map(model.chip_to_group, compound.value) if g],
            year_start=first.value,
            year_end=last.value,
            subject_areas=[] if scopus else list(subjects.value),
        )
        found = model.field_errors(project, form)
        if found:
            for key, text in found.items():
                (errors.get(key) or status).object = f"**{text}**"
            status.object = "Not saved. Correct the fields marked above."
            return
        try:
            with service.session_for(project, session) as active:
                service.save_query(active, form)
        except Exception as exc:  # noqa: BLE001 - every failure is shown as a sentence
            msg = service.translate(exc)
            status.object = f"**{msg.sentence}** {msg.next_step}"
            return
        status.object = "Query saved to project.toml and criteria.yaml."

    save.on_click(submit)
    return pn.Column(
        pn.pane.Markdown("## Query"),
        *notes,
        terms_box,
        errors["terms"],
        compound_box,
        errors["compound_terms"],
        pn.FlexBox(first, last),  # wraps in a narrow window: a Row put Last year on the page edge
        errors["years"],
        subjects,
        errors["subject_areas"],
        save,
        status,
        ChipTabstop(height=0, margin=0),
        sizing_mode="stretch_width",
    )
