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


@pytest.mark.parametrize(
    "char",
    [
        "\u201c", "\u201d", "\u201e", "\u00ab", "\u00bb", "\u2018", "\u2019",
        "\uff08", "\uff09", "\uff0a", "\uff1f", "\uff5e", "\uff02",
        "\u200b", "\u200c", "\u200d", "\u2060", "\ufeff", "\u00ad",
    ],
)  # fmt: skip
def test_validate__lookalike_syntax_or_invisible_character__refused_with_its_code(
    char: str,
) -> None:
    for text in (f"carbon {char}capture{char} AND policy", f"a{char}AND b"):
        with pytest.raises(ValidationError) as caught:
            validate_openalex_search(text)
        assert f"U+{ord(char):04X}" in str(caught.value)
        assert "plain ASCII" in str(caught.value)


def test_validate__nbsp_is_whitespace_not_refused() -> None:
    assert validate_openalex_search("solar\u00a0AND wind")  # query_key also treats it as a space


@pytest.mark.parametrize("text", ["women's health", '"it\'s"', "solar OR NOT nuclear"])
def test_validate__straight_apostrophe_and_or_not__accepted(text: str) -> None:
    assert validate_openalex_search(text) == text


def test_validate__encoded_length_boundary() -> None:
    assert validate_openalex_search("я" * 500)  # encodes to exactly 3,000 characters
    with pytest.raises(ValidationError, match="once encoded"):
        validate_openalex_search("я" * 501)


@pytest.mark.parametrize("text", ["solar AND\n", "solar\nAND\nAND\nwind"])
def test_validate__dangling_operator_across_lines__refused(text: str) -> None:
    with pytest.raises(ValidationError):
        validate_openalex_search(text)


def test_validate__multiline_query__accepted() -> None:
    assert validate_openalex_search('"carbon capture"\nAND policy')


@pytest.mark.parametrize("text", ["a - b", "a -- b"])
def test_validate__bare_hyphen__has_a_sensible_hint(text: str) -> None:
    with pytest.raises(ValidationError, match="bare '-' is not an operator") as caught:
        validate_openalex_search(text)
    assert "NOT  " not in str(caught.value)


def test_how_to_page__every_openalex_query_example__is_valid() -> None:
    import re
    import tomllib
    from pathlib import Path

    page = Path(__file__).parents[2] / "docs" / "how-to" / "build-an-openalex-query.md"
    blocks = re.findall(r"```toml\n(.*?)```", page.read_text(encoding="utf-8"), re.DOTALL)
    searches = [
        tomllib.loads(b)["openalex_query"]["search"] for b in blocks if "openalex_query" in b
    ]

    assert len(searches) >= 2
    for search in searches:
        assert validate_openalex_search(search) == search
