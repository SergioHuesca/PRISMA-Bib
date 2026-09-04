# ADR 0020: A Crossref TDM Resolver, and Assisted Manual Fetching

## Status

Accepted — 2026-09-03. Amends [ADR 0019](0019-fulltext-resolution-and-coverage.md)'s chain-order
constraint, which fixed the chain at "ScienceDirect → open access → manual drop, first hit
wins". A fourth resolver changes that, and §2.6 requires an ADR for any deviation from a
frozen constraint. Every other ADR 0019 constraint stands unchanged: a 403 still records
`entitled = false` and continues, `INACCESSIBLE` is still constructible only inside
`screening/`, publisher is still derived from the DOI, and Layer 1 is still rebuilt from
sealed Layer 0 runs.

No schema change. No published number moves.

## Context

Stage 6's chain resolves what is reachable without credentials. On the first real corpus
(`Baseball-CVPR`, 35 records sought for retrieval) that is **6**. The other 29 are paywalled,
or served by hosts that refuse automated clients.

The operator's stated design goal is that **human intervention belongs at screening, not at
fetching**. That is the right principle — eligibility is a judgement, retrieval is mechanics —
and the question is how far it can honestly be taken.

### What the measurements say, including the one that disappoints

Crossref exposes publisher-declared text-mining links in its free, keyless REST API. An
initial 8-record sample suggested half the residue carried one. **Measured across all 29, it
does not:**

| | count |
| --- | --- |
| no text-mining link at all | **23** |
| Springer, `application/pdf` | 3 |
| Springer, `text/html` | 3 |
| Elsevier, `text/xml` / `text/plain` | 4 |
| ACM, `unspecified` | 1 |

Realistic yield on this corpus is **about three records**, and the Elsevier links are
unusable here because this operator has no Elsevier entitlement — the same 403 that already
refuses all 35 ScienceDirect attempts. The earlier sample was unrepresentative and is
recorded here so nobody re-derives an optimistic number from it.

This ADR is therefore written with its own cost-benefit stated plainly: **the resolver is
built for future corpora, not for this one.** A review weighted toward Springer, Wiley or
Elsevier would benefit substantially; this one gains three papers for a client, a resolver,
and their tests. The project owner made that call knowing the number.

### The part of the gap no API closes

Twenty-three records have no machine-readable route at all. For those the honest answer is
that a human must fetch them, and "human only at screening" is unachievable by any means —
including browser automation, because the content is not machine-reachable, not merely
inconvenient.

What *can* be removed is the bookkeeping. The manual drop requires a filename derived by
`manual_drop_path`, whose sanitisation is lossy and non-reversible: `scopus:2-s2.0-…` becomes
`scopus_2-s2.0-….pdf`. A reviewer who saves a downloaded PDF under the record id verbatim
produces a file the resolver never looks for, and nothing reports the mistake — the record
simply stays unresolved.

## Decision

### 1. `CrossrefTdmResolver`, positioned second in the chain

```
ScienceDirect → Crossref TDM → open access → manual drop
```

Second, not last, because a TDM link is the publisher's own full text — the version of record
— whereas Unpaywall may legitimately return an author preprint. A review that can cite the
published version should prefer it, and `resolver_name` keeps which one was used visible in
the coverage table either way.

### 2. Only what sniffs as a PDF is accepted

`looks_like_pdf` decides, not the advertised content type. Springer's `application/pdf` is the
usable case, `text/html` is a landing page wearing a TDM label, and ACM's `unspecified` says
nothing at all. This is the same discipline the open-access resolver already applies, and for
the same reason: a landing page accepted as full text is counted as resolved forever, which
over-states coverage in the one artefact whose job is not to.

Elsevier's `text/xml` is excluded by the rule below rather than by content type.

### 3. A TDM link is skipped when a dedicated resolver already covers its host

`api.elsevier.com` is ScienceDirect's host, and `ScienceDirectResolver` has already tried it
one position earlier in the chain. Without this rule the same record is refused twice for one
underlying cause, and `coverage_by_resolver_table` reports two entitlement gaps where there is
one — inflating precisely the number ADR 0019 exists to keep honest.

### 4. Assisted manual fetching is split by testability, and is not a CLI command

`cli.py`'s own help states the posture: the app is the non-interactive half of the tool, and
*"decisions are human events, and a CLI is the wrong place to make them."* A prompting command
contradicts that, and the command surface is pinned deliberately by a meta-test.

So the work splits where the risk is:

- **`src/prismabib/fulltext/assist.py`** — identification and filing, under `mypy --strict`
  and the `fulltext` coverage gate. This is where a defect attaches one paper's full text to
  another record, so it is tested at full standard.
- **`scripts/fetch_assist.py`** — the interactive driver, alongside the existing
  `scripts/fulltext_missing.py`: opens a batch of DOI links in the operator's browser, watches
  their download directory, and files what arrives.

**Every retrieval stays human-initiated, one to one.** The script fetches nothing. It opens
tabs the operator would have opened and files what the operator downloads — the mechanical
half — while the access itself remains an ordinary use of their own subscription. This is a
deliberate boundary: automating the *access* would be systematic downloading under most
publishers' terms, and the blast radius of that is the institution's access, not one account.

### 5. Ambiguity prompts; it never guesses

Identification tries the DOI on page 1 first, then token containment against the record's
title, and requires a **margin** over the runner-up. Measured against the six PDFs already
fetched: DOI matched 2 of 6, title containment matched 5 of 6 confidently, and the sixth was
correctly refused — two near-identical baseball-video titles, runner-up at 0.92.

That refusal is the feature. A wrong match is silent, durable, and produces a review whose
full-text assessment was performed on the wrong paper.

## Alternatives rejected

### 1. Browser automation of retrieval (Playwright, Zotero-like, human-paced)

Drive a real browser through the operator's authenticated session and download the residue
automatically.

*Rejected.* Human-like pacing solves neither problem it appears to. Not the terms question:
publisher clauses concern systematic *access*, not rate, and one human action producing 29
downloads is the shape they name. Not the technical one either: bot detection fingerprints the
automation stack rather than the timing, and MDPI already refuses this project's plain HTTP
client — a paced Playwright would be building evasion, which is where this stops regardless.
The maintenance is also worse than it looks: twelve publishers, twelve DOM layouts, several
SSO flows, PDFs that open in embedded viewers, and breakage on every redesign.

The assisted approach keeps the useful half — no manual bookkeeping — and gives up only the
clicking, which at this scale is under an hour.

### 2. Accept Elsevier's TDM `text/xml`

Extract it with `extract_sciencedirect_xml`, which already parses that schema.

*Rejected here, not forever.* It is the same host and the same entitlement that already
refuses every ScienceDirect attempt for this operator, so it would resolve nothing and would
double-count the refusal (Decision 3). An operator holding an Elsevier institutional token
would benefit, and the rule to relax is the host-dedup one — not the PDF-only one.

### 3. Fold the interactive driver into `prismabib` as a command

One entry point, discoverable via `--help`.

*Rejected:* it contradicts a stated architectural posture, it would be the first interactive
command with no testing precedent for one, and `webbrowser.open_new_tab` spawns a process that
`pytest-socket` does not intercept — a test that forgot to inject the opener would launch a
real browser on CI. `scripts/` already holds the operator-facing hand-fetch tool.

## Consequences

1. **About three more records resolve on this corpus.** Stated so the next reader does not
   expect more. The value is in the capability, not this measurement.
2. **The manual residue costs clicks, not filing.** The `:`→`_` trap — a silent loss today —
   stops existing for anyone who uses the driver.
3. **`webbrowser` and directory polling are standard library.** No new dependency; the
   project's dependency block requires every entry to trace to a named BUILD_PLAN row.
4. **The chain grows to four resolvers**, so `coverage_by_resolver_table` grows a row. The
   by-publisher table is unaffected: publisher still comes from the DOI, never the resolver.
5. **`prismabib fulltext` now reaches the network with no credentials configured.** This was
   not anticipated when this ADR was first written, and it is a real behaviour change rather
   than an implementation detail.

   Every other resolver degrades out of the chain when its credential is absent, so a project
   with an empty `.env` previously ran manual-drop-only and made no request at all — a
   property several tests relied on and stated in their own docstrings. Crossref requires no
   credential, so it cannot degrade the same way and is always present.

   Accepted rather than worked around: a keyless public lookup that finds publisher-declared
   full text is useful precisely to the operator who has no subscriptions, and making it
   conditional on an unrelated credential would be arbitrary. But the consequence is that
   **there is currently no way to run the chain fully offline**, which the previous shape gave
   for free. If that is ever wanted, it needs an explicit switch rather than the absence of a
   key, and this ADR should be superseded rather than quietly reinterpreted.

## Constraints

- Chain order is ScienceDirect → Crossref TDM → open access → manual drop, first hit wins.
- A TDM link whose host a dedicated resolver already covers is not fetched.
- Only bytes that pass `looks_like_pdf` are accepted from a TDM link.
- The assisted driver never performs a retrieval. It opens links and files downloads.
- Identification refuses on an insufficient margin and asks. No threshold may be set such that
  a best guess is filed unattended.
- Filing copies, never moves, and never overwrites an existing drop.
- The browser opener is an injected parameter, so no test can launch a browser.

## Related decisions

- [ADR 0019](0019-fulltext-resolution-and-coverage.md) — the chain this amends, and the
  coverage table whose honesty Decision 3 protects
- [ADR 0003](0003-human-only-screening.md) — screening decisions are human, which is why
  `INACCESSIBLE` remains outside all of this
- [ADR 0011](0011-abstract-retrieval-for-subject-areas.md) — the entitlement asymmetry that
  makes Elsevier's TDM links unusable here

## References

- `src/prismabib/sources/crossref.py`, `src/prismabib/fulltext/resolve.py` —
  `CrossrefTdmResolver`, `default_chain`
- `src/prismabib/fulltext/assist.py`, `scripts/fetch_assist.py`
- [Crossref REST API — `link` and `intended-application`](https://api.crossref.org/swagger-ui/index.html)
- Measured 2026-09-03 on `Baseball-CVPR`: 29 unresolved, 23 with no text-mining link

---

This ADR records that the resolver chain gains a Crossref text-mining step in second position,
that only sniffed PDFs are accepted from it, that a TDM link is skipped when a dedicated
resolver already covers its host, and that assisted manual fetching automates filing but never
retrieval. Reordering the chain, accepting non-PDF TDM content, fetching an
already-covered host, or having the driver perform a retrieval itself requires a new ADR that
supersedes this one (§2.6).
