"""Unit tests for the pure functions of ``src/prismabib/sources/unpaywall.py`` (ADR 0019).

No network, no filesystem -- :func:`~prismabib.sources.unpaywall.best_oa_pdf_url`
and :func:`~prismabib.sources.unpaywall.looks_like_pdf` are both plain
functions over already-parsed data, and both had zero coverage before this
stage's review (a real defect: :func:`best_oa_pdf_url`'s landing-page
fallback is exactly what made a non-PDF resolvable in the first place).
"""

from __future__ import annotations

import pytest

from prismabib.sources.unpaywall import best_oa_pdf_url, looks_like_pdf, oa_pdf_candidates

_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\n%%EOF"
_HTML_BYTES = b"<!DOCTYPE html><html><body>Landing page</body></html>"


@pytest.mark.unit
def test_best_oa_pdf_url__url_for_pdf_present__is_preferred() -> None:
    response = {
        "best_oa_location": {
            "url_for_pdf": "https://oa.example.org/paper.pdf",
            "url": "https://oa.example.org/landing",
        }
    }

    assert best_oa_pdf_url(response) == "https://oa.example.org/paper.pdf"


@pytest.mark.unit
def test_best_oa_pdf_url__no_url_for_pdf__falls_back_to_generic_url() -> None:
    response = {"best_oa_location": {"url_for_pdf": None, "url": "https://oa.example.org/landing"}}

    assert best_oa_pdf_url(response) == "https://oa.example.org/landing"


@pytest.mark.unit
def test_best_oa_pdf_url__no_best_oa_location__is_none() -> None:
    assert best_oa_pdf_url({"best_oa_location": None}) is None
    assert best_oa_pdf_url({}) is None


@pytest.mark.unit
def test_best_oa_pdf_url__best_oa_location_not_a_mapping__is_none() -> None:
    assert best_oa_pdf_url({"best_oa_location": "not-a-dict"}) is None


@pytest.mark.unit
def test_best_oa_pdf_url__blank_url_for_pdf__falls_back_to_url() -> None:
    response = {"best_oa_location": {"url_for_pdf": "", "url": "https://oa.example.org/landing"}}

    assert best_oa_pdf_url(response) == "https://oa.example.org/landing"


@pytest.mark.unit
def test_looks_like_pdf__real_pdf_magic_bytes_no_content_type__is_true() -> None:
    assert looks_like_pdf(_PDF_BYTES, None) is True


@pytest.mark.unit
def test_looks_like_pdf__real_pdf_with_application_pdf_content_type__is_true() -> None:
    assert looks_like_pdf(_PDF_BYTES, "application/pdf; charset=binary") is True


@pytest.mark.unit
@pytest.mark.acceptance("S06-AC3")
def test_looks_like_pdf__html_landing_page__is_false() -> None:
    """The BLOCKING regression this pins: a 200 HTML body must not pass as a PDF."""
    assert looks_like_pdf(_HTML_BYTES, "text/html; charset=utf-8") is False


@pytest.mark.unit
def test_looks_like_pdf__html_bytes_with_no_content_type_header__is_false() -> None:
    """Even with no ``Content-Type`` at all, HTML has no ``%PDF-`` magic bytes."""
    assert looks_like_pdf(_HTML_BYTES, None) is False


@pytest.mark.unit
def test_looks_like_pdf__pdf_bytes_but_html_content_type__is_false() -> None:
    """The ``Content-Type`` header is authoritative when it clearly disagrees."""
    assert looks_like_pdf(_PDF_BYTES, "text/html") is False


@pytest.mark.unit
def test_looks_like_pdf__magic_bytes_not_at_offset_zero__still_true() -> None:
    """The PDF spec allows a short binary comment before the header; a strict
    offset-0 check would reject real-world PDFs some producers emit."""
    prefixed = b"\x00\x01\x02" + _PDF_BYTES

    assert looks_like_pdf(prefixed, None) is True


@pytest.mark.unit
def test_looks_like_pdf__empty_bytes__is_false() -> None:
    assert looks_like_pdf(b"", None) is False


@pytest.mark.unit
def test_oa_pdf_candidates__mirror_has_a_direct_pdf__it_outranks_a_landing_page() -> None:
    """Every direct PDF link is tried before any landing page, across all locations.

    `best_oa_pdf_url` reads only `best_oa_location` and falls straight back to
    its generic `url`. On a real 35-record corpus that produced nine
    `not_a_pdf` misses: records Unpaywall *knew* were open access, where the
    single location asked happened to offer only HTML.

    Here the publisher's "best" location has no `url_for_pdf` and a repository
    mirror does. The mirror's PDF must be tried first -- otherwise the landing
    page is downloaded, rejected, and the record is reported as though no
    open-access copy existed.
    """
    response = {
        "best_oa_location": {"url": "https://publisher.example.org/landing"},
        "oa_locations": [
            {"url": "https://publisher.example.org/landing"},
            {"url_for_pdf": "https://repo.example.org/bitstream/paper.pdf"},
        ],
    }

    assert oa_pdf_candidates(response) == (
        "https://repo.example.org/bitstream/paper.pdf",
        "https://publisher.example.org/landing",
    )


@pytest.mark.unit
def test_oa_pdf_candidates__no_open_access_location__is_empty() -> None:
    """`is_oa: false` yields nothing to try, not a spurious candidate."""
    assert oa_pdf_candidates({"best_oa_location": None, "oa_locations": []}) == ()


@pytest.mark.unit
def test_oa_pdf_candidates__many_locations__is_capped_and_deduplicated() -> None:
    """One record cannot become an unbounded number of downloads.

    The same URL commonly appears as both `best_oa_location` and a member of
    `oa_locations`; fetching it twice would waste a request and, on a host
    that rate-limits, could cost the record its second real chance.
    """
    shared = "https://repo.example.org/a.pdf"
    response = {
        "best_oa_location": {"url_for_pdf": shared},
        "oa_locations": [{"url_for_pdf": shared}]
        + [{"url_for_pdf": f"https://m{n}.example.org/p.pdf"} for n in range(9)],
    }

    candidates = oa_pdf_candidates(response)

    assert candidates[0] == shared
    assert len(candidates) == len(set(candidates))
    assert len(candidates) <= 5
