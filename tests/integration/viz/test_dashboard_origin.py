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


@pytest.mark.integration
def test_corpus_browser__registered_record__origin_column_distinguishes_it(tmp_path: Path) -> None:
    project = build_project(
        tmp_path, CorpusSpec(records=[RecordSpec(number=1)], criteria=CriteriaSpec())
    )
    connection = connect(project, read_only=False)
    try:
        insert_registered_record(connection, "registered:ev-1")
    finally:
        connection.close()
    dashboard = Dashboard(project)
    dashboard.apply_filters(stage=PrismaStage.RAW)

    table = dashboard._corpus_browser_tab()[1]
    origin_by_id = dict(zip(table.value["record_id"], table.value["origin"], strict=True))

    assert origin_by_id.pop("registered:ev-1") == "registered"
    assert set(origin_by_id.values()) == {"scopus"}
