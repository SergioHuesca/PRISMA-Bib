"""Contract tests against the sanitised/modelled Stage 6 cassette (ADR 0019).

BUILD_PLAN §3.7.2: a contract test asserts the *shape* of an upstream
payload prismabib depends on. See ``tests/fixtures/README.md``'s
"``sciencedirect-article-full-modelled.xml`` is modelled too" section for
what these two tests can and cannot claim: the cassette is modelled on
Elsevier's documented response shape, not recorded live, so these pin this
project's belief about the shape rather than verifying it against a real
response.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from prismabib.fulltext.extract import extract_sciencedirect_xml

_CASSETTE_PATH = (
    Path(__file__).parent.parent
    / "fixtures"
    / "cassettes"
    / "sciencedirect-article-full-modelled.xml"
)


def _load_cassette() -> bytes:
    return _CASSETTE_PATH.read_bytes()


@pytest.mark.contract
def test_contract__sciencedirect_article_retrieval__required_fields_present() -> None:
    """The envelope carries the identity and structure prismabib depends on.

    ``prism:doi`` and ``dc:title`` are what ties a fetched asset back to the
    record that requested it; the ``ce:abstract``/``ce:sections`` structure
    is what :func:`~prismabib.fulltext.extract.extract_sciencedirect_xml`
    depends on existing at all.
    """
    root = ET.fromstring(_load_cassette())

    namespaces = {
        "prism": "http://prismstandard.org/namespaces/basic/2.0/",
        "dc": "http://purl.org/dc/elements/1.1/",
    }
    coredata = root.find("{http://www.elsevier.com/xml/svapi/article/dtd}coredata")
    assert coredata is not None

    doi = coredata.find("prism:doi", namespaces)
    assert doi is not None
    assert doi.text and doi.text.startswith("10.")

    title = coredata.find("dc:title", namespaces)
    assert title is not None
    assert title.text

    local_names = {element.tag.rsplit("}", 1)[-1] for element in root.iter()}
    assert "abstract" in local_names
    assert "sections" in local_names
    assert "section" in local_names
    assert "section-title" in local_names
    assert "para" in local_names


@pytest.mark.contract
def test_extract__sciencedirect_xml__yields_expected_sections() -> None:
    """Section names and ordering, from the cassette -- the extractor's own contract."""
    sections = extract_sciencedirect_xml(_load_cassette())

    assert [section.section_name for section in sections] == [
        "abstract",
        "introduction",
        "methods",
        "results",
    ]
    assert [section.position for section in sections] == [0, 1, 2, 3]
    assert all(section.low_confidence is False for section in sections)
    assert all(section.text for section in sections)
