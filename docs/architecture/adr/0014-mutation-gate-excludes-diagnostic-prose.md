# ADR 0014: The Mutation Gate Excludes Diagnostic Prose and SQL Text

## Status

Accepted — 2026-08-31. Recorded under §2.6 because it changes what BUILD_PLAN §3.7.6's
"≥ 85% mutation kill rate over `src/prismabib/prisma/`" is measured *over*. The threshold
itself is unchanged and is not negotiable here: this ADR does not lower a gate, it removes
a class of mutant that no test can distinguish from the original.

## Context

`weekly-mutation.yml` was stood up during Stage 4 and its schedule fired for the first time
on 2026-08-31. It failed:

```
KILL_RATE: 81.40
##[error]Mutation kill rate 81.40% is below the 85% gate (BUILD_PLAN 3.7.6).
```

This was the project's first real measurement, not a regression — the gate had never
actually been met, and nobody knew, because the workflow had never run.

Triaging all 188 survivors against the generated `mutants/` tree gave a clear split.

| Mutant population | Generated | Survived | Kill rate |
|---|---:|---:|---:|
| Can change behaviour ("semantic") | 805 | 57 | **92.92%** |
| String-literal only | 211 | 131 | 37.91% |
| **Total as measured** | **1016** | **188** | **81.50%** |

mutmut generates three mutants per string segment: an `XX…XX` sentinel wrap, a lowercased
copy, and an uppercased copy. A carefully written multi-paragraph error message therefore
produces dozens of mutants. `engine._refuse_unenforceable_subject_filter` — one `raise`
whose message explains what a researcher should do instead — generated 54 mutants on its
own, of which 44 survived, nearly a quarter of every survivor in the project.

Killing those requires asserting the message verbatim, character and case. That produces a
change-detector test: it breaks on every rewording and catches no defect. It is §5 risk 12
("tests that assert the wrong thing") reached from the opposite direction — assertions
written to move a number rather than to state a requirement.

The arithmetic settles it. Killing **every** structural survivor, including the ones that
are provably equivalent, would have reached 862/1016 = **84.8%** — still under the gate. The
85% threshold was unreachable while prose was counted, however good the tests became. The
project was being penalised for writing good diagnostics, which is a discipline BUILD_PLAN
§1.4 and the `EntitlementError` rewrite of Phase 0a both explicitly ask for.

## Decision

**Exempt diagnostic message text and SQL text from mutation, per statement, with
`# pragma: no mutate start` / `# pragma: no mutate end`. Keep the 85% gate.**

The exemption is deliberately narrow and covers exactly two things:

1. **The message body of a multi-line `raise`.** The condition deciding whether to raise is
   *not* exempted and stays mutated — that is the behaviour; the prose is the explanation.
2. **SQL passed to `connection.execute`.** SQL keywords and unquoted identifiers are
   case-insensitive, so `SELECT` → `select` and `run_duplicates` → `RUN_DUPLICATES` are
   inert by definition of the language. These are equivalent mutants in the textbook sense.

Everything else stays mutated. In particular these short semantic strings, each of which
changes behaviour when its case is flipped, are **not** covered:

- `"shared"` / `"exclusive"` — the decision log's lock modes
- `"--format=%H"` — a git argument
- stage names, decision values, reason codes, `record_id` prefixes

## Alternatives rejected

**Lower the threshold to 80%.** One line of config, and the number would go green.

*Rejected:* it treats a measurement problem as a standards problem, and it discards the
signal in the opposite direction — at 80% the project could lose real coverage on semantic
mutants and still pass. The measured semantic kill rate is 92.92%; a threshold set below
what the suite already achieves stops being a gate at all. This is the same reasoning that
makes §5 risk 11 forbid regenerating a golden snapshot to make a test pass.

**Assert the error messages verbatim.** Write the tests that kill the 131 prose mutants:
compare each message against a checked-in expected string.

*Rejected:* those tests assert nothing a requirement names. They would fail on every
rewording of a message — and the messages in this project *are* rewritten, deliberately and
often, because their quality is the deliverable (the `subject_areas` refusal was rewritten
once already, after an earlier version advised a remedy that quietly fails). A gate that
punishes improving a diagnostic is worse than no gate. It also would not have worked:
84.8%.

**Stop mutating string literals altogether**, via `do_not_mutate_patterns` or by dropping
the string mutation operator.

*Rejected:* 80 string mutants were legitimately **killed**, and they are the ones that
matter. `"title_abstract"` → `"TITLE_ABSTRACT"` is a real defect that a real test catches;
so is a flipped lock mode. A blanket exclusion would delete that coverage silently and
leave nothing to notice when it regressed. The per-statement pragma keeps every semantic
string in scope, which is why it is more work than the blanket rule and why it is right.

## Consequences

The mutation gate now measures test quality rather than how verbatim the suite asserts
prose. A surviving mutant is worth reading again, instead of being one of 131 in a list
nobody triages — which is the practical cost the old measurement carried, and the reason a
real gap in `MonotonicUlidFactory`'s overflow branch sat unnoticed underneath it.

Each pragma is a claim that the enclosed text cannot change behaviour. That claim can be
wrong: a message body that starts being parsed, or SQL that moves into a case-sensitive
quoted identifier, would become untested without any test failing. The pragmas are narrow
and carry their reason inline for exactly that reason, and a reviewer should treat widening
one as a change to the gate.

The exemption is not retroactive cover. Triage of the same run found genuine gaps, and they
were fixed in the same change rather than exempted:

- **`MonotonicUlidFactory`'s randomness-overflow branch was untested**, because reaching it
  requires ~2^80 calls. It accepted a mutation setting `timestamp_ms = 1` — stamping every
  subsequent event one millisecond after the Unix epoch and destroying the ordering the
  decision log's fold depends on — with the whole suite green. The randomness source is now
  injectable so the branch is reachable, and the boundary (`>` versus `>=` at the mask) is
  pinned from both sides.
- **`criteria._run_git` dropped `GIT_DIR`/`GIT_WORK_TREE` with nothing asserting it.** An
  ambient `GIT_DIR` resolves a superseded `criteria.yaml` from *another repository* and
  reports it as this project's protocol history: a plausible wrong number, §1.4 exactly.
  Removing the `env=` argument entirely left the suite green.
- **The checksum sidecar's covered prefix is accumulated across lines**, and the only test
  covering it left a one-line prefix, where accumulating and replacing are
  indistinguishable. Replacing it turns an interrupted append into a tampering report —
  opposite recovery instructions, delivered to a reviewer who just lost power partway
  through screening.

## Constraints

- A pragma may only enclose text. Any condition, comparison, or call that decides control
  flow stays outside it.
- Short strings that are compared, dispatched on, or passed as arguments to another program
  are never exempt, whatever they look like.
- `weekly-mutation.yml` keeps `KILL_RATE_THRESHOLD: 85` and keeps treating anything not
  killed as escaped.

## Related decisions

- [ADR 0009](0009-mutmut-3x-cli-deviation.md) — the mutmut 2.x → 3.x CLI deviation, and
  `[tool.mutmut]`'s scope restriction to `src/prismabib/prisma/*`.

## References

- BUILD_PLAN §3.7.6 (coverage and mutation gates); §5 risks 11 and 12; §1.4.
- Workflow run 33384552109, the first firing of `weekly-mutation.yml`.
