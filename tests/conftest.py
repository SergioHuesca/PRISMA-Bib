"""Stage 0 test harness.

BUILD_PLAN.md §3.7.3 lists the rules of engagement every later stage's suite
inherits. This module provides the fixtures and hooks that make those rules
true by construction rather than by convention:

- a hard socket ban for the default suite (rule 2), live-marker-aware so
  ``tests/live/`` needs no per-test boilerplate;
- a frozen clock (rule 3);
- a seeded, monotonic ``IdFactory`` (rule 3);
- a ``tmp_project`` fixture matching the §2.3 directory layout;
- the ``--acceptance-report`` traceability hooks of §3.7.8 (logic in
  ``tests/markers.py``, wired in here because pytest only auto-discovers
  hook functions from ``conftest.py``).
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import pytest
import time_machine
from pytest_socket import disable_socket, enable_socket

from tests import markers

# Opt in to the `pytester` fixture (BUILD_PLAN.md §3.7.8's own meta-test uses
# it to exercise the acceptance-report plugin end to end without mutating
# this session's global state -- see tests/unit/test_harness.py).
pytest_plugins = ["pytester"]

# A fixed instant, arbitrary but memorable: 2024-01-15 12:00:00 UTC.
FROZEN_INSTANT = datetime(2024, 1, 15, 12, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# §3.7.8 traceability -- thin hook wiring; behaviour lives in tests/markers.py
# ---------------------------------------------------------------------------


def pytest_addoption(parser: pytest.Parser) -> None:
    """Register ``--acceptance-report`` (see ``tests/markers.py``)."""
    markers.add_option(parser)


def pytest_configure(config: pytest.Config) -> None:
    """Attach the acceptance-report plugin to this session."""
    markers.configure(config)


def pytest_itemcollected(item: pytest.Item) -> None:
    """Record acceptance-marker claims at collection time.

    Deliberately hooked at ``pytest_itemcollected`` rather than
    ``pytest_collection_modifyitems``: it fires before ``-m`` deselection, so
    a test deselected by ``-m "not live"`` still claims its criterion.
    """
    markers.item_collected(item)


def pytest_sessionfinish(session: pytest.Session) -> None:
    """Print the acceptance matrix and fail on any unclaimed criterion."""
    markers.session_finish(session)


# ---------------------------------------------------------------------------
# §3.7.3 rule 2 -- socket ban, live-marker-aware
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _socket_policy(request: pytest.FixtureRequest) -> Iterator[None]:
    """Ban outbound sockets everywhere except tests marked ``live``.

    Unix sockets stay allowed (DuckDB and similar local IPC do not threaten
    determinism or licensing; a real network call does). Tests under
    ``tests/live/`` are marked ``live`` and re-enable sockets here rather
    than needing per-test boilerplate; ``tests/live/conftest.py`` also
    re-enables sockets directly, so that directory is self-sufficient even
    if collected on its own.
    """
    if request.node.get_closest_marker("live") is not None:
        enable_socket()
        yield
        return

    disable_socket(allow_unix_socket=True)
    yield


# ---------------------------------------------------------------------------
# §3.7.3 rule 3 -- frozen clock
# ---------------------------------------------------------------------------


@pytest.fixture
def frozen_time() -> Iterator[None]:
    """Pin ``datetime.now(UTC)`` (and the rest of the clock) to a fixed instant."""
    with time_machine.travel(FROZEN_INSTANT, tick=False):
        yield


# ---------------------------------------------------------------------------
# §3.7.3 rule 3 -- seeded, monotonic IdFactory
# ---------------------------------------------------------------------------


class IdFactory(Protocol):
    """Injectable ID generator.

    Production code that needs a ULID/UUID takes one of these instead of
    calling a generator directly, so tests can inject a seeded, monotonic
    double and never depend on real entropy.
    """

    def __call__(self) -> str:
        """Return the next id in sequence."""
        ...


@dataclass
class SeededIdFactory:
    """A monotonic counter seeded at construction time.

    Two instances built with the same ``seed`` and ``prefix`` -- in the same
    process or a different one -- produce identical sequences.
    """

    seed: int = 0
    prefix: str = "id"
    _next: int = field(init=False)

    def __post_init__(self) -> None:
        self._next = self.seed

    def __call__(self) -> str:
        current, self._next = self._next, self._next + 1
        return f"{self.prefix}-{current:06d}"


@pytest.fixture
def id_factory() -> IdFactory:
    """A seeded, monotonic :class:`IdFactory` -- the same sequence every run."""
    return SeededIdFactory(seed=0, prefix="id")


# ---------------------------------------------------------------------------
# tmp_project -- §2.3 directory layout, forward-compatible with Stage 1
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TmpProject:
    """Plain-path skeleton of the §2.3 ``projects/<slug>/`` layout.

    Stage 1 defines the real ``prismabib.project.Project`` class. This
    fixture is deliberately built from plain ``Path`` objects and does not
    import ``prismabib.project`` -- that module does not exist yet -- so
    that Stage 1 can build ``Project`` on top of the same on-disk shape
    without this fixture needing to change.
    """

    root: Path
    raw: Path
    store_dir: Path
    decisions_dir: Path
    taxonomy_rules_dir: Path
    fulltext: Path
    exports: Path
    project_toml: Path
    criteria_yaml: Path
    decisions_jsonl: Path


@pytest.fixture
def tmp_project(tmp_path: Path) -> TmpProject:
    """A temporary ``projects/<slug>/`` skeleton per §2.3 (lines 168-177)."""
    root = tmp_path / "demo-project"
    raw = root / "raw"
    store_dir = root / "store"
    decisions_dir = root / "decisions"
    taxonomy_rules_dir = root / "taxonomy" / "rules"
    fulltext = root / "fulltext"
    exports = root / "exports"

    for directory in (raw, store_dir, decisions_dir, taxonomy_rules_dir, fulltext, exports):
        directory.mkdir(parents=True)

    project_toml = root / "project.toml"
    criteria_yaml = root / "criteria.yaml"
    decisions_jsonl = decisions_dir / "decisions.jsonl"
    for leaf in (project_toml, criteria_yaml, decisions_jsonl):
        leaf.touch()

    return TmpProject(
        root=root,
        raw=raw,
        store_dir=store_dir,
        decisions_dir=decisions_dir,
        taxonomy_rules_dir=taxonomy_rules_dir,
        fulltext=fulltext,
        exports=exports,
        project_toml=project_toml,
        criteria_yaml=criteria_yaml,
        decisions_jsonl=decisions_jsonl,
    )
