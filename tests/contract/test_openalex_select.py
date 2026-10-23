"""Contract test: the OpenAlex select carries the registration fields (ADR 0032 Decision 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismabib.capture import registrations as reg
from prismabib.sources import openalex
from tests.store_helpers import write_sealed_openalex_run

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "openalex" / "work_registration.json"
OLD = "id,doi,title,publication_year,abstract_inverted_index,authorships,topics"
RUN = "20260101T000000Z-aaaaaaaa"
FILE = f"openalex/{RUN}/response-0000.jsonl"


def _write(raw_dir: Path, api_view: str) -> None:
    works = json.loads(FIXTURE.read_text(encoding="utf-8"))["results"]
    dois = [w["doi"].removeprefix("https://doi.org/").lower() for w in works]
    request = {"lookup": "doi_batch", "identifiers": dois, "works": works}
    write_sealed_openalex_run(raw_dir, RUN, [request], api_view=api_view)


@pytest.mark.contract
def test_select__is_the_old_list_plus_exactly_the_three_registration_fields() -> None:
    fields = openalex._SELECT.split(",")

    assert fields == [*OLD.split(","), "type", "primary_location", "primary_topic"]
    assert set(fields) >= reg.REQUIRED_SELECT_FIELDS


@pytest.mark.contract
def test_registration__body_captured_with_the_amended_select__is_accepted(tmp_path: Path) -> None:
    _write(tmp_path, openalex._SELECT)

    stored = reg.read_response(tmp_path, FILE)

    assert reg.classify_work(stored.works[0])


@pytest.mark.contract
def test_registration__body_captured_without_the_new_fields__says_to_recapture(
    tmp_path: Path,
) -> None:
    _write(tmp_path, OLD)

    with pytest.raises(reg.RegistrationRefusal, match=r"Re-capture .* amended select"):
        reg.read_response(tmp_path, FILE)
