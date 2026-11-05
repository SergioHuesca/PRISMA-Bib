"""ADR 0042 Decision 3 matrix and Decision 9: the GUI's Scopus-side actions follow the mode."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.capture import manual_ui, registration_ui
from tests.openalex_search_helpers import openalex_project


@pytest.mark.integration
@pytest.mark.parametrize(
    ("mode", "register_refused", "resolve_refused"),
    [("scopus", True, True), ("scopus+openalex", False, False), ("openalex", False, True)],
)
def test_gui__scopus_actions__disabled_with_reason_in_openalex_mode(
    tmp_path: Path, mode: str, register_refused: bool, resolve_refused: bool
) -> None:
    project = openalex_project(tmp_path, mode)
    register = registration_ui.RegistrationForm(project, reviewer="kp")
    conflicts = manual_ui.ConflictPanel(project, reviewer="kp")

    assert (register.mode_refusal is not None) is register_refused
    assert (conflicts.mode_refusal is not None) is resolve_refused
    if register_refused:
        assert register.register_button.disabled
        assert mode in register.register()  # the handler refuses too, naming the mode
    if resolve_refused:
        assert all(b.disabled for b in conflicts.buttons.values())
        assert mode in conflicts.resolve("openalex")
    assert not project.registrations_path.exists()
    assert not project.conflict_resolutions_path.exists()


@pytest.mark.integration
@pytest.mark.parametrize(("mode", "prefix"), [("scopus", "scopus:"), ("openalex", "openalex:")])
def test_manual_form__placeholder__names_ids_of_the_mode(
    tmp_path: Path, mode: str, prefix: str
) -> None:
    form = manual_ui.ManualEntryForm(openalex_project(tmp_path, mode), reviewer="kp")

    assert form.record_input.placeholder.startswith(prefix)
