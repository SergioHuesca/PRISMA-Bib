# Amend Eligibility Criteria

How to change `criteria.yaml` after screening has begun, and how to find out what the change
costs before you commit to it.

Amending criteria is cheap by design. The automated sets `A` (year, subject area, document
type) and `L` (language) are **derived** — pure functions of `criteria.yaml` and Layer 1,
recomputed on every call, never stored (see [PRISMA Mapping](../methodology/prisma-mapping.md)).
Widen the year range and every count downstream recomputes by itself. What amendment cannot
do by itself is screen the records that a wider net just caught; that is what
`engine.replay()` tells you about.

**Nothing is ever deleted.** An amendment adds a new version of a file and, later, new
decision events. Every decision already in `decisions.jsonl` stays exactly where it is, with
the `criteria_version` under which it was made recorded on it, whether or not that criteria
version is still in force.

## Before you start: criteria history is git-only

There is no per-version archive directory in the project skeleton — no
`criteria/1.0.0.yaml`, `criteria/1.1.0.yaml`. `prisma/criteria.py` resolves a superseded
version from `criteria.yaml`'s **own git history**, using the equivalent of:

```bash
git log --follow --format=%H -- projects/<slug>/criteria.yaml   # every commit touching it
git show <hash>:projects/<slug>/criteria.yaml                   # the file at one commit
```

It walks those commits most-recent-first and returns the first one whose `criteria.yaml`
parses and declares the version you asked for.

Three consequences you must act on **before** you edit anything:

- **Commit the current `criteria.yaml` first.** If you overwrite version `1.0.0` with `1.1.0`
  and `1.0.0` was never committed, `1.0.0` is gone — `resolve_criteria` raises `ConfigError`
  naming the version rather than guessing, and every decision event stamped
  `criteria_version: "1.0.0"` now points at a criteria file nobody can reconstruct. Git is
  the audit trail; an uncommitted amendment has none.
- **Always bump `version`.** It is validated as `MAJOR.MINOR.PATCH`. If you change the
  eligibility rules without bumping it, two genuinely different criteria share one version
  string, replay picks whichever commit is more recent, and the `criteria_version` on every
  decision event stops identifying anything.
- **The project directory must be inside a git repository, with `git` on `PATH`.** Otherwise
  historical versions cannot be resolved at all. `criteria.yaml` and `project.toml` are
  tracked files; `raw/`, `store/`, `fulltext/`, and `*.duckdb` are not (§2.5).

If your `criteria.yaml` is a **symlink** into a tracked `projects-config/` mirror rather than
a file in the project directory (§2.5 allows either), resolution still works: the path is
resolved through the symlink first, so history is read from the real, tracked file. It only
fails if the symlink points outside the repository — see [Troubleshooting](#troubleshooting).

## Step 1: commit the criteria you are about to supersede

```bash
git add projects/my-review/criteria.yaml
git commit -m "docs(criteria): freeze v1.0.0 before amendment"
```

If it is already committed and unmodified, you are done with this step. Verify with
`git status projects/my-review/criteria.yaml`.

## Step 2: edit `criteria.yaml` and bump the version

```yaml
version: 1.1.0            # was 1.0.0 — always bump
temporal:
  year_start: 2014        # was 2016 — this amendment widens the window
  year_end: 2026
subject_areas: []         # must stay empty; a non-empty list is refused (see below)
doc_types:
  include: [ar, cp]
  conference_whitelist: ["Computer Vision and Pattern Recognition"]
languages: [English]
manual_abstract:
  exclude_reason_codes: [OFF_TOPIC, REVIEW_OR_SURVEY, NOT_PEER_REVIEWED]
manual_fulltext:
  exclude_reason_codes: [NO_FULL_TEXT, WRONG_POPULATION, NO_EVALUATION]
```

Four things worth knowing before you write a list:

- **An empty list means "no restriction on that dimension"**, not "match nothing". Emptying
  `languages` removes the language filter; it does not exclude everything.
- **An unknown key is refused, not ignored.** Writing `language:` for `languages:`, or a
  plausible-but-unsupported `study_designs:`, raises `ConfigError` naming the key, the block
  it appeared in, and the closest valid alternative. A silently dropped key would be an
  eligibility rule that silently did not apply.
- **An inverted window is refused.** `year_end` below `year_start` would match no record at
  all and report that automation excluded the entire corpus.
- **A record with no Layer 1 data on a dimension is never excluded on that dimension.** A
  record with a `NULL` language passes a `languages` filter. See
  [PRISMA Mapping — filter conventions](../methodology/prisma-mapping.md#four-filter-conventions-that-change-published-numbers)
  for the full set of rules, all of which move published numbers.

`Project.criteria` re-reads the file on every access, so the edit takes effect immediately —
there is nothing to reload or migrate.

## Step 3: preview the amendment with `engine.replay()`

```python
from prismabib.project import Project
from prismabib.prisma import engine

project = Project.open("my-review")
result = engine.replay(project, criteria_version="1.1.0")

print(result.criteria_version)
print(f"A = {len(result.automated)}   L = {len(result.language)}")
print(f"still valid:        {len(result.decisions_still_valid)}")
print(f"needs screening:    {len(result.newly_requires_screening)}")
print(f"no longer in scope: {len(result.no_longer_in_scope)}")
```

`replay()` reads and reports. It **writes nothing** — no event is appended, deleted, or
rewritten, and `criteria.yaml` is not touched. Run it as often as you like, including on an
amendment you have not committed yet: when the version you ask for is the one currently in
`criteria.yaml`, it is read straight from the working file with no git lookup. That is what
makes "what would this cost?" a question you can ask before deciding.

To ask the same question about a *superseded* version, name it — `criteria_version="1.0.0"` —
and it is resolved from git history, which is why Step 1 exists.

## Step 4: read the `ReplayResult`

| Field | What it contains | What you do about it |
| --- | --- | --- |
| `criteria_version` | The version actually resolved | Confirm it is the one you meant |
| `automated` | `A` recomputed under this criteria | Sanity-check the size against the previous run |
| `language` | `L` recomputed under this criteria | This is the screening universe under the amendment |
| `decisions_still_valid` | Records with an existing `title_abstract` decision that are still inside `L` | **Nothing.** These decisions carry over untouched; do not re-screen them |
| `newly_requires_screening` | Records inside `L` with no `title_abstract` decision yet | **Screen these.** They are the amendment's labour cost |
| `no_longer_in_scope` | Records with an existing `title_abstract` decision that now fall outside `L` | **Nothing.** Their events stay in the log; they simply no longer determine membership |

`newly_requires_screening` is the number to look at before deciding whether an amendment is
affordable. It contains both records that a widened filter has newly admitted and records
that were always in scope but never screened — the engine cannot tell those apart, and for
the purpose of "how much screening is left", it does not matter.

`no_longer_in_scope` is not a deletion and not an exclusion. If you later revert the
amendment, those records return to `L` and their original decisions apply again, unchanged,
because they were never anything but events in an append-only file.

### The one thing `replay()` does not tell you

!!! warning "replay() reports on title/abstract decisions only"

    `ReplayResult`'s three decision fields are computed against `PrismaStage.TITLE_ABSTRACT`
    events **only** (`engine.py`, in `replay()`'s fold: `if stage is PrismaStage.TITLE_ABSTRACT`).
    Full-text decisions are not considered at all.

    So for a project already into full-text screening:

    - `decisions_still_valid` is **not** "all my screening work that survives". It is the
      title/abstract subset of it. Do not report it, or reason about it, as though it covered
      the whole review.
    - A record whose *full-text* decision an amendment invalidates will not appear in
      `no_longer_in_scope`, and a record newly needing *full-text* screening will not appear
      in `newly_requires_screening`.

    Nothing is lost — every full-text event remains in the log, and
    `manual_fulltext_set()`/`corpus()` continue to fold them correctly under the amended
    criteria. What is missing is the *report*. Until that gap is closed, compare
    `compute_flow_counts()` before and after the amendment (Step 7) to see the full-text
    stages move, and treat `replay()` as a title/abstract planning tool.

## Step 5: commit the amendment

```bash
git add projects/my-review/criteria.yaml
git commit -m "feat(criteria): widen year window to 2014 (v1.1.0)"
```

The commit message is where the *reason* for the amendment lives — a protocol amendment in a
systematic review needs a justification, and this is the only place the system asks you for
one. Write it for the reader of the eventual manuscript, not for yourself.

## Step 6: screen what the amendment newly admitted

Screen the records in `newly_requires_screening`. Every new event records the criteria
version in force at the time automatically:

```python
from prismabib.prisma.log import DecisionLog
from prismabib.stage import PrismaStage

log = DecisionLog(project)
log.append(
    stage=PrismaStage.TITLE_ABSTRACT,
    record_id="scopus:2-s2.0-85101234567",
    reviewer="kp",
    decision="exclude",
    reason_code="REVIEW_OR_SURVEY",  # must be declared in criteria.yaml for this stage
)
```

`criteria_version` defaults to the project's current `criteria.version` — pass it explicitly
only when backfilling a decision that was genuinely made under a superseded version. This is
what later makes "which decisions were taken under 1.0.0?" a query over the log rather than
an act of memory.

## Step 7: regenerate the flow counts and republish

```python
from prismabib.prisma.flow import compute_flow_counts

counts = compute_flow_counts(project)
counts.assert_consistent()
print(counts)
```

Every number in the PRISMA flow diagram moves when criteria change, which is the point: the
diagram is derived, not typed. Any manuscript figure or table produced under the old criteria
is now stale and must be regenerated, and the criteria version it was produced under must be
cited alongside it.

If `assert_consistent()` raises on equation 1 (`identified - duplicates_across_searches -
removed_other_reasons - excluded_automated == after_automated`), do not adjust a number to
make it close — see [PRISMA Mapping — the four consistency
equations](../methodology/prisma-mapping.md#the-four-consistency-equations) for why that
equation is a genuine cross-check against Layer 0 rather than an identity, and [when equation
1 does not close](../methodology/prisma-mapping.md#when-equation-1-does-not-close) for the
causes to work through. Amending criteria is not one of them: `identified` and the two
removal counts are read from Layer 1 and do not depend on `criteria.yaml` at all, so an
amendment cannot break equation 1 and cannot fix it either.

## Common cases

**"I want to narrow the window to 2020–2022."** Edit `temporal`, bump the version, replay.
Records outside the new window appear in `no_longer_in_scope`; their decisions stay in the
log as history. `newly_requires_screening` is usually empty for a narrowing amendment.

**"I should also exclude book chapters."** Remove `ch` from — or rather, confirm it is absent
from — `doc_types.include`. Remember that an *empty* `include` list means "no document-type
restriction at all", so narrowing by emptying the list does the opposite of what you want.

**"Our subject area is broader than I thought."** You cannot express that here. Scopus
`view=COMPLETE` responses carry no subject-area codes, so no record in a captured corpus has
any, and a non-empty `subject_areas` would be a filter that matched everything while
appearing in your diagram as a restriction. It is therefore **refused**: the engine raises
`ConfigError` naming the codes and the corpus. Keep `subject_areas: []` and apply the
restriction at title/abstract screening, where it becomes a logged decision with a reason
code. See [Limitations](../methodology/limitations.md#subject_areas-is-declared-but-not-enforceable).

**"I want to see what the corpus looked like under 1.0.0."** Replay against the old version:
`engine.replay(project, criteria_version="1.0.0")`. Nothing is mutated, and you can compare
its `automated`/`language` sizes against the current ones.

## Troubleshooting

| `ConfigError` says | Cause | Fix |
| --- | --- | --- |
| "…is not inside a git repository" | The project directory is not under git | Put the project (or at least `criteria.yaml`) in a git repository; history is the only version store |
| "…has no git history under…" | `criteria.yaml` exists but was never committed | Commit it. Only committed versions are replayable |
| "…was not found anywhere in …'s git history" | No commit declares that version | Check the version string; check you bumped `version` when you amended. `git log -p -- projects/<slug>/criteria.yaml` shows every version the file has held |
| "does not appear to live inside the git repository rooted at…" | `criteria.yaml` (or the file its symlink resolves to) is outside the repository the project directory belongs to | Move the tracked criteria file, or the mirror it points at, inside that repository |
| "git is not available on PATH" | No `git` executable | Install git, or run from an environment that has it |
| "does not satisfy the criteria.yaml schema" | The edited YAML is invalid (e.g. a non-semantic `version`) | Fix the file; the message names the failing field |
| "contains N key(s) prismabib does not understand" | A misspelled or unsupported key | The message names each one, the block it is in, and the closest valid key. If prismabib cannot express the criterion, apply it at screening as a logged decision |
| "temporal.year_end … precedes temporal.year_start" | The window is inverted | Check whether the two values are transposed |
| "restricts subject_areas to … but not one of the N records … carries subject-area data" | `subject_areas` is non-empty on a Scopus Search API corpus | Set `subject_areas: []`; see [Limitations](../methodology/limitations.md#subject_areas-is-declared-but-not-enforceable) |

A `StoreError` instead means Layer 1 has not been built yet for this project — replay reads
the store to recompute `A` and `L`. A `LogError` means `decisions.jsonl` failed its checksum
or parse check; resolve that before amending anything, because it indicates the decision log
itself is not trustworthy.

## Related pages

- [PRISMA Mapping](../methodology/prisma-mapping.md) — the set definitions, the filter
  conventions, and the flow-count audit table
- [ADR 0002: Append-only decision log](../architecture/adr/0002-append-only-decision-log.md) —
  why amendment is additive and prior decisions are retained
- [ADR 0008: Multi-reviewer adjudication](../architecture/adr/0008-multi-reviewer-adjudication.md) —
  how several reviewers' decisions on a re-screened record combine
- [Architecture Overview](../architecture/overview.md) — where criteria sit in the four layers
