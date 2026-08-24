"""Regression tests for machine- and data-dependence in Layer 1.

Both cases here were found by review after the suite was already green, and both
are the same shape: correct on the machine that wrote them, wrong somewhere else.
That shape matters more in this project than in most, because BUILD_PLAN's Stage 11
criterion is not "the tests pass" but "a clean clone **on a different machine**
reproduces ``numbers.json``". A store that is internally consistent and
externally irreproducible satisfies every local test and still fails the thing
the architecture exists to guarantee (§1.4).

The tests are written to fail on the *original* defect rather than to describe the
fix, so they keep their value if the implementation changes again.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import polars as pl
import pytest

from prismabib.stage import PrismaStage
from prismabib.store.db import connect
from prismabib.store.load import Corpus, build_store
from tests.store_helpers import copy_reference_project, make_entry, write_sealed_run

_RUN_STARTED_AT = datetime(2026, 1, 15, 9, 0, 0, tzinfo=UTC)


@pytest.mark.integration
@pytest.mark.parametrize(
    "offset_hours",
    [0, -6, 9, 13],
    ids=["utc", "utc-minus-6", "utc-plus-9", "utc-plus-13"],
)
def test_citations__aware_at_on_the_snapshot_boundary__is_timezone_independent(
    tmp_path: Path, offset_hours: int
) -> None:
    """``citations(at=...)`` must not depend on the caller's timezone.

    ``RunManifest.started_at`` is timezone-aware, so ``citations(at=<run start>)``
    is the most natural call there is -- and it lands exactly on the stored
    ``retrieved_at`` boundary, where an off-by-one-offset comparison flips from
    "every row" to "no rows". Before the fix this returned 120 rows under UTC and
    0 under UTC-6: the same store, the same argument, a different answer per host.

    The parametrisation deliberately includes UTC+13, which is past the point where
    a naive comparison could be rescued by a small margin.
    """
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    connection = connect(project, read_only=True)
    at = _RUN_STARTED_AT.astimezone(timezone(timedelta(hours=offset_hours)))

    try:
        rows = Corpus(connection).citations(at=at)
    finally:
        connection.close()

    assert rows.height == 120


@pytest.mark.integration
def test_citations__naive_and_aware_at__agree(tmp_path: Path) -> None:
    """The same instant expressed two ways must select the same snapshots."""
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    connection = connect(project, read_only=True)

    try:
        aware = Corpus(connection).citations(at=_RUN_STARTED_AT)
        naive = Corpus(connection).citations(at=_RUN_STARTED_AT.replace(tzinfo=None))
    finally:
        connection.close()

    assert aware.height == naive.height


@pytest.mark.integration
def test_build_store__timestamps__do_not_depend_on_the_host_timezone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A timestamp written to Layer 1 must be the same instant on every machine.

    DuckDB's ``TIMESTAMP`` is naive, so an *aware* datetime is converted to the
    host's local time on insert. Two researchers loading the same Layer 0 archive
    in different timezones would otherwise store -- and later publish -- different
    citation snapshot dates, with nothing in the output to indicate why.

    ``monkeypatch.setenv`` here targets the process environment, not any
    ``prismabib`` internal, so §3.7.3 rule 1 is respected.
    """
    project = copy_reference_project(tmp_path)
    monkeypatch.setenv("TZ", "Asia/Tokyo")
    time.tzset()
    build_store(project, rebuild=True)
    connection = connect(project, read_only=True)

    try:
        stored = connection.execute("SELECT started_at FROM runs").fetchone()
    finally:
        connection.close()

    assert stored is not None
    assert stored[0] == _RUN_STARTED_AT.replace(tzinfo=None)


@pytest.mark.integration
def test_corpus_query__more_than_100_leading_nulls__still_materialises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nullable column whose first 100 values are NULL must not break the read.

    polars infers a DataFrame's schema from the first 100 rows by default, so a
    column that is NULL throughout that window is typed ``Null`` and row 101 raises
    ``ComputeError: could not append value``. ``records.doi`` is nullable and a real
    corpus can easily open with 100 DOI-less conference papers, so this is ordinary
    data rather than a pathological case -- and the 120-record reference fixture
    cannot surface it, which is why it needs its own fixture here.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _project_with_late_doi(tmp_path)
    build_store(project, rebuild=True)
    connection = connect(project, read_only=True)

    try:
        records = Corpus(connection).records(stage=PrismaStage.RAW)
    finally:
        connection.close()

    assert records.height == 101
    assert records.filter(pl.col("doi").is_not_null()).height == 1


def _project_with_late_doi(tmp_path: Path):
    """Build a Layer 0 archive whose only DOI-bearing record is row 101."""
    from prismabib.project import Project

    project = Project.init("late-doi", title="Late DOI", root=tmp_path)
    entries = [
        make_entry(eid=f"2-s2.0-9{index:011d}", doi=None, title=f"Untitled {index}")
        for index in range(100)
    ]
    entries.append(make_entry(eid="2-s2.0-9" + "9" * 11, doi="10.1000/late", title="Has a DOI"))
    write_sealed_run(
        project.raw_dir, "20260115T090000Z-latedoi", entries, started_at=_RUN_STARTED_AT
    )
    return project
