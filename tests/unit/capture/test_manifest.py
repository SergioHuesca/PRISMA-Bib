"""Unit test for the S02-AC5 invariant (BUILD_PLAN Stage 2 Tests table, line 828).

BUILD_PLAN's own description of this test -- "``FlowCounts.identified``
reads the manifest, not a row count" -- names a class (``FlowCounts``) that
belongs to Stage 4's PRISMA flow model (§Stage 4), which does not exist in
``src/prismabib`` yet; grepping the tree at this stage confirms there is no
``prisma``/``flow`` module to import. What Stage 2 *does* own, and *can*
pin now, is the half of that invariant that lives on this side of the
boundary: :func:`~prismabib.sources.scopus.extract_total_results` -- the
function BUILD_PLAN S02-AC5 (line 807) requires be "the only source of the
PRISMA 'records identified' number" -- must read
``opensearch:totalResults`` and must **not** derive a count from the
entries actually present on the page. This test proves that half by
constructing a page where the two disagree and asserting the server's
number wins; Stage 4's own test suite is where ``FlowCounts.identified``
itself gets pinned against a real manifest.
"""

from __future__ import annotations

import pytest

from prismabib.sources.scopus import extract_total_results


@pytest.mark.unit
@pytest.mark.acceptance("S02-AC5")
def test_manifest__total_results__is_the_only_identified_count() -> None:
    """A page's own entry count must never leak into ``total_results``.

    The page below has exactly 2 ``entry`` records but a server-reported
    ``opensearch:totalResults`` of 500 (as a real first page of a large
    result set would: it reports the *query's* total match count, not this
    page's size). If ``extract_total_results`` -- or anything built on top
    of it, such as ``RunManifest.total_results`` -- ever started counting
    entries instead of trusting the server field, this assertion is what
    would catch it.
    """
    page = {
        "search-results": {
            "opensearch:totalResults": "500",
            "entry": [
                {"dc:identifier": "SCOPUS_ID:1"},
                {"dc:identifier": "SCOPUS_ID:2"},
            ],
        }
    }

    total_results = extract_total_results(page)

    assert total_results == 500
    assert total_results != len(page["search-results"]["entry"])
