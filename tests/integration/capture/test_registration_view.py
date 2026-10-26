"""ADR 0035 Decision 11: the Register form drives the writer and nothing else (headless handlers).

Source levels 1 to 3 are unreachable from the view (it has no level control and always passes
4), so ADR 0035 Decision 11's "levels 1 to 3" refusal is covered at the writer only
(`test_registrations.py`), not here.

Reuses the synthetic OpenAlex run and ``env`` fixture of ``test_registrations.py``; no network.
"""

from __future__ import annotations

import copy
import hashlib
import html
import io
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import panel as pn
import pytest
from typer.testing import CliRunner

from prismabib.capture import registration_ui as ui
from prismabib.capture.candidates import CandidateEvent, CandidateRow
from prismabib.capture.registrations import (
    REVERSAL_REASONS,
    RegistrationRefusal,
    load_registration_events,
)
from prismabib.cli import app
from prismabib.errors import StoreError, ValidationError
from prismabib.prisma.flow import compute_flow_counts
from prismabib.prisma.impact import reversal_impact
from prismabib.prisma.log import DecisionLog
from prismabib.prisma.orphans import project_orphaned_decisions
from prismabib.project import Project
from prismabib.stage import PrismaStage
from tests.conftest import SeededIdFactory
from tests.integration.capture.test_registrations import (
    DOI_ONE,
    PAYLOAD,
    TITLE_ONE,
    Env,
    _works,
    env,  # noqa: F401  (pytest fixture)
)
from tests.store_helpers import make_entry

TA, FT = PrismaStage.TITLE_ABSTRACT, PrismaStage.FULLTEXT
AUTO_NAME = re.compile(r"^[A-Za-z]+\d{3,}$")  # Panel's default, unlabelled name


def _form(env: Env, cid: str, rule: str | None = None) -> ui.RegistrationForm:  # noqa: F811
    form = ui.RegistrationForm(env.project, reviewer="sh")
    form.candidate_select.value = cid
    if rule is not None:
        form.rule_select.value = rule
    assert list(form.payload_select.options.values()) == [PAYLOAD]  # exactly one is offered
    assert form.payload_select.value is None  # ...and never chosen for the reviewer
    form.payload_select.value = PAYLOAD
    return form


def _alert_text(form: ui.RegistrationForm | ui.ReversalForm) -> str:
    """The alert markup without the per-attempt marker."""
    return re.sub(r' data-attempt="\d+"', "", html.unescape(str(form.alert.object)))


def _events(project: Project) -> list[dict[str, Any]]:
    return [
        e.model_dump(mode="json", exclude={"event_id", "ts", "record_id"})
        for e in load_registration_events(project.registrations_path)
    ]


def _raw_digest(project: Project) -> str:
    h = hashlib.sha256()
    for p in sorted(project.raw_dir.rglob("*")):
        h.update(str(p.relative_to(project.raw_dir)).encode())
        if p.is_file():
            h.update(p.read_bytes())
    return h.hexdigest()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("year", "doi", "rule", "cli_extra"),
    [
        (2021, DOI_ONE, "doi", []),
        (2021, None, "title_year", ["--match-rule", "title_year"]),
        (2022, None, "near_confirmed", ["--match-rule", "near_confirmed", "--confirm-near"]),
    ],
)
def test_view__register__appends_event_equal_to_cli(
    env: Env,  # noqa: F811
    tmp_path: Path,
    year: int,
    doi: str | None,
    rule: str,
    cli_extra: list[str],
) -> None:
    cid = env.candidate(TITLE_ONE, year, doi)
    other_root = tmp_path / "copy"
    shutil.copytree(env.project.root, other_root / "reg")
    other = Project.open("reg", root=other_root)

    result = CliRunner().invoke(
        app,
        [
            "register",
            "reg",
            cid,
            "--reviewer",
            "sh",
            "--payload",
            PAYLOAD,
            *cli_extra,
            "--root",
            str(other_root),
        ],
    )
    form = _form(env, cid)
    assert form.rule_select.value == rule
    if rule == "near_confirmed":
        form.tick(True)
    message = form.register()

    assert result.exit_code == 0, result.output
    (gui,) = load_registration_events(env.project.registrations_path)
    assert message.startswith("Registered ") and gui.record_id in message
    assert _events(env.project) == _events(other)
    assert _events(env.project)[0]["match_rule"] == rule
    # The refreshed form now shows the candidate as registered and unselectable.
    assert f"registered ({gui.record_id})" in str(form.candidate_table.object)
    assert not form.candidate_select.options


@pytest.mark.integration
def test_view__register_near_without_confirmation__refused_and_log_unchanged(
    env: Env,  # noqa: F811
) -> None:
    cid = env.candidate(TITLE_ONE, 2022)  # year off by one: a near match
    form = _form(env, cid)
    assert form.rule_select.value == "near_confirmed"  # the only rule that applies
    assert form.register_button.disabled and form.near_checkbox.visible
    before = env.snapshot()
    with pytest.raises(Exception, match="confirm") as writer:  # the writer's own text
        env.register(cid, match_rule="near_confirmed")
    assert env.snapshot() == before

    message = form.register()

    assert message == f"Refused: {writer.value}"
    assert _alert_text(form) == f'<div role="alert">{message}</div>'
    assert env.snapshot() == before  # bytes and checksum sidecar


@pytest.mark.integration
def test_view__near_confirmation__only_source_of_confirmed_near(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid = env.candidate(TITLE_ONE, 2022)
    calls: list[dict[str, Any]] = []
    real = ui.register_candidate

    def spy(*args: Any, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(ui, "register_candidate", spy)
    form = _form(env, cid)
    form.tick(True)
    assert form.near_checkbox.value and not form.register_button.disabled

    form.rule_select.value = "doi"  # selection changes: the tick clears
    form.rule_select.value = "near_confirmed"
    form.payload_select.value = PAYLOAD
    assert not form.near_checkbox.value and form.register_button.disabled
    form.tick(True)
    form.register()

    (call,) = calls
    assert (call["source_level"], call["confirmed_near"], call["match_rule"]) == (
        4,
        True,
        "near_confirmed",
    )
    assert call["reviewer"] == "sh" and call["payload_file"] == PAYLOAD


@pytest.mark.integration
def test_view__doi_match__passes_confirmed_near_false(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    calls: list[dict[str, Any]] = []
    real = ui.register_candidate
    monkeypatch.setattr(
        ui, "register_candidate", lambda *a, **k: (calls.append(k), real(*a, **k))[1]
    )
    form = _form(env, cid)
    assert not form.near_checkbox.visible
    form.tick(True)  # ignored: not offered for doi

    form.register()

    assert calls[0]["confirmed_near"] is False and calls[0]["source_level"] == 4


@pytest.mark.integration
def test_view__writer_refusal__shown_verbatim_and_nothing_written(env: Env) -> None:  # noqa: F811
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    form = _form(env, cid)
    env.write_run([])  # the sealed response vanishes under the form: the writer refuses
    before = env.snapshot()
    with pytest.raises(Exception) as writer:
        env.register(cid)

    message = form.register()

    assert message == f"Refused: {writer.value}"
    assert env.snapshot() == before == (None, None)


@pytest.mark.integration
def test_view__register__raw_and_decisions_byte_identical(env: Env) -> None:  # noqa: F811
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    decisions = env.project.root / "decisions" / "decisions.jsonl"

    def state() -> tuple[str, bytes | None]:
        return _raw_digest(env.project), decisions.read_bytes() if decisions.exists() else None

    before = state()

    _form(env, cid).register()

    assert state() == before


@pytest.mark.integration
def test_view__alert_is_role_alert_in_rendered_html(env: Env) -> None:  # noqa: F811
    cid = env.candidate(TITLE_ONE, 2022)
    form = _form(env, cid)
    form.register()  # refused: near without the tick

    out = io.StringIO()
    form.view().save(out)

    # Panel's HTML pane keeps the markup (JSON-escaped in the saved document); its Alert widget
    # renders no role, which is why the alert is an HTML pane (ADR 0035 section 10).
    assert re.search(r"role=(&amp;quot;|&quot;|\\?\")alert", out.getvalue())
    assert "Refused" in html.unescape(html.unescape(out.getvalue()))


@pytest.mark.integration
def test_widgets__all_labelled(env: Env) -> None:  # noqa: F811
    form = ui.RegistrationForm(env.project, reviewer="sh")

    for widget in form.widgets():
        assert widget.name.strip() and not AUTO_NAME.match(widget.name), widget


@pytest.mark.integration
def test_view__blank_reviewer__raises_validation_error(env: Env) -> None:  # noqa: F811
    with pytest.raises(ValidationError):
        ui.registration_view(env.project, reviewer="  ")
    assert "sh" in str(ui.registration_view(env.project, reviewer="sh").objects[0].object)


def _refused_through_view(
    env: Env,  # noqa: F811
    form: ui.RegistrationForm,
    writer_call: Callable[[], object],
    *,
    wrote_before: bool = False,
) -> None:
    """The view shows the writer's exact refusal text and writes nothing."""
    before = env.snapshot()
    with pytest.raises(RegistrationRefusal) as writer:
        writer_call()
    assert env.snapshot() == before

    message = form.register()

    assert message == f"Refused: {writer.value}"
    assert _alert_text(form) == f'<div role="alert">{message}</div>'
    assert env.snapshot() == before  # log bytes and checksum sidecar
    assert (before[0] is not None) == wrote_before


@pytest.mark.integration
def test_view__refusal_no_doi__surfaces_the_writer_message(env: Env) -> None:  # noqa: F811
    cid = env.candidate(TITLE_ONE, 2021)
    form = _form(env, cid)
    works = _works()
    works[0]["doi"] = None
    env.write_run(works)  # the sealed response changes under the open form

    _refused_through_view(env, form, lambda: env.register(cid, match_rule="title_year"))


@pytest.mark.integration
def test_view__refusal_two_matching_works__surfaces_the_writer_message(
    env: Env,  # noqa: F811
) -> None:
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    form = _form(env, cid)
    works = _works()
    twin = copy.deepcopy(works[0])
    twin["id"] = "https://openalex.org/W2999999999"
    env.write_run([*works, twin])

    _refused_through_view(env, form, lambda: env.register(cid))


@pytest.mark.integration
def test_view__refusal_duplicate_of_scopus_record__surfaces_the_writer_message(
    env: Env,  # noqa: F811
) -> None:
    env.scopus_run(
        "20250102T000000Z-22222222",
        make_entry(eid="2-s2.0-800000000002", title="Other", cover_date="2020-01-01", doi=DOI_ONE),
    )
    env.build()
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    form = _form(env, cid)

    _refused_through_view(env, form, lambda: env.register(cid))


@pytest.mark.integration
def test_view__second_registration_same_candidate__refused(env: Env) -> None:  # noqa: F811
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    form = _form(env, cid)
    env.register(cid)  # registered elsewhere (CLI, another tab) while this form is open

    _refused_through_view(env, form, lambda: env.register(cid), wrote_before=True)
    assert len(load_registration_events(env.project.registrations_path)) == 1


HOSTILE = "<script>alert(1)</script>\"'& *x* _y_ `z`"


@pytest.mark.integration
def test_view__candidates_with_one_year__stay_selectable_with_unique_labels(
    env: Env,  # noqa: F811
) -> None:
    # candidate_id hashes the normalised title and year, so the same title and year (e.g. re-pasted
    # with a DOI) is one candidate; distinct ids differ in title and so in label. The id prefix
    # keeps labels unique regardless.
    first = env.candidate(TITLE_ONE, 2021)
    second = env.candidate(TITLE_ONE + " II", 2021, DOI_ONE)

    form = ui.RegistrationForm(env.project, reviewer="sh")

    assert set(form.candidate_select.options.values()) == {first, second}
    assert all(
        label.endswith(f"[{cid[:8]}]") for label, cid in form.candidate_select.options.items()
    )


@pytest.mark.integration
def test_view__hostile_text__is_escaped_and_markdown_stays_literal(env: Env) -> None:  # noqa: F811
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    works = _works()
    works[0]["title"] = f"{TITLE_ONE} {HOSTILE}"  # third-party text in the match table
    env.write_run(works)
    env.candidates.add(
        CandidateEvent.from_row(
            CandidateRow(title=HOSTILE, year=2020, doi=None),
            reviewer=HOSTILE,
            pasted_at=datetime(2026, 10, 22, tzinfo=UTC),
        )
    )
    form = ui.RegistrationForm(env.project, reviewer=HOSTILE)
    form.candidate_select.value = cid
    form.payload_select.value = PAYLOAD
    escaped = html.escape(HOSTILE)

    table = str(form.candidate_table.object)  # title and pasting reviewer of the hostile row
    assert table.count(escaped) == 2 and "<script>" not in table

    # Rendered to the page: no raw markup, markdown characters kept literally.
    assert form.view().objects[0].object.count(escaped) == 1  # header
    assert escaped in str(form.match_pane.object)
    assert escaped in str(form.confirm_pane.object)
    form.alert.object = ui._alert_html(f"Refused: {HOSTILE}", 1)  # a writer message echoing input
    assert escaped in str(form.alert.object)
    for pane in (form.view().objects[0], form.match_pane, form.confirm_pane, form.alert):
        assert "<script>" not in str(pane.object)
    assert isinstance(form.confirm_pane, pn.pane.HTML)  # not Markdown, which would render *x*


@pytest.mark.integration
def test_view__identical_refusals__render_distinct_alert_objects(env: Env) -> None:  # noqa: F811
    form = _form(env, env.candidate(TITLE_ONE, 2022))  # near without the tick: refused twice
    form.register()
    first = form.alert.object
    form.register()

    assert form.alert.object != first
    assert _alert_text(form) == re.sub(r' data-attempt="\d+"', "", html.unescape(str(first)))


def _picked(env: Env, cid: str) -> ui.RegistrationForm:  # noqa: F811
    form = ui.RegistrationForm(env.project, reviewer="sh")
    form.candidate_select.value = cid
    form.payload_select.value = PAYLOAD
    assert not form.register_button.disabled or form.rule_select.value == "near_confirmed"
    return form


@pytest.mark.integration
def test_view__response_choice__survives_a_reload_for_same_candidate_and_rule(
    env: Env,  # noqa: F811
) -> None:
    form = _picked(env, env.candidate(TITLE_ONE, 2021, DOI_ONE))

    form.reload()

    assert form.payload_select.value == PAYLOAD and not form.register_button.disabled


@pytest.mark.integration
def test_view__response_choice__cleared_on_candidate_change(env: Env) -> None:  # noqa: F811
    a = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    b = env.candidate(TITLE_ONE, 2022)  # the same response is offered for it too
    form = _picked(env, a)

    form.candidate_select.value = b

    assert PAYLOAD in form.payload_select.options.values()
    assert form.payload_select.value is None and form.register_button.disabled


@pytest.mark.integration
def test_view__response_choice__cleared_on_rule_change(env: Env) -> None:  # noqa: F811
    # DOI matches and the same title has another year: `doi` and `near_confirmed` both offer it.
    form = _picked(env, env.candidate(TITLE_ONE, 2022, DOI_ONE))
    assert form.rule_select.value == "doi"

    form.rule_select.value = "near_confirmed"

    assert PAYLOAD in form.payload_select.options.values()
    assert form.payload_select.value is None and form.register_button.disabled


# -- Reverse and Active registrations (ADR 0035 PR 4) -------------------------------------------


def _registered(env: Env) -> tuple[ui.ReversalForm, str, str]:  # noqa: F811
    """A reverse form over one active registration; returns (form, event_id, record_id)."""
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    form = ui.ReversalForm(env.project, reviewer="rv")
    form.select.value = event.event_id
    return form, event.event_id, event.record_id


def _all(project: Project) -> list[Any]:
    return list(load_registration_events(project.registrations_path))


def _spy_reverse(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    real = ui.reverse_registration
    monkeypatch.setattr(
        ui, "reverse_registration", lambda *a, **k: (calls.append(k), real(*a, **k))[1]
    )
    return calls


@pytest.mark.integration
@pytest.mark.parametrize("code", REVERSAL_REASONS)
def test_view__reverse_each_code__appends_reverse_event(env: Env, code: str) -> None:  # noqa: F811
    form, event_id, _ = _registered(env)
    form.reason_select.value = code
    form.note_input.value = "because"
    assert not form.reverse_button.disabled

    message = form.reverse()

    _register, reverse = _all(env.project)
    assert (reverse.action, reverse.reverses, reverse.reason) == ("reverse", event_id, code)
    assert message.startswith("Reversed ")
    assert "decisions.jsonl" in message
    assert "next `prismabib build <slug> --rebuild`" in message  # a plain build loads nothing
    assert "Reversed" in html.unescape(str(form.alert.object))


@pytest.mark.integration
def test_view__reverse_other_without_note__refused_and_log_unchanged(env: Env) -> None:  # noqa: F811
    form, event_id, _ = _registered(env)
    form.reason_select.value = "other"
    form.note_input.value = "   "
    assert form.reverse_button.disabled  # the predicate disables the button ...
    before = env.snapshot()
    with pytest.raises(RegistrationRefusal) as writer:
        env.reverse(event_id, reason="other")
    assert env.snapshot() == before

    message = form.reverse()  # ... and the writer still refuses a click that gets through

    assert message == f"Refused: {writer.value}"
    assert _alert_text(form) == f'<div role="alert">{message}</div>'
    assert env.snapshot() == before  # bytes and checksum sidecar
    assert len(_all(env.project)) == 1


@pytest.mark.integration
def test_view__reverse__reviewer_and_note_are_passed_through(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    form, event_id, _ = _registered(env)
    calls = _spy_reverse(monkeypatch)
    form.reason_select.value = "wrong_title"
    form.note_input.value = "title was truncated"

    form.reverse()

    (call,) = calls
    assert call == {
        "reverses": event_id,
        "reviewer": "rv",
        "reason": "wrong_title",
        "note": "title was truncated",
    }
    assert _all(env.project)[-1].reviewer == "rv"
    assert _all(env.project)[-1].note == "title was truncated"


@pytest.mark.integration
def test_view__reverse_blank_note_for_a_non_other_reason__passes_none(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    form, _, _ = _registered(env)
    calls = _spy_reverse(monkeypatch)
    form.reason_select.value = "wrong_doi"
    form.note_input.value = "  "

    form.reverse()

    assert calls[0]["note"] is None and _all(env.project)[-1].note is None


def _norm(project: Project) -> list[dict[str, Any]]:
    """Events modulo event_id/ts/record_id, with ``reverses`` replaced by its register's index."""
    events = _all(project)
    registers = [e.event_id for e in events if e.action == "register"]
    out = []
    for e in events:
        row = e.model_dump(mode="json", exclude={"event_id", "ts", "record_id"})
        if "reverses" in row:
            row["reverses"] = f"register#{registers.index(row['reverses'])}"
        out.append(row)
    return out


@pytest.mark.integration
def test_view__reverse__appends_event_equal_to_cli(env: Env, tmp_path: Path) -> None:  # noqa: F811
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    other_root = tmp_path / "copy"
    shutil.copytree(env.project.root, other_root / "reg")
    other = Project.open("reg", root=other_root)
    # The copy has its own log; its register event is a different object from the view's.
    cli_target = _all(other)[0].event_id
    argv = ["reverse", "reg", cli_target, "--reviewer", "rv", "--reason", "other", "--note", "n1"]
    result = CliRunner().invoke(app, [*argv, "--root", str(other_root)])
    form = ui.ReversalForm(env.project, reviewer="rv")
    form.select.value = event.event_id
    form.reason_select.value, form.note_input.value = "other", "n1"

    form.reverse()

    assert result.exit_code == 0
    assert _norm(env.project) == _norm(other)
    assert _norm(env.project)[-1]["reverses"] == "register#0"
    assert _all(env.project)[-1].reverses == event.event_id


@pytest.mark.integration
def test_view__reverse_after_decision__orphan_count_shown_then_reversed(env: Env) -> None:  # noqa: F811
    form, event_id, record_id = _registered(env)
    env.build()  # Layer 1 learns the registration; a decision needs an S_raw record
    log = DecisionLog(env.project, id_factory=SeededIdFactory(seed=0, prefix="ev"))
    for stage, reviewer in ((TA, "kp"), (TA, "kp"), (TA, "ab"), (FT, "kp")):
        log.append(stage=stage, record_id=record_id, reviewer=reviewer, decision="include")
    form.select.value = None
    form.select.value = event_id  # re-select: the confirmation reads the decisions
    impact = reversal_impact(env.project, record_id)
    decisions = env.project.decisions_path.read_bytes()
    assert impact.decisions_orphaned == 3  # the superseded kp event is not counted

    # Before the write the confirmation names the count and the inclusion state.
    text = str(form.confirm_pane.object)
    included = "is currently included" if impact.currently_included else "is not currently included"
    assert "orphans 3 current" in text and included in text and record_id in text

    form.reason_select.value = "not_eligible"
    form.reverse()

    assert env.project.decisions_path.read_bytes() == decisions  # the decisions stay
    assert record_id not in str(form.active_table.object)
    assert not form.select.options
    # Layer 1 changes only at build: rebuild, then the figures move.
    env.build()
    assert compute_flow_counts(env.project).registered == 0
    orphans = project_orphaned_decisions(env.project)
    assert len(orphans) == impact.decisions_orphaned == 3
    assert {o.record_id for o in orphans} == {record_id}


@pytest.mark.integration
def test_view__impact_unknown__alert_shown_and_reverse_still_available(env: Env) -> None:  # noqa: F811
    form, event_id, record_id = _registered(env)  # no store was built: the helper cannot read one
    with pytest.raises(StoreError) as helper:
        reversal_impact(env.project, record_id)
    form.select.value = None
    form.select.value = event_id

    assert f"Impact unknown: {helper.value}" in html.unescape(str(form.alert.object))
    assert 'role="alert"' in str(form.alert.object)
    assert "unknown" in str(form.confirm_pane.object)
    assert "orphans" not in str(form.confirm_pane.object)  # never a guessed count
    form.reason_select.value = "wrong_doi"
    assert not form.reverse_button.disabled

    form.reverse()

    assert [e.action for e in _all(env.project)] == ["register", "reverse"]


@pytest.mark.integration
def test_view__reverse_non_active_or_unknown__never_reaches_reversal_impact(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    form, event_id, _ = _registered(env)
    asked: list[str] = []
    monkeypatch.setattr(ui, "reversal_impact", lambda _p, rid: asked.append(rid))
    form.reason_select.value = "wrong_doi"
    form.select.value = "01NOSUCHEVENT"  # unknown id
    assert asked == [] and form.reverse_button.disabled and form.confirm_pane.object == ""
    before = env.snapshot()

    unknown = form.reverse()

    assert unknown.startswith("Refused: ") and env.snapshot() == before
    form.select.value = event_id  # an active registration: the one legitimate ask
    asked_for = list(asked)
    assert len(asked_for) == 1
    env.reverse(event_id)  # reversed elsewhere while this form is open: now stale
    stale = form.reverse()

    assert stale.startswith("Refused: ") and len(_all(env.project)) == 2
    assert asked == asked_for  # the Reverse button never asks
    assert not form.select.options  # the display was re-read after the attempt
    form.select.value = event_id  # inactive now: not asked about either
    assert asked == asked_for


@pytest.mark.integration
def test_view__active_registrations__table_lists_text_and_drops_the_reversed(
    env: Env,  # noqa: F811
) -> None:
    form, event_id, record_id = _registered(env)
    cells = html.unescape(re.sub(r"<[^>]+>", " ", str(form.active_table.object)))

    for text in (record_id, TITLE_ONE, "doi", "sh", _all(env.project)[0].ts.isoformat()):
        assert text in cells
    assert list(form.select.options.values()) == [event_id]

    form.reason_select.value = "wrong_doi"
    form.reverse()

    assert record_id not in str(form.active_table.object)
    assert form.select.value is None and form.reason_select.value is None


@pytest.mark.integration
def test_view__register_then_reverse__sections_refresh_each_other(env: Env) -> None:  # noqa: F811
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
    column = ui.registration_view(env.project, reviewer="sh")
    assert any(getattr(o, "name", "") == "Active registrations" for o in column.objects)
    register = ui.RegistrationForm(env.project, reviewer="sh")
    reverse = ui.ReversalForm(env.project, reviewer="sh", register=register)
    register.on_change, reverse.on_change = reverse.reload, register.reload
    register.candidate_select.value = cid
    register.payload_select.value = PAYLOAD  # the reviewer picks the response

    register.register()
    assert list(reverse.select.options.values())  # the new registration is offered at once
    assert "Registered" in html.unescape(str(register.alert.object))  # reload kept the alert

    reverse.select.value = next(iter(reverse.active))
    reverse.reason_select.value = "wrong_doi"
    reverse.reverse()

    assert "Reversed" in html.unescape(str(register.alert.object))  # one shared alert pane
    assert register.candidate_select.options  # the candidate can be registered again


@pytest.mark.integration
def test_view__reverse__raw_and_decisions_byte_identical(env: Env) -> None:  # noqa: F811
    form, _, _ = _registered(env)
    decisions = env.project.root / "decisions" / "decisions.jsonl"
    before = (_raw_digest(env.project), decisions.exists())
    form.reason_select.value = "wrong_doi"

    form.reverse()

    assert (_raw_digest(env.project), decisions.exists()) == before


@pytest.mark.integration
def test_view__reverse_refusal__is_role_alert_in_rendered_html(env: Env) -> None:  # noqa: F811
    form, _, _ = _registered(env)
    form.reason_select.value = "other"
    form.reverse()  # refused: 'other' without a note

    out = io.StringIO()
    pn.Column(*form.view_objects(), form.alert).save(out)

    assert re.search(r"role=(&amp;quot;|&quot;|\\?\")alert", out.getvalue())
    assert "requires a non-empty note" in html.unescape(html.unescape(out.getvalue()))


@pytest.mark.integration
def test_widgets__reverse_section_all_labelled(env: Env) -> None:  # noqa: F811
    form = ui.ReversalForm(env.project, reviewer="sh")

    assert form.widgets()
    for widget in form.widgets():
        assert widget.name.strip() and not AUTO_NAME.match(widget.name), widget
    assert tuple(form.reason_select.options) == REVERSAL_REASONS


@pytest.mark.integration
def test_view__reverse_form__blank_reviewer_raises(env: Env) -> None:  # noqa: F811
    with pytest.raises(ValidationError):
        ui.ReversalForm(env.project, reviewer=" ")


@pytest.mark.integration
def test_view__register_success__names_the_rebuild_command(env: Env) -> None:  # noqa: F811
    message = _form(env, env.candidate(TITLE_ONE, 2021, DOI_ONE)).register()

    assert message.startswith("Registered ")
    assert "next `prismabib build <slug> --rebuild`" in message


@pytest.mark.integration
def test_view__reverse_path__hostile_text_is_escaped(
    env: Env,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A hostile title and pasting reviewer reach the active table; a hostile impact error and
    # record id reach the alert and the confirmation.
    pasted = CandidateEvent.from_row(
        CandidateRow(title=f"{TITLE_ONE} {HOSTILE}", year=2021, doi=DOI_ONE),
        reviewer=HOSTILE,
        pasted_at=datetime(2026, 10, 22, tzinfo=UTC),
    )
    env.candidates.add(pasted)
    cid = pasted.candidate_id
    works = _works()
    works[0]["title"] = f"{TITLE_ONE} {HOSTILE}"
    env.write_run(works)
    event = env.register(cid, reviewer=HOSTILE)
    form = ui.ReversalForm(env.project, reviewer=HOSTILE)
    escaped = html.escape(HOSTILE)

    table = str(form.active_table.object)
    assert table.count(escaped) == 2 and "<script>" not in table  # candidate title, reviewer

    def unknown(_project: Project, _rid: str) -> object:
        raise StoreError(HOSTILE)

    monkeypatch.setattr(ui, "reversal_impact", unknown)
    form.select.value = event.event_id
    assert escaped in str(form.alert.object) and "<script>" not in str(form.alert.object)

    monkeypatch.setattr(ui, "reversal_impact", lambda _p, _r: ui.ReversalImpact(HOSTILE, True, 2))
    form.select.value = None
    form.select.value = event.event_id
    assert escaped in str(form.confirm_pane.object)
    assert "<script>" not in str(form.confirm_pane.object)
    assert isinstance(form.confirm_pane, pn.pane.HTML)  # Markdown would render *x*
    assert "<script>" not in str(form.view_objects()[0].object)


@pytest.mark.integration
def test_view__shared_alert__attempt_counter_is_monotonic_across_sections(
    env: Env,  # noqa: F811
) -> None:
    register = _form(env, env.candidate(TITLE_ONE, 2022))  # near without the tick: refused
    reverse = ui.ReversalForm(env.project, reviewer="sh", register=register)
    seen = []

    register.register()
    seen.append(str(register.alert.object))
    reverse.reverse()  # nothing selected: refused
    seen.append(str(register.alert.object))
    register.register()
    seen.append(str(register.alert.object))

    attempts = [int(re.search(r'data-attempt="(\d+)"', a).group(1)) for a in seen]  # type: ignore[union-attr]
    assert attempts == sorted(set(attempts)) and len(attempts) == 3
    assert register.alert is reverse.alert
