"""Live probe for ``src/prismabib/sources/sciencedirect.py`` (BUILD_PLAN Stage 6 Tests table).

Hits the real ScienceDirect Article Retrieval API once. Deselected by
default (``-m "not live"``); runs nightly and on demand (``pytest -m live``)
per ``tests/live/conftest.py``.

**Never asserts a specific entitlement.** Whether this project's key can
read this particular DOI's full text varies with institutional access and
Elsevier licensing, neither of which this suite controls or should assert
about. What it asserts is that the *endpoint still behaves* the way the
client's error taxonomy expects: exactly one of "entitled and the article
resolves", "not entitled" (403), or "not in ScienceDirect's catalogue"
(404) -- an upstream schema or status-code change that breaks all three
would fail this test, which is the early warning a live probe exists to
give (mirroring ``tests/live/test_scopus_live.py``'s own reasoning).
"""

from __future__ import annotations

import pytest

from prismabib.config import Settings
from prismabib.errors import EntitlementError
from prismabib.sources.sciencedirect import ArticleNotFoundError, ScienceDirectClient

pytestmark = pytest.mark.live

# A real, plausible Elsevier-registrant DOI (Pattern Recognition, a Stage 6
# reference-corpus venue). Its exact validity is not asserted -- only that
# the endpoint answers with one of the three known states.
_DOI = "10.1016/j.patcog.2021.107762"


def test_live__sciencedirect_entitlement_probe__returns_known_state() -> None:
    with ScienceDirectClient(Settings()) as client:
        try:
            body = client.article_retrieval_xml(_DOI)
        except EntitlementError:
            return  # Known state: valid key, not entitled to this article.
        except ArticleNotFoundError:
            return  # Known state: this DOI is not in ScienceDirect's catalogue.

    assert body  # Known state: entitled, and the article resolved to non-empty XML.
