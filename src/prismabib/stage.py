"""The PRISMA flow stages (BUILD_PLAN §Stage 4, lines 941-948, 1081).

``PrismaStage`` names the successive record sets of the PRISMA 2020 flow --
``S_raw`` (everything captured), the two deterministic filters ``A``
(year/subject/doc-type) and ``L`` (language), the two human-screened sets
``M_abs``/``M_full`` (folded from the Stage 4 decision log), and the final
corpus ``C = M_full``. It is frozen as the ``stage`` parameter of
:meth:`prismabib.store.load.Corpus.records` and
:meth:`prismabib.store.load.Corpus.keywords` (BUILD_PLAN lines 895-896), and
of the Stage 5 ``screening_queue(project, stage, reviewer)`` contract (line
1081).

**Why this lives in its own top-level module rather than in
``store/`` or ``models.py``.** ``PrismaStage`` is conceptually a Stage 4
(``prisma/``) artefact -- the PRISMA engine is its only *producer* -- but
Stage 3's frozen ``Corpus`` contract already needs it as a parameter type,
and BUILD_PLAN §0 rule 1 forbids Stage 3 from importing anything out of the
not-yet-built ``prisma/`` package. Two places could plausibly host it
instead:

- ``models.py`` (Stage 1's bibliographic domain models) would work
  mechanically, but ``PrismaStage`` has nothing to do with the shape of a
  bibliographic record; folding it in there would make a Stage 1 module
  carry a Stage 4 concept it otherwise never touches.
- ``store/`` would also work mechanically (Stage 3 is the first consumer),
  but would then make Stage 4's ``prisma/`` package depend on ``store/``
  just to import an enum that has nothing to do with DuckDB, and every
  future Stage 3 change to ``store/`` internals risks an unrelated diff to
  a name Stage 4 re-exports.

A standalone leaf module with zero imports of its own is the one place both
``prismabib.store`` (Stage 3, this stage) and ``prismabib.prisma`` (Stage 4,
later) can depend on it *downward* without either package depending on the
other. Stage 4 imports ``from prismabib.stage import PrismaStage`` rather
than Stage 3 reaching up into a package that does not exist yet.

Each member's string value is deliberately the exact literal used by the
Stage 4 decision-log event schema's ``stage`` field (BUILD_PLAN line 960:
``"stage": "title_abstract"``) where such an event stage exists, so a
decision event's ``stage`` string round-trips through
``PrismaStage(event["stage"])`` without a translation table. ``RAW``,
``AUTOMATED``, and ``LANGUAGE`` have no corresponding event stage (BUILD_PLAN
line 950: they are computed, never logged) but are named here anyway so the
full PRISMA flow is representable by one enum, not one enum plus a set of
magic strings scattered across ``prisma/``.
"""

from __future__ import annotations

from enum import StrEnum


class PrismaStage(StrEnum):
    """One named set of the PRISMA 2020 flow (BUILD_PLAN §Stage 4 table, lines 941-948).

    Members, in flow order:

    - ``RAW``: ``S_raw`` -- every record captured from the query (Layer 1,
      unfiltered).
    - ``AUTOMATED``: ``A`` -- ``S_raw`` filtered by year, subject area, and
      document type per ``criteria.yaml``. Deterministic.
    - ``LANGUAGE``: ``L`` -- ``A`` further filtered by language.
      Deterministic.
    - ``TITLE_ABSTRACT``: ``M_abs`` -- ``L`` folded through the decision log
      at the ``title_abstract`` screening stage.
    - ``FULLTEXT``: ``M_full`` -- ``M_abs`` folded through the decision log
      at the ``fulltext`` screening stage.
    - ``INCLUDED``: ``C`` -- the final corpus, ``= M_full``. The default for
      ``Corpus.records()``/``Corpus.keywords()``, since "the corpus" without
      qualification means the reviewed, included set.
    """

    RAW = "raw"
    AUTOMATED = "automated"
    LANGUAGE = "language"
    TITLE_ABSTRACT = "title_abstract"
    FULLTEXT = "fulltext"
    INCLUDED = "included"


__all__ = ["PrismaStage"]
