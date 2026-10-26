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
3. The second parent of each merge is an ancestor of `origin/main` (any ancestor, not only
   the tip). A merge of any other
   branch halts.
4. The merge adds only main's own change: `git diff` of the first parent against the merge
   is byte-identical to `git diff` of the merge base against the second parent, computed
   with `--binary --full-index`. Any extra byte in the merge halts.
5. If the PR and main edited the same file, rules 4 and 5 alone would halt with "update
   overlaps the PR's own edits: needs a fresh review" (relabel after review). Two things
   pass instead: rule 6, and the change the PR already holds (see "Stacked PRs" below). An
   overlap that is neither still halts.
6. **Clean automerge.** The merge commit's tree is exactly the tree of git's own merge of the
   PR head and main (`git merge-tree --write-tree`, merge base pinned, renames, attributes,
   merge drivers and user config ignored). It applies only when that merge is clean (no
   conflict) and the two have a single merge base. Both sides may then edit the same file in
   different places: the usual case is every PR adding its own bullet to `CHANGELOG.md`.
   The content is a deterministic function of the reviewed head and main, so no byte can be
   injected by the author. It is the same merge GitHub's "Update branch" produces.

   Caveat, stated plainly: rule 6 vouches for the bytes, not for the meaning. Two edits that
   merge cleanly can still be wrong together (for example a changed function and a new caller
   in another place). The required CI checks, which run on the merged head before the train
   merges it, are the semantic gate. A merge that conflicts, differs from git's merge by one
   byte, has two merge bases (a criss-cross history), or was hand-resolved still halts. Two
   PRs inserting a bullet at the very same place conflict: move one bullet, or resolve by hand
   and relabel.

Git runs with global and system config disabled and diff options pinned, so a machine's git
settings do not change the result. Any git failure, or a fetch failure, halts the head.
Submodules are compared exactly: the diffs use `--ignore-submodules=none`, so a submodule
pointer (gitlink) added or bumped by a merge is a change, even when `.gitmodules` says
`ignore = all`. A shallow clone that cannot compute merge bases halts; it is never ok.

A reviewer-approved head stays valid through clean main updates, including the absorption of
a merged parent PR (next section). Any other change to the
PR, and any overlap with main, needs a fresh review and a new `reviewed:` label.

## Stacked PRs: a sprint of dependent PRs in one batch

Build the stack by branching B from A's branch, and open both PRs against `main` (not
against A's branch). Label them `train:1` (A) and `train:2` (B) and give each its own
`reviewed:<sha>`. Review each PR on its own delta: B's reviewer reads `git diff A..B`, not
A's changes again. A stacked branch must contain A's final head, so push no further commit to
A after B was cut.

What the train does: A reaches the head, is verified and squash-merged. B becomes the head and
is now `BEHIND` (main has A's squash, B has A's commits). The train updates B from main. That
merge brings in nothing new for the paths A touched, because B already holds them. The
verifier calls those paths **absorbed**: a path main changed whose change the PR head already
holds. Git's own path-wise merge of the PR head and main (renames off, merge base pinned, `.gitattributes` and merge drivers ignored; needs git 2.40 or later, older halts) must
yield the head's entry there. That covers a file identical on both sides (mode, symlink,
gitlink and deletion included) and a file B also edited on top of A's change. For the paths
that are not absorbed, rule 4 applies unchanged: the merge must equal main's change on them
byte for byte, and rule 5 halts on overlap. So an update that also brings an unrelated commit
from main verifies exactly, and an update that drops or alters main's change, or adds a byte,
halts. After the update the squash of B is B's own delta on top of main, with the head's tree.
Three or more deep stacks work the same way, one parent at a time.

Still halts, by design: B edits the same lines as A (the merge conflicts, `DIRTY`); A changed
after B was cut (B holds an older A, so the paths differ and count as an overlap); main
changed a path B holds differently. Fix by hand, review the result, relabel.

Nothing else in the train changes: the head is still the lowest `train:N`, and a closed
unmerged `train:K` below the head still halts.

## Stop, halt and resume

- **Stop**: `gh variable set MERGE_TRAIN --body off` (any value other than `on` is off), or
  cancel the running workflow run.
- **Halted**: the workflow labels the head `train:halted` and posts one comment with the reason.
- **Resume**: fix the cause by hand, then `gh pr edit N --remove-label train:halted` and
  `gh workflow run "Update behind PRs"`.
- **After a rule change**: a head halted by an older verifier is not re-checked on its own,
  because every run does nothing while `train:halted` is set. Once the new rules are on
  `main`, remove `train:halted` and dispatch again; the next run verifies the head with the
  new rules. If the head was halted for an overlap, nothing else is needed (no relabel) when
  the update merge is now accepted; update the branch from main first if it is `BEHIND`.

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
