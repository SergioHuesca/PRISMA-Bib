"""No pure ``taxonomy/`` module may read the wall clock (ADR 0023: determinism).

ADR 0023's own determinism requirement -- "no ``hash()`` ... no clock, no
unseeded ``random``" -- for the coder, the rule loader, the schema loader
and the review queue, mirroring exactly the reproducibility argument
``tests/unit/bibliometrics/test_no_clock.py`` makes for ADR 0022 Decision 2
(and reusing that scan's own machinery -- see ``tests/no_clock_scan.py``).

**Deliberately excludes ``taxonomy/overrides.py``.** An override *event*
must carry a real wall-clock ``ts`` -- exactly as
:meth:`~prismabib.prisma.log.DecisionLog.append` stamps a decision event's
``ts`` from ``datetime.now(UTC)`` in ``prisma/log.py``, which this same
class of guard (deliberately) does not cover either. The guard here is about
the *pure computation* (coding, folding, sampling), not the *event log*,
exactly the same split BUILD_PLAN draws between "Layer 2 is an append-only
log of timestamped events" and "Layer 1/Stage 7's analyses must be
reproducible."
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.no_clock_scan import PERMITTED_SNIPPETS, PLANTED_VIOLATIONS, clock_calls

#: The pure, deterministic modules this guard covers -- everything under
#: ``taxonomy/`` except ``overrides.py`` (see the module docstring) and
#: ``__init__.py`` (a re-export list, nothing to scan).
_GUARDED_MODULE_NAMES = ("schema.py", "rules.py", "coder.py", "review.py")


def _taxonomy_source_files() -> list[Path]:
    """The guarded files under the installed ``prismabib.taxonomy`` package."""
    import prismabib.taxonomy as taxonomy_package

    package_dir = Path(taxonomy_package.__file__).parent
    return sorted(path for path in package_dir.glob("*.py") if path.name in _GUARDED_MODULE_NAMES)


@pytest.mark.unit
def test_guarded_module_list__is_not_accidentally_empty() -> None:
    """Guard the guard: an empty parametrize list would make the scan vacuously pass."""
    assert len(_taxonomy_source_files()) == len(_GUARDED_MODULE_NAMES)


@pytest.mark.unit
@pytest.mark.parametrize("path", _taxonomy_source_files(), ids=lambda path: path.name)
def test_taxonomy_module__source__never_calls_the_wall_clock(path: Path) -> None:
    """Scans one pure ``taxonomy/`` source file's AST for a forbidden clock call."""
    calls = clock_calls(path.read_text(encoding="utf-8"), path)
    assert not calls, f"{path} calls the wall clock: {calls} -- see ADR 0023"


@pytest.mark.unit
def test_taxonomy_overrides_module__is_deliberately_exempt_and_does_call_the_clock() -> None:
    """The one deliberate exception: ``overrides.py`` must stamp a real ``ts``.

    Asserts the *opposite* of the guard above, so this test would fail
    loudly if a future edit removed ``OverrideLog.append``'s
    ``datetime.now(UTC)`` call -- which would silently break every
    ``ts``-ordered fold (:func:`prismabib.taxonomy.overrides.fold_override_events`)
    the same way a frozen ``ts`` would.
    """
    import prismabib.taxonomy.overrides as overrides_module

    path = Path(overrides_module.__file__)

    calls = clock_calls(path.read_text(encoding="utf-8"), path)

    assert calls, "overrides.py should call datetime.now(UTC) to stamp OverrideEvent.ts"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("label", "source"), PLANTED_VIOLATIONS, ids=[label for label, _ in PLANTED_VIOLATIONS]
)
def test_no_clock_scan__detects_a_planted_violation(label: str, source: str) -> None:
    """The shared scanner is not vacuous over this module's own planted table."""
    assert clock_calls(source, Path("<planted>")), label


@pytest.mark.unit
@pytest.mark.parametrize(
    "source", PERMITTED_SNIPPETS, ids=["annotation", "construction", "timedelta"]
)
def test_no_clock_scan__permits_constructing_and_annotating_a_datetime(source: str) -> None:
    """Constructing/annotating with ``datetime``/``timedelta`` is not reading the clock."""
    assert clock_calls(source, Path("<planted>")) == []
