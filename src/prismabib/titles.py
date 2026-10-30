"""The one title normaliser (ADR 0028 Decision 4, ADR 0031 Decision 4 note).

A leaf module: it imports nothing from ``capture`` or ``sources`` so both can
use it without a cycle. ``candidate_id`` hashes its output, so a change here
changes persisted candidate ids -- see ``tests/unit/capture/test_candidates.py``.
"""

from __future__ import annotations

import unicodedata


def normalise_title(title: str) -> str:
    """Fold a title to a comparison key: accent-free, lowercase, no punctuation.

    Punctuation, symbols and separators (including Unicode dashes) become a
    space, so ``well-being`` and ``well being`` match; whitespace is then
    collapsed.
    """
    decomposed = unicodedata.normalize("NFKD", title).casefold()
    stripped = "".join(c for c in decomposed if not unicodedata.combining(c))
    spaced = "".join(" " if unicodedata.category(c)[0] in "PSZC" else c for c in stripped)
    return " ".join(spaced.split())


__all__ = ["normalise_title"]
