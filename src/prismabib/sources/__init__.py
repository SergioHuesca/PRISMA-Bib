"""Acquisition sources (BUILD_PLAN §2.3, ``src/prismabib/sources/``).

Everything that talks to an external metadata API lives here: the
header-aware rate limiter (:mod:`prismabib.sources.ratelimit`), the
content-addressed HTTP cache (:mod:`prismabib.sources.cache`), and the
Scopus client (:mod:`prismabib.sources.scopus`).
"""

from __future__ import annotations
