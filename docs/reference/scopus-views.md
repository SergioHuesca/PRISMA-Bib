# Scopus views: what the project's key can reach

Measured on 2026-11-25 against `api.elsevier.com/content` with the key in `.env`, with and
without `SCOPUS_INSTTOKEN`. Each cell is one live call (HTTP status). The insttoken changed
nothing in any cell.

Entitlements are granted per API key and often per institution. These results are for one key,
so another key or another institution may get different results.

A later measurement, on 2026-11-27, covers Search `STANDARD` paging and sample coverage. It is in the last section. RDF was not re-measured then.

| API | View | Result |
|---|---|---|
| Search (`/search/scopus`) | `STANDARD` | 200 |
| Search | `COMPLETE` | **401** `AUTHORIZATION_ERROR` |
| Abstract Retrieval (`/abstract/eid/{eid}`) | `META` | 200 |
| Abstract Retrieval | `META_ABS`, `FULL`, `REF` | **401** |
| Abstract Retrieval | `ENTITLED` | 401 |

## Widest Search view available: `STANDARD`

Search `STANDARD` is the widest view this key can read, but only without the `cursor` parameter: on 2026-11-27 every cursor request under `STANDARD` was refused (see the last section). Code reading: `ScopusClient.search` always sends a cursor, so for this key it would be refused on page 1; the client was not run. Abstract Retrieval `META` as JSON adds
only a few fields over it: an author block for one sampled record, `dc:publisher`, `srctype` and
`link` (see below). It costs one call per record against a separate quota, so it is not a
practical bulk path.

Fields returned (25-record sample; the share is how many records carried it):

- **Always present in the 25-record sample:** `eid`, `dc:identifier`, `dc:title`, `dc:creator` (first author only),
  `affiliation` (name, city, country), `prism:publicationName`, `prism:coverDate`,
  `prism:aggregationType`, `subtype`, `subtypeDescription`, `source-id`, `citedby-count`,
  `openaccess`, `openaccessFlag`, `prism:url`. On 2026-11-27, `dc:creator` was on 302 of 303 recorded entries and an affiliation object on 280 of 303, so neither is always present (see the last section).
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
  is a code change that needs the Main Flow and its own spec. Level 2 sends one DOI-batch query with no cursor (`search_standard_by_dois`, code reading), so the cursor refusal of 2026-11-27 does not apply to it as coded; that request was not run with this key.

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

1. Search `STANDARD` for the ids and the basic metadata. Paging past the first page by cursor is refused under `STANDARD` on this key (2026-11-27), so this step is not settled.
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

Paging past one page by cursor is refused under `STANDARD` for this key, so the check failed.
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
- Offsets above 150.

### Limits

- One key, one network, one day. The cursor refusal was seen on all five cursor requests, but
  another key or institution may have a different entitlement.
- Pre-2000 coverage comes from Q1 only (28 entries).
- Non-English status rests on the query restriction. A record may list more than one language.
- Default sort order. First pages only for Q2 and Q4.
- Raw responses were kept outside the repository and are not published here.
