"""Unit tests for ``src/prismabib/query.py`` (BUILD_PLAN Stage 2 Tests table, lines 813-814).

Pure logic, no I/O -- ``build_query`` never touches the filesystem or
network, so these belong under ``unit``.
"""

from __future__ import annotations

import pytest

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
