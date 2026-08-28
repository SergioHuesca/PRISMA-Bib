"""Contract tests against sanitised Scopus cassettes (BUILD_PLAN Stage 2 Tests table, lines 829-830).

BUILD_PLAN §3.7.2: contract tests assert the *shape* of an upstream payload
we depend on, against sanitised cassettes, and "fail when Scopus changes its
response schema." No mocking, no network, no ``ScopusClient`` at all here --
these load a cassette from ``tests/fixtures/cassettes/`` (produced by
``tests/fixtures/sanitise.py``; see ``tests/fixtures/README.md`` for the
recording procedure) and assert directly on its structure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

_CASSETTES_DIR = Path(__file__).parent.parent / "fixtures" / "cassettes"


def _load_cassette(name: str) -> dict[str, Any]:
    with (_CASSETTES_DIR / name).open(encoding="utf-8") as handle:
        return dict(json.load(handle))


@pytest.mark.contract
def test_contract__search_response__has_required_fields() -> None:
    """A ``view=COMPLETE`` page carries every field prismabib's later stages depend on.

    ``dc:identifier``, ``prism:coverDate``, and ``subtypeDescription`` are
    required on *every* entry (they are core bibliographic fields present
    regardless of record type). ``authkeywords`` and
    ``affiliation[].affiliation-country`` are only required on *some* entry
    -- BUILD_PLAN's own cassette (``tests/fixtures/README.md``) documents
    that 3 of this page's 25 entries are proceedings-level records with no
    author list at all, so asserting those two fields on every entry would
    be asserting something false about real Scopus data.
    """
    page = _load_cassette("complete-page-0000.json")

    entries = page["search-results"]["entry"]
    assert entries, "cassette must carry at least one entry to be a useful contract"

    for entry in entries:
        assert "dc:identifier" in entry
        assert "prism:coverDate" in entry
        assert "subtypeDescription" in entry

    entries_with_keywords = [entry for entry in entries if "authkeywords" in entry]
    assert entries_with_keywords, "at least one entry must carry authkeywords"

    entries_with_country = [
        entry
        for entry in entries
        if isinstance(entry.get("affiliation"), list)
        and any("affiliation-country" in affiliation for affiliation in entry["affiliation"])
    ]
    assert entries_with_country, "at least one entry must carry affiliation[].affiliation-country"


@pytest.mark.contract
def test_contract__standard_view_response__lacks_authkeywords() -> None:
    """``view=STANDARD`` never carries ``authkeywords`` -- pinning why COMPLETE is mandatory.

    BUILD_PLAN line 763: STANDARD omits ``authkeywords`` and full
    affiliation data, "which kills the keyword co-occurrence network and
    the geography analysis." This test exists so a future maintainer who
    considers "simplifying" ``ScopusClient`` to use STANDARD (e.g. to save
    quota) breaks a test immediately, rather than silently degrading the
    corpus (BUILD_PLAN §5 risk 1).
    """
    page = _load_cassette("standard-page-0000.json")

    entries = page["search-results"]["entry"]
    assert entries, "cassette must carry at least one entry to be a useful contract"

    entries_with_keywords = [entry for entry in entries if "authkeywords" in entry]
    assert entries_with_keywords == []


@pytest.mark.contract
@pytest.mark.parametrize(
    "cassette",
    [
        pytest.param("complete-page-0000.json", id="page-0"),
        pytest.param("complete-page-0001.json", id="page-1"),
    ],
)
def test_contract__search_complete_response__carries_no_subject_areas(cassette: str) -> None:
    """``view=COMPLETE`` carries **no** subject-area codes -- the reason enrichment exists.

    This is the pinned half of the measurement in
    :mod:`prismabib.capture.enrich`'s module docstring: against a real
    651-record corpus, 0 of 125 sampled entries carried a ``subject-area``
    key. These two cassettes are 50 real ``view=COMPLETE`` entries and not one
    of them has one either.

    Without this test, the Abstract Retrieval call in
    :func:`~prismabib.capture.enrich.capture_abstracts` looks like an
    expensive redundancy -- one HTTP request per record for data a reader
    would reasonably assume the search response already carried -- and the
    obvious "simplification" is to delete it and read the codes off the entry.
    That change would produce an empty ``subject_areas`` table and a
    ``criteria.yaml`` subject filter that silently matched nothing. This test
    is what makes that attempt fail immediately instead.
    """
    page = _load_cassette(cassette)

    entries = page["search-results"]["entry"]
    assert entries, "cassette must carry at least one entry to be a useful contract"

    assert [entry for entry in entries if "subject-area" in entry] == []
    assert [entry for entry in entries if "subject-areas" in entry] == []


@pytest.mark.contract
def test_contract__abstract_response__carries_coded_subject_areas() -> None:
    """An Abstract Retrieval record carries ``subject-area`` triples we can filter on.

    ``@code`` is the ASJC code ``criteria.yaml``'s ``subject_areas`` list is
    matched against; ``@abbrev`` is the four-letter top-level grouping
    (``COMP``, ``ENGI``, ...) and ``$`` the human-readable name. All three are
    asserted because a later stage stores all three: a code with no label is
    unreadable in a report, and a label with no code is unmatchable.
    """
    response = _load_cassette("abstract-full-multi-subject-area.json")

    areas = response["abstracts-retrieval-response"]["subject-areas"]["subject-area"]
    assert isinstance(areas, list)
    assert areas

    for area in areas:
        assert area["@code"]
        assert area["@abbrev"]
        assert area["$"]


@pytest.mark.contract
def test_contract__abstract_response__single_subject_area_is_a_lone_mapping() -> None:
    """One subject area comes back as a mapping, not a one-element list.

    The same scalar-vs-list inconsistency the Search-side fixtures exist to
    pin (``affiliation``, ``afid``). Code that assumes a list here reads
    ``area["@code"]`` off a dict of dicts and either raises or, worse, iterates
    the mapping's *keys* and stores ``"@code"`` as a subject area.
    """
    response = _load_cassette("abstract-full-single-subject-area.json")

    area = response["abstracts-retrieval-response"]["subject-areas"]["subject-area"]

    assert isinstance(area, dict)
    assert area["@code"]


@pytest.mark.contract
def test_contract__abstract_response__may_omit_subject_areas_entirely() -> None:
    """Some records carry no ``subject-areas`` key at all, and that is not an error.

    Conference-review records are the usual case. This is the shape that makes
    :class:`~prismabib.capture.manifest.AbstractUnavailable`'s
    ``"no_subject_areas"`` reason necessary: without it, Layer 1 cannot tell
    "Scopus assigns this record none" from "we never asked about this record",
    and a subject filter would treat the two identically.
    """
    response = _load_cassette("abstract-full-no-subject-areas.json")

    retrieval = response["abstracts-retrieval-response"]

    assert "subject-areas" not in retrieval
    assert retrieval["coredata"]["eid"]


@pytest.mark.contract
def test_contract__abstract_response__coredata_identifies_the_record() -> None:
    """The response carries the identity a payload line has to be keyed back to.

    Payload lines in ``raw/abstracts/<run_id>/`` are stored verbatim, with no
    ``record_id`` envelope wrapped around them, on the argument that identity
    is already in the response. That argument is only true while these two
    fields are present, so it is asserted rather than assumed. ``eid`` is the
    one that matters: :func:`prismabib.store.load._record_id_from_entry` builds
    ``scopus:<eid>`` from the Search side, and the two must agree for the same
    paper.
    """
    response = _load_cassette("abstract-full-multi-subject-area.json")

    coredata = response["abstracts-retrieval-response"]["coredata"]

    assert coredata["eid"].startswith("2-s2.0-")
    assert coredata["dc:identifier"].startswith("SCOPUS_ID:")
    assert coredata["dc:identifier"].removeprefix("SCOPUS_ID:") in coredata["eid"]
