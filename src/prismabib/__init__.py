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
    #
    # Not a plausible-looking number. This value is what `capture/writer.py` stamps
    # into `RunManifest.client_version`, and `raw/<run_id>/` is sealed on write, so
    # anything that reads like a release ("0.1.0.dev0", as this used to) becomes a
    # permanent, unfixable claim that a specific version of the code made that
    # capture. "0+unknown" matches the build-time `fallback-version` in
    # pyproject.toml and cannot be mistaken for a release.
    __version__ = "0+unknown"

__all__ = ["__version__"]
