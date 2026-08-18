"""Acceptance-criteria traceability, BUILD_PLAN.md §3.7.8.

Every acceptance criterion in the BUILD_PLAN is identified as ``S<stage>-AC<n>``
and must be claimed by at least one test via
``@pytest.mark.acceptance("S<NN>-AC<n>")``. ``pytest --acceptance-report``
prints a matrix of every criterion declared in ``tests/acceptance_criteria.toml``
against the tests that claim it, and fails the run if any declared criterion
is unclaimed.

This module holds the reusable logic. The actual pytest hook functions
(``pytest_addoption``, ``pytest_configure``, ``pytest_itemcollected``,
``pytest_sessionfinish``) live in ``conftest.py`` -- hooks are only
auto-discovered from a ``conftest.py`` module, never from an arbitrary
importable module -- and delegate straight to the functions below. This
split is what lets ``tests/unit/test_harness.py`` exercise the traceability
behaviour end-to-end (via ``pytester``) by wiring the same functions into a
synthetic conftest, without touching any global state of the real suite.

Design note on *when* a marker is collected: claims are recorded from
``pytest_itemcollected``, which fires once per test item as soon as it is
built -- *before* ``pytest_collection_modifyitems`` runs the ``-m`` expression
deselection. That ordering is deliberate: a test deselected by
``-m "not live"`` still claims its criterion, because Stage 0's four
GitHub-governance criteria are necessarily ``live``-only (see
``tests/live/test_github_governance.py``) and must never run in the default
suite, yet must still show as claimed in an honest report.
"""

from __future__ import annotations

import tomllib
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Mapping

CRITERIA_FILE = Path(__file__).parent / "acceptance_criteria.toml"

_STASH_KEY: pytest.StashKey[AcceptanceReportPlugin] = pytest.StashKey()


def load_criteria(path: Path = CRITERIA_FILE) -> dict[str, str]:
    """Load the declared ``{criterion_id: description}`` table.

    Args:
        path: Location of the TOML file holding the ``[criteria]`` table.
            Defaults to the repository's committed ``acceptance_criteria.toml``.

    Returns:
        A mapping from criterion id (e.g. ``"S00-AC1"``) to its description.
    """
    with path.open("rb") as handle:
        data = tomllib.load(handle)

    return dict(data.get("criteria", {}))


class AcceptanceReportPlugin:
    """Accumulates ``acceptance`` marker claims collected during a session."""

    def __init__(self, criteria: Mapping[str, str]) -> None:
        """Initialise with the declared criteria to check claims against.

        Args:
            criteria: Mapping from criterion id to its description, as
                returned by :func:`load_criteria`.
        """
        self.criteria: dict[str, str] = dict(criteria)
        self.claims: dict[str, list[str]] = defaultdict(list)

    def record(self, item: pytest.Item) -> None:
        """Record every criterion claimed by a single collected test item."""
        for marker in item.iter_markers(name="acceptance"):
            for criterion in marker.args:
                self.claims[criterion].append(item.nodeid)

    def unclaimed(self) -> set[str]:
        """Return the declared criteria with zero claiming tests."""
        return set(self.criteria) - set(self.claims)

    def render(self) -> str:
        """Render the criteria-vs-tests matrix as a human-readable report."""
        rule = "=" * 72
        lines = ["", rule, "Acceptance criteria matrix (BUILD_PLAN.md §3.7.8)", rule]

        for criterion in sorted(self.criteria):
            claimants = self.claims.get(criterion, [])
            status = "claimed" if claimants else "UNCLAIMED"
            lines.append(f"[{status:>9}] {criterion}: {self.criteria[criterion]}")
            lines.extend(f"{'':13}claimed by {nodeid}" for nodeid in claimants)

        lines.append(rule)
        return "\n".join(lines)


def add_option(parser: pytest.Parser) -> None:
    """Register the ``--acceptance-report`` flag."""
    group = parser.getgroup("acceptance")
    group.addoption(
        "--acceptance-report",
        action="store_true",
        default=False,
        help=(
            "Collect tests (without running them), print the acceptance-criteria "
            "matrix declared in tests/acceptance_criteria.toml (BUILD_PLAN.md "
            "§3.7.8), and exit non-zero if any declared criterion has no "
            "claiming test."
        ),
    )


def configure(config: pytest.Config, *, criteria: Mapping[str, str] | None = None) -> None:
    """Attach a fresh :class:`AcceptanceReportPlugin` to this session's config.

    Args:
        config: The pytest session config to attach the plugin to.
        criteria: Declared criteria to check claims against. Defaults to
            :func:`load_criteria`'s reading of the committed criteria file --
            callers wiring up an isolated test session may pass their own.
    """
    plugin = AcceptanceReportPlugin(criteria if criteria is not None else load_criteria())
    config.stash[_STASH_KEY] = plugin

    if config.getoption("--acceptance-report"):
        # A traceability check is a collection-time question. Actually running
        # the suite -- including `live` tests, which shell out to real `git`/
        # `gh` -- has no bearing on it and would make the report slow and
        # network-dependent, defeating the point of the socket ban.
        config.option.collectonly = True


def item_collected(item: pytest.Item) -> None:
    """Record the acceptance claims of one freshly collected test item."""
    item.config.stash[_STASH_KEY].record(item)


def session_finish(session: pytest.Session) -> None:
    """Print the matrix and fail the session if a criterion is unclaimed."""
    config = session.config
    if not config.getoption("--acceptance-report"):
        return

    plugin = config.stash[_STASH_KEY]
    reporter = config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(plugin.render())

    unclaimed = plugin.unclaimed()
    if unclaimed:
        if reporter is not None:
            reporter.write_line(f"UNCLAIMED criteria: {sorted(unclaimed)}", red=True, bold=True)
        session.exitstatus = 1
