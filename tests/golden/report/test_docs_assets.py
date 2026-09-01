"""The checked-in example diagram must be a real export, not a stale one.

``docs/assets/prisma-flow-example.svg`` is the image the PR and the docs show
as "what `prismabib export` produces". It was first committed with a message
saying exactly that -- and it had been generated from an *unscreened* copy of
the reference project, so it advertised ``included = 0`` for a review that
included 5.

That is the §1.4 failure in miniature: a plausible wrong number, presented as
authoritative, in the stage built to prevent it. Nothing caught it because a
checked-in asset with no regeneration path is unfalsifiable -- it is a golden
that nobody declared a golden.

This test declares it one. The SVG is a fixed function of its counts (no
timestamps, no font metrics, sorted reasons), which is what makes a
byte comparison the right assertion rather than a brittle one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.report.export import export_project
from prismabib.store.load import build_store
from tests.prisma_helpers import (
    copy_reference_project_with_criteria,
    screen_reference_project,
)

_ASSET = Path(__file__).parent.parent.parent.parent / "docs" / "assets" / "prisma-flow-example.svg"


@pytest.mark.golden
def test_docs_asset__prisma_flow_example__is_a_current_screened_export(tmp_path: Path) -> None:
    """The committed example equals a fresh export of the screened reference project.

    Regenerate it with the snippet in ``docs/testing.md`` when the diagram or
    the fixture deliberately changes, and say which in the PR -- never to make
    this test pass (§5 risk 11).
    """
    project = copy_reference_project_with_criteria(tmp_path)
    build_store(project, rebuild=True)
    screen_reference_project(project)

    result = export_project(project)

    generated = (result.root / "figures" / "prisma_flow.svg").read_text(encoding="utf-8")
    assert _ASSET.read_text(encoding="utf-8") == generated, (
        "docs/assets/prisma-flow-example.svg is stale -- regenerate it from a *screened* "
        "export (docs/testing.md), which is the mistake that shipped included=0 once"
    )
