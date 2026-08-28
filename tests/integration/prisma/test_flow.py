"""Integration tests for :mod:`prismabib.prisma.flow` (BUILD_PLAN §Stage 4, lines 1034-1043).

Every number here is derived end-to-end: a real Layer 0 archive is loaded
into a real Layer 1 store, real decisions are appended through
:class:`~prismabib.prisma.log.DecisionLog`, and
:func:`~prismabib.prisma.flow.compute_flow_counts` reads both. Nothing is
stubbed, because the thing under test *is* the derivation.

``assert_consistent``'s pure arithmetic (including one failing case per
equation) is unit-tested in ``tests/unit/prisma/test_flow.py``; what this
module adds is that the numbers ``compute_flow_counts`` actually produces
satisfy it, and that a genuinely corrupted project makes it fire.

The published golden itself, and the screening plan that produces it, live in
:mod:`tests.prisma_helpers` -- ``tests/e2e/`` asserts against the same one, and
a golden with two definitions is a golden that can drift.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import time_machine

from prismabib.errors import ValidationError
from prismabib.prisma import engine
from prismabib.prisma.flow import FlowCounts, compute_flow_counts
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.load import build_store
from tests.conftest import SeededIdFactory
from tests.prisma_helpers import (
    ABSTRACT_EXCLUDED,
    ABSTRACT_INCLUDED,
    CorpusSpec,
    CriteriaSpec,
    RecordSpec,
    build_project,
    copy_reference_project_with_criteria,
    overwrite_log_bytes,
    read_log_bytes,
    reference_golden,
    rewrite_sidecar,
    screen_reference_project,
)
from tests.store_helpers import write_sealed_run


@pytest.fixture
def screened_reference(tmp_path: Path) -> Project:
    """The frozen reference fixture, loaded and screened by the plan above."""
    project = copy_reference_project_with_criteria(tmp_path)
    build_store(project, rebuild=True)
    screen_reference_project(project)
    return project


# ---------------------------------------------------------------------------
# BUILD_PLAN's flow-counts table
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_flow_counts__reference_fixture__matches_golden(screened_reference: Project) -> None:
    counts = compute_flow_counts(screened_reference)

    assert counts == reference_golden()


@pytest.mark.integration
@pytest.mark.acceptance("S04-AC5")
def test_flow_counts__three_fresh_runs__are_integer_identical(screened_reference: Project) -> None:
    first = compute_flow_counts(screened_reference)

    build_store(screened_reference, rebuild=True)
    second = compute_flow_counts(screened_reference)
    reopened = Project.open(screened_reference.slug, root=screened_reference.root.parent)
    third = compute_flow_counts(reopened)

    assert [first, second, third] == [reference_golden()] * 3


@pytest.mark.integration
@pytest.mark.acceptance("S04-AC4")
def test_flow_counts__consistent_stream__assert_consistent_passes(
    screened_reference: Project,
) -> None:
    counts = compute_flow_counts(screened_reference)

    assert counts.assert_consistent() is None


@pytest.mark.integration
@pytest.mark.acceptance("S04-AC4")
def test_flow_counts__injected_off_by_one__assert_consistent_raises(tmp_path: Path) -> None:
    # The corruption is in the *fixture*, not in the dataclass: the run
    # manifest claims one more result than Layer 1 actually holds, which is
    # exactly the "identified does not reconcile with the corpus" defect
    # BUILD_PLAN line 993 says this guard exists to catch.
    records = [RecordSpec(number=index) for index in range(1, 6)]
    project = build_project(
        tmp_path,
        CorpusSpec(records=records, criteria=CriteriaSpec(), total_results=len(records) + 1),
        slug="off-by-one",
    )

    counts = compute_flow_counts(project)

    assert counts.identified == 6
    with pytest.raises(ValidationError, match=r"'identified - duplicates_across_searches"):
        counts.assert_consistent()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("corpus", "expected_identified", "expected_after_automated", "expected_after_language"),
    [
        pytest.param(
            CorpusSpec(
                records=[RecordSpec(number=index) for index in range(1, 9)],
                criteria=CriteriaSpec(),
            ),
            8,
            8,
            8,
            id="nothing-filtered",
        ),
        pytest.param(
            CorpusSpec(
                records=[RecordSpec(number=index, year=2000 + index) for index in range(1, 11)],
                criteria=CriteriaSpec(year_start=2005, year_end=2010),
            ),
            10,
            6,
            6,
            id="temporal-filter",
        ),
        pytest.param(
            CorpusSpec(
                records=[
                    RecordSpec(number=index, language="German" if index % 2 else "English")
                    for index in range(1, 11)
                ],
                criteria=CriteriaSpec(languages=("English",)),
            ),
            10,
            10,
            5,
            id="language-filter",
        ),
        pytest.param(
            CorpusSpec(
                records=[
                    RecordSpec(number=index, doc_type="Review" if index % 3 else "Article")
                    for index in range(1, 13)
                ],
                criteria=CriteriaSpec(doc_types_include=("ar",), languages=("English",)),
            ),
            12,
            4,
            4,
            id="doc-type-filter",
        ),
    ],
)
def test_flow_counts__arithmetic__closes_at_every_step(
    tmp_path: Path,
    corpus: CorpusSpec,
    expected_identified: int,
    expected_after_automated: int,
    expected_after_language: int,
) -> None:
    project = build_project(tmp_path, corpus, slug="arithmetic")

    counts = compute_flow_counts(project)

    assert counts.identified == expected_identified
    assert counts.after_automated == expected_after_automated
    assert counts.after_language == expected_after_language
    assert counts.identified - counts.excluded_automated == counts.after_automated
    assert counts.after_automated - counts.excluded_language == counts.after_language
    assert (
        counts.after_language - counts.excluded_title_abstract - counts.unsure_title_abstract
        == (counts.retrieved_fulltext)
    )
    assert (
        counts.retrieved_fulltext
        - sum(counts.excluded_fulltext.values())
        - (counts.unsure_fulltext)
        == counts.included
    )
    assert counts.assert_consistent() is None


@pytest.mark.integration
def test_flow_counts__excluded_fulltext__sums_to_retrieved_minus_included(
    screened_reference: Project,
) -> None:
    counts = compute_flow_counts(screened_reference)

    assert sum(counts.excluded_fulltext.values()) == (
        counts.retrieved_fulltext - counts.included - counts.unsure_fulltext
    )
    assert sum(counts.excluded_fulltext.values()) == 3


# ---------------------------------------------------------------------------
# The derivation's own edges
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_flow_counts__project_with_no_runs__is_every_count_zero(tmp_path: Path) -> None:
    project = Project.init("empty", title="Empty", root=tmp_path)
    build_store(project, rebuild=True)

    counts = compute_flow_counts(project)

    assert counts == FlowCounts(
        identified=0,
        duplicates_across_searches=0,
        removed_other_reasons=0,
        excluded_automated=0,
        after_automated=0,
        excluded_language=0,
        after_language=0,
        excluded_title_abstract=0,
        unsure_title_abstract=0,
        retrieved_fulltext=0,
        excluded_fulltext={},
        unsure_fulltext=0,
        included=0,
    )
    assert counts.assert_consistent() is None


@pytest.mark.integration
def test_flow_counts__unscreened_project__every_eligible_record_is_unsure(
    tmp_path: Path,
) -> None:
    records = [RecordSpec(number=index) for index in range(1, 8)]
    project = build_project(tmp_path, CorpusSpec(records=records), slug="unscreened")

    counts = compute_flow_counts(project)

    assert counts.unsure_title_abstract == 7
    assert counts.retrieved_fulltext == 0
    assert counts.assert_consistent() is None


@pytest.mark.integration
def test_flow_counts__fulltext_exclude_with_no_reason_code__is_bucketed_as_unknown(
    tmp_path: Path,
) -> None:
    # `DecisionLog.append` refuses to write this, but `load`/`fold` do not
    # re-check the business rule, so a decisions.jsonl written by anything
    # else can still reach `flow.py` missing a reason code. It must stay
    # visible and counted, never silently dropped.
    records = [RecordSpec(number=1)]
    project = build_project(tmp_path, CorpusSpec(records=records), slug="unknown-reason")
    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="id"))
    included = log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=records[0].record_id,
        reviewer="kp",
        decision="include",
    )
    smuggled = json.loads(included.model_dump_json())
    smuggled.update(event_id="smuggled-0001", stage="fulltext", decision="exclude")
    overwrite_log_bytes(project, read_log_bytes(project) + (json.dumps(smuggled) + "\n").encode())
    rewrite_sidecar(project)

    counts = compute_flow_counts(project)

    assert counts.excluded_fulltext == {"UNKNOWN": 1}
    assert counts.assert_consistent() is None


@pytest.mark.integration
def test_flow_counts__two_reviewers_exclude_with_different_reasons__reports_the_later_one(
    tmp_path: Path,
) -> None:
    records = [RecordSpec(number=1)]
    project = build_project(tmp_path, CorpusSpec(records=records), slug="reason-attribution")
    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="id"))
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=records[0].record_id,
        reviewer="kp",
        decision="include",
    )
    log.append(
        stage=PrismaStage.FULLTEXT,
        record_id=records[0].record_id,
        reviewer="kp",
        decision="exclude",
        reason_code="INACCESSIBLE",
    )
    log.append(
        stage=PrismaStage.FULLTEXT,
        record_id=records[0].record_id,
        reviewer="mm",
        decision="exclude",
        reason_code="NOT_PRIMARY_RESEARCH",
    )

    counts = compute_flow_counts(project)

    assert counts.excluded_fulltext == {"NOT_PRIMARY_RESEARCH": 1}


@pytest.mark.integration
def test_flow_counts__two_reviewers_exclude__attributes_the_reason_of_the_latest_timestamp(
    tmp_path: Path,
) -> None:
    # The reason attributed to an aggregated exclude is the one on the exclude
    # with the greatest `(ts, event_id)` -- reusing `fold_events`' own
    # tie-break -- not the last one the log happens to list. Those two
    # differ here and only here: `kp` decides on the 2nd but is appended
    # first, `mm` decides on the 1st but is appended last, so "latest
    # decision" says INACCESSIBLE while "last line in the file" says
    # NOT_PRIMARY_RESEARCH. A reviewer working through a backlog and logging
    # yesterday's decisions after today's is all it takes to produce this.
    records = [RecordSpec(number=1)]
    project = build_project(tmp_path, CorpusSpec(records=records), slug="reason-by-timestamp")
    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="id"))
    with time_machine.travel(datetime(2025, 3, 1, 8, 0, tzinfo=UTC), tick=False):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=records[0].record_id,
            reviewer="kp",
            decision="include",
        )

    with time_machine.travel(datetime(2025, 3, 2, 9, 0, tzinfo=UTC), tick=False):
        log.append(
            stage=PrismaStage.FULLTEXT,
            record_id=records[0].record_id,
            reviewer="kp",
            decision="exclude",
            reason_code="INACCESSIBLE",
        )
    with time_machine.travel(datetime(2025, 3, 1, 9, 0, tzinfo=UTC), tick=False):
        log.append(
            stage=PrismaStage.FULLTEXT,
            record_id=records[0].record_id,
            reviewer="mm",
            decision="exclude",
            reason_code="NOT_PRIMARY_RESEARCH",
        )

    counts = compute_flow_counts(project)

    assert counts.excluded_fulltext == {"INACCESSIBLE": 1}
    assert counts.assert_consistent() is None


@pytest.mark.integration
def test_flow_counts__several_exclusion_reasons__are_keyed_in_sorted_order(
    tmp_path: Path,
) -> None:
    # `excluded_fulltext` is built by walking `M_abs`, a frozenset, so without
    # the sort its *key order* varies with PYTHONHASHSEED from one process to
    # the next while every count in it stays identical -- and this dict is
    # serialised straight into published output, where `json.dumps` preserves
    # insertion order. That is a byte-different `numbers.json` on a second
    # machine (Stage 11's reproducibility criterion), which no equality
    # assertion can see: `dict.__eq__`, and therefore `FlowCounts.__eq__`,
    # ignores order entirely. Hence `list(...)` and `json.dumps(...)`, which
    # do not.
    #
    # The reason codes are attached to the records in *reverse* sorted order,
    # so neither set-iteration order nor decision-log order can produce the
    # expected sequence by accident.
    reason_codes = (
        "DUPLICATE_PUBLICATION",
        "INACCESSIBLE",
        "NOT_PRIMARY_RESEARCH",
        "PROTOCOL_ONLY",
        "WRONG_OUTCOME",
        "WRONG_POPULATION",
    )
    records = [RecordSpec(number=index) for index in range(1, len(reason_codes) + 1)]
    project = build_project(
        tmp_path,
        CorpusSpec(records=records, criteria=CriteriaSpec(fulltext_reason_codes=reason_codes)),
        slug="reason-order",
    )
    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="id"))
    for record, reason_code in zip(records, reversed(reason_codes), strict=True):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record.record_id,
            reviewer="kp",
            decision="include",
        )
        log.append(
            stage=PrismaStage.FULLTEXT,
            record_id=record.record_id,
            reviewer="kp",
            decision="exclude",
            reason_code=reason_code,
        )

    counts = compute_flow_counts(project)

    assert list(counts.excluded_fulltext) == sorted(reason_codes)
    assert json.dumps(counts.excluded_fulltext) == json.dumps(
        dict.fromkeys(sorted(reason_codes), 1)
    )
    assert counts.assert_consistent() is None


@pytest.mark.integration
def test_flow_counts__second_run_refreshing_citations__does_not_double_count_identified(
    tmp_path: Path,
) -> None:
    # `identified` is the *earliest* run's manifest total, never a sum and
    # never the latest: a re-query that refreshes citation counts is not a
    # second search.
    #
    # The second run's `total_results` is deliberately *different* from the
    # first's (9 against 4). Letting it default to `len(entries)` would make
    # the two manifests agree, and then "earliest run", "latest run" and "the
    # only distinct value" would all produce 4 -- the assertion below would
    # hold under every one of those rules and discriminate between none of
    # them. 9 is also a plausible refresh: the server reports how many results
    # the query matches *today*, which grows as the literature does, while the
    # four records this project actually captured are the same four.
    records = [RecordSpec(number=index) for index in range(1, 5)]
    project = build_project(tmp_path, CorpusSpec(records=records), slug="refreshed")
    write_sealed_run(
        project.raw_dir,
        "20250601T000000Z-stage04cd",
        [record.to_entry() for record in records],
        started_at=datetime(2025, 6, 1, tzinfo=UTC),
        total_results=9,
    )
    build_store(project, rebuild=True)

    counts = compute_flow_counts(project)

    assert counts.identified == 4
    assert counts.after_automated == 4
    assert counts.assert_consistent() is None


@pytest.mark.integration
def test_flow_counts__golden_is_not_trivially_all_zero__has_a_non_empty_corpus(
    screened_reference: Project,
) -> None:
    # Guards the golden itself: a `FlowCounts` of all zeros would satisfy
    # `assert_consistent` perfectly well, so the golden must be asserted to
    # be a *screened* project's numbers, not an empty one's.
    counts = compute_flow_counts(screened_reference)

    assert counts.included == len(engine.corpus(screened_reference)) == 5
    assert counts.retrieved_fulltext == ABSTRACT_INCLUDED
    assert counts.excluded_title_abstract == ABSTRACT_EXCLUDED
    assert counts.identified == 120


_QUERY_A = 'TITLE-ABS-KEY("alpha")'
_QUERY_B = 'TITLE-ABS-KEY("beta")'


def _search_entry(number: int) -> dict[str, object]:
    """One minimal Scopus search entry, enough for the loader to build a record."""
    return {
        "dc:identifier": f"SCOPUS_ID:{number}",
        "eid": f"2-s2.0-{number}",
        "dc:title": f"Title {number}",
        "prism:coverDate": "2020-01-01",
        "subtype": "ar",
        "prism:publicationName": "Journal",
        "dc:description": "abstract",
        "citedby-count": "1",
    }


def _project_with_runs(tmp_path: Path, runs: list[tuple[str, str, list[int]]]) -> Project:
    """Build a project from ``(run_id_suffix, query, record_numbers)`` triples."""
    project = Project.init("multi", title="Multi", root=tmp_path)
    for index, (suffix, query, numbers) in enumerate(runs):
        write_sealed_run(
            project.raw_dir,
            f"2026010{index + 1}T000000Z-{suffix}",
            [_search_entry(n) for n in numbers],
            started_at=datetime(2026, 1, index + 1, tzinfo=UTC),
            query=query,
            total_results=len(numbers),
        )
    build_store(project, rebuild=True)
    return project


@pytest.mark.integration
def test_flow_counts__two_distinct_searches__sums_identified_and_counts_the_overlap(
    tmp_path: Path,
) -> None:
    """Two search strings identify the union; the papers both found are duplicates.

    This is the shape no test in this suite exercised before -- every run was
    written with the helper's default query, so `run_duplicates` was never
    non-empty and the whole distinct-query path was dead under CI.
    """
    project = _project_with_runs(
        tmp_path, [("aaaaaaaa", _QUERY_A, [1, 2]), ("bbbbbbbb", _QUERY_B, [2, 3])]
    )

    counts = compute_flow_counts(project)

    assert (counts.identified, counts.duplicates_across_searches) == (4, 1)
    counts.assert_consistent()


@pytest.mark.integration
def test_flow_counts__refresh_of_a_later_search__does_not_re_subtract_its_overlap(
    tmp_path: Path,
) -> None:
    """A refresh of *any* search must not re-subtract, not just the first one.

    The gate first compared a record against its **first-seen** query, which
    only protected refreshes of whichever search saw the record first. A record
    found by A, re-found by B, then re-found by a refresh of B compared unequal
    to A both times and was counted twice.

    It ran on the operator's real corpus: three sealed runs -- search A, search
    B, and a byte-identical refresh of B -- reported 160 duplicates against a
    true overlap of 80, and the diagram failed by -81 with an unactionable
    "re-run --rebuild" remedy.
    """
    project = _project_with_runs(
        tmp_path,
        [
            ("aaaaaaaa", _QUERY_A, [1, 2]),
            ("bbbbbbbb", _QUERY_B, [2, 3]),
            ("cccccccc", _QUERY_B, [2, 3]),
        ],
    )

    counts = compute_flow_counts(project)

    # identified is unchanged by the refresh: B is one distinct query.
    assert (counts.identified, counts.duplicates_across_searches) == (4, 1)
    counts.assert_consistent()


@pytest.mark.integration
def test_flow_counts__one_record_unloadable_in_two_runs__is_subtracted_once(
    tmp_path: Path,
) -> None:
    """`malformed_entries` is keyed per Layer 0 line, but PRISMA counts records.

    The same paper failing in two runs of one search wrote two rows, and
    `count(*)` subtracted it twice. On the operator's corpus that was the
    second half of a -81 discrepancy.
    """
    project = Project.init("twice", title="Twice", root=tmp_path)
    broken = _search_entry(2)
    del broken["dc:title"]
    for index, suffix in enumerate(("aaaaaaaa", "bbbbbbbb")):
        write_sealed_run(
            project.raw_dir,
            f"2026010{index + 1}T000000Z-{suffix}",
            [_search_entry(1), broken],
            started_at=datetime(2026, 1, index + 1, tzinfo=UTC),
            query=_QUERY_A,
            total_results=2,
        )
    build_store(project, rebuild=True)

    counts = compute_flow_counts(project)

    assert counts.removed_other_reasons == 1
    counts.assert_consistent()


@pytest.mark.integration
def test_flow_counts__entry_unloadable_in_one_run_but_loaded_by_another__is_not_subtracted(
    tmp_path: Path,
) -> None:
    """A skipped entry is not a lost record when another run loaded that paper.

    ADR 0012 says so in words; the arithmetic ignored it and subtracted a
    record that is present in `records`, which understates the corpus.
    """
    project = Project.init("recovered", title="Recovered", root=tmp_path)
    broken = _search_entry(2)
    del broken["dc:title"]
    write_sealed_run(
        project.raw_dir,
        "20260101T000000Z-aaaaaaaa",
        [_search_entry(1), broken],
        started_at=datetime(2026, 1, 1, tzinfo=UTC),
        query=_QUERY_A,
        total_results=2,
    )
    write_sealed_run(
        project.raw_dir,
        "20260102T000000Z-bbbbbbbb",
        [_search_entry(1), _search_entry(2)],
        started_at=datetime(2026, 1, 2, tzinfo=UTC),
        query=_QUERY_A,
        total_results=2,
    )
    build_store(project, rebuild=True)

    counts = compute_flow_counts(project)

    assert counts.removed_other_reasons == 0
    counts.assert_consistent()
