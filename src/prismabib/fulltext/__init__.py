"""Stage 6: full-text resolution and coverage (BUILD_PLAN lines 1121-1179, ADR 0019).

- :mod:`prismabib.fulltext.resolve` -- the resolver chain
  (:class:`~prismabib.fulltext.resolve.FullTextResolver`,
  :func:`~prismabib.fulltext.resolve.resolve_fulltext`) and its three
  BUILD_PLAN implementations.
- :mod:`prismabib.fulltext.extract` -- section extraction from a resolved
  asset (ScienceDirect XML or PDF).
- :mod:`prismabib.fulltext.coverage` -- the coverage-by-resolver and
  coverage-by-publisher tables (S06-AC3).
- :mod:`prismabib.fulltext.run` -- orchestrates the chain over a project and
  persists results into Layer 1; what ``prismabib fulltext`` calls.

See the module docstrings of each for the anti-bias argument BUILD_PLAN and
ADR 0019 make for this stage's design; it is not repeated here.
"""

from __future__ import annotations
