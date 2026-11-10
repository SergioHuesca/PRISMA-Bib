"""The Flow counts screen and registering a Record, over a stubbed Layer 0 (ticket #334)."""

from __future__ import annotations

import re
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
from prismabib.gui import layer, tasks
from prismabib.gui.flow import build_flow
from prismabib.gui.new_project import build_stepper
from prismabib.project import Project
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
    with layer.Session(project) as session:
        fl.add_candidates(session, text, reviewer="Ada")
    return fl.candidates(project)[-1].event.candidate_id


def _register(project: Project, cid: str, rule: str = "doi", *, near: bool = False) -> str:
    with layer.Session(project) as session:
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
    project = layer.create_project(
        "demo", title="Demo", root=tmp_path, source_mode="scopus", reviewer="Ada"
    )
    project.db_path.write_bytes(b"x")  # only the mode may refuse here, not the missing store
    cid = _add(project, EXACT)

    with pytest.raises(ConfigError):
        _register(project, cid)
    assert fl.registration_refusal(project) is not None


def test_add_candidates__blank_reviewer_or_no_rows__refused(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    with layer.Session(project) as session:
        with pytest.raises(ValidationError):
            fl.add_candidates(session, EXACT, reviewer=" ")
        with pytest.raises(ValidationError):
            fl.add_candidates(session, "\n", reviewer="Ada")
    assert fl.candidates(project) == []


def test_read_only_session__register__refused(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    cid = _add(project, EXACT)
    held = layer.Session(project)
    second = layer.Session(project)

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
