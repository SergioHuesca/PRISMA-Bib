# ADR 0027: What v1.0.0 Claims, and What It Does Not

## Status

Proposed — 2026-09-09. Records three deviations from BUILD_PLAN Stage 11, taken together because
they are one decision: what the `v1.0.0` release asserts about this project.

§2.6 requires an ADR for each. Taking them separately would let a reader assemble a claim from
three documents that none of them makes.

## Context

Stage 11 is a checklist-execution stage. Its criteria assume a completed systematic review:
*"run the full pipeline on a real topic … and record `docs/methodology/validation.md` (query,
retrieval date, flow counts, delta vs. the reference review with deltas explained)"*.

The corpus this project actually has is `Baseball-CVPR`, and its flow counts today are:

```
identified   1294
removed         1
included        0
```

**Screening has not run.** Thirty-five records carry a title/abstract decision; none carries a
full-text decision. `included = 0` is not a finding — it is the absence of one.

Screening 1293 records is irreducibly human labour, and ADR 0003 exists to keep it that way. It
is weeks of work, and it is not work the software is waiting on: every stage is built, tested
and released.

Two further facts bear on the release:

- The Scopus API key **works**, and is refused only from GitHub-hosted runners, because the
  entitlement is bound to institutional IP. `X-ELS-Insttoken` is the documented remedy, is
  already plumbed at `sources/scopus.py:716`, and awaits a third party.
- No second physical machine is available to the maintainer.

## Decision

### 1. `v1.0.0` claims the tool is complete and reproducible. It does not claim a review was conducted

This is the whole of it, and every other decision here follows.

A `1.0.0` of a research *instrument* means the instrument works, its numbers are traceable, and
a reader can reproduce them. It does not mean somebody finished a study with it. Those are
different claims and conflating them would be the §1.4 failure applied to the project's own
release notes.

So `docs/methodology/validation.md` records what was actually established — the corpus, the
query, the retrieval date, the flow counts as they stand, and **explicitly that screening is
incomplete, so `included = 0` is not a review result**. The reference-review delta BUILD_PLAN
asks for is deferred, because there is no completed review to compare, and inventing one would
be worse than omitting it.

**The document must be readable by someone who did not read this ADR** and leave them unable to
mistake it for a completed review. That is its acceptance criterion.

### 2. The `live` nightly's Scopus failure does not block the release, and is named in the release notes

Blocking `v1.0.0` on an Elsevier support queue makes the release hostage to something outside
the code, for a failure that is not a defect in the code. The key is valid; a GitHub runner's IP
is not covered by the institutional entitlement.

What the checklist actually wants — *evidence the live contract still matches Scopus's API
shape* — is available and was obtained: the live Scopus test **passes from an entitled network**
and was run there. The release notes say so, and say plainly that the nightly's copy of that
test is red for an environmental reason, with the remedy pending.

An undisclosed red test would be dishonest. A disclosed one, with its cause and remedy named, is
the ordinary condition of software that depends on a licensed third-party API.

### 3. "A different machine" is satisfied by two independently provisioned CI runners

BUILD_PLAN requires a clean clone **on a different machine** to reproduce `numbers.json`,
because reproducing from a local copy proves nothing about what a reader can obtain.

No second machine is available here. Two GitHub-hosted runners are a *stronger* substitute than
two laptops would be, not a weaker one: each is provisioned fresh with no shared state, and the
comparison can span operating systems as well. So the criterion is met by exporting
`numbers.json` from a clean clone on **Linux and on Windows** and asserting the two are
byte-identical, modulo an explicit, reviewed timestamp allowlist.

That is a harder test than the spec describes. Two machines the maintainer owns would share an
OS, a filesystem, a locale and a Python build — the four axes on which this project has already
shipped three machine-dependence defects.

The honest limit, stated rather than glossed: both runners are GitHub-hosted, so a defect
depending on that platform specifically would survive. Nothing available here can close that,
and the alternative — reproducing on one machine — closes nothing at all.

## Alternatives considered

**Screen enough of `Baseball-CVPR` to produce a non-zero `included`.** Rejected as a release
blocker, not as work: it is weeks of human judgement, and a partially-screened corpus would
produce a *smaller* wrong number rather than a right one. Screening continues after the release,
using the released tool — which is the order these things should happen in.

**Wait for the institutional token before tagging.** Rejected in Decision 2.

**Claim validation without the caveats, on the grounds that the pipeline demonstrably runs.**
Rejected, and it is the failure this entire project was built to prevent. A `validation.md` that
reads as a completed review, on a corpus with zero included studies, is a plausible wrong number
in a published document.

## Consequences

1. **`v1.0.0` is a release of the software, and the release notes say so in the first line.**
2. **`validation.md` will read as thinner than BUILD_PLAN envisaged**, because it reports what
   was established rather than what was hoped for. That is the correct trade and the document
   says why.
3. **The reference-review comparison remains open work**, recorded as such — for a `v1.1` or for
   whoever completes a review with this tool.
4. **A reader can reproduce every number in the bundle**, which is the property that actually
   makes the tool trustworthy, and it is now tested across two machines and two operating
   systems.
5. **One release-checklist line ships unmet and disclosed.** A checklist whose items may be
   quietly skipped is not a checklist; this one is named, in the release notes and here.

## Related decisions

- [ADR 0003](0003-human-only-screening.md) — why screening cannot be automated to close this gap
- [ADR 0006](0006-public-repository-and-single-owner-review.md) — the single-owner constraint
  that makes "a different machine" hard here
- [ADR 0026](0026-figures-ship-as-svg-only.md) — the byte-reproducibility argument this reuses
