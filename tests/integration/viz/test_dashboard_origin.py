"""The Corpus browser shows ``origin`` (ADR 0032): Scopus and registered rows differ."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from prismabib.stage import PrismaStage
from prismabib.store.db import connect
from prismabib.viz.dashboard import Dashboard
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project
from tests.store_helpers import insert_registered_record

if TYPE_CHECKING:
    from pathlib import Path

    from prismabib.project import Project


def _origin_by_id(project: Project, stage: PrismaStage) -> tuple[list[str], dict[str, str]]:
    dashboard = Dashboard(project)
    dashboard.apply_filters(stage=stage)
    table = dashboard._corpus_browser_tab()[1]
    origins = dict(zip(table.value["record_id"], table.value["origin"], strict=True))
    return list(table.value.columns), origins


@pytest.fixture
def project_with_registered(tmp_path: Path) -> Project:
    project = build_project(
        tmp_path, CorpusSpec(records=[RecordSpec(number=1)], criteria=CriteriaSpec())
    )
    connection = connect(project, read_only=False)
    try:
        insert_registered_record(connection, "registered:ev-1")
    finally:
        connection.close()
    return project


@pytest.mark.integration
def test_corpus_browser__registered_record__origin_column_distinguishes_it(
    project_with_registered: Project,
) -> None:
    _columns, origins = _origin_by_id(project_with_registered, PrismaStage.RAW)

    assert origins.pop("registered:ev-1") == "registered"
    assert set(origins.values()) == {"scopus"}


@pytest.mark.integration
@pytest.mark.parametrize("stage", list(PrismaStage))
def test_corpus_browser__every_stage__origin_is_the_second_column_and_matches_raw(
    project_with_registered: Project, stage: PrismaStage
) -> None:
    raw_origins = _origin_by_id(project_with_registered, PrismaStage.RAW)[1]

    columns, origins = _origin_by_id(project_with_registered, stage)

    assert columns[:2] == ["record_id", "origin"]
    assert origins.items() <= raw_origins.items()
