# ADR 0021: An Entitlement Refusal Is Only Recorded Against a Publisher the Resolver Could Serve

## Status

Accepted — 2026-09-04. Amends [ADR 0019](0019-fulltext-resolution-and-coverage.md)'s constraint
*"A 403 records `entitled = false` and the chain continues"*, which is unconditional and turns
out to be wrong in one direction. Every other ADR 0019 and
[ADR 0020](0020-crossref-tdm-and-assisted-manual-fetch.md) constraint stands: the chain still
continues past a refusal, `INACCESSIBLE` is still constructible only inside `screening/`,
publisher still comes from the DOI, and the three-valued `entitled` vocabulary is unchanged.

**No schema change. No fifth status.** This ADR changes which of the three existing values a
refusal is recorded under.

## Context

The by-publisher coverage table produced by the first real run reads:

| Publisher | Records | Resolved | Refused | Coverage (%) |
| --- | --- | --- | --- | --- |
| IEEE | 17 | 0 | **17** | 0.00 |
| Springer | 5 | 0 | **5** | 0.00 |
| Nature Portfolio | 1 | 0 | **1** | 0.00 |

Read into a limitations section, "IEEE: 17 refused" says **we were denied access to 17 IEEE
papers**. We never asked IEEE. ScienceDirect — Elsevier's API — refused, and it never held
those papers. "Nature Portfolio: 1 refused" is plainly false: that record is in *Scientific
Reports*, which is fully open access and was in fact resolved from it.

An unentitled Elsevier key 403s every request before it looks at the DOI, so all 35 records —
33 of them not Elsevier's — were recorded as entitlement gaps.

This is the **mirror** of the defect the second review pass of ADR 0019's PR caught. That one
*under*-reported the gap: a publisher refused across the board had no resolved records and so
vanished from a table listing only publishers with one. This one *over*-reports it. Both put a
wrong number in the artefact whose entire job is to state the coverage bias honestly, which is
BUILD_PLAN §1.4.

## Decision

### 1. A refusal is `entitled = false` only when the refusing resolver could serve that record's publisher

Some resolvers are constrained to one publisher's content by construction. `ScienceDirect` is
Elsevier's Article Retrieval API and can never serve an IEEE paper. Others —
`CrossrefTdmResolver`, `OpenAccessResolver`, `ManualDropResolver` — are not constrained: they
follow a publisher-declared link, an open-access location, or a local file, so a refusal from
one *is* an entitlement question about whatever that record is.

So the rule is:

| refusing resolver | record's publisher | recorded |
| --- | --- | --- |
| constrained, and it matches | Elsevier paper refused by ScienceDirect | `entitled = false` |
| constrained, and it does not | IEEE paper refused by ScienceDirect | `entitled = NULL` |
| unconstrained | any paper refused by Crossref TDM | `entitled = false` |

`NULL` already means *"not an entitlement question"* in ADR 0019's vocabulary, and a
ScienceDirect 403 for an IEEE paper is exactly that. The value is not new; the attribution is.

### 1b. The attribution is derived in Layer 1, not recorded in Layer 0

*Added after implementation, when the fix was measured on the real corpus and did not repair
it.*

Applying Decision 1 at capture time corrects new attempts and leaves every sealed run alone —
which is right, because Layer 0 is immutable, and wrong, because the corpus that exposed the
defect already holds five IEEE refusals recorded before the fix. Its table still read
"IEEE: 5 refused". A fix that cannot reach the only data that exists has not fixed the
problem, and re-resolving from scratch to repair a *label* would re-spend a weekly quota.

The mistake was locating the derivation in the wrong layer. **A 403 is a fact; "this is an
entitlement gap for Springer" is an interpretation of that fact**, and this project's whole
architecture says facts live in Layer 0 and interpretations are derived from them
([ADR 0018](0018-abstract-runs-in-layer-1.md): "any table Layer 1 holds must be a function of
Layer 0").

So `attempts.jsonl` keeps recording `entitled: false` for any 403 — unchanged, and already
exactly the raw fact "this resolver was refused" — and `store/load.py` applies Decision 1 when
it builds `fulltext_assets`, using the record's DOI, which Layer 1 already holds. No Layer 0
format change, so every existing sealed run is re-interpreted correctly by
`prismabib build --rebuild`, at no quota cost.

The two layers therefore use the same field name for different things, which must be stated
plainly wherever either is documented: **Layer 0 `entitled=false` means "refused"; Layer 1
`entitled=false` means "refused, and it counts against this publisher".**

### 2. A publisher we cannot identify cannot substantiate a claim

A record with no DOI, or with a DOI prefix `publishers.py` does not map, yields `unknown`. A
constrained resolver's refusal on such a record records `NULL`, not `false`.

The asymmetry is deliberate. Over-reporting is an **active false statement** in a published
methods section — "we were refused 17 IEEE papers" — while under-reporting is an omission from
a limitations paragraph. Given a genuine inability to attribute, the honest record is that we
could not attribute it. `unmapped_publisher` values remain visible through
`publisher_from_doi`'s `matched` flag, so the population is not silently lost.

### 3. The attempt is still made

Only the *recording* changes. A constrained resolver still tries every record, because the DOI
prefix is a strong hint about a publisher and a poor one about what an API actually serves —
Elsevier has published under prefixes beyond `10.1016`, and skipping on a prefix mismatch would
silently drop fetchable content. That is the same failure in the other direction, and it costs
a paper rather than a footnote.

The wasted quota is real and is noted rather than fixed here: an unentitled key spends one call
per record to learn one fact, which is what Decision 4 addresses.

### 4. A consecutive-refusal breaker, not a first-record probe

`capture/enrich.py` aborts a fresh run when its **first** record is refused, so an unentitled
key costs one call rather than eighteen hundred. That shape does not transfer. Under Decision 1
a ScienceDirect 403 on a non-Elsevier first record is not a refusal at all, and on a corpus
where Elsevier is a minority the probe would sit un-armed for most of the run — or fire on
whichever record happened to be first.

The robust form of the same idea is a threshold on **consecutive genuine refusals from one
resolver, with nothing yet resolved by it**. That is what an unentitled key actually looks
like, and it cannot be triggered by a single embargoed article. `enrich.py` already carries the
same reasoning for a different symptom, in `CONSECUTIVE_NOT_FOUND_LIMIT`.

## Alternatives rejected

### 1. Skip the attempt when the publisher cannot match

Do not call ScienceDirect at all for a non-Elsevier DOI: no wasted quota, and no refusal to
reclassify.

*Rejected.* It makes retrieval depend on a prefix table that is necessarily incomplete. Elsevier
imprints publish under several prefixes, and a missing entry would silently skip a paper the
API would have served — trading a wrong footnote for a missing paper. `_TDM_HOSTS_ALREADY_COVERED`
skips on an exact **host** match, which is a fact about the API endpoint rather than an
inference about a publisher, and that is why it is safe there and not here.

### 2. A fourth `entitled` value for "not applicable"

Distinguish "asked, refused" / "asked, refused but irrelevant" / "not an entitlement question".

*Rejected.* ADR 0019's constraint fixes the vocabulary at three values, and consumers read
`entitled IS FALSE` as "an entitlement gap". A fourth value means every consumer must be
updated or it silently mis-reads, and the distinction it adds is one no report needs: a refusal
that cannot be attributed to a publisher has no place in a per-publisher table at all.

### 3. Report the raw refusal and explain it in prose

Keep `entitled = false` everywhere and add a footnote that some refusals are artefacts.

*Rejected* for the reason ADR 0019 rejected the same shape once already: diagram and table cells
get transcribed into manuscripts without their footnotes, and a number that is only correct
when accompanied by prose is the wrong number.

## Consequences

1. **The by-publisher table stops claiming refusals that never happened.** On the measured
   corpus, IEEE's 17, Springer's 5 and Nature Portfolio's 1 become `NULL` — not resolved, not
   refused, simply not reachable by the resolvers available.
2. **Elsevier's genuine gap still shows.** An unentitled key still records `entitled = false`
   for Elsevier records, which is the true and useful statement: *this reviewer lacks Elsevier
   access*.
3. **No published PRISMA number moves.** `entitled` feeds only the coverage tables; the flow
   counts come from the decision log and Layer 1.
5. **A rebuild repairs an existing corpus.** Because the attribution is derived rather than
   stored, `prismabib build --rebuild` corrects runs sealed before this ADR without
   re-fetching anything.
4. **An unentitled key now surfaces early** rather than after a full corpus, via the
   consecutive-refusal breaker.

## Constraints

- Layer 1's `entitled = false` is derived, and set only when the refusing resolver's publisher
  constraint is satisfied by the record's own publisher. Layer 0 keeps recording the raw
  refusal, so the derivation can be corrected by a rebuild.
- A resolver that is not publisher-constrained records `false` on any refusal.
- An unidentifiable publisher records `NULL`, never `false`.
- The attempt is still made; only the recording changes.
- The breaker counts **consecutive genuine refusals with nothing resolved by that resolver**,
  and cannot fire on a single embargoed article.

## Related decisions

- [ADR 0019](0019-fulltext-resolution-and-coverage.md) — the constraint amended, and the
  under-reporting mirror of this defect
- [ADR 0020](0020-crossref-tdm-and-assisted-manual-fetch.md) — the host-dedup rule, which skips
  on an exact endpoint match rather than a publisher inference
- [ADR 0017](0017-subject-areas-match-by-asjc-grouping.md) — `publishers.py`'s shape, and why an
  unmapped value is preserved rather than guessed

## References

- `src/prismabib/fulltext/resolve.py` — `resolve_fulltext`'s refusal handler
- `src/prismabib/publishers.py` — `publisher_from_doi`
- `src/prismabib/fulltext/coverage.py` — the tables this corrects
- [Issue #36](https://github.com/SergioHuesca/PRISMA-Bib/issues/36)

---

This ADR records that an entitlement refusal is attributed to a publisher only when the
refusing resolver could have served it, that an unidentifiable publisher records `NULL` rather
than `false`, that the attempt itself is unchanged, and that an unentitled key is detected by a
consecutive-refusal breaker rather than a first-record probe. Recording `false` unconditionally,
skipping attempts on a publisher mismatch, or adding a fourth `entitled` value requires a new
ADR that supersedes this one (§2.6).
