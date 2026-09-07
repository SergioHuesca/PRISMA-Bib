"""Integration tests for :mod:`prismabib.taxonomy.review`'s coverage report (BUILD_PLAN §Stage 8)."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.taxonomy.coder import code
from prismabib.taxonomy.overrides import OverrideLog
from prismabib.taxonomy.review import (
    CONFIDENCE_BANDS,
    build_coverage_report,
    build_review_queue,
)
from prismabib.taxonomy.schema import load_dimensions
from tests.bibliometrics_helpers import include_everything, open_corpus
from tests.conftest import SeededIdFactory
from tests.taxonomy_helpers import (
    LEARNING_PARADIGM_RULES_V1,
    TaxonomyRecordSpec,
    build_taxonomy_project,
    load_rule_files,
    write_dimensions,
    write_rule_file,
)

_RECORDS = [
    TaxonomyRecordSpec(number=1, title="A AlphaMarker approach"),  # rule-coded supervised
    TaxonomyRecordSpec(number=2, title="A BetaMarker approach"),  # rule-coded unsupervised
    TaxonomyRecordSpec(number=3, title="Plain title, no marker at all"),  # uncoded
    TaxonomyRecordSpec(
        number=4, title="Also plain, no marker at all"
    ),  # uncoded, then human-reviewed
]


@pytest.mark.integration
@pytest.mark.acceptance("S08-AC4")
def test_coverage_report__reports_rule_vs_human_split_and_audit_agreement(tmp_path: Path) -> None:
    project = build_taxonomy_project(tmp_path, _RECORDS, slug="cov")
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)

    override_log = OverrideLog(project, id_factory=SeededIdFactory(seed=0, prefix="ov"))
    override_log.append(
        record_id=_RECORDS[3].record_id,
        dimension="learning_paradigm",
        categories=["self_supervised"],
        reviewer="kp",
        schema=schema,
    )
    overrides = override_log.load()

    result = code(corpus, [rule_file], overrides)
    report = build_coverage_report(project, result, schema, [rule_file], overrides)

    (dimension_coverage,) = report.dimensions
    assert dimension_coverage.dimension == "learning_paradigm"
    assert dimension_coverage.corpus_size == 4
    assert dimension_coverage.pct_by_rule == pytest.approx(50.0)  # 2 of 4
    assert dimension_coverage.pct_by_human == pytest.approx(25.0)  # 1 of 4
    assert dimension_coverage.pct_uncoded == pytest.approx(25.0)  # record 3
    # Record 4 was reviewed and given a category, so nothing is
    # `reviewed_none` here -- pinned rather than left unasserted, because
    # `n_uncoded` is computed by subtraction: dropping the `reviewed_none`
    # count silently rolls those records into `uncoded` and the percentages
    # still sum to 100 (ADR 0023 Decision 4b). See
    # `..._distinguishes_reviewed_none_from_uncoded` for the loaded case.
    assert dimension_coverage.pct_reviewed_none == pytest.approx(0.0)
    assert dimension_coverage.pct_coded == pytest.approx(75.0)  # rule + human
    # No audit-sampled record has been reviewed in this fixture -- "no
    # evidence yet" reports as None, not a fabricated rate.
    assert dimension_coverage.audit_agreement_rate is None


@pytest.mark.integration
def test_coverage_report__field_diagnostics__are_scoped_to_their_own_dimension(
    tmp_path: Path,
) -> None:
    from tests.taxonomy_helpers import ARCHITECTURE_RULES_V1, LEARNING_PARADIGM_RULES_INDEX_KEYWORDS

    records_with_author_keywords = [
        *_RECORDS,
        TaxonomyRecordSpec(number=5, author_keywords=("DeltaMarker",)),
    ]
    project = build_taxonomy_project(tmp_path, records_with_author_keywords, slug="cov2")
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_INDEX_KEYWORDS)
    write_rule_file(project, "architecture", ARCHITECTURE_RULES_V1)
    schema = load_dimensions(project)
    rule_files = load_rule_files(project, schema, "learning_paradigm", "architecture")
    corpus = open_corpus(project)

    result = code(corpus, rule_files, [])
    report = build_coverage_report(project, result, schema, rule_files, [])

    by_dimension = {d.dimension: d for d in report.dimensions}
    assert any(
        diag.field == "index_keywords"
        for diag in by_dimension["learning_paradigm"].field_diagnostics
    )
    assert by_dimension["architecture"].field_diagnostics == ()


#: Twenty rule-coded records, so a 10% audit sample designates two and the
#: agreement rate has a denominator a reader can reason about. Every record
#: carries `AlphaMarker`, so the rules code them all `supervised` -- the
#: reviewer's job in the test below is to agree with some and not others.
_AUDITABLE_RECORDS = [
    TaxonomyRecordSpec(number=index, title=f"A AlphaMarker approach, number {index}")
    for index in range(1, 21)
]


def _audit_workflow_project(tmp_path: Path, slug: str) -> tuple[object, object, object, object]:
    """The documented Step-7 workflow's starting state: coded, queued, nothing reviewed yet."""
    project = build_taxonomy_project(tmp_path, _AUDITABLE_RECORDS, slug=slug)
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)
    return project, schema, rule_file, corpus


@pytest.mark.integration
@pytest.mark.acceptance("S08-AC4")
@pytest.mark.parametrize(
    ("n_agreeing", "n_disagreeing", "expected_rate"),
    [(2, 0, 1.0), (0, 2, 0.0), (1, 1, 0.5)],
    ids=["all-agree", "all-disagree", "half"],
)
def test_audit__documented_workflow__yields_a_real_rate(
    tmp_path: Path, n_agreeing: int, n_disagreeing: int, expected_rate: float
) -> None:
    """Walk `docs/how-to/write-taxonomy-rules.md` Step 7 end to end and get a number.

    The regression test for ADR 0023 Decision 5b. Before it, this workflow
    could not produce a rate at all: the queue excluded reviewed pairs from
    the audit pool, so every sampled record was by construction one with no
    override, and the rate returned `None` forever, for every corpus.

    The parametrisation is the point. A test asserting only "not None"
    would pass against a rate hardcoded to any constant; three different
    reviewer behaviours mapping to three different rates pin that the number
    means what its name says. And the all-agree row is the one that catches
    the second half of the defect -- comparing an override against the rule
    verdict it *replaced* rather than against the empty set the coder leaves
    behind, which scored unanimous agreement as 0.0.
    """
    project, schema, rule_file, corpus = _audit_workflow_project(tmp_path, slug=f"aud{n_agreeing}")
    override_log = OverrideLog(project, id_factory=SeededIdFactory(seed=0, prefix="ov"))

    first_pass = code(corpus, [rule_file], [])
    queue = build_review_queue(first_pass, schema, [rule_file], project_slug=project.slug)
    designated = queue.audit_samples["learning_paradigm"]
    assert len(designated) == n_agreeing + n_disagreeing, "fixture must designate exactly 2"

    for record_id in designated[:n_agreeing]:
        override_log.append(
            record_id=record_id,
            dimension="learning_paradigm",
            categories=("supervised",),  # what the rules said
            reviewer="alice",
            reason="agrees with the rule",
            schema=schema,
        )
    for record_id in designated[n_agreeing:]:
        override_log.append(
            record_id=record_id,
            dimension="learning_paradigm",
            categories=("unsupervised",),  # the rules said `supervised`
            reviewer="alice",
            reason="the marker is a baseline comparison",
            schema=schema,
        )

    overrides = override_log.load()
    second_pass = code(corpus, [rule_file], overrides)
    report = build_coverage_report(project, second_pass, schema, [rule_file], overrides)

    (coverage,) = report.dimensions
    assert coverage.audit_agreement_rate == pytest.approx(expected_rate)


@pytest.mark.integration
def test_audit__sample__is_stable_as_reviewing_proceeds(tmp_path: Path) -> None:
    """The designated set does not move when its members are reviewed (ADR 0023 Decision 5b).

    The mechanism behind the permanent `None`: reviewing a sampled record
    used to remove it from the pool, so the next run re-drew a *different*
    sample and the rate never had anything to measure. Asserting the set is
    identical across the two passes is what makes that non-recurrable.
    """
    project, schema, rule_file, corpus = _audit_workflow_project(tmp_path, slug="stable")
    override_log = OverrideLog(project, id_factory=SeededIdFactory(seed=0, prefix="ov"))

    before = build_review_queue(
        code(corpus, [rule_file], []), schema, [rule_file], project_slug=project.slug
    ).audit_samples["learning_paradigm"]

    for record_id in before:
        override_log.append(
            record_id=record_id,
            dimension="learning_paradigm",
            categories=("supervised",),
            reviewer="alice",
            reason="reviewed",
            schema=schema,
        )
    overrides = override_log.load()
    after_queue = build_review_queue(
        code(corpus, [rule_file], overrides), schema, [rule_file], project_slug=project.slug
    )

    assert after_queue.audit_samples["learning_paradigm"] == before
    # ...and every one of them has dropped out of the *work* list, which is
    # the distinction the two fields exist to draw.
    outstanding = [entry for entry in after_queue.entries if entry.priority == "audit_sample"]
    assert outstanding == []


@pytest.mark.integration
def test_coverage_report__distinguishes_reviewed_none_from_uncoded(tmp_path: Path) -> None:
    """A human's "no category applies" is not "nobody looked" (ADR 0023 Decision 4b).

    `n_uncoded` is computed as `corpus_size - n_rule - n_human -
    n_reviewed_none`, so failing to count the third bucket does not produce
    a wrong total -- it produces a *right* total with human verdicts
    relabelled as absent evidence. Nothing caught that: dropping the count
    left all 83 taxonomy tests green.

    Two records that differ only in whether a human looked, so the two
    percentages cannot both be right unless the distinction survives.
    """
    records = [
        TaxonomyRecordSpec(number=1, title="Plain, reviewed as nothing"),
        TaxonomyRecordSpec(number=2, title="Plain, nobody looked"),
    ]
    project = build_taxonomy_project(tmp_path, records, slug="rn")
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)
    log = OverrideLog(project, id_factory=SeededIdFactory(seed=0, prefix="ov"))
    reviewed_id = min(code(corpus, [rule_file], []).record_ids)
    log.append(
        record_id=reviewed_id,
        dimension="learning_paradigm",
        categories=(),
        reviewer="alice",
        reason="read it; no category applies",
        schema=schema,
    )

    overrides = log.load()
    report = build_coverage_report(
        project, code(corpus, [rule_file], overrides), schema, [rule_file], overrides
    )

    (coverage,) = report.dimensions
    assert coverage.pct_reviewed_none == pytest.approx(50.0)
    assert coverage.pct_uncoded == pytest.approx(50.0)
    assert coverage.pct_coded == pytest.approx(0.0)


@pytest.mark.integration
def test_audit__agreement__is_reported_per_confidence_band(tmp_path: Path) -> None:
    """`confidence` must affect something (ADR 0023 Decision 5c).

    Before this it was a field a rule author could set to anything --
    including `0.0` -- that no code path consumed, while the single
    corpus-wide rate averaged a 0.6 rule and a 0.95 rule together. That
    number moves whenever the *mix* of categories in a corpus moves, with no
    rule having changed, which is not what "audit agreement rate" is read to
    mean in a methods section.

    The fixture makes the bands disagree with each other on purpose: the
    reviewer agrees with every high-confidence assignment and with none of
    the low-confidence ones. A single rate would report 0.5 and hide
    precisely the thing worth knowing -- that the weak rule is the one
    failing.
    """
    project, schema, rule_file, corpus = _audit_workflow_project(tmp_path, slug="bands")
    override_log = OverrideLog(project, id_factory=SeededIdFactory(seed=0, prefix="ov"))
    first_pass = code(corpus, [rule_file], [])
    queue = build_review_queue(first_pass, schema, [rule_file], project_slug=project.slug)
    designated = queue.audit_samples["learning_paradigm"]

    for record_id in designated:
        override_log.append(
            record_id=record_id,
            dimension="learning_paradigm",
            categories=("supervised",),
            reviewer="alice",
            reason="agrees",
            schema=schema,
        )

    overrides = override_log.load()
    report = build_coverage_report(
        project, code(corpus, [rule_file], overrides), schema, [rule_file], overrides
    )

    (coverage,) = report.dimensions
    bands = coverage.audit_agreement_by_band
    # Every band label is always present, so a caption can state "no
    # evidence in this band" rather than omitting it and implying none was
    # sought.
    assert set(bands) == {label for label, _, _ in CONFIDENCE_BANDS}
    # `supervised` is declared at 0.9 in the test rules, so every judged
    # record lands in the high band and the others report None -- not 0.0,
    # which would read as "audited and disagreed".
    assert bands["high (>=0.85)"] == pytest.approx(1.0)
    assert bands["medium (0.7-0.85)"] is None
    assert bands["low (<0.7)"] is None
