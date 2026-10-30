"""The taxonomy engine (BUILD_PLAN §Stage 8; ADR 0005; ADR 0023).

Library-only, as ADR 0023 Decision 8 requires: there is no ``prismabib code``
command in this stage, and none of the five modules below imports
:mod:`prismabib.cli`. A notebook or a future CLI command wires them together.

- :mod:`prismabib.taxonomy.schema` -- per-project dimension declarations
  (:class:`~prismabib.taxonomy.schema.TaxonomySchema`) and
  :class:`~prismabib.taxonomy.schema.CountingUnit` (ADR 0005).
- :mod:`prismabib.taxonomy.rules` -- the regex-DSL rule file format and its
  load-time validation (ADR 0023 Decision 6: everything a rule file can get
  wrong fails at load, never at match time).
- :mod:`prismabib.taxonomy.coder` -- the pure function
  ``code(corpus, rule_files, overrides) -> CodingResult`` (ADR 0023
  Decision 1: assignments are computed, never stored).
- :mod:`prismabib.taxonomy.overrides` -- the append-only human-override event
  log (ADR 0023 Decision 3; reuses
  :class:`prismabib.log.AppendOnlyLog`, the same machinery
  ``decisions.jsonl`` uses).
- :mod:`prismabib.taxonomy.review` -- the review priority queue and the
  coverage/audit report.
- :mod:`prismabib.taxonomy.analysis` -- ``distribution``/evolution results
  reshaped as :class:`~prismabib.bibliometrics.base.AnalysisResult`, for
  ``viz/figures.py`` (ADR 0025 Decision 2; BUILD_PLAN §Stage 9 figures 7
  and 8).
"""

from __future__ import annotations

from typing import Final

#: The matching behaviour of the engine (ADR 0039). Bump it by any change that
#: can change what ``code()`` returns for an unchanged rule file, corpus and
#: override log (compile flags, the field text, the matching, normalisation).
#: ``1`` is case-sensitive matching. It is folded into the audit seed (for
#: values other than 1) and stamped on analysis ``params``, the review queue
#: and the coverage report.
TAXONOMY_ENGINE_BEHAVIOUR: Final[int] = 1

__all__ = ["TAXONOMY_ENGINE_BEHAVIOUR"]
