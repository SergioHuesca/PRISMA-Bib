"""ADR 0042 PR4b: identification and the flow equation for OpenAlex-primary projects.

Every project here is hand-built from synthetic sealed ``raw/openalex-search/`` runs
(``tests/openalex_search_helpers.py``), so nothing depends on the capture code or the network.
Equation 1 is the check under test: ``identified`` comes from the manifests, the duplicates from
the loader and the removals from ``malformed_entries``, and none is a remainder of the others.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from prismabib.prisma.flow import FlowCounts, compute_flow_counts
from prismabib.project import Project
from prismabib.store.db import connect
from prismabib.store.load import build_store
from tests.openalex_search_helpers import openalex_project, work, write_search_run
from tests.store_helpers import insert_registered_record, write_sealed_run

pytestmark = pytest.mark.integration

_RUN_A = "20261103T100000Z-aaaaaaaa"
_RUN_B = "20261103T110000Z-bbbbbbbb"
_RUN_C = "20261103T120000Z-cccccccc"


def _flow(project: Project) -> FlowCounts:
    build_store(project, rebuild=True)
    counts = compute_flow_counts(project)
    counts.assert_consistent()
    return counts


def test_flow_counts__openalex_mode__single_query__identified_is_the_manifest_total(
    tmp_path: Path,
) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "carbon capture", [[work(1), work(2)], [work(3)]])

    counts = _flow(project)

    assert (counts.identified, counts.duplicates_across_searches) == (3, 0)
    assert (counts.removed_other_reasons, counts.after_automated) == (0, 3)


def test_flow_counts__openalex_mode__identified_is_the_total_not_the_captured_count(
    tmp_path: Path,
) -> None:
    """The count is the server's ``meta.count``. A sealed run that disagrees must fail, not close."""
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1), work(2)]], total=5)
    build_store(project, rebuild=True)

    counts = compute_flow_counts(project)

    assert counts.identified == 5
    assert counts.after_automated == 2


def test_flow_counts__openalex_mode__identity_closes_with_two_overlapping_queries(
    tmp_path: Path,
) -> None:
    """ADR 0042 Verification 46."""
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "query a", [[work(1), work(2), work(3)]])
    write_search_run(project, _RUN_B, "query b", [[work(2), work(3), work(4)]])

    counts = _flow(project)

    assert counts.identified == 6
    assert counts.duplicates_across_searches == 2
    assert counts.after_automated == 4


def test_flow_counts__openalex_mode__duplicates_read_from_run_duplicates(tmp_path: Path) -> None:
    """ADR 0042 Verification 47: the duplicate term is the stored measurement, not a remainder."""
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "query a", [[work(1), work(2)]])
    write_search_run(project, _RUN_B, "query b", [[work(2), work(3)]])
    build_store(project, rebuild=True)
    connection = connect(project, read_only=False)
    try:
        connection.execute("DELETE FROM run_duplicates")
    finally:
        connection.close()

    counts = compute_flow_counts(project)

    assert counts.duplicates_across_searches == 0
    with pytest.raises(Exception, match="inconsistent"):
        counts.assert_consistent()


def test_flow_counts__same_query_refresh__not_a_duplicate(tmp_path: Path) -> None:
    """ADR 0042 Verification 48 (ADR 0013): a refresh adds no identified, member or duplicate."""
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "query a", [[work(1), work(2)]])
    write_search_run(project, _RUN_B, "query  a", [[work(1), work(2), work(3)]], offset_minutes=60)

    counts = _flow(project)

    assert (counts.identified, counts.duplicates_across_searches) == (2, 0)
    assert counts.after_automated == 2


def test_flow_counts__refresh_of_a_later_query__does_not_re_count_its_overlap(
    tmp_path: Path,
) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "query a", [[work(1), work(2)]])
    write_search_run(project, _RUN_B, "query b", [[work(2), work(3)]], offset_minutes=60)
    write_search_run(project, _RUN_C, "query b", [[work(2), work(3)]], offset_minutes=120)

    counts = _flow(project)

    assert (counts.identified, counts.duplicates_across_searches) == (4, 1)


def test_flow_counts__doi_twin_across_ids__counted_as_a_duplicate(tmp_path: Path) -> None:
    """Q-C: two OpenAlex ids with one DOI are one paper, found twice."""
    project = openalex_project(tmp_path)
    twin = work(2, doi="https://doi.org/10.1/ITEM-1")
    write_search_run(project, _RUN_A, "q", [[work(1), twin, work(3)]])

    counts = _flow(project)

    assert (counts.identified, counts.duplicates_across_searches) == (3, 1)
    assert counts.after_automated == 2


def test_flow_counts__doi_twin_found_by_another_query__counted_as_a_duplicate(
    tmp_path: Path,
) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "query a", [[work(1)]])
    write_search_run(project, _RUN_B, "query b", [[work(9, doi="https://doi.org/10.1/item-1")]])

    counts = _flow(project)

    assert (counts.identified, counts.duplicates_across_searches) == (2, 1)


def test_flow_counts__all_works_malformed__every_one_is_removed(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1, title=None), work(2, title=None)]])

    counts = _flow(project)

    assert (counts.identified, counts.removed_other_reasons) == (2, 2)
    assert counts.after_automated == 0


def test_flow_counts__mixed_malformed_and_loaded__both_terms_close(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(
        project,
        _RUN_A,
        "q",
        [[work(1), work(2, title=None), work(3, id=None)], [work(4, title=None), work(5)]],
    )

    counts = _flow(project)

    assert (counts.identified, counts.removed_other_reasons) == (5, 3)
    assert counts.after_automated == 2


def test_flow_counts__empty_run__is_zero_everywhere(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[]])

    counts = _flow(project)

    assert (counts.identified, counts.after_automated) == (0, 0)


def test_flow_counts__unsealed_or_discarded_run__is_not_identified(tmp_path: Path) -> None:
    """A run whose captured works are fewer than ``total_count`` never sealed: it counts nothing."""
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1), work(2)]], total=5, sealed=False)
    write_search_run(project, _RUN_B, "q2", [[work(3)]], total=1, sealed=False, discarded=True)
    write_search_run(project, _RUN_C, "q3", [[work(4)]])

    counts = _flow(project)

    assert (counts.identified, counts.after_automated) == (1, 1)


def test_flow_counts__openalex_mode_with_a_registration__registered_term_is_unchanged(
    tmp_path: Path,
) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1), work(2)]])
    build_store(project, rebuild=True)
    connection = connect(project, read_only=False)
    try:
        insert_registered_record(connection, "registered:ev-1")
    finally:
        connection.close()

    counts = compute_flow_counts(project)
    counts.assert_consistent()

    assert (counts.identified, counts.registered, counts.after_automated) == (2, 1, 3)


def test_flow_counts__no_search_runs__every_count_is_zero(tmp_path: Path) -> None:
    counts = _flow(openalex_project(tmp_path))

    assert (counts.identified, counts.duplicates_across_searches, counts.after_automated) == (
        0,
        0,
        0,
    )


def test_flow_counts__equation_one__fails_when_loader_disagrees_with_manifest(
    tmp_path: Path,
) -> None:
    """ADR 0042 Verification 50: the equation can fail, because nothing is a remainder."""
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1), work(2), work(3)]])
    build_store(project, rebuild=True)
    connection = connect(project, read_only=False)
    try:
        connection.execute("UPDATE openalex_search_runs SET total_count = total_count + 1")
    finally:
        connection.close()

    counts = compute_flow_counts(project)

    assert counts.identified == 4
    with pytest.raises(Exception, match="inconsistent"):
        counts.assert_consistent()


# -- the unloadable rule on records_all --------------------------------------------------------------


@pytest.mark.parametrize("valid_first", [True, False])
def test_unloadable__malformed_search_work_loaded_elsewhere__is_a_duplicate_and_equation_closes(
    tmp_path: Path, valid_first: bool
) -> None:
    """ADR 0042 Verification 51, with the owner's rule of 2026-11-04, in both query orders."""
    project = openalex_project(tmp_path)
    good = [[work(1), work(2)]]
    bad = [[work(3), work(2, title=None)]]
    first, second = (good, bad) if valid_first else (bad, good)
    write_search_run(project, _RUN_A, "query a", first)
    write_search_run(project, _RUN_B, "query b", second)

    counts = _flow(project)

    assert counts.removed_other_reasons == 0
    assert (counts.identified, counts.duplicates_across_searches) == (4, 1)
    assert counts.after_automated == 3


def test_unloadable__malformed_copy_in_the_same_run_as_its_valid_twin__is_a_duplicate(
    tmp_path: Path,
) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1, title=None), work(2)], [work(1)]])

    counts = _flow(project)

    assert (counts.removed_other_reasons, counts.duplicates_across_searches) == (0, 1)
    assert counts.after_automated == 2


@pytest.mark.parametrize("valid_first", [True, False])
def test_unloadable__malformed_copy_with_only_an_id__is_a_duplicate_by_id(
    tmp_path: Path, valid_first: bool
) -> None:
    """The copy has no DOI to match on, so only its OpenAlex id can identify it."""
    project = openalex_project(tmp_path)
    good = [[work(1), work(2)]]
    bad = [[work(3), work(2, title=None, doi=None)]]
    first, second = (good, bad) if valid_first else (bad, good)
    write_search_run(project, _RUN_A, "query a", first)
    write_search_run(project, _RUN_B, "query b", second)

    counts = _flow(project)

    assert (counts.removed_other_reasons, counts.duplicates_across_searches) == (0, 1)


def test_unloadable__malformed_copy_found_by_its_doi__is_a_duplicate(tmp_path: Path) -> None:
    """No usable id, but the DOI is a member's: the DOI is the dedup key first."""
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "query a", [[work(1)]])
    write_search_run(
        project,
        _RUN_B,
        "query b",
        [[work(9, id=None, doi="https://doi.org/10.1/ITEM-1")]],
    )

    counts = _flow(project)

    assert (counts.removed_other_reasons, counts.duplicates_across_searches) == (0, 1)


def test_unloadable__malformed_work_with_unknown_identity__stays_malformed_and_closes(
    tmp_path: Path,
) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "query a", [[work(1), work(5, title=None)]])
    write_search_run(project, _RUN_B, "query b", [[work(2), work(6, id=None, doi=None)]])

    counts = _flow(project)

    assert (counts.removed_other_reasons, counts.duplicates_across_searches) == (2, 0)
    assert counts.after_automated == 2


def test_unloadable__malformed_everywhere__counted_once_per_occurrence_of_a_lost_work(
    tmp_path: Path,
) -> None:
    """A work that is malformed in every query is one lost record, subtracted once."""
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "query a", [[work(1), work(2, title=None)]])
    write_search_run(project, _RUN_B, "query b", [[work(3), work(2, title=None)]])

    counts = _flow(project)

    assert counts.removed_other_reasons == 1
    assert counts.duplicates_across_searches == 1


def test_unloadable__malformed_copy_in_a_refresh_run__is_not_counted(tmp_path: Path) -> None:
    """Refreshes never add duplicates, malformed copies included."""
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "query a", [[work(1), work(2)]])
    write_search_run(
        project, _RUN_B, "query a", [[work(1), work(2, title=None)]], offset_minutes=60
    )

    counts = _flow(project)

    assert (counts.removed_other_reasons, counts.duplicates_across_searches) == (0, 0)


def test_unloadable__null_id_malformed_search_work__is_counted(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1), work(2, id=None)]])

    counts = _flow(project)

    assert counts.removed_other_reasons == 1


def test_unloadable__same_bad_work_in_two_queries__counted_once(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "query a", [[work(1), work(2, title=None)]])
    write_search_run(project, _RUN_B, "query b", [[work(3), work(2, title=None)]])

    counts = _flow(project)

    assert (counts.removed_other_reasons, counts.duplicates_across_searches) == (1, 1)


def test_malformed__two_bad_works_in_one_page__both_count_in_removed_other_reasons(
    tmp_path: Path,
) -> None:
    """ADR 0042 Verification 51a, flow side."""
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1, title=None), work(2, title=None), work(3)]])

    counts = _flow(project)

    assert counts.removed_other_reasons == 2


def test_unloadable__openalex_enrichment_body__not_counted(tmp_path: Path) -> None:
    """ADR 0042 Verification 52: the NULL-id exemption of an enrichment body still holds."""
    project = openalex_project(tmp_path, "scopus+openalex")
    write_sealed_run(
        project.raw_dir,
        "20260101T000000Z-aaaaaaaa",
        [],
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        query="q",
        total_results=0,
    )
    build_store(project, rebuild=True)
    connection = connect(project, read_only=False)
    try:
        connection.execute(
            "INSERT INTO malformed_entries VALUES"
            " ('r', 'openalex/r/response-0000.jsonl', 0, NULL, 'malformed_openalex_body')"
        )
    finally:
        connection.close()

    assert compute_flow_counts(project).removed_other_reasons == 0


# -- the Scopus modes keep today's numbers -------------------------------------------------------------


@pytest.mark.parametrize("mode", ["scopus", "scopus+openalex"])
def test_flow_counts__scopus_modes__ignore_openalex_search_runs(tmp_path: Path, mode: str) -> None:
    """ADR 0042 Verification 49 (mode gating): the Scopus modes read ``runs`` only."""
    project = openalex_project(tmp_path, mode)
    write_sealed_run(
        project.raw_dir,
        "20260101T000000Z-aaaaaaaa",
        [],
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        query="q",
        total_results=0,
    )
    write_search_run(project, _RUN_A, "q", [[work(1), work(2)]])

    counts = _flow(project)

    assert (counts.identified, counts.after_automated) == (0, 0)


def test_flow_counts__legacy_project__reads_runs(tmp_path: Path) -> None:
    project = Project.init("legacy", title="Legacy", root=tmp_path)
    write_sealed_run(
        project.raw_dir,
        "20260101T000000Z-aaaaaaaa",
        [],
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        query="q",
        total_results=7,
    )
    build_store(project, rebuild=True)

    assert compute_flow_counts(project).identified == 7


def test_stats__duplicate_doi_statistic__counts_openalex_records_too(tmp_path: Path) -> None:
    """Q-C, build report: DOI collisions are counted across ``records`` and ``openalex_records``."""
    from prismabib.store.load import _stats_from_connection

    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1), work(2)]])
    build_store(project, rebuild=True)
    connection = connect(project, read_only=False)
    try:
        connection.execute(
            "INSERT INTO openalex_records BY NAME"
            " SELECT * REPLACE ('openalex:twin' AS record_id)"
            " FROM openalex_records WHERE record_id LIKE '%W1'"
        )
        stats = _stats_from_connection(connection, rebuilt=False)
    finally:
        connection.close()

    assert (stats.duplicate_doi_groups, stats.duplicate_records) == (1, 2)


def test_unloadable__same_unknown_bad_work_twice_in_one_run__closes(tmp_path: Path) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(1), work(2, title=None), work(2, title=None)]])

    counts = _flow(project)

    assert (counts.identified, counts.removed_other_reasons) == (3, 1)
    assert counts.duplicates_across_searches == 1


def test_unloadable__same_unknown_bad_work_on_two_pages_of_one_query__closes(
    tmp_path: Path,
) -> None:
    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "q", [[work(2, title=None), work(1)], [work(2, title=None)]])

    counts = _flow(project)

    assert (counts.identified, counts.removed_other_reasons) == (3, 1)
    assert counts.duplicates_across_searches == 1


def _random_project(tmp_path: Path, seed: int) -> Project:
    """A project from ``seed``: runs of random overlapping, twinned and malformed works."""
    import random

    rng = random.Random(seed)
    project = openalex_project(tmp_path)
    run_ids = [f"20261103T{h:02d}0000Z-{h:08x}" for h in range(10, 10 + rng.randint(1, 6))]
    rng.shuffle(run_ids)
    queries = [f"query {n}" for n in range(rng.randint(1, 4))]
    for position, run_id in enumerate(run_ids):
        query = rng.choice(queries)
        works = []
        for _ in range(rng.randint(0, 6)):
            n = rng.randint(1, 8)
            kind = rng.choice(["ok", "ok", "twin", "no_title", "no_title_no_doi", "no_id", "bare"])
            if kind == "twin":  # another id, the DOI of work n
                works.append(work(n + 20, doi=f"https://doi.org/10.1/Item-{n}"))
            elif kind == "no_title":
                works.append(work(n, title=None))
            elif kind == "no_title_no_doi":
                works.append(work(n, title=None, doi=None))
            elif kind == "no_id":
                works.append(work(n, id=None))
            elif kind == "bare":
                works.append(work(n, doi=None))
            else:
                works.append(work(n))
        pages = [works[i : i + 3] for i in range(0, len(works), 3)] or [[]]
        sealed = rng.random() < 0.8
        write_search_run(
            project,
            run_id,
            query,
            pages,
            sealed=sealed,
            discarded=not sealed and rng.random() < 0.5,
            offset_minutes=position,
        )
    return project


@pytest.mark.parametrize("seed", range(60))
def test_flow_counts__randomised_openalex_projects__equation_one_always_closes(
    tmp_path: Path, seed: int
) -> None:
    """Fixed seeds: refreshes, DOI twins, malformed works, empty and unsealed runs, run order."""
    _flow(_random_project(tmp_path, seed))


def test_numbers_map__openalex_mode__carries_the_source_sentence(tmp_path: Path) -> None:
    """ADR 0042 methods text: the exported numbers name OpenAlex and the stored query."""
    from prismabib.report.numbers import numbers_map

    project = openalex_project(tmp_path)
    write_search_run(project, _RUN_A, "carbon capture", [[work(1), work(2)]])
    build_store(project, rebuild=True)

    sentence = numbers_map(project)["methods.source_sentence"]

    assert sentence.startswith("Records were identified by searching OpenAlex;")
    assert "`carbon capture`, run on 2026-11-03" in sentence
    assert "OpenAlex reported 2 works across 1 distinct queries" in sentence
