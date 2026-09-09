# ADR 0026: Figures Ship as SVG Only, and PNG Is Deferred Rather Than Forgotten

## Status

Accepted — 2026-09-09. Records a deviation from BUILD_PLAN Stage 10, which specifies the PRISMA
diagram as *"SVG/PNG"* and `exports/figures/` as holding *"PNG + SVG + the source dataframe as
CSV next to each"*. Only SVG ships.

§2.6 requires an ADR for a frozen-contract deviation. [ADR 0015](0015-stage-order-and-stage-10-scope.md)
covers Stage 10's three deferred tables and does not reach this one, so the omission has been
undocumented since Stage 10 shipped — raised in [issue #25](https://github.com/SergioHuesca/PRISMA-Bib/issues/25) §3.

## Context

Two of this project's own criteria pull against raster output.

**Stage 11 requires a clean clone on a different machine to reproduce `numbers.json`**, and the
same reasoning applies to every exported artefact a reader might diff. A PNG is not byte-stable
across machines: the encoder embeds font rasterisation, which depends on the installed font
files and their versions, and on the rendering library's own version. Two correct runs of the
same code on two machines produce different bytes for the same figure.

**Stage 9 already rejected pixel comparison for exactly this reason.** ADR 0025 Decision 8 keeps
visual regression out of scope because it is "brittle across matplotlib versions and font
stacks", and has goldens operate on SVG text and structure instead. Shipping a PNG would
reintroduce, in the export bundle, the fragility the test suite deliberately declined.

An SVG is text. It diffs, it greps, it survives a clean-clone comparison, and a reader can open
it in a browser or drop it into a manuscript unchanged.

## Decision

### 1. `exports/figures/` holds SVG and the source CSV beside it. No PNG.

The rule S10-AC1 states — every figure has a sibling CSV that reproduces its values — is
unchanged and applies to whatever formats are present.

### 2. The acceptance test stops assuming the format

`test_export__every_figure__has_a_sibling_source_csv` globbed `*.svg`. A PNG added later would
therefore have escaped S10-AC1 **entirely and silently**, on the day it was added — the
criterion would have kept passing while the file it was written to govern went unchecked.

It now walks every file in `figures/` that is not itself a source CSV. That makes the criterion
about *figures*, which is what it says, rather than about SVG, which is what it happened to
measure. Verified by planting a PNG with no sibling CSV and confirming the test reds.

This matters more than the format decision it accompanies: a criterion that silently narrows to
whatever the implementation currently does is the recurring defect shape this project keeps
finding, and it had one here.

### 3. Adding PNG later is a decision with a stated cost, not an oversight

If a journal demands raster, the export can gain it — but whoever does that must also answer
what it means for Stage 11's reproducibility criterion, because a bundle containing a PNG can no
longer be compared byte-for-byte across machines. The honest options at that point are to
exclude raster from the comparison explicitly, or to accept that the criterion covers only the
text artefacts. Both are defensible; neither should be arrived at by accident.

Matplotlib already produces the figures, so this is a rendering call rather than new machinery.
The cost is not implementation effort.

## Alternatives considered

**Ship PNG and exclude it from the reproducibility comparison.** Rejected for now: it puts an
exception into Stage 11's criterion before anything has asked for one, and an exception granted
speculatively is harder to remove than one granted on demand.

**Ship PNG and pin it with a perceptual hash.** Rejected: that is pixel comparison with a
tolerance, which ADR 0025 Decision 8 already declined on the same evidence.

## Consequences

1. **`exports/figures/` is byte-comparable across machines**, which is what Stage 11's
   clean-clone criterion needs of it.
2. **S10-AC1 now governs any format**, so the next figure format cannot arrive unchecked.
3. **A reader wanting raster converts the SVG themselves.** Stated plainly because it is a real
   cost to someone, not a neutral trade.

## Related decisions

- [ADR 0015](0015-stage-order-and-stage-10-scope.md) — Stage 10's other scope deferrals
- [ADR 0025](0025-figures-render-what-the-engines-computed.md) — Decision 8, which declined
  pixel comparison on the same evidence about font stacks and library versions
