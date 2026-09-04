"""Unit tests for the pure functions of ``src/prismabib/sources/crossref.py`` (ADR 0020).

No network, no filesystem -- :func:`~prismabib.sources.crossref.tdm_links` is
a plain function over already-parsed data.
"""

from __future__ import annotations

import pytest

from prismabib.sources.crossref import TdmLink, tdm_links


@pytest.mark.unit
def test_tdm_links__mixed_intended_applications__keeps_only_text_mining() -> None:
    """Crossref's own vocabulary also carries ``similarity-checking`` links, which are not this."""
    response = {
        "message": {
            "link": [
                {
                    "URL": "https://plagiarism.example.org/check.pdf",
                    "intended-application": "similarity-checking",
                    "content-type": "application/pdf",
                },
                {
                    "URL": "https://link.springer.com/content/pdf/10.1007/x.pdf",
                    "intended-application": "text-mining",
                    "content-type": "application/pdf",
                },
                {
                    "URL": "https://api.elsevier.com/content/article/doi/10.1016/x",
                    "intended-application": "text-mining",
                    "content-type": "text/xml",
                },
            ]
        }
    }

    assert tdm_links(response) == (
        TdmLink(
            url="https://link.springer.com/content/pdf/10.1007/x.pdf",
            content_type="application/pdf",
        ),
        TdmLink(
            url="https://api.elsevier.com/content/article/doi/10.1016/x", content_type="text/xml"
        ),
    )


@pytest.mark.unit
def test_tdm_links__no_message__is_empty() -> None:
    assert tdm_links({}) == ()
    assert tdm_links({"message": "not-a-mapping"}) == ()


@pytest.mark.unit
def test_tdm_links__no_link_array__is_empty() -> None:
    """Crossref's own measured majority case: 23 of 29 records on the corpus ADR 0020 measured."""
    assert tdm_links({"message": {}}) == ()
    assert tdm_links({"message": {"link": "not-a-list"}}) == ()


@pytest.mark.unit
def test_tdm_links__entry_missing_url__is_skipped() -> None:
    response = {
        "message": {
            "link": [
                {"intended-application": "text-mining", "content-type": "application/pdf"},
                {"URL": "", "intended-application": "text-mining"},
                {"URL": "https://good.example.org/x.pdf", "intended-application": "text-mining"},
            ]
        }
    }

    assert tdm_links(response) == (
        TdmLink(url="https://good.example.org/x.pdf", content_type=None),
    )


@pytest.mark.unit
def test_tdm_links__unspecified_content_type__is_still_returned() -> None:
    """ACM's own declared type ('unspecified') carries no information -- the link is still a candidate."""
    response = {
        "message": {
            "link": [
                {
                    "URL": "https://dl.acm.org/doi/pdf/10.1145/x",
                    "intended-application": "text-mining",
                    "content-type": "unspecified",
                }
            ]
        }
    }

    assert tdm_links(response) == (
        TdmLink(url="https://dl.acm.org/doi/pdf/10.1145/x", content_type="unspecified"),
    )


@pytest.mark.unit
def test_tdm_links__non_mapping_entries__are_skipped_not_raised() -> None:
    response = {"message": {"link": ["not-a-mapping", 42, None]}}

    assert tdm_links(response) == ()
