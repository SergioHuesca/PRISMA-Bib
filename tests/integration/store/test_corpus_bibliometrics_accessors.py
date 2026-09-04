"""``Corpus.venues``/``affiliations``/``authors`` (ADR 0022 Decision 9).

The three accessors Stage 7 adds to the frozen Stage 3 ``Corpus`` contract.
Each is asserted against an *independently* computed value -- a raw SQL
query run here, not through ``Corpus`` -- never against ``Corpus``'s own
prior output, matching every other test in this store suite
(``test_corpus__records_by_stage__delegates_to_prisma_engine`` is the
precedent this file follows).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.errors import StoreError
from prismabib.prisma import engine
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.stage import PrismaStage
from prismabib.store.db import connect
from prismabib.store.load import Corpus, build_store
from tests.store_helpers import copy_reference_project


def _screen_first_n(project: Project, n: int, m: int) -> list[str]:
    """Include the first ``n`` language-eligible records at title/abstract, ``m`` at full text.

    Args:
        project: A built, unscreened project.
        n: How many records to include at ``TITLE_ABSTRACT``.
        m: How many of those (from the front) to also include at ``FULLTEXT``.

    Returns:
        The ``m`` record ids that reach ``PrismaStage.INCLUDED``.
    """
    log = DecisionLog(project)
    eligible = sorted(engine.language_set(project))[:n]
    for record_id in eligible:
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT, record_id=record_id, reviewer="kp", decision="include"
        )
    for record_id in eligible[:m]:
        log.append(
            stage=PrismaStage.FULLTEXT, record_id=record_id, reviewer="kp", decision="include"
        )
    return eligible[:m]


@pytest.mark.integration
def test_corpus__venues__raw_stage__matches_independent_sql(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    corpus = Corpus.open(project)

    result = corpus.venues(stage=PrismaStage.RAW)

    connection = connect(project, read_only=True)
    try:
        expected = connection.execute(
            "SELECT count(*) FROM records r JOIN venues v ON r.venue_id = v.venue_id"
        ).fetchone()
    finally:
        connection.close()

    assert expected is not None
    assert result.height == int(expected[0])
    assert set(result.columns) == {
        "record_id",
        "venue_id",
        "name",
        "issn",
        "eissn",
        "venue_type",
        "abbreviation",
    }


@pytest.mark.integration
def test_corpus__venues__ordered_by_record_id(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    corpus = Corpus.open(project)

    result = corpus.venues(stage=PrismaStage.RAW)

    record_ids = result.get_column("record_id").to_list()
    assert record_ids == sorted(record_ids)


@pytest.mark.integration
def test_corpus__venues__included_stage__delegates_to_prisma_engine(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    included = _screen_first_n(project, n=6, m=4)
    corpus = Corpus.open(project)

    result = corpus.venues(stage=PrismaStage.INCLUDED)

    assert set(result.get_column("record_id").to_list()) == set(included) == engine.corpus(project)
    assert result.height == 4


@pytest.mark.integration
def test_corpus__affiliations__raw_stage__matches_independent_sql(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    corpus = Corpus.open(project)

    result = corpus.affiliations(stage=PrismaStage.RAW)

    connection = connect(project, read_only=True)
    try:
        expected = connection.execute("SELECT count(*) FROM record_affiliations").fetchone()
    finally:
        connection.close()

    assert expected is not None
    assert result.height == int(expected[0])
    assert set(result.columns) == {"record_id", "afid", "name", "city", "country_iso3"}


@pytest.mark.integration
def test_corpus__affiliations__fans_out_multiple_rows_per_multi_affiliation_record(
    tmp_path: Path,
) -> None:
    """A record with several affiliations gets several rows -- the fan-out ``keywords`` also does."""
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    corpus = Corpus.open(project)

    result = corpus.affiliations(stage=PrismaStage.RAW)

    counts = result.group_by("record_id").len()
    multi = counts.filter(counts["len"] > 1)
    assert multi.height > 0, "the reference fixture is expected to carry a multi-affiliation record"


@pytest.mark.integration
def test_corpus__affiliations__ordered_by_record_id_then_afid(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    corpus = Corpus.open(project)

    result = corpus.affiliations(stage=PrismaStage.RAW)

    pairs = list(
        zip(
            result.get_column("record_id").to_list(),
            result.get_column("afid").to_list(),
            strict=True,
        )
    )
    assert pairs == sorted(pairs)


@pytest.mark.integration
def test_corpus__affiliations__included_stage__restricted_to_engine_set(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    included = _screen_first_n(project, n=6, m=4)
    corpus = Corpus.open(project)

    result = corpus.affiliations(stage=PrismaStage.INCLUDED)

    # `==`, not `<=`: every reference-fixture record carries affiliation
    # data (verified independently -- see the module docstring's own
    # "never against Corpus's own prior output" rule), so a `<=` bound is
    # satisfied even by an accessor that returns nothing at all. Matches
    # `test_corpus__venues__included_stage__delegates_to_prisma_engine`'s
    # stronger assertion above.
    assert set(result.get_column("record_id").to_list()) == set(included) == engine.corpus(project)


@pytest.mark.integration
def test_corpus__authors__raw_stage__matches_independent_sql(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    corpus = Corpus.open(project)

    result = corpus.authors(stage=PrismaStage.RAW)

    connection = connect(project, read_only=True)
    try:
        expected = connection.execute("SELECT count(*) FROM record_authors").fetchone()
    finally:
        connection.close()

    assert expected is not None
    assert result.height == int(expected[0])
    assert set(result.columns) == {"record_id", "author_id", "surname", "given_name", "position"}


@pytest.mark.integration
def test_corpus__authors__ordered_by_record_id_then_position(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    corpus = Corpus.open(project)

    result = corpus.authors(stage=PrismaStage.RAW)

    triples = list(
        zip(
            result.get_column("record_id").to_list(),
            result.get_column("position").to_list(),
            result.get_column("author_id").to_list(),
            strict=True,
        )
    )
    assert triples == sorted(triples)


@pytest.mark.integration
def test_corpus__authors__included_stage__restricted_to_engine_set(tmp_path: Path) -> None:
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    included = _screen_first_n(project, n=6, m=4)
    corpus = Corpus.open(project)

    result = corpus.authors(stage=PrismaStage.INCLUDED)

    # See `test_corpus__affiliations__included_stage__restricted_to_engine_set`
    # above for why `==` rather than `<=`.
    assert set(result.get_column("record_id").to_list()) == set(included) == engine.corpus(project)


@pytest.mark.integration
@pytest.mark.parametrize("accessor_name", ["venues", "affiliations", "authors"])
def test_corpus__bare_construction__non_raw_stage_raises_for_the_new_accessor(
    tmp_path: Path, accessor_name: str
) -> None:
    """The same rule ``records``/``keywords`` already enforce (module docstring's "no project")."""
    project = copy_reference_project(tmp_path)
    build_store(project, rebuild=True)
    connection = connect(project, read_only=True)
    try:
        corpus = Corpus(connection)
        accessor = getattr(corpus, accessor_name)
        with pytest.raises(StoreError, match=r"Corpus\.open"):
            accessor(stage=PrismaStage.INCLUDED)
    finally:
        connection.close()
