"""Country counts and citation impact by country (BUILD_PLAN Stage 7, ADR 0022 Decision 4)."""

from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from prismabib.bibliometrics.geography import (
    COUNTING_METHODS,
    _record_country_membership,
    citation_impact_by_country,
    country_counts,
)
from prismabib.errors import AnalysisError
from prismabib.stage import PrismaStage
from tests.bibliometrics_helpers import (
    AffiliationSpec,
    BibCorpusSpec,
    BibRecordSpec,
    build_bib_project,
    include_everything,
    open_corpus,
)

#: Four records: one single-country (USA), one two-country (USA + JPN), one
#: with an affiliation carrying no country at all, one with no affiliation
#: data whatsoever. By hand: 4 records total, 2 carry USA, 1 carries JPN, 2
#: carry no known country (UNK).
_GEOGRAPHY_RECORDS = [
    BibRecordSpec(number=1, affiliations=(AffiliationSpec(afid="AF1", country="USA"),)),
    BibRecordSpec(
        number=2,
        affiliations=(
            AffiliationSpec(afid="AF2", country="USA"),
            AffiliationSpec(afid="AF3", country="JPN"),
        ),
    ),
    BibRecordSpec(number=3, affiliations=(AffiliationSpec(afid="AF4", country=None),)),
    BibRecordSpec(number=4, affiliations=()),
]


@pytest.mark.unit
def test_country_counts__invalid_method__raises() -> None:
    # `country_counts` validates `method` before ever touching `corpus`, so
    # `None` is safe here -- it never gets far enough to be dereferenced.
    with pytest.raises(AnalysisError):
        country_counts(None, method="bogus")  # type: ignore[arg-type]


@pytest.mark.integration
def test_geography__full_counting__shares_sum_within_tolerance(tmp_path: Path) -> None:
    """Multi-country papers make shares exceed 100% under ``full`` -- the documented behaviour."""
    project = build_bib_project(tmp_path, BibCorpusSpec(records=_GEOGRAPHY_RECORDS))
    corpus = open_corpus(project)

    result = country_counts(corpus, stage=PrismaStage.RAW, method="full")

    by_country = {row["country"]: row for row in result.data.to_dicts()}
    assert by_country["USA"]["count"] == pytest.approx(2.0)
    assert by_country["JPN"]["count"] == pytest.approx(1.0)
    assert by_country["UNK"]["count"] == pytest.approx(2.0)
    total_share = sum(row["share"] for row in result.data.to_dicts())
    assert total_share == pytest.approx(5 / 4)  # 5 memberships over 4 records
    assert total_share > 1.0
    assert result.params["method"] == "full"


@pytest.mark.integration
def test_geography__fractional_counting__shares_sum_to_one(tmp_path: Path) -> None:
    """The alternative mode is arithmetically exact: every record contributes exactly 1.0."""
    project = build_bib_project(tmp_path, BibCorpusSpec(records=_GEOGRAPHY_RECORDS))
    corpus = open_corpus(project)

    result = country_counts(corpus, stage=PrismaStage.RAW, method="fractional")

    total_share = sum(row["share"] for row in result.data.to_dicts())
    assert total_share == pytest.approx(1.0, abs=1e-9)
    by_country = {row["country"]: row for row in result.data.to_dicts()}
    # Record 2 splits 1.0 over USA and JPN (0.5 each); records 1, 3, 4
    # each contribute 1.0 whole to their one bucket.
    assert by_country["USA"]["count"] == pytest.approx(1.5)
    assert by_country["JPN"]["count"] == pytest.approx(0.5)
    assert by_country["UNK"]["count"] == pytest.approx(2.0)


@pytest.mark.integration
def test_geography__unknown_country_bucket__preserves_record_total(tmp_path: Path) -> None:
    """No record vanishes: the total weighted count always equals the corpus size."""
    project = build_bib_project(tmp_path, BibCorpusSpec(records=_GEOGRAPHY_RECORDS))
    corpus = open_corpus(project)

    result = country_counts(corpus, stage=PrismaStage.RAW, method="fractional")

    assert sum(row["count"] for row in result.data.to_dicts()) == pytest.approx(4.0)
    assert result.provenance.corpus_size == 4


@pytest.mark.integration
def test_geography__affiliations_present_but_no_country_mapped__every_record_is_unk(
    tmp_path: Path,
) -> None:
    """An ordinary corpus: every affiliation lacks a mapped country (not zero affiliation rows).

    Distinct from `test_geography__no_affiliation_data_at_all__every_record_is_unk`
    below (no `record_affiliations` row at all): here `Corpus.affiliations`
    returns real, non-empty rows, but `country_iso3` is `null` on every one --
    the shape `build_store`'s `unmapped_country_values` log already
    anticipates. `_record_country_membership`'s `known` frame is then
    zero-row with a `country_iso3` column typed `Null` (every fetched value
    was Python `None`), which must not crash the `pl.concat` against
    `unknown`'s `Utf8` column.
    """
    records = [
        BibRecordSpec(
            number=1, affiliations=(AffiliationSpec(afid="AF1", name="Somewhere U", country=None),)
        ),
        BibRecordSpec(
            number=2, affiliations=(AffiliationSpec(afid="AF2", name="Elsewhere U", country=None),)
        ),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    corpus = open_corpus(project)

    result = country_counts(corpus, stage=PrismaStage.RAW)

    assert result.data.to_dicts() == [{"country": "UNK", "count": 2.0, "share": 1.0}]


@pytest.mark.integration
def test_geography__no_affiliation_data_at_all__every_record_is_unk(tmp_path: Path) -> None:
    """A paper with no country: the boundary case named explicitly in the task brief."""
    project = build_bib_project(
        tmp_path, BibCorpusSpec(records=[BibRecordSpec(number=1, affiliations=())])
    )
    corpus = open_corpus(project)

    result = country_counts(corpus, stage=PrismaStage.RAW)

    assert result.data.to_dicts() == [{"country": "UNK", "count": 1.0, "share": 1.0}]


@pytest.mark.integration
def test_geography__empty_corpus__empty_data(tmp_path: Path) -> None:
    project = build_bib_project(tmp_path, BibCorpusSpec(records=[]))
    corpus = open_corpus(project)

    result = country_counts(corpus, stage=PrismaStage.INCLUDED)

    assert result.data.height == 0


@pytest.mark.unit
def test_record_country_membership__empty_records__empty_output() -> None:
    empty = pl.DataFrame(schema={"record_id": pl.Utf8})
    affiliations = pl.DataFrame(schema={"record_id": pl.Utf8, "country_iso3": pl.Utf8})

    membership = _record_country_membership(empty, affiliations)

    assert membership.height == 0


@pytest.mark.integration
def test_citation_impact_by_country__hand_computed_totals__matches(tmp_path: Path) -> None:
    records = [
        BibRecordSpec(
            number=1, cited_by_count=10, affiliations=(AffiliationSpec(afid="AF1", country="USA"),)
        ),
        BibRecordSpec(
            number=2, cited_by_count=6, affiliations=(AffiliationSpec(afid="AF2", country="USA"),)
        ),
        BibRecordSpec(
            number=3, cited_by_count=4, affiliations=(AffiliationSpec(afid="AF3", country="JPN"),)
        ),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    corpus = open_corpus(project)

    result = citation_impact_by_country(corpus, stage=PrismaStage.RAW, method="full")

    by_country = {row["country"]: row for row in result.data.to_dicts()}
    assert by_country["USA"]["total_citations"] == pytest.approx(16.0)
    assert by_country["USA"]["mean_citations"] == pytest.approx(8.0)
    assert by_country["JPN"]["total_citations"] == pytest.approx(4.0)


@pytest.mark.integration
def test_country_counts__default_included_stage__hand_computed_value_matches(
    tmp_path: Path,
) -> None:
    """`Corpus.affiliations(INCLUDED)` value-checked, not just type/shape-checked.

    Every other `country_counts`/`citation_impact_by_country` test in this
    module passes `stage=PrismaStage.RAW`, which never reaches
    `Corpus._prisma_stage_record_ids` -- so a `Corpus.affiliations` that
    silently returned nothing for `INCLUDED` (or any other non-`RAW` stage)
    would still pass every other test in this file.
    """
    records = [
        BibRecordSpec(number=1, affiliations=(AffiliationSpec(afid="AF1", country="USA"),)),
        BibRecordSpec(number=2, affiliations=(AffiliationSpec(afid="AF2", country="JPN"),)),
        BibRecordSpec(number=3, affiliations=(AffiliationSpec(afid="AF3", country="USA"),)),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    include_everything(project)
    corpus = open_corpus(project)

    result = country_counts(corpus)  # default stage=PrismaStage.INCLUDED

    by_country = {row["country"]: row["count"] for row in result.data.to_dicts()}
    assert by_country == {"USA": 2.0, "JPN": 1.0}
    assert result.provenance.corpus_size == 3
    assert result.provenance.stage is PrismaStage.INCLUDED


@pytest.mark.unit
def test_counting_methods__is_exactly_full_and_fractional() -> None:
    """The closed set ADR 0022 Decision 4 names -- a third needs its own ADR."""
    assert COUNTING_METHODS == ("full", "fractional")
