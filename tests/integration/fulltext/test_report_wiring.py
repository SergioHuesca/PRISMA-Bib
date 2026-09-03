"""The Stage 6 coverage tables must actually reach ``build_tables``/``prismabib export`` (ADR 0019).

BUILD_PLAN line 1146: the Stage 9/10 report "must include a full-text
coverage table by resolver and by publisher, so the bias is visible in the
output rather than hidden in the method." ``coverage_by_resolver_table``/
``coverage_by_publisher_table`` existing and being individually tested (see
``tests/integration/fulltext/test_coverage.py``) proves nothing about whether
anything actually calls them for a real export -- before this fix, nothing
in ``src/`` did.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.project import Project
from prismabib.report.export import export_project
from prismabib.report.numbers import numbers_map
from prismabib.report.tables import build_tables
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project

CORPUS = CorpusSpec(
    records=[RecordSpec(number=n, cited_by_count=n) for n in range(1, 6)],
    criteria=CriteriaSpec(abstract_reason_codes=("OFF_TOPIC",)),
)


@pytest.fixture
def project(tmp_path: Path) -> Project:
    return build_project(tmp_path, CORPUS, slug="fulltext-report-wiring")


@pytest.mark.integration
@pytest.mark.acceptance("S06-AC3")
def test_build_tables__includes_both_fulltext_coverage_tables(project: Project) -> None:
    numbers = numbers_map(project)

    tables = build_tables(project, numbers)

    slugs = {table.slug for table in tables}
    assert "fulltext_coverage_by_resolver" in slugs
    assert "fulltext_coverage_by_publisher" in slugs

    # No fulltext run has ever been made against this project: both tables
    # render with zero rows rather than being omitted -- an absent table
    # would read as "no bias to report" instead of "not run yet".
    by_resolver = next(t for t in tables if t.slug == "fulltext_coverage_by_resolver")
    by_publisher = next(t for t in tables if t.slug == "fulltext_coverage_by_publisher")
    assert by_resolver.rows == ()
    assert by_publisher.rows == ()


@pytest.mark.integration
def test_export_project__writes_both_fulltext_coverage_table_files(project: Project) -> None:
    result = export_project(project)

    for slug in ("fulltext_coverage_by_resolver", "fulltext_coverage_by_publisher"):
        for suffix in ("csv", "md", "tex"):
            path = result.root / "tables" / f"{slug}.{suffix}"
            assert path.is_file(), f"{slug}.{suffix} was not written by prismabib export"
