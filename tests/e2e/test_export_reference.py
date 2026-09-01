"""Full-pipeline export golden: Layer 0 → store → decisions → ``exports/``.

BUILD_PLAN §Stage 10's last test row. Everything upstream has its own golden;
this asserts that the *bundle a manuscript is built from* is what the pipeline
produces, which is the last place the §1.4 failure can be caught before a
number reaches a page.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from prismabib.report.export import VOLATILE_MANIFEST_KEYS, export_project
from prismabib.store.load import build_store
from tests.prisma_helpers import (
    copy_reference_project_with_criteria,
    reference_golden,
    screen_reference_project,
)

_GOLDEN_NUMBERS = (
    Path(__file__).parent.parent / "golden" / "report" / "__snapshots__" / "reference_numbers.json"
)


def exported_reference(tmp_path: Path) -> tuple[Path, dict[str, object]]:
    """Run the whole pipeline and export (helper, not a test)."""
    project = copy_reference_project_with_criteria(tmp_path)
    build_store(project, rebuild=True)
    screen_reference_project(project)
    result = export_project(project)
    return result.root, result.numbers


@pytest.mark.e2e
def test_export__reference_project__matches_golden_numbers_json(tmp_path: Path) -> None:
    """The exported numbers are the golden numbers, through the real pipeline.

    Read back off disk rather than taken from the returned object: what a
    manuscript is filled from is the *file*, and a serialiser that dropped or
    reformatted a value would not be caught by comparing in-memory mappings.
    """
    root, _ = exported_reference(tmp_path)

    on_disk = json.loads((root / "numbers.json").read_text(encoding="utf-8"))

    assert on_disk == json.loads(_GOLDEN_NUMBERS.read_text(encoding="utf-8"))


@pytest.mark.e2e
def test_export__numbers_json__agrees_with_the_flow_counts_golden(tmp_path: Path) -> None:
    """The export cannot drift from the PRISMA golden the rest of the suite pins.

    Two goldens describing the same corpus could disagree after an
    unrelated change; this ties them together so that they cannot.
    """
    import dataclasses

    _, numbers = exported_reference(tmp_path)
    golden = reference_golden()

    for field in dataclasses.fields(golden):
        value = getattr(golden, field.name)
        if isinstance(value, int):
            assert numbers[f"flow.{field.name}"] == value, field.name


@pytest.mark.e2e
def test_export__run_twice__numbers_json_identical(tmp_path: Path) -> None:
    """Two exports of the same corpus produce byte-identical numbers.

    Stage 11 asks for this across machines; asserting it across runs here is
    the cheapest place to catch the things that would break it -- a timestamp
    in the wrong file, a set iterated instead of sorted, a float formatted
    from an unstable aggregate.
    """
    first_root, _ = exported_reference(tmp_path / "one")
    second_root, _ = exported_reference(tmp_path / "two")

    assert (first_root / "numbers.json").read_bytes() == (second_root / "numbers.json").read_bytes()


@pytest.mark.e2e
def test_export__manifest__differs_only_in_volatile_keys_between_runs(tmp_path: Path) -> None:
    """The manifest is reproducible apart from an explicit, reviewed allowlist.

    BUILD_PLAN requires any key added to that allowlist to carry a
    reviewer's justification, because every entry is a value that stops being
    compared. Asserting the *complement* is what keeps the list honest: a new
    volatile field cannot be waved through without appearing here.
    """
    first_root, _ = exported_reference(tmp_path / "one")
    second_root, _ = exported_reference(tmp_path / "two")

    first = json.loads((first_root / "manifest.json").read_text(encoding="utf-8"))
    second = json.loads((second_root / "manifest.json").read_text(encoding="utf-8"))

    assert set(first) == set(second)
    for key in first:
        if key in VOLATILE_MANIFEST_KEYS:
            continue
        assert first[key] == second[key], f"{key} is not reproducible but is not declared volatile"


@pytest.mark.e2e
def test_export__figures_and_tables__are_all_written(tmp_path: Path) -> None:
    """The bundle a manuscript needs is complete, not partially written."""
    root, _ = exported_reference(tmp_path)

    assert (root / "figures" / "prisma_flow.svg").is_file()
    assert (root / "figures" / "prisma_flow.csv").is_file()
    assert (root / "manifest.json").is_file()
    for slug in ("eligibility_criteria", "top_venues", "citation_statistics", "top_cited"):
        for suffix in ("csv", "md", "tex"):
            assert (root / "tables" / f"{slug}.{suffix}").is_file(), f"{slug}.{suffix} missing"
