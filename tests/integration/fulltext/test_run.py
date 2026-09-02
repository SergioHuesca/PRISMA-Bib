"""Integration tests for ``prismabib.fulltext.run`` -- what ``prismabib fulltext`` calls.

Real DuckDB (a store built the normal way, via ``build_store``), real
filesystem, no network: ``Settings`` here carries no
``ELSEVIER_SD_API_KEY``/``UNPAYWALL_EMAIL``, so
:func:`~prismabib.fulltext.resolve.default_chain` degrades to
:class:`~prismabib.fulltext.resolve.ManualDropResolver` alone (see that
function's own docstring) -- exactly the scenario a researcher with no
Elsevier entitlement and no wish to fetch open-access copies actually has,
and it lets this module's *orchestration* (targeting, resumability,
budget, persistence) be tested without mocking any HTTP boundary at all.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from prismabib.config import Settings
from prismabib.errors import ValidationError
from prismabib.fulltext.run import already_resolved_record_ids, run_fulltext_resolution
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.db import connect
from prismabib.store.load import build_store
from tests.conftest import SeededIdFactory
from tests.fixtures.pdf_builder import make_minimal_pdf
from tests.store_helpers import make_entry, write_sealed_run

_STARTED_AT = datetime(2025, 1, 1, tzinfo=UTC)


def _settings() -> Settings:
    # No ELSEVIER_SD_API_KEY, no UNPAYWALL_EMAIL: only ManualDropResolver runs.
    return Settings(_env_file=None, scopus_api_key="test-scopus-key")  # pragma: allowlist secret


def _build_project_with_two_included_records(tmp_path: Path) -> tuple[Project, str, str]:
    project = Project.init("fulltext-run-demo", title="Fulltext Run Demo", root=tmp_path)
    entries = [
        make_entry(eid="2-s2.0-85100000201", doi="10.1016/j.example.2026.100201"),
        make_entry(eid="2-s2.0-85100000202", doi="10.1109/tpami.2026.100202"),
    ]
    write_sealed_run(project.raw_dir, "20250101T000000Z-demorun01", entries, started_at=_STARTED_AT)
    build_store(project, rebuild=True)

    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="id"))
    record_a = "scopus:2-s2.0-85100000201"
    record_b = "scopus:2-s2.0-85100000202"
    for record_id in (record_a, record_b):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record_id,
            reviewer="kp",
            decision="include",
        )
    return project, record_a, record_b


def _drop_manual_pdf(project: Project, record_id: str) -> None:
    manual_dir = project.fulltext_dir / "manual"
    manual_dir.mkdir(parents=True, exist_ok=True)
    (manual_dir / f"{record_id}.pdf").write_bytes(
        make_minimal_pdf(b"BT /F1 24 Tf 10 100 Td (Synthetic Manual Drop) Tj ET")
    )


@pytest.mark.integration
def test_run__manual_drop_for_both_records__resolves_both_and_persists_sections(
    tmp_path: Path,
) -> None:
    project, record_a, record_b = _build_project_with_two_included_records(tmp_path)
    _drop_manual_pdf(project, record_a)
    _drop_manual_pdf(project, record_b)

    summary = run_fulltext_resolution(project, settings=_settings())

    assert summary.records_considered == 2
    assert summary.records_attempted == 2
    assert summary.records_resolved == 2
    assert summary.resolved_by_resolver == {"manual": 2}
    assert summary.refused_by_resolver == {}
    assert summary.unresolved_record_ids == ()

    connection = connect(project, read_only=True)
    try:
        resolved = already_resolved_record_ids(connection)
        section_count = connection.execute("SELECT count(*) FROM fulltext_sections").fetchone()
    finally:
        connection.close()

    assert resolved == {record_a, record_b}
    # A page with no text layer still produces one (low-confidence) section row.
    assert section_count is not None
    assert section_count[0] == 2


@pytest.mark.integration
def test_run__second_invocation__does_not_re_attempt_already_resolved_records(
    tmp_path: Path,
) -> None:
    project, record_a, record_b = _build_project_with_two_included_records(tmp_path)
    _drop_manual_pdf(project, record_a)
    _drop_manual_pdf(project, record_b)

    run_fulltext_resolution(project, settings=_settings())
    second_summary = run_fulltext_resolution(project, settings=_settings())

    assert second_summary.records_considered == 2
    assert second_summary.records_attempted == 0
    assert second_summary.records_resolved == 0


@pytest.mark.integration
def test_run__budget_of_one__attempts_only_one_record_this_call(tmp_path: Path) -> None:
    project, record_a, record_b = _build_project_with_two_included_records(tmp_path)
    _drop_manual_pdf(project, record_a)
    _drop_manual_pdf(project, record_b)

    first = run_fulltext_resolution(project, settings=_settings(), budget=1)

    assert first.records_considered == 2
    assert first.records_attempted == 1
    assert first.records_resolved == 1

    second = run_fulltext_resolution(project, settings=_settings(), budget=1)

    assert second.records_attempted == 1
    assert second.records_resolved == 1

    connection = connect(project, read_only=True)
    try:
        resolved = already_resolved_record_ids(connection)
    finally:
        connection.close()
    assert resolved == {record_a, record_b}


@pytest.mark.integration
def test_run__no_manual_drop__record_stays_unresolved_and_is_reported(tmp_path: Path) -> None:
    project, record_a, record_b = _build_project_with_two_included_records(tmp_path)
    _drop_manual_pdf(project, record_a)
    # record_b gets no manual drop: the chain is exhausted for it.

    summary = run_fulltext_resolution(project, settings=_settings())

    assert summary.records_resolved == 1
    assert summary.unresolved_record_ids == (record_b,)


@pytest.mark.integration
def test_run__no_target_records__raises_validation_error(tmp_path: Path) -> None:
    project = Project.init("empty-fulltext-demo", title="Empty", root=tmp_path)
    entries = [make_entry(eid="2-s2.0-85100000301", doi="10.1016/j.example.2026.100301")]
    write_sealed_run(project.raw_dir, "20250101T000000Z-demorun02", entries, started_at=_STARTED_AT)
    build_store(project, rebuild=True)
    # No decision logged: manual_abstract_set is empty.

    with pytest.raises(ValidationError, match="No records to resolve"):
        run_fulltext_resolution(project, settings=_settings())
