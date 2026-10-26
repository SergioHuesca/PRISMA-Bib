"""ADR 0035 Decision 4: ``reversal_impact`` is the prospective, read-only warning figure."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.prisma.flow import compute_flow_counts
from prismabib.prisma.impact import ReversalImpact, reversal_impact
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.db import connect
from tests.conftest import SeededIdFactory
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project
from tests.store_helpers import insert_registered_record

REG = "registered:ev-1"
OTHER = "registered:ev-2"
TA, FT = PrismaStage.TITLE_ABSTRACT, PrismaStage.FULLTEXT


def _project(tmp_path: Path) -> Project:
    spec = CorpusSpec(records=[RecordSpec(number=1)], criteria=CriteriaSpec(), total_results=1)
    project = build_project(tmp_path, spec, slug="impact")
    connection = connect(project, read_only=False)
    try:
        insert_registered_record(connection, REG)
        insert_registered_record(connection, OTHER)
    finally:
        connection.close()
    return project


@pytest.mark.integration
def test_reversal_impact__counts_current_decisions_and_included(tmp_path: Path) -> None:
    project = _project(tmp_path)
    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="ev"))
    # REG: kp includes at TA twice (the first is superseded), ab includes at TA, kp at FT -> 3 current.
    for stage, reviewer in ((TA, "kp"), (TA, "kp"), (TA, "ab"), (FT, "kp")):
        log.append(stage=stage, record_id=REG, reviewer=reviewer, decision="include")
    log.append(stage=TA, record_id=OTHER, reviewer="kp", decision="unsure")
    log_file = project.decisions_path
    before, files = log_file.read_bytes(), sorted(p.name for p in project.root.rglob("*"))

    impact = reversal_impact(project, REG)

    assert impact == ReversalImpact(REG, currently_included=True, decisions_orphaned=3)
    assert reversal_impact(project, OTHER) == ReversalImpact(OTHER, False, 1)
    assert reversal_impact(project, "registered:none") == ReversalImpact(
        "registered:none", False, 0
    )
    assert log_file.read_bytes() == before
    assert sorted(p.name for p in project.root.rglob("*")) == files


@pytest.mark.integration
def test_reversal_impact__included_count__equals_flow_counts_included(tmp_path: Path) -> None:
    project = _project(tmp_path)
    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="ev"))
    for stage in (TA, FT):
        log.append(stage=stage, record_id=REG, reviewer="kp", decision="include")
    log.append(stage=TA, record_id=OTHER, reviewer="kp", decision="include")
    log.append(stage=FT, record_id=OTHER, reviewer="kp", decision="unsure")
    ids = [REG, OTHER, "scopus:2-s2.0-90000000001"]

    included = [i for i in ids if reversal_impact(project, i).currently_included]

    assert included == [REG]
    assert len(included) == compute_flow_counts(project).included


@pytest.mark.integration
def test_reversal_impact__project_without_decision_log__creates_nothing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    files = sorted(p.name for p in project.root.rglob("*"))

    assert reversal_impact(project, REG) == ReversalImpact(REG, False, 0)
    assert sorted(p.name for p in project.root.rglob("*")) == files
