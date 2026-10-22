# Merge Train

How the maintainer merges a batch of open PRs in order, with no per-merge confirmation,
and how the update-behind workflow (`.github/workflows/update-behind-prs.yml`) does it.
This page covers the repository workflow only; it is not part of the research pipeline.

## Labels

| Label | Meaning |
|---|---|
| `train:N` (N = 1 to 3 digits) | Position in a batch. The lowest N among open PRs is the head. Inert until the train starts. |
| `reviewed:<20 hex>` | The reviewer's verdict on the PR's content (the id below). Required on the head before merge. |
| `train:halted` | Set by the workflow on the head when it stops. While any open PR carries it, every run does nothing. |

`gh pr edit --add-label` fails when the label does not exist. Create `train:halted` once
(`gh label create train:halted`), and create each `train:N` and `reviewed:<id>` label before
adding it (`gh label create reviewed:<id>`).

## Assumptions the owner must confirm once

1. The GitHub App is not a branch-protection bypass actor. Its token has only contents and
   pull-requests write, so it cannot approve or override required checks or conversations.
2. Label write access is owner-only. Anyone who can add `reviewed:` or `train:N` can
   approve content the workflow then merges.
3. The labels exist: `train:halted`, every `train:N` used, and every `reviewed:<id>` added.

## Batch and start

1. Open the PRs for the batch and label them `train:1`, `train:2`, ... in merge order.
   Keep the numbers consecutive. Keep them drafts while you refine them.
2. Review each PR. Compute its id and add the matching `reviewed:` label (below).
3. Mark every batch PR ready for review. A draft head halts the train.
4. Start: `gh variable set MERGE_TRAIN --body on`, then `gh workflow run "Update behind PRs"`.

Starting is the owner's standing approval to squash-merge exactly those PRs, in order.

## Stop, halt and resume

- **Stop**: `gh variable set MERGE_TRAIN --body off` (any value other than `on` is off), or
  cancel the running workflow run.
- **Halted**: the workflow labels the head `train:halted` and posts one comment with the reason.
- **Resume**: fix the cause by hand, then `gh pr edit N --remove-label train:halted` and
  `gh workflow run "Update behind PRs"`.

## What the workflow does each run

Only the train head is acted on. A PR is merged only if all of these hold:

1. It is the head: the lowest `train:N` among open PRs. No batch PR is closed unmerged below
   it (see the closed-unmerged halt).
2. It is not a draft.
3. `mergeStateStatus` is `CLEAN`, and every check is `SUCCESS`, `SKIPPED` or `NEUTRAL`, with
   none queued or running. A failing non-required check (such as `gates`) stops the train.
4. It has a `reviewed:<id>` label, and `<id>` equals the id recomputed from the PR head
   against `origin/main` at merge time.

Then it runs `gh pr merge N --squash --match-head-commit <head sha>`. The next run starts
when the push to `main` lands and handles the next PR.

| Head state | Action |
|---|---|
| `BEHIND`, no check running | Update the branch from main. The comment says the net patch is verified before merge. |
| Any state, with a check queued or running | Wait for the next trigger. A failing check halts instead. |
| All four gates pass | Merge. |
| Anything else | Halt. |

Non-train PRs are not updated while a train is on. With no `train:N` labels, or with
`MERGE_TRAIN` not `on`, the update-only rule applies: only the lowest-numbered open PR is
updated, and only when it is BEHIND with no running checks. No PR is ever merged in that mode.

## Halt reasons

| Reason | What to check |
|---|---|
| Head is a draft | Mark the batch PR ready. |
| Missing `reviewed:<id>` label | The reviewer has not recorded a verdict for this head. |
| Id mismatch | The content changed after review. Re-review, then move the label. |
| Id not computed | The PR head could not be fetched. Re-run after checking the head exists. |
| Check `<name>` is not passing | A red or cancelled check. Fix it; the train never retries a failed check. |
| `DIRTY` | The branch conflicts with main. Resolve by hand. |
| `BLOCKED` with every check green | An unresolved conversation, often a Copilot review thread. Resolve it. |
| `UNKNOWN` after about 60 s | GitHub did not compute the state. Look at the PR and re-run. |
| Closed unmerged `train:K` below the head | A batch PR was closed without merging, so the next PR would jump the queue. Re-add it or renumber, then resume. |
| Duplicate `train:N`, missing number, malformed or doubled train label | Fix the labels so the numbers are unique and consecutive. |
| List at the 100-PR limit | More than 99 open or unmerged-closed PRs: the labels cannot all be seen. |
| Squash merge failed | Check the PR, then resume. |

Check names come from the PR's own CI, so the workflow strips control characters and
markup from them and truncates them to 60 characters before they reach the log or a comment.

## How the orchestrator computes `reviewed:`

Use the same script as the workflow, on the PR head:

```bash
git fetch origin pull/<N>/head
python scripts/train_patch_id.py <head sha>   # prints 20 hex characters
gh pr edit <N> --add-label reviewed:<those 20 characters>
```

The id is the sha256 (first 20 hex characters) of the exact bytes of the PR's own changed
lines: the `+` and `-` lines of `git diff -U0 origin/main...<head sha>`, plus the file paths,
file mode lines and binary markers. Hunk headers, index lines and context are left out, so
it does not change when main moves on or when main is merged into the branch. Whitespace is
not normalised: indentation, spacing, trailing spaces, CRLF line endings and file modes all
change it. `git patch-id` was rejected because it ignores whitespace. Any change to the
PR's content changes the id, and then the label no longer matches.

## Caveats

- **Starvation**: while a train runs, non-train PRs are not updated. A head that stays
  `BEHIND` or waiting holds up the whole batch.
- **Halt is sticky**: a halted train stays halted until a person removes the label.
- **Label trust**: the id checks content, not who wrote the label. See the owner
  assumptions above.
- **Copilot threads**: a review thread can appear when a PR is marked ready. A `BLOCKED`
  head with green checks halts until the thread is resolved.
- **Lists are capped at 100**: the open list and the closed-unmerged list are each
  requested with `--limit 100`. A full list halts the train. The closed-unmerged search
  covers only recent unmerged PRs, so an old closed `train:K` beyond that window is not seen.
- **Edge cases**: a head with no net change against main has no id and halts. Rename
  detection is off, so a rename is hashed as a delete plus an add.
- **No `train:N` gaps**: a missing number halts the train, even if the batch is otherwise
  correct. Renumber the batch rather than skipping one.
