# ADR 0024: One Implementation of Append-Only Durability, and the Guard That Has to Widen With It

## Status

Proposed — 2026-09-04. Amends [ADR 0002](0002-append-only-decision-log.md), which describes
`DecisionLog` as *the* implementation of this project's append-only guarantees. It is now one of
two users of a shared implementation.

Written **after** the code, during Stage 8's review. That order is the wrong one and is recorded
as such: the extraction arrived as an unrequested part of a taxonomy PR, in the module that
protects the one artefact this project cannot recompute.

## Context

Stage 8 needs an append-only log for taxonomy overrides with the same properties the decision
log has: crash-safe ordered writes, a checksum sidecar, cross-platform locking, tamper
detection on load. ADR 0023 Consequence 2 says overrides "get the decision log's guarantees".

There were two ways to honour that. Duplicate the mechanics into `overrides.py`, or extract them
once and have both compose the result. The implementation chose extraction — a 634-line diff in
`prisma/log.py` — and the review then had to establish, after the fact, that a module nobody
asked to change had not changed.

It had not, and the evidence is worth recording because it is the standard any future change
here should meet:

- **Log bytes, sidecar bytes, sidecar filename and directory contents are identical** after every
  append, verified by running both implementations side by side against identical projects.
- **The syscall order is unmoved**, verified by tracing: data write → data fsync → sidecar tmp →
  tmp fsync → close → atomic rename.
- **The Windows byte-range locking backend is byte-identical**, including which byte is locked
  and when the sentinel is taken.
- The refactored code reads the live project's real 842-event log and its sidecar validates.
- No test function was deleted from the decision-log suite.

## Decision

### 1. One `AppendOnlyLog`, composed by both logs

The mechanics live in one place. Two implementations of crash-safety is the worse failure:
a fix applied to one and not the other is invisible until a machine loses power, and the
duplicate would drift silently because nothing compares them.

### 2. The ordering that everything depends on gets a test

The extraction's real problem was not correctness, it was that **the property the module
docstring spends four paragraphs on was protected by nothing.** Demonstrated by injection: with
the sidecar written *before* the data write and fsync — the exact inversion that lets a crash
leave a valid-looking sidecar for an append that was lost — the full 282-test prisma and
taxonomy suite still passed. Deleting the data `fsync` outright: also 282 passed.

So a syscall-ordering test lands with this ADR. It is the acceptance criterion the extraction
should have had, and it protects a guarantee that predates the extraction and was never covered.

### 3. The INACCESSIBLE guard widens to match the new surface

`tests/unit/test_inaccessible_ast.py` forbids any code path that writes an `INACCESSIBLE`
decision event (ADR 0019: only a human, during full-text screening, may record one). It matches
on the *attribute name* of the write call.

Extraction created a second, public path to the same file that the guard does not see:
`AppendOnlyLog.write_event` is exported, and `DecisionLog._store.write_event(event)` writes a
validated-looking event to `decisions.jsonl` — fsynced, sidecar updated, loadable — **skipping
`_validate_business_rules` entirely.** Confirmed by running: it accepts an `INACCESSIBLE` event,
and it accepts a `reason_code` that the project's `criteria.yaml` does not declare at all, both
of which `append_event` refuses.

Before extraction, writing an unvalidated decision meant reimplementing the locking and checksum
machinery by hand. Now it is one attribute access, and — unlike the evasions the guard's
docstring already enumerates — it requires no deliberate obfuscation. It is the obvious thing a
future author reaches for.

So the guard grows two rules: **constructing `AppendOnlyLog` with `model=DecisionEvent` outside
`log.py` is refused, and `._store` access is refused outside the class that owns it.** A guard
that only catches the spelling that existed when it was written is a guard against the past.

### 4. Three error messages changed, and they are named

The extraction made three `LogError` messages less specific, because a generic log cannot say
"decision": the reentrancy message, the missing-sidecar message, and the truncated-line recovery
narrative — which dropped the word "screening" from a sentence
[ADR 0010](0010-cross-platform-decision-log-locking.md) quotes as the log's defining property.
No test or document asserts them, which is why review caught them and the suite did not. They
are restored to their decision-specific wording, since the composing class knows what it holds.

One message became *more* correct: the checksum-mismatch text now interpolates the log's own
filename where it previously hardcoded `decisions.jsonl`.

## Alternatives considered

**Duplicate the mechanics in `overrides.py`.** Rejected in Decision 1.

**Land the extraction as its own PR, before Stage 8.** This was the review's recommendation and
it is the right *default* — a change of this blast radius in this module deserves its own
review, and bundling it meant the verification that established equivalence lived in a scratch
directory rather than the repo.

It is not taken here for one reason: the gap the split was meant to close is closed better by
Decision 2. Splitting would have produced two PRs and the same untested ordering; adding the
syscall test produces one PR and a guarantee the project has never had. The judgement is
recorded so a reader can disagree with it. **The general rule stands and is not weakened by this
exception: an unrequested refactor of a durability-critical module belongs in its own PR.**

## Consequences

1. **`OverrideLog` and `DecisionLog` cannot drift.** A durability fix reaches both, and the
   conformance suite runs against both.
2. **The write ordering is now covered**, for the first time, for both logs.
3. **The INACCESSIBLE guard covers a surface that did not exist before**, and its docstring lists
   this evasion alongside the ones it already knew about.
4. **`AppendOnlyLog` is public API.** Anything else that ever needs these guarantees composes it
   rather than copying it — and inherits Decision 3's obligation to keep the guard current.

## Related decisions

- [ADR 0002](0002-append-only-decision-log.md) — the guarantees, now implemented once and used
  twice
- [ADR 0010](0010-cross-platform-decision-log-locking.md) — the locking backends, unchanged
- [ADR 0019](0019-fulltext-resolution-and-coverage.md) — why an `INACCESSIBLE` decision may only
  be written by a human, which Decision 3 keeps enforceable
- [ADR 0023](0023-taxonomy-assignments-are-derived-and-overrides-replace-a-dimension.md) — the
  stage that needed a second log
