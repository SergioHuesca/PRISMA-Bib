"""Layer 0 raw capture (BUILD_PLAN §2.2, lines 99-102; §Stage 2, lines 754-838).

:mod:`prismabib.capture.writer` performs the acquisition run and writes the
immutable ``raw/<run_id>/`` tree; :mod:`prismabib.capture.manifest` defines
the :class:`~prismabib.capture.manifest.RunManifest` that makes each run
reproducible and citable.
"""

from __future__ import annotations
