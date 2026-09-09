# Validation

What this project has established, and what it has not.

`v1.0.0` releases the **instrument**. It does not report a completed systematic review, and
nothing on this page should be read as one. The distinction is recorded in
[ADR 0027](../architecture/adr/0027-what-v1-0-0-claims.md).

## What was established

### The pipeline runs end to end on a real corpus

`Baseball-CVPR`, a live Scopus corpus, exercised through every stage: search capture → Layer 1
store → decision log → bibliometrics → taxonomy → figures → export.

| | |
| --- | --- |
| Query | `TITLE-ABS-KEY` over baseball / pitching / balk terms intersected with pose estimation, graph convolutional networks, anomaly detection and computer vision (see `projects/Baseball-CVPR/project.toml`) |
| Retrieval date | 2026-09-02 |
| Scopus `total_results` | 1294 |
| Records loaded into Layer 1 | 1293 |
| Criteria version in force | 0.1.0 |
| Sealed search runs | 1 |

### Every number is reproducible across machines

Two independently provisioned CI runners — **Linux and Windows** — export the reference
project's `numbers.json` from a clean clone, and all 39 numbers are asserted byte-identical.
The volatile-key allowlist is **empty**: no number in that file is permitted to vary by machine.

This is the criterion that makes the tool trustworthy, and it is the one this project has
historically needed. Three machine-dependence defects reached `main` and passed every local
test: a timezone-sensitive citation query, an absolute path inside a checksummed table, and a
clock-derived year boundary. Each would have been caught by a cross-machine comparison, and none
by a same-machine one.

### The flow arithmetic closes

`FlowCounts.assert_consistent()` runs on every export and refuses to write a diagram whose
numbers do not balance. An export that produced an inconsistent PRISMA diagram would be the
§1.4 failure with a provenance file attached.

### The live Scopus contract still matches

`tests/live/test_scopus_live.py` passes **from an entitled network**, verified on 2026-09-09.
The nightly's copy is red for an environmental reason, disclosed below.

## What was not established

### No completed review, and therefore no reference-review comparison

BUILD_PLAN asks for a delta against a published review, with differences explained. There is
none to compare, because **screening has not been performed**:

```
identified   1294
removed         1
included        0
```

Thirty-five records carry a title/abstract decision; none carries a full-text decision.
**`included = 0` is the absence of a result, not a result.** Any figure, table or taxonomy
distribution generated over the `INCLUDED` set is correspondingly empty, and the captions say
so — every one states `n = 0 (included)`.

Screening is irreducibly human ([ADR 0003](../architecture/adr/0003-human-only-screening.md)),
and this release does not wait on it. The comparison remains open work for whoever completes a
review with this tool.

### The nightly's Scopus test is red, for a reason that is not a defect

The API key is valid and works from an institutional network. It is refused from
GitHub-hosted runners with HTTP 401 because the Scopus entitlement is bound to institutional IP
addresses, and a runner's is not among them.

The remedy is an institutional token (`X-ELS-Insttoken`), which the client already sends when
configured (`sources/scopus.py`). It has been requested and awaits the provider. Until then the
nightly reports one failure, and the release notes say so rather than leaving a red badge
unexplained.

### One limit of the cross-machine claim

Both reproducibility runners are GitHub-hosted. A defect depending on that platform
specifically would survive the comparison. Nothing available to this project can close that gap;
the alternative — reproducing on one machine — closes nothing at all.

## Reproducing this yourself

```bash
git clone https://github.com/SergioHuesca/PRISMA-Bib
cd PRISMA-Bib
uv sync --all-extras
uv run python scripts/export_reference_numbers.py numbers.json
```

Compare your `numbers.json` against another machine's with
`scripts/compare_reference_numbers.py`. That is the same pair of scripts CI runs, deliberately:
a reader should be able to execute the criterion, not take its word for it.

## Related

- [Limitations](limitations.md) — what the data and the method cannot tell you
- [PRISMA mapping](prisma-mapping.md) — how each flow number is derived
- [ADR 0027](../architecture/adr/0027-what-v1-0-0-claims.md) — what `v1.0.0` claims
