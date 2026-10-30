"""The one title normaliser is shared by candidates and OpenAlex (ADR 0031 note)."""

from __future__ import annotations

import pytest

from prismabib.capture import candidates
from prismabib.capture.candidates import candidate_id
from prismabib.sources import openalex
from prismabib.titles import normalise_title

_TITLES = [
    "Well-being: Études",
    "Café-au-lait",
    "Naïve Bayes \u2013 a survey",
    "Don't panic (really)",
]


@pytest.mark.unit
def test_normalise_title__candidates_and_openalex__are_the_shared_function() -> None:
    assert candidates.normalise_title is normalise_title
    assert openalex.normalise_title is normalise_title


@pytest.mark.unit
@pytest.mark.parametrize("title", _TITLES)
def test_normalise_title__table__both_public_names_agree(title: str) -> None:
    assert candidates.normalise_title(title) == openalex.normalise_title(title)


@pytest.mark.unit
def test_normalise_title__well_being_études__matches_well_being_etudes() -> None:
    assert normalise_title("Well-being: Études") == normalise_title("Well being: Etudes")
    assert normalise_title("Well-being: Études") == "well being etudes"


# Hard-coded from the code before the normalisers were unified: a changed value here means
# every persisted candidate id changed.
@pytest.mark.unit
@pytest.mark.parametrize(
    ("title", "year", "expected"),
    [
        (
            "Well-being: Études",
            2020,
            "231a569384c869af392e45b7a147abb6",  # pragma: allowlist secret
        ),
        (
            "Café-au-lait",
            2019,
            "16f8465025f62b2a95128f00a58eca65",  # pragma: allowlist secret
        ),
        (
            "Naïve Bayes \u2013 a survey",
            2021,
            "63394012662f1f20c243d08589bf3ac5",  # pragma: allowlist secret
        ),
        (
            "Don't panic (really)",
            2018,
            "69b70c10501e7bf63b43bf4c218a1f38",  # pragma: allowlist secret
        ),
    ],
)
def test_candidate_id__golden__is_unchanged(title: str, year: int, expected: str) -> None:
    assert candidate_id(title, year) == expected
