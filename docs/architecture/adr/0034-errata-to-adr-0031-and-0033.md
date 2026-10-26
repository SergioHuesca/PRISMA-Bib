# ADR 0034: Errata to ADR 0031 and ADR 0033

## Status

Accepted — 2026-10-26, on the merge of the pull request that adds this record, which the owner approves. Until the
merge, this record was `Proposed` on the branch `docs/adr-0034-errata`. Accepting it changes no code and no number
(Decision 3).

ADRs 0001 to 0033 are frozen, so neither [ADR 0031](0031-openalex-responses-are-layer0-runs.md) nor
[ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md) is edited. Each keeps its text as written. This
record is the place a reader is sent to for the correct statement.

## Context

Two sentences in accepted records are wrong. Both are checked below against the code and the tests. Each correction
states the old text, quoted exactly, what is true, and which document is authoritative.

### Correction 1: the OpenAlex `select` (ADR 0031)

**Old text.** The note dated 2026-10-22 in [ADR 0031](0031-openalex-responses-are-layer0-runs.md) (line 14) says:

> The owner approved adding `type`, `primary_location` and `primary_topic` to the `select` list named in "What the
> client asks for today" (Context, line 44). `language` is not added: [...] The manifest's `api_view` field (Decision 3)
> records the select actually sent, so each body captured after this note carries the four new fields.

**What is true.** The note names three fields, and three were added: `type`, `primary_location` and `primary_topic`.
The word "four" is a miscount. Each body captured after the note carries the three new fields. `language` was not
added, as the note itself says. The select is:

```text
id,doi,title,publication_year,abstract_inverted_index,authorships,topics,type,primary_location,primary_topic
```

The Context at line 44 of ADR 0031 describes the select as it was before the note, and it stays accurate for that state.
Only the count in the note is wrong.

**Authority.** The `_SELECT` constant in `src/prismabib/sources/openalex.py` (lines 56-59) is the authority for what
is sent. `tests/contract/test_openalex_select.py` asserts that the select is the old list "plus exactly the three
registration fields", so the test and the code agree on three. [ADR 0032](0032-registered-records-table-and-classification-map.md)
Decision 2 and the CHANGELOG entry under Unreleased name the same three fields.

### Correction 2: what a reversal does to the counts (ADR 0033)

**Old text.** [ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md) §4, "Reversal", says:

> `registered` drops by one. If the paper had reached `included`, `included` drops by one, and the diagram shows
> that.

Consequence 4 says: "A reversal or supersession can lower `included` after a paper was reported in a draft." The
Context says a reversal or supersession changes "`included` and `unsure_title_abstract`".

**Why this is wrong.** Each sentence is true for one case, a paper that had reached `included`. Read as the rule for
a reversal, they name `included` (and, in the Context, `unsure_title_abstract`) as the only counts a reversal can move.
They are incomplete, and that misleads a reader. A reversal can move any count the record was in.

**What is true.** A reversal removes the registered record from `S_raw`. Every set in the count is derived from `S_raw`:
`A`, `L`, `M_abs` and `M_full` all descend from the record set. So the record leaves every set it was in, and each
`FlowCounts` term that counted it falls by one, whichever term that is:

- `registered` always falls by one.
- `excluded_automated` (and its reason in `excluded_automated_by_reason`) falls if the record failed the automated set.
- `excluded_language` falls if the record was in `A` but not in `L`.
- `excluded_title_abstract` falls if the record was excluded at title and abstract.
- `unsure_title_abstract` falls if the record had no title-and-abstract decision, or an `unsure` one.
- `retrieved_fulltext`, `excluded_fulltext` or `unsure_fulltext` falls if the record reached full text.
- `included` falls only if the record had reached `included`.

Equation 1 still closes, because `identified` does not move and `registered` falls by the same one as the record.

**Authority.** [`docs/methodology/prisma-mapping.md`](../../methodology/prisma-mapping.md), section "When a registration
is reversed or superseded", is the authoritative statement. It says: "`registered` falls by one, and so does every count
the record contributed to, at whichever stage its decisions had taken it. `included` falls only if the paper had reached
`included`; a paper excluded at title and abstract lowers `excluded_title_abstract` instead." The code implements that
statement: `compute_flow_counts` in `src/prismabib/prisma/flow.py` (lines 552-599) and the set construction in
`src/prismabib/prisma/engine.py` (lines 946-957). The reversal test in
`tests/integration/prisma/test_registered_flow.py` (lines 116-135) covers the `included` case only. No test yet
reverses a registration excluded or unsure at title and abstract. That is a gap in coverage, and this record does not
close it.

## Decision

1. **Correction 1 (ADR 0031).** The note's "four new fields" is corrected to three: `type`, `primary_location` and
   `primary_topic`. The select sent is the one `_SELECT` defines. ADR 0031's body is unchanged.
2. **Correction 2 (ADR 0033).** The reversal rule is the one in `docs/methodology/prisma-mapping.md`, as stated in
   Context, correction 2. A reversal lowers whichever `FlowCounts` terms the reversed record's decisions placed it in.
   The `included` sentences in ADR 0033 §4, Consequence 4 and the Context are not false for their case, and they stay as
   written. ADR 0033's body is unchanged.
3. **No behaviour changes.** Nothing in `src/`, `tests/`, the schema, `numbers.json`, the goldens or the flow diagram
   changes. `FlowCounts` gets no new field. The code already has the behaviour this record states, and the select
   already carries the three fields.

## Consequences

1. Readers of ADR 0031 and ADR 0033 are pointed to this record by its number. The dated pointers are in this record,
   not in the frozen records.
2. `docs/methodology/prisma-mapping.md` is not changed, because it is already the correct statement.
3. No ADR index needs a change beyond the navigation entry in `mkdocs.yml`, which lists every ADR.
4. **Not corrected here.** Other statements in ADRs 0031 to 0033 are dated, and they are not behaviour claims. Two of
   them are stale: ADR 0032 cites `sources/openalex.py` line 52 for `_SELECT` (the constant is at line 56 now), and
   ADR 0032 says "The current OpenAlex select does not carry those fields", which was true on 2026-10-22 and is not
   true now. Line-number citations in ADR 0033 were not checked. None of them is corrected by this record, because
   none states a rule a reader acts on.

## Alternatives considered

**Edit ADR 0031 and ADR 0033 in place.** Rejected. ADRs 0001 to 0033 are frozen. An erratum record keeps the original
text and the correction side by side, so a reader sees what was accepted and what is true.

**Add a dated pointer note under each affected sentence, as earlier records did.** Rejected for these two sentences.
ADR 0032 and ADR 0033 used dated notes to change a decision, and this record corrects two statements without changing
a decision. One record that quotes both is easier to check than two notes that each need the other.

## Related decisions

- [ADR 0031](0031-openalex-responses-are-layer0-runs.md): the note at line 14 is corrected by Correction 1.
- [ADR 0032](0032-registered-records-table-and-classification-map.md): Decision 2 names the three fields.
- [ADR 0033](0033-flowcounts-registered-count-and-orphaned-decisions.md): §4 and Consequence 4 are corrected by
  Correction 2.
- [ADR 0007](0007-flow-counts-unsure-fields.md): the `unsure` partition that the reversal rule moves through.
- [`docs/methodology/prisma-mapping.md`](../../methodology/prisma-mapping.md): the authoritative statement of the
  reversal rule.
