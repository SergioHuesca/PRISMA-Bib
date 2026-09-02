"""Unit tests for PDF extraction (BUILD_PLAN Stage 6 Tests table, ADR 0019).

No OCR: a page with no machine-readable text layer must be flagged
``low_confidence`` and a page with one must not -- the negative case is what
makes the flag mean something rather than being permanently ``True``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.fulltext.extract import extract_pdf
from tests.fixtures.pdf_builder import make_minimal_pdf


@pytest.mark.unit
def test_extract__pdf_without_text_layer__sets_low_confidence(tmp_path: Path) -> None:
    pdf_path = tmp_path / "scanned.pdf"
    pdf_path.write_bytes(make_minimal_pdf(b""))

    sections = extract_pdf(pdf_path)

    assert len(sections) == 1
    assert sections[0].low_confidence is True
    assert sections[0].text == ""


@pytest.mark.unit
def test_extract__pdf_with_text_layer__does_not_set_low_confidence(tmp_path: Path) -> None:
    pdf_path = tmp_path / "born-digital.pdf"
    pdf_path.write_bytes(make_minimal_pdf(b"BT /F1 24 Tf 10 100 Td (Hello World) Tj ET"))

    sections = extract_pdf(pdf_path)

    assert len(sections) == 1
    assert sections[0].low_confidence is False
    assert "Hello World" in sections[0].text


@pytest.mark.unit
def test_extract__pdf__names_the_page_one_indexed(tmp_path: Path) -> None:
    """``position`` is zero-based (a sort key); ``section_name`` reads as a citation would."""
    pdf_path = tmp_path / "one-page.pdf"
    pdf_path.write_bytes(make_minimal_pdf(b"BT /F1 24 Tf 10 100 Td (Page One) Tj ET"))

    sections = extract_pdf(pdf_path)

    assert sections[0].position == 0
    assert sections[0].section_name == "page_1"
