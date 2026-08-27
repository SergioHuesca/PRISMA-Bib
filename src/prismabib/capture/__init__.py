"""Layer 0 raw capture (BUILD_PLAN §2.2, lines 99-102; §Stage 2, lines 754-838).

:mod:`prismabib.capture.writer` performs the Scopus **Search** acquisition run
and writes the immutable ``raw/<run_id>/`` tree.
:mod:`prismabib.capture.enrich` performs the Scopus **Abstract Retrieval**
enrichment run and writes ``raw/abstracts/<run_id>/`` -- the subject-area codes
``criteria.yaml`` needs and the Search API does not return (ADR 0011).
:mod:`prismabib.capture.manifest` defines the manifest each kind seals itself
with, and :mod:`prismabib.capture.layout` holds the on-disk vocabulary both
writers -- and :mod:`prismabib.store.load` -- have to agree on.
"""

from __future__ import annotations
