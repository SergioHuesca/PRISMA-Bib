# Scopus views: what the project's key can reach

Measured on 2026-11-25 against `api.elsevier.com/content` with the key in `.env`, with and
without `SCOPUS_INSTTOKEN`. Each cell is one live call (HTTP status). The insttoken changed
nothing in any cell.

Entitlements are granted per API key and often per institution. These results are for one key,
so another key or another institution may get different results.

| API | View | Result |
|---|---|---|
| Search (`/search/scopus`) | `STANDARD` | 200 |
| Search | `COMPLETE` | **401** `AUTHORIZATION_ERROR` |
| Abstract Retrieval (`/abstract/eid/{eid}`) | `META` | 200 |
| Abstract Retrieval | `META_ABS`, `FULL`, `REF` | **401** |
| Abstract Retrieval | `ENTITLED` | 401 |

## Widest Search view available: `STANDARD`

Search `STANDARD` is the widest view this key can read. Abstract Retrieval `META` as JSON adds
only a few fields over it: an author block for one sampled record, `dc:publisher`, `srctype` and
`link` (see below). It costs one call per record against a separate quota, so it is not a
practical bulk path.

Fields returned (25-record sample; the share is how many records carried it):

- **Always present:** `eid`, `dc:identifier`, `dc:title`, `dc:creator` (first author only),
  `affiliation` (name, city, country), `prism:publicationName`, `prism:coverDate`,
  `prism:aggregationType`, `subtype`, `subtypeDescription`, `source-id`, `citedby-count`,
  `openaccess`, `openaccessFlag`, `prism:url`.
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
  is a code change that needs the Main Flow and its own spec.

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

### What is still unreachable

**The abstract.** It appears only in `META_ABS`/`FULL` (401). No media type, `field=` value or
id type returned it, and no other endpoint tried carries it. With this key it must come from
another source (OpenAlex returned one for 10 of 10).

### Recommended path with this key

This is a recommendation for later work. It is not what the project's cascade does today.

1. Search `STANDARD` for the ids and the basic metadata.
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
