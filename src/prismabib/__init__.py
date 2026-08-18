"""PRISMA-Bibliometric Research Lab.

A reproducible PRISMA + bibliometric research system for systematic reviews.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("prismabib")
except PackageNotFoundError:  # pragma: no cover
    # Only reachable when the package is imported without being installed — running
    # straight from a source tree. Every supported path (`uv sync`, `uv run`, CI)
    # installs it, so no test can exercise this without faking the import system,
    # which §3.7.3 rule 1 rules out. Excluded rather than tested.
    __version__ = "0.1.0.dev0"

__all__ = ["__version__"]
