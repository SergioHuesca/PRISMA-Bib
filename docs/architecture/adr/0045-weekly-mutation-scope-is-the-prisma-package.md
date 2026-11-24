# ADR 0045: The weekly mutation scope is the prisma package

## Status

Accepted, 2026-11-16. Supersedes the scope part of [ADR 0009](0009-mutmut-3x-cli-deviation.md), the line that adds `src/prismabib/taxonomy/*` to `only_mutate`. The rest of ADR 0009 stands: the mutmut 3.x CLI, the scope read from `[tool.mutmut]`, and the rule that a scope change needs an ADR. The number 0044 is reserved for issue #354.

Amended 2026-11-17 (issue #405): the closing sentence of Consequences, which named the Stage 9 change as the next suspect, is removed. `git show 88c506c --stat` lists 36 files and none is under `src/prismabib/prisma/` or `src/prismabib/log.py`, so Stage 9 did not touch the code the mutation scope now covers. The History bullet and the CHANGELOG describe Stage 9 accurately and are unchanged.

Amended 2026-11-24 (issue #405, final outcome): `src/prismabib/log.py` is out of the scope as well. The first version of this ADR kept it in; nine manual runs showed that does not finish either (see Manual runs). The Decision and Consequences below are the final text.

## Context

The weekly mutation job has been cancelled at its 180-minute limit on every scheduled run since 2026-09-14, so the kill rate has not been read since the 2026-09-07 run (87.14%, default `timeout_multiplier` of 15, an older scope).

The scope was written three ways: the workflow header said the prisma package only, while ADR 0009 and `[tool.mutmut]` said the prisma package plus taxonomy.

History, checked for this record:

- The glob for the 2026-09-07 measurement was `only_mutate = ["src/prismabib/prisma/*"]`, with the prisma, property and engine-invariant suites selected. Taxonomy joined at 10e568b, merged 2026-09-07 at 17:09 UTC, after that day's 04:17 UTC run, so the first scheduled run to include it is 2026-09-14.
- Taxonomy was not the only change between the 2026-09-07 and 2026-09-14 runs. Stage 9 (88c506c, 2026-09-08) added the visualisation layer and changed the bibliometrics and taxonomy engines. Ten commits also changed the prisma package. The narrowing may not remove the hang.
- `src/prismabib/log.py` joined the scope on 2026-10-22 (13a7a5c), after the hang began. It holds the append-only log that ADR 0029 moved out of `prisma/`, which was in scope on 2026-09-07.

The cause of the hang is not established. A guess, that mutant workers idle inside polars after the fork, has not been reproduced and is not recorded as fact.

### Manual runs (#405)

Nine manual runs of the workflow were used. Counts are from the run logs.

- Runs 1 to 6 were a bisect, on branches `bisect/405-run1-prisma-only`, `run2-log-only`, `run3-prisma-minus-flow`, `run4-engine-only`, `run5-no-property` and `run6-multiplier3`. Timeouts were spread over `flow.py`, `engine.py`, `orphans.py` and `AppendOnlyLog` in `log.py`. No single file or function was located. Dropping the property suite changed nothing, so the hypothesis that its shrinking caused the timeouts is withdrawn.
- Run 7 (44896486656): prisma, `log.py` and taxonomy, multiplier 3. Cut off at the 60-minute step limit at 1421 of 2105 mutants: 765 killed, 637 timeouts, 19 survived.
- Run 8 (45334455709): prisma and `log.py`. Cut off at 940 of 1113: 799 killed, 122 timeouts, 19 survived. The last mutants were `AppendOnlyLog.load`.
- Run 9 (45342685259): prisma only. Cut off at 756 of 820: 654 killed, 85 timeouts, 17 survived. The four mutants still running were `engine._refuse_unenforceable_subject_filter` `mutmut_9`, `mutmut_10`, `mutmut_12` and `mutmut_13`. The pace fell from about 19 to about 3.7 mutants a minute after about minute 30.

No run finished, so there is no kill rate for the final scope.

## Decision

The scope is the prisma package only: `src/prismabib/prisma/*`. The taxonomy package and `src/prismabib/log.py` are out of `only_mutate`, and the taxonomy suites leave the test selection. `[tool.mutmut]` sets `timeout_multiplier = 3` (the mutmut default is 15), and the "Mutation run" step has `timeout-minutes: 60`. The 180-minute job limit is unchanged. No file is exempted with `do_not_mutate` and no new pragma is added. `[tool.mutmut]`, the workflow header, this ADR and the CHANGELOG state the same scope, and a fast test (`tests/unit/test_weekly_mutation_scope.py`) reads the configuration and the header as data. The weekly summary prints the count of mutants never checked beside the kill rate.

Exempting `_refuse_unenforceable_subject_filter` was considered and rejected. It is logic, and [ADR 0014](0014-mutation-gate-excludes-diagnostic-prose.md) allows a pragma to enclose text only, not conditions. It is also the BUILD_PLAN section 1.4 guard, so it is the code the gate exists to check. The slowness of its mutants is not explained by anything found so far.

Mutation testing of taxonomy, mutation testing of `log.py`, and the cause of the slow mutants and a run-time budget are tracked in follow-up issues with the label `mutation`, filed by the maintainer, so the hang is not lost. Issues #363 and #42 stay open.

## Consequences

The weekly score is meant to measure code a run can finish measuring, but run 9 did not finish within 60 minutes, so the weekly job may still be cut off at the step limit. Taxonomy and `log.py` have no mutation gate until their issues are resolved, and their kill rates stay unmeasured. A timeout counts as caught, so a rate read at multiplier 3 is not comparable with the 87.14% read at 15. The test shows the files say the right thing; only a finished run of the workflow shows the narrowing works, and none has finished.