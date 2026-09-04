# ADR 0022: Every Bibliometric Number Carries the Corpus and the Knobs That Produced It

## Status

Proposed — 2026-09-04. Implements BUILD_PLAN Stage 7. Amends the frozen Stage 3 `Corpus`
contract (BUILD_PLAN lines 891-896) by adding three read accessors — see Decision 9 — which is
a §2.6 deviation and the reason this ADR exists before any code.

Supersedes nothing. Re-points two Stage 10 functions at this stage's definitions (Decision 5).

## Context

Stage 7 computes every quantitative finding in the review. BUILD_PLAN §1.4 names the failure
mode the whole project exists to prevent: **a plausible wrong number in a published paper.**
Stages 0-6 protected the *inputs* to a number — provenance, immutability, honest coverage.
This stage is the first that produces numbers a reader will quote, and it is therefore the
first where a defect is directly a wrong claim rather than a wrong input to one.

Three properties of this project's actual data make that concrete, and each drove a decision
below. Measured on the live `Baseball-CVPR` store, 1293 records:

- **The corpus contains records dated 2027.** Ahead-of-print. Any "is the final year partial?"
  test written against the wall clock is both wrong here and *differently* wrong next January.
- **769 distinct venue names over 1293 records.** Scopus emits variants. Stage 10 already
  groups venues by exact name and publishes `venues.total`; if this stage normalises and Stage
  10 does not, one export bundle carries two different answers to "how many venues".
- **Citation snapshots are uniform today (one `retrieved_at`) and are not guaranteed to be.**
  A caption reading "as at 2026-09-02" is a lie the moment a second capture refreshes half the
  corpus.

BUILD_PLAN sketches the contract in a comment — `corpus size, criteria_version, citation
snapshot, run_id` — and leaves the shape to this stage. Two of those four are singular in the
sketch and plural in reality.

## Decision

### 1. `AnalysisResult` and `Provenance`

```python
@dataclass(frozen=True)
class Provenance:
    corpus_size: int  # n, after `stage` filtering
    stage: PrismaStage  # which PRISMA set the number is over
    retrieved_at: datetime | None  # max(runs.started_at): the corpus's own as-at date
    run_ids: tuple[str, ...]  # every sealed search run contributing, sorted
    criteria_versions: tuple[str, ...]  # distinct, sorted
    citation_snapshot: datetime | None  # None unless this analysis read citations
    citation_snapshot_is_uniform: bool  # False when records disagree on retrieved_at


@dataclass(frozen=True)
class AnalysisResult:
    data: pl.DataFrame
    params: Mapping[str, Any]
    provenance: Provenance

    def caption(self) -> str: ...
```

**`run_ids` and `criteria_versions` are plural because the corpus is.** `Baseball-CVPR` has ten
sealed runs. A `run_id: str` field would have to pick one, and every caption would then name a
single run as the source of a number computed over ten. That is not a simplification, it is a
false statement of provenance — the precise class of defect ADR 0021 was written to remove from
the coverage table. Where a corpus really does have one run, the tuple has one element and the
caption reads identically to the singular form.

**`citation_snapshot` is `None` for an analysis that did not read citations.** A keyword
frequency table captioned "citations as at 2026-09-02" invites a reader to believe citations
entered the number. `retrieved_at` — the corpus's own as-at date — is always present, so every
caption can still state *when the corpus was*, which is what
`test_all_analyses__caption__contains_n_and_snapshot_date` is really asking for.

**`citation_snapshot_is_uniform` changes the caption's wording, not just its data.** Uniform:
`citations as at 2026-09-02`. Not uniform: `latest citation snapshot per record, most recent
2026-09-02`. The second sentence is longer and less quotable, which is correct: the underlying
fact is messier, and a caption that hides that is worth less than one that states it.

### 2. "Partial final year" is derived from the corpus, never from the clock

A publication year is **complete** for this corpus only if the whole year had elapsed when the
corpus was retrieved. So:

```
first_incomplete_year = year(max(runs.started_at))
```

Every year `>= first_incomplete_year` is partial. This captures both cases the live data
presents: 2026, in which the corpus was retrieved part-way through, and 2027, which had not
begun. A record dated after the retrieval date is ahead-of-print, and its year is not a year of
publication output at all.

**The obvious implementation — `datetime.now().year` — passes every local test and is a
Stage 11 defect.** S11 requires a clean clone on a different machine to reproduce
`numbers.json`; a clock-derived boundary reproduces only until midnight on 31 December, and
never on a machine in a different timezone within ten hours of the year boundary. This project
has already shipped three defects of exactly this class (CLAUDE.md, "watch for
machine-dependence"). No module under `bibliometrics/` may call `datetime.now`, `date.today`,
or `time.time`; a source scan test enforces it.

### 3. CAGR

`CAGR = (V_end / V_start)^(1 / (Y_end - Y_start)) - 1`, computed over **complete years only**
by default. The result exposes `v_start`, `v_end`, `year_start`, `year_end`, `span_years`, and
`partial_years_excluded` — a bare growth rate is not checkable, and the BUILD_PLAN makes
exposing them an acceptance criterion rather than a nicety.

Degenerate inputs raise `AnalysisError` rather than returning a number:

| input | why it raises |
| --- | --- |
| `V_start == 0` | the ratio is `inf`; "infinite growth" is not a finding |
| `span_years == 0` | a single year has no growth rate; the root is undefined |
| fewer than two complete years | same, after partial-year exclusion — and this is the case a corpus retrieved in January produces |

`include_partial_final_year=True` overrides the exclusion, records itself in `params`, and the
caption then says so. It exists because a reader may legitimately want the number the naive
method gives; it is never the default, because including a part-year understates growth badly
and silently.

The CAGR caption states `year_start`, `year_end`, `span_years`, `v_start` and `v_end`, not only
the rate. Decision 3's "a bare growth rate is not checkable" is not satisfied by putting them in
`data` alone: the caption is the sentence that travels into a manuscript, and on the live corpus
the rate is 13.8% anchored on `v_start = 1` — a single 1986 paper. A reader who cannot see that
anchor cannot judge the number, and a reader who can will immediately want to re-base it.

### 3b. Partial years are marked wherever they are *shown*, not only where they are excluded

*Added after implementation, when the first version was measured on the live corpus.*

Scoping the partial-year rule to CAGR alone was wrong, and measurably so. `annual_counts` — the
frame behind the publication-trend figure, the most-reproduced chart in any review of this kind
— returned the live corpus's tail as:

| year | count |
| --- | --- |
| 2025 | 153 |
| 2026 | 139 |
| 2027 | 1 |

with `params = {}` and a caption saying nothing about it. Plotted, that is a visible decline
beginning in 2025. There is no decline: the corpus was captured on 2 September 2026, so 2026 is
two-thirds of a year and 2027 is a single ahead-of-print record. CAGR excluded both and was
right; the figure a reader actually looks at showed both and said nothing.

**Stage 9 cannot repair this.** Its figure functions are forbidden to compute, so the flag
cannot be derived at the figure layer — it has to exist in the frame. So `annual_counts` gains
an `is_partial` boolean column, and `first_incomplete_year` enters `params` and therefore the
caption. Marking is deliberately chosen over dropping: a trend figure that silently ends two
years before the corpus does is its own kind of dishonest, and a reader is entitled to see the
part-year as long as it is labelled as one.

The general rule this stage now follows: **a value that is excluded from one number because it
is incomplete must be marked as incomplete everywhere else it is shown.** Excluding it in one
place and displaying it unannotated in another is worse than doing neither, because the two
outputs disagree while both look authoritative.

### 4. Geography declares its counting method

`full` (default): each distinct country on a paper counts once. Shares are over records, so
they sum to **more** than 100% and that is the documented, intended behaviour, not a bug to be
normalised away. `fractional`: each record contributes exactly `1.0`, split `1/k` over its `k`
distinct countries; shares sum to 1.0 within float tolerance.

Records with no country-bearing affiliation go to an explicit `"UNK"` bucket — 58 of 1293 on
the live corpus. Both modes must account for every record: dropping the unknowns would make the
denominator quietly smaller than the corpus and every share quietly larger.

The mode is in `params` and in the caption. A country share is uninterpretable without it, and
the two methods can differ by a factor of two on a heavily co-authored corpus.

### 5. Venue normalisation is defined here, and Stage 10 delegates to it

Normalisation is deliberately conservative — casefold, collapse whitespace, strip a trailing
parenthetical, strip a leading `The`, unify `&`/`and`, drop trailing punctuation. Nothing that
merges venues on a similarity score: a wrong merge silently invents a venue that published
papers it did not.

The display name for a normalised group is the **most frequent** raw variant, ties broken
lexicographically, so the choice is deterministic and reproducible.

**`report/numbers.py::_venue_numbers` and `report/tables.py::top_venues_table` must be
re-pointed at this module.** They group by exact `name` today. Leaving them would put two
definitions of "a venue" in one export bundle — and `citation_statistics_table`'s own docstring
already states that principle for citations ("computing both from one source makes that
disagreement impossible rather than unlikely"). The same argument applies to
`_citation_numbers`, which must delegate to `citations.py`.

This was expected to move `venues.total` on the live corpus. **It did not, and the measurement
is recorded here rather than quietly dropped.** On `Baseball-CVPR` (1293 records, 769 distinct
venue names) not one of the six rules fires on a single name: no trailing parenthetical, no
leading `The`, no `&`, no trailing punctuation, nothing collapsible. `venues.total` is 769
before and 769 after; every `citations.*` key is identical; no golden moved.

The variants Scopus actually emits on this corpus are **conference editions** — "Proceedings
2017 IEEE Winter Conference on Applications of Computer Vision Workshops WACVW 2017" beside the
2020 and 2024 editions of the same workshop, 15 such groups spanning 21 names. The difference is
a *year*, not formatting, so a rule set forbidden from merging on similarity cannot reach them
by construction.

Two consequences follow, and both are decisions rather than observations:

- **The normalisation rules stay**, because their cost is nil and they are a real safety net for
  a corpus that does carry `The X` / `X & Y` / `X (Series B)`. But no document may claim they
  *had* an effect here. The value actually delivered by Decision 5 is the **delegation** — one
  definition of "a venue" shared by `numbers.json`, `top_venues_table` and this stage, so a
  table and the prose beside it cannot disagree. That value is fully realised and wholly
  independent of whether any rule fires.
- **Folding conference editions is out of scope, by design, and must be documented as a
  limitation rather than left as an implication.** `venues.total = 769` does not mean "769
  venues"; it means "769 distinct venue name strings, in which a recurring conference appears
  once per edition". Measured, edition-folding would give 746 — a 3% change that leaves the
  top-ten table byte-identical. That is far too small a gain to justify flipping a published
  number inside this stage, and the question of whether WACV 2020 and WACV 2024 are one venue is
  a methodological choice a reviewer must make explicitly, not a default this stage should pick
  for them. It becomes a declared `normalisation` mode later, with the unit of count named in
  the caption — the same discipline Stage 8 applies with `CountingUnit`.

One rule is tightened as a result of looking: the trailing-parenthetical strip fires only on a
parenthetical that is **purely a year**. A parenthetical suffix is precisely how Scopus
disambiguates same-titled journals — `Sensors (Basel, Switzerland)`, `Nature (London)` — and
stripping it is the "wrong merge invents a venue that published papers it did not" failure this
decision opens by forbidding.

### 6. The stopword list is project data

A default list ships as a package data file. The loader reads a file; the module holds no
stopword literal. `test_stopwords__come_from_project_data_not_source` is only a real test if it
can fail — it scans the module source for the default list's terms, so hardcoding one reds it.

**The override is a caller-supplied path, not a project-relative one this package resolves.**
The first draft of this decision named `<project>/config/stopwords.txt` as though the module
would resolve it. It cannot, and should not: `bibliometrics/` functions receive a `Corpus` and
never a `Project`, which is the boundary Decision 9 exists to keep narrow. Pushing project-path
resolution into an analysis module to satisfy a convenience would invert it. So `keyword_*`
takes `stopwords_path`, and *where a project keeps its list* is a convention for whichever
caller resolves it — deferred until a caller exists. No document may describe
`<project>/config/stopwords.txt` as a mechanism until one does, because a user who creates that
file today would get silence rather than an override.

A missing **default** file raises; a missing explicit **override** does not. The asymmetry is
deliberate. Losing the packaged default would silently readmit `human`, `learning`, `network`
and `model` to every keyword table — a wrong frequency table with no error — whereas an override
the caller named and did not provide is the caller's own business.

### 7. Networks are seeded, and the seed is in `params`

Edge weight = the number of records in which both terms appear. Communities via
`networkx.algorithms.community.louvain_communities(seed=...)`, seed default `0`, recorded in
`params` alongside `min_occurrence`, `top_n` and `resolution`. Louvain is randomised: an
unseeded run produces a different community assignment on each call, so an unseeded clustering
is not reproducible and therefore not publishable.

Leiden is not used: it requires `igraph`, which is not a dependency, and adding one to reach a
marginally better modularity score is not worth the supply-chain surface here.

VOSviewer export writes `map.txt` and `network.txt`, tab-separated, pinned by golden.

### 8. "Bit-identical" means bytes, from two connections

`test_all_analyses__recomputed_twice__is_bit_identical` compares the **serialised** form —
`data` as CSV bytes, `params` as canonical JSON — of every analysis, computed twice from two
separately-opened `Corpus` handles. Comparing two in-process `DataFrame` objects for equality
would pass while the CSV differed in float formatting or row order, which is what a reader
actually receives.

Every ordering is total: sort keys always break ties down to a unique column, never leaving the
engine's group-by order to decide. Ordering that is stable only by coincidence is the defect
`pytest-randomly` and `-n auto` exist to surface.

### 9. `Corpus` gains `venues`, `affiliations` and `authors`

Stage 3 froze `records`, `keywords` and `citations`. Stage 7 needs venue, affiliation and
author rows for the same PRISMA set, and has two options: issue its own SQL, or extend the
handle.

Extending the handle wins because `PrismaStage` filtering is not a `WHERE` clause. For any
stage other than `RAW` it delegates to the Stage 4 PRISMA engine and folds the decision log.
Reimplementing that in six analysis modules would put the definition of "the corpus" in seven
places; a divergence between two of them would be invisible and would produce two different
values of `n` in one bundle. The three new accessors follow the existing pattern exactly:
same delegation, same total ordering, same return of a `pl.DataFrame`.

### 10. An empty corpus is a defined case, not a crash

The live corpus's `C` is **empty** — screening has not run, so `included = 0`. Every analysis
must therefore return an empty frame *with its correct schema* and a caption stating `n = 0`.
CAGR is the exception and raises, per Decision 3: there is no growth rate over no years, and
returning `0.0` would be a number a reader could quote.

## Alternatives considered

**Return bare dataframes and attach provenance at the figure layer.** Rejected: Stage 9
requires figure functions to compute nothing, so provenance assembled there would be assembled
from whatever the figure happened to receive. Provenance has to be produced where the number
is, or it is a decoration rather than a record.

**A single `run_id` on `Provenance`, as the BUILD_PLAN comment sketches.** Rejected in
Decision 1.

**Normalise venues by fuzzy match.** Rejected in Decision 5.

**Let Stage 10 keep its own venue and citation queries, and reconcile later.** Rejected: it
ships a bundle whose table and whose prose disagree, which is §1.4 stated as a plan.

## Consequences

1. **Every number in the review becomes quotable with its own caption.** Captions are
   generated, so a figure cannot drift from the number it shows.
2. **Nothing moved on the live corpus, and that is recorded rather than dropped.** `venues.total`
   769 → 769, every `citations.*` key identical, no golden changed — see Decision 5. A predicted
   effect that does not occur is a measurement, not a non-event; the ADR that predicted it is
   where the correction belongs.
3. **`Corpus` is three methods wider**, and the frozen Stage 3 contract is amended for the
   first time. Any later stage needing a fourth accessor follows this precedent rather than
   issuing its own SQL.
4. **No module in `bibliometrics/` may read the clock**, enforced by test. This is a constraint
   on future contributors, not only on this stage.
5. **CAGR raises on corpora that Stage 11 might legitimately produce** (a January retrieval
   with one complete year). That is intended: the alternative is a number derived from one
   year of data presented as a growth rate.

## Constraints

- Every public function in `bibliometrics/` returns `AnalysisResult`. Enforced by an
  introspective sweep, not by convention.
- No `datetime.now`, `date.today` or `time.time` under `bibliometrics/`.
- Every metric is tested against an **independently derived** expected value — hand-computed,
  analytically known, or a second implementation. Never against the function's own prior
  output. A golden that is the only authority for a metric is forbidden (§5 risk 11).
- Partial-final-year exclusion is derived from `runs.started_at`, never from the wall clock, and
  any frame that *shows* a partial year marks it (Decision 3b).
- No document claims a measured effect that was not measured. A predicted change that did not
  happen is recorded as such.
- The counting method (`full`/`fractional`), `min_occurrence`, `top_n`, `resolution` and the
  clustering seed appear in `params` and therefore in the caption.
- Stage 10's venue and citation numbers come from this stage's functions, not from their own
  queries.
- Ordering is total everywhere; no reliance on engine group-by order.

## Related decisions

- [ADR 0021](0021-entitlement-refusals-are-attributed-to-a-publisher.md) — the same principle
  one layer down: a number is only reported against a thing it is actually about
- [ADR 0018](0018-abstract-runs-in-layer-1.md) — Layer 1 is a function of Layer 0, which is why
  provenance is derivable rather than recorded
- [ADR 0015](0015-stage-order-and-stage-10-scope.md) — why Stage 10 exists before Stage 7, and
  therefore why Decision 5 is a re-pointing rather than a first wiring
