"""Section extraction from resolved full text (BUILD_PLAN Stage 6, line 1148).

Two source shapes, two extractors:

- **ScienceDirect FULL-view XML** (:func:`extract_sciencedirect_xml`) is
  structured markup -- Elsevier's Article Retrieval ``FULL`` response wraps
  an ``originalText`` subtree using the ``ce:`` (Elsevier "common") element
  vocabulary: ``ce:abstract``, and a ``ce:sections`` container of
  ``ce:section`` elements, each carrying a ``ce:section-title`` and one or
  more ``ce:para``. Extraction walks that structure directly and needs no
  confidence flag -- the text was never rasterised, so there is no "text
  layer" question to ask of it.
- **PDF** (:func:`extract_pdf`) has no such structure to lean on: a PDF is a
  page-by-page presentation format, not a section outline. Each page becomes
  one section (``"page_N"``), and ``pdfplumber`` either finds a text layer
  or it does not. **No OCR is attempted** (BUILD_PLAN line 148): a page with
  no extractable text is flagged ``low_confidence`` and left for a human to
  read, rather than silently contributing an empty or hallucinated section.

Both return an ordered tuple of :class:`Section`, whose ``position`` is what
lets a reader recover document order after the sections are stored as rows
in ``fulltext_sections`` -- section names alone cannot say that "methods"
came before "results" (ADR 0019).

**A known simplification.** Elsevier's real markup can nest ``ce:section``
inside ``ce:section`` for subsections; this extractor only reads the
*direct* children of the outermost ``ce:sections`` container, one row per
top-level section. A subsection's prose is not lost -- ``ce:para`` anywhere
inside a top-level section (including inside a nested subsection) is folded
into that section's text -- only the subsection *boundary* is not
represented as its own row. Modelling nested sections as their own
first-class rows is future work, not required by any Stage 6 acceptance
criterion.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

import pdfplumber


@dataclass(frozen=True)
class Section:
    """One row of ``fulltext_sections`` (ADR 0019), before it is persisted.

    Attributes:
        position: Zero-based order within the source document. The sort key
            a reader needs to reconstruct document order; section names
            alone cannot (two records can both have a "results" section
            with no way to tell which came first without this).
        section_name: A short, lower-cased label -- ``"abstract"``,
            ``"introduction"``, ``"methods"``, ``"page_3"``, ... Free text,
            not a closed vocabulary: Elsevier's own section titles vary by
            journal and this module reports them as given.
        text: The extracted prose for this section.
        low_confidence: ``True`` when this section's text may be incomplete
            or absent because its source had no machine-readable text layer
            (a scanned PDF page). Always ``False`` for XML-derived sections,
            which were never rasterised in the first place.
    """

    position: int
    section_name: str
    text: str
    low_confidence: bool


def _localname(tag: str) -> str:
    """Strip a Clark-notation XML namespace off an element tag.

    Args:
        tag: An ``xml.etree.ElementTree`` element tag, e.g.
            ``"{http://www.elsevier.com/xml/common/dtd}section"``.

    Returns:
        Just the local part, e.g. ``"section"``. Matching by local name
        rather than the fully-qualified tag keeps this extractor tolerant
        of the exact namespace URI Elsevier's response declares, which is
        not itself part of the section-shape contract this module cares
        about.
    """
    return tag.rsplit("}", 1)[-1]


def _direct_children(element: ET.Element, name: str) -> list[ET.Element]:
    """The direct children of ``element`` whose local tag name is ``name``.

    Args:
        element: The parent element to search.
        name: The local (namespace-stripped) tag name to match.

    Returns:
        Matching direct children, in document order. Deliberately not
        recursive -- see the module docstring's note on nested sections.
    """
    return [child for child in element if _localname(child.tag) == name]


def _find_first(element: ET.Element, name: str) -> ET.Element | None:
    """The first descendant of ``element`` (at any depth) with local tag ``name``.

    Args:
        element: The subtree root to search.
        name: The local (namespace-stripped) tag name to match.

    Returns:
        The first matching element in document order, or ``None``.
    """
    for candidate in element.iter():
        if _localname(candidate.tag) == name:
            return candidate
    return None


def _collect_text(element: ET.Element) -> str:
    """Join every text node under ``element``, collapsing whitespace runs.

    Args:
        element: The subtree to collect text from.

    Returns:
        All descendant text, concatenated with a single space between
        non-empty fragments and stripped -- markup-agnostic, so it does not
        matter whether the prose sits directly in ``ce:para`` or one level
        deeper in a ``ce:simple-para``.
    """
    fragments = [fragment.strip() for fragment in element.itertext() if fragment.strip()]
    return " ".join(fragments)


def extract_sciencedirect_xml(xml_bytes: bytes) -> tuple[Section, ...]:
    """Extract ordered sections from a ScienceDirect FULL-view Article Retrieval response.

    Args:
        xml_bytes: The raw XML response body, as returned by the
            ScienceDirect Article Retrieval API under ``view=FULL``.

    Returns:
        An ordered tuple of :class:`Section`: the abstract first (when
        present), then each top-level ``ce:section`` in document order. A
        section with no extractable paragraph text is omitted -- an empty
        row would carry no information and would shift every later
        position for no benefit. ``low_confidence`` is always ``False``:
        XML is never a "no text layer" question.

    Raises:
        xml.etree.ElementTree.ParseError: If ``xml_bytes`` is not
            well-formed XML.
    """
    root = ET.fromstring(xml_bytes)
    sections: list[Section] = []
    position = 0

    abstract_element = _find_first(root, "abstract")
    if abstract_element is not None:
        abstract_text = _collect_text(abstract_element)
        if abstract_text:
            sections.append(
                Section(
                    position=position,
                    section_name="abstract",
                    text=abstract_text,
                    low_confidence=False,
                )
            )
            position += 1

    sections_container = _find_first(root, "sections")
    if sections_container is not None:
        for section_element in _direct_children(sections_container, "section"):
            title_element = _find_first(section_element, "section-title")
            name = _collect_text(title_element) if title_element is not None else ""
            paragraphs = [
                _collect_text(paragraph)
                for paragraph in section_element.iter()
                if _localname(paragraph.tag) == "para"
            ]
            text = "\n\n".join(paragraph for paragraph in paragraphs if paragraph)
            if not text:
                continue
            sections.append(
                Section(
                    position=position,
                    section_name=(name or "section").casefold(),
                    text=text,
                    low_confidence=False,
                )
            )
            position += 1

    return tuple(sections)


def extract_pdf(path: Path) -> tuple[Section, ...]:
    """Extract one section per page from a PDF, flagging pages with no text layer.

    No OCR is attempted (BUILD_PLAN line 148): a page ``pdfplumber`` cannot
    find machine-readable text on (a scanned image, most commonly) gets an
    empty ``text`` and ``low_confidence=True``, so a human reads it instead
    of prismabib inventing content or silently dropping the page.

    Args:
        path: Path to a local PDF file (as written by a resolver into
            ``project.fulltext_dir``, or a manual drop).

    Returns:
        One :class:`Section` per page, in page order, named ``"page_N"``
        (1-indexed, matching how a reader would cite it, e.g. "see page 3").

    Raises:
        pdfplumber.pdfminer.pdfdocument.PDFSyntaxError: If ``path`` is not a
            parseable PDF.
        FileNotFoundError: If ``path`` does not exist.
    """
    sections: list[Section] = []
    with pdfplumber.open(path) as pdf:
        for index, page in enumerate(pdf.pages):
            text = (page.extract_text() or "").strip()
            sections.append(
                Section(
                    position=index,
                    section_name=f"page_{index + 1}",
                    text=text,
                    low_confidence=not text,
                )
            )
    return tuple(sections)


__all__ = ["Section", "extract_pdf", "extract_sciencedirect_xml"]
