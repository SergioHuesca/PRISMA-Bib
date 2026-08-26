"""The Stage 4 end-to-end run (BUILD_PLAN §Stage 4, line 1055).

BUILD_PLAN names one test here:
``test_e2e__reference_project__flow_counts_match_published_golden`` "runs
Layer 0 → ``FlowCounts`` on the frozen fixture". This module is that test,
and the ``e2e`` CI job of §3.7.7 (``pytest -m e2e``) is what runs it.

**What this adds over the integration suite.** Stage 4's integration tests
each exercise one module against a project some fixture has already prepared;
this one starts where a real review starts -- a sealed Layer 0 capture and a
``criteria.yaml``, with **no Layer 1 store in existence** -- and drives every
layer in order:

``raw/<run_id>/page-*.jsonl`` + ``manifest.json``
→ :func:`~prismabib.store.load.build_store` (Layer 1)
→ :func:`~prismabib.prisma.engine.language_set` (``S_raw`` → ``A`` → ``L``)
→ :class:`~prismabib.prisma.log.DecisionLog` appends (Layer 2)
→ :func:`~prismabib.prisma.flow.compute_flow_counts`
→ :meth:`~prismabib.store.load.Corpus.records` reading back the same corpus.

The assertion that makes it an *end-to-end* test rather than a second copy of
``test_flow_counts__reference_fixture__matches_golden`` is the last link:
the number ``FlowCounts`` reports as ``included`` and the rows Stage 3's
frozen ``Corpus`` contract hands back for
:attr:`~prismabib.stage.PrismaStage.INCLUDED` must be the same records, not
merely the same count. A defect in the Layer 1 → Layer 2 delegation that
Stage 3 and Stage 4 each test in isolation -- each against its own idea of
what the other returns -- would show up only here.

The golden is imported, never restated: :func:`tests.prisma_helpers.reference_golden`
is the single definition, shared with the integration suite (§3.7.5 -- a
snapshot with two sources of truth can drift, and then neither is obviously
the wrong one).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.prisma import engine
from prismabib.prisma.flow import compute_flow_counts
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus, build_store
from tests.prisma_helpers import (
    REFERENCE_IDENTIFIED,
    copy_reference_project_with_criteria,
    reference_golden,
    screen_reference_project,
)


@pytest.mark.e2e
@pytest.mark.acceptance("S04-AC4")
def test_e2e__reference_project__flow_counts_match_published_golden(tmp_path: Path) -> None:
    project = copy_reference_project_with_criteria(tmp_path, slug="vad-2026")
    assert not project.db_path.exists()

    stats = build_store(project, rebuild=True)
    screen_reference_project(project)
    counts = compute_flow_counts(project)

    corpus = Corpus.open(project)
    included_rows = corpus.records(stage=PrismaStage.INCLUDED)
    raw_rows = corpus.records(stage=PrismaStage.RAW)

    assert stats.records_loaded == raw_rows.height == REFERENCE_IDENTIFIED
    assert counts == reference_golden()
    assert counts.assert_consistent() is None
    assert set(included_rows["record_id"].to_list()) == engine.corpus(project)
    assert included_rows.height == counts.included
