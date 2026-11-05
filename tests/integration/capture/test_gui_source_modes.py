"""ADR 0042 Decision 3 matrix and Decision 9: the GUI's Scopus-side actions follow the mode."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.capture import manual_ui, registration_ui
from tests.integration.capture.test_manual_view import _built
from tests.integration.capture.test_registrations import (
    DOI_ONE,
    PAYLOAD,
    TITLE_ONE,
    env,  # noqa: F401  (pytest fixture)
)
from tests.integration.store.test_provenance_folds import R1
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


# -- seeded: a candidate with a captured response and an open conflict exist, so only the mode
# -- can decide whether the controls are enabled ---------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize("refusal", [None, "refused in this mode"])
def test_gui__register_control__enabled_or_disabled_by_the_mode_alone(
    env: object,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    refusal: str | None,
) -> None:
    monkeypatch.setattr(registration_ui, "command_refusal", lambda _p, _c: refusal)
    env.write_run()  # type: ignore[attr-defined]
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)  # type: ignore[attr-defined]
    form = registration_ui.RegistrationForm(env.project, reviewer="sh")  # type: ignore[attr-defined]
    form.candidate_select.value = cid
    form.payload_select.value = PAYLOAD

    assert form.register_button.disabled is (refusal is not None)
    assert form.mode_refusal == refusal


@pytest.mark.integration
@pytest.mark.parametrize("refusal", [None, "refused in this mode"])
def test_gui__conflict_resolve_controls__enabled_or_disabled_by_the_mode_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: str | None
) -> None:
    monkeypatch.setattr(manual_ui, "command_refusal", lambda _p, _c: refusal)
    panel = manual_ui.ConflictPanel(_built(tmp_path), reviewer="kp")
    assert f"{R1} / abstract" in panel.conflicts  # an open conflict is selected

    assert all(b.disabled is (refusal is not None) for b in panel.buttons.values())
    assert panel.mode_refusal == refusal
