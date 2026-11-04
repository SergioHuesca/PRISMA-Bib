"""Unit tests for the OpenAlex query language (ADR 0042 Decision 7, Verification 35 to 39)."""

from __future__ import annotations

import pytest

from prismabib.errors import ValidationError
from prismabib.query import OPENALEX_MAX_QUERY_CHARS, build_query, validate_openalex_search

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    "text",
    [
        "carbon",
        "carbon capture",  # implicit AND
        '"carbon capture" AND "policy"',
        "solar OR wind",
        "solar NOT nuclear",
        "solar AND NOT nuclear",
        'solar OR "wind power" NOT "fossil fuel"',
        "state-of-the-art review",  # a hyphen inside a word is not a minus
        "carbon and policy",  # lower case is an ordinary word, never rewritten
        'energía renovable AND "política pública"',
        "日本語 研究",
        "  spaced   out \n query ",
        "x" * OPENALEX_MAX_QUERY_CHARS,
    ],
)
def test_validate__accepted_forms__returned_unchanged(text: str) -> None:
    assert validate_openalex_search(text) == text


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("", "empty"),
        ("   \n\t", "empty"),
        ('""', "empty quoted"),
        ('"  "', "empty quoted"),
        ('"never closed', "never closed"),
        ('"a"b', "stuck"),
        ('a"b c"', "stuck"),
        ("(solar OR wind) AND policy", "parentheses"),
        ("solar)", "parentheses"),
        ("sola*", "wildcards"),
        ("sol?r", "wildcards"),
        ("solar~2", "~"),
        ("-nuclear", "NOT nuclear"),
        ("solar -nuclear", "NOT nuclear"),
        ('TITLE-ABS-KEY("carbon")', "Scopus"),
        ('AUTHKEY("carbon")', "Scopus"),
        ("NOT nuclear", "start with a term"),
        ("solar NOT NOT nuclear", "NOT needs a term"),
        ("AND solar", "AND with no term"),
        ("OR solar", "OR with no term"),
        ("solar AND OR wind", "OR with no term"),
        ("solar AND", "ends with AND"),
        ("solar OR", "ends with OR"),
        ("solar NOT", "ends with NOT"),
    ],
)
def test_validate__refused_forms__raise_naming_the_problem(text: str, needle: str) -> None:
    with pytest.raises(ValidationError) as caught:
        validate_openalex_search(text)
    assert needle in str(caught.value)


def test_validate__quoted_phrase_may_hold_operator_words() -> None:
    # An operator word inside a phrase is part of the phrase, not an operator.
    assert validate_openalex_search('"carbon AND capture"') == '"carbon AND capture"'


def test_validate__scopus_hint_shows_the_phrase_form() -> None:
    with pytest.raises(ValidationError, match='"carbon capture"'):
        validate_openalex_search('TITLE-ABS-KEY("x")')


def test_scopus_build_query__unchanged_by_the_openalex_validator() -> None:
    """Verification 40, second half: the Scopus renderer is the same as before."""
    assert build_query(terms=["a b"]) == 'TITLE-ABS-KEY("a b")'


def test_validate__over_2000_characters__refused() -> None:
    with pytest.raises(ValidationError, match="2001 characters"):
        validate_openalex_search("x" * (OPENALEX_MAX_QUERY_CHARS + 1))


def test_validate__short_in_characters_but_long_in_the_url__refused() -> None:
    # 600 characters, but each Cyrillic letter is six characters once percent-encoded.
    with pytest.raises(ValidationError, match="once encoded"):
        validate_openalex_search("я" * 600)


@pytest.mark.parametrize("text", ['"what is AI?"', '"COVID (19)"', '"sola*"', '"a~b"', "(a)"])
def test_validate__refused_character_inside_a_phrase__says_to_rephrase(text: str) -> None:
    with pytest.raises(ValidationError, match="rephrase without it, even inside a quoted phrase"):
        validate_openalex_search(text)
