"""Unit tests for ``src/prismabib/project.py`` (BUILD_PLAN Stage 1 Tests table, lines 731-744).

Pure-logic behaviour of :class:`~prismabib.project.Criteria` -- no I/O.
``Project``'s filesystem-touching behaviour lives in
``tests/integration/test_project.py`` instead, per §3.7.2's kind/dir split.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from prismabib.project import Criteria

_CRITERIA_WITHOUT_VERSION: dict[str, object] = {
    "temporal": {"year_start": 2016, "year_end": 2026},
    "subject_areas": ["COMP", "ENGI"],
    "doc_types": {"include": ["ar", "cp"]},
    "languages": ["English"],
    "manual_abstract": {"exclude_reason_codes": ["OFF_TOPIC"]},
    "manual_fulltext": {"exclude_reason_codes": ["INACCESSIBLE"]},
}


@pytest.mark.unit
def test_criteria__semantic_version_missing__raises() -> None:
    with pytest.raises(PydanticValidationError):
        Criteria.model_validate(_CRITERIA_WITHOUT_VERSION)


@pytest.mark.unit
@pytest.mark.parametrize("version", ["1.0", "v1.0.0", "1.0.0.0", "abc"])
def test_criteria__non_semantic_version__raises(version: str) -> None:
    criteria = {**_CRITERIA_WITHOUT_VERSION, "version": version}

    with pytest.raises(PydanticValidationError):
        Criteria.model_validate(criteria)


@pytest.mark.unit
@pytest.mark.parametrize("version", ["1.0.0", "10.20.30"])
def test_criteria__valid_semantic_version__is_accepted(version: str) -> None:
    criteria = {**_CRITERIA_WITHOUT_VERSION, "version": version}

    validated = Criteria.model_validate(criteria)

    assert validated.version == version


@pytest.mark.unit
@pytest.mark.parametrize(
    "codes",
    [
        pytest.param(["COMPUTER"], id="a-plausible-misspelling"),
        pytest.param(["COMP", "XXXX"], id="one-good-one-unknown"),
        pytest.param([" XXXX "], id="unknown-even-after-stripping"),
    ],
)
def test_criteria__an_unknown_subject_area_code__is_refused(codes: list[str]) -> None:
    """An unknown code is refused rather than matching nothing.

    A code ASJC does not define cannot intersect any record's subject areas,
    so it would narrow the review to nothing while reading, in the protocol,
    as a deliberate restriction -- and the flow diagram would report the
    whole corpus excluded "by subject area" with no error anywhere. §1.4.
    """
    criteria = {**_CRITERIA_WITHOUT_VERSION, "version": "1.0.0", "subject_areas": codes}

    with pytest.raises(PydanticValidationError, match="unknown subject-area code"):
        Criteria.model_validate(criteria)


@pytest.mark.unit
def test_criteria__a_four_digit_asjc_number__is_refused_as_a_silent_widening() -> None:
    """``1702`` names one category but can only be matched at its grouping.

    Accepting it would silently widen "Artificial Intelligence" to the whole
    of ``COMP`` -- the opposite error from an unknown code, and equally
    invisible in the resulting diagram.
    """
    criteria = {**_CRITERIA_WITHOUT_VERSION, "version": "1.0.0", "subject_areas": ["1702"]}

    with pytest.raises(PydanticValidationError, match="unknown subject-area code"):
        Criteria.model_validate(criteria)


@pytest.mark.unit
def test_criteria__the_asjc_groupings__are_accepted() -> None:
    criteria = {
        **_CRITERIA_WITHOUT_VERSION,
        "version": "1.0.0",
        "subject_areas": ["COMP", "ENGI", "MATH", "MULT"],
    }

    assert Criteria.model_validate(criteria).subject_areas == ["COMP", "ENGI", "MATH", "MULT"]


@pytest.mark.unit
def test_criteria__surrounding_whitespace_on_a_known_code__is_tolerated() -> None:
    """``"comp "`` is a known grouping, not an unknown code.

    The value is kept verbatim; only the *check* strips and upper-cases, the
    same normalisation :func:`prismabib.asjc.area_abbrev` applies when the
    filter runs, so what validates is exactly what matches.
    """
    criteria = {**_CRITERIA_WITHOUT_VERSION, "version": "1.0.0", "subject_areas": ["comp "]}

    assert Criteria.model_validate(criteria).subject_areas == ["comp "]
