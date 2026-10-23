"""ADR 0033: the registered term, the orphaned-decision listing, and the outputs that quote them.

The registration writer and loader fold are another batch, so every test gives the store a
registration by inserting a ``registered_records`` row (``insert_registered_record``) and
"reverses" or "supersedes" it by deleting that row, which is exactly what the fold will do.
Reversal and supersession reasons come from the registration log, which does not exist yet:
the tests pass them as ``outcomes``, the seam ``orphaned_decisions`` exposes for it.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import pytest
import time_machine

from prismabib.prisma import engine
from prismabib.prisma.flow import FlowCounts, compute_flow_counts
from prismabib.prisma.log import DecisionLog
from prismabib.prisma.orphans import project_orphaned_decisions
from prismabib.project import Project
from prismabib.report.flow_diagram import flow_diagram_svg
from prismabib.report.methods import orphaned_decisions_text, registered_sentence
from prismabib.report.numbers import numbers_map
from prismabib.stage import PrismaStage
from prismabib.store.db import connect
from tests.conftest import SeededIdFactory
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project
from tests.store_helpers import insert_registered_record

REGISTERED = "registered:ev-1"


def _project(tmp_path: Path, *, scopus: int, slug: str) -> Project:
    records = [RecordSpec(number=n) for n in range(1, scopus + 1)]
    spec = CorpusSpec(records=records, criteria=CriteriaSpec(), total_results=scopus)
    return build_project(tmp_path, spec, slug=slug)


def _register(project: Project) -> None:
    connection = connect(project, read_only=False)
    try:
        insert_registered_record(connection, REGISTERED)
    finally:
        connection.close()


def _unregister(project: Project) -> None:
    connection = connect(project, read_only=False)
    try:
        connection.execute("DELETE FROM registered_records")
    finally:
        connection.close()


def _include_through_fulltext(project: Project, record_id: str) -> None:
    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="ev"))
    for stage in (PrismaStage.TITLE_ABSTRACT, PrismaStage.FULLTEXT):
        log.append(stage=stage, record_id=record_id, reviewer="kp", decision="include")


# ---------------------------------------------------------------------------
# registered in FlowCounts
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_flow_counts__registrations_absent__identity_unchanged(tmp_path: Path) -> None:
    project = _project(tmp_path, scopus=4, slug="none")

    counts = compute_flow_counts(project)

    assert counts.registered == 0
    assert (counts.identified, counts.after_automated, counts.unsure_title_abstract) == (4, 4, 4)
    assert counts.assert_consistent() is None


@pytest.mark.integration
def test_flow_counts__one_registration__identity_closes_with_registered_term(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, scopus=4, slug="one")
    _register(project)
    _include_through_fulltext(project, REGISTERED)

    counts = compute_flow_counts(project)

    assert counts.identified == 4  # the Scopus total alone (ADR 0013): never 5
    assert counts.registered == 1
    assert counts.after_automated == 5  # 4 + 1 - 0 - 0 - 0
    assert (counts.included, counts.unsure_title_abstract) == (1, 4)
    assert counts.assert_consistent() is None


@pytest.mark.integration
def test_raw_set__registered_id__is_in_S_raw(tmp_path: Path) -> None:
    project = _project(tmp_path, scopus=2, slug="raw")
    _register(project)

    raw = engine.raw_set(project)
    counts = compute_flow_counts(project)

    assert REGISTERED in raw
    assert len([r for r in raw if r.startswith("registered:")]) == counts.registered == 1


# ---------------------------------------------------------------------------
# reversal / supersession / orphans
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_orphaned_decisions__reversed_registration__reported_not_folded(tmp_path: Path) -> None:
    project = _project(tmp_path, scopus=4, slug="reversed")
    _register(project)
    _include_through_fulltext(project, REGISTERED)
    before = compute_flow_counts(project)
    assert (before.registered, before.included) == (1, 1)
    assert project_orphaned_decisions(project) == []

    _unregister(project)  # the fold drops a reversed registration
    after = compute_flow_counts(project)
    orphans = project_orphaned_decisions(project, outcomes={REGISTERED: "reversal"})

    assert (after.registered, after.included, after.after_automated) == (0, 0, 4)
    assert after.assert_consistent() is None
    assert REGISTERED not in engine.raw_set(project)
    assert [(o.record_id, o.stage, o.reason) for o in orphans] == [
        (REGISTERED, "fulltext", "reversal"),
        (REGISTERED, "title_abstract", "reversal"),
    ]
    assert {o.event_id for o in orphans} == {"ev-000000", "ev-000001"}
    assert "orphan" not in {f.name for f in dataclasses.fields(FlowCounts)}


@pytest.mark.integration
def test_orphaned_decisions__superseded_registration__scopus_record_is_unsure(
    tmp_path: Path,
) -> None:
    registered = _project(tmp_path, scopus=4, slug="before")
    _register(registered)
    _include_through_fulltext(registered, REGISTERED)
    before = compute_flow_counts(registered)

    # A second search brings the same paper in as a Scopus record: identified rises, the
    # registration is gone, and the Scopus record has no decision.
    superseded = _project(tmp_path, scopus=5, slug="after")
    _include_through_fulltext(superseded, REGISTERED)
    after = compute_flow_counts(superseded)
    orphans = project_orphaned_decisions(superseded, outcomes={REGISTERED: "supersession"})

    assert (before.included, before.unsure_title_abstract) == (1, 4)
    assert (after.identified, after.registered, after.included) == (5, 0, 0)
    assert after.unsure_title_abstract == 5
    assert after.assert_consistent() is None
    assert {o.reason for o in orphans} == {"supersession"}
    assert len(orphans) == 2


@pytest.mark.integration
def test_orphaned_decisions__current_is_latest_by_ts_not_file_order(tmp_path: Path) -> None:
    project = _project(tmp_path, scopus=2, slug="latest")
    gone = "scopus:2-s2.0-90000000999"  # a Scopus id a rebuild no longer holds
    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="ev"))
    stage = PrismaStage.TITLE_ABSTRACT
    with time_machine.travel(datetime(2025, 3, 2, tzinfo=UTC), tick=False):
        log.append(stage=stage, record_id=gone, reviewer="kp", decision="unsure")
    with time_machine.travel(datetime(2025, 3, 1, tzinfo=UTC), tick=False):  # older, written later
        log.append(stage=stage, record_id=gone, reviewer="kp", decision="include")

    orphans = project_orphaned_decisions(project)

    assert [(o.event_id, o.decision, o.reason) for o in orphans] == [
        ("ev-000000", "unsure", "record not in S_raw")
    ]
    assert compute_flow_counts(project).assert_consistent() is None


# ---------------------------------------------------------------------------
# numbers.json, methods text, diagram
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_numbers_json__registered_key__always_present(tmp_path: Path) -> None:
    project = _project(tmp_path, scopus=3, slug="key")

    numbers = numbers_map(project)

    assert numbers["flow.registered"] == 0
    assert registered_sentence(numbers) == ""


@pytest.mark.integration
def test_numbers_and_methods__registered__agree(tmp_path: Path) -> None:
    project = _project(tmp_path, scopus=3, slug="agree")
    _register(project)

    numbers = numbers_map(project)

    assert numbers["flow.registered"] == 1
    assert numbers["corpus.size"] == 4
    assert registered_sentence(numbers) == (
        "4 records, of which 1 are registrations from a manual Google Scholar search (ADR 0029)."
    )


@pytest.mark.integration
def test_orphaned_decisions_text__lists_record_stage_and_reason(tmp_path: Path) -> None:
    project = _project(tmp_path, scopus=2, slug="text")
    _register(project)
    _include_through_fulltext(project, REGISTERED)
    _unregister(project)

    orphans = project_orphaned_decisions(project, outcomes={REGISTERED: "reversal"})
    text = orphaned_decisions_text(orphans)

    assert text.splitlines()[0].startswith("2 screening decision(s)")
    assert f"- {REGISTERED} (fulltext, include, event ev-000001): reversal" in text
    assert orphaned_decisions_text([]) == ""


@pytest.mark.integration
def test_flow_diagram__registered__other_methods_box_only_when_nonzero(tmp_path: Path) -> None:
    project = _project(tmp_path, scopus=3, slug="svg")
    plain = flow_diagram_svg(compute_flow_counts(project), title="T")
    _register(project)
    with_reg = flow_diagram_svg(compute_flow_counts(project), title="T", orphaned_decisions=2)

    assert "other methods" not in plain
    assert "orphaned-decisions" not in plain
    assert "Records identified from other methods (n = 1)" in with_reg
    assert "Orphaned decisions (n = 2)" in with_reg
    assert "Records identified from Scopus (n = 3)" in with_reg  # identified is not summed
