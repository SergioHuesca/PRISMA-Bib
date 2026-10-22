# Merge Train

How the maintainer merges a batch of open PRs in order, with no per-merge confirmation,
and how the update-behind workflow (`.github/workflows/update-behind-prs.yml`) does it.
This page covers the repository workflow only; it is not part of the research pipeline.

## Labels

| Label | Meaning |
|---|---|
| `train:N` (N = 1 to 3 digits) | Position in a batch. The lowest N among open PRs is the head. Inert until the train starts. |
| `reviewed:<12 hex>` | The reviewer's verdict on the PR's net patch id (below). Required on the head before merge. |
| `train:halted` | Set by the workflow on the head when it stops. While any open PR carries it, every run does nothing. |

`gh pr edit --add-label` fails when the label does not exist. Create `train:halted` once
(`gh label create train:halted`), and create each `train:N` and `reviewed:<id>` label before
adding it (`gh label create reviewed:<id>`).

## Batch and start

1. Open the PRs for the batch and label them `train:1`, `train:2`, ... in merge order.
   Keep the numbers consecutive. Keep them drafts while you refine them.
2. Review each PR. Compute its net patch id and add the matching `reviewed:` label (below).
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

1. It is the head: the lowest `train:N` among open PRs.
2. It is not a draft.
3. `mergeStateStatus` is `CLEAN`, and every check is `SUCCESS`, `SKIPPED` or `NEUTRAL`, with
   none queued or running. A failing non-required check (such as `gates`) stops the train.
4. It has a `reviewed:<id>` label, and `<id>` equals the net patch id recomputed from the
   PR head against `origin/main` at merge time.

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
| Patch id mismatch | The content changed after review. Re-review, then move the label. |
| Net patch id not computed | The PR head could not be fetched. Re-run after checking the head exists. |
| Check `<name>` is not passing | A red or cancelled check. Fix it; the train never retries a failed check. |
| `DIRTY` | The branch conflicts with main. Resolve by hand. |
| `BLOCKED` with every check green | An unresolved conversation, often a Copilot review thread. Resolve it. |
| `UNKNOWN` after about 60 s | GitHub did not compute the state. Look at the PR and re-run. |
| Duplicate `train:N`, missing number, malformed or doubled train label | Fix the labels so the numbers are unique and consecutive. |
| Squash merge failed | Check the PR, then resume. |

## How the orchestrator computes `reviewed:`

Use the same script as the workflow, on the PR head:

```bash
git fetch origin pull/<N>/head
python scripts/train_patch_id.py <head sha>   # prints 12 hex characters
gh pr edit <N> --add-label reviewed:<those 12 characters>
```

The id is `git patch-id --stable` of `git diff -U0 origin/main...<head sha>`, with rename
detection and the diff algorithm pinned. It covers only the PR's own changes, so main moving
on, or the workflow merging main into the branch, does not change it. A new commit that
changes the PR's content does change it, and the label then no longer matches.

## Caveats

- **Starvation**: while a train runs, non-train PRs are not updated. A head that stays
  `BEHIND` or waiting holds up the whole batch.
- **Halt is sticky**: a halted train stays halted until a person removes the label.
- **Label trust**: `reviewed:` and `train:N` are trusted as written by the owner. Anyone with
  write access can add them. The workflow checks the patch id, not who added the label.
- **Copilot threads**: a review thread can appear when a PR is marked ready. A `BLOCKED`
  head with green checks halts until the thread is resolved.
- **Patch id edge cases**: a head with no net change against main has no patch id and
  halts. Rename detection is off, so a rename is hashed as a delete plus an add.
- **No `train:N` gaps**: a missing number halts the train, even if the batch is otherwise
  correct. Renumber the batch rather than skipping one.
