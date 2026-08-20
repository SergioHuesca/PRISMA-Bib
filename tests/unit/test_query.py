"""Unit tests for ``src/prismabib/query.py`` (BUILD_PLAN Stage 2 Tests table, lines 813-814).

Pure logic, no I/O -- ``build_query`` never touches the filesystem or
network, so these belong under ``unit``. ``build_query_for_project``, which
does read ``project.toml`` from disk, is exercised separately in
``tests/integration/test_query.py``.
"""

from __future__ import annotations

import pytest

from prismabib.errors import ConfigError, ValidationError
from prismabib.query import build_query


@pytest.mark.unit
def test_query_builder__terms_and_compound__renders_expected_boolean() -> None:
    """The exact BUILD_PLAN §3.1 worked example (module docstring, lines 6-24)."""
    rendered = build_query(
        terms=["video anomaly detection", "surveillance anomaly detection"],
        compound_terms=[{"all": ["abnormal event detection", "video"]}],
        fields=["TITLE-ABS-KEY"],
    )

    assert rendered == (
        'TITLE-ABS-KEY("video anomaly detection") OR '
        'TITLE-ABS-KEY("surveillance anomaly detection") OR '
        '(TITLE-ABS-KEY("abnormal event detection") AND TITLE-ABS-KEY("video"))'
    )


@pytest.mark.unit
def test_query_builder__term_with_quotes__is_escaped() -> None:
    """An embedded ``") OR TITLE-ABS-KEY("`` cannot inject a second Boolean clause.

    Without escaping, this term would render as two OR-ed clauses instead of
    one -- a silently wrong query, exactly the defect class BUILD_PLAN line
    814 exists to close.
    """
    injection_term = 'foo") OR TITLE-ABS-KEY("bar'

    rendered = build_query(terms=[injection_term], fields=["TITLE-ABS-KEY"])

    assert rendered == 'TITLE-ABS-KEY("foo\\") OR TITLE-ABS-KEY(\\"bar")'
    # The unescaped injection substring never appears contiguously: every
    # would-be clause-breaking quote is preceded by a backslash instead.
    assert '") OR TITLE-ABS-KEY("' not in rendered


@pytest.mark.unit
def test_query_builder__multiple_fields__or_joins_and_parenthesises() -> None:
    """Two fields must OR-join inside parentheses.

    Every other test uses a single field, which leaves the multi-field join and
    its interaction with compound-group parenthesisation unverified. Precedence is
    the whole point: without the inner parentheses an OR would escape its group
    and widen the corpus silently.
    """
    rendered = build_query(terms=["deep learning"], fields=["TITLE-ABS-KEY", "AUTHKEY"])

    assert rendered == '(TITLE-ABS-KEY("deep learning") OR AUTHKEY("deep learning"))'


@pytest.mark.unit
def test_query_builder__compound_with_multiple_fields__nests_correctly() -> None:
    rendered = build_query(
        compound_terms=[{"all": ["a", "b"]}], fields=["TITLE-ABS-KEY", "AUTHKEY"]
    )

    assert rendered == (
        '((TITLE-ABS-KEY("a") OR AUTHKEY("a")) AND (TITLE-ABS-KEY("b") OR AUTHKEY("b")))'
    )


@pytest.mark.unit
def test_query_builder__bare_sequence_compound_group__is_accepted() -> None:
    """The branch production actually takes.

    ``build_query_for_project`` unwraps each TOML ``{all = [...]}`` mapping and
    passes a plain list, so this branch runs on every real capture while the
    mapping branch is what the §3.1 example test exercises. Both are supported;
    only this one was untested.
    """
    rendered = build_query(compound_terms=[["a", "b"]], fields=["TITLE-ABS-KEY"])

    assert rendered == '(TITLE-ABS-KEY("a") AND TITLE-ABS-KEY("b"))'


@pytest.mark.unit
@pytest.mark.parametrize(
    ("group", "expected_fragment"),
    [
        pytest.param({"any": ["a", "b"]}, "'all'", id="unknown-key"),
        pytest.param({"all": ["a"], "any": ["b"]}, "'all'", id="two-keys"),
        pytest.param("just a string", "bare string", id="bare-string"),
        pytest.param({"all": "not-a-list"}, "list of strings", id="all-not-a-list"),
        pytest.param({"all": ["a", 7]}, "list of strings", id="all-non-string-member"),
        pytest.param(42, "mapping", id="not-a-sequence"),
    ],
)
def test_query_builder__uninterpretable_compound_group__raises_config_error(
    group: object, expected_fragment: str
) -> None:
    """Every shape the coercer cannot read must raise, never coerce.

    This is the regression guard for the defect that made a wrong corpus
    invisible: the old builder iterated a mapping's *keys* and rendered
    ``TITLE-ABS-KEY("all")`` without complaint. Each case asserts the message
    names what was received, so an operator can see which entry is wrong rather
    than being told only that something is.
    """
    with pytest.raises(ConfigError) as excinfo:
        build_query(terms=["x"], compound_terms=[group])

    assert expected_fragment in str(excinfo.value)


@pytest.mark.unit
def test_query_builder__empty_fields__raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        build_query(terms=["x"], fields=[])


@pytest.mark.unit
def test_query_builder__no_terms_at_all__raises_validation_error() -> None:
    """An empty query must fail, not render to a string that matches everything."""
    with pytest.raises(ValidationError):
        build_query(terms=[], compound_terms=[])


@pytest.mark.unit
def test_query_builder__empty_all_list__raises_validation_error() -> None:
    with pytest.raises(ValidationError):
        build_query(compound_terms=[{"all": []}])
