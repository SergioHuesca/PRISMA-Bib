"""``prismabib.viz.figures``'s own layering rule and signature contract (BUILD_PLAN §Stage 9).

``test_figures__no_module_performs_arithmetic`` is the load-bearing test of
this stage (ADR 0025 Decision 1): a figure function that computes is a
number with no provenance, no ``params``, no caption obligation. The scan
itself lives in ``tests/no_arithmetic_scan.py`` -- the "no wall clock" guard's
extraction pattern (``tests/no_clock_scan.py``), reused for an identically-
shaped rule.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

import prismabib.viz.figures as figures_module
from prismabib.bibliometrics.base import AnalysisResult, Provenance
from prismabib.stage import PrismaStage
from tests.no_arithmetic_scan import PERMITTED_SNIPPETS, PLANTED_VIOLATIONS, arithmetic_offences

_FIGURES_SOURCE_PATH = Path(figures_module.__file__)

#: Every public figure function that draws one of the nine required
#: figures, in BUILD_PLAN's own order. `prisma_flow_caption` is deliberately
#: excluded -- a caption helper, not a figure function -- and
#: `prisma_flow_figure` is figure 9, whose first parameter is `FlowCounts`,
#: not `AnalysisResult` (ADR 0025 Decision 5: it wraps
#: `report/flow_diagram.py` rather than taking an analysis result at all).
_REQUIRED_FIGURE_NAMES = (
    "trend_figure",
    "geography_figure",
    "venues_figure",
    "keywords_figure",
    "network_figure",
    "citation_distribution_figure",
    "taxonomy_distribution_figure",
    "taxonomy_evolution_figure",
    "prisma_flow_figure",
)

#: Figure 7/8 take a `Sequence[AnalysisResult]` rather than a single one --
#: a disclosed, narrow exception; see `viz/figures.py`'s own module
#: docstring for why.
_SEQUENCE_ARGUMENT_FIGURES = frozenset(
    {"taxonomy_distribution_figure", "taxonomy_evolution_figure"}
)

#: Figure 9 wraps `FlowCounts`, not an `AnalysisResult` (ADR 0025 Decision 5).
_FLOWCOUNTS_ARGUMENT_FIGURES = frozenset({"prisma_flow_figure"})


#: The one `viz/` module the arithmetic ban does not apply to, and why.
#:
#: `theme.py` is the *colour* layer: CIEDE2000, the deuteranopia matrix,
#: WCAG contrast and the 300 dpi legibility formula are all arithmetic, and
#: all of it is over palette constants and counts handed in -- never over
#: `AnalysisResult.data`. Banning arithmetic there would make the module
#: unwritable for no safety benefit, because none of its numbers is a
#: quantitative finding about the corpus.
#:
#: Named as a single file rather than skipped by directory, and pinned by
#: `test_no_arithmetic_scan__exemption__is_exactly_one_module`. A guard whose
#: exemption is unasserted is how this project's INACCESSIBLE guard shipped
#: three separate holes.
_ARITHMETIC_EXEMPT_MODULES = frozenset({"theme.py"})


def _scanned_viz_sources() -> list[Path]:
    """Every `viz/` module the arithmetic ban applies to.

    Returns:
        Sorted paths, `theme.py` excluded. Globbing the package rather than
        naming one file is deliberate: the first version scanned only
        `figures.py`, so `dashboard.py`'s own `min(years)`/`max(years)` --
        a second definition of the corpus's year range, derived in the view
        layer -- passed unseen, and a new `viz/_helpers.py` would have been
        exempt with no test change.
    """
    package_dir = Path(figures_module.__file__).parent
    return sorted(
        path for path in package_dir.rglob("*.py") if path.name not in _ARITHMETIC_EXEMPT_MODULES
    )


@pytest.mark.unit
@pytest.mark.parametrize("path", _scanned_viz_sources(), ids=lambda path: path.name)
def test_figures__no_module_performs_arithmetic(path: Path) -> None:
    """ADR 0025 Decision 1: the figure layer must not compute anything.

    Parametrised per file rather than looped, so a violation fails that
    file's own node instead of being buried in one aggregate pass/fail.
    """
    offences = arithmetic_offences(path.read_text(encoding="utf-8"), path)

    assert not offences, f"{path.name} contains arithmetic/aggregation: {offences}"


@pytest.mark.unit
def test_no_arithmetic_scan__exemption__is_exactly_one_module() -> None:
    """The exemption's *narrowness*, asserted rather than described in a comment.

    `theme.py` computes colour distances and contrast ratios, which is
    legitimate; every other `viz/` module is scanned. Widening this set is
    the cheapest way to disable the stage's load-bearing guard, so it costs
    a deliberate edit here.
    """
    package_dir = Path(figures_module.__file__).parent
    scanned = {path.name for path in _scanned_viz_sources()}
    present = {path.name for path in package_dir.rglob("*.py")}

    assert {"theme.py"} == _ARITHMETIC_EXEMPT_MODULES
    assert present - scanned == {"theme.py"}
    assert scanned, "guard the guard: an empty file list would make the scan vacuous"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("label", "source"), PLANTED_VIOLATIONS, ids=[label for label, _ in PLANTED_VIOLATIONS]
)
def test_no_arithmetic_scan__detects_a_planted_violation(label: str, source: str) -> None:
    """The scan is not vacuous: BUILD_PLAN's own two required plants, plus more of the same shape."""
    assert arithmetic_offences(source, Path("<planted>")), label


@pytest.mark.unit
@pytest.mark.parametrize("source", PERMITTED_SNIPPETS)
def test_no_arithmetic_scan__permits_reading_filtering_and_formatting(source: str) -> None:
    """The rule bounds computing a new value, not reading/selecting/formatting an existing one."""
    assert arithmetic_offences(source, Path("<planted>")) == []


@pytest.mark.unit
@pytest.mark.parametrize("name", _REQUIRED_FIGURE_NAMES)
def test_figures__every_function__accepts_analysis_result(name: str) -> None:
    """Introspective sweep over signatures -- every figure function's first parameter.

    ``AnalysisResult`` for six figures, ``Sequence[AnalysisResult]`` for the
    two per-dimension taxonomy figures, ``FlowCounts`` for figure 9 -- see
    this module's constants above for exactly which and why.
    """
    function = getattr(figures_module, name)
    signature = inspect.signature(function)
    first_parameter = next(iter(signature.parameters.values()))
    annotation = str(first_parameter.annotation)

    if name in _SEQUENCE_ARGUMENT_FIGURES:
        assert "Sequence" in annotation and "AnalysisResult" in annotation, name
    elif name in _FLOWCOUNTS_ARGUMENT_FIGURES:
        assert "FlowCounts" in annotation, name
    else:
        assert "AnalysisResult" in annotation and "Sequence" not in annotation, name


@pytest.mark.unit
def test_figures__required_figure_count__is_nine() -> None:
    """Pinned exactly (BUILD_PLAN names nine required figures), not as a floor."""
    assert len(_REQUIRED_FIGURE_NAMES) == 9


@pytest.mark.unit
def test_figures__figure_pair_type__is_a_two_tuple() -> None:
    origin = getattr(figures_module.FigurePair, "__origin__", None)
    assert origin is tuple


@pytest.mark.unit
def test_caption__no_criteria_versions__is_unchanged() -> None:
    """`_caption` appends nothing only when the store genuinely records no criteria version.

    This test used to certify the defect it looks like it guards. Every
    figure defaults to `PrismaStage.INCLUDED`, and `build_provenance`
    derived `criteria_versions` from the runs that contributed a record *to
    that stage set* -- so on a corpus where screening has not run, which is
    the first state any corpus is in, `criteria_versions` was empty and
    every caption shipped without a criteria version. S09-AC2 requires it.
    The golden claiming that criterion ran at `RAW` with `n = 2`: a fixture
    that could not reach the failing branch.

    `build_provenance` now falls back to the store's own runs when the stage
    set contributes none (see its comment). So this case is reachable only
    for a store with no runs at all, which is what the hand-built
    `Provenance` below represents -- and it is the honest one: nothing
    recorded, nothing claimed.
    """
    result = AnalysisResult(
        data=pl.DataFrame({"x": [1]}),
        params={},
        provenance=Provenance(
            corpus_size=1,
            stage=PrismaStage.INCLUDED,
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            run_ids=(),
            criteria_versions=(),
        ),
    )

    assert figures_module._caption(result) == result.caption()
    assert "criteria version" not in figures_module._caption(result)


@pytest.mark.unit
def test_caption__with_criteria_versions__appends_them() -> None:
    result = AnalysisResult(
        data=pl.DataFrame({"x": [1]}),
        params={},
        provenance=Provenance(
            corpus_size=1,
            stage=PrismaStage.INCLUDED,
            retrieved_at=datetime(2026, 1, 1, tzinfo=UTC),
            run_ids=("run1",),
            criteria_versions=("1.0.0", "1.1.0"),
        ),
    )

    caption = figures_module._caption(result)

    assert caption.startswith(result.caption())
    assert "criteria version(s): 1.0.0, 1.1.0" in caption
