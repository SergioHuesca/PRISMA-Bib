"""Unit tests for :mod:`prismabib.taxonomy.review` (BUILD_PLAN §Stage 8).

Queue ordering and audit-sample determinism are asserted against the
engine's behaviour, never against a particular rule matching a particular
string (ADR 0023 Decision 7).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prismabib.stage import PrismaStage
from prismabib.taxonomy.coder import CodingResult
from prismabib.taxonomy.overrides import OverrideEvent, fold_override_events
from prismabib.taxonomy.review import (
    ReviewQueue,
    audit_agreement_rate,
    audit_sample_seed,
    build_review_queue,
)
from prismabib.taxonomy.rules import load_rule_file
from prismabib.taxonomy.schema import TaxonomySchema

_SCHEMA = TaxonomySchema.model_validate(
    {
        "dimensions": [
            {
                "id": "learning_paradigm",
                "multi_label": False,
                "categories": ["supervised", "unsupervised", "self_supervised"],
            }
        ]
    }
)


def _rule_file(tmp_path, version: str = "1.0.0"):
    path = tmp_path / "learning_paradigm.yaml"
    path.write_text(
        f"""\
version: {version}
dimension: learning_paradigm
counting_unit: papers
categories:
  - id: supervised
    any:
      - {{field: title, pattern: 'x'}}
  - id: unsupervised
    any:
      - {{field: title, pattern: 'y'}}
  - id: self_supervised
    any:
      - {{field: title, pattern: 'z'}}
""",
        encoding="utf-8",
    )
    return load_rule_file(path, schema=_SCHEMA)


@pytest.mark.unit
def test_review_queue__uncoded_record__is_priority_one(tmp_path) -> None:
    rule_file = _rule_file(tmp_path)
    result = CodingResult(
        stage=PrismaStage.INCLUDED,
        record_ids=("scopus:1", "scopus:2"),
        assignments=(),  # neither record has any rule-fired category
        field_diagnostics=(),
        human_reviewed=frozenset(),
    )

    queue = build_review_queue(result, _SCHEMA, [rule_file], project_slug="p")

    assert [entry.priority for entry in queue.entries][:2] == ["uncoded", "uncoded"]
    assert {entry.record_id for entry in queue.entries if entry.priority == "uncoded"} == {
        "scopus:1",
        "scopus:2",
    }


@pytest.mark.unit
def test_review_queue__conflicting_single_label__is_priority_two(tmp_path) -> None:
    from prismabib.taxonomy.coder import Assignment

    rule_file = _rule_file(tmp_path)
    result = CodingResult(
        stage=PrismaStage.INCLUDED,
        record_ids=("scopus:1",),
        assignments=(
            Assignment(
                "scopus:1", "learning_paradigm", "supervised", "supervised", "1.0.0", 1.0, "rule"
            ),
            Assignment(
                "scopus:1",
                "learning_paradigm",
                "unsupervised",
                "unsupervised",
                "1.0.0",
                1.0,
                "rule",
            ),
        ),
        field_diagnostics=(),
        human_reviewed=frozenset(),
    )

    queue = build_review_queue(result, _SCHEMA, [rule_file], project_slug="p")

    assert [entry.priority for entry in queue.entries] == ["conflicting"]
    assert set(queue.entries[0].rule_categories) == {"supervised", "unsupervised"}


@pytest.mark.unit
def test_review_queue__priority_order__uncoded_before_conflicting_before_audit_sample(
    tmp_path,
) -> None:
    from prismabib.taxonomy.coder import Assignment

    rule_file = _rule_file(tmp_path)
    result = CodingResult(
        stage=PrismaStage.INCLUDED,
        record_ids=("scopus:uncoded", "scopus:conflict", "scopus:clean"),
        assignments=(
            Assignment(
                "scopus:conflict",
                "learning_paradigm",
                "supervised",
                "supervised",
                "1.0.0",
                1.0,
                "rule",
            ),
            Assignment(
                "scopus:conflict",
                "learning_paradigm",
                "unsupervised",
                "unsupervised",
                "1.0.0",
                1.0,
                "rule",
            ),
            Assignment(
                "scopus:clean",
                "learning_paradigm",
                "supervised",
                "supervised",
                "1.0.0",
                1.0,
                "rule",
            ),
        ),
        field_diagnostics=(),
        human_reviewed=frozenset(),
    )

    queue = build_review_queue(result, _SCHEMA, [rule_file], project_slug="p", audit_fraction=1.0)

    priorities = [entry.priority for entry in queue.entries]
    assert (
        priorities.index("uncoded")
        < priorities.index("conflicting")
        < priorities.index("audit_sample")
    )


@pytest.mark.unit
def test_review_queue__already_human_reviewed__is_never_queued(tmp_path) -> None:
    rule_file = _rule_file(tmp_path)
    result = CodingResult(
        stage=PrismaStage.INCLUDED,
        record_ids=("scopus:1",),
        assignments=(),
        field_diagnostics=(),
        human_reviewed=frozenset({("scopus:1", "learning_paradigm")}),
    )

    queue = build_review_queue(result, _SCHEMA, [rule_file], project_slug="p")

    assert queue.entries == ()


@pytest.mark.unit
def test_review_queue__audit_sample__is_10pct_and_reproducible_from_seed(tmp_path) -> None:
    from prismabib.taxonomy.coder import Assignment

    rule_file = _rule_file(tmp_path)
    record_ids = tuple(f"scopus:{i}" for i in range(100))
    assignments = tuple(
        Assignment(record_id, "learning_paradigm", "supervised", "supervised", "1.0.0", 1.0, "rule")
        for record_id in record_ids
    )
    result = CodingResult(
        stage=PrismaStage.INCLUDED,
        record_ids=record_ids,
        assignments=assignments,
        field_diagnostics=(),
        human_reviewed=frozenset(),
    )

    queue_a = build_review_queue(result, _SCHEMA, [rule_file], project_slug="proj")
    queue_b = build_review_queue(result, _SCHEMA, [rule_file], project_slug="proj")

    audit_entries_a = [e for e in queue_a.entries if e.priority == "audit_sample"]
    assert len(audit_entries_a) == 10  # 10% of 100
    assert queue_a == queue_b  # reproducible: same input, same seed, same sample
    assert queue_a.audit_seeds["learning_paradigm"] == audit_sample_seed(
        project_slug="proj",
        dimension_id="learning_paradigm",
        rule_version="1.0.0",
        record_ids=record_ids,
    )


@pytest.mark.unit
def test_review_queue__audit_sample__changes_when_rule_version_changes(tmp_path) -> None:
    """ADR 0023 Decision 5: the seed changes when what is being audited changes."""
    from prismabib.taxonomy.coder import Assignment

    rule_file_v1 = _rule_file(tmp_path, version="1.0.0")
    rule_file_v2 = _rule_file(tmp_path, version="2.0.0")
    record_ids = tuple(f"scopus:{i}" for i in range(50))
    assignments = tuple(
        Assignment(record_id, "learning_paradigm", "supervised", "supervised", "1.0.0", 1.0, "rule")
        for record_id in record_ids
    )
    result = CodingResult(
        stage=PrismaStage.INCLUDED,
        record_ids=record_ids,
        assignments=assignments,
        field_diagnostics=(),
        human_reviewed=frozenset(),
    )

    queue_v1 = build_review_queue(result, _SCHEMA, [rule_file_v1], project_slug="proj")
    queue_v2 = build_review_queue(result, _SCHEMA, [rule_file_v2], project_slug="proj")

    assert queue_v1.audit_seeds["learning_paradigm"] != queue_v2.audit_seeds["learning_paradigm"]


@pytest.mark.unit
def test_audit_sample_seed__fixture_that_would_fail_on_a_constant_seed(tmp_path) -> None:
    """A fixture strong enough to fail if the seed were ever hardcoded to a constant.

    Two different record-id sets must resolve to different seeds; if this
    function ever regressed to ``return 0`` (or any other constant), this
    is the assertion that would catch it.
    """
    seed_a = audit_sample_seed(
        project_slug="p",
        dimension_id="d",
        rule_version="1.0.0",
        record_ids=("scopus:1", "scopus:2"),
    )
    seed_b = audit_sample_seed(
        project_slug="p",
        dimension_id="d",
        rule_version="1.0.0",
        record_ids=("scopus:1", "scopus:3"),
    )

    assert seed_a != seed_b


@pytest.mark.unit
def test_audit__disagreement_rate__is_computed_not_assumed(tmp_path) -> None:
    """The agreement rate is derived from override events, never a value handed in directly."""
    from prismabib.taxonomy.coder import Assignment

    rule_file = _rule_file(tmp_path)
    record_ids = tuple(f"scopus:{i}" for i in range(10))
    assignments = tuple(
        Assignment(record_id, "learning_paradigm", "supervised", "supervised", "1.0.0", 1.0, "rule")
        for record_id in record_ids
    )
    result = CodingResult(
        stage=PrismaStage.INCLUDED,
        record_ids=record_ids,
        assignments=assignments,
        field_diagnostics=(),
        human_reviewed=frozenset(),
    )
    queue = build_review_queue(result, _SCHEMA, [rule_file], project_slug="p", audit_fraction=1.0)
    audited_ids = [e.record_id for e in queue.entries if e.priority == "audit_sample"]
    assert audited_ids, "the fixture must actually populate the audit sample"

    def override_event(record_id: str, categories: tuple[str, ...]) -> OverrideEvent:
        return OverrideEvent(
            event_id=f"ov-{record_id}",
            ts=datetime(2026, 1, 1, tzinfo=UTC),
            project="p",
            record_id=record_id,
            dimension="learning_paradigm",
            categories=categories,
            reviewer="kp",
        )

    # Half agree with the rule ("supervised"), half disagree ("unsupervised").
    midpoint = len(audited_ids) // 2 or 1
    agreeing = [override_event(rid, ("supervised",)) for rid in audited_ids[:midpoint]]
    disagreeing = [override_event(rid, ("unsupervised",)) for rid in audited_ids[midpoint:]]
    overrides_fold = fold_override_events(agreeing + disagreeing)

    rate = audit_agreement_rate(result, overrides_fold, queue, "learning_paradigm")

    expected = len(agreeing) / len(audited_ids)
    assert rate == pytest.approx(expected)


@pytest.mark.unit
def test_audit_agreement_rate__no_reviewed_audit_records__is_none_not_zero(tmp_path) -> None:
    """No evidence yet must not be reported as '0% agreement'."""
    from prismabib.taxonomy.coder import Assignment

    rule_file = _rule_file(tmp_path)
    record_ids = ("scopus:1",)
    result = CodingResult(
        stage=PrismaStage.INCLUDED,
        record_ids=record_ids,
        assignments=(
            Assignment(
                "scopus:1", "learning_paradigm", "supervised", "supervised", "1.0.0", 1.0, "rule"
            ),
        ),
        field_diagnostics=(),
        human_reviewed=frozenset(),
    )
    queue = build_review_queue(result, _SCHEMA, [rule_file], project_slug="p", audit_fraction=1.0)

    rate = audit_agreement_rate(result, {}, queue, "learning_paradigm")

    assert rate is None


@pytest.mark.unit
def test_review_queue__empty_input__is_the_empty_queue(tmp_path) -> None:
    """A fixture that would fail loudly if ``build_review_queue`` crashed on nothing to queue."""
    rule_file = _rule_file(tmp_path)
    result = CodingResult(
        stage=PrismaStage.INCLUDED,
        record_ids=(),
        assignments=(),
        field_diagnostics=(),
        human_reviewed=frozenset(),
    )

    queue = build_review_queue(result, _SCHEMA, [rule_file], project_slug="p")

    assert queue == ReviewQueue(
        entries=(),
        audit_seeds={"learning_paradigm": queue.audit_seeds["learning_paradigm"]},
        audit_samples={"learning_paradigm": ()},
    )
