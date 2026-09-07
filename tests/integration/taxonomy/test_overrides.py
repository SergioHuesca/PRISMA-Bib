"""Integration tests for :mod:`prismabib.taxonomy.overrides` (BUILD_PLAN §Stage 8).

Real files, real ``flock``, real checksums -- nothing here is mocked, and in
particular nothing patches a ``prismabib.*`` symbol (§3.7.3 rule 1), matching
``tests/integration/prisma/test_log.py``'s own discipline.
"""

from __future__ import annotations

import itertools
from pathlib import Path

import pytest

from prismabib.errors import LogError, ValidationError
from prismabib.project import Project
from prismabib.taxonomy.overrides import OverrideEvent, OverrideLog, fold_override_events
from prismabib.taxonomy.schema import TaxonomySchema
from tests.append_only_log_conformance import (
    LogUnderTest,
    append_only_log__append__is_fsynced_and_checksummed,
    append_only_log__duplicate_event_id_inside_the_file__raises,
    append_only_log__hand_edited_file__raises_log_error_on_load,
    append_only_log__is_appended_not_edited,
    append_only_log__truncated_final_line__raises_with_line_number,
    append_only_log__unknown_schema_version__raises,
)
from tests.bibliometrics_helpers import BibCorpusSpec, BibRecordSpec, build_bib_project
from tests.conftest import SeededIdFactory
from tests.taxonomy_helpers import TEST_DIMENSIONS_YAML, write_dimensions


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """A project with a dimension schema, no rule files, no corpus needed for these tests."""
    project = build_bib_project(
        tmp_path, BibCorpusSpec(records=[BibRecordSpec(number=1)]), slug="ov"
    )
    write_dimensions(project, TEST_DIMENSIONS_YAML)
    return project


@pytest.fixture
def schema(project: Project) -> TaxonomySchema:
    from prismabib.taxonomy.schema import load_dimensions

    return load_dimensions(project)


def open_override_log(project: Project, *, prefix: str = "ov") -> OverrideLog:
    """An :class:`OverrideLog` with a seeded, deterministic id factory."""
    return OverrideLog(project, id_factory=SeededIdFactory(seed=0, prefix=prefix))


def as_persisted(event: OverrideEvent) -> OverrideEvent:
    """The event as :meth:`OverrideLog.load` will read it back.

    ``OverrideEvent`` serialises ``ts`` at millisecond precision while
    :meth:`OverrideLog.append` stamps it from ``datetime.now(UTC)`` at
    microsecond precision -- the identical asymmetry
    ``tests/integration/prisma/test_log.py::as_persisted`` exists for, on
    the sibling event type.
    """
    return OverrideEvent.model_validate_json(event.model_dump_json())


# ---------------------------------------------------------------------------
# The shared append-only conformance suite (see tests/append_only_log_conformance.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def override_log_under_test(project: Project, schema: TaxonomySchema) -> LogUnderTest:
    """Adapt :class:`OverrideLog` to the shared append-only conformance suite."""
    log = open_override_log(project)
    counter = itertools.count()

    def append_one() -> OverrideEvent:
        index = next(counter)
        return log.append(
            record_id=f"scopus:shared-suite-{index}",
            dimension="learning_paradigm",
            categories=["supervised"],
            reviewer=f"reviewer-{index}",
            schema=schema,
        )

    return LogUnderTest(
        path=log.path,
        append_one=append_one,
        load=log.load,
        event_id_of=lambda event: event.event_id,
    )


@pytest.mark.integration
def test_override_log__shared_suite__append_is_fsynced_and_checksummed(
    override_log_under_test: LogUnderTest,
) -> None:
    append_only_log__append__is_fsynced_and_checksummed(override_log_under_test)


@pytest.mark.integration
def test_override__is_appended_not_edited(override_log_under_test: LogUnderTest) -> None:
    """Claims BUILD_PLAN's own test name: same append-only guarantee as the decision log.

    Reuses the Stage 4 log tests via the shared parameterised suite in
    ``tests/append_only_log_conformance.py`` -- see
    ``tests/integration/prisma/test_log.py::test_log__shared_suite__is_appended_not_edited``
    for the identical check run against :class:`~prismabib.prisma.log.DecisionLog`.
    """
    append_only_log__is_appended_not_edited(override_log_under_test)


@pytest.mark.integration
def test_override_log__shared_suite__hand_edited_file__raises_log_error_on_load(
    override_log_under_test: LogUnderTest,
) -> None:
    append_only_log__hand_edited_file__raises_log_error_on_load(override_log_under_test)


@pytest.mark.integration
def test_override_log__shared_suite__truncated_final_line__raises_with_line_number(
    override_log_under_test: LogUnderTest,
) -> None:
    append_only_log__truncated_final_line__raises_with_line_number(override_log_under_test)


@pytest.mark.integration
def test_override_log__shared_suite__duplicate_event_id_inside_the_file__raises(
    override_log_under_test: LogUnderTest,
) -> None:
    append_only_log__duplicate_event_id_inside_the_file__raises(override_log_under_test)


@pytest.mark.integration
def test_override_log__shared_suite__unknown_schema_version__raises(
    override_log_under_test: LogUnderTest,
) -> None:
    append_only_log__unknown_schema_version__raises(override_log_under_test)


# ---------------------------------------------------------------------------
# What is specific to an override: the schema cross-check, empty-categories,
# and the (record_id, dimension) fold
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_override_log__unknown_dimension__raises(project: Project, schema: TaxonomySchema) -> None:
    log = open_override_log(project)

    with pytest.raises(LogError, match="not declared"):
        log.append(
            record_id="scopus:1",
            dimension="not_a_real_dimension",
            categories=[],
            reviewer="kp",
            schema=schema,
        )

    assert log.load() == []


@pytest.mark.integration
def test_override_log__undeclared_category__raises(
    project: Project, schema: TaxonomySchema
) -> None:
    log = open_override_log(project)

    with pytest.raises(LogError, match="undeclared"):
        log.append(
            record_id="scopus:1",
            dimension="learning_paradigm",
            categories=["not_a_real_category"],
            reviewer="kp",
            schema=schema,
        )

    assert log.load() == []


@pytest.mark.integration
def test_override_log__empty_categories__is_a_valid_verdict(
    project: Project, schema: TaxonomySchema
) -> None:
    """``categories: []`` -- a human looked and nothing applies -- is not refused."""
    log = open_override_log(project)

    event = log.append(
        record_id="scopus:1",
        dimension="learning_paradigm",
        categories=[],
        reviewer="kp",
        reason="Genuinely does not fit any listed paradigm.",
        schema=schema,
    )

    assert (event.categories, log.load()) == ((), [as_persisted(event)])


@pytest.mark.integration
def test_override_log__fold__latest_event_per_record_and_dimension_wins(
    project: Project, schema: TaxonomySchema
) -> None:
    log = open_override_log(project)
    log.append(
        record_id="scopus:1",
        dimension="learning_paradigm",
        categories=["supervised"],
        reviewer="kp",
        schema=schema,
    )
    second = log.append(
        record_id="scopus:1",
        dimension="learning_paradigm",
        categories=["unsupervised"],
        reviewer="mm",
        schema=schema,
    )
    # A different dimension for the same record folds under a different key
    # entirely, and must not be shadowed by the events above.
    other_dimension = log.append(
        record_id="scopus:1",
        dimension="architecture",
        categories=["cnn"],
        reviewer="kp",
        schema=schema,
    )

    folded = log.fold()

    assert folded[("scopus:1", "learning_paradigm")] == as_persisted(second)
    assert folded[("scopus:1", "architecture")] == as_persisted(other_dimension)
    assert len(folded) == 2


def test_fold_override_events__permuted_input__yields_an_identical_mapping() -> None:
    """The fold does not depend on the order events are handed to it (§3.7.3)."""
    from datetime import UTC, datetime

    def event(
        event_id: str, ts_second: int, dimension: str, categories: tuple[str, ...]
    ) -> OverrideEvent:
        return OverrideEvent(
            event_id=event_id,
            ts=datetime(2026, 1, 1, 0, 0, ts_second, tzinfo=UTC),
            project="p",
            record_id="scopus:1",
            dimension=dimension,
            categories=categories,
            reviewer="kp",
        )

    first = event("a", 0, "learning_paradigm", ("supervised",))
    second = event("b", 1, "learning_paradigm", ("unsupervised",))
    third = event("c", 0, "architecture", ("cnn",))

    forward = fold_override_events([first, second, third])
    reversed_order = fold_override_events([third, second, first])

    assert (
        forward
        == reversed_order
        == {
            ("scopus:1", "learning_paradigm"): second,
            ("scopus:1", "architecture"): third,
        }
    )


@pytest.mark.integration
def test_override__blank_record_id__raises_validation_error(
    project: Project, schema: TaxonomySchema
) -> None:
    log = open_override_log(project)

    with pytest.raises(ValidationError, match="invalid taxonomy override event"):
        log.append(
            record_id="   ",
            dimension="learning_paradigm",
            categories=[],
            reviewer="kp",
            schema=schema,
        )


@pytest.mark.integration
def test_override_log__paths__are_the_projects_taxonomy_overrides_log_and_its_sidecar(
    project: Project,
) -> None:
    log = open_override_log(project)

    assert log.path == project.taxonomy_overrides_path
    assert log.checksum_path == log.path.with_name(log.path.name + ".sha256")
