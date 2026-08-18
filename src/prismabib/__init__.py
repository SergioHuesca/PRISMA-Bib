"""PRISMA-Bibliometric Research Lab.

A reproducible PRISMA + bibliometric research system for systematic reviews.
"""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("prismabib")
except PackageNotFoundError:
    __version__ = "0.1.0.dev0"

__all__ = ["__version__"]
