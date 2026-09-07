"""Integration tests for :mod:`prismabib.taxonomy.coder` (BUILD_PLAN §Stage 8).

Real DuckDB, real filesystem -- a project built exactly the way
``tests/taxonomy_helpers.py`` and ``tests/store_helpers.py`` build one for
every other stage's integration suite.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.stage import PrismaStage
from prismabib.taxonomy.coder import CountingUnit, code, distribution
from prismabib.taxonomy.overrides import OverrideLog
from prismabib.taxonomy.schema import load_dimensions
from tests.bibliometrics_helpers import include_everything, open_corpus
from tests.conftest import SeededIdFactory
from tests.taxonomy_helpers import (
    LEARNING_PARADIGM_RULES_V1,
    LEARNING_PARADIGM_RULES_V2,
    TaxonomyRecordSpec,
    build_taxonomy_project,
    load_rule_files,
    write_dimensions,
    write_rule_file,
)

_RECORDS = [
    TaxonomyRecordSpec(number=1, title="A AlphaMarker approach"),
    TaxonomyRecordSpec(number=2, title="A BetaMarker approach"),
    TaxonomyRecordSpec(number=3, title="A GammaMarker approach"),
    TaxonomyRecordSpec(number=4, title="Plain title, no marker at all"),
]


def _build(tmp_path: Path, slug: str = "coder") -> Path:
    project = build_taxonomy_project(tmp_path, _RECORDS, slug=slug)
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V1)
    return project.root


@pytest.mark.integration
@pytest.mark.acceptance("S08-AC2")
def test_coder__same_rules_and_corpus__is_deterministic(tmp_path: Path) -> None:
    """Two independently-opened Corpus handles over the same input agree exactly.

    Deliberately reopens the *project* twice, from scratch, rather than
    reusing one in-memory handle: the pure-function property BUILD_PLAN
    asks for is about the input on disk, not about Python object identity.
    """
    from prismabib.project import Project

    root = _build(tmp_path)

    project_a = Project.open("coder", root=tmp_path)
    project_b = Project.open("coder", root=tmp_path)
    schema = load_dimensions(project_a)
    (rule_file_a,) = load_rule_files(project_a, schema, "learning_paradigm")
    (rule_file_b,) = load_rule_files(project_b, schema, "learning_paradigm")

    result_a = code(open_corpus(project_a), [rule_file_a], [])
    result_b = code(open_corpus(project_b), [rule_file_b], [])

    assert result_a == result_b
    assert root.exists()  # guard: the fixture actually built something


@pytest.mark.integration
def test_coder__run_twice__is_idempotent(tmp_path: Path) -> None:
    """Running the coder twice over an unchanged input never duplicates anything.

    There is nothing to *store*, so "idempotent" here means: two calls
    against the same open corpus produce byte-for-byte, row-for-row
    identical results -- not merely the same length.
    """
    project_root = _build(tmp_path)
    from prismabib.project import Project

    project = Project.open("coder", root=project_root.parent)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)

    first = code(corpus, [rule_file], [])
    second = code(corpus, [rule_file], [])

    assert first == second
    assert len(first.assignments) == len(
        {(a.record_id, a.dimension, a.category) for a in first.assignments}
    )


@pytest.mark.integration
@pytest.mark.acceptance("S08-AC3")
def test_override__survives_rule_version_bump_and_recode(tmp_path: Path) -> None:
    """The core value proposition of rules-plus-override (ADR 0005/0023).

    Bump the rule file's version and re-run the coder; a human override
    recorded under the old version must still win, untouched.
    """
    project_root = _build(tmp_path)
    from prismabib.project import Project

    project = Project.open("coder", root=project_root.parent)
    schema = load_dimensions(project)
    corpus = open_corpus(project)
    (rule_file_v1,) = load_rule_files(project, schema, "learning_paradigm")
    target_record_id = _RECORDS[0].record_id  # rule-coded "supervised" under v1

    override_log = OverrideLog(project, id_factory=SeededIdFactory(seed=0, prefix="ov"))
    override_log.append(
        record_id=target_record_id,
        dimension="learning_paradigm",
        categories=["self_supervised"],
        reviewer="kp",
        reason="Re-read the paper; it is self-supervised, not supervised.",
        schema=schema,
    )
    overrides = override_log.load()

    before_bump = code(corpus, [rule_file_v1], overrides)

    write_rule_file(project, "learning_paradigm", LEARNING_PARADIGM_RULES_V2)
    (rule_file_v2,) = load_rule_files(project, schema, "learning_paradigm")
    after_bump = code(corpus, [rule_file_v2], overrides)

    assert before_bump.effective_categories("learning_paradigm")[target_record_id] == (
        "self_supervised",
    )
    assert after_bump.effective_categories("learning_paradigm")[target_record_id] == (
        "self_supervised",
    )
    # And the rule file really did change underneath the override -- guard
    # against this test passing because v1 and v2 code identically.
    assert rule_file_v1.version != rule_file_v2.version


@pytest.mark.integration
@pytest.mark.acceptance("S08-AC5")
def test_counting_unit__papers_single_label__sums_to_corpus_size(tmp_path: Path) -> None:
    project_root = _build(tmp_path)
    from prismabib.project import Project

    project = Project.open("coder", root=project_root.parent)
    schema = load_dimensions(project)
    (rule_file,) = load_rule_files(project, schema, "learning_paradigm")
    corpus = open_corpus(project)

    result = code(corpus, [rule_file], [])
    dist = distribution(result, schema, "learning_paradigm", CountingUnit.PAPERS)

    assert sum(dist.counts.values()) == dist.corpus_size == len(_RECORDS)
    assert dist.corpus_size == corpus.records(PrismaStage.INCLUDED).height
