"""The Criteria form, headless (ticket #326)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.errors import StepLockedError
from prismabib.gui import layer
from prismabib.gui.criteria import build_criteria
from prismabib.gui.criteria_model import CriteriaFormError, criteria_from_form, form_from_criteria
from prismabib.gui.new_project import build_stepper
from prismabib.project import Project, scaffold_criteria
from tests.integration.gui.test_home import unlabelled_controls

pytestmark = pytest.mark.integration


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return layer.create_project(
        "demo", title="Demo", root=tmp_path, source_mode="openalex", reviewer="Ada"
    )


def _w(view: pn.Column, label: str) -> pn.widgets.Widget:
    return next(w for w in view.select(pn.widgets.Widget) if (w.name or "").startswith(label))


def _text(view: pn.Column) -> str:
    return "\n".join(str(p.object) for p in view.select(pn.pane.Markdown))


def _log_decision(project: Project) -> None:
    project.decisions_path.parent.mkdir(exist_ok=True)
    line = json.dumps({"schema_version": layer.CURRENT_PROJECT_VERSION}) + "\n"
    project.decisions_path.write_text(line, encoding="utf-8")


def test_form__saved__writes_criteria_the_library_reads_back(project: Project) -> None:
    view = build_criteria(project)
    _w(view, "Include languages").value = "English"
    _w(view, "Include from year").value = "2015"
    _w(view, "Reason codes for excluding at abstract").value = "OFF_TOPIC\nTOO_OLD"
    _w(view, "Save Criteria").clicks += 1

    saved = project.criteria
    assert saved.languages == ["English"]
    assert saved.temporal.year_start == 2015
    assert saved.manual_abstract.exclude_reason_codes == ["OFF_TOPIC", "TOO_OLD"]
    assert "saved" in _text(view)
    assert not (project.root / "criteria.yaml.tmp").exists()


HAND_EDITED = """\
# my protocol, version 2 -- do not lose this comment
version: 0.2.0   # bumped after the pilot
temporal:
  year_start: 2010
  year_end: 2024
subject_areas: []   # none on purpose
doc_types:
  include: [ar]
  conference_whitelist: []
languages: []
manual_abstract:
  exclude_reason_codes: [OFF_TOPIC]
manual_fulltext:
  exclude_reason_codes: [NO_FULL_TEXT]
"""
BACKUP = "criteria.yaml.before-app"


def _hand_edit(project: Project, text: str = HAND_EDITED) -> bytes:
    data = text.encode("utf-8")
    (project.root / "criteria.yaml").write_bytes(data)
    return data


def test_form__saving_over_a_hand_edited_file__keeps_the_original_byte_for_byte(
    project: Project,
) -> None:
    original = _hand_edit(project)
    view = build_criteria(project)
    _w(view, "Include languages").value = "English"
    _w(view, "Save Criteria").clicks += 1

    assert (project.root / BACKUP).read_bytes() == original
    assert project.criteria.languages == ["English"]
    assert BACKUP in _text(view) and "original" in _text(view)  # the screen says where it is


def test_form__second_save__does_not_overwrite_the_backup(project: Project) -> None:
    original = _hand_edit(project)
    view = build_criteria(project)
    _w(view, "Save Criteria").clicks += 1
    _hand_edit(project, HAND_EDITED.replace("my protocol", "my newer protocol"))  # edited again
    view = build_criteria(project)
    _w(view, "Save Criteria").clicks += 1

    assert (project.root / BACKUP).read_bytes() == original


def test_form__saving_over_a_file_the_app_wrote__makes_no_backup(project: Project) -> None:
    view = build_criteria(project)
    _w(view, "Save Criteria").clicks += 1  # the scaffold's comments go; the backup keeps them
    (project.root / BACKUP).unlink()

    view = build_criteria(project)
    _w(view, "Include languages").value = "English"
    _w(view, "Save Criteria").clicks += 1

    assert not (project.root / BACKUP).exists()
    assert BACKUP not in _text(view)


def test_layer__saving_an_unreadable_hand_edit__keeps_it_too(project: Project) -> None:
    broken = _hand_edit(project, "version: [")
    with layer.open_session("demo", root=project.root.parent) as session:
        layer.save_criteria(session, criteria_from_form(form_from_criteria(scaffold_criteria())))

    assert (project.root / BACKUP).read_bytes() == broken


def test_form__invalid_rule__message_sits_beside_its_field_and_nothing_is_written(
    project: Project,
) -> None:
    before = (project.root / "criteria.yaml").read_bytes()
    view = build_criteria(project)
    _w(view, "Include subject areas").value = "NOPE"
    _w(view, "Include from year").value = "abc"
    _w(view, "Save Criteria").clicks += 1

    items = list(view)
    after_subject = items[items.index(_w(view, "Include subject areas")) + 1]
    after_year = items[items.index(_w(view, "Include from year")) + 1]
    assert "NOPE" in str(after_subject.object)
    assert "integer" in str(after_year.object)
    assert (project.root / "criteria.yaml").read_bytes() == before


def test_form__year_window_reversed__error_is_beside_the_end_year(project: Project) -> None:
    form = form_from_criteria(project.criteria) | {
        "temporal.year_start": "2020",
        "temporal.year_end": "2010",
    }
    with pytest.raises(CriteriaFormError) as caught:
        criteria_from_form(form)
    assert set(caught.value.fields) == {"temporal.year_end"}


def test_form__hand_edited_file__loads_into_the_form(project: Project) -> None:
    path = project.root / "criteria.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("version: 0.1.0", "version: 3.4.5"),
        encoding="utf-8",
    )
    assert _w(build_criteria(project), "Criteria version").value == "3.4.5"


def test_form__broken_hand_edit__shows_a_sentence_not_a_traceback(project: Project) -> None:
    (project.root / "criteria.yaml").write_text("version: [", encoding="utf-8")
    view = build_criteria(project)
    assert "criteria.yaml" in _text(view)
    assert not view.select(pn.widgets.Button)


def test_form__decision_logged__is_read_only_with_the_reason(project: Project) -> None:
    _log_decision(project)
    view = build_criteria(project)
    assert "Decision has been logged" in _text(view)
    assert all(w.disabled for w in view.select(pn.widgets.Widget))


def test_layer__decision_logged__refuses_a_save(project: Project) -> None:
    _log_decision(project)
    before = (project.root / "criteria.yaml").read_bytes()
    with (
        layer.open_session("demo", root=project.root.parent) as session,
        pytest.raises(StepLockedError, match="Decision"),
    ):
        layer.save_criteria(session, project.criteria)
    assert (project.root / "criteria.yaml").read_bytes() == before


def test_form__another_window_holds_the_lock__save_says_so(project: Project) -> None:
    other = layer.Session(project)
    view = build_criteria(project)
    _w(view, "Save Criteria").clicks += 1
    assert "Another window" in _text(view)
    other.close()


def test_form__every_control_has_a_label(project: Project) -> None:
    assert unlabelled_controls(build_criteria(project)) == []


def test_stepper__criteria_step__shows_the_form(project: Project) -> None:
    view = build_stepper(project)
    button = next(b for b in view.select(pn.widgets.Button) if b.name.startswith("Criteria"))
    button.clicks += 1
    assert any(w.name == "Save Criteria" for w in view.select(pn.widgets.Button))
