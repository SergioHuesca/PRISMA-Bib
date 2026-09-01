"""Layer 3: manuscript-ready artefacts, with no number typed by a human.

BUILD_PLAN §Stage 10. This package turns the store and the decision log into
the things a manuscript actually cites -- a PRISMA 2020 flow diagram, tables,
and a flat map of every scalar the prose quotes -- and it exists to close the
failure mode §1.4 names: *a plausible wrong number in a published paper*.

The anti-drift mechanism is :mod:`~prismabib.report.numbers` plus
:mod:`~prismabib.report.fill`. A manuscript never contains a literal count; it
contains ``{{corpus.size}}``, which is substituted at build time from
``numbers.json``. `fill` fails on an unknown key *and* on an unused one, so a
number that stopped being cited is as loud as one that was never defined.

**Scope note (ADR 0015).** BUILD_PLAN's Stage 10 also lists a taxonomy
distribution, a dataset/benchmark usage table and a research-gap table. Those
read from Stages 8 and 9, which do not exist yet: the amended plan moved this
stage ahead of 6-9 deliberately, because export is what makes a *complete*
review possible and the analysis stages are not on that path. Every Stage 10
acceptance criterion is reachable without them, which is why the move is safe;
the three tables arrive with the stages that own their data.
"""

from prismabib.report.export import ExportResult, export_project
from prismabib.report.fill import FillError, fill_manuscript
from prismabib.report.flow_diagram import flow_diagram_svg
from prismabib.report.numbers import numbers_map

__all__ = [
    "ExportResult",
    "FillError",
    "export_project",
    "fill_manuscript",
    "flow_diagram_svg",
    "numbers_map",
]
