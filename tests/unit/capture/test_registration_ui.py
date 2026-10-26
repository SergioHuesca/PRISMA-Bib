"""ADR 0035 Decision 11 unit tests: the pure registration view models (no Panel, no log)."""

from __future__ import annotations

import ast
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from prismabib.capture import registration_ui as ui
from prismabib.capture.candidates import CandidateEvent, CandidateRow, CandidateView
from prismabib.capture.registrations import REVERSAL_REASONS
from prismabib.prisma.impact import ReversalImpact

PASTED = datetime(2026, 10, 22, tzinfo=UTC)
WORK: dict[str, Any] = {
    "id": "https://openalex.org/W1",
    "doi": "https://doi.org/10.9999/x",
    "title": "A Synthetic Registered Article",
    "publication_year": 2021,
}


def _view(
    title: str = "A Synthetic Registered Article", year: int = 2021, doi: str | None = None
) -> CandidateView:
    event = CandidateEvent.from_row(
        CandidateRow(title=title, year=year, doi=doi), reviewer="sh", pasted_at=PASTED
    )
    return CandidateView(event=event, doi=doi)


@pytest.mark.unit
def test_candidate_rows__registered_candidate__shows_record_and_is_not_selectable() -> None:
    free, taken = _view("One", 2020), _view("Two", 2021)

    rows = ui.candidate_rows([free, taken], {taken.event.candidate_id: "registered:E1"})

    assert [(r.title, r.state, r.selectable) for r in rows] == [
        ("One", "unregistered", True),
        ("Two", "registered (registered:E1)", False),
    ]
    assert rows[0].candidate_id == free.event.candidate_id


@pytest.mark.unit
def test_reason_options__equal_REVERSAL_REASONS() -> None:
    assert ui.reason_options() is REVERSAL_REASONS


@pytest.mark.unit
@pytest.mark.parametrize(
    ("reason", "note", "ok"),
    [(r, n, r != "other" or bool(n.strip())) for r in REVERSAL_REASONS for n in ("", "  \t", "why")]
    + [("not_a_reason", "why", False)],
)
def test_note_required__only_for_other(reason: str, note: str, ok: bool) -> None:
    assert ui.can_reverse(reason, note) is ok


@pytest.mark.unit
def test_near_confirmation__cleared_on_selection_change() -> None:
    a = ("c1", "openalex/r/response-0000.jsonl", "near_confirmed")
    b = ("c1", "openalex/r/response-0001.jsonl", "near_confirmed")
    state = ui.NearConfirmation().select(a).tick(True)
    assert state.confirmed_near
    assert state.select(a).confirmed_near  # same selection: kept
    changed = state.select(b)
    assert not changed.checked and not changed.confirmed_near


@pytest.mark.unit
@pytest.mark.parametrize("rule", ["doi", "title_year"])
def test_near_confirmation__not_offered_for_doi(rule: str) -> None:
    state = ui.NearConfirmation().select(("c1", "p", rule)).tick(True)

    assert not state.offered and not state.checked and not state.confirmed_near
    assert not ui.NearConfirmation().tick(True).confirmed_near  # nothing selected


@pytest.mark.unit
def test_match_table__near_rule_named() -> None:
    table = ui.match_table(_view(year=2020), WORK, "near_confirmed")

    assert table.near_rule == "same_title_other_year"
    assert ("year", "2020", "2021") in table.rows
    assert table.rule_definition == ui.RULE_DEFINITIONS["near_confirmed"]
    assert ui.match_table(_view(doi="10.9999/x"), WORK, "doi").near_rule is None
    assert "Near match" in ui.register_confirmation_text(table)


@pytest.mark.unit
def test_preselected_rule__only_when_one_rule_applies() -> None:
    assert ui.preselected_rule(_view(doi="10.9999/x"), [WORK]) == "doi"
    assert ui.preselected_rule(_view(), [WORK]) == "title_year"
    assert ui.preselected_rule(_view(year=2020), [WORK]) == "near_confirmed"
    assert ui.preselected_rule(_view("Nothing", 1999), [WORK]) is None


@pytest.mark.unit
def test_reversal_confirmation_text__names_count_and_inclusion_or_unknown() -> None:
    text = ui.reversal_confirmation_text(ReversalImpact("registered:E1", True, 3))
    assert "3 current" in text and "is currently included" in text and "registered:E1" in text
    assert "is not currently included" in ui.reversal_confirmation_text(
        ReversalImpact("registered:E1", False, 0)
    )
    assert "unknown" in ui.reversal_confirmation_text(None)


@pytest.mark.unit
def test_view__imports_only_the_two_writers_and_reversal_impact() -> None:
    tree = ast.parse(Path(ui.__file__).read_text(encoding="utf-8"))
    imported: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.setdefault(node.module or "", set()).update(a.name for a in node.names)
        elif isinstance(node, ast.Import):
            imported.update({a.name: set() for a in node.names})
    names = set().union(*imported.values())

    # Panel is allowed solely for the widget layer (ADR 0035 PR 3); the log ban below stands.
    assert not names & {
        "DecisionLog",
        "AppendOnlyLog",
        "registration_log",
        "write_event",
        "append_if",
        "RegisterEvent",
        "ReverseEvent",
        "RegistrationRecord",
        "reverse_registration",  # PR 4 adds it with reversal_impact
    }
    assert not [m for m in imported if m in ("prismabib.log", "prismabib.prisma.log")]
    # Of the registration module: the readers, the closed set, and the one writer the view calls.
    assert imported["prismabib.capture.registrations"] <= {
        "REVERSAL_REASONS",
        "RegistrationRefusal",
        "matching_works",
        "read_response",
        "MatchRule",
        "active_registrations",
        "load_registration_events",
        "register_candidate",
    }
    assert imported["prismabib.prisma.impact"] == {"ReversalImpact"}  # PR 4: + reversal_impact


@pytest.mark.unit
def test_preselected_rule__two_rules_or_two_works_preselect_nothing() -> None:
    # Same DOI, different year: `doi` and `near_confirmed` both match exactly one work.
    assert ui.preselected_rule(_view(doi="10.9999/x", year=2020), [WORK]) is None
    # Two works satisfy `doi`: not exactly one, so nothing is preselected.
    assert ui.preselected_rule(_view(doi="10.9999/x"), [WORK, dict(WORK)]) is None


@pytest.mark.unit
def test_keymap__registration_actions__not_bound() -> None:
    from prismabib.screening import ui as screening_ui

    assert not [
        b.action
        for b in screening_ui.KEY_MAP
        if b.action.startswith(("register", "reverse", "registration"))
    ]
    assert {b.key for b in screening_ui.KEY_MAP} >= set("ienpz?")
