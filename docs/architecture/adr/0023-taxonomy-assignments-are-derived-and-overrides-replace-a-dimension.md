# ADR 0023: Taxonomy Assignments Are Derived, and a Human Override Replaces a Whole Dimension

## Status

Proposed — 2026-09-04. Implements BUILD_PLAN Stage 8, under [ADR 0005](0005-rules-plus-override-taxonomy.md),
which already fixed the architecture: versioned rule files as data, human override events,
mandatory counting units, a seeded audit sample.

This ADR records only what ADR 0005 left open, and **amends one thing it fixed**: the override
event's shape (Decision 3). Everything else in ADR 0005 stands.

**No schema change** — Decision 1 is the reason there is none.

## Context

ADR 0005 was written at Stage 1, before Layer 1 existed and before ADR 0018 stated the rule that
governs this stage. Four questions it did not have to answer are now blocking, and one of its
sketches turns out to be unable to express something a reviewer will need on the first day of
coding.

The live corpus makes two of them concrete. `Baseball-CVPR` has 1293 records, an **empty `C`**
(screening has not run), and 4878 author keywords against **zero** index keywords — so a rule
referencing `index_keywords` matches nothing there, silently, forever.

## Decision

### 1. Assignments are computed, never stored — no `taxonomy_assignments` table

ADR 0005 says assignments are "derived from the rule file run over the corpus… not appended to
a log". It does not say whether they are *materialised*. They are not, and the reason is
structural rather than a preference.

[ADR 0018](0018-abstract-runs-in-layer-1.md) fixed the rule: **any table Layer 1 holds must be a
function of Layer 0.** An assignment is a function of Layer 1 *and the rule files* — and rule
files live in the project's own git repository, versioned on their own cadence, deliberately
outside Layer 0. A `taxonomy_assignments` table would therefore be a Layer 1 table that
`build --rebuild` could not reproduce from `raw/` alone, which is the one property Layer 1 has.

So the coder is a pure function:

```
assignments = code(corpus_records, rule_files, overrides)
```

recomputed on demand. Three of Stage 8's acceptance criteria — deterministic, idempotent,
overrides survive a rule-version bump — become properties of a pure function rather than
invariants a store has to maintain. Re-coding after a rule edit costs one pass over the corpus
and no reconciliation at all.

The cost is honest and worth stating: a large corpus re-runs every regex on every read. At 1293
records × a few dozen patterns this is milliseconds. If it ever is not, the answer is a cache
keyed on `(rule file digest, corpus digest)`, not a table.

### 2. Dimensions are declared in `taxonomy/dimensions.yaml`, not in `criteria.yaml`

Both are project-level protocol files, but they answer different questions and change on
different cadences. `criteria.yaml` defines *eligibility*: every screening decision records the
`criteria.version` in force when it was made, and `engine.replay()` resolves that version out of
git history. Folding taxonomy into it means every taxonomy tweak bumps the criteria version, and
a reader auditing an eligibility amendment finds a changed regex instead.

A separate file keeps two audit trails separate.

*Corrected after review:* this ADR originally said both were "already allowlisted in the
project's own `.gitignore`". They are not. The existing allowlist covers `taxonomy/rules/**`
only, so `dimensions.yaml` — the closed category set that every rule file **and every override
event** is validated against — is untracked today. That is worse than it sounds: the override
log would then commit to categories whose definition has no git history, so a replay could
recover *which* category a reviewer chose but not what the dimension declared when they chose
it. `dimensions.yaml` and the override log both need explicit allowlist entries, in the
template *and* in the existing projects, which the template's `if not exists` guard by design
never reaches.

### 3. An override carries the *complete category set* for a `(record, dimension)`

**This amends ADR 0005**, whose event sketch carries a single `"category": "transformer"`.

ADR 0005 says "a human assignment always beats a rule assignment", which is unambiguous for a
`multi_label: false` dimension and ambiguous for a multi-label one. If a reviewer asserts
`transformer`, do the rule-assigned `cnn` and `gan` survive?

They do not. The override event carries `categories: [...]` — a list — and it **replaces the
rules' entire output for that dimension on that record**:

```json
{
  "record_id": "scopus:2-s2.0-85101234567",
  "dimension": "architecture",
  "categories": ["transformer", "vlm_foundation"],
  "reviewer": "alice",
  "reason": "Explicitly a ViT; the CNN mention is a baseline comparison",
  "ts": "2026-01-18T14:22:07.412Z"
}
```

Three reasons, in order of weight:

- **A reviewer who overrides has read the paper; the rules have matched a string.** Leaving a
  rule-assigned `cnn` beside a human `transformer` dilutes the judgement with exactly the noise
  the reviewer was asked to correct — and the reason field above is the ordinary case, not a
  contrived one.
- **The singular form cannot express "none of these apply."** A reviewer looking at a paper that
  fits no category in a dimension has no event to write, so the record stays rule-coded or
  uncoded with no record that a human looked. `categories: []` says it exactly, and needs no
  second event type for removal.
- **The reviewer's unit of work is a record × dimension**, because that is what the review queue
  surfaces. An event shaped like the queue's row is one a reviewer can check; an event shaped
  like a cell requires them to reason about which cells they did *not* write.

The fold is unchanged from ADR 0005 otherwise: human beats rule, then most recent event within
source, keyed on `(record_id, dimension)`.

### 4. The distribution carries an explicit `uncoded` bucket, and only then sums to `|C|`

BUILD_PLAN requires that `PAPERS` counting with a `multi_label: false` dimension be asserted to
sum to exactly `|C|`. Taken literally against assignments alone, that is false whenever any
record is uncoded — no rule fired, no human ruled — and it is false on any real corpus on the
first run.

The resolution is not to relax the assertion but to make the distribution complete: every
record lands in exactly one bucket, `uncoded` included, and the sum is then exactly `|C|` by
construction. A taxonomy distribution that silently omits the records nothing fired on
overstates every category's share, which is the same defect Stage 7's geography `UNK` bucket
exists to prevent, in the same shape, for the same reason.

`uncoded` is a bucket in the *distribution*, never a category in the schema: a rule file cannot
name it, and a human cannot override into it. **Both reserved bucket names are refused by the
dimension-schema loader** — an unenforced sentence here let a `dimensions.yaml` declare
`categories: [uncoded]`, after which a genuinely rule-coded `uncoded` and a record nobody
looked at land in the same integer and the sum-to-`|C|` guard still passes.

### 4b. There are three buckets, not two

*Added after review.* `categories: []` — a human looked and nothing applies — is materially
different from "nobody looked", and Decision 3 introduced the distinction without Decision 4
carrying it into the distribution. The buckets are:

| bucket | meaning |
| --- | --- |
| a category | a rule fired, or a human said so |
| `reviewed_none` | a human read the paper and no category applies |
| `uncoded` | nothing fired and nobody has looked |

Collapsing the middle into the last is the failure to guard against, and it is a *plausible*
failure rather than an arithmetic one: the total still equals `|C|`, so nothing looks wrong,
while human verdicts have been relabelled as absent evidence. Every distribution and every
coverage report keeps all three, and a test must red when any two are merged.

### 5. The audit sample's seed is derived from the data and recorded in the output

Reproducible means reproducible on another machine, in another process, next year. So the seed
is `sha256` over the project slug, the dimension id, the rule file's declared `version`, and the
sorted coded record ids — not `hash()` (varies with `PYTHONHASHSEED`), not the clock, not an
unseeded `random`.

Deriving it from the rule version and the corpus is deliberate: **the sample changes when what
is being audited changes.** A fixed seed would re-draw the same records after a rule rewrite,
which is precisely when the previous sample has stopped being evidence about the current rules.

The resolved seed appears in the review queue's output, so an audit agreement rate quoted in a
methods section can be regenerated from the number beside it.

### 5b. The audit sample is a designated set; the queue is a work list. They are not the same thing

*Added after review, which found the agreement rate unreachable — permanently `None` through
the only path that produces it.*

The original text asked one function to do two jobs that pull in opposite directions:

- a **work list** answers "what should a human look at next?", and must therefore *exclude*
  records already reviewed;
- an **audit sample** is a measurement instrument, and the agreement rate is computed over
  *exactly* the reviewed members of it.

Drawing the sample from the queue's pool made those mutually exclusive by construction. Review
a sampled record, re-run, and it is filtered out of the pool; the sample is re-drawn from what
remains; the rate looks up overrides for records that by definition have none, finds nothing,
and returns `None`. Forever, for every corpus, with no reviewing pattern that escapes it.

So the audit sample is **drawn once over all coded records for a dimension and is stable across
reviewing progress** — it is a function of `(project, dimension, rule_version, corpus)`, which
is precisely what the Decision 5 seed already digests. The queue then presents the *unreviewed*
members as work; the agreement rate measures the *reviewed* members. Reviewing progress changes
which members are outstanding, never which records were designated.

**And the rate must compare against the rule assignments the override replaced.** The coder
removes rule rows for an overridden pair, so a "what did the rules say?" lookup after an
override returns the empty set — making ten reviewers who unanimously *agreed* with the rules
read as 0% agreement. The superseded rule assignments must be retained rather than dropped.
That also gives Consequence 3's mitigation ("the queue shows the current rule assignments as
the starting point") a source it otherwise does not have for a record already reviewed once.

### 5c. Agreement is reported per confidence band

*Added after review.* `confidence` was a field a rule author could set to anything, including
`0.0`, consumed by nothing. Meanwhile "the audit sample is 10% of *confidently* rule-coded
records" was implemented as "non-empty and non-conflicting" — which is a defensible pool, but
it makes the reported rate a corpus-weighted average over every firing rule, moving whenever
the *mix* of categories in a corpus moves and no rule has changed.

Filtering the pool by a confidence threshold is the obvious fix and is backwards: it would
leave the low-confidence rules — the ones whose author has already said "I expect this to be
wrong sometimes" — permanently unaudited. So the pool stays whole and the **report is
stratified**: agreement is given per confidence band, and a methods section quotes a number
that is unambiguous about what it covers.

### 6. Everything a rule file can get wrong fails at load

A malformed regex, a `field:` outside the closed vocabulary, a category absent from the
dimension schema, a missing `version`, a missing `counting_unit`, a `dimension:` that no schema
declares — all raise when the file is read, never when a record is matched. A rule that fails on
record 900 has already written 899 assignments under a file that was never valid.

The field vocabulary is closed: `title`, `abstract`, `author_keywords`, `index_keywords`. A
misspelling like `abstrct` is refused rather than treated as a field that matches nothing,
because a silently-never-matching rule is indistinguishable from a category that genuinely does
not occur.

**One thing this cannot catch, stated rather than implied.** "Everything a rule file can get
wrong fails at load" is false for one class, and a one-line rule file falsifies it: `(a+)+$`
compiles without complaint and takes over five seconds against a 27-character string. Across a
corpus that is an unbounded hang partway through coding, not an error. Catastrophic
backtracking is undecidable at load for any backtracking engine, so it is **out of scope** and
named here rather than left as a gap between the claim and the code.

**One warning that is not an error.** A rule referencing `index_keywords` on a corpus with no
index keywords — the live corpus, exactly — is *valid* and matches nothing. That is worth
surfacing in the coverage report as a diagnostic, and it is not a load failure: the rule may be
correct for the next corpus, and refusing it would make rule files un-shareable between reviews.

### 7. Rule *content* is validated by the audit sample, not by unit tests

Restated from ADR 0005 as a constraint on this stage's test suite because it is the rule most
likely to be broken with good intentions. A test asserting `\btransformer\b` matches
`"transformer"` tests the `re` module. The suite tests the *engine* — load-time validation,
determinism, idempotency, fold precedence, counting-unit arithmetic, queue ordering. Whether
`architecture.yaml` is a good rule file is measured by the audit agreement rate, and reported.

### 8. Library-only, as Stage 7 was

No `prismabib code` command in this stage. The CLI is Stage 11's, `limitations.md` already says
so of both, and adding a command here would make Stage 11's surface a moving target.

## Alternatives considered

**Materialise assignments in Layer 1.** Rejected in Decision 1: it makes a Layer 1 table that
Layer 0 cannot reproduce.

**Keep ADR 0005's singular `category` and add a `remove` event type.** Rejected: two event types
where one list suffices, and the fold then has to define what a `remove` for a category no rule
assigned means. The list makes the reviewer's assertion total and self-describing.

**Assert the `PAPERS` sum over coded records only.** Rejected in Decision 4 — it makes the
assertion pass by narrowing the denominator, which is the failure it exists to catch.

**A fixed audit seed.** Rejected in Decision 5: it stops re-sampling exactly when the rules
change.

## Consequences

1. **A rule-version bump costs one re-run and nothing else.** No migration, no reconciliation,
   no stale table. This is the property that makes iterating on rules cheap, which is the whole
   argument for rules-plus-override.
2. **Human overrides are the only irreplaceable artefact this stage produces**, and they get the
   decision log's guarantees: append-only, `.sha256` sidecar, the same locking. The project
   `.gitignore` must allowlist `taxonomy/taxonomy_overrides.jsonl` and its sidecar — it
   currently allowlists `taxonomy/rules/**` only, so an override written today would be
   untracked, which for the one thing that cannot be recomputed is the worst available outcome.
3. **A multi-label override loses information a reviewer might have wanted to keep** — the rule
   assignments they did not restate. Accepted, and mitigated by the review queue showing the
   current rule assignments as the starting point for the event.
4. **`uncoded` appears in every distribution**, including figures. Intended: a reader seeing
   `uncoded: 41%` on the first pass learns something true and useful about the rule file.
5. **Stage 8's central guard cannot be exercised on the live corpus yet**, because `C` is empty
   until screening runs. The suite covers it on fixtures; the first real corpus to exercise it
   is a Stage 11 concern.

## Constraints

- No new Layer 1 table; assignments are computed from `(corpus, rules, overrides)`.
- Every rule-file defect raises at load, never at match time.
- The field vocabulary is closed; an unknown field is an error, an empty-matching valid field is
  a reported diagnostic.
- An override carries a complete `categories` list and replaces the dimension for that record.
- `PAPERS` + `multi_label: false` sums to exactly `|C|` across all three buckets, and the
  assertion must fail loudly on a deliberately over-assigned fixture.
- `uncoded` and `reviewed_none` are reserved names the dimension schema refuses as categories.
- The audit sample is stable across reviewing progress, and the agreement rate is computable
  through the documented workflow — a rate that can only ever be `None` is not a rate.
- Agreement is reported per confidence band, so `confidence` affects something.
- The audit seed is data-derived, machine-independent, and reported.
- No test asserts that a particular regex matches a particular string.
- Overrides are append-only with a checksum sidecar, and are allowlisted in the project repo.

## Related decisions

- [ADR 0005](0005-rules-plus-override-taxonomy.md) — the architecture this implements; Decision 3
  amends its event sketch
- [ADR 0002](0002-append-only-decision-log.md) — the append-only guarantees overrides reuse
- [ADR 0018](0018-abstract-runs-in-layer-1.md) — "any table Layer 1 holds must be a function of
  Layer 0", which is why Decision 1 stores nothing
- [ADR 0022](0022-the-analysis-result-contract-and-its-provenance.md) — Stage 7's `UNK` bucket,
  the same argument as Decision 4's `uncoded`
