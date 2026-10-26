"""Registration form: pure view models plus the thin Panel view (ADR 0035 Decisions 1-6).

Everything a reviewer sees or a test asserts is a plain function over plain data (the first
part of this module), so the widgets (the last section) can be thin. The module imports no log
class: the only writers it calls are ``register_candidate`` and ``reverse_registration``, and
matching is the writer's own :func:`~prismabib.capture.registrations.matching_works`.
"""

from __future__ import annotations

import html
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Final, cast

import panel as pn  # widget layer only; see the last section

from prismabib.capture.candidates import CandidateLog, CandidateView, near_rule
from prismabib.capture.layout import OPENALEX_DIRNAME, RUN_MANIFEST_FILENAME, is_sealed
from prismabib.capture.manifest import OpenAlexRunManifest
from prismabib.capture.registrations import (
    REVERSAL_REASONS,
    MatchRule,
    RegistrationRefusal,
    active_registrations,
    load_registration_events,
    matching_works,
    read_response,
    register_candidate,
    reverse_registration,
)
from prismabib.errors import ConfigError, LogError, PrismabibError, StoreError, ValidationError
from prismabib.prisma.impact import ReversalImpact, reversal_impact
from prismabib.project import Project
from prismabib.sources.openalex import parse_work

# A plain build reuses an existing store and loads nothing (cli.py), so only --rebuild folds in
# a new registration or reversal.
REBUILD_COMMAND: Final = "`prismabib build <slug> --rebuild`"
RULES: Final[tuple[str, ...]] = ("doi", "title_year", "near_confirmed")
NEAR: Final = "near_confirmed"
RULE_DEFINITIONS: Final[Mapping[str, str]] = {
    "doi": "the work's DOI equals the candidate's DOI",
    "title_year": "the candidate has no DOI; the normalised title and the year are equal",
    "near_confirmed": (
        "same normalised title with a different year, or one title a whole-word prefix of "
        "the other; needs the reviewer's confirmation"
    ),
}
NEAR_RULE_TEXT: Final[Mapping[str, str]] = {
    "same_title_other_year": "same normalised title, different year",
    "title_prefix": "one title is a whole-word prefix of the other, same year",
}


# -- candidates ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateRowView:
    """One row of the candidate table. ``record_id`` is set when it has an active registration."""

    candidate_id: str
    title: str
    year: int
    doi: str | None
    reviewer: str
    pasted_at: str
    record_id: str | None

    @property
    def state(self) -> str:
        """``unregistered`` or ``registered (<record_id>)``."""
        return "unregistered" if self.record_id is None else f"registered ({self.record_id})"

    @property
    def selectable(self) -> bool:
        """A candidate with an active registration cannot be selected for Register."""
        return self.record_id is None


def candidate_rows(
    views: Sequence[CandidateView], registered: Mapping[str, str]
) -> list[CandidateRowView]:
    """Rows from ``CandidateLog.view()``; ``registered`` maps candidate_id to active record_id."""
    return [
        CandidateRowView(
            candidate_id=v.event.candidate_id,
            title=v.event.title,
            year=v.event.year,
            doi=v.doi,
            reviewer=v.event.reviewer,
            pasted_at=v.event.pasted_at.isoformat(),
            record_id=registered.get(v.event.candidate_id),
        )
        for v in views
    ]


# -- payload choice ------------------------------------------------------------------------------


@dataclass(frozen=True)
class ResponseOption:
    """A sealed response in which exactly one work satisfies the rule."""

    label: str  # <run>/response-NNNN
    value: str  # openalex/<run>/response-NNNN.jsonl, what the writer takes
    api_view: str
    http_status: int
    work: dict[str, Any]


@dataclass(frozen=True)
class ResponseOptions:
    """The offered responses plus the counts that explain the ones not offered."""

    options: tuple[ResponseOption, ...]
    unmatched: int  # readable responses with no work or several under the rule
    unreadable: int  # responses the writer would refuse to read (not 200, old select, bad digest)


def response_options(raw_dir: Path, candidate: CandidateView, rule: str) -> ResponseOptions:
    """Sealed OpenAlex responses with exactly one work satisfying ``rule`` (Layer 0 read only)."""
    options: list[ResponseOption] = []
    unmatched = unreadable = 0
    root = raw_dir / OPENALEX_DIRNAME
    run_dirs = sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []
    for run_dir in run_dirs:
        if not is_sealed(run_dir):
            continue
        try:
            manifest = OpenAlexRunManifest.model_validate_json(
                (run_dir / RUN_MANIFEST_FILENAME).read_text(encoding="utf-8")
            )
        except ValueError:
            unreadable += 1
            continue
        for request in sorted(manifest.requests, key=lambda r: r.file):
            value = f"{OPENALEX_DIRNAME}/{run_dir.name}/{request.file}"
            try:
                works = read_response(raw_dir, value).works
            except RegistrationRefusal:
                unreadable += 1
                continue
            found = matching_works(candidate, rule, works)
            if len(found) != 1:
                unmatched += 1
                continue
            options.append(
                ResponseOption(
                    label=f"{run_dir.name}/{request.file.removesuffix('.jsonl')}",
                    value=value,
                    api_view=request.api_view,
                    http_status=request.http_status,
                    work=found[0],
                )
            )
    return ResponseOptions(tuple(options), unmatched, unreadable)


def preselected_rule(candidate: CandidateView, works: Sequence[dict[str, Any]]) -> str | None:
    """The rule to preselect: the only one under which exactly one work matches, else ``None``."""
    ok = [r for r in RULES if len(matching_works(candidate, r, works)) == 1]
    return ok[0] if len(ok) == 1 else None


# -- the match table -----------------------------------------------------------------------------


@dataclass(frozen=True)
class MatchTable:
    """Candidate and work side by side, with the rule named (ADR 0035 section 3)."""

    rule: str
    rule_definition: str
    near_rule: str | None  # which near rule fired; only for ``near_confirmed``
    rows: tuple[tuple[str, str, str], ...]  # (field, candidate, work)


def match_table(candidate: CandidateView, work: Mapping[str, Any], rule: str) -> MatchTable:
    """Side-by-side evidence for the one satisfying ``work`` under ``rule``."""
    parsed = parse_work(work)
    title, year = parsed.title or "", parsed.publication_year
    fired = near_rule(candidate.event, title, year) if rule == NEAR else None
    return MatchTable(
        rule=rule,
        rule_definition=RULE_DEFINITIONS[rule],
        near_rule=fired,
        rows=(
            ("title", candidate.event.title, title),
            ("year", str(candidate.event.year), "" if year is None else str(year)),
            ("doi", candidate.doi or "", parsed.doi or ""),
        ),
    )


def register_confirmation_text(table: MatchTable) -> str:
    """The last step before the write: DOI, title, year and rule (ADR 0029 section 1)."""
    work = {name: w for name, _c, w in table.rows}
    text = f"Register {work['doi']}: {work['title']} ({work['year']}) under rule {table.rule}."
    if table.near_rule is not None:
        text += f" Near match: {NEAR_RULE_TEXT[table.near_rule]}."
    return text


# -- near-match confirmation ---------------------------------------------------------------------


@dataclass(frozen=True)
class NearConfirmation:
    """The confirmation checkbox: the only source of ``confirmed_near=True``.

    ``selection`` is ``(candidate_id, payload, rule)``. Any change of selection clears the tick,
    and the box is not offered (so cannot be ticked) for ``doi`` and ``title_year``.
    """

    selection: tuple[str, str, str] | None = None
    checked: bool = False

    @property
    def offered(self) -> bool:
        """Whether the checkbox is shown at all."""
        return self.selection is not None and self.selection[2] == NEAR

    @property
    def confirmed_near(self) -> bool:
        """The value to pass as ``confirmed_near``."""
        return self.checked and self.offered

    def select(self, selection: tuple[str, str, str] | None) -> NearConfirmation:
        """A new state for ``selection``; the tick is cleared unless the selection is unchanged."""
        return self if selection == self.selection else NearConfirmation(selection, False)

    def tick(self, checked: bool) -> NearConfirmation:
        """A new state with the box set; ignored when the box is not offered."""
        return replace(self, checked=checked and self.offered)


# -- reversal ------------------------------------------------------------------------------------


def reason_options() -> tuple[str, ...]:
    """The reversal reasons, the writer's own tuple (never a copy)."""
    return REVERSAL_REASONS


def can_reverse(reason: str, note: str) -> bool:
    """Whether the reverse button may be enabled: a known reason, and a note for ``other``."""
    return reason in REVERSAL_REASONS and (reason != "other" or bool(note.strip()))


def reversal_confirmation_text(impact: ReversalImpact | None) -> str:
    """Warning text for the single reversal confirmation; ``None`` means the impact is unknown."""
    if impact is None:
        return "The impact of this reversal is unknown (see the error). Reverse anyway?"
    included = "is currently included" if impact.currently_included else "is not currently included"
    return (
        f"Reversing {impact.record_id} orphans {impact.decisions_orphaned} current "
        f"decision(s); the paper {included}. The decisions stay in the log. Reverse?"
    )


# -- the Panel view (widget layer; ADR 0035 sections 1, 3, 5, 6, 8, 10) --------------------------


def _alert_html(text: str, attempt: int) -> str:
    """The persistent alert region; ``role="alert"`` is what a screen reader announces.

    ``attempt`` makes two identical refusals distinct objects, so the pane re-renders and the
    live region announces the second one too.
    """
    return f'<div role="alert" data-attempt="{attempt}">{html.escape(text)}</div>' if text else ""


def _paragraph_html(text: str) -> str:
    """Escaped text as a paragraph: third-party titles and DOIs are never markup or markdown."""
    return f"<p>{html.escape(text)}</p>" if text else ""


def _match_html(table: MatchTable) -> str:
    rows = "".join(
        f"<tr><th>{html.escape(f)}</th><td>{html.escape(c)}</td><td>{html.escape(w)}</td></tr>"
        for f, c, w in table.rows
    )
    near = f"<p>Near rule: {NEAR_RULE_TEXT[table.near_rule]}</p>" if table.near_rule else ""
    return (
        f"<p>Rule <b>{html.escape(table.rule)}</b>: {html.escape(table.rule_definition)}</p>"
        f"<table><tr><th>Field</th><th>Candidate</th><th>Work</th></tr>{rows}</table>{near}"
    )


class RegistrationForm:
    """The Register section. Handlers are plain methods so tests drive them without a browser."""

    def __init__(self, project: Project, *, reviewer: str) -> None:
        if not reviewer.strip():
            raise ValidationError("reviewer must not be blank")
        self.project, self.reviewer = project, reviewer
        self.candidates = CandidateLog(project.root / "decisions" / "candidates.jsonl")
        self.near = NearConfirmation()
        self._attempts = 0
        self.views: dict[str, CandidateView] = {}
        self.options: dict[str, ResponseOption] = {}  # payload value -> option, for the rule

        self.candidate_table = pn.pane.HTML(name="Candidates")
        self.candidate_select = pn.widgets.Select(name="Candidate")
        self.rule_select = pn.widgets.Select(name="Match rule", options=list(RULES))
        self.payload_select = pn.widgets.Select(name="Captured OpenAlex response")
        self.payload_note = pn.pane.Markdown(name="Responses not offered")
        self.match_pane = pn.pane.HTML(name="Match evidence")
        self.near_checkbox = pn.widgets.Checkbox(
            name="I confirm this work is this candidate", visible=False
        )
        self.confirm_pane = pn.pane.HTML(name="Confirmation")
        self.register_button = pn.widgets.Button(name="Register")
        self.alert = pn.pane.HTML(name="Alert", sizing_mode="stretch_width")

        self.candidate_select.param.watch(lambda _e: self._on_change(self._on_candidate), "value")
        self.rule_select.param.watch(lambda _e: self._on_change(self._refresh_payloads), "value")
        self.payload_select.param.watch(lambda _e: self._on_selection(), "value")
        self.near_checkbox.param.watch(lambda e: self.tick(bool(e.new)), "value")
        self.register_button.on_click(self._on_register)
        self.on_change: Callable[[], None] | None = None  # the sibling section's reload
        self.reload()

    def widgets(self) -> list[Any]:
        """Every input and output control, for the labelled-widget test."""
        return [
            self.candidate_table,
            self.candidate_select,
            self.rule_select,
            self.payload_select,
            self.payload_note,
            self.match_pane,
            self.near_checkbox,
            self.confirm_pane,
            self.register_button,
            self.alert,
        ]

    # -- state -----------------------------------------------------------------------------------

    def reload(self) -> None:
        """Re-read the candidate and registration logs, so the display matches the files."""
        events = load_registration_events(self.project.registrations_path)
        registered = {e.candidate_id: e.record_id for e in active_registrations(events)}
        views = self.candidates.view()
        self.views = {v.event.candidate_id: v for v in views}
        rows = candidate_rows(views, registered)
        self.candidate_table.object = (
            "<table><tr><th>Title</th><th>Year</th><th>DOI</th><th>Pasted by</th>"
            "<th>Pasted at</th><th>State</th></tr>"
            + "".join(
                "<tr>"
                + "".join(
                    f"<td>{html.escape(str(c))}</td>"
                    for c in (r.title, r.year, r.doi or "", r.reviewer, r.pasted_at, r.state)
                )
                + "</tr>"
                for r in rows
            )
            + "</table>"
        )
        keep = self.candidate_select.value
        self.candidate_select.options = {
            # Unique per candidate: same title and year can have several content-hash ids.
            f"{r.title} ({r.year}) {r.doi or 'no DOI'} [{r.candidate_id[:8]}]": r.candidate_id
            for r in rows
            if r.selectable
        }
        if keep not in self.candidate_select.options.values():
            self.candidate_select.value = None
        self._on_candidate()

    def _on_change(self, then: Callable[[], None]) -> None:
        """A new candidate or rule: the reviewer's response choice does not carry over."""
        self.payload_select.value = None
        then()

    def _candidate(self) -> CandidateView | None:
        return self.views.get(self.candidate_select.value or "")

    def _on_candidate(self) -> None:
        view = self._candidate()
        if view is not None:
            usable = [r for r in RULES if response_options(self.project.raw_dir, view, r).options]
            if len(usable) == 1 and usable[0] != self.rule_select.value:  # preselect if unambiguous
                self.rule_select.value = usable[0]  # its watcher refreshes the payloads
                return
        self._refresh_payloads()

    def _refresh_payloads(self) -> None:
        view = self._candidate()
        found = (
            ResponseOptions((), 0, 0)
            if view is None
            else response_options(self.project.raw_dir, view, self.rule_select.value)
        )
        self.options = {o.value: o for o in found.options}
        self.payload_select.options = {
            f"{o.label} ({o.api_view}, HTTP {o.http_status})": o.value for o in found.options
        }
        # Registration is the reviewer's act: a response is never chosen for them. Their own
        # earlier choice survives a reload (e.g. after a refusal) while it is still offered.
        if self.payload_select.value not in self.options:
            self.payload_select.value = None
        self.payload_note.object = (
            f"{len(found.options)} response(s) offered; {found.unmatched} with no work or "
            f"several under this rule; {found.unreadable} unreadable."
        )
        self._on_selection()

    def _selection(self) -> tuple[str, str, str] | None:
        cid, payload = self.candidate_select.value, self.payload_select.value
        return None if not cid or not payload else (cid, payload, self.rule_select.value)

    def _on_selection(self) -> None:
        selection = self._selection()
        self.near = self.near.select(selection)  # clears the tick on any change
        view = self._candidate()
        option = self.options.get(self.payload_select.value or "")
        if selection is None or view is None or option is None:
            self.match_pane.object = self.confirm_pane.object = ""
        else:
            table = match_table(view, option.work, selection[2])
            self.match_pane.object = _match_html(table)
            self.confirm_pane.object = _paragraph_html(register_confirmation_text(table))
        self._sync_controls()

    def tick(self, checked: bool) -> None:
        """The near-match checkbox; the only source of ``confirmed_near=True``."""
        self.near = self.near.tick(checked)
        self._sync_controls()

    def _sync_controls(self) -> None:
        self.near_checkbox.visible = self.near.offered
        self.near_checkbox.value = self.near.checked
        ready = self._selection() is not None and (not self.near.offered or self.near.checked)
        self.register_button.disabled = not ready

    # -- the one write ---------------------------------------------------------------------------

    def _on_register(self, _event: Any) -> None:
        self.register()

    def register(self) -> str:
        """Call the writer and nothing else; show its message verbatim on a refusal."""
        selection = self._selection()
        try:
            if selection is None:
                raise ValidationError("choose a candidate and a captured response first")
            cid, payload, rule = selection
            event = register_candidate(
                self.project,
                self.candidates,
                candidate_id=cid,
                reviewer=self.reviewer,
                payload_file=payload,
                match_rule=cast(MatchRule, rule),
                source_level=4,
                confirmed_near=self.near.confirmed_near,
            )
        except PrismabibError as exc:
            message = f"Refused: {exc}"
        else:
            message = (
                f"Registered {event.record_id} (event {event.event_id}). "
                f"It takes effect at the next {REBUILD_COMMAND}."
            )
        self._attempts += 1
        self.alert.object = _alert_html(message, self._attempts)
        self.reload()  # after any outcome, so the display matches the file
        if self.on_change is not None:
            self.on_change()
        return message

    def view(self) -> pn.Column:
        """The Register section, in reading order."""
        return pn.Column(
            pn.pane.HTML(
                "<h2>Register a candidate</h2>"
                f"<p>Reviewer: <b>{html.escape(self.reviewer)}</b></p>",
                name="Header",
            ),
            self.alert,
            self.candidate_table,
            self.candidate_select,
            self.rule_select,
            self.payload_select,
            self.payload_note,
            self.match_pane,
            self.near_checkbox,
            self.confirm_pane,
            self.register_button,
        )


class ReversalForm:
    """The Reverse and Active registrations sections (ADR 0035 section 4)."""

    def __init__(
        self, project: Project, *, reviewer: str, register: RegistrationForm | None = None
    ) -> None:
        if not reviewer.strip():
            raise ValidationError("reviewer must not be blank")
        self.project, self.reviewer = project, reviewer
        self.candidates = CandidateLog(project.root / "decisions" / "candidates.jsonl")
        self.on_change: Callable[[], None] | None = None  # the sibling section's reload
        self.active: dict[str, Any] = {}  # event_id -> active RegisterEvent
        self._reloading = False
        self._register = register  # shares its alert pane and its attempt counter
        self._attempts = 0

        self.active_table = pn.pane.HTML(name="Active registrations")
        self.select = pn.widgets.Select(name="Registration to reverse")
        self.reason_select = pn.widgets.Select(
            name="Reversal reason", options=list(reason_options()), value=None
        )
        self.note_input = pn.widgets.TextAreaInput(name="Note (required when the reason is other)")
        self.hint = pn.pane.Markdown(
            name="Correcting a registration",
            object="To correct a wrong DOI or title, reverse the registration, then register again.",
        )
        self.confirm_pane = pn.pane.HTML(name="Reversal confirmation")
        self.reverse_button = pn.widgets.Button(name="Reverse")
        self.alert = register.alert if register is not None else pn.pane.HTML(name="Alert")

        self.select.param.watch(lambda _e: self._on_select(), "value")
        self.reason_select.param.watch(lambda _e: self._sync_controls(), "value")
        self.note_input.param.watch(lambda _e: self._sync_controls(), "value")
        self.reverse_button.on_click(self._on_reverse)
        self.reload()

    def _show(self, message: str) -> None:
        """Show ``message`` in the alert; the counter is shared so each one is a new object."""
        owner: Any = self._register if self._register is not None else self
        owner._attempts += 1
        self.alert.object = _alert_html(message, owner._attempts)

    def widgets(self) -> list[Any]:
        """Every input and output control, for the labelled-widget test."""
        return [
            self.active_table,
            self.select,
            self.reason_select,
            self.note_input,
            self.hint,
            self.confirm_pane,
            self.reverse_button,
        ]

    def reload(self) -> None:
        """Re-read the registration log, so the table and the choices match the file."""
        titles = {v.event.candidate_id: v.event.title for v in self.candidates.view()}
        self.active = {
            e.event_id: e
            for e in active_registrations(load_registration_events(self.project.registrations_path))
        }
        cells = [
            (
                e.record_id,
                titles.get(e.candidate_id, e.candidate_id),
                e.match_rule,
                e.reviewer,
                e.ts.isoformat(),
            )
            for e in self.active.values()
        ]
        self.active_table.object = (
            "<table><tr><th>Record</th><th>Candidate</th><th>Rule</th><th>Reviewer</th>"
            "<th>Time</th></tr>"
            + "".join(
                "<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in row) + "</tr>"
                for row in cells
            )
            + "</table>"
        )
        self._reloading = True  # the watcher would clear the alert the caller just set
        try:
            self.select.options = {
                f"{c[0]} - {c[1]}": eid for c, eid in zip(cells, self.active, strict=True)
            }
            if self.select.value not in self.active:
                self.select.value = None
                self.confirm_pane.object = ""
        finally:
            self._reloading = False
        self._sync_controls()

    def _on_select(self) -> None:
        """Show the impact of reversing the selection; only an active registration is asked about."""
        if self._reloading:
            return
        target = self.select.value
        self.alert.object = ""
        self.confirm_pane.object = ""
        if target in self.active:
            try:
                impact: ReversalImpact | None = reversal_impact(
                    self.project, self.active[target].record_id
                )
            except (StoreError, ConfigError, LogError) as exc:
                impact = None
                self._show(f"Impact unknown: {exc}")
            self.confirm_pane.object = _paragraph_html(reversal_confirmation_text(impact))
        self._sync_controls()

    def _sync_controls(self) -> None:
        self.reverse_button.disabled = self.select.value not in self.active or not can_reverse(
            self.reason_select.value or "", self.note_input.value or ""
        )

    def _on_reverse(self, _event: Any) -> None:
        self.reverse()

    def reverse(self) -> str:
        """Call the writer and nothing else; show its message verbatim on a refusal."""
        try:
            target = self.select.value
            if not target:
                raise ValidationError("choose a registration to reverse first")
            note = self.note_input.value or ""
            event = reverse_registration(
                self.project,
                reverses=target,
                reviewer=self.reviewer,
                reason=self.reason_select.value or "",
                note=note if note.strip() else None,
            )
        except PrismabibError as exc:
            message, done = f"Refused: {exc}", False
        else:
            message = (
                f"Reversed {event.reverses} (event {event.event_id}). It takes effect at the "
                f"next {REBUILD_COMMAND}; the decisions stay in decisions.jsonl."
            )
            done = True
        self.reload()  # after any outcome, so the display matches the file
        if done:
            self.reason_select.value, self.note_input.value = None, ""
        self._show(message)
        if self.on_change is not None:
            self.on_change()
        return message

    def view_objects(self) -> list[Any]:
        """The Reverse and Active registrations sections, in reading order."""
        return [
            pn.pane.Markdown("## Reverse a registration"),
            self.select,
            self.reason_select,
            self.note_input,
            self.hint,
            self.confirm_pane,
            self.reverse_button,
            pn.pane.Markdown("## Active registrations"),
            self.active_table,
        ]


def registration_view(project: Project, *, reviewer: str) -> pn.viewable.Viewable:
    """Return the Register, Reverse and Active registrations sections (ADR 0004).

    Display in a notebook cell or ``panel serve`` it. Both sections share one alert pane.

    Raises:
        ValidationError: If ``reviewer`` is blank.
    """
    pn.extension()
    register = RegistrationForm(project, reviewer=reviewer)
    reverse = ReversalForm(project, reviewer=reviewer, register=register)
    register.on_change, reverse.on_change = reverse.reload, register.reload
    return pn.Column(*register.view().objects, *reverse.view_objects()).servable()


__all__ = [
    "CandidateRowView",
    "MatchTable",
    "NearConfirmation",
    "RegistrationForm",
    "ResponseOption",
    "ResponseOptions",
    "ReversalForm",
    "can_reverse",
    "candidate_rows",
    "match_table",
    "preselected_rule",
    "reason_options",
    "register_confirmation_text",
    "registration_view",
    "response_options",
    "reversal_confirmation_text",
]
