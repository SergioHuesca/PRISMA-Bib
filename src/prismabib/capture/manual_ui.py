"""Panel views for manual entry and conflict review (ADR 0041 Decisions 2 and 4, ADR 0004).

Like :mod:`prismabib.capture.registration_ui`, handlers are plain methods so tests drive them
without a browser. The views never touch a log: the only writers they call are
:func:`~prismabib.capture.manual.add_manual_entry` and
:func:`~prismabib.capture.conflicts.resolve_conflict`, the ones the CLI calls. Manual entry is
not a screening action, so no key binding is added (``KEY_MAP`` is unchanged).
"""

from __future__ import annotations

import html
import json
import webbrowser
from collections.abc import Callable
from functools import partial
from typing import Any, Final
from urllib.parse import quote

import panel as pn  # widget layer only

from prismabib.capture.conflicts import SIDES, OpenConflict, open_conflicts, resolve_conflict
from prismabib.capture.manual import FIELDS, add_manual_entry, manual_target
from prismabib.errors import PrismabibError, ValidationError
from prismabib.project import Project
from prismabib.source_mode import command_refusal, effective_mode

_ID_EXAMPLE: Final = {
    "scopus": "scopus:2-s2.0-...",
    "scopus+openalex": "scopus:2-s2.0-...",
    "openalex": "openalex:W...",
}
REBUILD_COMMAND: Final = "`prismabib build <slug> --rebuild`"
NO_DOI_TEXT: Final = "This record has no DOI, so there is no publisher page to open."


def publisher_url(doi: str | None) -> str | None:
    """``https://doi.org/<doi>``, or ``None`` for a record with no DOI."""
    return f"https://doi.org/{quote(doi.strip(), safe='/()')}" if doi and doi.strip() else None


def _shown(stored: str) -> str:
    """A stored canonical-JSON value as reviewer text: lists one item per line."""
    value = json.loads(stored)
    return "\n".join(value) if isinstance(value, list) else str(value)


def conflict_html(conflict: OpenConflict) -> str:
    """Both sides of one conflict, side by side."""
    return (
        f"<table><tr><th>Scopus</th><th>OpenAlex</th></tr><tr>"
        f"<td><pre>{html.escape(_shown(conflict.scopus_value))}</pre></td>"
        f"<td><pre>{html.escape(_shown(conflict.openalex_value))}</pre></td></tr></table>"
    )


def _label(conflict: OpenConflict) -> str:
    return f"{conflict.record_id} / {conflict.field}"


def _click(action: Callable[[], object]) -> Callable[[Any], None]:
    def handler(_event: Any) -> None:
        action()

    return handler


def _alert(text: str) -> str:
    return f'<div role="alert">{html.escape(text)}</div>'


class ManualEntryForm:
    """Enter missing fields of one Scopus record, after opening its publisher page."""

    def __init__(self, project: Project, *, reviewer: str) -> None:
        if not reviewer.strip():
            raise ValidationError("reviewer must not be blank")
        self.project, self.reviewer = project, reviewer
        self.record_input = pn.widgets.TextInput(
            label="Record id", placeholder=_ID_EXAMPLE[effective_mode(project)]
        )
        self.load_button = pn.widgets.Button(label="Load record")
        self.doi_button = pn.widgets.Button(label="Open publisher page", disabled=True)
        self.doi_note = pn.pane.Markdown(name="Publisher page")
        self.inputs = {
            f: pn.widgets.TextAreaInput(label=f.capitalize(), disabled=True) for f in FIELDS
        }
        self.save_button = pn.widgets.Button(label="Save", disabled=True)
        self.alert = pn.pane.HTML(name="Alert", sizing_mode="stretch_width")
        self.url: str | None = None
        self.load_button.on_click(_click(self.load))
        self.doi_button.on_click(_click(self.open_publisher))
        self.save_button.on_click(_click(self.save))

    def widgets(self) -> list[Any]:
        """Every control, for the labelled-widget test."""
        return [
            self.record_input,
            self.load_button,
            self.doi_button,
            self.doi_note,
            *self.inputs.values(),
            self.save_button,
            self.alert,
        ]

    def load(self) -> str:
        """Read the record from the store: enable one area per unavailable field."""
        try:
            target = manual_target(self.project, (self.record_input.value or "").strip())
        except PrismabibError as exc:
            self.url, unavailable, message = None, tuple[str, ...](), f"Refused: {exc}"
        else:
            self.url, unavailable = publisher_url(target.doi), target.unavailable
            message = "" if unavailable else "No field of this record is unavailable."
        self.doi_button.disabled = self.url is None
        self.doi_note.object = "" if self.url else NO_DOI_TEXT
        for field, widget in self.inputs.items():
            widget.disabled = field not in unavailable
        self.save_button.disabled = not unavailable
        self.alert.object = _alert(message) if message else ""
        return message

    def open_publisher(self) -> bool:
        """Open the DOI page in the default browser; ``False`` (and nothing opened) without a DOI."""
        return self.url is not None and webbrowser.open(self.url)

    def save(self) -> str:
        """Write one entry per filled-in area, through the writer; show refusals verbatim."""
        messages: list[str] = []
        for field, widget in self.inputs.items():
            if widget.disabled or not (widget.value or "").strip():
                continue
            try:
                event = add_manual_entry(
                    self.project,
                    record_id=(self.record_input.value or "").strip(),
                    field=field,
                    value=widget.value or "",
                    reviewer=self.reviewer,
                )
            except PrismabibError as exc:
                messages.append(f"Refused {field}: {exc}")
            else:
                widget.value = ""
                messages.append(
                    f"Entered {field} (event {event.event_id}). It takes effect at the next "
                    f"{REBUILD_COMMAND}."
                )
        message = " ".join(messages) or "Nothing to save: fill in at least one field."
        self.alert.object = _alert(message)
        return message

    def view(self) -> pn.Column:
        """The form, in reading order."""
        return pn.Column(
            pn.pane.Markdown(f"## Enter a missing field by hand\n\nReviewer: **{self.reviewer}**"),
            self.alert,
            self.record_input,
            self.load_button,
            self.doi_button,
            self.doi_note,
            *self.inputs.values(),
            self.save_button,
        )


class ConflictPanel:
    """Review open conflicts and resolve each one by choosing a side."""

    def __init__(self, project: Project, *, reviewer: str) -> None:
        if not reviewer.strip():
            raise ValidationError("reviewer must not be blank")
        self.project, self.reviewer = project, reviewer
        self.mode_refusal = command_refusal(project, "conflict-resolve")  # reverse is never blocked
        self.conflicts: dict[str, OpenConflict] = {}
        self.select = pn.widgets.Select(label="Open conflict")
        self.values = pn.pane.HTML(name="Values")
        self.manual_input = pn.widgets.TextAreaInput(label="Manual value (for the manual side)")
        self.buttons = {
            "scopus": pn.widgets.Button(label="Keep Scopus"),
            "openalex": pn.widgets.Button(label="Use OpenAlex"),
            "manual": pn.widgets.Button(label="Use manual value"),
        }
        assert set(self.buttons) == set(SIDES)
        self.alert = pn.pane.HTML(name="Alert", sizing_mode="stretch_width")
        self.select.param.watch(lambda _e: self._show(), "value")
        for side, button in self.buttons.items():
            button.on_click(_click(partial(self.resolve, side)))
        self.reload()
        if self.mode_refusal:
            self.alert.object = _alert(self.mode_refusal)

    def widgets(self) -> list[Any]:
        """Every control, for the labelled-widget test."""
        return [self.select, self.values, self.manual_input, *self.buttons.values(), self.alert]

    def reload(self) -> None:
        """Re-read the open conflicts from the store (empty when there is no store yet)."""
        try:
            found = open_conflicts(self.project)
        except PrismabibError:
            found = []
        self.conflicts = {_label(c): c for c in found}
        self.select.options = list(self.conflicts)
        if self.select.value not in self.conflicts:
            self.select.value = next(iter(self.conflicts), None)
        self._show()

    def _show(self) -> None:
        chosen = self.conflicts.get(self.select.value or "")
        self.values.object = conflict_html(chosen) if chosen else "<p>No open conflicts.</p>"
        for button in self.buttons.values():
            button.disabled = chosen is None or self.mode_refusal is not None

    def resolve(self, side: str) -> str:
        """Write the resolution through the writer; show a refusal verbatim."""
        chosen = self.conflicts.get(self.select.value or "")
        try:
            if self.mode_refusal:
                raise ValidationError(self.mode_refusal)
            if chosen is None:
                raise ValidationError("choose an open conflict first")
            event = resolve_conflict(
                self.project,
                record_id=chosen.record_id,
                field=chosen.field,
                chosen_side=side,
                reviewer=self.reviewer,
                value=self.manual_input.value if side == "manual" else None,
            )
        except PrismabibError as exc:
            message = f"Refused: {exc}"
        else:
            message = (
                f"Resolved {_label(chosen)} as {side} (event {event.event_id}). It takes "
                f"effect at the next {REBUILD_COMMAND}."
            )
            self.manual_input.value = ""
        self.alert.object = _alert(message)
        return message

    def view(self) -> pn.Column:
        """The panel, in reading order."""
        return pn.Column(
            pn.pane.Markdown("## Review conflicts"),
            self.alert,
            self.select,
            self.values,
            *self.buttons.values(),
            self.manual_input,
        )


def manual_view(project: Project, *, reviewer: str) -> pn.viewable.Viewable:
    """Return the manual-entry form and the conflict review panel as two tabs.

    Raises:
        ValidationError: If ``reviewer`` is blank.
    """
    pn.extension()
    return pn.Tabs(
        ("Manual entry", ManualEntryForm(project, reviewer=reviewer).view()),
        ("Conflicts", ConflictPanel(project, reviewer=reviewer).view()),
    ).servable()


__all__ = [
    "ConflictPanel",
    "ManualEntryForm",
    "conflict_html",
    "manual_view",
    "publisher_url",
]
