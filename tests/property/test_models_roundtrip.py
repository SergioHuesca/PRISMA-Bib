"""Property test for the ``Record`` round trip (BUILD_PLAN Stage 1 Tests table, line 733).

CRITICAL per the table: the strategy is a real ``st.builds``/``st.composite``
generator over the actual :class:`~prismabib.models.Record` model -- not a
handful of hand-picked examples -- and it must, on every run, cover:
``abstract=None``, empty keyword lists, non-ASCII author names, 40+ authors,
and ``year`` at both ends of the generated range.

``_record_strategy`` already generates all five cases at random (a ``None``
abstract and empty keyword lists are in its domain, ``st.text()``'s default
alphabet spans full Unicode so non-ASCII surnames occur, author lists run up
to 45, and ``year`` spans ``[YEAR_MIN, YEAR_MAX]``). The ``@example(...)``
calls below *pin* one instance of each case so it is exercised on every
invocation regardless of what Hypothesis happens to draw -- the
deterministic half of the guarantee (§3.7.3 rule 3), with
``@settings(derandomize=True)`` making the random half reproducible too.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from prismabib.models import Affiliation, Author, PayloadRef, Record, Venue

YEAR_MIN = 1900
YEAR_MAX = 2100

_SAFE_PATH_TEXT = st.from_regex(r"[A-Za-z0-9_./-]{1,50}", fullmatch=True)

_author_strategy: st.SearchStrategy[Author] = st.builds(
    Author,
    author_id=st.none() | st.text(min_size=1, max_size=20),
    surname=st.text(min_size=1, max_size=60),
    given_name=st.none() | st.text(min_size=1, max_size=60),
    initials=st.none() | st.text(min_size=1, max_size=10),
)

_affiliation_strategy: st.SearchStrategy[Affiliation] = st.builds(
    Affiliation,
    afid=st.none() | st.text(min_size=1, max_size=20),
    name=st.text(min_size=1, max_size=100),
    city=st.none() | st.text(min_size=1, max_size=50),
    country=st.none() | st.sampled_from(["USA", "FRA", "KOR", "Wakanda"]),
)

_venue_strategy: st.SearchStrategy[Venue] = st.builds(
    Venue,
    name=st.text(min_size=1, max_size=100),
    issn=st.none() | st.from_regex(r"\d{4}-\d{3}[0-9X]", fullmatch=True),
    eissn=st.none() | st.from_regex(r"\d{4}-\d{3}[0-9X]", fullmatch=True),
    venue_type=st.sampled_from(["journal", "conference", "book", "other"]),
    abbreviation=st.none() | st.text(min_size=1, max_size=30),
)

_payload_ref_strategy: st.SearchStrategy[PayloadRef] = st.builds(
    PayloadRef,
    path=_SAFE_PATH_TEXT.map(Path),
    line=st.integers(min_value=0, max_value=10_000),
)


@st.composite
def _record_strategy(draw: st.DrawFn) -> Record:
    """Draw an arbitrary, schema-valid :class:`Record`."""
    return Record(
        record_id=draw(st.from_regex(r"scopus:2-s2\.0-\d{11}", fullmatch=True)),
        doi=draw(st.none() | st.from_regex(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+", fullmatch=True)),
        title=draw(st.text(min_size=1, max_size=200)),
        abstract=draw(st.none() | st.text(min_size=1, max_size=500)),
        year=draw(st.integers(min_value=YEAR_MIN, max_value=YEAR_MAX)),
        cover_date=draw(
            st.none() | st.dates(min_value=date(1900, 1, 1), max_value=date(2100, 12, 31))
        ),
        doc_type=draw(st.sampled_from(["ar", "cp", "re", "ch", "ed"])),
        language=draw(st.none() | st.text(min_size=1, max_size=30)),
        venue=draw(_venue_strategy),
        authors=draw(st.lists(_author_strategy, min_size=1, max_size=45)),
        affiliations=draw(st.lists(_affiliation_strategy, min_size=0, max_size=5)),
        author_keywords=draw(st.lists(st.text(min_size=1, max_size=30), max_size=10)),
        index_keywords=draw(st.lists(st.text(min_size=1, max_size=30), max_size=10)),
        subject_areas=draw(st.lists(st.text(min_size=1, max_size=10), max_size=5)),
        open_access=draw(st.none() | st.booleans()),
        source_payload_ref=draw(_payload_ref_strategy),
    )


def _record(**overrides: Any) -> Record:
    """Build one concrete, deterministic :class:`Record` for use in ``@example(...)``."""
    fields: dict[str, Any] = {
        "record_id": "scopus:2-s2.0-00000000001",
        "doi": "10.1000/example",
        "title": "A Study of Example Things",
        "abstract": "An example abstract.",
        "year": 2020,
        "cover_date": None,
        "doc_type": "ar",
        "language": "English",
        "venue": Venue(
            name="Journal of Testing",
            issn="1234-567X",
            eissn=None,
            venue_type="journal",
            abbreviation="J. Test.",
        ),
        "authors": [Author(author_id="aut-1", surname="Smith", given_name="John", initials="J.")],
        "affiliations": [],
        "author_keywords": ["example", "testing"],
        "index_keywords": ["testing"],
        "subject_areas": ["COMP"],
        "open_access": True,
        "source_payload_ref": PayloadRef(path=Path("raw/scopus.jsonl"), line=0),
    }
    fields.update(overrides)
    return Record(**fields)


def _authors(surnames: list[str]) -> list[Author]:
    return [
        Author(author_id=f"aut-{index}", surname=surname, given_name="Jordan", initials="J.")
        for index, surname in enumerate(surnames)
    ]


# One example doing double duty: 45 authors (> the mandated 40+) whose
# surnames include non-ASCII scripts (Latin-with-diacritics and CJK).
_NON_ASCII_AND_MANY_AUTHORS = _authors(
    ["Müller", "陈", "Åström", "Øyvind", "García"] + [f"Author{index}" for index in range(40)]
)


@pytest.mark.property
@pytest.mark.acceptance("S01-AC2")
@settings(
    max_examples=50,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)
@example(record=_record(abstract=None))
@example(record=_record(author_keywords=[], index_keywords=[]))
@example(record=_record(authors=_NON_ASCII_AND_MANY_AUTHORS))
@example(record=_record(year=YEAR_MIN))
@example(record=_record(year=YEAR_MAX))
@given(record=_record_strategy())
def test_record__round_trip__is_lossless(record: Record) -> None:
    dumped = record.model_dump_json()

    restored = Record.model_validate_json(dumped)

    assert restored == record
