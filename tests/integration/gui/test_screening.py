"""The screening step and its layer, over a real store and a real Decision log (ticket #332)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.errors import ProjectLockedError
from prismabib.gui import layer, tasks
from prismabib.gui import screening_layer as sl
from prismabib.gui.new_project import build_stepper
from prismabib.gui.screening import build_screening
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.screening import ui
from prismabib.stage import PrismaStage
from prismabib.store.load import build_store
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.integration.gui.test_home import unlabelled_controls
from tests.prisma_helpers import (
    RUN_ID,
    RUN_STARTED_AT,
    CriteriaSpec,
    RecordSpec,
    write_criteria,
)
from tests.store_helpers import write_sealed_run

pytestmark = pytest.mark.integration

STAGE = PrismaStage.TITLE_ABSTRACT
CODES = ("OFF_TOPIC", "REVIEW_OR_SURVEY")


@pytest.fixture(autouse=True)
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "settings.env"))
    yield


def make_project(root: Path, *, built: bool = True, criteria: bool = True) -> Project:
    """A Project created through the layer (Reviewer Ada), with five authored Records."""
    project = layer.create_project(
        "demo", title="Demo", root=root, source_mode="scopus", reviewer="Ada"
    )
    if criteria:
        write_criteria(project, CriteriaSpec(abstract_reason_codes=CODES))
    if built:
        records = [
            RecordSpec(number=n, authors=(("Alvarez", "Rosa"),), cited_by_count=417)
            for n in range(1, 6)
        ]
        write_sealed_run(
            project.raw_dir,
            RUN_ID,
            [r.to_entry() for r in records],
            started_at=RUN_STARTED_AT,
            criteria_version="1.0.0",
        )
        build_store(project, rebuild=True)
    return project


def _log(project: Project) -> list[dict[str, object]]:
    lines = project.decisions_path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_decide__through_the_hosted_screener__carries_the_creation_reviewer(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    reviewer = sl.creation_reviewer(project)
    screener = sl.open_screening(project, STAGE, reviewer)

    screener.handlers["include"]()
    screener.handlers["unsure"]()

    events = _log(project)
    assert reviewer == "Ada"
    assert [e["reviewer"] for e in events] == ["Ada", "Ada"]
    assert [e["decision"] for e in events] == ["include", "unsure"]
    assert not any("actor" in e for e in events)
    text = sl.screen_markdown(sl.screen_model(screener.queue, {}, blind=True))
    assert sl.REVIEWER_LABEL in text and sl.ACTOR_LABEL in text
    assert sl.REVIEWER_LABEL != sl.ACTOR_LABEL


def test_screener__runs_with_palette_and_progress_inside_the_view(tmp_path: Path) -> None:
    screener = sl.open_screening(make_project(tmp_path), STAGE, "Ada")

    assert screener.palette == {"1": "OFF_TOPIC", "2": "REVIEW_OR_SURVEY"}
    screener.handlers["begin_exclude"]()
    screener.handlers["reason_2"]()
    assert "1 / 5" in str(screener._progress_pane.object)
    assert _log(screener.queue.project)[0]["reason_code"] == "REVIEW_OR_SURVEY"


def test_every_keyboard_action__has_a_mouse_button(tmp_path: Path) -> None:
    screener = sl.open_screening(make_project(tmp_path), STAGE, "Ada")
    labels = [b.label for b in screener.view().select(pn.widgets.Button)]

    for binding in ui.KEY_MAP:
        if binding.action.startswith("reason_"):
            if binding.key in screener.palette:  # a digit with no declared code has no action
                assert any(label.startswith(f"{binding.key}:") for label in labels), binding
        elif binding.action == "begin_exclude":  # the reason buttons arm the exclusion
            assert any(label.startswith("1:") for label in labels)
        else:
            assert any(label.endswith(f"({binding.key})") for label in labels), binding


def test_progress__counts_unscreened_and_unsure(tmp_path: Path) -> None:
    screener = sl.open_screening(make_project(tmp_path), STAGE, "Ada")
    assert sl.counts(screener.queue) == {"unscreened": 5, "unsure": 0, "total": 5}

    screener.handlers["unsure"]()
    screener.handlers["include"]()

    assert sl.counts(screener.queue) == {"unscreened": 3, "unsure": 1, "total": 5}
    assert "**Unscreened:** 3 · **Unsure:** 1" in str(screener._header.object)


def test_blind_mode__second_reviewer__nothing_leaks_into_the_view_model(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    queue = sl.LockedQueue(project, STAGE, "Ada")
    records = ui.load_records(project, queue.pending)
    DecisionLog(project).append(
        stage=STAGE, record_id=queue.current or "", reviewer="Bea", decision="include"
    )

    blind = sl.screen_model(queue, records, blind=True)
    seen = sl.screen_model(queue, records, blind=False)

    flat = json.dumps(blind, default=str)
    assert "Alvarez" not in flat and "417" not in flat and "Bea" not in flat
    assert "other_decisions" not in blind
    assert set(blind["record"]) == set(ui.VISIBLE_FIELDS)
    assert seen["other_decisions"] == [{"reviewer": "Bea", "decision": "include"}]
    assert "Alvarez" in json.dumps(seen["record"])


def test_read_only_window__decision__is_refused_and_logged_nothing(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    screener = sl.open_screening(project, STAGE, "Ada")
    holder = layer.Session(project)  # another window holds the write lock

    with pytest.raises(ProjectLockedError):
        screener.queue.decide("include")
    screener.dispatch("include")  # the screen shows the refusal instead of raising
    assert "Another window" in screener.status

    assert not project.decisions_path.exists() or not _log(project)
    holder.close()


def test_open_early__says_what_is_missing(tmp_path: Path) -> None:
    project = make_project(tmp_path, built=False, criteria=False)

    view = build_screening(project)

    text = " ".join(str(p.object) for p in view.select(pn.pane.Markdown))
    assert "Criteria are not written" in text and "store is not built" in text
    with pytest.raises(layer.ValidationError):
        sl.open_screening(project, STAGE, "Ada")


def test_smoke__stepper_screening_step__builds_with_labelled_controls(tmp_path: Path) -> None:
    project = make_project(tmp_path)
    view = build_screening(project)
    start = next(b for b in view.select(pn.widgets.Button) if b.name == "Start screening")

    start.clicks += 1
    tasks.runner_for(project).join(30)
    view.poll()

    assert not unlabelled_controls(view)
    assert view.select(pn.widgets.TextInput)[0].value == "Ada"
    assert any("Unscreened" in str(p.object) for p in view.select(pn.pane.Markdown))
    assert build_stepper(project) is not None
