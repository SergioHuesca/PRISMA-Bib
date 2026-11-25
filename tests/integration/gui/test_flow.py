"""The Flow counts screen and registering a Record, over a stubbed Layer 0 (ticket #334)."""

from __future__ import annotations

import re
import threading
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("panel")

import panel as pn
from typer.testing import CliRunner

from prismabib.capture.registrations import load_registration_events
from prismabib.cli import app
from prismabib.errors import ConfigError, ProjectLockedError, ValidationError
from prismabib.gui import flow_layer as fl
from prismabib.gui import service, tasks
from prismabib.gui.flow import build_flow
from prismabib.gui.new_project import build_stepper
from prismabib.project import Project
from prismabib.source_mode import command_refusal
from prismabib.store.load import build_store
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.integration.capture.test_registrations import (
    DOI_ONE,
    OA_RUN,
    PAYLOAD,
    SELECT,
    TITLE_ONE,
    _works,
)
from tests.integration.gui.helpers import returns_while_held, wait_for
from tests.integration.gui.test_home import unlabelled_controls
from tests.prisma_helpers import CorpusSpec, RecordSpec, build_project
from tests.store_helpers import write_sealed_openalex_run

pytestmark = pytest.mark.integration

BANNED = ("corpus", "final corpus")
EXACT = f"{TITLE_ONE}, 2021, {DOI_ONE}"
NEAR = f"{TITLE_ONE}, 2022"  # same title, other year: a near match


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "settings.env"))
    tasks.reset_runners()
    yield
    tasks.reset_runners()


def make_project(root: Path, *, built: bool = True) -> Project:
    """Three Scopus Records, built; plus a sealed OpenAlex response holding the missed work."""
    project = build_project(root, CorpusSpec(records=[RecordSpec(number=n) for n in (1, 2, 3)]))
    write_sealed_openalex_run(
        project.raw_dir,
        OA_RUN,
        [{"lookup": "doi_batch", "identifiers": [DOI_ONE], "works": _works()}],
        api_view=SELECT,
    )
    if not built:
        project.db_path.unlink()
    return project


def _add(project: Project, text: str) -> str:
    with service.Session(project) as session:
        fl.add_candidates(session, text, reviewer="Ada")
    return fl.candidates(project)[-1].event.candidate_id


def _register(project: Project, cid: str, rule: str = "doi", *, near: bool = False) -> str:
    with service.Session(project) as session:
        event = fl.register_record(
            session,
            candidate_id=cid,
            reviewer="Ada",
            payload_file=PAYLOAD,
            match_rule=rule,
            confirmed_near=near,
        )
    return event.record_id


def _text(view: pn.viewable.Viewable) -> str:
    parts = [str(getattr(o, "object", "") or "") for o in view.select(pn.pane.base.PaneBase)]
    parts += [str(w.name) for w in view.select(pn.widgets.Widget)]
    return "\n".join(parts)


def cli_numbers(project: Project) -> list[int]:
    """Every count the CLI ``flow`` command prints, in order (the sign is its own business)."""
    result = CliRunner().invoke(app, ["flow", project.slug, "--root", str(project.root.parent)])
    assert result.exit_code == 0, result.output
    found = (re.match(r"^ {2}.*\S\s+(-?[\d,]+)$", line) for line in result.output.splitlines())
    return [abs(int(m.group(1).replace(",", ""))) for m in found if m]


def screen_numbers(project: Project) -> list[int]:
    return [r.value for r in fl.flow_rows(fl.flow_counts(project), mode=project.source_mode)]


def test_flow_rows__same_numbers_as_the_cli(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    assert screen_numbers(project) == cli_numbers(project)
    assert len(screen_numbers(project)) > 10


def test_register__then_rebuild__own_line_and_identified_unchanged(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    before = fl.flow_counts(project)
    assert "Registered records" not in _text(build_flow(project))

    record_id = _register(project, _add(project, EXACT))
    assert fl.pending_registrations(project, fl.flow_counts(project)) == 1
    assert "not counted yet" in _text(build_flow(project))  # takes effect at the next build

    build_store(project, rebuild=True)
    after = fl.flow_counts(project)
    text = _text(build_flow(project))

    assert record_id.startswith("registered:")
    assert (before.registered, after.registered) == (0, 1)
    assert after.identified == before.identified
    assert "Registered records (added by a reviewer)" in text and "not counted yet" not in text
    assert screen_numbers(project) == cli_numbers(project)


def test_register__near_match__refused_without_confirmation_and_nothing_written(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)
    cid = _add(project, NEAR)

    with pytest.raises(ValidationError, match=r"needs the reviewer.s confirmation"):
        _register(project, cid, "near_confirmed")
    assert not project.registrations_path.exists()

    _register(project, cid, "near_confirmed", near=True)
    assert [e.match_rule for e in load_registration_events(project.registrations_path)] == [
        "near_confirmed"
    ]


def test_screen__near_match__button_waits_for_the_confirmation_box(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _add(project, NEAR)
    view = build_flow(project)
    select = {w.name: w for w in view.select(pn.widgets.Widget)}
    select["Reviewer name"].value = "Ada"
    register = select["Register this Record"]
    assert register.disabled

    select["Candidate"].value = select["Candidate"].options[next(iter(select["Candidate"].options))]
    select["Match rule"].value = "near_confirmed"
    select["Captured OpenAlex response"].value = next(
        iter(select["Captured OpenAlex response"].options.values())
    )
    box = select["I confirm this work is this candidate"]
    assert box.visible and register.disabled and not project.registrations_path.exists()

    box.value = True
    assert not register.disabled
    register.clicks += 1  # the click handler runs the same layer call

    assert project.registrations_path.exists()
    assert "Registered registered:" in _text(view)
    assert not box.value  # the choice reset, so the confirmation did too


def test_screen__exact_match__no_confirmation_box_offered(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    _add(project, EXACT)
    view = build_flow(project)
    select = {w.name: w for w in view.select(pn.widgets.Widget)}

    select["Candidate"].value = next(iter(select["Candidate"].options.values()))
    select["Captured OpenAlex response"].value = next(
        iter(select["Captured OpenAlex response"].options.values())
    )

    assert not select["I confirm this work is this candidate"].visible
    assert not select["Register this Record"].disabled


def test_register__no_built_store__refused_with_the_next_step(tmp_path: Path) -> None:
    project = make_project(tmp_path, built=False)
    cid = _add(project, EXACT)

    with pytest.raises(ValidationError, match="Finish the Build step"):
        _register(project, cid)
    view = build_flow(project)
    assert "Finish the Build step" in _text(view)  # a sentence and the next step
    assert all(
        w.disabled for w in view.select(pn.widgets.Widget) if w.name == "Register this Record"
    )
    assert not project.registrations_path.exists()


def test_register__scopus_only_mode__refused_as_on_the_cli(tmp_path: Path) -> None:
    project = service.create_project(
        "demo", title="Demo", root=tmp_path, source_mode="scopus", reviewer="Ada"
    )
    project.db_path.write_bytes(b"x")  # only the mode may refuse here, not the missing store
    cid = _add(project, EXACT)

    with pytest.raises(ConfigError):
        _register(project, cid)
    assert fl.registration_refusal(project) is not None


def test_add_candidates__blank_reviewer_or_no_rows__refused(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    with service.Session(project) as session:
        with pytest.raises(ValidationError):
            fl.add_candidates(session, EXACT, reviewer=" ")
        with pytest.raises(ValidationError):
            fl.add_candidates(session, "\n", reviewer="Ada")
    assert fl.candidates(project) == []


def test_read_only_session__register__refused(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    cid = _add(project, EXACT)
    held = service.Session(project)
    second = service.Session(project)

    with pytest.raises(ProjectLockedError):
        fl.register_record(
            second, candidate_id=cid, reviewer="Ada", payload_file=PAYLOAD, match_rule="doi"
        )
    held.close()


def test_screen__headless__builds_with_labels_and_no_banned_words(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    view = build_flow(project)
    text = _text(view).lower()

    assert unlabelled_controls(view) == []
    assert "included studies" in text
    assert [b for b in BANNED if b in text] == []
    assert all(isinstance(w, pn.widgets.Widget) for w in view.select(pn.widgets.Button))


def test_screen__no_store__shows_a_sentence_and_the_next_step(tmp_path: Path) -> None:
    project = make_project(tmp_path, built=False)

    text = _text(build_flow(project))

    assert "Layer 1 store" in text or "store" in text
    assert "Next" in text or "Build" in text or "build" in text


def test_stepper__flow_step__shows_the_screen(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    stepper = build_stepper(project)
    assert "Register a Record the search missed" not in _text(stepper)

    next(
        b for b in stepper.select(pn.widgets.Button) if b.name.startswith("Flow counts")
    ).clicks += 1

    assert "Register a Record the search missed" in _text(stepper)


# -- the cascade control (ticket #399) ---------------------------------------------------------


RUN = "Run the cascade"
CANCEL = "Cancel the cascade"


def _mode_project(root: Path, mode: str) -> Project:
    return service.create_project(
        "demo",
        title="Demo",
        root=root,
        source_mode=mode,
        reviewer="Ada",  # type: ignore[arg-type]
    )


def _button(view: pn.viewable.Viewable, name: str) -> pn.widgets.Button:
    return next(b for b in view.select(pn.widgets.Button) if b.name == name)


def _fake_cascade(
    monkeypatch: pytest.MonkeyPatch, *lines: str, hold: threading.Event | None = None
) -> list[dict[str, object]]:
    """Replace the library cascade: record the call, emit ``lines``, wait on ``hold`` after one."""
    calls: list[dict[str, object]] = []

    def fake(project: Project, **kwargs: object) -> list[str]:
        calls.append({k: v for k, v in kwargs.items() if k != "emit"})
        emit = kwargs["emit"]
        assert callable(emit)
        for n, line in enumerate(lines):
            emit(line)
            if hold is not None and n == 0:
                assert hold.wait(10)
        return list(lines)

    monkeypatch.setattr(fl, "run_cascade", fake)
    return calls


@pytest.mark.parametrize("mode", ["openalex", "scopus+openalex"])
def test_cascade_control__openalex_modes__present_and_enabled(tmp_path: Path, mode: str) -> None:
    view = build_flow(_mode_project(tmp_path, mode))

    assert not _button(view, RUN).disabled
    assert unlabelled_controls(view) == []


def test_cascade_control__scopus_mode__disabled_with_the_cli_refusal(tmp_path: Path) -> None:
    project = _mode_project(tmp_path, "scopus")
    view = build_flow(project)
    reason = command_refusal(project, "cascade")

    assert reason
    assert _button(view, RUN).disabled
    assert reason in _text(view)


def test_cascade_control__click__calls_the_library_with_the_cli_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _mode_project(tmp_path, "scopus+openalex")
    calls = _fake_cascade(monkeypatch, "openalex: 1 DOIs, 0 titles planned")
    view = build_flow(project)

    _button(view, RUN).clicks += 1
    tasks.runner_for(project).join(30)

    assert calls == [{"opt_in": False, "scopus_budget": None, "refresh": False, "dry_run": False}]


def test_cascade_control__running__shows_progress_and_cancel_stops_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _mode_project(tmp_path, "openalex")
    release = threading.Event()
    _fake_cascade(monkeypatch, "first report line", "second report line", hold=release)
    view = build_flow(project)

    _button(view, RUN).clicks += 1
    wait_for(
        lambda: fl.cascade_status(project),
        lambda s: s.running and s.progress == "first report line",
    )
    shown = returns_while_held(lambda: _text(build_flow(project)), release)
    assert "first report line" in shown and "Running the cascade" in shown
    assert _button(build_flow(project), RUN).disabled
    assert not _button(build_flow(project), CANCEL).disabled

    fl.cancel_cascade(project)
    stopping = returns_while_held(lambda: _text(build_flow(project)), release)
    assert "Stopping the cascade" in stopping and "Running the cascade" not in stopping
    release.set()
    tasks.runner_for(project).join(30)

    assert fl.cascade_status(project).outcome == "cancelled"
    text = _text(build_flow(project))
    assert "Stopped as you asked" in text
    assert "Responses already captured were kept" in text
    assert "store may not have been rebuilt" in text and "Build step" in text


def test_cascade_control__failure__is_a_sentence_with_the_next_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _mode_project(tmp_path, "openalex")

    def boom(*_: object, **__: object) -> list[str]:
        raise ConfigError("A setting is missing.")

    monkeypatch.setattr(fl, "run_cascade", boom)
    _button(build_flow(project), RUN).clicks += 1
    tasks.runner_for(project).join(30)

    text = _text(build_flow(project))
    assert "The cascade failed." in text and service.translate(ConfigError("x")).next_step in text


def test_registration__guard__starts_no_task(tmp_path: Path) -> None:
    project = make_project(tmp_path)

    _register(project, _add(project, EXACT))

    assert tasks.runner_for(project).status() == tasks.TaskStatus()
    assert len(load_registration_events(project.registrations_path)) == 1


def test_cascade_summary__after_a_run__says_in_sentences_who_has_the_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = make_project(tmp_path)  # holds the sealed response for TITLE_ONE / DOI_ONE
    _add(project, EXACT)
    _add(project, "Nobody Wrote This, 1999, 10.9999/missing")
    _fake_cascade(monkeypatch, "openalex: 2 DOIs, 0 titles planned")
    view = build_flow(project)

    _button(view, RUN).clicks += 1
    tasks.runner_for(project).join(30)
    text = _text(build_flow(project))

    assert f"{TITLE_ONE} (2021) now has the OpenAlex response it needs" in text
    assert "Nobody Wrote This (1999) still has no OpenAlex response" in text
    assert [b for b in BANNED if b in text.lower()] == []


def test_cascade_summary__near_match_only__points_to_register_not_cannot(
    tmp_path: Path,
) -> None:
    project = make_project(tmp_path)  # the sealed response is TITLE_ONE, 2021
    _add(project, NEAR)  # same title, year off by one: only the near rule matches

    text = fl.cascade_summary(project)

    assert f"{TITLE_ONE} (2022) has a close match" in text
    assert "Open Register and confirm" in text
    assert "cannot be registered" not in text and "now has the OpenAlex response" not in text
