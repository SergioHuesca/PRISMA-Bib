"""A minimal, hand-authored PDF builder for Stage 6's extraction tests.

``pdfplumber``/``pdfminer.six`` need a syntactically valid PDF to exercise --
not a real paper, and not a third-party fixture (§2.4's dependency list has
no PDF-generation library, and pulling one in purely to build test fixtures
would be scope creep this project's own dependency discipline exists to
prevent). :func:`make_minimal_pdf` writes the smallest PDF structure that
satisfies both shapes :mod:`prismabib.fulltext.extract` needs to
distinguish: a page with a ``Tj`` text-showing operator in its content
stream (a real text layer) and a page with an empty content stream (what a
scanned, OCR-less page looks like to a parser -- no text layer at all).
"""

from __future__ import annotations

import io


def make_minimal_pdf(content_stream: bytes = b"") -> bytes:
    """Build a syntactically valid, single-page PDF with a given content stream.

    Args:
        content_stream: The page's raw PDF content-stream operators, e.g.
            ``b"BT /F1 24 Tf 10 100 Td (Hello World) Tj ET"`` to render the
            text "Hello World". An empty bytestring (the default) produces
            a page with no text layer at all -- pdfminer's fallback
            recovery does not need a byte-accurate xref table to read
            either shape, so precision beyond "parses" is not attempted
            here.

    Returns:
        The complete PDF file bytes: one page, ``MediaBox`` 200x200, a
        Helvetica font resource (used only when ``content_stream``
        actually shows text).
    """
    page_object = (
        b"<</Type/Page/Parent 2 0 R/Resources<</Font<</F1 4 0 R>>>>"
        b"/MediaBox[0 0 200 200]/Contents 5 0 R>>"
    )
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        page_object,
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        b"<</Length %d>>\nstream\n%s\nendstream" % (len(content_stream), content_stream),
    ]

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(buffer.tell())
        buffer.write(b"%d 0 obj\n" % index)
        buffer.write(obj)
        buffer.write(b"\nendobj\n")

    xref_start = buffer.tell()
    object_count = len(objects) + 1
    buffer.write(b"xref\n")
    buffer.write(b"0 %d\n" % object_count)
    buffer.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        buffer.write(b"%010d 00000 n \n" % offset)
    buffer.write(b"trailer\n<</Size %d/Root 1 0 R>>\n" % object_count)
    buffer.write(b"startxref\n%d\n" % xref_start)
    buffer.write(b"%%EOF")
    return buffer.getvalue()


__all__ = ["make_minimal_pdf"]
