"""ADR 0035 Decision 11: response options come from sealed Layer 0 and agree with the writer."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from prismabib.capture import registration_ui as ui
from prismabib.capture.candidates import CandidateEvent, CandidateRow, CandidateView
from tests.store_helpers import write_sealed_openalex_run

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "openalex" / "work_registration.json"
SELECT = (
    "id,doi,title,publication_year,abstract_inverted_index,authorships,topics,"
    "type,primary_location,primary_topic"
)
TITLE, YEAR = "A Synthetic Registered Article", 2021
RUN = "20260101T000000Z-aaaaaaaa"


def _works() -> list[dict[str, Any]]:
    return copy.deepcopy(json.loads(FIXTURE.read_text(encoding="utf-8"))["results"])


def _candidate(doi: str | None = None) -> CandidateView:
    event = CandidateEvent.from_row(
        CandidateRow(title=TITLE, year=YEAR, doi=doi),
        reviewer="sh",
        pasted_at=datetime(2026, 10, 22, tzinfo=UTC),
    )
    return CandidateView(event=event, doi=doi)


@pytest.mark.integration
def test_response_options__only_exactly_one_match_listed(tmp_path: Path) -> None:
    raw = tmp_path / "raw"
    works = _works()
    one_match, no_match, two_matches = works, works[1:], [works[0], copy.deepcopy(works[0])]
    write_sealed_openalex_run(
        raw,
        RUN,
        [
            {"lookup": "doi_batch", "identifiers": [], "works": w}
            for w in (one_match, no_match, two_matches)
        ],
        api_view=SELECT,
    )
    # Captured before the select amendment: the writer cannot read it, so it is never offered.
    write_sealed_openalex_run(
        raw,
        "20260102T000000Z-bbbbbbbb",
        [{"lookup": "doi_batch", "identifiers": [], "works": works}],
    )

    result = ui.response_options(raw, _candidate(), "title_year")

    assert [(o.label, o.value) for o in result.options] == [
        (f"{RUN}/response-0000", f"openalex/{RUN}/response-0000.jsonl")
    ]
    assert result.options[0].work["title"] == TITLE
    assert (result.unmatched, result.unreadable) == (2, 1)
    assert ui.response_options(tmp_path / "none", _candidate(), "doi") == ui.ResponseOptions(
        (), 0, 0
    )
