# ADR 0017: `subject_areas` Matches by ASJC Grouping, Not by Raw Code

## Status

Accepted — 2026-09-01. Supersedes the sentence in
[ADR 0011](0011-abstract-retrieval-for-subject-areas.md)'s Context that describes `@code` as
"the ASJC classification number the criteria list is matched against". No other part of ADR
0011 changes: the Abstract Retrieval design, the Layer 0 placement, the entitlement probe and
the resumability rules all stand. No schema change, no Layer 0 change, and no re-capture.

## Context

ADR 0011 shipped subject-area enrichment. Scopus's Abstract Retrieval API returns each area
as three fields:

```json
{"@code": "2202", "@abbrev": "ENGI", "$": "Aerospace Engineering"}
```

`store/load.py::_subject_areas_from_entry` prefers `@code`, so Layer 1's
`subject_areas.area_code` holds `"2202"`. `criteria.yaml`'s own template, meanwhile, teaches
the four-letter form — "Scopus subject-area codes, e.g. MEDI, COMP, ENGI" — and that is the
level a protocol is written at. `_passes_subject_areas` compared the two case-insensitively
and directly.

`{"2202"} & {"engi"}` is empty. **The filter had never been able to match anything.**

### This does not weaken the filter, it inverts it

`_passes_subject_areas` treats "this record carries no subject-area data" as *passes* — the
right latitude for one sparse record among many. Combined with a comparison that can never
succeed, the result is precise and backwards: **every enriched record is excluded, and only
records that failed to enrich survive.**

Measured on a four-record corpus with criteria `[COMP, ENGI, MATH, MULT]`, before this ADR:

| record | stored areas | correct | actual |
| --- | --- | --- | --- |
| 1 | `2202`, `1702` (ENGI, COMP) | keep | **excluded** |
| 2 | `2611` (MATH) | keep | **excluded** |
| 3 | `2746` (MEDI) | exclude | excluded |
| 4 | *(never enriched)* | keep | keep |

Three excluded where one should be. A reviewer would read "excluded by subject area: 3" and
have no way to see that it names the wrong three. This is BUILD_PLAN §1.4 exactly. ADR 0011
recorded that `@code` and `@abbrev` both exist, but resolved which one the criteria match the
other way (see Status), and no mapping table was ever written.

It stayed invisible because the corpus had zero subject-area rows, so
`_refuse_unenforceable_subject_filter` raised before any comparison happened. The defect was
unreachable until the first enrichment run — which is to say, until the first time anyone
relied on it.

### Why the two forms both have to exist

`@code` is strictly more informative: `1702` is *Artificial Intelligence*, a category within
COMP. Storing the abbreviation instead would discard that permanently, and Layer 1 is meant
to be reconstructible without re-spending quota. Meanwhile a researcher writing a protocol
means "computer science", not twenty numeric categories. Neither form can be dropped, so
something has to bridge them.

## Decision

**Both sides are normalised to ASJC's four-letter grouping before comparison**, by a closed
checked-in table in `src/prismabib/asjc.py`, shaped like the existing `countries.py`: a
`(value, matched)` return, an unmapped value preserved rather than guessed at or dropped.

The first two digits of a four-digit ASJC code *are* its grouping (`22xx` → `ENGI`), which is
what makes the table small and total rather than a 300-row category list. An abbreviation
passes through unchanged, so a store built from a capture that already held abbreviations
still matches.

**`criteria.yaml` accepts only the groupings.** `COMPUTER` is refused, because a code ASJC
does not define matches no record and would narrow a review to nothing while reading as a
deliberate restriction. `1702` is refused too, for the opposite reason: it names one category
but can only be matched at its grouping, so accepting it would silently widen to all of COMP.
Both errors are invisible in the resulting diagram, which is why they are refused at load
rather than reported later.

## Alternatives rejected

### 1. Store `@abbrev` instead of `@code`

Have the loader write the four-letter form, so the criteria match directly and no table is
needed.

*Rejected:* it discards the finer classification irrecoverably. `1702` and `1712` both become
`COMP`, and no later analysis — a subject-area breakdown of the included corpus, say — can
recover which. Layer 1 is meant to hold what Scopus said; throwing away precision to avoid a
27-row table is the wrong trade.

### 2. Add `area_abbrev` as a second column

Store the grouping alongside the code: `subject_areas(record_id, area_code, area_abbrev)`.

*Rejected here, not forever:* it is a change to the frozen Layer 1 schema, requiring its own
ADR under ADR 0012's rule, and it stores a value wholly derivable from one already present —
a denormalisation that can go stale against the table that derives it. The mapping is needed
in the engine regardless, so the column would buy nothing this ADR does not already give. If
a future stage needs to *group by* abbreviation in SQL, that is the moment to reconsider.

### 3. Require criteria to list numeric codes

Take ADR 0011's Context at its word and have researchers write `subject_areas: [1702, 2202]`.

*Rejected:* it contradicts the template every project is created from, and it is unusable —
"computer science" is roughly twenty ASJC numbers, and a protocol that lists nineteen of them
has a silent hole no reviewer would catch. It also inverts the burden: the tool knows the
mapping, the researcher should not have to.

## Consequences

### 1. The filter can be used for the first time

`subject_areas` has never excluded a record in this project's history. After this ADR it
does, correctly, and the exclusion is reportable per
[ADR 0016](0016-automated-exclusion-reasons.md) as `by subject area: N`.

### 2. Enrichment must precede setting `subject_areas`

Unchanged from ADR 0011, and worth restating: `_refuse_unenforceable_subject_filter` raises
when criteria declare subject areas and no record carries any. The order is enrich, rebuild,
then amend `criteria.yaml` — not the reverse.

### 3. The table is validated against Scopus, not against itself

`asjc.py`'s unit tests use `@code`/`@abbrev` pairs transcribed from a recorded Scopus response
that carries both fields side by side, so they check the table against Elsevier's own answer.
A one-call probe against the live API on 2026-09-01 returned `3306`/`SOCI` and `2739`/`MEDI`;
the table agreed with both.

### 4. An unmapped code is a miss, not a silent drop

`area_abbrev` returns `matched=False` and preserves the input. A grouping ASJC adds later
cannot quietly remove a record from a review.

## Constraints

- `_PREFIX_TO_ABBREV` is extended by adding entries, never by inferring a grouping.
- Criteria accept groupings only. Numeric ASJC codes and unknown strings are both refused, and
  for opposite reasons that must both stay in the error message.
- What validates must be what matches: the criteria validator and `area_abbrev` apply the same
  strip-and-upper normalisation.
- A test of this mapping must use `@code`/`@abbrev` pairs from a real Scopus response. A table
  checked against itself proves nothing.

## Related decisions

- [ADR 0011](0011-abstract-retrieval-for-subject-areas.md) — how the codes are obtained
- [ADR 0016](0016-automated-exclusion-reasons.md) — how the exclusion is reported
- [ADR 0012](0012-persisting-skipped-layer0-entries.md) — the rule that a `schema.sql` change
  needs its own ADR, which is why alternative 2 is deferred rather than taken

## References

- `src/prismabib/asjc.py` — `_PREFIX_TO_ABBREV`, `KNOWN_ABBREVS`, `area_abbrev`
- `src/prismabib/prisma/engine.py` — `_passes_subject_areas`
- `src/prismabib/project.py` — `Criteria._require_known_subject_areas`, the `criteria.yaml`
  template
- `src/prismabib/store/load.py` — `_subject_areas_from_entry`, which still prefers `@code`
- `tests/fixtures/cassettes/abstract-full-multi-subject-area.json` — the recorded response the
  unit tests transcribe their pairs from
- [Scopus ASJC code list](https://service.elsevier.com/app/answers/detail/a_id/15181/supporthub/scopus/)

---

This ADR records that `subject_areas` is matched at ASJC's four-letter grouping, that both
sides are normalised through `asjc.py` before comparison, and that `criteria.yaml` accepts
groupings only. Comparing raw values, storing the abbreviation in place of the code, adding an
`area_abbrev` column, or accepting numeric codes in criteria requires a new ADR that
supersedes this one (§2.6).
