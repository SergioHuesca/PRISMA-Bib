"""Pure view-model tests for the manual-entry panel (no Panel widgets, no store)."""

from __future__ import annotations

import pytest

from prismabib.capture import manual_ui as ui
from prismabib.capture.conflicts import OpenConflict


@pytest.mark.unit
@pytest.mark.parametrize(
    ("doi", "url"),
    [
        ("10.1000/one", "https://doi.org/10.1000/one"),
        (" 10.1000/a b ", "https://doi.org/10.1000/a%20b"),
        ("", None),
        ("  ", None),
        (None, None),
    ],
)
def test_publisher_url__doi_or_none(doi: str | None, url: str | None) -> None:
    assert ui.publisher_url(doi) == url


@pytest.mark.unit
def test_conflict_html__shows_both_sides_escaped_one_item_per_line() -> None:
    conflict = OpenConflict("scopus:1", "authors", '["Smith","Lee"]', '["<b>Park</b>"]')

    out = ui.conflict_html(conflict)

    assert "Smith\nLee" in out
    assert "&lt;b&gt;Park&lt;/b&gt;" in out
    assert "<b>Park</b>" not in out


@pytest.mark.unit
def test_open_conflict_digests__are_sha256_of_the_stored_text() -> None:
    import hashlib

    conflict = OpenConflict("scopus:1", "abstract", '"a"', '"b"')

    assert conflict.digests == (
        hashlib.sha256(b'"a"').hexdigest(),
        hashlib.sha256(b'"b"').hexdigest(),
    )
