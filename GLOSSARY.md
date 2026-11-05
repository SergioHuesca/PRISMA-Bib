# prismabib

A tool for running a reproducible PRISMA 2020 systematic review, in which every number in the flow diagram and the manuscript is derived from stored data and never typed by hand.

## Language

### Data layers

**Layer 0**:
The immutable archive of what a search returned, kept as it arrived. Nothing downstream may change it.
_Avoid_: raw store, cache

**Layer 1**:
The normalised store of records, authors, venues, keywords and citations, rebuilt entirely from Layer 0. Disposable by design.
_Avoid_: database, corpus

**Layer 2**:
The human judgement that sits on top of Layer 1: the decision log and taxonomy coding. Append-only.

**Layer 3**:
The figures, tables, flow diagram, dashboard and exports computed from Layers 1 and 2. It stores no numbers of its own.

### Capture

**Project**:
One systematic review, with its query, eligibility criteria, captured data and decisions.

**Source mode**:
Which databases a project searches: OpenAlex only, Scopus only, or Scopus plus OpenAlex filling gaps. Recorded per project and fixed once a search or a decision exists.

**Run**:
One execution of a search, stored in Layer 0 with a manifest describing it.

**Sealed run**:
A run that finished completely and whose manifest is written. A sealed run is never modified.
_Avoid_: completed run, finished run

**Record**:
As in PRISMA 2020: the title or abstract (or both) of a report indexed in a database. Here, one such item in Layer 1, identified by a stable record id.
_Avoid_: paper, work, entry (these name the source-specific raw form)

**Report**:
As in PRISMA 2020: a document supplying information about a particular study, such as a journal article, preprint or conference paper.

**Study**:
As in PRISMA 2020: an investigation that may be described in several reports. The flow counts here are counts of records; "reports not retrieved" cannot be expressed in them.

**Registered record**:
A record a reviewer adds because the search missed it. It is counted on its own line in the flow and never added to the identified count.

**Enrichment**:
Fetching only the subject-area codes of every record from Scopus Abstract Retrieval, which the search does not return, so that a subject-area restriction can be applied. It must be complete before screening, because a half-enriched set of records cannot be filtered by subject area.
_Avoid_: OpenAlex enrichment (that is the cascade)

**Cascade**:
Filling a record's missing abstract, authors, affiliations and keywords (not subject-area codes) from further sources in a fixed order: Scopus STANDARD, then Scopus META, then OpenAlex. The two Scopus steps run only when you opt in and set a Scopus budget; otherwise only OpenAlex runs. Records with no DOI are first looked up in OpenAlex by title, to find a DOI for the Scopus steps.

### Eligibility

**Criteria**:
The versioned, machine-readable eligibility rules of a project. Every amendment gets a new version.

**Automated exclusion**:
Removal of a record by the criteria alone (year, subject area, document type, venue, language), with no human decision.

**Decision**:
One reviewer's include, exclude or unsure judgement on one record at one screening stage, stored as an immutable event.
_Avoid_: label, vote

**Decision log**:
The append-only sequence of decisions. A change of mind is a new decision; the current state is derived from the whole log.

**Screening stage**:
One of the positions a record can reach in the PRISMA flow: raw, automated, language, title and abstract, full text, included. Only title and abstract and full text take human decisions.
_Avoid_: stage on its own, phase

**Build stage**:
One of the numbered units (0 to 11) in which the tool itself is built and released. Unrelated to a screening stage.
_Avoid_: stage on its own

**Unsure**:
A logged decision that keeps the record in the queue. It never counts as inclusion.

**Unscreened**:
A record in the queue with no decision logged at the current screening stage. The flow output prints unsure and unscreened on one line, but they are different states.

**Reason code**:
The label required on every exclusion, chosen from a closed list declared in the criteria.

**Queue**:
The records that passed the automated criteria and are waiting for, or under, title and abstract screening.

### PRISMA flow

**Identified**:
The number of records the searches reported, summed over the project's distinct searches, before any removal. Re-running the same search does not add to it.

**Duplicates across searches**:
Records returned by more than one distinct search. They are removed before screening and reported on their own line.

**Duplicate DOI group**:
Records that share a normalised DOI. Reported, never removed; removing one is a screening decision.

**Included studies**:
The records whose full-text decision is include after passing every earlier screening stage. This is PRISMA 2020's "studies included in the review".
_Avoid_: corpus, final corpus (PRISMA 2020 does not use the word)

**Flow counts**:
The full set of numbers in the PRISMA 2020 diagram. They are recomputed from stored data on every request and must satisfy the diagram's consistency equations.
