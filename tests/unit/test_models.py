"""Unit tests for ``src/prismabib/models.py`` (BUILD_PLAN Stage 1 Tests table, lines 731-744).

Pure-logic behaviour of :class:`~prismabib.models.Record` and
:class:`~prismabib.models.Affiliation` -- no I/O, no filesystem. The
provenance round trip's success path (``resolve()``, real file I/O) lives in
``tests/integration/test_models.py`` instead, per §3.7.2's kind/dir split;
``resolve()``'s two failure branches are added below alongside the rest of
this coverage follow-up, since exercising them needs no real fixture file
beyond what ``tmp_path`` already gives a unit test.

Coverage follow-up (orchestrator correction): brings forward the two Stage 3
dedup tests named at BUILD_PLAN lines 917-918 (they test Stage 1-shipped
functions per §3.2 line 374 and claim no acceptance criterion -- Stage 3's
own AC1-4 are unrelated), plus a `normalise_doi` parametrisation, the
`_coerce_afid_scalar` validator (distinct from the already-covered
`_coerce_affiliations_scalar`), and `PayloadRef.resolve`'s two failure
branches.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError
from structlog.testing import capture_logs

from prismabib.models import Affiliation, Author, PayloadRef, Record, dedup_key, normalise_doi
from tests.factories import RecordFactory


def _valid_record_kwargs() -> dict[str, Any]:
    """A complete, valid set of :class:`Record` constructor kwargs.

    Built from the schema-derived factory (never a hand-rolled field list,
    per §3.7.4) so the "missing required field" test below is exercising the
    model's own required-ness, not a stale copy of it.
    """
    return RecordFactory.build().model_dump(mode="python")


@pytest.mark.unit
def test_record__scalar_affiliation__coerces_to_list() -> None:
    scalar_affiliation = {"afid": None, "name": "MIT", "city": "Cambridge", "country": "USA"}

    record = RecordFactory.build(affiliations=scalar_affiliation)

    assert record.affiliations == [Affiliation(**scalar_affiliation)]


@pytest.mark.unit
def test_record__missing_required_title__raises_validation_error() -> None:
    kwargs = _valid_record_kwargs()
    del kwargs["title"]

    with pytest.raises(PydanticValidationError):
        Record(**kwargs)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Korea, Republic of", "KOR"),
        ("South Korea", "KOR"),
        ("Viet Nam", "VNM"),
        ("USA", "USA"),
        ("U.S.A.", "USA"),
    ],
)
def test_affiliation__country_variants__normalise_to_iso3(raw: str, expected: str) -> None:
    affiliation = Affiliation(afid=None, name="Test Institute", city=None, country=raw)

    assert affiliation.country == expected


@pytest.mark.unit
def test_affiliation__unknown_country__is_preserved_and_flagged() -> None:
    unmapped_country = "Wakanda"

    with capture_logs() as logs:
        affiliation = Affiliation(
            afid=None, name="Test Institute", city=None, country=unmapped_country
        )

    assert affiliation.country == unmapped_country
    assert any(
        entry["event"] == "affiliation.country.unmapped" and entry["raw"] == unmapped_country
        for entry in logs
    )


@pytest.mark.unit
def test_affiliation__scalar_afid_list__coerces_to_first_element() -> None:
    affiliation = Affiliation(
        afid=["60001", "60002"], name="Test Institute", city=None, country=None
    )

    assert affiliation.afid == "60001"


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        "10.1000/Example.DOI",
        "https://doi.org/10.1000/Example.DOI",
        "http://dx.doi.org/10.1000/Example.DOI",
        "doi:10.1000/Example.DOI",
        "DOI: 10.1000/Example.DOI",
        "  10.1000/Example.DOI  ",
    ],
)
def test_normalise_doi__url_and_scheme_forms__collapse_to_bare_lowercase(raw: str) -> None:
    normalised = normalise_doi(raw)

    assert normalised == "10.1000/example.doi"


@pytest.mark.unit
def test_dedup_key__same_doi_different_case__collides() -> None:
    record_a = RecordFactory.build(doi="10.1000/Example.DOI")
    record_b = RecordFactory.build(doi="https://doi.org/10.1000/EXAMPLE.doi")

    assert dedup_key(record_a) == dedup_key(record_b)


@pytest.mark.unit
def test_dedup_key__no_doi__falls_back_to_title_author_year() -> None:
    first_author = Author(author_id=None, surname="Smith", given_name="Jane", initials="J.")
    record = RecordFactory.build(doi=None, title="  A   Study  ", year=2021, authors=[first_author])

    key = dedup_key(record)

    assert key == ("fallback", "a study", "smith", 2021)


@pytest.mark.unit
def test_dedup_key__no_doi_no_authors__raises_value_error() -> None:
    record = RecordFactory.build(doi=None, authors=[])

    with pytest.raises(ValueError, match=re.escape(record.record_id)):
        dedup_key(record)


@pytest.mark.unit
def test_payload_ref__missing_file__raises_file_not_found_error(tmp_path: Path) -> None:
    payload_ref = PayloadRef(path=tmp_path / "does-not-exist.jsonl", line=0)

    with pytest.raises(FileNotFoundError):
        payload_ref.resolve()


@pytest.mark.unit
def test_payload_ref__line_out_of_range__raises_index_error(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    raw_path.write_text('{"id": 0}\n', encoding="utf-8")
    payload_ref = PayloadRef(path=raw_path, line=5)

    with pytest.raises(IndexError):
        payload_ref.resolve()
