"""Top venues and venue-type split (BUILD_PLAN Stage 7, ADR 0022 Decision 5)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import polars as pl
import pytest

from prismabib.bibliometrics.venues import (
    _NO_VENUE_TYPE_CELL,
    _UNKNOWN_VENUE_TYPE,
    DEFAULT_TOP_N,
    _normalise_venue_name,
    top_venues,
    venue_type_split,
)
from prismabib.errors import AnalysisError
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus
from tests.bibliometrics_helpers import (
    BibCorpusSpec,
    BibRecordSpec,
    build_bib_project,
    include_everything,
    open_corpus,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("IEEE Conference on X", "ieee conference on x"),
        ("The IEEE Conference on X", "ieee conference on x"),
        ("IEEE Conference on X (2020)", "ieee conference on x"),
        ("IEEE  Conference   on X", "ieee conference on x"),
        ("Robotics & Automation", "robotics and automation"),
        ("Robotics and Automation.", "robotics and automation"),
        ("THE Robotics & Automation Journal", "robotics and automation journal"),
    ],
    ids=[
        "plain",
        "leading-the",
        "trailing-paren",
        "extra-whitespace",
        "ampersand",
        "trailing-dot",
        "combined",
    ],
)
def test_normalise_venue_name__variants__fold_to_the_same_key(raw: str, expected: str) -> None:
    assert _normalise_venue_name(raw) == expected


@pytest.mark.integration
def test_top_venues__records_without_venue_name__are_not_a_venue(tmp_path: Path) -> None:
    """Two real-journal records + two nameless ones: one venue, never ''."""
    records = [
        BibRecordSpec(number=1, venue_name="Real Journal", source_id="1"),
        BibRecordSpec(number=2, venue_name="Real Journal", source_id="1"),
        BibRecordSpec(number=3, venue_name="", source_id="2"),
        BibRecordSpec(number=4, venue_name="", source_id="3"),
    ]
    corpus = open_corpus(build_bib_project(tmp_path, BibCorpusSpec(records=records)))

    data = top_venues(corpus, stage=PrismaStage.RAW, top_n=10).data

    assert data.height == 1
    assert data.item(0, "venue") == "Real Journal"
    assert data.item(0, "count") == 2


@pytest.mark.integration
def test_venues__name_variants__group_together(tmp_path: Path) -> None:
    """Four raw variants of one venue, three records of a second -- all by hand."""
    # Every distinct raw name variant gets its own `source_id`: that is what
    # makes Scopus emit a variant in the first place (`venue_id` is keyed on
    # `source-id`, ``store/load.py::_venue_id_from_entry``) -- two records
    # sharing one `source_id` always share one venue *name* too.
    records = [
        BibRecordSpec(number=1, venue_name="IEEE Conference on X", source_id="1"),
        BibRecordSpec(number=2, venue_name="The IEEE Conference on X", source_id="2"),
        BibRecordSpec(number=3, venue_name="The IEEE Conference on X", source_id="2"),
        BibRecordSpec(number=4, venue_name="IEEE Conference on X (2020)", source_id="3"),
        BibRecordSpec(number=5, venue_name="Journal of Testing", source_id="4"),
        BibRecordSpec(number=6, venue_name="Journal of Testing", source_id="4"),
        BibRecordSpec(number=7, venue_name="Journal of Testing", source_id="4"),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    corpus = open_corpus(project)

    result = top_venues(corpus, stage=PrismaStage.RAW, top_n=10)

    by_name = {row["venue"]: row["count"] for row in result.data.to_dicts()}
    assert by_name == {
        # Most frequent raw variant, ties broken lexicographically: two
        # records read "The IEEE Conference on X", one each read the other
        # two spellings -- "The IEEE..." wins outright on frequency.
        "The IEEE Conference on X": 4,
        "Journal of Testing": 3,
    }


@pytest.mark.integration
def test_venues__tied_variant_frequency__breaks_lexicographically(tmp_path: Path) -> None:
    """One occurrence each of two variants: the display name is the alphabetically first."""
    records = [
        BibRecordSpec(number=1, venue_name="Zeta Journal", source_id="1"),
        BibRecordSpec(number=2, venue_name="The Zeta Journal", source_id="2"),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    corpus = open_corpus(project)

    result = top_venues(corpus, stage=PrismaStage.RAW)

    assert result.data.to_dicts() == [
        {"venue": "The Zeta Journal", "venue_type": "journal", "count": 2}
    ]


@pytest.mark.integration
def test_top_venues__top_n__truncates_and_is_recorded_in_params(tmp_path: Path) -> None:
    records = [
        BibRecordSpec(number=i, venue_name=f"Venue {i}", source_id=str(i)) for i in range(1, 6)
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    corpus = open_corpus(project)

    result = top_venues(corpus, stage=PrismaStage.RAW, top_n=2)

    assert result.data.height == 2
    assert result.params["top_n"] == 2
    assert "top_n=2" in result.caption()


@pytest.mark.integration
def test_top_venues__empty_corpus__empty_data(tmp_path: Path) -> None:
    project = build_bib_project(tmp_path, BibCorpusSpec(records=[]))
    corpus = open_corpus(project)

    result = top_venues(corpus, stage=PrismaStage.INCLUDED)

    assert result.data.height == 0


@pytest.mark.integration
def test_top_venues__default_included_stage__hand_computed_value_matches(tmp_path: Path) -> None:
    """`Corpus.venues(INCLUDED)` value-checked, not just type/shape-checked (see test_geography.py)."""
    records = [
        BibRecordSpec(number=1, venue_name="Journal A", source_id="1"),
        BibRecordSpec(number=2, venue_name="Journal A", source_id="1"),
        BibRecordSpec(number=3, venue_name="Journal B", source_id="2"),
    ]
    project = build_bib_project(tmp_path, BibCorpusSpec(records=records))
    include_everything(project)
    corpus = open_corpus(project)

    result = top_venues(corpus)  # default stage=PrismaStage.INCLUDED

    by_name = {row["venue"]: row["count"] for row in result.data.to_dicts()}
    assert by_name == {"Journal A": 2, "Journal B": 1}
    assert result.provenance.stage is PrismaStage.INCLUDED


@pytest.mark.integration
def test_venue_type_split__single_type__one_row(tmp_path: Path) -> None:
    project = build_bib_project(
        tmp_path, BibCorpusSpec(records=[BibRecordSpec(number=1, venue_type="Journal")])
    )
    corpus = open_corpus(project)

    result = venue_type_split(corpus, stage=PrismaStage.RAW)

    assert result.data.to_dicts() == [{"venue_type": "journal", "count": 1}]


@pytest.mark.unit
def test_venue_type_sentinels__are_the_documented_pair() -> None:
    """The two spellings of "no venue type" are pinned together, not left to coincidence.

    `top_venues` renders `""` into a table cell (preserving the old SQL's
    `COALESCE(venue_type, '')`, so `report/tables.py`'s golden does not
    move); `venue_type_split` uses `"unknown"` because there the value is a
    row *label* and a blank label is unreadable. Two spellings of one fact
    is a drift risk (ADR 0022 Decision 5), so a future author changing
    either is told about the other here.
    """
    assert _NO_VENUE_TYPE_CELL == ""
    assert _UNKNOWN_VENUE_TYPE == "unknown"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Sensors (Basel, Switzerland)", "sensors (basel, switzerland)"),
        ("Nature (London)", "nature (london)"),
        ("Journal of Testing (Series B)", "journal of testing (series b)"),
        ("Journal of Testing (2020)", "journal of testing"),
        ("Journal of Testing (1998)", "journal of testing"),
    ],
    ids=["issn-disambiguator", "city", "series", "year-2020", "year-1998"],
)
def test_normalise_venue_name__trailing_parenthetical__strips_only_a_bare_year(
    raw: str, expected: str
) -> None:
    """A parenthetical suffix is how Scopus disambiguates same-titled journals.

    `Sensors (Basel, Switzerland)` and a hypothetical bare `Sensors` are
    different venues, and merging them would invent a venue that published
    papers it did not -- the exact failure ADR 0022 Decision 5 opens by
    forbidding. A bare year carries no such meaning, so it alone is
    stripped.

    The expectations are written out in full rather than derived from
    `_normalise_venue_name`, so they cannot agree with the implementation
    by construction.
    """
    assert _normalise_venue_name(raw) == expected


# --- ADR 0040: the `years` engine argument ------------------------------------------------


def _year_venue_corpus(tmp_path: Path) -> Corpus:
    # Old Journal leads the whole corpus (3 papers) but has one paper in 2020-2021;
    # New A / New B have 2 and 1 in range, so a pre-filter top-N of 1 would be wrong.
    conf = "Conference Proceeding"
    records = [
        BibRecordSpec(number=1, year=2018, venue_name="Old Journal", source_id="1"),
        BibRecordSpec(number=2, year=2019, venue_name="Old Journal", source_id="1"),
        BibRecordSpec(number=3, year=2020, venue_name="Old Journal", source_id="1"),
        BibRecordSpec(number=4, year=2020, venue_name="New A", source_id="2", venue_type=conf),
        BibRecordSpec(number=5, year=2021, venue_name="New A", source_id="2", venue_type=conf),
        BibRecordSpec(number=6, year=2021, venue_name="New B", source_id="3"),
        BibRecordSpec(number=7, year=2023, venue_name="New B", source_id="3"),
    ]
    return open_corpus(build_bib_project(tmp_path, BibCorpusSpec(records=records)))


@pytest.mark.integration
def test_top_venues__years__top_n_applied_after_filtering(tmp_path: Path) -> None:
    corpus = _year_venue_corpus(tmp_path)

    whole = top_venues(corpus, stage=PrismaStage.RAW, top_n=1)
    ranged = top_venues(corpus, stage=PrismaStage.RAW, top_n=2, years=(2020, 2021))

    assert whole.data.item(0, "venue") == "Old Journal"
    assert ranged.data.rows() == [("New A", "conference", 2), ("New B", "journal", 1)]
    assert ranged.params == {
        "top_n": 2,
        "years": "2020-2021",
        "n_in_range": 4,
        "n_no_year_dropped": 0,
    }


@pytest.mark.integration
def test_top_venues__years__top_n_one_returns_in_range_leader(tmp_path: Path) -> None:
    ranged = top_venues(
        _year_venue_corpus(tmp_path), stage=PrismaStage.RAW, top_n=1, years=(2020, 2021)
    )

    assert ranged.data.item(0, "venue") == "New A"


@pytest.mark.integration
def test_top_venues__years__provenance_stays_stage_level(tmp_path: Path) -> None:
    corpus = _year_venue_corpus(tmp_path)

    plain = top_venues(corpus, stage=PrismaStage.RAW)
    ranged = top_venues(corpus, stage=PrismaStage.RAW, years=(2020, 2021))

    assert ranged.provenance == plain.provenance
    assert ranged.provenance.corpus_size == 7


@pytest.mark.integration
def test_venues__no_years__params_and_output_unchanged(tmp_path: Path) -> None:
    corpus = _year_venue_corpus(tmp_path)
    top = top_venues(corpus, stage=PrismaStage.RAW)
    split = venue_type_split(corpus, stage=PrismaStage.RAW)

    for years in (None, (None, None)):
        top_same = top_venues(corpus, stage=PrismaStage.RAW, years=years)
        split_same = venue_type_split(corpus, stage=PrismaStage.RAW, years=years)
        assert top_same.params == {"top_n": DEFAULT_TOP_N}
        assert split_same.params == {}
        assert top_same.data.equals(top.data)
        assert split_same.data.equals(split.data)
        assert top_same.caption() == top.caption()
        assert split_same.caption() == split.caption()


@pytest.mark.integration
def test_venue_type_split__years__counts_in_range(tmp_path: Path) -> None:
    corpus = _year_venue_corpus(tmp_path)

    result = venue_type_split(corpus, stage=PrismaStage.RAW, years=(2020, 2021))

    assert result.data.rows() == [("conference", 2), ("journal", 2)]
    assert result.params == {"years": "2020-2021", "n_in_range": 4, "n_no_year_dropped": 0}
    assert "papers published in 2020-2021" in result.caption()


@pytest.mark.integration
def test_venue_type_split__null_year__dropped_and_counted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus = _year_venue_corpus(tmp_path)
    original = corpus.records

    def with_null_year(stage: PrismaStage = PrismaStage.INCLUDED) -> pl.DataFrame:
        frame = original(stage)
        return frame.with_columns(
            pl.when(pl.col("record_id") == frame.item(0, "record_id"))
            .then(None)
            .otherwise(pl.col("year"))
            .alias("year")
        )

    monkeypatch.setattr(corpus, "records", with_null_year)

    result = venue_type_split(corpus, stage=PrismaStage.RAW, years=(2000, 2030))

    assert result.params["n_no_year_dropped"] == 1
    assert result.params["n_in_range"] == 6
    assert sum(result.data.get_column("count").to_list()) == 6


@pytest.mark.unit
@pytest.mark.parametrize("fn", [top_venues, venue_type_split])
def test_venues__inverted_range__raises(fn: Any) -> None:
    with pytest.raises(AnalysisError):
        fn(None, years=(2021, 2020))
