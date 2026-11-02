"""The manual-entry form and conflict panel drive the same writers as the CLI (no browser)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.capture import manual_ui as ui
from prismabib.errors import ValidationError
from prismabib.models import load_conflict_events, load_manual_events
from prismabib.project import Project
from prismabib.store.db import connect
from tests.integration.store.test_provenance_folds import R1, R2, _build, _project, _prov


def _built(tmp_path: Path) -> Project:
    project = _project(tmp_path)
    _build(project)
    return project


@pytest.mark.integration
def test_widgets__all_labelled(tmp_path: Path) -> None:
    project = _built(tmp_path)
    for widget in [
        *ui.ManualEntryForm(project, reviewer="kp").widgets(),
        *ui.ConflictPanel(project, reviewer="kp").widgets(),
    ]:
        assert widget.name.strip() and not widget.name[-3:].isdigit(), widget


@pytest.mark.integration
def test_view__blank_reviewer__raises_validation_error(tmp_path: Path) -> None:
    project = _built(tmp_path)
    with pytest.raises(ValidationError):
        ui.manual_view(project, reviewer=" ")
    with pytest.raises(ValidationError):
        ui.ConflictPanel(project, reviewer=" ")


@pytest.mark.integration
def test_form__load_enables_only_unavailable_fields_and_save_goes_through_the_writer(
    tmp_path: Path,
) -> None:
    project = _built(tmp_path)
    form = ui.ManualEntryForm(project, reviewer="kp")

    form.record_input.value = R2
    form.load()

    assert {f: not w.disabled for f, w in form.inputs.items()} == {
        "abstract": True,
        "authors": True,
        "affiliations": True,
    }
    form.inputs["abstract"].value = "Typed abstract"
    form.inputs["affiliations"].value = "A; B; C; D"  # malformed: refused, nothing written
    message = form.save()

    assert "Entered abstract" in message
    assert "Refused affiliations" in message
    (event,) = load_manual_events(project.manual_entries_path)
    assert event.field == "abstract"
    _build(project)
    assert _prov(project, R2, "abstract")[:2] == ("manual", 5)


@pytest.mark.integration
def test_form__record_with_every_field_supplied__nothing_is_enterable(tmp_path: Path) -> None:
    project = _built(tmp_path)
    form = ui.ManualEntryForm(project, reviewer="kp")
    form.record_input.value = R1  # abstract, authors, affiliations all supplied by Scopus

    form.load()

    assert all(w.disabled for w in form.inputs.values())
    assert form.save_button.disabled
    assert not project.manual_entries_path.exists()


@pytest.mark.integration
def test_form__unknown_record__is_refused_on_load(tmp_path: Path) -> None:
    form = ui.ManualEntryForm(_built(tmp_path), reviewer="kp")
    form.record_input.value = "scopus:nope"

    assert "Refused" in form.load()
    assert form.doi_button.disabled and form.save_button.disabled


@pytest.mark.integration
def test_form__open_publisher_page__opens_the_doi_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(ui.webbrowser, "open", lambda url: opened.append(url) or True)
    form = ui.ManualEntryForm(_built(tmp_path), reviewer="kp")
    form.record_input.value = R2
    form.load()

    assert not form.doi_button.disabled
    assert form.open_publisher()
    assert opened == ["https://doi.org/10.1000/two"]


@pytest.mark.integration
def test_form__record_without_doi__button_disabled_with_text_and_nothing_opens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _built(tmp_path)
    connection = connect(project, read_only=False)
    connection.execute("UPDATE records SET doi = NULL WHERE record_id = ?", [R2])
    connection.close()
    opened: list[str] = []
    monkeypatch.setattr(ui.webbrowser, "open", lambda url: opened.append(url) or True)
    form = ui.ManualEntryForm(project, reviewer="kp")
    form.record_input.value = R2

    form.load()

    assert form.doi_button.disabled
    assert form.doi_note.object == ui.NO_DOI_TEXT
    assert not form.open_publisher()
    assert opened == []


@pytest.mark.integration
def test_panel__lists_open_conflicts_and_resolves_through_the_writer(tmp_path: Path) -> None:
    project = _built(tmp_path)
    panel = ui.ConflictPanel(project, reviewer="kp")
    assert set(panel.conflicts) == {f"{R1} / {f}" for f in ("abstract", "affiliations", "authors")}
    panel.select.value = f"{R1} / abstract"

    assert "Scopus abstract one" in panel.values.object
    assert "An openalex abstract" in panel.values.object
    message = panel.resolve("openalex")

    assert "Resolved" in message
    (event,) = load_conflict_events(project.conflict_resolutions_path)
    assert (event.record_id, event.field, event.chosen_side) == (R1, "abstract", "openalex")


@pytest.mark.integration
def test_panel__manual_side_without_value__is_refused_and_writes_nothing(tmp_path: Path) -> None:
    project = _built(tmp_path)
    panel = ui.ConflictPanel(project, reviewer="kp")
    panel.select.value = f"{R1} / abstract"

    assert panel.resolve("manual").startswith("Refused")
    assert not project.conflict_resolutions_path.exists()


@pytest.mark.integration
def test_panel__without_a_store__is_empty_and_buttons_disabled(tmp_path: Path) -> None:
    project = Project.init("nostore", title="T", root=tmp_path)
    panel = ui.ConflictPanel(project, reviewer="kp")

    assert panel.conflicts == {}
    assert all(b.disabled for b in panel.buttons.values())
    assert panel.resolve("scopus").startswith("Refused")
