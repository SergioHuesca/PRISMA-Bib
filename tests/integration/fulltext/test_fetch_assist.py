"""Integration test for ``scripts/fetch_assist.py`` against a registered record (ADR 0032)."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.db import connect
from prismabib.store.load import build_store
from tests.conftest import SeededIdFactory
from tests.store_helpers import insert_registered_record, make_entry, write_sealed_run

_SCOPUS_ID = "scopus:2-s2.0-85100000201"
_REGISTERED_ID = "registered:ev-1"


@pytest.mark.integration
def test_fetch_assist_script__registered_record__gets_a_doi_tab_like_a_scopus_record(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The script's metadata query reads `records_all`, so a registration is a candidate.

    If the reader goes back to `records`, the registered row is absent from `metadata`, is
    silently dropped from `candidates`, and no `https://doi.org/<its DOI>` tab is opened,
    so the opened-URL assertion below fails (nothing raises, which is why it needs a test).
    """
    project = Project.init("fetch-assist-demo", title="Fetch Assist Demo", root=tmp_path)
    entries = [make_entry(eid="2-s2.0-85100000201", doi="10.1016/j.example.2026.100201")]
    write_sealed_run(
        project.raw_dir,
        "20250101T000000Z-demorun01",
        entries,
        started_at=datetime(2025, 1, 1, tzinfo=UTC),
    )
    build_store(project, rebuild=True)
    connection = connect(project, read_only=False)
    try:
        insert_registered_record(connection, _REGISTERED_ID, doi="10.1016/j.registered.1")
    finally:
        connection.close()
    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="id"))
    for record_id in (_SCOPUS_ID, _REGISTERED_ID):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT, record_id=record_id, reviewer="kp", decision="include"
        )

    script = Path(__file__).resolve().parents[3] / "scripts" / "fetch_assist.py"
    spec = importlib.util.spec_from_file_location("fetch_assist_script", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    opened: list[str] = []

    module.run(
        slug=project.slug,
        root=tmp_path,
        batch_size=5,
        download_dir=tmp_path / "downloads",
        poll_timeout=0,  # no watching: this test is about which tabs get opened
        opener=lambda url: opened.append(url) or True,
    )

    assert sorted(opened) == [
        "https://doi.org/10.1016/j.example.2026.100201",
        "https://doi.org/10.1016/j.registered.1",
    ]
    out = capsys.readouterr().out
    assert "Fetching 2 of 2 still-missing records" in out
    assert f"{_REGISTERED_ID} -- A Registered Work" in out
