"""Property test: ``rebuild_abstract`` inverts OpenAlex's abstract inversion."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from prismabib.sources.openalex import rebuild_abstract

_words = st.lists(
    st.text(alphabet=st.characters(blacklist_categories=("Z", "C")), min_size=1, max_size=12),
    min_size=1,
    max_size=60,
)


@pytest.mark.property
@given(words=_words)
def test_rebuild_abstract__invert_then_rebuild__round_trips(words: list[str]) -> None:
    index: dict[str, list[int]] = {}
    for position, word in enumerate(words):
        index.setdefault(word, []).append(position)

    assert rebuild_abstract(index) == " ".join(words)
