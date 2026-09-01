"""Golden snapshots for ``numbers.json``: the key set, and the reference values.

BUILD_PLAN §Stage 10 asks for the key set to be snapshotted so that *"adding a
key is a reviewable diff"*. That is the point: a manuscript cites keys, so a
key appearing or vanishing changes what a manuscript can say, and it should
never happen as a side effect of an unrelated change.

§5 risk 11 applies with full force here. Neither snapshot is regenerated to
make a failing test pass -- both are plain JSON, hand-reviewable, and a PR that
changes one must say which deliberate change moved it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismabib.project import Project
from prismabib.report.numbers import numbers_map
from prismabib.store.load import build_store
from tests.prisma_helpers import (
    copy_reference_project_with_criteria,
    screen_reference_project,
)

_KEYS_PATH = Path(__file__).parent / "__snapshots__" / "numbers_keys.json"
_VALUES_PATH = Path(__file__).parent / "__snapshots__" / "reference_numbers.json"


def reference_export_project(tmp_path: Path) -> Project:
    """The reference fixture, built and screened (helper, not a test).

    Screened, not merely built. ``reference_golden()`` describes the fixture
    *after* its recorded decisions are applied, so a snapshot taken from an
    unscreened copy would report ``included = 0`` against a golden that says
    5 -- which is how the first draft of these snapshots was wrong, and what
    cross-checking against that golden caught.
    """
    project = copy_reference_project_with_criteria(tmp_path)
    build_store(project, rebuild=True)
    screen_reference_project(project)
    return project


@pytest.mark.golden
def test_numbers_json__keys__are_stable_across_runs(tmp_path: Path) -> None:
    """The key set is a contract with every manuscript that cites it.

    A key that disappears breaks `fill` for any paper citing it; a key that
    appears unnoticed is a number nobody decided to publish. Both are visible
    here as a line-by-line diff.
    """
    project = reference_export_project(tmp_path)

    keys = sorted(numbers_map(project))

    expected = json.loads(_KEYS_PATH.read_text(encoding="utf-8"))
    assert keys == expected


@pytest.mark.golden
def test_numbers_json__reference_project__matches_golden_values(tmp_path: Path) -> None:
    """Every value, not just every key.

    The key-set test above passes while every number is wrong. This is the
    one that would fail if a query, a filter or a fold changed what the
    reference corpus reports -- which is the §1.4 failure caught at the last
    place it can still be caught before publication.
    """
    project = reference_export_project(tmp_path)

    numbers = numbers_map(project)

    expected = json.loads(_VALUES_PATH.read_text(encoding="utf-8"))
    assert numbers == expected


@pytest.mark.golden
def test_numbers_json__every_value__is_scalar_and_json_serialisable(tmp_path: Path) -> None:
    """No nested structure may leak into prose substitution.

    A list or a mapping has no sensible rendering inside a sentence, and
    ``fill`` would happily substitute its ``repr``. Asserted on a real
    project rather than a constructed mapping, so it covers whatever the
    queries actually return.
    """
    project = reference_export_project(tmp_path)

    numbers = numbers_map(project)

    for key, value in numbers.items():
        assert isinstance(value, (bool, int, float, str)), f"{key} is {type(value).__name__}"
    # Round-trips without a custom encoder, which is the operational form of
    # "JSON-serialisable" -- `json.dumps` with a default= would hide exactly
    # the types this is meant to reject.
    assert json.loads(json.dumps(numbers)) == numbers
