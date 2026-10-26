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
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from prismabib.capture import registration_ui as ui
from prismabib.capture.registrations import RegistrationRefusal, load_registration_events
from prismabib.cli import app
from prismabib.errors import ValidationError
from prismabib.project import Project
from tests.integration.capture.test_registrations import (
    DOI_ONE,
    PAYLOAD,
    TITLE_ONE,
    Env,
    _works,
    env,  # noqa: F401  (pytest fixture)
)
from tests.store_helpers import make_entry

AUTO_NAME = re.compile(r"^[A-Za-z]+\d{3,}$")  # Panel's default, unlabelled name


def _form(env: Env, cid: str, rule: str | None = None) -> ui.RegistrationForm:  # noqa: F811
    form = ui.RegistrationForm(env.project, reviewer="sh")
    form.candidate_select.value = cid
    if rule is not None:
        form.rule_select.value = rule
    assert form.payload_select.value == PAYLOAD  # exactly one response offered
    return form


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
def test_view__register_doi_match__appends_event_equal_to_cli(env: Env, tmp_path: Path) -> None:  # noqa: F811
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)
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
            "--root",
            str(other_root),
        ],
    )
    form = _form(env, cid)
    message = form.register()

    assert result.exit_code == 0
    (gui,) = load_registration_events(env.project.registrations_path)
    assert message.startswith("Registered ") and gui.record_id in message
    assert _events(env.project) == _events(other)
    assert _events(env.project)[0]["match_rule"] == "doi"
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
    assert html.unescape(str(form.alert.object)) == f'<div role="alert">{message}</div>'
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
    assert html.unescape(str(form.alert.object)) == f'<div role="alert">{message}</div>'
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
