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
