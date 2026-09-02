# ADR 0019: Full-Text Resolution Records Every Attempt, and Publisher Comes From the DOI

## Status

Accepted — 2026-09-02. Implements BUILD_PLAN §Stage 6 (lines 1121–1179). **Fourth addition to
the frozen Layer 1 schema** (§Stage 3, lines 847–879), after ADR 0012's `malformed_entries`,
ADR 0013's `run_duplicates`, and ADR 0018's `abstract_runs` / `record_subject_area_coverage`.
Two new tables, `fulltext_assets` and `fulltext_sections`. No existing table, column, type or
primary key changes.

BUILD_PLAN names `fulltext_assets` and fixes its columns; it names `fulltext_sections` without
specifying any. It says nothing about how "publisher" is determined for the coverage table
that S06-AC3 requires. Those two gaps are what this ADR decides.

Tag will be `v0.16.0`, not BUILD_PLAN's `v0.7.0`: [ADR 0015](0015-stage-order-and-stage-10-scope.md)
ended the stage-to-tag mapping.

## Context

Stage 6 exists to answer `Accessible(p) ∧ PrimaryResearch(p)` **without letting an entitlement
limit silently bias the corpus**. BUILD_PLAN states the hazard directly: the ScienceDirect API
serves Elsevier content only, so implementing `Accessible(p)` as "ScienceDirect returned text"
makes the corpus Elsevier-weighted and every downstream venue and geography statistic wrong.

That is not a hypothetical for this project. On `Baseball-CVPR`, 35 records are currently
sought for retrieval and 0 are included, because there is nowhere to record a full-text
outcome at all.

The frozen design answers the hazard with a resolver chain — ScienceDirect, then open access,
then a manual drop directory — plus three hard rules: a 403 records `entitled = false` and
**moves on**; `INACCESSIBLE` may only ever be written by a human; and the Stage 9 report must
show coverage by resolver *and by publisher*, so the skew appears in the output rather than in
a footnote.

## Decision

### 1. `fulltext_assets` holds one row per resolver *attempt*, not per asset

```sql
CREATE TABLE fulltext_assets (
  record_id TEXT, resolver_name TEXT, media_type TEXT, path TEXT,
  retrieved_at TIMESTAMP, entitled BOOLEAN,
  PRIMARY KEY (record_id, resolver_name)
);
```

The columns are BUILD_PLAN's, verbatim. The primary key and the per-attempt reading are this
ADR's.

An asset-only table cannot satisfy S06-AC2. A ScienceDirect 403 produces no asset, yet the
acceptance criterion requires that `entitled = False` be *recorded* — and `entitled` would be
a constant `true` on a table of successes, carrying no information. The refusal is the datum:
it is what distinguishes "this paper is not available" from "we are not subscribed to this
paper", and conflating those two is precisely the bias the stage exists to prevent.

So `path` and `media_type` are `NULL` when an attempt yielded no asset, and `entitled` is
three-valued and nullable:

| `entitled` | meaning |
| --- | --- |
| `true` | the resolver could access this record (an asset was obtained) |
| `false` | the resolver was **refused** — HTTP 403. An entitlement gap, not an absent paper |
| `NULL` | not an entitlement question — no open-access copy exists, no manual drop, HTTP 404 |

Distinguishing `false` from `NULL` is what lets the coverage table say "we were refused 41
IEEE papers" rather than "41 IEEE papers were unavailable".

### 2. `fulltext_sections` carries `low_confidence` per section

```sql
CREATE TABLE fulltext_sections (
  record_id TEXT, position INTEGER, section_name TEXT, text TEXT,
  low_confidence BOOLEAN,
  PRIMARY KEY (record_id, position)
);
```

`position` preserves document order, which section names alone cannot: a reader comparing
"methods" across papers needs to know it came before "results". `low_confidence` is per
section rather than per document because a ScienceDirect XML document and a scanned PDF can
both contribute to one record; flagging the document would either over- or under-claim. It is
set when `pdfplumber` finds no text layer. **No OCR** — the flag exists so a human reads that
paper, per BUILD_PLAN.

### 3. Publisher is derived from the DOI registrant prefix, never from the resolver

A new `src/prismabib/publishers.py`, shaped like the existing `countries.py` and `asjc.py`: a
closed checked-in table, a `(value, matched)` return, unmapped prefixes preserved and
surfaced rather than guessed at.

**The alternative is circular and would defeat the acceptance criterion.** Deriving publisher
from which resolver succeeded means every resolved paper is Elsevier by construction and every
unresolved paper has no publisher — a coverage table that reports its own method back to
itself and can never show a gap. The DOI prefix is assigned by the registrant and is knowable
for a record we failed to resolve, which is exactly the population the table must describe.
`10.1016` is Elsevier whether or not we could read the paper.

Records with no DOI are reported as `unknown`, counted, and never silently dropped.

### 4. `INACCESSIBLE` is enforced architecturally, not documented

S06-AC4 requires that no code path can write it. A docstring cannot enforce that, so a unit
test walks the source AST and fails if any module outside `screening/` constructs a decision
event with `reason_code="INACCESSIBLE"`. Exhausting the chain returns `None` and writes no
decision event of any kind: **exhaustion is not a verdict**, it is the absence of one, and
only a human who has confirmed no institutional route exists may turn it into one.

## Alternatives rejected

### 1. `fulltext_assets` keyed on `record_id` alone, with a separate attempts table

Keep the table meaning what its name says, and record failures elsewhere.

*Rejected:* it splits one question across two tables for a naming preference. Every consumer —
the coverage report, the resolver chain's resume logic, the Stage 9 bias table — needs
attempts and successes together, and would join them back on every query. BUILD_PLAN also
fixed these columns on this table, including `entitled`, which only earns its place under the
per-attempt reading.

### 2. Publisher from the venue name

Map "Pattern Recognition" → Elsevier with a venue table.

*Rejected:* venue names are not unique across publishers, change over time, and the mapping
would need thousands of entries to cover a real corpus — where the DOI prefix needs a few
dozen and is authoritative. `venues` also carries no publisher column, and adding one would
modify a frozen Stage 3 table, which every previous schema ADR deliberately avoided.

### 3. Attempt every resolver and keep the best asset

Rather than first-hit-wins, fetch from all three and prefer XML over PDF.

*Rejected:* it multiplies API calls and quota for a marginal quality gain, and it makes
"which resolver served this record" ambiguous — the provenance S06-AC2 and the coverage table
both depend on. First hit wins is BUILD_PLAN's chain, and a record whose text came from two
sources is not one asset.

## Consequences

1. **The Elsevier skew becomes a number in the output.** Two tables: by resolver
   (resolved / refused / not found) and by publisher (records / resolved / refused /
   coverage %).

   The publisher table's population is every record the chain **attempted**, not every record
   it resolved. That distinction is the whole value of the table. A publisher we were refused
   across the board — 41 IEEE papers, all 403 — has no resolved records, so a table listing
   only publishers with a resolved record omits it entirely; the reader sees "Elsevier, 100%"
   and takes it for coverage when it is only composition, with no way to tell whether IEEE was
   zero of three or zero of three hundred. "Records" is the denominator that makes the
   percentage mean something, and "Refused" keeps an entitlement gap distinguishable from a
   paper that does not exist.
2. **A 403 never removes a record from the review.** It records an entitlement gap and the
   chain continues; only a human can write `INACCESSIBLE`.
3. **`fulltext/` holds fetched bytes and is never committed.** Publisher PDFs and XML are
   licensed content, already covered by the `projects/*/fulltext/` guard.
4. **No published number moves.** No project has full-text assets yet, so every existing
   golden is unchanged; `reference_table_checksums.json` gains two empty-table digests.

## Constraints

- A 403 records `entitled = false` and the chain continues. No code path may turn a transport
  outcome into an eligibility verdict.
- `INACCESSIBLE` is constructible only inside `screening/`, enforced by an AST test.
- Publisher is derived from the DOI, never from the resolver that succeeded.
- Chain order is ScienceDirect → open access → manual drop, first hit wins, and a resolver is
  not called once an earlier one has produced an asset.
- No OCR. A PDF with no text layer is flagged `low_confidence` and left to a human.
- Fetched full text stays under `projects/<slug>/fulltext/` and is never committed.

## Related decisions

- [ADR 0018](0018-abstract-runs-in-layer-1.md) — the previous schema addition, and the
  per-record coverage pattern this follows
- [ADR 0012](0012-persisting-skipped-layer0-entries.md) — the rule that a `schema.sql` change
  needs its own ADR
- [ADR 0015](0015-stage-order-and-stage-10-scope.md) — why the tag is not `v0.7.0`
- [ADR 0003](0003-human-only-screening.md) — screening decisions are human, which
  `INACCESSIBLE` is a case of

## References

- BUILD_PLAN §Stage 6, lines 1121–1179 (frozen; outside this repository)
- `src/prismabib/fulltext/` — `resolve.py`, `extract.py`, `coverage.py`
- `src/prismabib/publishers.py` — the DOI-prefix table
- `src/prismabib/store/schema.sql` — `fulltext_assets`, `fulltext_sections`
- [Unpaywall API](https://unpaywall.org/products/api), [ScienceDirect Article Retrieval](https://dev.elsevier.com/sd_article_retrieval.html)

---

This ADR records that `fulltext_assets` holds one row per resolver attempt with a three-valued
`entitled`, that `fulltext_sections` carries `position` and a per-section `low_confidence`,
that publisher is derived from the DOI registrant prefix rather than from the resolver, and
that `INACCESSIBLE` is enforced by an AST test. Keying assets on `record_id` alone, deriving
publisher from the resolver or the venue, attempting every resolver, or permitting
`INACCESSIBLE` outside `screening/` requires a new ADR that supersedes this one (§2.6).
