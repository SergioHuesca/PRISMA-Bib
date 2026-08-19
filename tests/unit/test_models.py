"""Unit tests for ``src/prismabib/models.py`` (BUILD_PLAN Stage 1 Tests table, lines 731-744).

Pure-logic behaviour of :class:`~prismabib.models.Record` and
:class:`~prismabib.models.Affiliation` -- no I/O, no filesystem. The
provenance round trip (``resolve()``, real file I/O) lives in
``tests/integration/test_models.py`` instead, per §3.7.2's kind/dir split.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError
from structlog.testing import capture_logs

from prismabib.models import Affiliation, Record
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
