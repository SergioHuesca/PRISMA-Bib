"""The visualisation layer (BUILD_PLAN Stage 9, ADR 0025).

One validated palette applied to both Plotly and Matplotlib, the nine
figures the methodology requires, and the Panel dashboard that presents
them over a single :class:`~prismabib.store.load.Corpus` handle.

**Figure functions compute nothing** (ADR 0025 Decision 1), enforced by an
AST scan over every module here except :mod:`~prismabib.viz.theme`. A figure
that computes is a number with no ``params``, no provenance and no caption
obligation -- which is precisely what
:class:`~prismabib.bibliometrics.base.AnalysisResult` exists to make
impossible. Arithmetic belongs in Stage 7 or Stage 8; if a figure needs a
value it cannot get, the engine gains it rather than the figure deriving it.

Submodules:

- :mod:`~prismabib.viz.theme` -- the palette, CIEDE2000, the deuteranopia
  simulation, WCAG contrast and the 300 dpi legibility formula. The one
  module exempt from the arithmetic ban, because its arithmetic is over
  palette constants and never over ``AnalysisResult.data``.
- :mod:`~prismabib.viz.figures` -- one function per figure, each returning
  ``(plotly_figure, matplotlib_figure)``.
- :mod:`~prismabib.viz.dashboard` -- the nine-tab Panel dashboard.

This package existed without an ``__init__.py`` from Stage 9 until the
reference documentation was written: an implicit namespace package ships
fine in the wheel and imports fine at runtime, so nothing failed -- but
``mkdocstrings`` could not collect ``prismabib.viz``, and the gap surfaced
only when someone asked the documentation to describe it.
"""

from __future__ import annotations

__all__: list[str] = []
