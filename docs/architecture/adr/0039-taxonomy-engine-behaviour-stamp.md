# ADR 0039: Taxonomy Engine Behaviour Is Stamped, and the Audit Seed Folds It In

## Status

Accepted — 2026-10-30, on the merge of the pull request that adds this record, which the owner approves. Until the
merge, this record was `Proposed` on the branch `docs/adr-0039-taxonomy-engine-behaviour`. Accepting it changes no code
and no test: it fixes the design that two implementation steps follow (Decision 4). Step 1 is the implementation PR
(the constant at value 1, the seed token, the caption stamp and the new fields). Step 2 is PR #227 (draft, branch
`fix/taxonomy-rules-case-insensitive`), rebased onto step 1, which makes rule patterns match case-insensitively by
default and sets the constant to 2. The owner's answers to the nine open questions are in "Questions put to the owner,
and the answers".

ADRs 0001 to 0038 are frozen and this record does not edit any of them. The only edits to older records are two dated
notes appended to ADR 0005 and ADR 0023 (2026-10-30). They record the case-insensitive decision and point here. ADR 0023
Decision 5's seed input list is extended by Decision 2 below; the note in ADR 0023 says so, and its text is not edited.
BUILD_PLAN.md is frozen and is not present in this worktree or at the main checkout root, so it is cited only as ADR
0005 and ADR 0023 quote it.

## Context

**The gap.** A rule file's `version` is author-declared. `RuleFile` (`src/prismabib/taxonomy/rules.py`) checks only that
it is shaped like a semantic version, and nothing ties it to what the engine does. On `main`, `_compile_pattern`
(`rules.py`, around line 272) compiles every field pattern with no flags. PR #227 changes that to `re.IGNORECASE`, with
`(?-i:...)` as the per-pattern opt-out. After it lands, a rule file that is byte-identical and carries the same
`version` produces different assignments on the two engines.

**Why the seed does not show it.** `audit_sample_seed` (`src/prismabib/taxonomy/review.py`) digests the project slug,
the dimension id, the rule `version` and the sorted record ids (ADR 0023 Decision 5). Nothing about the engine enters
the digest. An audit sample drawn under the old engine and under the new one therefore carries the same seed and the
same label, but the population it is drawn from differs. The seed reports reproducibility, and the agreement rate beside
it measures a different engine's output.

**Why no stored artefact can show it either.**

- Assignments are not stored. `code(corpus, rule_files, overrides)` recomputes them on every call (`coder.py`, `code`;
  ADR 0023 Decision 1).
- The superseded rule rows that an agreement rate compares a human verdict against are kept on the in-memory
  `CodingResult` only (`superseded_rule_assignments`). The next `code()` call recomputes them under whichever engine runs
  it. So an agreement rate computed after an engine change compares past overrides against the new engine's rules.
- Only human overrides are stored. They are verdicts on `(record_id, dimension)` and do not depend on the engine (ADR
  0023 Decision 3).

**What reads it today.** Taxonomy is not in the export yet. `report/export.py` does not run the coder, `numbers_map` has
no taxonomy key, and the key-set golden (`tests/golden/report/__snapshots__/numbers_keys.json`) has 43 keys, none of them
taxonomy. The defect is latent for `numbers.json`. It is not latent for the taxonomy figures:
`taxonomy_distribution_figure` and `taxonomy_evolution_figure` (`viz/figures.py`) caption through `_caption(result)`,
which renders `params`. Stage 11's reproducibility goal reaches taxonomy as soon as a taxonomy figure or the coverage
report is exported.

## Decision

### 1. The engine-behaviour constant

`TAXONOMY_ENGINE_BEHAVIOUR: Final[int] = 2`, defined in `src/prismabib/taxonomy/__init__.py` and listed in that module's
`__all__`. Its values:

- **`1`: case-sensitive.** Patterns compile with no flags. This is `main` at the time of this ADR, and the behaviour behind
  every assignment and every seed produced before it.
- **`2`: case-insensitive.** Every field pattern compiles with `re.IGNORECASE`. `(?-i:...)` inside a pattern opts that
  part out. Introduced with PR #227.

Step 1 of Decision 4 lands the constant at `1`. Step 2 sets it to `2`.

**Bump rule.** The constant is bumped by any change that can change what `code()` returns for an unchanged rule file,
corpus and override log. That covers the compile flags (`rules._compile_pattern`), the field text `coder._field_texts`
builds, the matching in `coder._category_matches`, and any normalisation added later. It is not bumped for changes that
cannot change assignments. Changing the seed formula is not a constant bump: it is a change to Decision 2 and needs its
own ADR.

**Home.** The four modules the constant describes (`coder`, `rules`, `review`, `analysis`) all live in the package, and
the package `__init__` imports nothing, so there is no import cycle. `rules.py` alone was rejected: the field-text build
and the matching live in `coder.py`, so a constant there would describe half the engine.

**Type.** An integer, not the package version. `package_version` comes from git tags through hatch-vcs
(`report/export.py`, `_package_version`) and changes on releases that do not touch matching. The integer changes exactly
when assignments can change, and it can be folded into a digest.

**Not on the rows.** The constant is not added to `Assignment` or to the `CodingResult.as_dataframe()` columns. That column
list is the shape ADR 0005 names (`record_id, dimension, category, rule_id, rule_version, confidence, source`), and
changing it is a schema change. PR #227 declined a stamp for the same reason. The stamp describes the run, not each row.

### 2. Folded into the audit-sample seed

`audit_sample_seed` gains a keyword-only parameter `engine_behaviour: int` with **no default**. A default would let a
caller seed silently with the current engine's value. The digest is the ADR 0023 Decision 5 digest with one optional
token inserted after the rule `version`:

```
sha256( slug US dimension US rule_version [ US "engine-behaviour=N" when N != 1 ] US sorted(record_ids) )
```

`US` is the existing `\x1f` separator. **The token is omitted when `engine_behaviour == 1`.** Consequences:

- Every seed recorded before this ADR was computed with engine 1. Omitting the token for 1 keeps those seeds
  byte-identical, so `audit_sample_seed(..., engine_behaviour=1)` reproduces any pre-ADR designated sample and any rate
  quoted from it. ADR 0023 Decision 5 requires exactly that.
- The first step of the sequence (Decision 4) is then a no-op for every project's seeds.
- The token contains no colon. Record ids carry a `source:` prefix (ADR 0005's example, `scopus:2-s2.0-85101234567`), so
  the token cannot equal a record id, and it sits in a fixed position before the variable record list. The digest stays
  unambiguous.
- This departs from the wording "add the constant to the seed" in one respect: the constant is folded in for every
  non-baseline engine, not for the baseline. The owner confirmed this (Question 1). The alternative is in "Alternatives
  considered".

`build_review_queue` passes `TAXONOMY_ENGINE_BEHAVIOUR`. Nothing else in the seed changes, and the record list is still the
stage's record ids, not the audit pool.

**Existing audit samples and recorded overrides.** On a project moving from engine 1 to engine 2 the seed changes for every
dimension, so the designated sample re-draws. Overrides are not touched. They are verdicts on `(record_id, dimension)` and
stay in force as overrides. A reviewed record that falls out of the new designated set still stands in the coding; it
stops counting toward the agreement rate. A newly designated record appears as outstanding audit work. The rate is taken
over the overlap, so it may become `None`, or move, with no new review.

### 3. Provenance: where the stamp is recorded

| Place | Change | Why |
| --- | --- | --- |
| `AnalysisResult.params` of `dimension_distribution` and `dimension_evolution` (`taxonomy/analysis.py`) | adds `taxonomy_engine_behaviour` | ADR 0022 Decision 3 puts every knob that affects data in `params`, and therefore in the caption. The taxonomy figures caption from `params`. The caption renders `taxonomy_engine_behaviour=2` (key=value, as the other params render; Question 7). |
| `ReviewQueue` (`taxonomy/review.py`) | adds a required field `taxonomy_engine_behaviour: int` | The queue carries `audit_seeds`. The stamp sits beside the seed a reader regenerates from. |
| `CoverageReport` (`taxonomy/review.py`) | adds required fields `taxonomy_engine_behaviour: int` and `audit_seeds: Mapping[str, int]` | The report carries `audit_agreement_rate`, the number a methods section quotes. Adding `audit_seeds` lets that rate be regenerated from the report alone (Question 8). |
| Not changed: `CodingResult`, `Distribution` and its `caption()`, `Assignment` and `as_dataframe()`, the override log, `DimensionCoverage` | none | `code()` results are never persisted (ADR 0023 Decision 1), so the process holding a `CodingResult` is the run that made it. `Distribution.caption()` is pinned by three golden tests and stays unchanged (Question 7). The `Assignment` columns are frozen. |

The fields are required, with no default, so that no result can be built carrying a stamp it did not compute.

**numbers.json: no key in this ADR's implementation.**

- The export does not run the coder, and no taxonomy key exists. A key that stamps a taxonomy engine into a bundle with no
  taxonomy numbers would assert something about numbers the bundle does not contain.
- A new key outside the `methods.` prefix would be **required**. `fill` raises `FillError` for any key a manuscript never
  cites (`report/fill.py`; the unused-key check exempts only `methods.*`). Every existing manuscript would fail
  `prismabib fill` until it cited the key. That is a breaking change to every template. This ADR does not make it.
- **Owner decision (Question 6): both an optional `methods.*` snippet and a manifest key, when the Stage 11 taxonomy export
  is built.** An optional `methods.*` snippet (for example `methods.taxonomy_engine_sentence`) is exempt from the
  unused-key check, so it breaks no template. This is future work, planned for that export. It is not part of this ADR's
  implementation.

**manifest.json: no key in this ADR's implementation.** The manifest records the code (`git_commit`, `package_version`) and
the export's inputs. Taxonomy is not exported. The export test asserts manifest keys as a subset
(`tests/integration/report/test_export.py`), so a key can be added later without breaking it. The manifest key is planned
with the Stage 11 taxonomy export, with the `methods.*` snippet above (Question 6).

### 4. Sequence and upgrade

Two steps, in this order. The owner approved the split (Question 2).

1. **Step 1, the implementation PR: constant at 1, no coding change.** Adds `TAXONOMY_ENGINE_BEHAVIOUR = 1`, the seed token
   (omitted for 1), the `params` entries, and the fields in Decision 3, including `audit_seeds` on `CoverageReport`
   (Question 8). No seed changes and no coverage change. The observable changes are the new caption clause
   `taxonomy_engine_behaviour=1` on taxonomy figures and the new report fields.
2. **Step 2, PR #227 with value 2.** PR #227 is rebased onto step 1 and sets the constant to 2 in the same PR as its
   matching change. #227 must not reach `main` without the constant, because that would be exactly the unstamped change
   this ADR exists to prevent.

**Existing projects, after step 2.**

- No migration. No file is rewritten. Rule files, `dimensions.yaml`, the override log and its `.sha256` sidecar are
  unchanged, and the tool deletes nothing.
- Coding output changes for rules whose matches depend on case. Coverage rises, and `conflicting` entries appear in
  `multi_label: false` dimensions.
- **A `papers` distribution over a `multi_label: false` dimension raises `ValidationError`** (ADR 0023 Decision 4) when a
  record now matches two categories. That is the sum-to-|C| guard working: loud rather than silent. It is also the first
  thing an upgraded project will see. The remedy is to narrow the rule or to append an override.
- Audit samples re-draw and agreement rates are recomputed (Decision 2).
- Pre-ADR seeds stay reproducible: `audit_sample_seed(..., engine_behaviour=1)` returns them.
- **Rule re-versioning (Question 3).** In the upgrade commit, each affected project bumps the `version` of each rule file
  whose output changed, so the git history explains the change. The code does not enforce it. The how-to guidance
  (`docs/how-to/write-taxonomy-rules.md`) and the CHANGELOG entry say so.

### 5. Goldens and templates

**Existing goldens that re-baseline under this ADR: none.**

- `tests/golden/report/__snapshots__/numbers_keys.json` and `reference_numbers.json`: no taxonomy key, and none is added.
- `tests/golden/taxonomy/test_coder.py` (three `Distribution.caption()` tests): `Distribution.caption()` is unchanged.
- `tests/golden/viz/__snapshots__/captions.txt`: no taxonomy line.

**Existing non-golden tests that must change.**

- `tests/unit/taxonomy/test_review.py`: the `audit_sample_seed` calls (about lines 208, 250 and 256) gain
  `engine_behaviour=`, and the `ReviewQueue(...)` construction (about line 350) gains the stamp.
- `tests/unit/taxonomy/test_analysis.py` and the taxonomy and viz tests: caption assertions are substrings and still hold.
  A search for `.params` in the taxonomy, viz and golden tests found no comparison by equality. Figure rendering tests
  are checked for existence only and should be re-run at implementation.

**New goldens.**

- `tests/golden/taxonomy/test_engine_behaviour.py`: pins the engine's default matching policy on a fixed probe, through
  `code()`. A case-variant probe matches by default, and the `(?-i:...)` opt-out does not match it. This is an accepted
  exception to ADR 0023 Decision 7, which forbids tests that a regex matches a string (Question 5).
- `tests/golden/taxonomy/__snapshots__/audit_seed.json`: the seed of one fixed fixture at engine 1 and at engine 2. It pins
  the value across machines, which is the Stage 11 check for this class of defect.

**PR #227 itself.** Its changed-file list was not visible when this ADR was drafted. Its description reports 106 taxonomy
tests passing and mentions no golden change. That suggests none moves, but it is unverified and must be read off the diff
when the PR is reviewed.

**Templates.** No template changes, because no `numbers.json` key is added.

**Docs (in the implementation PR).**

- `docs/how-to/write-taxonomy-rules.md`: the seed inputs (Step 6, about lines 207-213), the sample-stability paragraph
  (about lines 231-233), and the "the audit sample re-draws only if you bumped version" line (about lines 265-267), which
  now also depends on the engine. Add the upgrade guidance: when the engine changes, bump the affected rule files'
  `version` in the upgrade commit (Question 3), and read the stamp on the report.
- `CHANGELOG.md` `[Unreleased]`: the case-insensitive default, the upgrade effects in Decision 4, the papers-guard note,
  and the re-versioning instruction.
- `mkdocs.yml`: the ADR 0039 nav entry after ADR 0038 (about line 125). This is done on this branch.

### 6. ADR 0005 and ADR 0023

- **ADR 0005** gets a dated note. It records the case-insensitive default, and that ADR 0005 is silent on case, so this
  fills a gap and reverses nothing. It also records that the engine is now a third input, stamped by this ADR.
- **ADR 0023** gets a dated note. It records the default, the extended seed input (Decision 5, as Decision 2 here), the
  implicit engine input to `code()` (Decision 1, still no stored table), and the extension of Consequence 1's bump cost to
  engine bumps.

## Alternatives considered

- **Always fold the token, including for engine 1.** Simpler to state. Rejected: it changes every pre-ADR seed at step 1,
  so pre-ADR rates stop being reproducible from the new code. The upgrade re-draws the samples anyway, so the simplicity
  buys nothing.
- **A default value (the current engine) on the seed, the queue and the report.** Rejected: silent stamping. The defect
  this ADR fixes is a result that looks current when it is not.
- **Stamp each `Assignment` row, or add a column to `as_dataframe()`.** Rejected: changes the ADR 0005 column shape, and
  rows do not need it.
- **The package version as the stamp.** Rejected: it changes without a behaviour change, and it does not encode behaviour.
- **Rule files declare the engine they were written for, and loading refuses a mismatch.** The strongest option, since an
  engine-dependent rule is then visible in the file itself. Rejected for now: it is a required key in every rule file, so
  every existing rule file breaks at load. The owner answered not now (Question 4).
- **Store the engine with each override.** Rejected: overrides are human verdicts that do not depend on the engine (ADR
  0023 Decision 3), and the rate is recomputed under the current engine regardless, so a stored value would not explain it.
- **A required `numbers.json` key.** Rejected for this implementation (Decision 3): it breaks every `fill` template and
  asserts taxonomy facts that the bundle does not contain. The optional `methods.*` route is future work (Question 6).
- **Case sensitivity as a per-rule-file setting.** Rejected: the per-pattern `(?-i:...)` opt-out already gives authors the
  control, and the default is the owner's decision.
- **The constant in `rules.py`.** Rejected (Decision 1).

## Consequences

- **Positive.** Every taxonomy figure caption, review queue and coverage report states which engine produced it. The seed
  changes when the audited behaviour changes, as ADR 0023 Decision 5 intends. Pre-ADR seeds remain reproducible. There is
  no storage change and no template break. A coverage report now carries the seeds beside its agreement rate.
- **Negative.** Upgrading re-draws every project's audit sample and can change agreement rates without a review. Upgraded
  projects can hit the papers guard. The constant is a manual bump: nothing forces it when matching changes. The
  engine-behaviour golden covers only its probe, so a change outside the probe does not fail it. The reviewer must catch a
  matching change the golden does not probe; the bump rule in Decision 1 is the check, and the golden is extended whenever
  a matching rule is added.
- The coverage report lacked the audit seed before this ADR, so a rate quoted from the report alone could not be
  regenerated. Step 1 adds `audit_seeds` to the report (Question 8).

## Questions put to the owner, and the answers

1. **Fold rule (Decision 2).** Omit the seed token for engine 1, which departs from "add the constant to the seed" as
   worded. **Answer: confirmed.** The token is folded in only for a non-baseline engine, so every pre-ADR seed stays
   byte-identical.
2. **Split PR #227.** The sequence needs step 1 (constant at 1, no coding change) to land before #227. **Answer: approved.**
   Step 1 is the implementation PR (constant at 1, seed token, caption stamp, the new fields including `audit_seeds`).
   Step 2 is PR #227, rebased onto step 1, which sets the constant to 2.
3. **Rule re-versioning.** Should the upgrade commit bump each affected rule file's `version`? **Answer: yes.** Each
   affected project bumps the `version` of each affected rule file in the upgrade commit. The code does not enforce it.
   The how-to guidance and the CHANGELOG entry say so.
4. **Rule-declared engine.** Should a rule file declare the engine behaviour it was written for and fail at load on a
   mismatch? **Answer: not now.** There is no rule-declared engine and no load-time check on it.
5. **Exception to ADR 0023 Decision 7.** That Decision forbids tests asserting that a regex matches a string, because such
   tests exercise the `re` module. The engine-behaviour golden asserts the engine's default flag policy on a fixed probe,
   not `re`. **Answer: accepted** as a stated exception, for `tests/golden/taxonomy/test_engine_behaviour.py`.
6. **Stage 11 export.** When taxonomy numbers enter the export, should it add an optional `methods.*` snippet, a manifest
   key, both, or neither? **Answer: both.** An optional `methods.*` snippet and a manifest key, planned for the Stage 11
   taxonomy export. This is future work and is not part of this ADR's implementation.
7. **Caption wording.** `taxonomy_engine_behaviour=2` (key=value, as the other params render) or prose? And should
   `Distribution.caption()` carry the stamp too? **Answer: key=value.** `Distribution.caption()` stays unchanged.
8. **Coverage report seeds.** Add `audit_seeds` to `CoverageReport`, so a quoted rate can be regenerated from the report
   alone? **Answer: yes,** in the implementation PR (step 1).
9. **No quoted rates.** Confirm that no audit agreement rate has been quoted outside this repository. **Answer: confirmed
   by the owner:** no audit agreement rate has been quoted anywhere outside the repository. The upgrade re-draw therefore
   changes no published number.

## Verification (for the implementation PR)

- New unit tests, each shown to fail on the defect it guards:
  - `test_audit_sample_seed__baseline__matches_pre_adr_digest`: a pinned digest computed from the pre-change formula before
    step 1 lands. Fails if the token is added for engine 1.
  - `test_audit_sample_seed__engine_bump__changes_seed`: same inputs at engine 1 and engine 2, different seed. Fails if the
    token is dropped.
  - `test_review_queue__stamps_engine_behaviour`, `test_coverage_report__stamps_engine_behaviour`, and
    `test_coverage_report__carries_audit_seeds` (the seeds in the report equal `queue.audit_seeds`).
  - `test_dimension_distribution__params__carry_engine_behaviour`, and the same for `dimension_evolution`: the caption
    contains `taxonomy_engine_behaviour=`.
  - `tests/golden/taxonomy/test_engine_behaviour.py` and `audit_seed.json` (Decision 5).
- Run `pytest` the way CI does (`-n auto`) and single-process. On the local machine use `-n 4`, per the project's memory
  note. The audit-seed golden is the cross-machine check: a clean clone on another machine must reproduce it.
- `mypy --strict` on `src/prismabib`, Python 3.11.
- The digest input is UTF-8 with `sha256`. No `hash()`, no clock, no unseeded `random`.

## Records touched by this ADR

- New: this file.
- Appended, dated 2026-10-30, with no older text edited: `docs/architecture/adr/0005-rules-plus-override-taxonomy.md` and
  `docs/architecture/adr/0023-taxonomy-assignments-are-derived-and-overrides-replace-a-dimension.md`.
- Edited on this branch: `mkdocs.yml`, one nav entry after ADR 0038. No other ADR index exists; the nav is the only list of
  ADRs.
- Not touched by this record: code, tests, goldens and the remaining docs. The implementation PR owns them (Decision 5).

## Related decisions

- [ADR 0005](0005-rules-plus-override-taxonomy.md): versioned rules and the override log.
- [ADR 0023](0023-taxonomy-assignments-are-derived-and-overrides-replace-a-dimension.md): derived assignments (Decision 1),
  the seed (Decision 5), the papers guard (Decision 4).
- [ADR 0022](0022-the-analysis-result-contract-and-its-provenance.md): every knob that affects data is in `params`, and so
  in the caption (Decision 3).
- [ADR 0025](0025-figures-render-what-the-engines-computed.md): figures render what the engines computed, through the caption
  path used here.
- [ADR 0018](0018-abstract-runs-in-layer-1.md): Layer 1 is a function of Layer 0, which is why no table stores assignments.
- [ADR 0038](0038-refresh-semantics-of-the-http-cache.md): the precedent for dated notes appended to a frozen record, and for
  the Status wording used here.
