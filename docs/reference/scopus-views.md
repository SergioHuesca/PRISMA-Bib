# Scopus views: what the project's key can reach

Measured on 2026-11-25 against `api.elsevier.com/content` with the key in `.env`, with and
without `SCOPUS_INSTTOKEN`. Each cell is one live call (HTTP status). The insttoken changed
nothing in any cell.

Entitlements are granted per API key and often per institution. These results are for one key,
so another key or another institution may get different results.

A later measurement, on 2026-11-27, covers Search `STANDARD` paging and sample coverage. It is in [Search STANDARD paging and sample coverage (2026-11-27)](#search-standard-paging-and-sample-coverage-2026-11-27). Reading a whole result set without a cursor is in [Reading a whole STANDARD result set without a cursor (2026-11-27)](#reading-a-whole-standard-result-set-without-a-cursor-2026-11-27). RDF was not re-measured then.

| API | View | Result |
|---|---|---|
| Search (`/search/scopus`) | `STANDARD` | 200 |
| Search | `COMPLETE` | **401** `AUTHORIZATION_ERROR` |
| Abstract Retrieval (`/abstract/eid/{eid}`) | `META` | 200 |
| Abstract Retrieval | `META_ABS`, `FULL`, `REF` | **401** |
| Abstract Retrieval | `ENTITLED` | 401 |

## Widest Search view available: `STANDARD`

Search `STANDARD` is the widest view this key can read, but only without the `cursor` parameter: on 2026-11-27 every cursor request under `STANDARD` was refused (see [Search STANDARD paging and sample coverage (2026-11-27)](#search-standard-paging-and-sample-coverage-2026-11-27)). Code reading: `ScopusClient.search` always sends a cursor, so for this key it would be refused on page 1; the client was not run. Abstract Retrieval `META` as JSON adds
only a few fields over it: an author block for one sampled record, `dc:publisher`, `srctype` and
`link` (see below). It costs one call per record against a separate quota, so it is not a
practical bulk path.

Fields returned (25-record sample; the share is how many records carried it):

- **Always present in the 25-record sample:** `eid`, `dc:identifier`, `dc:title`, `dc:creator` (first author only),
  `affiliation` (name, city, country), `prism:publicationName`, `prism:coverDate`,
  `prism:aggregationType`, `subtype`, `subtypeDescription`, `source-id`, `citedby-count`,
  `openaccess`, `openaccessFlag`, `prism:url`. On 2026-11-27, `dc:creator` was on 302 of 303 recorded entries and an affiliation object on 280 of 303, so neither is always present (see [Search STANDARD paging and sample coverage (2026-11-27)](#search-standard-paging-and-sample-coverage-2026-11-27)).
- **Present when the record has them:** `prism:doi` (23/25), `prism:volume` (22), `prism:eIssn`
  (18), `prism:issn` (17), `prism:issueIdentifier` (16), `prism:pageRange` (18),
  `freetoread` / `freetoreadLabel` (18), `article-number` (6), `pubmed-id` (8), `prism:isbn`
  (4), `pii` (4).

`affilname`, `affiliation-city` and `affiliation-country` are accepted in `field=` but only
repeat data already inside `affiliation`. `orcid` is also accepted; this page does not record
whether it returned values.

## What `STANDARD` cannot give

Requesting these with `field=` returns HTTP 400, so a field list does not get around the view:
`author` / `authname` / `authid` (the full author list), `afid`, `dc:description` (abstract),
`authkeywords`, `idxterms`, `fund-acr` / `fund-no` / `fund-sponsor`, `dc:publisher`.

Abstract Retrieval `META` **as JSON** adds an `author` block under `dc:creator` (one author in
the sample), `dc:publisher`, `srctype` and `link`, and returns no abstract,
author keywords, references or subject areas (indexed terms are the one exception: see the
`field=` row in the experiment below). The same call **as RDF/XML** returns much more: see the
experiment below, which supersedes the earlier conclusion that the author list, keywords,
subject areas and references are out of reach. The abstract is still out of reach.

[ADR 0011](../architecture/adr/0011-abstract-retrieval-for-subject-areas.md) says the opposite
about `view=META`: it states that `META` does carry `subject-areas`. This page's JSON `META`
result shows none. The two differ, and the difference is unresolved. This page does not decide
which one is right.

## Consequences for the project

- `prismabib search` always requests `view=COMPLETE` ([ADR 0028, Decision 7](../architecture/adr/0028-field-level-fallback-cascade.md#7-what-this-does-not-change)).
  This key gets HTTP 401 for `COMPLETE`. The client maps 401 to `AuthError` and 403 to
  `EntitlementError` (`src/prismabib/sources/scopus.py`). The measurements above are raw HTTP
  calls, not runs of `prismabib search`, so the 401 branch has not been run end to end with
  this key. A `COMPLETE` entitlement, usually tied to an institutional subscription, is needed.
- **The 401 path.** Outside an opt-in run, a 401 from Scopus stops the command with
  `AuthError`. The message begins `Scopus rejected the API key (HTTP 401) for`, followed by the
  endpoint and a checklist about `SCOPUS_API_KEY` and `SCOPUS_INSTTOKEN`. The checklist
  does not mention entitlements, and its first step is to check that `SCOPUS_API_KEY` is set in
  `.env`. That reads misleadingly for a key that is valid but not entitled. Only a 403 becomes
  the entitlement error. ADR 0028 measured the same refusals, all HTTP 401, and calls
  the `AuthError` classification a misclassification that it leaves as an open decision. Its Resolved decisions item 1 keeps the mapping
  unchanged outside an opt-in cascade run. In an opt-in cascade run, a 401 is recorded as
  `refused` for the field and the cascade goes on to the next level (the client does the same for
  a 403)
  ([ADR 0028, Resolved decisions, item 1](../architecture/adr/0028-field-level-fallback-cascade.md#resolved-decisions)).
- `prismabib enrich` (subject areas, [ADR 0011](../architecture/adr/0011-abstract-retrieval-for-subject-areas.md))
  requests `view=FULL`, also closed to this key.
- The cascade's Scopus steps (levels 2 and 3 of [ADR 0028](../architecture/adr/0028-field-level-fallback-cascade.md),
  Search `STANDARD` and Abstract `META` as JSON) supply affiliations only. The glossary's Cascade
  entry and [ADR 0041](../architecture/adr/0041-field-level-provenance-and-cascade-completion.md)
  (Decision 1a) say so. They do not supply the abstract, the full author list, keywords,
  subject areas or references. The RDF/XML route in the experiment below returns more. It is a
  follow-up, not current behavior: the code does not request RDF, and switching level 3 to RDF
  is a code change that needs the Main Flow and its own spec. Level 2 (`search_standard_by_dois`) sends one request per batch of up to 25 DOIs, with no cursor (code reading), so the cursor refusal of 2026-11-27 does not apply to it as coded. It was not re-run on 2026-11-27; the code records one live single-DOI request (2026-10-21, HTTP 200) and says the multi-DOI form is not verified.

## Quota observed

Response headers showed `X-RateLimit-Limit` 20000 for Search and 10000 for Abstract Retrieval.
A 401 for an unentitled view appeared to count against the limit; this was not separately verified.

## Experiment: a second call by id to recover the missing fields

Sample size: one seed record for the exhaustive grid, and one query (systematic reviews in
bibliometrics, 2023) that gave 10 records for the comparison with OpenAlex. The limits of
this sample are listed at the end of the section.

**Question.** Search `STANDARD` yields the identifiers (`eid`, Scopus id, DOI, PubMed id, PII).
Can some second call, using only the Scopus key and no insttoken, return the full author list,
abstract, keywords, subject areas and references for that id?

**Design.** One seed record (`2-s2.0-85181038387`, with DOI and PubMed id), then every
combination of: endpoint x id type x view x media type x `field=` value. About 145 live calls
on 2026-11-25. A response counted as a hit only if the raw body contained the target field.
Raw responses were kept; keys are not logged.

| Axis | Values tried | Outcome |
|---|---|---|
| Abstract Retrieval id type | `eid`, `scopus_id`, `doi`, `pubmed_id` | Identical responses; the id type does not matter. `pii` was not tried (the seed had none). |
| Abstract Retrieval view | none, `META`, `META_ABS`, `FULL`, `REF`, `ENTITLED`, plus `BASIC`/`ENHANCED`/`STANDARD`/`COMPLETE`/`LIGHT` | No view = `META`. `META_ABS`, `FULL`, `REF`, `ENTITLED`: 401. The rest: 400 (not valid for this API). |
| Media type | `application/json`, `text/xml`, `application/xml`, `application/atom+xml`, `application/rdf+xml`, `text/plain`, `text/html`, `application/pdf`, `application/ld+json`, `text/turtle` | **Only `application/rdf+xml` returns more.** `atom+xml` is 406 on Abstract Retrieval. The others are 406 or the same data. |
| `field=` on `META` (JSON) | `author`, `affiliation`, `idxterms`, `abstract`, `dc:description`, `authkeywords`, `subject-areas`, `references`, `bibliography`, `item`, `classifications` | `idxterms` works (indexed terms). `author`/`affiliation` give nothing new. The rest: 400 "restricted". |
| Search facets | 17 facets (`subjarea`, `authname`, `exactkeyword`, ...) on `EID(x)` | All 400. The responses do not name the cause; the key may lack facet entitlement. |
| Search views | `ENHANCED`, `LIGHT`, `BASIC`, `FULL`, `META`, `META_ABS`, `REF` | All 400. Search accepts only `STANDARD` and `COMPLETE` (the latter 401). |
| Other endpoints | Author, Author search, Affiliation, Serial Title, `/search/index:SCOPUS` | 401. |
| | Citation Overview | 403 `AUTHENTICATION_ERROR`. The response does not state the reason; it may need a separate service entitlement. |
| | `/search/scidir`, `/search/sciencedirect`, `/article/doi` or `/article/eid` | 410 / 400 / 404 for this record. The seed record is not in an Elsevier journal, so these codes say nothing about ScienceDirect coverage. |
| | `REFEID()` query | 400: restricted field. |

Note (2026-11-27): a `facets=pubyear(count=200)` request was refused as not entitled; see [Reading a whole STANDARD result set without a cursor (2026-11-27)](#reading-a-whole-standard-result-set-without-a-cursor-2026-11-27). The 17 facets on `EID(x)` were not re-run.

### Result: `Accept: application/rdf+xml` on Abstract Retrieval `META`

```text
GET /content/abstract/eid/{eid}?view=META     Accept: application/rdf+xml     (key only)
```

The RDF serialization of `META` is not subject to the field restrictions the JSON one is.
For the seed record it returned (18.5 kB):

| Field | In the RDF response |
|---|---|
| Full author list, in order | `api:Author` with `authid`, surname, given name and `afid` |
| Affiliations | `afid` plus the affiliation name |
| Author keywords | `prism:keyword` (`bibliometric analysis`, `circadian rhythm`, `sport`, `visual maps`, `VOSviewer`). These are the author keywords; the JSON `idxterms` are the separate indexed terms. |
| Subject areas | `dc:subject` to an ASJC code, with `api:code`, `api:abbrev` (`MEDI`) and name (`Medicine (all)`) |
| References | one `api:reference` per cited item, with Scopus id, title, source, year, volume and pages. No DOI and no author names. |
| Also | `pui`, `pubmedId`, ISSNs, publisher, `copyrightYear`, cited-by count |

**Validation on 10 records** (same query), compared with OpenAlex:

| Check | Result |
|---|---|
| Author count, Scopus RDF vs OpenAlex | equal on 10 of 10 (2 to 10 authors) |
| Reference count | equal on 3 of 10; within 10% on 4; Scopus had more references in the other 3 (Scopus vs OpenAlex: 26 vs 17, 35 vs 27, 96 vs 0). The direction of the 4 records within 10% was not recorded. Completeness was not verified. |
| Keywords | 0 to 6 per record; one record had none |
| Subject areas | 1 to 4 ASJC codes per record |

RDF was not re-measured on 2026-11-27. The results above stand only on their original sample: one seed record for the grid and 10 records for the comparison.

### What is still unreachable

**The abstract.** It appears only in `META_ABS`/`FULL` (401). No media type, `field=` value or
id type returned it, and no other endpoint tried carries it. With this key it must come from
another source (OpenAlex returned one for 10 of 10).

### Recommended path with this key

This is a recommendation for later work. It is not what the project's cascade does today. It was written before the 2026-11-27 cursor finding and does not consider cursor paging, so step 1 below does not hold as written.

1. Search `STANDARD` for the ids and the basic metadata. Every cursor request under `STANDARD`, including the first page, was refused on this key (2026-11-27), so this step is not settled.
   Note (2026-11-27): the ids and basic metadata of step 1 were read without a cursor for result sets of up to 6,602 records; see [Reading a whole STANDARD result set without a cursor (2026-11-27)](#reading-a-whole-standard-result-set-without-a-cursor-2026-11-27). The procedure there was pieced together from separate tests and is not implemented in prismabib. Steps 2 and 3 of this path were not re-measured. The no-go stands.
2. Abstract Retrieval `META` with `Accept: application/rdf+xml`, one call per record, for the
   full author list, affiliations, author keywords, ASJC subject areas and references.
3. The abstract from OpenAlex.

The project's current level-3 fallback (ADR 0028) asks for `META` as JSON and does not request
RDF, so it gets none of the RDF-only fields. Switching it to RDF is a code change that needs the
Main Flow and its own spec.

### Limits of the experiment

One seed record for the exhaustive grid, and one query (systematic reviews in bibliometrics, 2023)
for the 10-record check, so journals from other publishers, older records and non-English
records are not covered. Only the 401 and 400 behavior of each view was observed, never its
documented purpose. Author, Affiliation and Serial Title APIs returned 401, so their content
was not seen.

## Search STANDARD paging and sample coverage (2026-11-27)

Measured on 2026-11-27 with the same key, `X-ELS-APIKey` only (no insttoken) and a generic
User-Agent, against `api.elsevier.com/content/search/scopus`. It was the go or no-go check for
the spec tracked as #466 (ticket #467). Only aggregate counts are given here. No response body,
record content, key or personal detail is recorded on this page.

### Outcome

Cursor paging is refused under `STANDARD` for this key, so the check failed.
Offset paging with `start` returned complete result sets for two of the four queries and first
pages for the other two. Offset paging is outside the project's spec, so using it needs a new
decision. The decision recorded for #467 is no-go. No Abstract Retrieval request was sent, so
this measurement has no RDF results.

### Cursor requests

Every request below carried `cursor=*`. None returned a page.

| Query | View | Count | HTTP | Result |
|---|---|---|---|---|
| Q1 | `STANDARD` | 25 | 403 | refused |
| Q3 | `STANDARD` | 25 | 403 | refused |
| Q1 | none | 25 | 403 | refused |
| Q1 | `STANDARD` | 1 | 403 | refused |
| Q3 | `STANDARD` (verification) | 25 | 403 | refused |

Each refusal had status `ENTITLEMENTS_ERROR` with the text "Use of the cursor parameter is
restricted". The three consecutive diagnostic refusals reported an unchanged remaining-quota
header. No page came back by cursor, so no next cursor was followed: page 2 and later are not
reachable by cursor with this key.

Code reading, not a client run: `ScopusClient.search` in `src/prismabib/sources/scopus.py`
sends a `cursor` parameter on every request. The first request uses `start_cursor`, which defaults
to `*`; each later request uses the next cursor the previous page reported. For this key a
`STANDARD` call through that method would therefore be refused on its first request, in the same
way as the raw `cursor=*` requests above.

### Paging by `start` offset

Not in the spec, and not requested by the ticket. It was run to show that the same queries answer
without a cursor. Parameters: `view=STANDARD`, `count=25`, `start` = 0, 25, 50, and so on.

| Query | Pages read | `totalResults` | Entries | Distinct entries | Entries without EID | Result set |
|---|---|---|---|---|---|---|
| Q1 | 2 (all) | 28 | 28 | 28 | 0 | complete; count matches |
| Q3 | 7 (all) | 175 | 175 | 175 | 0 | complete; count matches; total the same on every page |
| Q2 | 2 | 2625 | 50 | 50 | 0 | first 50 only |
| Q4 | 2 | 6602 | 50 | 50 | 0 | first 50 only |

All 13 offset requests of the main run returned 200. A later verification request (`start=25`,
query Q3) also returned 200, with 25 entries of the 175 total. Responses carried `first`, `last`
and `self` links, plus `next` and `prev` where applicable, and no `cursor` object. Offsets above
150 were not requested, so the upper limit of `start` was not measured. The client docstring says
`start` and `count` cap at 5,000 results; Q4 reports more than that, but the cap was not tested.

Note (2026-11-27): the upper limit of `start` and the 5,000 cap were tested on this date. See [Reading a whole STANDARD result set without a cursor (2026-11-27)](#reading-a-whole-standard-result-set-without-a-cursor-2026-11-27).

### Queries

Default sort order. No query is about bibliometrics.

| Label | Query string |
|---|---|
| Q1 | `TITLE("circadian rhythm") AND TITLE(exercise) AND PUBYEAR < 2000` |
| Q2 | `TITLE-ABS-KEY("physical education") AND LANGUAGE(spanish)` |
| Q3 | `TITLE-ABS-KEY("self-healing concrete") AND PUBYEAR = 2023` |
| Q4 | `TITLE-ABS-KEY(philosophy) AND (LANGUAGE(french) OR LANGUAGE(german)) AND PUBYEAR < 2010` |

Disciplines intended by query design: physiology and medicine, education, materials engineering,
humanities. These were not verified per record.

### Sample composition (search entries only)

The sample is the first 50 distinct entries of each query, except Q1, which matches only 28. The
total is 178, short of the ticket's "about 200". No Abstract Retrieval request was made for any of
them.

| Category | Q1 (n=28) | Q2 (n=50) | Q3 (n=50) | Q4 (n=50) | All (n=178) |
|---|---|---|---|---|---|
| Pre-2000 (cover date) | 28 | 0 | 0 | 0 | 28 |
| Non-English by query restriction | not restricted | 50 | not restricted | 50 | 100 |
| Without DOI | 13 | 1 | 0 | 31 | 45 |
| DOI prefix `10.1016/` | 1 | 2 | 29 | 0 | 32 |
| DOI with another prefix | 14 | 47 | 21 | 19 | 101 |

Pre-2000 status is by cover date and comes from Q1 only. Non-English status comes from the query
restriction, because entries carry no language field. The `10.1016/` prefix is a proxy for
Elsevier. Other prefixes exist, and entries without a DOI are not classified. Publisher is not in
`STANDARD` entries.

### STANDARD entry coverage

The sample column is the 178 entries above. The all-recorded column is the 303 distinct entries
read by offset paging (Q1 28, Q2 50, Q3 175, Q4 50).

| Field | Sample (n=178) | All recorded (n=303) |
|---|---|---|
| Any affiliation | 156 | 280 |
| Affiliation name | 156 | 280 |
| Affiliation country | 156 | 280 |
| Affiliation id (`afid`) | 0 | 0 |
| DOI (`prism:doi`) | 133 | 255 |
| `dc:creator` | 178 | 302 |
| `dc:description` (abstract) | 0 | 0 |
| `authkeywords` | 0 | 0 |
| `author` list | 0 | 0 |
| `pii` | 32 | 85 |
| `pubmed-id` | 28 | 29 |

By query, Q1 / Q2 / Q3 / Q4 (n = 28 / 50 / 50 / 50): any affiliation 26 / 49 / 50 / 31; `afid`
0 / 0 / 0 / 0; DOI 15 / 49 / 50 / 19.

Affiliation objects: 173 in the sample and 317 across all recorded entries. All 317 carried
`affilname`, `affiliation-country` and `@_fa`; 315 carried `affiliation-city`. None carried `afid`
or an affiliation URL, which agrees with the list above. Affiliation objects per entry (303
entries): none 23, one 244, two 35, three 1. Whether `STANDARD` truncates the affiliation list was
not established, because there is no RDF comparison.

### Not measured

- Any RDF request: RDF parse rate, DTD rejections, and RDF coverage of the author list, author
  ids, affiliations, author keywords, ASJC subject areas and references.
- RDF element and attribute names. The names on this page come from 2026-11-25 and were not
  re-checked.
- Any OpenAlex comparison: author counts, keyword and subject presence, abstract availability and
  reference counts.
- Abstract and reference coverage.
- The spec's assumptions about authors given only as `dc:creator`, missing author ids and RDF
  affiliation country.
- The client's own `search()` path: only a raw request with the same parameters was sent.
- Cursor entitlement for any other key or institution.
- Offsets above 150. Note (2026-11-27): the upper limit of `start` was tested on this date; see [Reading a whole STANDARD result set without a cursor (2026-11-27)](#reading-a-whole-standard-result-set-without-a-cursor-2026-11-27).

### Limits

- One key, one network, one day. The cursor refusal was seen on all five cursor requests, but
  another key or institution may have a different entitlement.
- Pre-2000 coverage comes from Q1 only (28 entries).
- Non-English status rests on the query restriction. A record may list more than one language.
- Default sort order. First pages only for Q2 and Q4.
- The 17 responses of the main run were kept outside the repository and are not published here. The two verification responses were not stored.

## Reading a whole STANDARD result set without a cursor (2026-11-27)

Dated 2026-11-27. The recorded requests ran on 2026-11-26, 22:38 to 22:58 UTC.

**Setup.** Same key, `X-ELS-APIKey` only (no insttoken), generic User-Agent,
`api.elsevier.com/content/search/scopus`, `view=STANDARD`, `count=25` for page reads. No request
carried `cursor`, and no attempt was made to get around the refusal.

**Outcome.** On the sets read, offset paging with query splitting and a check of distinct
EIDs against the total reached the whole set. One pass is not enough.

### Limits found

- 25 is the page size ceiling: 26, 100, 200 and 201 were refused with HTTP 400.
- `start` plus `count` must not exceed 5,000: `start`/`count` 4975/25 and 4999/1 were accepted, and 5000/1 and 4990/25 were refused.
- All probes used Q4.
- A facets request was refused as not entitled.
- `sort` was accepted.
- A `date` filter was accepted, but was not compared with a publication-year clause.

### Records lost in single passes

A pass is a complete read of two pages or more. Records lost is the total minus the distinct EIDs
of the pass.

| Sort value | Passes | Records read | Records lost |
|---|---|---|---|
| default (no `sort` parameter) | 13 | 13,559 | 7 |
| `+orig-load-date` | 3 | 7,205 | 88 |
| `+coverDate` | 1 | 612 | 38 (the Q2 slice) |
| `+publicationName,+volume,+pagefirst` | 1 | 612 | 4 (the Q2 slice) |
| `+orig-load-date,+publicationName,+pagefirst` | 1 | 612 | 14 (the Q2 slice) |

Passes under the default order, each given as records read, then records lost:

- Q2 unsplit: 2,625, lost 1.
- Seven Q2 year-range parts (31, 501, 94, 341, 601, 612, 437): only the 612 part lost records, 2.
- Two Q4 parts: 2,563, lost 1; and 4,030, lost 3.
- A second pass over the 612 slice: lost 0.
- Q4 publication year 2008: 343, lost 0.
- The one-go query (the French-language query listed under Query strings): 769, lost 0.

Passes under `+orig-load-date`: the 612 slice (lost 0) and the two Q4 parts (lost 37 and 51).

The one-page parts (8 records of Q2, 9 records of Q4) are not passes. That is why the listed parts sum to
2,617 and 6,593, not to the full totals of 2,625 and 6,602.

The default order was cover date, newest first, in the passes checked. Over all passes counted, it lost
the smallest share. The sorts were not all run on the same sets, though: on the 612-record slice the
default lost 2 and then 0, and `+orig-load-date` lost 0.

Under the default order, all 7 repeated records were the last entry of one page and the first entry of
the next. Each had a cover date shared by 60 to 177 records of the same read.

Losses differ between passes. A second default pass over the 612-record slice lost nothing, where the
first lost 2.

In a repeated-page test, two non-empty Q2 pages were asked again. At offset 0 the response was
identical. At offset 1300 it held the same 25 records with two adjacent ones swapped, and all records
on that page share one cover date. The likely cause is that tied records have no stable order between
requests. This is an inference, not a measurement.

No sort tried can be trusted on its own.

### Splitting tests

- **Q2** (2,625 records), read directly and again as 8 non-empty year ranges: 2,624 and 2,623
  distinct. Their union is 2,625. The split was forced with a limit of 800 per part. All splits added up.
- **Q4** (6,602 records) in 3 year ranges: the first pass gave 6,598 distinct. With an extra
  `+orig-load-date` pass the union was 6,601. Adding a re-read of publication year 2008, the union held
  6,602 distinct EIDs, equal to the total. 2008 was chosen because, walking publication years downward
  from 2009, it was the first whose count exceeded the records held for that cover-date year. All
  splits added up.
- **Load-date split on Q4**, run as a before/after pair (`ORIG-LOAD-DATE BEF 20150101` and
  `ORIG-LOAD-DATE AFT 20141231`), not as `AND NOT`: 6,315 plus 287 equals 6,602.
- **Machine learning query** (48,162 records, three years each above 5,000), split by publication
  year, then document type, subject area and source type: 20 parts, the largest 4,995. All 19 splits
  add up. Split and counted only; not read.
- **One-go run** (769 records): 769 distinct in the first pass. The per-part limit was 1,000, and 769
  is under it, so this run split nothing.

### Procedure (not implemented in prismabib)

The five steps below are not implemented in prismabib.

1. Split the query by `PUBYEAR` until each part is small enough (5,000 is the hard limit). If one year
   is still too large, split it into `X` and `AND NOT X` on `DOCTYPE`, `SUBJAREA`, `SRCTYPE` or the
   load date. Keep the base query in parentheses and put every `AND NOT` clause last.
2. Check that every split adds up to its parent.
3. Read each part with `start` = 0, 25, 50 and so on, `count=25`, in the default order, keyed by EID.
4. While a part's distinct EIDs are fewer than its total, read it again (another sort, or the default
   again) and take the union. Stop after a fixed number of passes and report the part as incomplete;
   never accept it silently.
5. Stop if a total changes during the read.

### What was exercised live

- **Step 1, split by publication year:** exercised live on Q2 and Q4. Counts only on the 48,162-record set.
- **Step 1, split by document type, subject area or source type:** counts only, on the 48,162-record set.
- **Step 1, split by load date:** counts only, on Q4.
- **Step 2, sum check:** exercised on the Q2, Q4 and 48,162-record splits. The one-go run did not reach it.
- **Step 3, read each part by `start`, keyed by EID:** exercised on Q2, Q4, the Q2 slice and the one-go
  run. Not exercised on the 48,162-record set.
- **Step 4, repeat passes and union:** exercised as a repair on Q4 only, outside the one-go run (the Q2
  union above joins two comparison reads). An extra `+orig-load-date` pass over the parts was followed
  by a re-read of 2008, chosen because its count differed from the records held for that cover-date
  year. That re-read is a narrower query than the part, which the step as written does not describe.
  No live request reached the branch that reports a part as incomplete.
- **Step 5, stop if a total changes during a read:** the stop existed only in the one-go run, which read
  one unsplit part, and it never triggered. The first split reads of Q2 and Q4 had no stop; they counted
  the pages that reported another total, and the count was zero. The later passes did not check. Every
  request of a query with the same parameters reported the same total.

### Request counts

The requests sum to 990: 983 returned HTTP 200 and 7 returned HTTP 400.

| Request set | Requests |
|---|---|
| Limit probes (7 returned HTTP 400) | 11 |
| Q2 direct read (105 pages plus 1 request past the end) | 106 |
| Q2 read through year ranges (includes the counting requests) | 119 |
| Q2 repeated pages | 3 |
| Q4 first pass through year ranges (includes the counting requests) | 270 |
| Q4 first page, unsplit query (the page at offset 4975 is one of the limit probes) | 1 |
| Q2 slice under five sort settings | 125 |
| Q4 orig-load-date pass | 266 |
| Q4 re-read of publication year 2008 (14 pages), plus 1 counting request for 2009 | 15 |
| Q4 load-date split | 2 |
| Machine learning split, counts only | 40 |
| One-go run (1 request for the root total plus 31 pages) | 32 |
| Total | 990 |

### Query strings

Q2 and Q4 are the same strings as in the Queries table above. Verbatim:

- Q2: `TITLE-ABS-KEY("physical education") AND LANGUAGE(spanish)`
- Q4: `TITLE-ABS-KEY(philosophy) AND (LANGUAGE(french) OR LANGUAGE(german)) AND PUBYEAR < 2010`
- Q2 slice (612 records): `(TITLE-ABS-KEY("physical education") AND LANGUAGE(spanish)) AND PUBYEAR > 2020 AND PUBYEAR < 2024`
- Machine learning (split and counted only): `TITLE-ABS-KEY("machine learning") AND PUBYEAR > 2014 AND PUBYEAR < 2018`
- One-go run: `TITLE-ABS-KEY("physical education") AND LANGUAGE(french)`

Year splits append `AND PUBYEAR = Y`, or `AND PUBYEAR > A AND PUBYEAR < B`, to the base query in
parentheses. Load-date split: `ORIG-LOAD-DATE BEF 20150101` and `ORIG-LOAD-DATE AFT 20141231`.

### Capacity

One request per 25 records. The Search quota limit header read 20,000. Taking it as a weekly quota
(the period was not measured), that is at most about 500,000 records a week with no repeat passes.
This is arithmetic, not a measurement.

### Claim bound

Completeness is claimed only for result sets of up to 6,602 records. Completeness means that the
distinct EIDs equal the API's own total. There is no outside reference.

### Limits of this measurement

- One key, one network, about 20 minutes on one day.
- No result set above 6,602 records was read.
- A total that changes between sessions was not seen or tested.
- Elsevier's pages said the Boolean operator precedence would change in 2026. This was not checked.
- The sum check in step 2 would show parts that do not add up to their parent. It would not show that
  the parent query itself is read differently.
- The project does none of this: its search path, `ScopusClient.search`, always sends a cursor.
- The experiment scripts and the recorded responses are kept outside the repository and are not published here.

### Terms of use

No clause on splitting queries was found in the API Service Agreement or in the parts of the other
pages read on 2026-11-27. The agreement asks users to avoid excessive and unnecessary use. The Use
Policies for academic research forbid mining the entire dataset and limit retention of the research
dataset. One policy page was only partly read verbatim. This is not legal advice.
