"""The whole-package contract ADR 0022 states as Constraints (S07-AC1, S07-AC5).

Every public function across the six analysis modules --
:mod:`~prismabib.bibliometrics.trends`, :mod:`~prismabib.bibliometrics.geography`,
:mod:`~prismabib.bibliometrics.venues`, :mod:`~prismabib.bibliometrics.citations`,
:mod:`~prismabib.bibliometrics.keywords`, :mod:`~prismabib.bibliometrics.network`
-- is introspected here rather than named one by one, so a new analysis
function added later is swept in automatically (BUILD_PLAN: "This is a
one-test guarantee of an architectural rule").
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest

from prismabib.bibliometrics import citations, geography, keywords, network, trends, venues
from prismabib.bibliometrics.base import AnalysisResult, _serialise
from prismabib.stage import PrismaStage
from prismabib.store.load import Corpus
from tests.bibliometrics_helpers import (
    AffiliationSpec,
    AuthorSpec,
    BibCorpusSpec,
    BibRecordSpec,
    build_bib_project,
    include_everything,
    open_corpus,
)

_ANALYSIS_MODULES: tuple[ModuleType, ...] = (
    trends,
    geography,
    venues,
    citations,
    keywords,
    network,
)


def _public_analysis_functions() -> list[tuple[str, Callable[..., AnalysisResult]]]:
    """Every function named in one of the six modules' own ``__all__``.

    Returns:
        ``(qualified_name, function)`` pairs, sorted by name for a stable
        parametrisation order. Each function's first parameter is
        ``corpus`` and every other parameter has a default -- the shape
        every analysis function in this package follows -- so
        ``function(corpus)`` alone is a complete, valid call.
    """
    found: list[tuple[str, Callable[..., AnalysisResult]]] = []
    for module in _ANALYSIS_MODULES:
        for name in module.__all__:
            candidate = getattr(module, name)
            if inspect.isfunction(candidate):
                found.append((f"{module.__name__}.{name}", candidate))
    return sorted(found, key=lambda item: item[0])


_FUNCTIONS = _public_analysis_functions()
_FUNCTION_IDS = [name for name, _ in _FUNCTIONS]

#: How many public analysis functions the six modules expose between them.
#: Pinned exactly, not as a floor -- see
#: `test_all_analyses__introspective_sweep__enumerates_every_analysis_function`.
#: Adding an analysis is expected to change this number in the same commit.
_EXPECTED_ANALYSIS_FUNCTIONS = 12

#: `cagr` is the one function that legitimately raises on an empty corpus
#: (ADR 0022 Decisions 3 and 10), so the empty-corpus sweep excludes it.
#: A module-level constant rather than a comprehension-level `if` in a test
#: body (BUILD_PLAN §3.7.3 rule 9).
_NON_CAGR_FUNCTIONS = [(name, fn) for name, fn in _FUNCTIONS if fn is not trends.cagr]


@pytest.fixture(scope="module")
def rich_project(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """One project exercising every dimension every analysis function reads.

    Module-scoped: every test below only reads the built store, and
    rebuilding it per test would be pure overhead across ~15 parametrised
    call sites.

    **Ties are engineered in deliberately, and are the point of several of
    the values below.** S07-AC5's byte-equality check can only detect a
    missing sort tie-break where the underlying `group_by` actually
    produces a tie -- otherwise polars' output order happens to be stable
    and the assertion passes with the tie-break deleted. Removing the
    tie-break from seven sort sites reded exactly one test while this
    fixture had ties in one place only. So:

    - **countries tie on count** (USA 6, JPN 6) -- arms `country_counts`;
    - **countries tie on total citations** (72 each; the even-numbered
      records carry `i * 2 - 2` rather than `i * 2` purely to make this
      true) -- arms `citation_impact_by_country`;
    - **`vision` and `audio` tie on record count** (6 each) -- arms
      `keyword_frequency`, and ties again within a single year against
      `baseball` -- arms `keyword_evolution`;
    - **three venue types tie on count** (4 each) -- arms
      `venue_type_split`. Three rather than two: with only two groups
      polars' output order was stable in practice and the assertion stayed
      disarmed, so the tie has to be wide enough for the group order to
      actually vary.

    A future change that removes a tie silently disarms the corresponding
    assertion, so each is named here rather than left to be re-derived.
    """
    tmp_path = tmp_path_factory.mktemp("rich-project")
    records = [
        BibRecordSpec(
            number=i,
            year=2019 + (i % 4),
            venue_name="ICML" if i % 2 else "Journal of Testing",
            source_id=str(100 + (i % 3)),
            venue_type=("Journal", "Conference Proceeding", "Book Series")[i % 3],
            cited_by_count=i * 2 if i % 2 else i * 2 - 2,
            author_keywords=("baseball", "vision") if i % 2 else ("baseball", "audio"),
            affiliations=(AffiliationSpec(afid=f"AF{i}", country="USA" if i % 2 else "JPN"),),
            authors=(
                AuthorSpec(author_id=f"A{i % 3}", surname=f"Surname{i % 3}"),
                AuthorSpec(author_id=f"A{(i + 1) % 3}", surname=f"Surname{(i + 1) % 3}"),
            ),
        )
        for i in range(1, 13)
    ]
    project = build_bib_project(
        tmp_path,
        BibCorpusSpec(records=records, run_started_ats=(datetime(2025, 6, 15, tzinfo=UTC),)),
        slug="rich",
    )
    include_everything(project)
    return project


@pytest.mark.integration
@pytest.mark.acceptance("S07-AC1")
@pytest.mark.parametrize(("name", "function"), _FUNCTIONS, ids=_FUNCTION_IDS)
def test_all_analyses__return_type__is_analysis_result(
    rich_project: Path, name: str, function: Callable[..., AnalysisResult]
) -> None:
    corpus = open_corpus(rich_project)

    result = function(corpus)

    assert isinstance(result, AnalysisResult), (
        f"{name} returned {type(result).__name__}, not AnalysisResult"
    )
    assert not isinstance(result, dict | list | tuple)


@pytest.mark.integration
@pytest.mark.parametrize(("name", "function"), _FUNCTIONS, ids=_FUNCTION_IDS)
def test_all_analyses__caption__contains_n_and_snapshot_date(
    rich_project: Path, name: str, function: Callable[..., AnalysisResult]
) -> None:
    corpus = open_corpus(rich_project)

    result = function(corpus, stage=PrismaStage.RAW)
    caption = result.caption()

    assert f"n = {result.provenance.corpus_size}" in caption, name
    assert re.search(r"\d{4}-\d{2}-\d{2}", caption), (
        f"{name}'s caption has no ISO date: {caption!r}"
    )


@pytest.mark.integration
@pytest.mark.acceptance("S07-AC5")
@pytest.mark.parametrize(("name", "function"), _FUNCTIONS, ids=_FUNCTION_IDS)
def test_all_analyses__recomputed_twice__is_bit_identical(
    rich_project: Path, name: str, function: Callable[..., AnalysisResult]
) -> None:
    """ADR 0022 Decision 8: two separately-opened `Corpus` handles, byte-for-byte agreement."""
    first_corpus = Corpus.open(rich_project, read_only=True)
    second_corpus = Corpus.open(rich_project, read_only=True)

    first = _serialise(function(first_corpus))
    second = _serialise(function(second_corpus))

    assert first == second, name


@pytest.mark.integration
def test_all_analyses__on_an_empty_corpus__every_function_still_returns_a_result(
    tmp_path: Path,
) -> None:
    """ADR 0022 Decision 10: the live corpus's `C` is empty today, and that must not crash anything.

    `cagr` is the one documented exception (it raises -- Decision 3/10);
    every other function returns a correctly-shaped, zero-row result.
    """
    project = build_bib_project(tmp_path, BibCorpusSpec(records=[]), slug="empty")
    corpus = open_corpus(project)

    results = [
        function(corpus, stage=PrismaStage.INCLUDED) for _name, function in _NON_CAGR_FUNCTIONS
    ]

    assert all(isinstance(result, AnalysisResult) for result in results)
    assert all(result.provenance.corpus_size == 0 for result in results)


@pytest.mark.integration
def test_all_analyses__introspective_sweep__enumerates_every_analysis_function(
    rich_project: Path,
) -> None:
    """The sweep's own vacuity guard, pinned to the exact count rather than a floor.

    A `>=` floor below the true count is this project's recurring defect
    shape: at `>= 10` against a true 12, dropping a function from a
    module's `__all__` removed three parametrised nodes from the suite and
    every test still passed. An equality makes the sweep notice its own
    shrinkage.

    Renamed from `..._has_no_undocumented_bare_return`, which described a
    guarantee this test does not make -- that one lives in
    `test_all_analyses__return_type__is_analysis_result`.
    """
    assert len(_FUNCTIONS) == _EXPECTED_ANALYSIS_FUNCTIONS, (
        f"the sweep found {len(_FUNCTIONS)} analysis functions, expected "
        f"{_EXPECTED_ANALYSIS_FUNCTIONS} -- a function was added to or removed from an "
        "analysis module's __all__ without updating this count"
    )
