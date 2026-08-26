"""Integration tests for :mod:`prismabib.prisma.engine` (BUILD_PLAN §Stage 4, lines 939-951, 1045-1051).

Real DuckDB, real ``criteria.yaml``, real ``decisions.jsonl`` -- the engine
is only meaningful against an actual Layer 1 store, so every set here is
computed from one. The set-algebra *invariants* over arbitrary event streams
live in ``tests/property/test_engine_invariants.py``; this module pins the
concrete, worked membership of one deliberately-shaped corpus, where every
record exists to be excluded (or not) by exactly one criteria dimension.

``CORPUS`` below is that corpus. Its expected ``A`` and ``L`` are written
out as literal sets rather than recomputed from the specs, so a change in
the filter logic shows up as a diff against a stated expectation instead of
against a second implementation of the same rules.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

import pytest

from prismabib.errors import LogError
from prismabib.prisma import engine
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.stage import PrismaStage
from tests.conftest import SeededIdFactory
from tests.prisma_helpers import (
    CorpusSpec,
    CriteriaSpec,
    RecordSpec,
    build_project,
    commit_criteria,
    overwrite_log_bytes,
    write_criteria,
)

JOURNAL_2020 = RecordSpec(number=1)
JOURNAL_1999 = RecordSpec(number=2, year=1999)
GERMAN_JOURNAL = RecordSpec(number=3, language="German")
WHITELISTED_CONFERENCE = RecordSpec(
    number=4,
    doc_type="Conference Paper",
    aggregation_type="Conference Proceeding",
    venue_name="Proceedings of CVPR 2020",
)
UNLISTED_CONFERENCE = RecordSpec(
    number=5,
    doc_type="Conference Paper",
    aggregation_type="Conference Proceeding",
    venue_name="Proceedings of the Obscure Workshop",
)
REVIEW_ARTICLE = RecordSpec(number=6, doc_type="Review")
UNKNOWN_LANGUAGE = RecordSpec(number=7, language=None)
OFF_SUBJECT = RecordSpec(number=8, subject_areas=("MEDI",))
ON_SUBJECT = RecordSpec(number=9, subject_areas=("COMP",))
DOC_TYPE_AS_CODE = RecordSpec(number=10, doc_type="ar")

ALL_RECORDS = [
    JOURNAL_2020,
    JOURNAL_1999,
    GERMAN_JOURNAL,
    WHITELISTED_CONFERENCE,
    UNLISTED_CONFERENCE,
    REVIEW_ARTICLE,
    UNKNOWN_LANGUAGE,
    OFF_SUBJECT,
    ON_SUBJECT,
    DOC_TYPE_AS_CODE,
]

CRITERIA = CriteriaSpec(
    version="1.0.0",
    year_start=2016,
    year_end=2026,
    subject_areas=("COMP", "ENGI"),
    doc_types_include=("ar", "cp"),
    conference_whitelist=("CVPR", "ICCV"),
    languages=("English",),
)

CORPUS = CorpusSpec(records=ALL_RECORDS, criteria=CRITERIA)

EXPECTED_RAW = {record.record_id for record in ALL_RECORDS}
EXPECTED_AUTOMATED = {
    JOURNAL_2020.record_id,
    GERMAN_JOURNAL.record_id,
    WHITELISTED_CONFERENCE.record_id,
    UNKNOWN_LANGUAGE.record_id,
    ON_SUBJECT.record_id,
    DOC_TYPE_AS_CODE.record_id,
}
EXPECTED_LANGUAGE = EXPECTED_AUTOMATED - {GERMAN_JOURNAL.record_id}


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """The worked corpus above, loaded into a real Layer 1 store."""
    return build_project(tmp_path, CORPUS, slug="engine")


def open_log(project: Project, *, prefix: str = "id") -> DecisionLog:
    """A :class:`DecisionLog` with a seeded, deterministic id factory."""
    return DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix=prefix))


# ---------------------------------------------------------------------------
# S_raw, A, L -- the deterministic sets
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_raw_set__built_store__is_every_layer_1_record(project: Project) -> None:
    assert engine.raw_set(project) == EXPECTED_RAW


@pytest.mark.integration
def test_automated_set__worked_criteria__is_exactly_the_expected_records(
    project: Project,
) -> None:
    assert engine.automated_set(project) == EXPECTED_AUTOMATED


@pytest.mark.integration
def test_language_set__worked_criteria__excludes_only_the_non_english_record(
    project: Project,
) -> None:
    assert engine.language_set(project) == EXPECTED_LANGUAGE


@pytest.mark.integration
def test_language_set__is_a_subset_of_automated_set__which_is_a_subset_of_raw(
    project: Project,
) -> None:
    raw = engine.raw_set(project)
    automated = engine.automated_set(project)
    language = engine.language_set(project)

    assert language <= automated <= raw


@pytest.mark.integration
def test_automated_set__every_criteria_list_empty__restricts_nothing(project: Project) -> None:
    write_criteria(project, CriteriaSpec(year_start=1900, year_end=2100))

    assert engine.automated_set(project) == EXPECTED_RAW
    assert engine.language_set(project) == EXPECTED_RAW


@pytest.mark.integration
@pytest.mark.parametrize("include", [("ar",), ("Article",), ("AR",), ("article", "cp")])
def test_automated_set__doc_type_written_as_code_or_description__both_match(
    project: Project, include: tuple[str, ...]
) -> None:
    write_criteria(project, CriteriaSpec(doc_types_include=include))

    assert JOURNAL_2020.record_id in engine.automated_set(project)


@pytest.mark.integration
def test_automated_set__doc_type_not_in_include__is_excluded(project: Project) -> None:
    write_criteria(project, CriteriaSpec(doc_types_include=("re",)))

    automated = engine.automated_set(project)

    assert REVIEW_ARTICLE.record_id in automated
    assert JOURNAL_2020.record_id not in automated


@pytest.mark.integration
def test_automated_set__conference_whitelist__narrows_conferences_but_not_journals(
    project: Project,
) -> None:
    write_criteria(project, CriteriaSpec(conference_whitelist=("CVPR", "ICCV")))

    automated = engine.automated_set(project)

    assert WHITELISTED_CONFERENCE.record_id in automated
    assert UNLISTED_CONFERENCE.record_id not in automated
    assert JOURNAL_2020.record_id in automated


@pytest.mark.integration
def test_automated_set__record_with_no_subject_area_rows__is_never_excluded_by_subject(
    project: Project,
) -> None:
    write_criteria(project, CriteriaSpec(subject_areas=("COMP",)))

    automated = engine.automated_set(project)

    assert JOURNAL_2020.record_id in automated
    assert ON_SUBJECT.record_id in automated
    assert OFF_SUBJECT.record_id not in automated


@pytest.mark.integration
@pytest.mark.parametrize(
    ("year_start", "year_end", "expected_member"),
    [(2020, 2020, True), (2021, 2026, False), (1999, 2019, False), (1999, 2020, True)],
)
def test_automated_set__temporal_window__is_inclusive_at_both_ends(
    project: Project, year_start: int, year_end: int, expected_member: bool
) -> None:
    write_criteria(project, CriteriaSpec(year_start=year_start, year_end=year_end))

    assert (JOURNAL_2020.record_id in engine.automated_set(project)) is expected_member


@pytest.mark.integration
def test_language_set__record_with_no_language__is_never_excluded_by_language(
    project: Project,
) -> None:
    write_criteria(project, CriteriaSpec(languages=("English",)))

    language = engine.language_set(project)

    assert UNKNOWN_LANGUAGE.record_id in language
    assert GERMAN_JOURNAL.record_id not in language


@pytest.mark.integration
def test_automated_set__a_logged_decision__cannot_widen_it(project: Project) -> None:
    before = engine.automated_set(project)
    log = open_log(project)

    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_1999.record_id,
        reviewer="kp",
        decision="include",
    )

    assert engine.automated_set(project) == before
    assert JOURNAL_1999.record_id not in engine.automated_set(project)


# ---------------------------------------------------------------------------
# M_abs, M_full, C -- the folded sets
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_manual_abstract_set__no_decisions_logged__is_empty(project: Project) -> None:
    assert engine.manual_abstract_set(project) == frozenset()
    assert engine.corpus(project) == frozenset()


@pytest.mark.integration
@pytest.mark.parametrize(
    ("decision", "reason_code", "expected_member"),
    [("include", None, True), ("exclude", "OFF_TOPIC", False), ("unsure", None, False)],
)
def test_manual_abstract_set__one_decision__only_include_admits_the_record(
    project: Project, decision: str, reason_code: str | None, expected_member: bool
) -> None:
    log = open_log(project)

    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="kp",
        decision=decision,  # type: ignore[arg-type]
        reason_code=reason_code,
    )

    assert (JOURNAL_2020.record_id in engine.manual_abstract_set(project)) is expected_member


@pytest.mark.integration
def test_manual_abstract_set__record_outside_the_language_set__is_never_admitted(
    project: Project,
) -> None:
    log = open_log(project)

    for record in (GERMAN_JOURNAL, JOURNAL_1999, UNLISTED_CONFERENCE):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record.record_id,
            reviewer="kp",
            decision="include",
        )

    assert engine.manual_abstract_set(project) == frozenset()


@pytest.mark.integration
def test_manual_abstract_set__empty_language_set__never_reads_the_decision_log(
    project: Project,
) -> None:
    log = open_log(project)
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="kp",
        decision="include",
    )
    # A log that would raise the moment anything read it. `L` is empty under
    # these criteria, so a correct `M_abs` is empty without consulting it --
    # and this is the only way to assert the short circuit actually happens
    # rather than merely producing the same answer by accident.
    write_criteria(project, CriteriaSpec(year_start=1800, year_end=1801))
    overwrite_log_bytes(project, b'{"not":"an event"}\n')

    assert engine.manual_abstract_set(project) == frozenset()
    assert engine.corpus(project) == frozenset()
    with pytest.raises(LogError):
        log.load()


@pytest.mark.integration
def test_manual_fulltext_set__include_at_both_stages__is_the_only_way_into_the_corpus(
    project: Project,
) -> None:
    log = open_log(project)
    for record in (JOURNAL_2020, ON_SUBJECT, DOC_TYPE_AS_CODE):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record.record_id,
            reviewer="kp",
            decision="include",
        )
    log.append(
        stage=PrismaStage.FULLTEXT,
        record_id=JOURNAL_2020.record_id,
        reviewer="kp",
        decision="include",
    )
    log.append(
        stage=PrismaStage.FULLTEXT,
        record_id=ON_SUBJECT.record_id,
        reviewer="kp",
        decision="exclude",
        reason_code="INACCESSIBLE",
    )

    assert engine.manual_abstract_set(project) == {
        JOURNAL_2020.record_id,
        ON_SUBJECT.record_id,
        DOC_TYPE_AS_CODE.record_id,
    }
    assert engine.manual_fulltext_set(project) == {JOURNAL_2020.record_id}
    assert engine.corpus(project) == engine.manual_fulltext_set(project)


@pytest.mark.integration
def test_manual_fulltext_set__fulltext_include_without_an_abstract_include__is_ignored(
    project: Project,
) -> None:
    log = open_log(project)

    log.append(
        stage=PrismaStage.FULLTEXT,
        record_id=JOURNAL_2020.record_id,
        reviewer="kp",
        decision="include",
    )

    assert engine.corpus(project) == frozenset()


@pytest.mark.integration
def test_manual_fulltext_set__empty_abstract_set__is_empty(project: Project) -> None:
    log = open_log(project)
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="kp",
        decision="exclude",
        reason_code="OFF_TOPIC",
    )
    log.append(
        stage=PrismaStage.FULLTEXT,
        record_id=JOURNAL_2020.record_id,
        reviewer="kp",
        decision="include",
    )

    assert engine.manual_abstract_set(project) == frozenset()
    assert engine.manual_fulltext_set(project) == frozenset()


# ---------------------------------------------------------------------------
# Aggregating several reviewers' decisions for one record
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_aggregation__one_reviewer_excludes__outvotes_another_reviewers_include(
    project: Project,
) -> None:
    log = open_log(project)

    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="kp",
        decision="include",
    )
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="mm",
        decision="exclude",
        reason_code="OFF_TOPIC",
    )

    assert engine.manual_abstract_set(project) == frozenset()


@pytest.mark.integration
def test_aggregation__one_reviewer_unsure__keeps_the_record_out_of_the_advanced_set(
    project: Project,
) -> None:
    log = open_log(project)

    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="kp",
        decision="include",
    )
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="mm",
        decision="unsure",
    )

    assert engine.manual_abstract_set(project) == frozenset()


@pytest.mark.integration
def test_aggregation__an_exclude_logged_before_another_reviewers_include__still_wins(
    project: Project,
) -> None:
    # The *losing* decision is appended last, which is the only shape that
    # tells ADR 0008's rule ("any reviewer's exclude wins") apart from
    # "the most recent event across reviewers wins" -- the latest-wins rule
    # ADR 0008 rejects by name. Every other multi-reviewer test in this file
    # happens to log the winning decision last, so all of them accept both
    # rules; this one does not.
    log = open_log(project)

    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="kp",
        decision="exclude",
        reason_code="OFF_TOPIC",
    )
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="mm",
        decision="include",
    )

    assert engine.manual_abstract_set(project) == frozenset()


@pytest.mark.integration
def test_aggregation__an_unsure_logged_before_another_reviewers_include__still_wins(
    project: Project,
) -> None:
    # As above, for the second half of the rule: an unresolved disagreement
    # keeps the record in the queue (BUILD_PLAN line 973), and a later
    # reviewer's include does not resolve it on the earlier reviewer's behalf.
    log = open_log(project)

    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="kp",
        decision="unsure",
    )
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="mm",
        decision="include",
    )

    assert engine.manual_abstract_set(project) == frozenset()


@pytest.mark.integration
def test_aggregation__an_exclude_at_fulltext_before_another_reviewers_include__keeps_it_out(
    project: Project,
) -> None:
    # The same discriminator at the second screening stage, where the answer
    # is the published corpus itself.
    log = open_log(project)
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="kp",
        decision="include",
    )
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="mm",
        decision="include",
    )

    log.append(
        stage=PrismaStage.FULLTEXT,
        record_id=JOURNAL_2020.record_id,
        reviewer="kp",
        decision="exclude",
        reason_code="INACCESSIBLE",
    )
    log.append(
        stage=PrismaStage.FULLTEXT,
        record_id=JOURNAL_2020.record_id,
        reviewer="mm",
        decision="include",
    )

    assert engine.manual_abstract_set(project) == {JOURNAL_2020.record_id}
    assert engine.corpus(project) == frozenset()


@pytest.mark.integration
def test_aggregation__every_reviewer_includes__advances_the_record(project: Project) -> None:
    log = open_log(project)

    for reviewer in ("kp", "mm", "sh"):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=JOURNAL_2020.record_id,
            reviewer=reviewer,
            decision="include",
        )

    assert engine.manual_abstract_set(project) == {JOURNAL_2020.record_id}


# ---------------------------------------------------------------------------
# Criteria amendment (BUILD_PLAN lines 1045-1051)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_replay__widened_year_range__reports_new_records_needing_screening(
    tmp_path: Path,
) -> None:
    narrow = CriteriaSpec(version="1.0.0", year_start=2020, year_end=2026)
    project = build_project(
        tmp_path,
        CorpusSpec(records=[JOURNAL_2020, JOURNAL_1999, ON_SUBJECT], criteria=narrow),
        slug="replay-widen",
    )
    log = open_log(project)
    for record in (JOURNAL_2020, ON_SUBJECT):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record.record_id,
            reviewer="kp",
            decision="include",
        )
    write_criteria(project, CriteriaSpec(version="1.1.0", year_start=1990, year_end=2026))

    result = engine.replay(project, criteria_version="1.1.0")

    assert result.criteria_version == "1.1.0"
    assert result.newly_requires_screening == {JOURNAL_1999.record_id}
    assert result.decisions_still_valid == {JOURNAL_2020.record_id, ON_SUBJECT.record_id}
    assert result.no_longer_in_scope == frozenset()
    assert (
        result.language
        == result.automated
        == {
            JOURNAL_2020.record_id,
            JOURNAL_1999.record_id,
            ON_SUBJECT.record_id,
        }
    )


@pytest.mark.integration
def test_replay__narrowed_subject__retains_no_longer_relevant_decisions_as_history(
    tmp_path: Path,
) -> None:
    open_scope = CriteriaSpec(version="1.0.0")
    project = build_project(
        tmp_path,
        CorpusSpec(records=[ON_SUBJECT, OFF_SUBJECT], criteria=open_scope),
        slug="replay-narrow",
    )
    log = open_log(project)
    logged = [
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record.record_id,
            reviewer="kp",
            decision="include",
        )
        for record in (ON_SUBJECT, OFF_SUBJECT)
    ]
    write_criteria(project, CriteriaSpec(version="2.0.0", subject_areas=("COMP",)))

    result = engine.replay(project, criteria_version="2.0.0")

    assert result.no_longer_in_scope == {OFF_SUBJECT.record_id}
    assert result.decisions_still_valid == {ON_SUBJECT.record_id}
    assert result.newly_requires_screening == frozenset()
    assert [event.event_id for event in log.load()] == [event.event_id for event in logged]
    assert engine.corpus(project) == frozenset()


@pytest.mark.integration
def test_replay__superseded_criteria_version__is_resolved_from_git_history(
    tmp_path: Path,
) -> None:
    project = build_project(
        tmp_path,
        CorpusSpec(records=[JOURNAL_2020, JOURNAL_1999], criteria=CriteriaSpec(version="1.0.0")),
        slug="replay-history",
    )
    commit_criteria(
        project, CriteriaSpec(version="1.0.0", year_start=1990, year_end=2026), "criteria v1.0.0"
    )
    commit_criteria(
        project, CriteriaSpec(version="2.0.0", year_start=2020, year_end=2026), "criteria v2.0.0"
    )

    current = engine.replay(project, criteria_version="2.0.0")
    historical = engine.replay(project, criteria_version="1.0.0")

    assert current.language == {JOURNAL_2020.record_id}
    assert historical.language == {JOURNAL_2020.record_id, JOURNAL_1999.record_id}


@pytest.mark.integration
def test_replay__criteria_version_recorded_on_every_event__is_queryable(
    tmp_path: Path,
) -> None:
    project = build_project(
        tmp_path,
        CorpusSpec(records=[JOURNAL_2020, ON_SUBJECT], criteria=CriteriaSpec(version="1.0.0")),
        slug="replay-versions",
    )
    log = open_log(project)
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=JOURNAL_2020.record_id,
        reviewer="kp",
        decision="include",
    )
    write_criteria(project, CriteriaSpec(version="1.1.0"))
    log.append(
        stage=PrismaStage.TITLE_ABSTRACT,
        record_id=ON_SUBJECT.record_id,
        reviewer="kp",
        decision="include",
    )

    decided_under: dict[str, list[str]] = defaultdict(list)
    for event in log.load():
        decided_under[event.criteria_version].append(event.record_id)

    assert decided_under == {
        "1.0.0": [JOURNAL_2020.record_id],
        "1.1.0": [ON_SUBJECT.record_id],
    }
