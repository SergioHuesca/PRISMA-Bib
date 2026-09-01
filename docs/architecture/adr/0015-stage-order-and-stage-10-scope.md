# ADR 0015: Stage Order, the End of the Stage↔Tag Mapping, and Stage 10's Scope

## Status

Accepted — 2026-08-31. Recorded under §2.6, which requires an ADR for any deviation from a
frozen contract. This one is overdue: the resequencing it records was agreed on 2026-08-26
and implemented across five releases before anything wrote it down. Several places, a
`v0.10.0` release note among them, cited "ADR 0010" as its authority; ADR 0010 is
*Cross-Platform Locking for the Decision Log*, and the ADR that number was reserved for was
never written. This is that ADR, written at the first stage whose scope actually depends on
it.

## Context

BUILD_PLAN builds stages in numerical order and maps each to a tag: Stage N ships
`v0.(N+1).0`. EXECUTION_PLAN line 207 adds that *"Stage 10 requires 4 (for flow counts) and
effectively all analysis stages"*.

On 2026-08-26 the goal changed. It stopped being "finish the BUILD_PLAN" and became: **any
researcher can clone this repository, point it at their own Scopus key and their own topic,
and run a real systematic review.** That is a different objective, and it reorders the work.

A PRISMA systematic review is complete when a reviewer can screen a corpus and export a
diagram and a set of numbers they can defend. It is *not* incomplete without full-text
resolution, bibliometrics, a taxonomy or a dashboard. Those are analysis features built on
top of a finished review. Under the original order they all shipped before the researcher
could export anything at all — so the tool would have had four analysis engines and no way
to produce a citable artefact.

The screening of a real corpus is also the long pole: 1,110 records is weeks of human work
that runs in parallel with whatever is being built. Stage 10 is the thing that becomes
useful the moment that screening finishes. Stage 6 is not.

## Decision

**Stage 10 is built before Stages 6-9, and the stage↔tag mapping ends.**

The realised order, with the tags actually cut:

| | | tag |
|---|---|---|
| Stages 0-4 | as planned | `v0.1.0` … `v0.5.0` |
| Phase 0a | truth and safety | `v0.6.0`, `v0.6.1` |
| Phase 1 | the operator's real capture | — |
| Phase 0b | Windows, subject areas, `identified` | `v0.7.0`, `v0.8.0`, `v0.9.0` |
| Stage 5 | screening UI | `v0.10.0` |
| **Stage 10** | **reporting and export** | **`v0.11.0`** |
| Stages 6-9 | analysis | after |
| Stage 11 | validation and release | `v1.0.0` |

Tags no longer track stage numbers. Phase 0a and 0b were breaking changes to a methodology
surface — `extra="forbid"` rejects `criteria.yaml` files that used to load, and a working
`subject_areas` legitimately changes corpus size — and honest version signalling is worth
more than a tidy mapping.

**Stage 10 ships the deliverables its acceptance criteria need, and no more.** BUILD_PLAN's
Stage 10 lists a taxonomy distribution, a dataset/benchmark usage table and a research-gap
table. Those read Stage 8 and Stage 9 output, which does not exist. They are deferred to the
stages that own their data.

What is delivered: `report/flow_diagram.py`, `report/tables.py` (eligibility criteria, top
venues, citation statistics, top-cited papers), `report/numbers.py`, `report/export.py`,
`report/fill.py`, and the `export` and `fill` CLI commands.

**All four acceptance criteria are reachable in that scope**, which is what makes the
reordering safe rather than merely convenient:

| | reachable from |
|---|---|
| S10-AC1 — every figure has a sibling source CSV | the exporter |
| S10-AC2 — manifest carries the git SHA, flags a dirty tree | git and the package |
| S10-AC3 — `fill` exits non-zero on an undefined key | the fill contract |
| S10-AC4 — diagram numbers equal `FlowCounts` | Stage 4, merged |

## Alternatives rejected

**Build Stages 6-9 first, as BUILD_PLAN orders them.** The plan's own order, and the tables
would have arrived complete.

*Rejected:* it inverts the priority the goal actually has. Until Stage 10 exists there is no
`numbers.json`, no PRISMA diagram and no manifest — so a researcher can screen 1,110 records
and then have nothing citable to show for it. Four analysis engines on top of a review
nobody can export is a worse state than an exportable review with fewer tables, and it
delays the only milestone an outside user can perceive.

**Ship the three blocked tables as empty placeholders**, so the deliverable list matches
BUILD_PLAN's literally.

*Rejected:* the same reasoning that keeps `prismabib code` from existing as a stub. An empty
"taxonomy distribution" table in `exports/tables/` is indistinguishable from a real one over
a corpus with no taxonomy yet, and a researcher who put it in a manuscript would be
publishing a table asserting something nobody computed. An absent table is honest; a
present, empty one is a claim.

**Defer Stage 10 until 6-9 land, and keep the mapping.** Preserves both the order and the
tag scheme.

*Rejected:* this is the first alternative wearing a different hat. It also compounds: every
stage after the first deviation would have to choose between a wrong tag and a wrong order.
The mapping is a convention, and the reordering already broke it at `v0.6.0`; pretending
otherwise for four more releases would make the tags actively misleading rather than merely
uninformative.

## Consequences

A researcher can complete a review at `v0.11.0`: capture, build, screen, export, fill. That
is the milestone this reordering exists to reach, and it arrives roughly four stages earlier
than the original plan allowed.

`exports/tables/` has four tables where BUILD_PLAN describes seven. Anyone reading the
export and expecting the taxonomy distribution will not find it, so the scope note is
repeated in `report/__init__.py`, `report/tables.py` and the export how-to rather than
living only here.

BUILD_PLAN's Stage 10 test table lists `test_export__source_csv__reproduces_the_figure_values`
against an `AnalysisResult` — a Stage 7 type. That test exists here, asserting against
`FlowCounts` instead, which is the data the figure it ships actually has.

**The tags will look wrong to anyone reading BUILD_PLAN alone.** Stage 5 shipped `v0.10.0`
where the plan says `v0.6.0`. This ADR is the only reconciliation between the two documents,
which is precisely why the five-release delay in writing it was a mistake worth recording.

Stages 6-9 inherit an obligation: each adds its table to `report/tables.py` and its keys to
`numbers.json`. The golden key-set snapshot makes each addition a reviewable diff, so the
deferral cannot be forgotten silently — the tables' absence is visible in a checked-in file.

## Constraints

- The three deferred tables are deferred, not cancelled. Stage 8 adds the taxonomy
  distribution and dataset/benchmark usage; the research-gap table follows its supporting
  counts.
- No stage may claim an acceptance criterion it cannot reach. The reordering was safe
  *because* all four of Stage 10's are reachable without 6-9; a future reordering has to
  demonstrate the same thing rather than assume it.
- Tags remain monotonic and every one keeps a matching GitHub Release. Stage 11 verifies
  that as a release gate, and it is the check that would catch a stage merged outside the
  §3.6 workflow.

## Related decisions

- [ADR 0011](0011-abstract-retrieval-for-subject-areas.md) — Phase 0b, one of the amended
  plan's releases.
- [ADR 0014](0014-mutation-gate-excludes-diagnostic-prose.md) — the mutation gate, whose
  first real run exposed `FlowCounts.assert_consistent` as invisible to mutation. Stage 10
  restructures it, because the export contract freezes around `FlowCounts` here.

## References

- BUILD_PLAN §Stage 10 (lines 1400-1430); §7 build-order table; §2.6.
- EXECUTION_PLAN line 207 ("Stage 10 requires ... effectively all analysis stages").
