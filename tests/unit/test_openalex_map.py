"""Unit tests for :mod:`prismabib.openalex_map` (ADR 0032, Decision 3)."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from prismabib import openalex_map as m
from prismabib.asjc import area_abbrev
from prismabib.errors import PrismabibError

_ADR = (
    Path(__file__).resolve().parents[2]
    / "docs/architecture/adr/0032-registered-records-table-and-classification-map.md"
)
_PINNED_SHA256 = (
    "801149ebb3c2b6114a54034909f4719d6ab179ba0eceaa53b0f4fa89a03d20f1"  # pragma: allowlist secret
)


def _adr_text(start: str, end: str) -> str:
    text = _ADR.read_text(encoding="utf-8")
    return text[text.index(start) : text.index(end)]


@pytest.mark.unit
def test_subfield_table__shape__252_rows_239_exact_13_qualifier() -> None:
    rows = m.SUBFIELD_TO_ASJC
    assert len(rows) == 252
    assert sum(r.exact for r in rows.values()) == 239
    assert sum(not r.exact for r in rows.values()) == 13
    assert len(m._SUBFIELD_ROWS) == 252  # no duplicate ids collapsed by the dict


@pytest.mark.unit
def test_subfield_table__id_and_code__equal_per_row() -> None:
    assert all(sid == r.code for sid, r in m.SUBFIELD_TO_ASJC.items())


@pytest.mark.unit
def test_subfield_table__every_row__exposes_asjc_name() -> None:
    assert all(r.asjc_name for r in m.SUBFIELD_TO_ASJC.values())
    assert m.subfield_to_asjc(1702).asjc_name == "Artificial Intelligence"
    assert m.subfield_to_asjc(1204).asjc_name == "Archeology (arts and humanities)"


@pytest.mark.unit
def test_map_provenance__returns_version_and_pinned_digest() -> None:
    p = m.map_provenance()
    assert (p.subject_map_version, p.subject_map_sha256) == ("1", _PINNED_SHA256)


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "https://evil/x/1702",
        "https://evil.org/subfields/1702",
        "http://openalex.org/subfields/1702",
        "https://openalex.org/subfields/1702/x",
        "https://openalex.org/works/1702",
        "1702/",
        "",
        "17x2",
        "abc",
        -1702,
        "-1702",
        True,
        "１７０２",  # noqa: RUF001 (fullwidth digits)
    ],
)
def test_subfield_to_asjc__malformed_or_foreign__is_refused(raw: str | int) -> None:
    with pytest.raises(m.ClassificationRefusal) as exc:
        m.subfield_to_asjc(raw)
    assert str(exc.value) == f"subfield {raw!r} is not in classification map version 1"


@pytest.mark.unit
def test_subfield_to_asjc__url_with_trailing_slash__maps() -> None:
    assert m.subfield_to_asjc("https://openalex.org/subfields/1702/").code == "1702"


@pytest.mark.unit
def test_primary_topic_to_subject_area__foreign_prefix__is_refused() -> None:
    with pytest.raises(m.ClassificationRefusal):
        m.primary_topic_to_subject_area({"subfield": {"id": "https://evil/x/1702"}})


@pytest.mark.unit
def test_subfield_table__every_code__is_known_to_asjc_matcher() -> None:
    assert all(area_abbrev(r.code)[1] for r in m.SUBFIELD_TO_ASJC.values())


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "code", "exact"),
    [
        (1702, "1702", True),
        ("1204", "1204", False),  # Archeology (arts and humanities)
        ("https://openalex.org/subfields/2737", "2737", False),
    ],
)
def test_subfield_to_asjc__known_id__returns_code_and_flag(
    raw: str | int, code: str, exact: bool
) -> None:
    got = m.subfield_to_asjc(raw)
    assert (got.code, got.exact) == (code, exact)


@pytest.mark.unit
def test_subfield_to_asjc__unknown_id__is_refused_naming_value() -> None:
    with pytest.raises(m.ClassificationRefusal) as exc:
        m.subfield_to_asjc("9999")
    assert str(exc.value) == "subfield '9999' is not in classification map version 1"


@pytest.mark.unit
def test_type_table__shape__25_rows_8_mapped_17_refused() -> None:
    t = m.TYPE_TO_DOC_TYPE
    assert len(t) == 25
    assert sum(v is not None for v in t.values()) == 8
    assert sum(v is None for v in t.values()) == 17


@pytest.mark.unit
@pytest.mark.parametrize(
    ("work_type", "doc_type"),
    [
        ("article", "article"),
        ("review", "review"),
        ("editorial", "editorial"),
        ("letter", "letter"),
        ("erratum", "erratum"),
        ("book", "book"),
        ("book-chapter", "book chapter"),
        ("conference-paper", "conference paper"),
    ],
)
def test_work_type_to_doc_type__mapped__returns_description(work_type: str, doc_type: str) -> None:
    assert m.work_type_to_doc_type(work_type) == doc_type


@pytest.mark.unit
@pytest.mark.parametrize(
    "work_type",
    [t for t, v in m._TYPE_ROWS if v is None] + ["not-a-type", ""],
)
def test_work_type_to_doc_type__refused__names_value_and_version(work_type: str) -> None:
    with pytest.raises(m.ClassificationRefusal) as exc:
        m.work_type_to_doc_type(work_type)
    assert str(exc.value) == f"type {work_type!r} is not in classification map version 1"


@pytest.mark.unit
def test_work_type_to_doc_type__preprint__matches_adr_example_message() -> None:
    with pytest.raises(
        m.ClassificationRefusal, match=r"^type 'preprint' is not in classification map version 1$"
    ):
        m.work_type_to_doc_type("preprint")


@pytest.mark.unit
def test_refusal__type__is_a_prismabib_error() -> None:
    assert issubclass(m.ClassificationRefusal, PrismabibError)


@pytest.mark.unit
def test_source_type_table__rows__match_adr_seven() -> None:
    assert m.SOURCE_TYPE_TO_VENUE_TYPE == {
        "journal": "journal",
        "conference": "conference",
        "ebook platform": "book",
        "book series": "book",
        "repository": "other",
        "metadata": "other",
        "other": "other",
    }


@pytest.mark.unit
def test_source_type_to_venue_type__every_row__maps() -> None:
    for src, venue in m.SOURCE_TYPE_TO_VENUE_TYPE.items():
        assert m.source_type_to_venue_type(src) == venue


@pytest.mark.unit
@pytest.mark.parametrize("bad", ["podcast", "", None])
def test_source_type_to_venue_type__outside_table_or_missing__is_refused(bad: str | None) -> None:
    with pytest.raises(m.ClassificationRefusal) as exc:
        m.source_type_to_venue_type(bad)
    assert str(exc.value) == f"source type {bad!r} is not in classification map version 1"


@pytest.mark.unit
def test_primary_topic_to_subject_area__uses_primary_topic_subfield() -> None:
    topic = {"id": "T1", "subfield": {"id": "https://openalex.org/subfields/1702"}}
    assert m.primary_topic_to_subject_area(topic) == m.SubjectArea(
        "1702", True, "Artificial Intelligence"
    )


@pytest.mark.unit
@pytest.mark.parametrize("topic", [None, {}, {"subfield": None}, {"subfield": {}}])
def test_primary_topic_to_subject_area__no_subfield__is_refused(topic: dict | None) -> None:
    with pytest.raises(m.ClassificationRefusal, match=r"primary_topic\.subfield\.id is missing"):
        m.primary_topic_to_subject_area(topic)


@pytest.mark.unit
def test_primary_topic_to_subject_area__unmapped_subfield__is_refused() -> None:
    with pytest.raises(m.ClassificationRefusal, match=r"subfield '1' is not in classification"):
        m.primary_topic_to_subject_area({"subfield": {"id": "1"}})


@pytest.mark.unit
def test_map_version__is_1() -> None:
    assert m.MAP_VERSION == "1"


@pytest.mark.unit
def test_map_sha256__equals_recomputed_digest_and_pinned_value() -> None:
    assert m.compute_map_sha256() == m.MAP_SHA256 == _PINNED_SHA256


@pytest.mark.unit
def test_map_sha256__canonical_form__sorted_compact_utf8() -> None:
    blob = json.dumps(
        m.canonical_tables(), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    assert blob.startswith('{"SOURCE_TYPE_TO_VENUE_TYPE":{"book series":"book"')
    assert hashlib.sha256(blob.encode("utf-8")).hexdigest() == m.MAP_SHA256


@pytest.mark.unit
def test_map_sha256__table_edit__changes_digest(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(m, "_SOURCE_ROWS", (*m._SOURCE_ROWS, ("x", "other")))
    assert m.compute_map_sha256() != m.MAP_SHA256


@pytest.mark.unit
def test_subfield_table__against_adr_markdown__identical() -> None:
    block = _adr_text("**(a) Subfield to ASJC", "**Unmapped rule.**")
    pat = re.compile(r"^\| (\d+) \| (.+) \| (\d+) \| (.+) \|$", re.MULTILINE)
    expected = {}
    for sid, name, code, match in pat.findall(block):
        if match == "exact":
            expected[sid] = (name, code, True, None)
        else:
            quoted = re.fullmatch(r'qualifier: ASJC "(.+?)"( \(case\))?', match)
            assert quoted, match
            expected[sid] = (name, code, False, quoted.group(1))
    assert len(expected) == 252
    actual = {sid: (name, code, ex, an) for sid, name, code, ex, an in m._SUBFIELD_ROWS}
    assert actual == expected
    assert [r[0] for r in m._SUBFIELD_ROWS] == list(expected)  # ADR (API) order


@pytest.mark.unit
def test_type_table__against_adr_markdown__identical() -> None:
    block = _adr_text("**(b) Work type", "**(c) Source type")
    expected: dict[str, str | None] = {}
    for line in block.splitlines():
        if not line.startswith("| ") or line.startswith(("| OpenAlex", "|---")):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        keys = re.findall(r"`([^`]+)`", cells[0])
        target = None if cells[1] == "(none)" else cells[1].strip("`")
        for k in keys:
            expected[k] = target
    assert expected == m.TYPE_TO_DOC_TYPE
    assert len(expected) == 25


@pytest.mark.unit
def test_source_table__against_adr_markdown__identical() -> None:
    block = _adr_text("**(c) Source type", "**Language: no table.**")
    expected = dict(re.findall(r"^\| `([^`]+)` \| `([^`]+)` \| judgement", block, re.MULTILINE))
    assert expected == m.SOURCE_TYPE_TO_VENUE_TYPE
    assert len(expected) == 7
