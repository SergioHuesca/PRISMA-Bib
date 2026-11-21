# ADR 0045: The weekly mutation scope is the prisma package

## Status

Accepted, 2026-11-16. Supersedes the scope part of [ADR 0009](0009-mutmut-3x-cli-deviation.md), the line that adds `src/prismabib/taxonomy/*` to `only_mutate`. The rest of ADR 0009 stands: the mutmut 3.x CLI, the scope read from `[tool.mutmut]`, and the rule that a scope change needs an ADR. The number 0044 is reserved for issue #354.

Amended 2026-11-17 (issue #405): the closing sentence of Consequences, which named the Stage 9 change as the next suspect, is removed. `git show 88c506c --stat` lists 36 files and none is under `src/prismabib/prisma/` or `src/prismabib/log.py`, so Stage 9 did not touch the code the mutation scope now covers. The History bullet and the CHANGELOG describe Stage 9 accurately and are unchanged.

## Context

The weekly mutation job has been cancelled at its 180-minute limit on every scheduled run since 2026-09-14, so the kill rate has not been read since the 2026-09-07 run (87.14%).

The scope was written three ways: the workflow header said the prisma package only, while ADR 0009 and `[tool.mutmut]` said the prisma package plus taxonomy.

History, checked for this record:

- The glob for the 2026-09-07 measurement was `only_mutate = ["src/prismabib/prisma/*"]`, with the prisma, property and engine-invariant suites selected. Taxonomy joined at 10e568b, merged 2026-09-07 at 17:09 UTC, after that day's 04:17 UTC run, so the first scheduled run to include it is 2026-09-14.
- Taxonomy was not the only change between the 2026-09-07 and 2026-09-14 runs. Stage 9 (88c506c, 2026-09-08) added the visualisation layer and changed the bibliometrics and taxonomy engines. Ten commits also changed the prisma package. The narrowing may not remove the hang.
- `src/prismabib/log.py` joined the scope on 2026-10-22 (13a7a5c), after the hang began. It holds the append-only log that ADR 0029 moved out of `prisma/`, which was in scope on 2026-09-07.

The cause of the hang is not established. A guess, that mutant workers idle inside polars after the fork, has not been reproduced and is not recorded as fact.

## Decision

The scope is the prisma package only: `src/prismabib/prisma/*`, plus `src/prismabib/log.py` because it is code moved out of that package. The taxonomy package leaves `only_mutate`, and the taxonomy suites leave the test selection. `[tool.mutmut]`, the workflow header, this ADR and the CHANGELOG state the same scope, and a fast test reads the configuration and the header as data. The 180-minute limit is unchanged. The weekly summary prints the count of mutants never checked beside the kill rate.

Mutation testing of taxonomy is tracked in its own follow-up issue, filed by the maintainer, so the hang is not lost.

## Consequences

The weekly score again measures code a run can finish measuring. Taxonomy has no mutation gate until that issue is resolved, and its kill rate stays unmeasured. The test shows the files say the right thing; only a manual run of the workflow shows the narrowing works.