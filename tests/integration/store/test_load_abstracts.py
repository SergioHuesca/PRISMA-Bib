"""Integration tests for the Layer 1 abstract-run loader (ADR 0018, issue #31).

Real filesystem, real DuckDB, no network (§3.7.2) -- every test here builds a
sealed Layer 0 abstract run by hand
(:func:`tests.store_helpers.write_sealed_abstract_run`) and folds it in with
:func:`prismabib.store.load.build_store`, exactly the path ``prismabib enrich``
followed by ``prismabib build --rebuild`` takes in practice.

Kept out of ``tests/integration/store/test_load.py`` (already large) for the
same reason ``test_portability.py`` sits beside it: one coherent concern, not
a grab-bag addition to an existing file.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from prismabib.capture.manifest import AbstractUnavailable
from prismabib.errors import ConfigError
from prismabib.prisma import engine
from prismabib.prisma.flow import compute_flow_counts
from prismabib.project import Project
from prismabib.store.checksums import table_checksums
from prismabib.store.db import connect
from prismabib.store.load import build_store
from tests.prisma_helpers import CriteriaSpec, RecordSpec, write_criteria
from tests.store_helpers import (
    make_abstract_entry,
    make_entry,
    write_sealed_abstract_run,
    write_sealed_run,
)

_SEARCH_RUN_ID = "20250101T000000Z-11111111"
_SEARCH_STARTED_AT = datetime(2025, 1, 1, tzinfo=UTC)
_ABSTRACT_RUN_ID = "20250102T000000Z-22222222"
_ABSTRACT_STARTED_AT = datetime(2025, 1, 2, tzinfo=UTC)
_LATER_ABSTRACT_RUN_ID = "20250103T000000Z-33333333"
_LATER_ABSTRACT_STARTED_AT = datetime(2025, 1, 3, tzinfo=UTC)


def _record_id(number: int) -> str:
    """A canonical record id for a small, hand-numbered synthetic corpus."""
    return f"scopus:2-s2.0-8000000009{number:02d}"


def _eid(number: int) -> str:
    return f"2-s2.0-8000000009{number:02d}"


def _write_search_run(
    raw_dir: Path, numbers: list[int], *, total_results: int | None = None
) -> None:
    """Write one sealed search run covering ``numbers``, with no subject-area data.

    Args:
        raw_dir: The project's ``raw/`` directory.
        numbers: Which synthetic records (by :func:`_record_id`'s numbering)
            this run should identify.
        total_results: ``RunManifest.total_results``; defaults to
            ``len(numbers)``.
    """
    entries = [make_entry(eid=_eid(number), title=f"Record {number}") for number in numbers]
    write_sealed_run(
        raw_dir,
        _SEARCH_RUN_ID,
        entries,
        started_at=_SEARCH_STARTED_AT,
        total_results=total_results if total_results is not None else len(numbers),
    )


def _subject_areas(connection: duckdb.DuckDBPyConnection, record_id: str) -> set[str]:
    """The set of ``subject_areas.area_code`` currently stored for ``record_id``."""
    return {
        row[0]
        for row in connection.execute(
            "SELECT area_code FROM subject_areas WHERE record_id = ?", [record_id]
        ).fetchall()
    }


def _coverage_rows(connection: duckdb.DuckDBPyConnection, record_id: str) -> set[tuple[str, str]]:
    """``(run_id, status)`` pairs currently stored for ``record_id``."""
    return {
        (row[0], row[1])
        for row in connection.execute(
            "SELECT run_id, status FROM record_subject_area_coverage WHERE record_id = ?",
            [record_id],
        ).fetchall()
    }


@pytest.mark.integration
def test_build_store__sealed_abstract_run__loads_nonzero_subject_area_rows(tmp_path: Path) -> None:
    """The behaviour issue #31 exists for: a sealed abstract run must actually load.

    Written first and watched fail before the loader existed (per the task
    brief) -- ``store/load.py`` used to skip ``raw/abstracts/`` by name
    entirely, so this asserted 0 == 2 until :func:`_load_abstract_run` was
    added.
    """
    project = Project.init("loads", title="Loads", root=tmp_path)
    _write_search_run(project.raw_dir, [1])
    entry = make_abstract_entry(
        eid=_eid(1),
        subject_areas=[
            {"@code": "2202", "@abbrev": "ENGI", "$": "Aerospace Engineering"},
            {"@code": "1702", "@abbrev": "COMP", "$": "Artificial Intelligence"},
        ],
    )
    write_sealed_abstract_run(
        project.raw_dir, _ABSTRACT_RUN_ID, [entry], started_at=_ABSTRACT_STARTED_AT
    )

    stats = build_store(project, rebuild=True)

    assert stats.subject_area_links_loaded == 2
    connection = connect(project, read_only=True)
    try:
        assert _subject_areas(connection, _record_id(1)) == {"2202", "1702"}
    finally:
        connection.close()


@pytest.mark.integration
def test_build_store__four_coverage_states__are_distinguishable(tmp_path: Path) -> None:
    """``assigned``/``none_assigned``/``not_found``/``not_entitled``, and "never asked".

    Five records exercise all four named states plus the unnamed fifth:
    absence. "Never asked" must not read as ``none_assigned`` -- that
    conflation is exactly what ``subject_areas`` alone could not represent,
    and the entire reason ``record_subject_area_coverage`` exists (ADR 0018).
    """
    project = Project.init("coverage", title="Coverage", root=tmp_path)
    _write_search_run(project.raw_dir, [1, 2, 3, 4, 5])
    assigned = make_abstract_entry(eid=_eid(1), subject_areas=[{"@code": "2202"}])
    none_assigned = make_abstract_entry(eid=_eid(2), subject_areas=None)
    write_sealed_abstract_run(
        project.raw_dir,
        _ABSTRACT_RUN_ID,
        [assigned, none_assigned],
        started_at=_ABSTRACT_STARTED_AT,
        unavailable=[
            AbstractUnavailable(record_id=_record_id(3), http_status=404, reason="not_found"),
            AbstractUnavailable(record_id=_record_id(4), http_status=403, reason="not_entitled"),
        ],
        # record 5 is described nowhere in this run at all: "never asked".
        records_requested=5,
    )

    build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        assert _coverage_rows(connection, _record_id(1)) == {(_ABSTRACT_RUN_ID, "assigned")}
        assert _coverage_rows(connection, _record_id(2)) == {(_ABSTRACT_RUN_ID, "none_assigned")}
        assert _coverage_rows(connection, _record_id(3)) == {(_ABSTRACT_RUN_ID, "not_found")}
        assert _coverage_rows(connection, _record_id(4)) == {(_ABSTRACT_RUN_ID, "not_entitled")}
        # The fourth state -- never asked -- is a row's *absence*, not a value.
        assert _coverage_rows(connection, _record_id(5)) == set()
        assert _subject_areas(connection, _record_id(1)) == {"2202"}
        assert _subject_areas(connection, _record_id(2)) == set()
        assert _subject_areas(connection, _record_id(5)) == set()
    finally:
        connection.close()


@pytest.mark.integration
def test_build_store__abstract_run__adds_no_row_to_runs_and_leaves_identified_unchanged(
    tmp_path: Path,
) -> None:
    """An abstract run identifies no record (ADR 0011 / ADR 0018): ``runs`` must not grow.

    Asserted directly against ``runs``, not inferred from a count that could
    also be explained by something else -- and cross-checked against
    ``compute_flow_counts``'s ``identified``, the one number an extra ``runs``
    row would have been able to move.
    """
    project = Project.init("no-runs-row", title="No runs row", root=tmp_path)
    _write_search_run(project.raw_dir, [1, 2, 3], total_results=3)
    entry = make_abstract_entry(eid=_eid(1), subject_areas=[{"@code": "2202"}])
    write_sealed_abstract_run(
        project.raw_dir, _ABSTRACT_RUN_ID, [entry], started_at=_ABSTRACT_STARTED_AT
    )

    stats = build_store(project, rebuild=True)

    assert stats.runs_loaded == 1
    assert stats.abstract_runs_loaded == 1
    connection = connect(project, read_only=True)
    try:
        (run_count,) = connection.execute("SELECT COUNT(*) FROM runs").fetchone()
        run_ids = {row[0] for row in connection.execute("SELECT run_id FROM runs").fetchall()}
    finally:
        connection.close()
    assert run_count == 1
    assert run_ids == {_SEARCH_RUN_ID}
    assert compute_flow_counts(project).identified == 3


@pytest.mark.integration
def test_build_store__run_twice_over_layer0_with_abstract_runs__is_byte_stable(
    tmp_path: Path,
) -> None:
    """Two full rebuilds over identical Layer 0 (search + abstract) checksum identically (S03-AC1)."""
    project = Project.init("deterministic", title="Deterministic", root=tmp_path)
    _write_search_run(project.raw_dir, [1, 2, 3])
    first_seen = make_abstract_entry(eid=_eid(1), subject_areas=[{"@code": "2202"}])
    write_sealed_abstract_run(
        project.raw_dir, _ABSTRACT_RUN_ID, [first_seen], started_at=_ABSTRACT_STARTED_AT
    )
    re_enriched = make_abstract_entry(eid=_eid(1), subject_areas=[{"@code": "1702"}])
    write_sealed_abstract_run(
        project.raw_dir,
        _LATER_ABSTRACT_RUN_ID,
        [re_enriched],
        started_at=_LATER_ABSTRACT_STARTED_AT,
    )

    build_store(project, rebuild=True)
    connection = connect(project, read_only=True)
    first = table_checksums(connection)
    connection.close()

    build_store(project, rebuild=True)
    connection = connect(project, read_only=True)
    second = table_checksums(connection)
    connection.close()

    assert first == second


@pytest.mark.integration
def test_build_store__abstract_record_absent_from_records__is_skipped_and_counted(
    tmp_path: Path,
) -> None:
    """A record an abstract run describes that no search run loaded must not be silently dropped."""
    project = Project.init("unmatched", title="Unmatched", root=tmp_path)
    _write_search_run(project.raw_dir, [1])
    matched = make_abstract_entry(eid=_eid(1), subject_areas=[{"@code": "2202"}])
    unmatched = make_abstract_entry(eid=_eid(99), subject_areas=[{"@code": "1702"}])
    write_sealed_abstract_run(
        project.raw_dir,
        _ABSTRACT_RUN_ID,
        [matched, unmatched],
        started_at=_ABSTRACT_STARTED_AT,
    )

    stats = build_store(project, rebuild=True)

    assert stats.unmatched_abstract_record_ids == (_record_id(99),)
    assert stats.records_loaded == 1
    connection = connect(project, read_only=True)
    try:
        assert _subject_areas(connection, _record_id(99)) == set()
        assert _coverage_rows(connection, _record_id(99)) == set()
        (record_count,) = connection.execute(
            "SELECT COUNT(*) FROM records WHERE record_id = ?", [_record_id(99)]
        ).fetchone()
    finally:
        connection.close()
    assert record_count == 0


@pytest.mark.integration
def test_build_store__two_runs_cover_one_record__the_later_run_id_wins(tmp_path: Path) -> None:
    """A re-enrichment observes Scopus as it is now; the earlier run's codes do not linger."""
    project = Project.init("later-wins", title="Later wins", root=tmp_path)
    _write_search_run(project.raw_dir, [1])
    earlier = make_abstract_entry(eid=_eid(1), subject_areas=[{"@code": "2202"}])
    write_sealed_abstract_run(
        project.raw_dir, _ABSTRACT_RUN_ID, [earlier], started_at=_ABSTRACT_STARTED_AT
    )
    later = make_abstract_entry(eid=_eid(1), subject_areas=[{"@code": "1702"}])
    write_sealed_abstract_run(
        project.raw_dir, _LATER_ABSTRACT_RUN_ID, [later], started_at=_LATER_ABSTRACT_STARTED_AT
    )

    stats = build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        # Only the later run's codes survive in `subject_areas` ...
        assert _subject_areas(connection, _record_id(1)) == {"1702"}
        # ... but both runs' coverage rows are kept -- the PK is (record_id, run_id).
        assert _coverage_rows(connection, _record_id(1)) == {
            (_ABSTRACT_RUN_ID, "assigned"),
            (_LATER_ABSTRACT_RUN_ID, "assigned"),
        }
    finally:
        connection.close()
    assert stats.record_subject_area_coverage_loaded == 2
    assert stats.subject_area_links_loaded == 1


@pytest.mark.integration
def test_build_store__unsealed_abstract_run__is_ignored_entirely(tmp_path: Path) -> None:
    """An in-progress (unsealed) abstract run contributes nothing -- a partial load is worse than none."""
    project = Project.init("unsealed", title="Unsealed", root=tmp_path)
    _write_search_run(project.raw_dir, [1])
    entry = make_abstract_entry(eid=_eid(1), subject_areas=[{"@code": "2202"}])
    sealed_dir = write_sealed_abstract_run(
        project.raw_dir, _ABSTRACT_RUN_ID, [entry], started_at=_ABSTRACT_STARTED_AT
    )
    # Seal the run, then remove exactly the manifest -- `is_sealed` keys off
    # nothing else, so this is the minimal way to make an otherwise-complete
    # run directory look unsealed, exactly as an interrupted `capture_abstracts`
    # would leave one (manifest written last).
    (sealed_dir / "manifest.json").unlink()

    stats = build_store(project, rebuild=True)

    assert stats.abstract_runs_loaded == 0
    assert stats.subject_area_links_loaded == 0
    connection = connect(project, read_only=True)
    try:
        (coverage_count,) = connection.execute(
            "SELECT COUNT(*) FROM record_subject_area_coverage"
        ).fetchone()
    finally:
        connection.close()
    assert coverage_count == 0


#: Pairwise-distinct by construction, so an identity error (e.g. reporting
#: `after_automated` where `excluded_automated` belongs) is visible rather
#: than accidentally passing: 5 identified, 1 excluded, 4 remaining.
_E2E_KEEP_BOTH = RecordSpec(number=1)  # ENGI + COMP via abstract run -> keep
_E2E_KEEP_MATH = RecordSpec(number=2)  # MATH via abstract run -> keep
_E2E_EXCLUDE_MEDI = RecordSpec(number=3)  # MEDI via abstract run -> excluded
_E2E_NEVER_ENRICHED = RecordSpec(number=4)  # no abstract-run entry at all -> keep
_E2E_ASSIGNED_NONE = RecordSpec(number=5)  # enriched, Scopus assigned none -> keep


@pytest.mark.integration
def test_build_store__real_asjc_codes_from_an_abstract_run__filters_the_engine_end_to_end(
    tmp_path: Path,
) -> None:
    """The Stage 4 engine, fed subject areas that came from Layer 0 abstract runs, not test scaffolding.

    Distinct from ``test_engine.py``'s own ASJC tests: those write
    ``subject-area`` directly onto a search entry, exercising the (currently
    unobserved in practice) search-entry path. This is the path ADR 0018
    actually completes: search entries carry no subject-area data at all,
    ``prismabib enrich`` supplies it via a separate sealed run, and the
    engine must see exactly the same result once that run is loaded.
    """
    project = Project.init("e2e-asjc", title="E2E ASJC", root=tmp_path)
    records = [
        _E2E_KEEP_BOTH,
        _E2E_KEEP_MATH,
        _E2E_EXCLUDE_MEDI,
        _E2E_NEVER_ENRICHED,
        _E2E_ASSIGNED_NONE,
    ]
    write_criteria(project, CriteriaSpec(subject_areas=("COMP", "ENGI", "MATH", "MULT")))
    write_sealed_run(
        project.raw_dir,
        _SEARCH_RUN_ID,
        [record.to_entry() for record in records],
        started_at=_SEARCH_STARTED_AT,
        total_results=len(records),
    )
    write_sealed_abstract_run(
        project.raw_dir,
        _ABSTRACT_RUN_ID,
        [
            make_abstract_entry(
                eid=_E2E_KEEP_BOTH.eid,
                subject_areas=[{"@code": "2202"}, {"@code": "1702"}],  # ENGI, COMP
            ),
            make_abstract_entry(eid=_E2E_KEEP_MATH.eid, subject_areas=[{"@code": "2611"}]),  # MATH
            make_abstract_entry(
                eid=_E2E_EXCLUDE_MEDI.eid, subject_areas=[{"@code": "2746"}]
            ),  # MEDI
            make_abstract_entry(eid=_E2E_ASSIGNED_NONE.eid, subject_areas=None),
            # `_E2E_NEVER_ENRICHED` is absent from this run entirely.
        ],
        started_at=_ABSTRACT_STARTED_AT,
        records_requested=4,
    )
    build_store(project, rebuild=True)

    # `_refuse_unenforceable_subject_filter` no longer raises: real, recognised
    # subject-area data now exists for this corpus.
    automated = engine.automated_set(project)

    assert automated == {
        _E2E_KEEP_BOTH.record_id,
        _E2E_KEEP_MATH.record_id,
        _E2E_NEVER_ENRICHED.record_id,
        _E2E_ASSIGNED_NONE.record_id,
    }

    counts = compute_flow_counts(project)
    assert counts.identified == 5
    assert counts.excluded_automated_by_reason["subject_area"] == 1
    assert counts.after_automated == 4


@pytest.mark.integration
def test_build_store__subject_filter_declared_before_enrichment__still_refuses(
    tmp_path: Path,
) -> None:
    """A sanity check on the ordering the brief states: enrich -> rebuild -> amend criteria.

    Without any abstract run at all, declaring ``subject_areas`` must still be
    refused exactly as before this feature existed -- this change adds a
    *source* of subject-area data, it does not loosen the guard for a corpus
    that has none.
    """
    project = Project.init("still-refused", title="Still refused", root=tmp_path)
    write_criteria(project, CriteriaSpec(subject_areas=("COMP",)))
    _write_search_run(project.raw_dir, [1])
    build_store(project, rebuild=True)

    with pytest.raises(ConfigError, match="recognises"):
        engine.automated_set(project)
