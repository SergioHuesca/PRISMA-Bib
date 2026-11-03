"""Unit tests for the pure OpenAlex search-work derivation (ADR 0042 Decision 5, PR 4a)."""

from __future__ import annotations

import pytest

from prismabib.errors import ValidationError
from prismabib.sources.openalex import authorship_details, parse_work
from prismabib.store.openalex_search import parse_search_work, work_id
from tests.openalex_search_helpers import work

pytestmark = pytest.mark.unit


def test_authorship_details__returns_author_and_institution_ids() -> None:
    parsed = parse_work(work(1))

    details = authorship_details(parsed)

    assert [(a.name, a.author_id) for a in details.authors] == [
        ("Ada Author1", "https://openalex.org/A1")
    ]
    assert [(i.name, i.inst_id, i.country_code) for i in details.institutions] == [
        ("Institute 1", "https://openalex.org/I1", "ES")
    ]


def test_authorship_details__skips_blank_names_and_junk_and_keeps_missing_ids_as_none() -> None:
    raw = work(
        1,
        authorships=[
            {"author": {"display_name": ""}, "institutions": [{"display_name": ""}]},
            {"author": "not a mapping", "institutions": None},
            {"author": {"id": 7, "display_name": "Solo Name"}, "institutions": ["junk"]},
        ],
    )

    details = authorship_details(parse_work(raw))

    assert [(a.name, a.author_id) for a in details.authors] == [("Solo Name", None)]
    assert details.institutions == ()


@pytest.mark.parametrize("bad", [None, "", "   ", 5])
def test_work_id__not_a_usable_string__is_none(bad: object) -> None:
    assert work_id({"id": bad}) is None


def test_parse_search_work__reads_the_fields_the_tables_need() -> None:
    parsed = parse_search_work(work(3, type="book-chapter"))

    assert parsed.openalex_id == "https://openalex.org/W3"
    assert parsed.doi == "10.1/item-3"
    assert (parsed.title, parsed.year, parsed.doc_type) == (
        "Synthetic title 3",
        2020,
        "book chapter",
    )
    assert parsed.abstract == "Synthetic abstract3"
    assert parsed.subject_code == "1702"
    assert parsed.cited_by_count == 30
    assert parsed.topics == ("Topic 3",)
    assert parsed.authors == (("openalex-author:https://openalex.org/A3", "Author3", "Ada"),)
    assert parsed.affiliations == (("openalex-inst:https://openalex.org/I3", "Institute 3", "ESP"),)


@pytest.mark.parametrize("title", [None, "", "  \t "])
def test_parse_search_work__no_title__raises_validation_error(title: str | None) -> None:
    with pytest.raises(ValidationError, match="title"):
        parse_search_work(work(1, title=title or None) | {"title": title})


@pytest.mark.parametrize("count", [True, -1, "5", 2.5, None])
def test_parse_search_work__cited_by_count_that_is_not_a_count__is_none(count: object) -> None:
    assert parse_search_work(work(1) | {"cited_by_count": count}).cited_by_count is None


def test_parse_search_work__single_token_name__surname_only() -> None:
    raw = work(1, authorships=[{"author": {"display_name": "Plato"}}])

    assert parse_search_work(raw).authors == (("openalex-name:plato", "Plato", None),)
