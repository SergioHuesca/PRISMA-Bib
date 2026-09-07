# Write Taxonomy Rules

How to declare a project's classification dimensions, write versioned regex rules for them,
override a rule's verdict by hand, and read the coverage/audit report that tells you how much
of that is trustworthy.

This is a **library**, not a CLI command (ADR 0023 Decision 8). Everything below is called
from a notebook cell or a script — there is no `prismabib code`.

## The two files a project needs

```
projects/<slug>/taxonomy/
  dimensions.yaml          # what the dimensions are (this project's, not hardcoded)
  rules/
    learning_paradigm.yaml # versioned regex rules for one dimension
    architecture.yaml
  taxonomy_overrides.jsonl # append-only human verdicts (created on first override)
  taxonomy_overrides.jsonl.sha256
```

`dimensions.yaml` and `taxonomy/rules/**` are tracked in the project's own `.gitignore`
allowlist alongside `criteria.yaml` — they are methodology, not derived data, and their diff
history on GitHub *is* the protocol-amendment audit trail (§2.5). `taxonomy_overrides.jsonl`
and its `.sha256` sidecar are allowlisted too, for a different reason: they are the one
taxonomy artefact this stage produces that cannot be recomputed (see
[ADR 0023](../architecture/adr/0023-taxonomy-assignments-are-derived-and-overrides-replace-a-dimension.md)
Consequence 2).

## Step 1: declare your dimensions

```yaml
# taxonomy/dimensions.yaml
dimensions:
  - id: learning_paradigm
    multi_label: false
    categories: [unsupervised, weakly_supervised, self_supervised, semi_supervised,
                 fully_supervised, foundation_zero_shot]
  - id: architecture
    multi_label: true
    categories: [cnn, autoencoder, gan, rnn_lstm, transformer, gnn, diffusion, vlm_foundation]
```

`multi_label: false` means a record carries **at most one** category in that dimension;
`multi_label: true` means it may carry several (a paper can use both a CNN and a transformer).
This is not a stylistic choice — it is what
`prismabib.taxonomy.coder.distribution` enforces
when the counting unit is `papers` (see [Step 4](#step-4-declare-the-counting-unit)).

```python
from prismabib.taxonomy.schema import load_dimensions

schema = load_dimensions(project)
```

Unknown top-level keys, a dimension with no categories, or two dimensions sharing an id all
raise `ConfigError` naming the file, the same way a malformed `criteria.yaml` does.

## Step 2: write a rule file per dimension

```yaml
# taxonomy/rules/architecture.yaml
version: 1.3.0
dimension: architecture
counting_unit: assignments
categories:
  - id: transformer
    any:
      - {field: author_keywords, pattern: '\b(transformer|vision transformer|vit)\b'}
      - {field: title,           pattern: '\btransformer\b'}
    none:
      - {field: abstract, pattern: '\btransformer (?:oil|winding|substation)\b'}
    confidence: 0.9
```

- `field` is one of exactly four values: `title`, `abstract`, `author_keywords`,
  `index_keywords`. A typo (`abstrct`) is refused at load, not silently treated as a field
  that never matches.
- `any` is OR-ed: any one match fires the category.
- `none` is a negative lookaside: a match here *suppresses* an otherwise-fired category — the
  "transformer oil" case above.
- `confidence` is recorded on every assignment this category produces; it is not itself
  compared against a threshold anywhere in this stage.

```python
from prismabib.taxonomy.rules import load_rule_file

rule_file = load_rule_file(project.taxonomy_rules_dir / "architecture.yaml", schema=schema)
```

**Everything that can go wrong here fails at load, not partway through coding the corpus**
(ADR 0023 Decision 6): a malformed regex, an unknown field, a category the dimension does not
declare, a missing `version`/`dimension`/`counting_unit`, or a `dimension:` no schema
declares. A rule file that failed on record 900 of 1000 would already have written 899
assignments under a file that was never valid — this project refuses that outcome by
construction.

One exception is *not* an error: a rule referencing a field that carries no content anywhere
in the corpus (the live `Baseball-CVPR` project's `index_keywords`, always empty — the Scopus
Search API never returns indexed keywords) is valid and matches nothing, silently, forever.
That is reported as a diagnostic in the coverage report, not refused at load: the rule may be
correct for the next corpus, and refusing it would make rule files un-shareable between
reviews.

## Step 3: run the coder

```python
from prismabib.stage import PrismaStage
from prismabib.taxonomy.coder import code
from prismabib.taxonomy.overrides import OverrideLog
from prismabib.store.load import Corpus

corpus = Corpus.open(project)
overrides = OverrideLog(project).load()
result = code(corpus, [rule_file], overrides, stage=PrismaStage.INCLUDED)
```

`code()` is a pure function: `assignments = code(corpus, rule_files, overrides)`. Nothing is
written to the store — a taxonomy assignment is a function of Layer 1 *and* the rule files,
which live in the project's own git repository on their own versioning cadence, deliberately
outside Layer 0 ([ADR 0018](../architecture/adr/0018-abstract-runs-in-layer-1.md)'s "any table
Layer 1 holds must be a function of Layer 0" is why). Re-coding after a rule edit costs one
pass over the corpus and no reconciliation.

`result.effective_categories("architecture")` gives you, per record, the categories currently
in force after folding any human override over the rule output.

## Step 4: declare the counting unit

Every rule file's `counting_unit` is mandatory, and it is not cosmetic:

| Unit | Meaning | What `distribution()` does |
| --- | --- | --- |
| `papers` | Each record contributes at most one count per category. | For a `multi_label: false` dimension, asserts the distribution sums to exactly `\|C\|`; **raises** if some record carries more than one category — this is the check the source manuscript's own taxonomy figure did not have. |
| `assignments` | One count per (record, category) assignment. | Sum may legitimately exceed `\|C\|` for a `multi_label: true` dimension — no error. |
| `mentions` | Raw term frequency. | **Not** a paper distribution; the caption says so explicitly. |

```python
from prismabib.taxonomy.coder import distribution
from prismabib.taxonomy.schema import CountingUnit

dist = distribution(result, schema, "learning_paradigm", CountingUnit.PAPERS)
print(dist.caption())
# "learning_paradigm paper distribution, counting_unit=papers (n=842); multi_label=false."
```

`dist.counts` always includes an explicit `"uncoded"` bucket (no rule fired, nobody has
reviewed the record) and a `"reviewed_none"` bucket (a human looked and confirmed nothing
applies) — the two are kept distinct on purpose (see
[Step 5](#step-5-override-a-verdict-by-hand)). A reader seeing `uncoded: 41%` learns something
true about the rule file; folding it into a category's count would not.

## Step 5: override a verdict by hand

An override **replaces the dimension's entire rule output for that record**, not just the
category it names ([ADR 0023](../architecture/adr/0023-taxonomy-assignments-are-derived-and-overrides-replace-a-dimension.md)
Decision 3 — this amends ADR 0005's original single-category sketch). A reviewer who overrides
has read the paper; leaving a rule-assigned `cnn` beside a human `transformer` would dilute the
judgement with exactly the noise the reviewer was asked to correct.

```python
from prismabib.taxonomy.overrides import OverrideLog

override_log = OverrideLog(project)
override_log.append(
    record_id="scopus:2-s2.0-85101234567",
    dimension="architecture",
    categories=["transformer", "vlm_foundation"],
    reviewer="alice",
    reason="Explicitly a ViT; the CNN mention is a baseline comparison",
    schema=schema,
)
```

`categories=[]` is a valid, meaningful override: "a human looked and nothing applies" — which
is materially different from "nobody looked", and the coder keeps the two states distinct all
the way through to the coverage report's `uncoded`/`reviewed_none` split.

Overrides are appended, never edited, using the identical machinery `decisions.jsonl` uses
(locking, per-write `fsync`, a `.sha256` checksum sidecar) —
`prismabib.prisma.log.AppendOnlyLog`, reused rather than
reimplemented. Bump the rule file's `version`, re-run `code()`, and every override you have
recorded survives untouched: that is the entire value proposition of rules-plus-override
([ADR 0005](../architecture/adr/0005-rules-plus-override-taxonomy.md)).

## Step 6: let the review queue tell you what to look at

The point of rules-plus-override is that a human only ever touches the hard cases:

```python
from prismabib.taxonomy.review import build_review_queue

queue = build_review_queue(result, schema, [rule_file], project_slug=project.slug)
```

`queue.entries` is priority-ordered:

1. **Uncoded** — no rule fired for the dimension.
2. **Conflicting** — more than one category fired in a `multi_label: false` dimension.
3. **Audit sample** — a seeded, reproducible sample of every unambiguously rule-coded record,
   reviewed or not: at least 10%, rounded up. Not the *remainder* — see below for why the
   designated set and the work list are different things.

A record you have already overridden for a dimension — even with `categories: []` — is never
re-queued for it; your verdict already stands.

The audit sample's seed is derived from data, not the clock: `sha256` over the project slug,
the dimension id, the rule file's `version`, and the sorted coded record ids
([ADR 0023](../architecture/adr/0023-taxonomy-assignments-are-derived-and-overrides-replace-a-dimension.md)
Decision 5). Bump the rule version and the sample re-draws itself — exactly when the previous
sample has stopped being evidence about the current rules. `queue.audit_seeds` carries the
resolved seed for every dimension, so an agreement rate quoted in a methods section can be
regenerated from the number printed beside it.

## Step 7: read the coverage report

```python
from prismabib.taxonomy.review import build_coverage_report

report = build_coverage_report(project, result, schema, [rule_file], overrides)
for dimension in report.dimensions:
    print(dimension.dimension, dimension.pct_coded, dimension.audit_agreement_rate)
    print("  by confidence band:", dimension.audit_agreement_by_band)
```

`audit_agreement_rate` is computed from override events actually recorded against
audit-sampled records — never a value you supply. It is `None`, not `0.0`, until at least one
sampled record has actually been reviewed: "no evidence yet" and "0% agreement" are different
facts, and this stage never conflates them.

**The audit sample does not move as you review it.** It is designated once per
`(project, dimension, rule version, corpus)` — `queue.audit_samples[dimension]` — and stays
put while you work through it. Only `queue.entries` shrinks, because that is the *work list*.
Those being two different things is what makes the rate computable at all: an earlier version
drew the sample from the work list, so reviewing a sampled record removed it from the pool, the
next run re-drew a different sample, and the rate was permanently `None` for every corpus.

**Read `audit_agreement_by_band` alongside the headline rate.** A single number averages every
rule that fired, so it moves when the mix of categories in a corpus moves even though no rule
has changed. The bands say which rules the number is about, and a record is placed by the
*weakest* confidence among the categories assigned to it — a record coded partly by a `0.6`
rule is only as trustworthy as that rule.

## On the live corpus

`Baseball-CVPR`'s `\|C\|` is empty until screening (Stage 4/5) has run, so
`test_counting_unit__papers_single_label__sums_to_corpus_size`-style behaviour can only be
exercised on fixtures for now — see the module's own test suite. That is a fact about where
this project is in its pipeline, not a gap in the taxonomy engine.

## What this stage's test suite does *not* check

Rule *content* is validated by the audit agreement rate, not by the unit test suite
([ADR 0023](../architecture/adr/0023-taxonomy-assignments-are-derived-and-overrides-replace-a-dimension.md)
Decision 7). A test asserting `\btransformer\b` matches `"transformer"` tests the `re` module,
not this system — so do not add one. If you want to know whether `architecture.yaml` is a
*good* rule file, look at the coverage report's audit agreement rate after a review round, not
at a green test.

## Common patterns

- **"Add a new method category for X."** Add the category to `dimensions.yaml`, add a
  category rule to the relevant `taxonomy/rules/*.yaml`, bump its `version`, commit both as
  one reviewable diff.
- **"The regex is too broad; it's catching unrelated papers."** Add a `none` clause, or narrow
  the `any` pattern. Re-run `code()` — every existing override survives, and the audit sample
  re-draws itself only if you also bumped `version`.
- **"Thirty records are miscoded; should I rewrite the rule?"** Only if the miscoding is
  systematic. A handful of one-off errors are exactly what `OverrideLog.append` is for — thirty
  separate, reasoned overrides are cheaper and more auditable than a regex rewrite chasing edge
  cases.

See [Architecture Overview](../architecture/overview.md) for where Layer 2 sits, and
[ADR 0005](../architecture/adr/0005-rules-plus-override-taxonomy.md) /
[ADR 0023](../architecture/adr/0023-taxonomy-assignments-are-derived-and-overrides-replace-a-dimension.md)
for the design this page documents the use of.
