"""The PRISMA screening engine (BUILD_PLAN §Stage 4, lines 941-1004).

This package hosts Layer 2 -- the append-only decision log that screening
membership is folded from (BUILD_PLAN §2.2, lines 107-118) -- and the
deterministic machinery built on top of it.

Present:

- :mod:`prismabib.prisma.events` -- the ``DecisionEvent`` schema and a
  stdlib-only monotonic ULID generator.
- :mod:`prismabib.prisma.log` -- ``DecisionLog``, the append-only,
  checksum-guarded ``decisions.jsonl`` writer/reader and its fold.
- :mod:`prismabib.prisma.engine` -- the deterministic sets
  ``A``/``L``/``M_abs``/``M_full``/``C`` (BUILD_PLAN's Stage 4 table) and
  ``replay`` for the criteria-amendment workflow.
- :mod:`prismabib.prisma.flow` -- ``FlowCounts``, the PRISMA 2020
  flow-diagram numbers, entirely derived from ``engine``/``log``.
- :mod:`prismabib.prisma.criteria` -- resolving a ``criteria.yaml`` version
  other than the current one, from git history.
"""

from __future__ import annotations
