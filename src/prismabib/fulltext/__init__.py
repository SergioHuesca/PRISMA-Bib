"""Stage 6: full-text resolution and coverage (BUILD_PLAN lines 1121-1179, ADR 0019).

- :mod:`prismabib.fulltext.resolve` -- the resolver chain
  (:class:`~prismabib.fulltext.resolve.FullTextResolver`,
  :func:`~prismabib.fulltext.resolve.resolve_fulltext`) and its three
  BUILD_PLAN implementations.
- :mod:`prismabib.fulltext.extract` -- section extraction from a resolved
  asset (ScienceDirect XML or PDF).
- :mod:`prismabib.fulltext.coverage` -- the coverage-by-resolver and
  coverage-by-publisher tables (S06-AC3).
- :mod:`prismabib.fulltext.capture` -- seals the chain's output into a Layer 0
  run under ``project.fulltext_dir`` (ADR 0019 Decision 0); the only place
  fetched bytes ever touch a disk.
- :mod:`prismabib.fulltext.run` -- orchestrates the chain over a project and
  hands the result to :mod:`~prismabib.fulltext.capture`; what
  ``prismabib fulltext`` calls. ``fulltext_assets``/``fulltext_sections`` are
  then rebuilt into Layer 1 by ``prismabib build --rebuild``, the same
  two-step shape ``prismabib enrich`` already has.

See the module docstrings of each for the anti-bias argument BUILD_PLAN and
ADR 0019 make for this stage's design; it is not repeated here.
"""

from __future__ import annotations
