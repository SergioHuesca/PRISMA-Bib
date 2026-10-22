# Merge Train

How the maintainer merges a batch of open PRs in order, with no per-merge confirmation,
and how the update-behind workflow (`.github/workflows/update-behind-prs.yml`) does it.
This page covers the repository workflow only; it is not part of the research pipeline.

## Labels

| Label | Meaning |
|---|---|
| `train:N` (N = 1 to 3 digits) | Position in a batch. The lowest N among open PRs is the head. Inert until the train starts. |
| `reviewed:<40 hex>` | The reviewed head commit: the exact commit whose content the reviewer approved. Exactly one per batch PR. |
| `train:halted` | Set by the workflow on the head when it stops. While any open PR carries it, every run does nothing. |

## Owner confirmations (once)

1. The GitHub App is not a branch-protection bypass actor. Its token has only contents and
   pull-requests write, so it cannot approve or override required checks or conversations.
2. Label write access is owner-only. Anyone who can add `reviewed:` or `train:N` can
   approve content the workflow then merges.
3. The labels exist before use: `train:halted`, every `train:N` used, and every
   `reviewed:<40 hex>` added. `gh pr edit --add-label` fails for a missing label.
   Create them with `gh label create`.

## Batch and start

1. Open the PRs for the batch and label them `train:1`, `train:2`, ... in merge order.
   Keep the numbers consecutive. Keep them drafts while you refine them.
2. Review each PR at its exact head commit. After the final verdict at that commit, record it:
   `gh pr edit N --add-label "reviewed:$(git rev-parse <head>)"`.
3. Mark every batch PR ready for review. A draft head halts the train.
4. Start: `gh variable set MERGE_TRAIN --body on`, then `gh workflow run "Update behind PRs"`.

Starting is the owner's standing approval to squash-merge exactly those PRs, in order.

## The gate: exact commit, plus verified update merges only

The head passes only if it is the reviewed commit R, or R plus merge commits that bring in
main and nothing else. `scripts/train_verify_head.py <R> <head>` decides this. It requires:

1. R is an ancestor of the head and lies on the head's first-parent chain (or R is the head).
2. Every commit from R to the head on the first-parent chain is a merge with exactly two
   parents. A one-parent commit (an unreviewed change) or an octopus merge halts.
3. The second parent of each merge is an ancestor of `origin/main`. A merge of any other
   branch halts.
4. The merge adds only main's own change: `git diff` of the first parent against the merge
   is byte-identical to `git diff` of the merge base against the second parent, computed
   with `--binary --full-index`. Any extra byte in the merge halts.
5. If the PR and main edited the same file, the update is not main's change alone. It halts
   with "update overlaps the PR's own edits: needs a fresh review". Relabel after review.

Git runs with global and system config disabled and diff options pinned, so a machine's git
settings do not change the result. Any git failure, or a fetch failure, halts the head.

A reviewer-approved head stays valid through clean main updates. Any other change to the
PR, and any overlap with main, needs a fresh review and a new `reviewed:` label.

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
4. It has exactly one valid `reviewed:<40 hex>` label, and the gate above passes for it.

Then it runs `gh pr merge N --squash --match-head-commit <head sha>`. The next run starts
when the push to `main` lands and handles the next PR.

| Head state | Action |
|---|---|
| `BEHIND`, no check running | Update the branch from main. The comment says the head is verified before merge. |
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
| Missing or malformed `reviewed:` label | Exactly one `reviewed:<40 lowercase hex>` label is required. |
| Head verification failed: not an ancestor, not on the first-parent chain, not a two-parent merge, a merge of a non-main branch, extra changes, octopus, unknown commit | The head is not the reviewed commit plus main updates. Review the head, then relabel. |
| Overlap with the PR's own edits | Main changed a file the PR also edits. Review the merged head, then relabel. |
| Check `<name>` is not passing | A red or cancelled check. Fix it; the train never retries a failed check. |
| `DIRTY` | The branch conflicts with main. Resolve by hand. |
| `BLOCKED` with every check green | An unresolved conversation, often a Copilot review thread. Resolve it. |
| `UNKNOWN` after about 60 s | GitHub did not compute the state. Look at the PR and re-run. |
| Closed unmerged `train:K` below the head | A batch PR was closed without merging, so the next PR would jump the queue. See the cleanup step. |
| Duplicate `train:N`, missing number, malformed or doubled train label | Fix the labels so the numbers are unique and consecutive. |
| List at the 100-PR limit | More than 99 open or unmerged-closed PRs: the labels cannot all be seen. |
| Squash merge failed | Check the PR, then resume. |

Check names come from the PR's own CI, so the workflow strips control characters and
markup from them and truncates them to 60 characters before they reach the log or a comment.

## Cleanup when a batch PR is closed without merging

Remove its `train:*` label as part of closing it:
`gh pr close N && gh pr edit N --remove-label train:K`.
Otherwise the closed PR halts a later batch loudly, since the next PR would be promoted past it.

## Caveats

- **Closed-PR search is eventually consistent and capped at 100.** The closed-unmerged list
  is requested with `--search "is:unmerged" --limit 100`. A full list halts the train. A
  closed train PR may take time to appear, so the cleanup step above is still required.
- **Open list capped at 100**, requested with `--limit 100`; a full list halts the train.
- **Starvation**: while a train runs, non-train PRs are not updated. A head that stays
  `BEHIND` or waiting holds up the whole batch.
- **Halt is sticky**: a halted train stays halted until a person removes the label.
- **Label trust**: the gate checks the commit, not who wrote the label. See the confirmations
  above.
- **Copilot threads**: a review thread can appear when a PR is marked ready. A `BLOCKED`
  head with green checks halts until the thread is resolved.
- **No `train:N` gaps**: a missing number halts the train, even if the batch is otherwise
  correct. Renumber the batch rather than skipping one.
