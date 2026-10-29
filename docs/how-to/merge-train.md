# Merge Train

How the maintainer merges a batch of open PRs in order, with no per-merge confirmation,
and how the update-behind workflow (`.github/workflows/update-behind-prs.yml`) does it.
This page covers the repository workflow only; it is not part of the research pipeline.

## Labels

| Label | Meaning |
|---|---|
| `train:N` (N = 1 to 3 digits) | Position in a batch, not a priority. N is unique within the batch and the numbers are consecutive. The lowest N among open PRs is the head. Two PRs with the same N halt the train. Inert until the train starts. |
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
   injected by the author. It is usually the same merge GitHub's "Update branch" produces; if GitHub's differs by even one byte (for example through rename detection, which the verifier turns off), the verifier halts instead of passing.

   Caveat, stated plainly: rule 6 vouches for the bytes, not for the meaning. Two edits that
   merge cleanly can still be wrong together (for example a changed function and a new caller
   in another place). The required CI checks, which run on the merged head before the train
   merges it, are the semantic gate. A merge that conflicts, differs from git's merge by one
   byte, has two merge bases (a criss-cross history), or was hand-resolved still halts. Two
   PRs inserting a bullet at the same place, or on adjacent lines, conflict: move one bullet,
   or resolve by hand and relabel.

Git runs with global and system config disabled and diff options pinned, so a machine's git
settings do not change the result. Any git failure, or a fetch failure, halts the head.
Submodules are compared exactly: the diffs use `--ignore-submodules=none`, so a submodule
pointer (gitlink) added or bumped by a merge is a change, even when `.gitmodules` says
`ignore = all`. A shallow clone that cannot compute merge bases halts; it is never ok.

A reviewer-approved head stays valid through clean main updates, including the absorption of
a merged parent PR (see "Stacked PRs" below). Any other change to the
PR, and any overlap with main, needs a fresh review and a new `reviewed:` label.

## Stacked PRs: what works and what halts

A stacked PR is cut from another batch PR's branch. Build it by branching B from A's branch,
and open both PRs against `main`, not against A's branch. Label them `train:1` (A) and
`train:2` (B) and give each its own `reviewed:<sha>`. B's reviewer reads `git diff A..B`, not
A's changes again. A stacked branch must contain A's final head, so push no further commit to
A after B was cut.

Squash-merging A puts A's change on `main` under a commit that B does not contain. The update
of B from main (GitHub's "Update branch", or the train) then has to reconcile two copies of A's
change. Whether that works depends on where B's edits are.

**Works:**

- Independent PRs, and PRs that edit the same file in places that are not next to each other.
  Git merges these cleanly and rule 6 (called rule g in the script) accepts the merge. The automerge tests cover three PRs
  that each add a bullet to one `CHANGELOG.md`, with the third stacked on the first and its
  bullet in a different section.
- A stacked child whose edits do not touch hunks adjacent to its parent's. The verifier calls
  the paths the parent already holds **absorbed** (rule f): main's change on them is exactly
  what the head has. A clean automatic merge already passes under rule 6 without needing
  absorption; otherwise the absorbed paths are set aside and rules 4 and 5 (exact, no overlap)
  apply to the rest. Rule f needs git 2.40 or later; older git halts.

**Does not work: adjacent hunks.**

If B edits the same lines as A, or lines next to them, git cannot merge the two copies of A's
change, because both sides inserted at the same place with different text. This is a textual
conflict. After A merges, B is `DIRTY` ("the branch conflicts with main") and the train halts
there. Typical cases in this repository: both PRs add a bullet at the top of the same
CHANGELOG list, or both append tests to the end of one file. Rules f and g verify a merge that
exists; they cannot resolve a conflict, and the verifier never creates a merge.

Still halts, by design: A changed after B was cut (B holds an older A, so the paths differ and
count as an overlap); main changed a path B holds differently. Fix by hand, review the result,
and relabel.

**What happened on 2026-10-26.** PR #182 added rule f and PR #183 added rule g. PR #177 had
halted once on overlap (a shared `CHANGELOG.md` edit) before #183 was on `main`; after the halt was
cleared, rule g let its update merge through and it merged. PR #178, stacked on #177, halted `DIRTY` after #177 was squash-merged. The
GUI of ADR 0035 was then landed as one PR, #184.

**Recommended practice today:**

1. **Land dependent work as one PR.** Put the net change in one branch cut from `main`, and
   review it once at its head. The individual deltas can still be reviewed separately, with
   their verdicts cited in the review of the single PR.
2. **Or merge in sequential batches, restacking in between.** Merge the parent in its own
   batch. Then, once `main` holds the parent's squash, restack the child:

   ```bash
   git fetch origin
   git rebase --onto origin/main <last parent commit> <child branch>
   git push --force-with-lease
   ```

   `<last parent commit>` is the final commit of A that B was cut after. The rebase drops A's
   commits, which `main` now holds as one squash, and keeps B's own commits. Then review the new
   head's diff against `main`, which is B's own change. Remove the old `reviewed:` label and add
   `reviewed:<new head>`: the gate needs exactly one, and the old one names a commit that is no
   longer on the PR. The restacked head is the reviewed commit, so the verifier needs no update
   merge unless `main` has moved since.

Nothing else in the train changes: the head is still the lowest `train:N`, and a closed
unmerged `train:K` below the head still halts.

**Not built (options, not promises):**

- **Automatic restacking**: the train could rebase a child onto `main` after its parent merges.
  Nothing does this today; the restack above is manual.
- **GitHub's native stacked PRs**: GitHub offers them as a public preview. Before relying on
  them, check GitHub's current documentation on each of these points: as of this writing, auto-merge
  is not supported for them; a rebase changes the head commit, which invalidates a `reviewed:`
  label; GitHub's documentation does not state whether squash merge is supported; and whether they
  are available for this repository has not been confirmed.

## Stop, halt and resume

- **Stop**: `gh variable set MERGE_TRAIN --body off` (any value other than `on` is off), or
  cancel the running workflow run.
- **Halted**: the workflow labels the head `train:halted` and posts one comment with the reason.
- **Resume**: fix the cause by hand, then `gh pr edit N --remove-label train:halted` and
  `gh workflow run "Update behind PRs"`. The label must be removed first, or every run does
  nothing.
- **After a rule change**: a head halted by an older verifier is not re-checked on its own,
  because every run does nothing while `train:halted` is set. The workflow checks out the
  default branch, so the scripts it runs are `main`'s. Once the new rules are on `main`, remove
  `train:halted` and dispatch again; the next run verifies the head with the new rules. If the
  head was halted for an overlap, nothing else is needed (no relabel) when the update merge is
  now accepted; update the branch from main first if it is `BEHIND`.

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
| `DIRTY` | The branch conflicts with main. For a stacked child, its hunks touch its parent's. Restack it or merge the stack as one PR (see "Stacked PRs"). |
| `BLOCKED` with every check green | An unresolved conversation, often a Copilot review thread. Resolve it. |
| `UNKNOWN` after about 60 s | GitHub did not compute the state. Look at the PR and re-run. |
| Closed unmerged `train:K` below the head | A batch PR was closed without merging, so the next PR would jump the queue. See the cleanup step. |
| Duplicate `train:N`, missing number, malformed or doubled train label | Fix the labels so the numbers are unique and consecutive. |
| Stacked head is BEHIND (`PR #N: stacked and BEHIND; the train is halted (restack by hand)` in the step summary) | Update-branch is not supported for stacked PRs. Restack the child by hand (see "Stacked PRs"), review the new head, and relabel. |
| Post-merge audit failed; `#N could NOT be halted (label or comment failed), halt it by hand` | The PR named before it is already merged. Add `train:halted` to #N by hand and read the audit failure before resuming. |
| List at the limit: 100 or more open PRs, or 1000 or more unmerged-closed PRs | The list is cut at the gh limit, so the labels cannot all be seen. |
| Squash merge failed | Check the PR, then resume. |

Check names come from the PR's own CI, so the workflow strips control characters and
markup from them and truncates them to 60 characters before they reach the log or a comment.

## Native stacked PRs (draft, off by default)

This is the workflow side of ADR 0037. It is a draft and must not be enabled until a rehearsal on
a scratch repository has proved the trigger and token behaviour (below).

- **Switch.** The repository variable `MERGE_TRAIN_STACKS`. Anything but `on` is off: the train does not
  recognise stacks, non-stacked PRs merge exactly as before, and a stacked head halts when the run
  reaches it `BEHIND` (the update step checks the PR's `stack` key) or when `gh pr merge` is refused. `gh variable set MERGE_TRAIN_STACKS --body off`
  is the rollback; `MERGE_TRAIN=off` stops the whole train.
- **Two-step decide.** With the switch on, `scripts/train_facts.py` reads the head's stack record.
  `train_step.py decide` runs once without a verifier result, only to give the parent's commits
  (`verifier_args`); those commits are fetched by SHA as data and never checked out;
  `train_verify_head.py` runs with them (rule h); then `decide` runs again with the result.
- **Merge.** A stacked head is merged with `scripts/train_merge_async.py` pinned to the verified head,
  never `gh pr merge`, which GitHub refuses for stacks. A non-stacked head keeps `gh pr merge`.
- **Audit stop.** The merge client audits the squash commit in the same run. If the audit fails the
  PR is already merged, so the job re-lists the open PRs and `train_step.py audit-stop` names the
  lowest open `train:N` PR, which gets `train:halted`. With none, the job comments on the merged PR
  and fails.
- **`update-branch` is HTTP 403 for a stacked PR.** The workflow never runs it on one (it checks the
  PR's `stack` first). In train mode a stacked head that is `BEHIND` halts (restack by hand). In
  update-only mode the PR is skipped with a warning and a summary line, and, as for any `BEHIND` PR
  that cannot be updated, it holds up the PRs behind it until restacked or closed. If the stack
  check itself fails (API error) the run warns and skips the update; the next trigger retries.
- **The lists now include every base.** (a) A train head whose base is not `main` halts. (b) The
  closed-unmerged list counts PRs of every base and halts the train for good at 1000 entries; it can
  only grow. (c) 100 open PRs, one of them not based on `main`, now raise an input error in train
  mode (before, 99 `main` PRs worked). Update-only mode still targets `main` only.
- **The merge client fetches the squash commit by SHA** before the audit, because GitHub creates it
  after the workflow's last fetch; a failed fetch fails the audit and halts the successor.
- **No `schedule` trigger.** A wait after a parent's merge relies on a `workflow_run` or `push`
  event after GitHub's rebase. Whether one fires for an App-triggered merge is part of the rehearsal;
  a `schedule` trigger is added only if it does not.
- **Still required before enabling:** the rehearsal must show that the App token can call
  `merge-async` under `enforce_admins`, that a stale `sha` is rejected, that checks and events fire
  after the rebase, that an open child keeps `stack.position == 2` and a 40-hex label while its
  parent is merged, and that fetch by SHA works on the real repository. The App token must also be allowed
  `GET /repos/{owner}/{repo}/stacks/{number}` (the facts record reads it).

## Cleanup when a batch PR is closed without merging

Remove its `train:*` label as part of closing it:
`gh pr close N && gh pr edit N --remove-label train:K`.
Otherwise the closed PR halts a later batch loudly, since the next PR would be promoted past it.
A closed unmerged PR that still carries its `train:K` label keeps halting later runs until the
label is removed.

## Caveats

- **Closed-PR search is eventually consistent and capped at 1000.** The closed-unmerged list
  is requested with `--search "is:unmerged" --limit 1000`. A full list halts the train. A
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
