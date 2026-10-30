"""Golden pins for the taxonomy engine stamp and the audit seed (ADR 0039).

``audit_seed.json`` holds the seed of one fixed fixture at engine 1 and at
engine 2. The engine-1 value was computed by the pre-ADR formula on ``main``
before ``engine_behaviour`` existed; the engine-2 value was checked
independently with :mod:`hashlib`. A clean clone on another machine must
reproduce both (Stage 11's cross-machine criterion).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismabib.taxonomy import TAXONOMY_ENGINE_BEHAVIOUR
from prismabib.taxonomy.review import audit_sample_seed

_SNAPSHOT = json.loads(
    (Path(__file__).parent / "__snapshots__" / "audit_seed.json").read_text(encoding="utf-8")
)


@pytest.mark.golden
def test_engine_behaviour__constant__is_one_until_matching_changes() -> None:
    # Step 1 of ADR 0039 changes no behaviour; PR #227 bumps this to 2 with its matching change.
    assert TAXONOMY_ENGINE_BEHAVIOUR == 1


@pytest.mark.golden
@pytest.mark.parametrize("engine", ["1", "2"])
def test_audit_sample_seed__fixed_fixture__matches_snapshot(engine: str) -> None:
    seed = audit_sample_seed(**_SNAPSHOT["inputs"], engine_behaviour=int(engine))

    assert seed == _SNAPSHOT["seed_by_engine_behaviour"][engine]
