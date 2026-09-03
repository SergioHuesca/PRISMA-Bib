"""Unit tests for the pure functions of ``src/prismabib/sources/unpaywall.py`` (ADR 0019).

No network, no filesystem -- :func:`~prismabib.sources.unpaywall.best_oa_pdf_url`
and :func:`~prismabib.sources.unpaywall.looks_like_pdf` are both plain
functions over already-parsed data, and both had zero coverage before this
stage's review (a real defect: :func:`best_oa_pdf_url`'s landing-page
fallback is exactly what made a non-PDF resolvable in the first place).
"""

from __future__ import annotations

import pytest

from prismabib.sources.unpaywall import best_oa_pdf_url, looks_like_pdf

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
