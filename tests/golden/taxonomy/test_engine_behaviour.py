"""Golden pins for the taxonomy engine stamp and the audit seed (ADR 0039).

``audit_seed.json`` holds the seed of one fixed fixture at engine 1 and at
engine 2 (the current engine). The engine-1 value was computed by the pre-ADR
formula on ``main`` before ``engine_behaviour`` existed and must stay
byte-identical; the engine-2 value was checked independently with
:mod:`hashlib`. A clean clone on another machine must
reproduce both (Stage 11's cross-machine criterion).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismabib.taxonomy import TAXONOMY_ENGINE_BEHAVIOUR
from prismabib.taxonomy.coder import code
from prismabib.taxonomy.review import audit_sample_seed
from prismabib.taxonomy.schema import load_dimensions
from tests.bibliometrics_helpers import include_everything, open_corpus
from tests.taxonomy_helpers import (
    TaxonomyRecordSpec,
    build_taxonomy_project,
    load_rule_files,
    write_dimensions,
    write_rule_file,
)

_SNAPSHOT = json.loads(
    (Path(__file__).parent / "__snapshots__" / "audit_seed.json").read_text(encoding="utf-8")
)


@pytest.mark.golden
def test_engine_behaviour__constant__is_two_for_case_insensitive_matching() -> None:
    # ADR 0039: 1 was case-sensitive; 2 compiles every pattern with re.IGNORECASE.
    assert TAXONOMY_ENGINE_BEHAVIOUR == 2


@pytest.mark.golden
@pytest.mark.parametrize("engine", ["1", "2"])
def test_audit_sample_seed__fixed_fixture__matches_snapshot(engine: str) -> None:
    seed = audit_sample_seed(**_SNAPSHOT["inputs"], engine_behaviour=int(engine))

    assert seed == _SNAPSHOT["seed_by_engine_behaviour"][engine]


#: One probe, two patterns: ``transformer`` is written in lower case only and
#: ``GAN`` opts out of the default with a scoped inline flag.
_PROBE_RULES = r"""version: 1.0.0
dimension: architecture
counting_unit: papers
categories:
  - id: transformer
    any:
      - {field: title, pattern: '\btransformer\b'}
  - id: gan
    any:
      - {field: title, pattern: '(?-i:GAN)'}
"""

_PROBE_TITLES = (
    "Transformer Networks",
    "TRANSFORMER",
    "transformer",
    "GAN training",
    "gan training",
)


# ADR 0039 Question 5 / ADR 0023 Decision 7 exception: this pins the engine's default
# case policy through code() on a fixed probe, not the `re` module.
@pytest.mark.golden
def test_code__default_matching_policy__case_insensitive_with_scoped_opt_out(
    tmp_path: Path,
) -> None:
    specs = [TaxonomyRecordSpec(number=i, title=t) for i, t in enumerate(_PROBE_TITLES, start=1)]
    project = build_taxonomy_project(tmp_path, specs, slug="golden-policy")
    include_everything(project)
    write_dimensions(project)
    write_rule_file(project, "architecture", _PROBE_RULES)
    schema = load_dimensions(project)
    rule_files = load_rule_files(project, schema, "architecture")

    result = code(open_corpus(project), rule_files, [])

    by_title = {
        spec.title: sorted(a.category for a in result.assignments if a.record_id == spec.record_id)
        for spec in specs
    }
    assert by_title == {
        "Transformer Networks": ["transformer"],
        "TRANSFORMER": ["transformer"],
        "transformer": ["transformer"],
        "GAN training": ["gan"],
        "gan training": [],
    }
