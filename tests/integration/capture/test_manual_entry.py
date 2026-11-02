"""ADR 0041 PR 6: manual-entry and conflict-resolution writers and CLI, through the real loader.

Each test writes events with the writer (or the CLI), rebuilds the store and reads the result,
so the writer and the PR 3 folds cannot drift apart. Refusals must write nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from prismabib.capture.conflicts import resolve_conflict, reverse_conflict
from prismabib.capture.manual import (
    ManualEntryRefusal,
    add_manual_entry,
    reverse_manual_entry,
)
from prismabib.cli import app
from prismabib.errors import ValidationError
from prismabib.models import load_conflict_events, load_manual_events
from prismabib.project import Project
from tests.integration.store.test_provenance_folds import (
    R1,
    R2,
    _build,
    _conflict,
    _project,
    _prov,
    _sql,
)

runner = CliRunner()


def _cli(project: Project, *args: str) -> tuple[int, str]:
    result = runner.invoke(app, [*args, "--root", str(project.root.parent)])
    return result.exit_code, result.output


def _built(tmp_path: Path) -> Project:
    project = _project(tmp_path)
    _build(project)
    return project


# -- manual entry ------------------------------------------------------------------------------


@pytest.mark.integration
def test_manual_entry__writer_event_is_folded_to_level_5(tmp_path: Path) -> None:
    project = _built(tmp_path)

    event = add_manual_entry(
        project, record_id=R2, field="abstract", value="Typed.", reviewer="kp", note="from page"
    )

    assert [e.event_id for e in load_manual_events(project.manual_entries_path)] == [event.event_id]
    assert event.doi == "10.1000/two"  # the page the reviewer opened, from the store
    _build(project)
    assert _prov(project, R2, "abstract")[:3] == ("manual", 5, "value")


@pytest.mark.integration
def test_manual_entry__cli_round_trip_with_reversal(tmp_path: Path) -> None:
    project = _built(tmp_path)

    code, out = _cli(
        project, "manual-entry", "fold", R2, "authors", "--value", "Park, Min", "--reviewer", "kp"
    )
    assert code == 0, out
    (entry,) = load_manual_events(project.manual_entries_path)
    _build(project)
    assert _prov(project, R2, "authors")[:2] == ("manual", 5)

    code, out = _cli(
        project,
        "manual-reverse",
        "fold",
        entry.event_id,
        "--reviewer",
        "kp",
        "--reason",
        "wrong_value",
    )
    assert code == 0, out
    _build(project)
    assert _prov(project, R2, "authors")[:3] == ("unavailable", 0, "unavailable")
    # a reversed entry no longer blocks a new one for the same key
    add_manual_entry(project, record_id=R2, field="authors", value="Lee, Bo", reviewer="kp")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("record_id", "field", "value", "reviewer", "message"),
    [
        ("scopus:nope", "abstract", "x", "kp", "no scopus record"),
        ("registered:E1", "abstract", "x", "kp", "no scopus record"),
        (R2, "keywords", "x", "kp", "field"),
        (R2, "abstract", "  ", "kp", "blank"),
        (R1, "abstract", "x", "kp", "not unavailable"),
        (R2, "affiliations", "A; B; C; D", "kp", "more than three"),
        (R2, "abstract", "x", "  ", "reviewer"),
    ],
)
def test_manual_entry__refusals_raise_cleanly_and_write_nothing(
    tmp_path: Path, record_id: str, field: str, value: str, reviewer: str, message: str
) -> None:
    project = _built(tmp_path)

    with pytest.raises(ValidationError, match=message):
        add_manual_entry(project, record_id=record_id, field=field, value=value, reviewer=reviewer)

    assert not project.manual_entries_path.exists()


@pytest.mark.integration
def test_manual_entry__second_active_entry_for_the_key_is_refused(tmp_path: Path) -> None:
    project = _built(tmp_path)
    add_manual_entry(project, record_id=R2, field="abstract", value="One", reviewer="kp")

    with pytest.raises(ManualEntryRefusal, match="active"):
        add_manual_entry(project, record_id=R2, field="abstract", value="Two", reviewer="kp")

    assert len(load_manual_events(project.manual_entries_path)) == 1


@pytest.mark.integration
def test_manual_entry__cli_refusal_exits_1_without_traceback(tmp_path: Path) -> None:
    project = _built(tmp_path)

    code, out = _cli(
        project,
        "manual-entry",
        "fold",
        R2,
        "affiliations",
        "--value",
        "a;b;c;d",
        "--reviewer",
        "kp",
    )

    assert code == 1
    assert "more than three" in out
    assert "Traceback" not in out
    assert not project.manual_entries_path.exists()


@pytest.mark.integration
def test_manual_reverse__unknown_target_and_bad_arguments_are_refused(tmp_path: Path) -> None:
    project = _built(tmp_path)
    entry = add_manual_entry(project, record_id=R2, field="abstract", value="x", reviewer="kp")

    with pytest.raises(ManualEntryRefusal, match="not an active"):
        reverse_manual_entry(project, reverses="01NOPE", reviewer="kp", reason="other", note="n")
    with pytest.raises(ValidationError, match="note"):
        reverse_manual_entry(project, reverses=entry.event_id, reviewer="kp", reason="other")
    with pytest.raises(ValidationError, match="reason"):
        reverse_manual_entry(project, reverses=entry.event_id, reviewer="kp", reason="bad")
    reverse_manual_entry(project, reverses=entry.event_id, reviewer="kp", reason="wrong_value")
    with pytest.raises(ManualEntryRefusal, match="not an active"):  # already reversed
        reverse_manual_entry(project, reverses=entry.event_id, reviewer="kp", reason="wrong_value")


# -- conflict resolution -----------------------------------------------------------------------


def _row(project: Project, field: str) -> tuple[Any, ...]:
    return _conflict(project, field)


@pytest.mark.integration
def test_conflict__openalex_resolution_resolves_the_row_and_reversal_reopens_it(
    tmp_path: Path,
) -> None:
    project = _built(tmp_path)
    assert _row(project, "abstract")[0] == "open"

    event = resolve_conflict(
        project, record_id=R1, field="abstract", chosen_side="openalex", reviewer="kp"
    )
    _build(project)  # the digests the writer computed match, so the fold applies it (not stale)
    assert _row(project, "abstract") == ("resolved", "openalex", event.event_id)
    assert _sql(project, "SELECT abstract FROM records WHERE record_id = ?", R1) == [
        ("An openalex abstract",)
    ]

    reverse_conflict(project, reverses=event.event_id, reviewer="kp", reason="wrong_side")
    _build(project)
    assert _row(project, "abstract")[0] == "open"


@pytest.mark.integration
def test_conflict__cli_manual_side_takes_the_typed_value(tmp_path: Path) -> None:
    project = _built(tmp_path)

    code, out = _cli(
        project,
        "conflict-resolve",
        "fold",
        R1,
        "abstract",
        "manual",
        "--value",
        "Reviewer text",
        "--reviewer",
        "kp",
    )
    assert code == 0, out
    _build(project)
    assert _prov(project, R1, "abstract")[:2] == ("manual", 5)
    (event,) = load_conflict_events(project.conflict_resolutions_path)

    code, out = _cli(
        project,
        "conflict-reverse",
        "fold",
        event.event_id,
        "--reviewer",
        "kp",
        "--reason",
        "other",
        "--note",
        "n",
    )
    assert code == 0, out
    _build(project)
    assert _row(project, "abstract")[0] == "open"


@pytest.mark.integration
@pytest.mark.parametrize(
    ("record_id", "field", "side", "value", "message"),
    [
        (R2, "abstract", "openalex", None, "no open conflict"),
        (R1, "abstract", "manual", "  ", "value"),
        (R1, "abstract", "both", None, "chosen_side"),
        (R1, "keywords", "openalex", None, "field"),
        (R1, "affiliations", "manual", "A; B; C; D", "more than three"),
    ],
)
def test_conflict__refusals_raise_cleanly_and_write_nothing(
    tmp_path: Path, record_id: str, field: str, side: str, value: str | None, message: str
) -> None:
    project = _built(tmp_path)

    with pytest.raises(ValidationError, match=message):
        resolve_conflict(
            project, record_id=record_id, field=field, chosen_side=side, value=value, reviewer="kp"
        )

    assert not project.conflict_resolutions_path.exists()


@pytest.mark.integration
def test_conflict__second_active_resolution_is_refused_even_on_a_stale_store(
    tmp_path: Path,
) -> None:
    project = _built(tmp_path)
    resolve_conflict(project, record_id=R1, field="authors", chosen_side="scopus", reviewer="kp")

    # no rebuild: the store still says "open", so only the log check can refuse
    with pytest.raises(ValidationError, match="active"):
        resolve_conflict(
            project, record_id=R1, field="authors", chosen_side="openalex", reviewer="kp"
        )

    assert len(load_conflict_events(project.conflict_resolutions_path)) == 1


@pytest.mark.integration
def test_conflict__blank_reviewer_is_refused(tmp_path: Path) -> None:
    project = _built(tmp_path)
    with pytest.raises(ValidationError, match="reviewer"):
        resolve_conflict(
            project, record_id=R1, field="abstract", chosen_side="scopus", reviewer=" "
        )
    assert not project.conflict_resolutions_path.exists()
