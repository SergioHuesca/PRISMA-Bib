# ADR 0014: The Mutation Gate Excludes Diagnostic Message Bodies

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
| Can change behaviour ("semantic") | 805 | 57 | 92.92% |
| String-literal only | 211 | 131 | 37.91% |
| **Total as measured** | **1016** | **188** | **81.40%** |

(827 caught of 1016 considered — 826 killed plus one timeout, which the gate counts
as caught, against 188 survivors and one segfault.)

mutmut generates three mutants per string segment: an `XX…XX` sentinel wrap, a lowercased
copy, and an uppercased copy. A carefully written multi-paragraph error message therefore
produces dozens of mutants. `engine._refuse_unenforceable_subject_filter` — one `raise`
whose message explains what a researcher should do instead — generated 54 mutants on its
own, of which 44 survived, nearly a quarter of every survivor in the project.

Killing those requires asserting the message verbatim, character and case. That produces a
change-detector test: it breaks on every rewording and catches no defect. It is §5 risk 12
("tests that assert the wrong thing") reached from the opposite direction — assertions
written to move a number rather than to state a requirement.

**This is a judgement about equivalent mutants, not an arithmetic certainty, and an earlier
draft of this ADR got that wrong.** It claimed that killing every structural survivor would
still reach only 862/1016 = 84.8%, "still under the gate". That figure is a category error:
862 is the semantic *population* (805) plus the semantic *survivor count* (57), which is not
a kill total. Killing all 57 gives 827 + 57 = 884/1016 = **87.01%** — over the gate. The
error survived into the CHANGELOG and the pull request, and it happened to land just below
the threshold, in the direction that supported the decision. It is recorded here rather than
quietly corrected, because a wrong number that flatters its author is exactly what this
project's review discipline exists to catch.

The honest statement of the problem is this. 85% of 1016 is 863.6, so the gate needed 864
caught, which is **37 of the 57 structural survivors**. Triage of those 57 found roughly 20
to 26 killable and the remainder genuinely equivalent — unreachable `else` branches on a
`fetchone()` DuckDB always answers, `_O_BINARY` on POSIX, `check=False` versus `check=None`,
init sentinels never read before being written. So the gate was *probably* unreachable, by a
margin of a handful of mutants, resting on a triage judgement rather than on a calculation.

That is a weaker claim than the one this ADR originally made, and it is the one the decision
actually rests on. What is not in doubt is the shape of the incentive: 211 of 1016 mutants
rewrote a string literal and 131 of them survived, so the measured rate was dominated by how
verbatim the suite asserts prose. The project was being penalised for writing good
diagnostics, which is a discipline BUILD_PLAN §1.4 and the `EntitlementError` rewrite of
Phase 0a both explicitly ask for.

## Decision

**Exempt the message body of a multi-line `raise` from mutation, per statement, with
`# pragma: no mutate start` / `# pragma: no mutate end`. Keep the 85% gate.**

That is the whole exemption: 20 statements. The condition deciding whether to raise is
*not* exempted and stays mutated — that is the behaviour; the prose is the explanation.

**SQL was in an earlier draft of this decision and has been removed.** The reasoning was
sound in the abstract — SQL keywords and unquoted identifiers are case-insensitive, so
`SELECT` → `select` is inert by definition of the language — but the implementation could
not express it. A pragma suppresses mutations by *line*, and the SQL in this codebase sits
inside the statement that runs it:

```python
row = connection.execute("""SELECT COALESCE(SUM(total_results), 0) ...""").fetchone()
```

Exempting the SQL therefore exempted `row = connection.execute(...)` → `row = None`, which
makes `_identified_count` return `0` — the "records identified" number in the published
PRISMA diagram silently becoming zero. The suite kills that mutant today; with the pragma
in place the gate would never again report if it stopped. Six such statements were
affected. Losing the ~18 inert SQL-case mutants to the survivor list costs about two
percentage points and is the correct trade.

Everything else stays mutated. In particular these short semantic strings, each of which
changes behaviour when its case is flipped, are **not** covered:

- `"shared"` / `"exclusive"` — the decision log's lock modes
- `"--format=%H"` — a git argument
- stage names, decision values, reason codes, `record_id` prefixes

**Being in scope is not the same as being covered, and the first of those is currently
both.** `_locked("shared")` → `_locked("SHARED")` survives: `log.py`'s POSIX backend reads
`LOCK_SH if kind == "shared" else LOCK_EX`, so the mutant silently makes every *read* take
an exclusive lock, and no test notices. Six survivors of that shape remain, along with a
`_capture_layer1(project, None)` group that opens a second connection and breaks the
single-read guarantee `engine.py` documents three lines above. They are real gaps, they are
filed as issue #23 rather than fixed here, and they are named in this ADR rather than left
in a list because citing these strings as evidence of narrowness while they go untested
would be having it both ways.

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
punishes improving a diagnostic is worse than no gate.

This alternative is rejected on that argument alone. An earlier draft added "it also would
not have worked: 84.8%", which is both the discredited figure and false: killing the 131
prose survivors gives 827 + 131 = 958/1016 = **94.29%**, comfortably over the gate. It
would have worked numerically and it is still the wrong thing to do — and propping a sound
argument up with a wrong number is the exact pattern this ADR was corrected for once
already.

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
  flow stays outside it. **This constraint was violated by the first implementation of
  this ADR** — the SQL spans enclosed the `connection.execute(...)` call that retrieves
  the data — which is why SQL is no longer exempt at all. A reviewer should read every
  span against this line, not against the intent stated above it.
- A pragma suppresses by *line*, so anything left inside a message body is exempted with
  it. **Compute above the pragma, never inside it.** Four expressions were originally left
  interpolated -- `' '.join(args)`, `list(criteria.subject_areas)`,
  `project.root / 'criteria.yaml'`, `sorted(allowed)` -- and each was suppressed along with
  the prose, turning a killed mutant into an exempt one. They are now assigned to locals
  above their `raise`. Measured after that change, the 20 spans suppress **122 string
  literals and 17 `Error(...)` → `Error(None)` message replacements, and nothing else**:
  no expression, no comparison, no call that computes anything. `Error(None)` leaves the
  exception type unchanged, which is the line this exemption draws.
- **mutmut does not mutate a decorated function** unless its only decorator is literally
  `staticmethod` or `classmethod`. In `src/prismabib/prisma/` that excludes **eight**
  functions from the gate's population entirely, not just the pydantic validators: the
  four `events.py` hooks (`_must_be_nonempty`, `_stage_must_be_loggable`,
  `_ts_must_be_timezone_aware`, `_serialize_ts`), `log.DecisionLog.path` and
  `.checksum_path` (`@property`), and -- most consequentially --
  `engine._layer1_connection` and `log._locked`, both `@contextmanager`. Those last two
  hold the connection and locking logic that issue #23's first two findings are about, so
  the blind spot overlaps precisely the code least covered. A pragma on such a function is
  a claim with no effect; one was written and removed. The 91.04% is measured over a
  population that excludes all eight.
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
