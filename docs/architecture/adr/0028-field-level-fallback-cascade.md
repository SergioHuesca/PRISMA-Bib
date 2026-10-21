# ADR 0028: Field-Level Fallback Cascade, Candidate Intake, and Manual Entry

## Status

Accepted — 2026-10-21. The owner resolved every open item listed at the end of this record. Changes what the capture and loading layers may do when a preferred
Scopus view is refused. Each change is a deviation from the frozen BUILD_PLAN and needs this record
under §2.6. Moves to Accepted when the owner approves it on merge.

## Context

Scopus refuses the views that carry full records for this project's key and network. Measured
on 2026-10-21 from a PC on the institutional VPN (public IP 130.153.0.3; the owner has not confirmed
that this address is one Elsevier has registered). Every refusal below is HTTP 401. BUILD_PLAN §3.3
maps 401 to `AuthError` (bad credentials) and 403 to `EntitlementError`, so under the existing
client these refusals are misclassified. This ADR does not resolve that; it is an open decision.
[ADR 0027](0027-what-v1-0-0-claims.md) says the live test passes from an entitled network, so the
two records must be reconciled before implementation.

| Endpoint and view | Result | Fields it carries |
|---|---|---|
| Search `COMPLETE` | 401 | abstract, full author block, author keywords |
| Search `STANDARD` | 200 | affiliation city and country, first author, identifiers; no abstract, no author keywords |
| Abstract `META` | 200 | title, DOI, venue, citation count, affiliations; no abstract, no full author list |
| Abstract `META_ABS`, `FULL` | 401 | abstract, full author list |

Abstract `BASIC` returned 400 `INVALID_INPUT`, although the Scopus guide lists it (p. 28). The cause
is not known and is an open item.

BUILD_PLAN §5 risk 1 and the Stage 2 criteria make `view=COMPLETE` mandatory for acquisition, and
BUILD_PLAN §5 risk 1 says "Fail loudly, never degrade", and Stage 2 says the client "never falls back
to STANDARD". A degraded corpus is biased and misreports what was measured. Line 392 separately
forbids swallowing `EntitlementError` in the full-text stage. The 1,293-record Baseball-CVPR corpus was captured under
`COMPLETE` on 2026-09-02, so it is unaffected. What is blocked is every new search and every new
enrichment.

OpenAlex covers 1,084 of the corpus's 1,119 DOIs, with abstracts for 852 and institution
affiliations for 1,001 (measured 2026-10-21). Google Scholar has no API. Automated access to it
breaks its terms, so it is used here only by a person, who pastes candidate titles and years.

So the project needs a way to keep enriching records when a preferred view is refused, without
silently producing a biased corpus. Two rules must hold: every field's source is recorded, and
every refusal is recorded too.

## Decision

### 1. Enrichment is a cascade of named levels, evaluated per field

For each record, each field (abstract, authors, affiliations, keywords) is requested from the
levels below in order. The first level that returns a value supplies it. Each level records its
outcome for that field: `value`, `refused` (with the HTTP status), `absent` (the source has no value
for this record), or `not_found` (the source has no record at all, for example an Abstract 404 on a
dummy record; guide p. 30).

| Level | Source | Fields it can supply |
|---|---|---|
| 1 | Scopus Search `COMPLETE` / Abstract `FULL` | all |
| 2 | Scopus Search `STANDARD`, batched by DOI over every record not yet supplied | affiliations, first author, identifiers |
| 3 | Scopus Abstract `META`, one record per request, only for records still missing affiliations | affiliations, first author |
| 4 | OpenAlex works | abstract, authors, affiliations, keywords (topics) |
| 5 | Manual entry by a named reviewer, from the publisher page | any still missing |

Level 1 is the acquisition path already specified. Levels 2 and 3 are not silent fallbacks: each
is named here, and every field it supplies says which view did it. This is an exception to
BUILD_PLAN §5 risk 1, not a reading of it. Because the exception changes a frozen rule, it is
allowed only as an explicit per-run opt-in recorded in `RunManifest`, and the public documents that
state the rule must change in the same pull request. [ADR 0011](0011-abstract-retrieval-for-subject-areas.md)
Decision 3 rejects mixing META or STANDARD with FULL in one corpus. This ADR supersedes that point
for enrichment only, and records the reason.
Level 2 is evaluated first because it batches many DOIs into one request, so it spends the least
quota. Level 3 runs only on what level 2 did not cover.

A field with no value after level 5 is recorded as `unavailable`, with every level's outcome. It is
never exported as a blank, because a blank could mean "not in the source" or "never tried".

### 2. Provenance is recorded per field, not per record

A record carries a provenance map: for each field, the level that supplied it, the source, the
view, the outcome of each earlier level, and the reference to the raw payload. This extends
`source_payload_ref`, which keeps pointing at the Scopus payload that created the record.

Keywords from OpenAlex are topics, not author keywords. They are stored with source
`openalex-topic` and are never compared one-to-one with Scopus author keywords.

### 3. Every attempt is a Layer 0 payload

Each response and each refusal from a source is written as an immutable payload under `raw/` (Layer
0, which BUILD_PLAN §2.2 defines as raw HTTP responses), with the run manifest and checksum pattern
of `capture/writer.py`. Each candidate paste and each manual entry is a human-written event. These go
to Layer 2 as append-only events with a `schema_version`, following [ADR 0002](0002-append-only-decision-log.md),
because BUILD_PLAN §2.2 places human-written data in Layer 2. Layer 1 is rebuilt from both by the
loader, and nothing is kept only in memory or in `raw/_cache/`. The loader is the only thing that
writes Layer 1.

### 4. Attachment is by DOI, then by normalised title and year

An OpenAlex or manual payload attaches to an existing record when its DOI equals the record's DOI.
Without a DOI, it attaches when the normalised title (lowercased, punctuation and whitespace
removed) is equal and the year is the same. A title match that is not exact goes to a review
list, and a person confirms or rejects it. Attachment never creates a record from an OpenAlex
result, so the set of records is still fixed by the Scopus search.

### 5. Google Scholar is candidate discovery, and a person does the searching

The Scholar boolean query is run by a person in a browser. The person pastes the resulting
title and year rows into the app, which writes them as a candidate payload. The app never
requests Scholar. Candidates are not records. Each candidate runs through the same levels:

- with a DOI, it is searched by DOI at every level;
- without a DOI, it is searched by title through the levels that support search, and matched
  under rule 4;
- a candidate matching nothing stays unresolved.

**Candidate registration is decided, and conditional.** The owner has chosen that a reviewer may
register a candidate as a record. BUILD_PLAN Stage 2 makes Scopus's `total_results` the only source
of identified records, so registration needs its own ADR amending Stage 2, and it changes the
"identified" count. Until that amendment is accepted, candidates stay outside the record set and no
registration path is built.

### 6. Manual entry is labelled, and it is the last level

Manual entry is level 5. The screening view gains an "Open publisher page" action, which opens the
record's DOI link in the browser, and an editable form for the fields still missing. Each saved
value is written as a manual payload with the reviewer and time. Methods and exports report manual
values as manual, and state which page they came from.

### 7. What this does not change

- Acquisition runs for a new corpus still require `view=COMPLETE`. A refused `COMPLETE` run is
  still refused, and the run stays unsealed, so no manifest is written. The levels above apply to enrichment of records, not to
  the search that creates them.
- The frozen BUILD_PLAN is not edited. This ADR is the recorded deviation.
- The v1.0.0 release claim (ADR 0027) is unchanged. This ADR adds data paths and does not change
  what the released software was verified to do.

## Alternatives considered

**Use one fallback view for everything.** Rejected. `STANDARD` carries no abstract and no author
keywords, so it cannot stand in for `COMPLETE`. Using it silently would be the degradation
line 392 forbids.

**Record only the lowest level that succeeded, per record.** Rejected. A record drawing on
several sources cannot be explained field by field, and the methods could not say which source
supplied the abstract.

**Create new records from OpenAlex and candidates.** Rejected. It would change the set of records
without a search that defines it, and reintroduce the deduplication problem that DOI attachment
avoids.

**Automate Google Scholar, through a scraping service.** Rejected. Google's terms prohibit
automated access, and the service would be a commercial scraper with its own legal exposure.

**Fuzzy title matching with a threshold.** Rejected for now. A threshold attaches the wrong
paper silently. Exact matching plus a human review list is auditable. It can be revisited with
evidence.

## Consequences

1. The corpus can be enriched after a refusal, and each field says where it came from.
2. The provenance map is new data in `Record`. Existing stores must be rebuilt from Layer 0 to
   carry it. The rebuild is the loader's job, and a test checks that it reproduces the counts.
3. OpenAlex data changes over time. Each OpenAlex payload carries its retrieval date and checksum,
   and a rerun is a new run, not an overwrite.
4. Affiliations from level 2 or 3 have city and country but not necessarily institution names in
   the same form as level 1. The methods must say so, and the field's provenance shows the view.
5. Manual entry is a human step, so it is slow and it is a place for error. It is labelled, and it
   is never the default.
6. The v1.0.0 numbers (ADR 0027) are reproducible from their recorded payloads, but a corpus
   enriched under this ADR has a different provenance profile. A reader of the methods must be
   able to tell the two apart.

## Related decisions

- [ADR 0002](0002-append-only-decision-log.md) — the append-only pattern, applied here to human-written
  events (manual entries, candidate pastes)
- [ADR 0003](0003-human-only-screening.md) — human-only screening, which the manual form must not
  bypass
- [ADR 0011](0011-abstract-retrieval-for-subject-areas.md) — rejects mixing META or STANDARD with FULL;
  enrichment is the case this ADR overrides
- [ADR 0018](0018-abstract-runs-in-layer-1.md) and [ADR 0021](0021-entitlement-refusals-are-attributed-to-a-publisher.md)
  — abstract runs and refusal attribution, which the refusal outcomes must follow
- [ADR 0022](0022-the-analysis-result-contract-and-its-provenance.md) — analysis-level provenance;
  this ADR adds per-field sources, which 0022 does not have
- [ADR 0027](0027-what-v1-0-0-claims.md) — what v1.0.0 claims, which this ADR does not change

## Resolved decisions

1. **401 refusals and ADR 0027.** The entitlement request was sent months ago and is still under
   review, so it cannot be relied on for this work. ADR 0027's premise (the live test passes from an
   entitled network) is unchanged and still true of the 2026-09 measurements. This ADR does not depend on
   the entitlement. A 401 from Scopus is written as a `refused` outcome for the field, and the cascade
   continues to the next level, **only in an opt-in run**. Outside an opt-in run, the spec's
   fail-loud behaviour is unchanged. This is the exception to "never degrade" that §1 names. The
   401/403 split in BUILD_PLAN §3.3 is unchanged: a 401 remains `AuthError` for every client call
   that is not part of an opt-in cascade. Whether `130.153.0.3` is a registered address is recorded
   in the run manifest, not assumed.
2. **DOI-less records.** Resolved by a probe on 2026-10-21. Abstract `META` returned HTTP 200 for three
   DOI-less corpus records looked up by `scopus_id`, and by `eid`. Every Scopus record carries its
   `scopus_id` in its `record_id`, so levels 2 and 3 key on `scopus_id`. Only the OpenAlex level needs a
   title fallback, under the title-and-year rule in §4.
3. **Candidate registration.** A reviewer may register a candidate as a record. This needs a separate ADR
   amending Stage 2, which changes the "identified" count. No registration code is written before that ADR
   is accepted.
4. **Opt-in fallback.** Fallback runs are an explicit per-run opt-in, recorded in `RunManifest`. The
   public documents that state "never degrade" change in the same pull request.
5. **Keyword topics.** OpenAlex topics feed the keyword network as a **new keyword kind**, separate from
   `author` and `index`. This changes the `record_keywords` schema, so it needs a separate ADR amending
   the data model. No keyword code is written before that ADR is accepted.
6. **Unavailable fields in exports.** Each field with no value from any level is exported as the literal
   `unavailable`, and the denominators in Stage 7 and Stage 10 count it explicitly. A blank is never
   written for a field, so an absent value cannot be confused with an empty one.
7. **Reproducibility.** The reproducibility claim applies to the recorded Layer 0 payloads, not to live
   re-queries. A fresh OpenAlex or Scopus call is a new run with its own checksum. ADR 0027's cross-runner
   test keeps running on the frozen reference fixture, which contains no enrichment.
