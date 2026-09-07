"""Unit tests for :mod:`prismabib.taxonomy.coder`'s engine behaviour (BUILD_PLAN §Stage 8).

Every assertion is about the *engine* -- fold precedence, provenance, the
``none``-clause suppression mechanism, counting-unit arithmetic -- never
about whether a particular pattern matches a particular string (ADR 0023
Decision 7).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from prismabib.errors import ValidationError
from prismabib.taxonomy.coder import CountingUnit, code, distribution
from prismabib.taxonomy.overrides import OverrideEvent, OverrideLog
from prismabib.taxonomy.schema import load_dimensions
from tests.bibliometrics_helpers import include_everything, open_corpus
from tests.conftest import SeededIdFactory
from tests.taxonomy_helpers import (
    ARCHITECTURE_RULES_V1,
    LEARNING_PARADIGM_RULES_OVER_ASSIGNED,
    LEARNING_PARADIGM_RULES_V1,
    TEST_DIMENSIONS_YAML,
    TaxonomyRecordSpec,
    build_taxonomy_project,
    load_rule_files,
    write_dimensions,
    write_rule_file,
)


def _override(
    record_id: str, dimension: str, categories: tuple[str, ...], *, reviewer: str = "kp"
) -> OverrideEvent:
    return OverrideEvent(
        event_id=f"ov-{record_id}-{dimension}",
        ts=datetime(2026, 1, 1, tzinfo=UTC),
        project="p",
        record_id=record_id,
        dimension=dimension,
        categories=categories,
        reviewer=reviewer,
    )


@pytest.fixture
def coded_project(tmp_path: Path):
    """A four-record project, coded against ``learning_paradigm`` and ``architecture``."""
    records = [
        TaxonomyRecordSpec(number=1, title="A AlphaMarker approach"),
        TaxonomyRecordSpec(
            number=2, title="An BetaMarker approach", author_keywords=("DeltaMarker",)
        ),
        TaxonomyRecordSpec(
            number=3,
            title="A GammaMarker approach",
            author_keywords=("DeltaMarker", "EpsilonMarker"),
        ),
        TaxonomyRecordSpec(number=4, title="Plain title, no marker at all"),
    ]
    project = build_taxonomy_project(tmp_path, records)
    include_everything(project)
    write_dimensions(project, TEST_DIMENSIONS_YAML)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    write_rule_file(project, "architecture", ARCHITECTURE_RULES_V1)
    schema = load_dimensions(project)
    rule_files = load_rule_files(project, schema, "learning_paradigm", "architecture")
    corpus = open_corpus(project)
    return project, schema, rule_files, corpus, records


@pytest.mark.unit
def test_coder__none_clause__suppresses_a_would_be_match(tmp_path: Path) -> None:
    records = [
        TaxonomyRecordSpec(
            number=1,
            title="A SupervisedMarker approach",
            abstract="SupervisedMarkerExcluded, this is actually noise",
        ),
        TaxonomyRecordSpec(number=2, title="A SupervisedMarker approach, no exclusion"),
    ]
    project = build_taxonomy_project(tmp_path, records)
    include_everything(project)
    write_dimensions(project)
    write_rule_file(
        project,
        "learning_paradigm",
        """\
version: 1.0.0
dimension: learning_paradigm
counting_unit: papers
categories:
  - id: supervised
    any:
      - {field: title, pattern: 'SupervisedMarker'}
    none:
      - {field: abstract, pattern: 'SupervisedMarkerExcluded'}
""",
    )
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)

    result = code(corpus, [rule_file], [])
    coded_record_ids = {a.record_id for a in result.assignments if a.category == "supervised"}

    assert coded_record_ids == {records[1].record_id}


@pytest.mark.unit
def test_coder__assignment__records_rule_id_and_rule_version(coded_project) -> None:
    _project, _schema, rule_files, corpus, records = coded_project

    result = code(corpus, rule_files, [])

    supervised = next(
        a
        for a in result.assignments
        if a.record_id == records[0].record_id and a.dimension == "learning_paradigm"
    )
    assert supervised.source == "rule"
    assert supervised.rule_id == "supervised"
    assert supervised.rule_version == "1.0.0"
    assert supervised.confidence == 1.0


@pytest.mark.unit
def test_override__beats_rule_assignment(coded_project) -> None:
    _project, _schema, rule_files, corpus, records = coded_project
    record_id = records[0].record_id  # rule-coded "supervised"
    override = _override(record_id, "learning_paradigm", ("unsupervised",))

    result = code(corpus, rule_files, [override])

    effective = result.effective_categories("learning_paradigm")
    assert effective[record_id] == ("unsupervised",)


@pytest.mark.unit
def test_override__empty_categories__replaces_rule_output_with_nothing(coded_project) -> None:
    _project, _schema, rule_files, corpus, records = coded_project
    record_id = records[0].record_id  # rule-coded "supervised"
    override = _override(record_id, "learning_paradigm", ())

    result = code(corpus, rule_files, [override])

    assert result.effective_categories("learning_paradigm")[record_id] == ()
    assert record_id not in result.uncoded("learning_paradigm")
    assert (record_id, "learning_paradigm") in result.human_reviewed


@pytest.mark.unit
def test_coder__uncoded_record__has_no_effective_category_and_is_not_human_reviewed(
    coded_project,
) -> None:
    _project, _schema, rule_files, corpus, records = coded_project
    uncoded_record_id = records[3].record_id  # "Plain title, no marker at all"

    result = code(corpus, rule_files, [])

    assert result.effective_categories("learning_paradigm")[uncoded_record_id] == ()
    assert uncoded_record_id in result.uncoded("learning_paradigm")


@pytest.mark.unit
def test_coder__multi_label_dimension__a_record_may_carry_more_than_one_category(
    coded_project,
) -> None:
    _project, _schema, rule_files, corpus, records = coded_project
    record_id = records[2].record_id  # author_keywords carry both DeltaMarker and EpsilonMarker

    result = code(corpus, rule_files, [])

    assert set(result.effective_categories("architecture")[record_id]) == {"cnn", "transformer"}


@pytest.mark.unit
def test_counting_unit__assignments_mode__may_exceed_n_without_error(coded_project) -> None:
    _project, schema, rule_files, corpus, _records = coded_project

    result = code(corpus, rule_files, [])
    dist = distribution(result, schema, "architecture", CountingUnit.ASSIGNMENTS)

    # Record 3 alone contributes two assignments (cnn + transformer) to a
    # four-record corpus with only two records coded at all -- the sum
    # legitimately exceeds neither category count nor the corpus size being
    # an error, which is exactly what ASSIGNMENTS declares.
    assert sum(dist.counts[c] for c in ("cnn", "transformer", "gan")) == 3
    assert dist.corpus_size == 4


@pytest.mark.unit
def test_counting_unit__over_assigned_fixture__assertion_fails_loudly(tmp_path: Path) -> None:
    records = [TaxonomyRecordSpec(number=1, title="A record with an AmbiguousMarker inside")]
    project = build_taxonomy_project(tmp_path, records)
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_OVER_ASSIGNED)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)

    result = code(corpus, [rule_file], [])

    with pytest.raises(ValidationError, match="multi_label=false with counting_unit=papers"):
        distribution(result, schema, "learning_paradigm", CountingUnit.PAPERS)


@pytest.mark.unit
def test_counting_unit__over_assigned_fixture__assertion_is_reachable_when_not_corrupted(
    coded_project,
) -> None:
    """Guard the guard: the assertion above must not fire on ordinary, non-corrupted data.

    Without this, ``test_counting_unit__over_assigned_fixture__assertion_fails_loudly``
    could pass because :func:`distribution` always raises, which would prove
    nothing about detecting over-assignment specifically.
    """
    _project, schema, rule_files, corpus, _records = coded_project

    result = code(corpus, rule_files, [])

    dist = distribution(result, schema, "learning_paradigm", CountingUnit.PAPERS)
    assert sum(dist.counts.values()) == dist.corpus_size


@pytest.mark.unit
def test_review_and_coder__stage_included_but_empty_corpus__codes_nothing_and_does_not_raise(
    tmp_path: Path,
) -> None:
    """The live corpus's actual situation: |C| is empty because screening has not run yet."""
    records = [TaxonomyRecordSpec(number=1, title="A AlphaMarker approach")]
    project = build_taxonomy_project(tmp_path, records)
    # Deliberately not calling include_everything(): nothing is screened, so
    # PrismaStage.INCLUDED is empty (ADR 0023's own live-corpus example).
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)

    result = code(corpus, [rule_file], [])
    dist = distribution(result, schema, "learning_paradigm", CountingUnit.PAPERS)

    assert (result.record_ids, result.assignments) == ((), ())
    assert dist.corpus_size == 0
    assert sum(dist.counts.values()) == 0


@pytest.mark.unit
def test_coder__field_with_no_content_anywhere__is_reported_as_a_diagnostic_not_an_error(
    tmp_path: Path,
) -> None:
    """ADR 0023 Decision 6: a rule referencing a corpus-empty field is valid, and flagged.

    ``index_keywords`` is always empty on a corpus loaded from the Scopus
    Search API (``store/load.py``'s own module docstring) -- the live
    corpus's exact situation.
    """
    from tests.taxonomy_helpers import LEARNING_PARADIGM_RULES_INDEX_KEYWORDS

    records = [TaxonomyRecordSpec(number=1, title="An BetaMarker record")]
    project = build_taxonomy_project(tmp_path, records)
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_INDEX_KEYWORDS)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)

    result = code(corpus, [rule_file], [])  # must not raise

    assert any(d.field == "index_keywords" for d in result.field_diagnostics)


@pytest.mark.unit
def test_distribution__three_buckets__are_never_merged(tmp_path: Path) -> None:
    """`reviewed_none` and `uncoded` are different facts and must stay different integers.

    ADR 0023 Decision 4b. Nothing asserted this before, and collapsing the
    two left every taxonomy test green -- which is the sharp edge here: the
    total still equals `|C|`, so nothing looks wrong, while a human's "I
    read it and no category applies" has been relabelled as "nobody
    looked". A plausible number, which is the failure mode this project
    exists to prevent, rather than an arithmetic one a guard would catch.

    Four records, one per state, so no two buckets can be swapped without
    changing a value: a rule-coded one, a human-assigned one, a
    human-reviewed-as-nothing one, and one nobody has touched.
    """
    records = [
        TaxonomyRecordSpec(number=1, title="A AlphaMarker approach"),  # rule -> supervised
        TaxonomyRecordSpec(number=2, title="Plain, no marker"),  # human -> unsupervised
        TaxonomyRecordSpec(number=3, title="Plain, no marker either"),  # human -> ()
        TaxonomyRecordSpec(number=4, title="Plain, untouched"),  # uncoded
    ]
    project = build_taxonomy_project(tmp_path, records, slug="three")
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)
    log = OverrideLog(project, id_factory=SeededIdFactory(seed=0, prefix="ov"))
    ids = sorted(code(corpus, [rule_file], []).record_ids)
    log.append(
        record_id=ids[1],
        dimension="learning_paradigm",
        categories=("unsupervised",),
        reviewer="alice",
        reason="human assignment",
        schema=schema,
    )
    log.append(
        record_id=ids[2],
        dimension="learning_paradigm",
        categories=(),
        reviewer="alice",
        reason="read it; no category applies",
        schema=schema,
    )

    result = code(corpus, [rule_file], log.load())
    counts = distribution(result, schema, rule_file.dimension, rule_file.counting_unit).counts

    assert counts["supervised"] == 1
    assert counts["unsupervised"] == 1
    assert counts["reviewed_none"] == 1
    assert counts["uncoded"] == 1
    # ...and the whole point of the three-way split: the sum still closes.
    assert sum(counts.values()) == len(result.record_ids) == 4


@pytest.mark.unit
@pytest.mark.parametrize(
    ("planted", "why"),
    [
        ("uncoded", "a log written before `uncoded` became a reserved name"),
        ("category_removed_from_schema", "a category dropped from dimensions.yaml since"),
    ],
    ids=["reserved-bucket-name", "undeclared-category"],
)
def test_distribution__override_naming_an_undeclared_category__raises(
    tmp_path: Path, planted: str, why: str
) -> None:
    """A category the schema does not declare must not be counted into existence.

    `OverrideLog.append` validates against the schema and the rule loader
    validates against it too, but `OverrideLog.load()` deliberately does
    not -- an append-only log is a historical record, and re-validating on
    read would let a schema edit retroactively corrupt a reviewer's past
    verdict. So a `counts.get(category, 0) + 1` here silently invented a
    bucket outside the closed set.

    The reserved-name row is not hypothetical: this release makes `uncoded`
    a reserved name, so any log written before it is exactly this case. And
    it was the worst version of it -- the count landed in the real `uncoded`
    bucket, the sum still equalled `|C|` so the counting guard saw nothing,
    and `build_coverage_report` reported the same record as human-coded.
    Two views of one record, contradictory, no error.

    The events are constructed directly rather than appended through the
    log, because `append` is the path that (correctly) refuses them.
    """
    records = [TaxonomyRecordSpec(number=1, title="Plain, no marker")]
    project = build_taxonomy_project(tmp_path, records, slug=f"und{len(planted)}")
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)
    record_id = min(code(corpus, [rule_file], []).record_ids)

    legacy = OverrideEvent(
        event_id="ov0000000000000000000000001",
        project=project.slug,
        record_id=record_id,
        dimension="learning_paradigm",
        categories=(planted,),
        reviewer="alice",
        reason=why,
        ts=datetime(2026, 1, 1, tzinfo=UTC),
    )
    result = code(corpus, [rule_file], [legacy])

    with pytest.raises(ValidationError, match="which the schema does not declare"):
        distribution(result, schema, rule_file.dimension, rule_file.counting_unit)
