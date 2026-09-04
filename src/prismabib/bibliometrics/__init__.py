"""The bibliometrics engine (BUILD_PLAN Stage 7, ADR 0022).

Every quantitative finding a manuscript can cite -- publication trends,
geography, venues, citation impact, keywords, and co-occurrence/co-
authorship networks -- computed from :class:`~prismabib.store.load.Corpus`
and returned as :class:`~prismabib.bibliometrics.base.AnalysisResult`, never
a bare :class:`polars.DataFrame` or scalar (ADR 0022's Constraints).

Submodules:

- :mod:`~prismabib.bibliometrics.base` -- the ``AnalysisResult``/
  ``Provenance`` contract and the caption logic every other module builds
  its result through.
- :mod:`~prismabib.bibliometrics.trends` -- annual publication counts, CAGR.
- :mod:`~prismabib.bibliometrics.geography` -- country counts and citation
  impact by country.
- :mod:`~prismabib.bibliometrics.venues` -- top venues, venue-type split.
- :mod:`~prismabib.bibliometrics.citations` -- citation statistics
  (including h-index) and by-year averages.
- :mod:`~prismabib.bibliometrics.keywords` -- keyword frequency and
  year-by-year evolution.
- :mod:`~prismabib.bibliometrics.network` -- keyword co-occurrence and
  co-authorship graphs, and the VOSviewer export.

**No module in this package may read the clock** (``datetime.now``,
``date.today``, ``time.time``) -- ADR 0022 Decision 2 and Constraints.
"""

from __future__ import annotations
