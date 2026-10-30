# ADR 0037: The Merge Train and GitHub Native Stacked Pull Requests

## Status

Accepted — 2026-10-27, on the merge of the pull request that adds this record, which the owner approves. Until the
merge, this record was `Proposed` on the branch `docs/adr-0037-train-native-stacks`. Accepting it changes no code, no
workflow and no repository setting: it fixes the design that the pull requests of Decision 9 implement, one at a time.
Points the owner has not decided are in "Open questions for the owner" and are not claimed as decided.

ADRs 0001 to 0036 are frozen. This record does not edit any of them.

## Context

### What happened on 2026-10-26

The unattended train (`.github/workflows/update-behind-prs.yml`, `scripts/train_step.py`,
`scripts/train_verify_head.py`) merged #182, #183 and #177, then halted at #178, stacked on #177. When #177 was
squash-merged, the update of the child from `main` conflicted on adjacent hunks (top `CHANGELOG.md` bullet, appended
tests). Rules f and g of the verifier check a merge that exists; they cannot make one. The how-to
(`docs/how-to/merge-train.md`, "Stacked PRs") offers only a manual restack.

### What the pilot showed

`SergioHuesca/stacks-pilot` (throwaway, public) has squash-only merges, required check `gates` (strict), conversation
resolution and `enforce_admins`. Three native stacked PRs (`gh-stack` v0.3.0; stack number 4; #1, #2, #3) were merged bottom
first. Read-only checks in this session confirm the following.

- After a parent is squash-merged, GitHub retargets the child to `main` and rebases it, in steps. #2 (parent #1 merged
  01:25:58): `automatic_base_change_succeeded` 01:25:59, `head_ref_force_pushed` 01:26:03. #3: a force-push alone at
  01:26:04 (after #1 merged; base still the branch of #2), then, after #2 merged at 01:26:59, base change 01:27:00 and
  force-push 01:27:04. For 1 to 5 seconds after a parent merges, the child has base `main` (or its parent's branch) and
  its **old** head. Other timeline events seen: `added_to_stack`, `committed`, `merged`, `closed`, `head_ref_deleted`
  (`delete_branch_on_merge` is on in both repositories, so the parent's branch is deleted at once; `pull/N/head` persists).
  Every PR had one commit; no adjacent-hunk conflict occurred.
- `gates` ran on each new head, 9 to 10 seconds after it was created.
- Closed #2 still carries its label (`reviewed:test`). That is not a 40-hex label, and nothing here shows that an **open**
  child keeps a 40-hex label across its parent's merge and rebase.
- `gh pr merge` and `--auto` are refused for a stacked PR. `PUT /repos/{o}/{r}/pulls/{n}/merge-async` with
  `merge_method=squash` and `sha` returns `202` and a uuid; `GET …/merge-async/{uuid}` is polled until `merged`.
  `GET …/merge-async/<unknown uuid>` is `404`.
- Each merge changes the head SHA of every PR above it, with no author push. The new head is the new `main` plus the PR's
  own diff. The rebased commits are unsigned; the squash commits on `main` are signed by GitHub and have one parent. The
  squash commit's tree equals the merged head's tree.
- #3 was rebased twice (onto the rebased head of #2, `0d35733f…`, then onto `main`, `cc2b8739…`). A force-pushed-away
  commit and the `pull/N/head` of a closed PR can both be fetched by SHA over HTTPS (reviewer, from the pilot).

### API surface (read-only)

| Need | Endpoint and fields |
|---|---|
| In a stack, and where | `GET /pulls/{n}`, key `stack`: `{id, number, position, size, base:{ref,sha}}`, position 1 is the bottom. Verified on #1 to #3. For a PR that is not stacked the key is **absent** (PRISMA-Bib #188: `has("stack")` false; the reviewer saw `null`): treat absent and `null` alike. GraphQL has `PullRequest.stack` and `stackEntry`. |
| The stack's PRs in order | `GET /stacks/{number}`: `pull_requests[]` in array order (no position field); entry keys `base, draft, head, html_url, id, merged_at, node_id, number, state, title, url, user`; **no** merge commit, **no** labels. `GET /stacks` is slimmer: `draft, head{ref,sha}, merged_at, number, state`. |
| Merge commit, labels, `merged_at` of a PR | `GET /pulls/{n}`: `merged`, `merged_at`, `merge_commit_sha`, `labels` (verified on #2). |
| Rebase finished | No field. Observable: `merge_commit_sha` of the parent is an ancestor of the child's head. |
| `merge-async` on failure, on a stale `sha`, repeated during a flight; a child whose parent's merge fails; `strict` semantics of `merge-async` | **Not verified.** Writing to the pilot is not allowed. |
| Real repository | `required_status_checks` strict with `lint, fast, full, docs, e2e`; `enforce_admins` on; `required_signatures` off; no rulesets (no merge queue); 73 closed-unmerged PRs. |

### Why the current verifier cannot accept a GitHub-rebased child

The child's reviewed commit R is not an ancestor of the rebased head H, so rule a halts. Asking for a new `reviewed:` label
per rebase would be the manual restack this record removes.

## Decision

### 1. The invariant

What merges is the reviewed change plus only `main`'s own changes, or the train halts. For a stacked child whose parent has
merged: the tree of H equals the three-way merge of the current `main` and the reviewed head, on the base the reviewer
reviewed against, which is the parent's reviewed head. The squash tree equals the tree of an up-to-date head, so proving
`tree(H)` proves what lands.

Rejected: trusting GitHub's rebase unchecked; a new label per rebase; comparing the rebased diff with the reviewed diff
(wrong when `main` touched the same files); comparing patch ids (they ignore context).

### 2. Rule h (the verifier)

`train_verify_head.py` gains three optional arguments, all or none: `--parent-reviewed R_parent`, `--parent-head P_final`
(the parent's final head) and `--parent-merge M_parent` (its merge commit). With them, rule h replaces the `R == H`
shortcut and rule a. Walk the first-parent chain down from H. Two-parent merges at the top are update merges made after the
rebase. T is the first commit with one parent. The series is `main..T` on the first-parent chain, X the parent of its bottom
commit. The head is accepted iff:

- **h1 (shape).** The series is not empty; every commit in it has one parent (a merge commit in the series halts); the bottom
  commit has a parent (a root commit halts); X is an ancestor of `origin/main` (not necessarily its tip); at most 500
  commits.
- **h2.** `R_parent` is a proper ancestor of `R_child`.
- **h3.** `tree(T)` equals the tree of `git merge-tree --write-tree --merge-base=R_parent X R_child`, in the pinned
  environment of rule g (config off, `merge.renames=false`, `GIT_ATTR_SOURCE` the empty tree, `core.attributesFile` off,
  empty `XDG_CONFIG_HOME`, pinned `diff.*`, git 2.40 or later). The result is `X + (R_child - R_parent)`.
- **h4.** That merge has no conflict (exit 1 halts).
- **h5.** Each two-parent merge above T passes the full existing `verify(T, H)` (rules a to g with T as reviewed commit).
  Rule h alone returns ok for an honest T under an evil update merge; only `verify(T, H)` rejects it, so the hand-off is
  part of the rule and has a named test and a mutation (Decision 8).
- **h6 (the parent).** `tree(M_parent)` equals `tree(P_final)`, and `verify(R_parent, P_final)` (rules a to g) passes.

X, not the tip of `main`, is "ours" in h3, so the proof does not depend on where `main` is when the train looks. The label
keeps `R_child`; nothing is written back. If GitHub rebases again, the proof is run again from the same inputs.

**`R_parent`.** It is the single valid `reviewed:<40 hex>` label of the merged parent PR, found through the stack record,
never through the child's history. Label write access is owner-only (existing confirmation 2).

**What a wrong `R_parent` can do.** The merge in h3 draws every byte from X and `R_child`, so no unreviewed byte enters
whatever `R_parent` is. A wrong, older `R_parent` changes which of `main`'s changes win: an H' that re-adds a line `main`
had deliberately reverted is accepted with an old `R_parent` and rejected with the true one (reviewer's `s5.py`, V4). h2
bounds the base to the child's ancestry; h6 ties the label to what the parent actually merged: a stale or wrong label fails
`verify(R_parent, P_final)`.

**One level only, no recursion.** h6 is checked for a parent that is the bottom of its stack (the child is at position 2),
which is the incident's case. A child at position 3 or higher with a merged parent **halts** ("three-level stacks are not
enabled") until a later PR, after a rehearsal. Recursion is dropped because a human may squash-merge a parent with an extra
unreviewed commit and leave the label (reviewer's V3): rule h accepts the child correctly, since the extra commit is now
`main`'s own change, but a re-derivation of the parent's label at every level would halt that child for good. At the one
level kept, the same parent halts the child too, deliberately: `verify(R_parent, P_final)` fails. **No label can unblock it**
(reviewer's `s6.py`): with the label `a1` (the reviewed commit) h6 fails because the merged head `a2` carries the extra
commit; with the label `a2`, h2 fails because `a2` is not an ancestor of the child's reviewed commit `b1`, which was cut
from `a1`. The only way out is that the owner **restacks the child by hand** (as the how-to describes) and reviews it
again. This is fail-closed and safe; no rule is changed to make a relabel work. A parent without a valid label is a case
where the workflow cannot run the verifier: it follows the "verifier did not run" rule of Decision 3.

**Cases.**

- *Position 1.* No parent, no rebase: existing rules a to g against its own label. It is still merged with `merge-async`.
- *Only part of the stack in the batch.* If the head's parent is merged, rule h applies whether or not the parent was in the
  batch. If the parent is open or closed unmerged, halt.
- *`main` moved after the rebase.* X is any ancestor of `main`; the head is then `BEHIND`, and h5 checks the update.
- *Conflict.* GitHub's rebase is rename-aware and our merge is not. A child that renames a file that `main` also edits (or
  renames and edits it) conflicts in h3 where GitHub's rebase succeeded: a false halt, restack by hand. Not false halts:
  edits to the parent's lines, deletions, mode changes, adding an executable, three commits (reviewer's fuzz, 300 cases, no
  divergence). How GitHub rebases a multi-commit child or one with a merge commit is *not verified*.

### 3. The decision (`train_step.py`)

**Visibility.** Today both lists use `--base main`. A native child's base is its parent's branch until GitHub retargets it, so
after A merges, B and C vanish for 1 to 5 seconds, an independent D with a higher `train:N` becomes head and merges ahead of
them. Fix: both `gh pr list` calls drop `--base main` and add `baseRefName`; the head is the lowest `train:N` among **all**
open PRs. A head whose base is not `main` and that is not stacked halts. Update-only mode keeps "the lowest-numbered open PR
targeting main" by filtering on `baseRefName` inside the call to `select_next_pr`; that filter is the only one left.
No separate stack-completeness check is added: the 100-entry cap already halts.

**Pre-existing cliff, not fixed here.** A full closed-unmerged list (100) halts the train permanently. The real repository has
73 today. Every PR closed unmerged moves it closer. Owner question 4.

**Facts record.** The pure `decide` gets, for the head only, one JSON object assembled by the workflow:

```json
{"pr": 178, "now": "2026-10-27T01:30:00Z",
 "stack": null | {"id": 2644774, "number": 4, "position": 2, "size": 3},
 "parent": null | {"number": 177, "merged": true, "merged_at": "…Z", "merge_commit_sha": "40 hex",
                   "in_main": true, "reviewed": ["reviewed:…", "…"]}}
```

Calls: `GET /pulls/{head}` (`stack`), `GET /stacks/{number}` (the parent is the entry before the head's; the head's own entry
is checked against `position`), `GET /pulls/{parent}` (`merged`, `merged_at`, `merge_commit_sha`, `labels`). `in_main` is
`git merge-base --is-ancestor <merge_commit_sha> origin/main`, after one re-fetch of `main` if it is false. Anything
unparseable raises `InputError` and the script exits 2: the step fails, nothing is merged and no label is set (there is no
labelling path for a malformed record); the next trigger tries again and the owner sees the failed run. `stack` absent or `null` means not stacked. `parent` is null at position 1.

**Path selection, strictly by the stack record.** `stack` null or position 1: the existing verifier call. Position 2 with a
merged parent: `train_verify_head.py <R_child> <H> --parent-reviewed … --parent-head … --parent-merge …`. Position 3 or
higher with a merged parent, or a parent that is not merged: halt. The path is never chosen from the shape of the head:
an old child head plus an update merge from `main` that contains M_parent is accepted by today's verifier (rules b to g
absorb the parent's change; reviewer's X1) but is **not** a head rule h accepts, and must be judged by rule h only.
`MERGE_TRAIN_STACKS` not `on`: any stacked head halts.

**Pending (a wait, decide only).** The verifier gains no new output. A stacked head at position 2 whose parent is merged, and
for which any of these holds, is a **wait**: the verifier is not ok, did not run (a fetch failed, or the parent has no valid
`reviewed:` label so no verifier call can be built), the base is not `main`, or `in_main` is false (regardless of any
verifier result) while
`now - parent.merged_at` is under 10 minutes, and a halt afterwards (with the verifier's reason). It is never a merge. The
case "the parent merged between my fetch and my lookup" is `in_main: false` after one re-fetch: the same wait, not a sticky
halt. The wait is checked **before** the verifier's `ok`, and after the draft and label halts of the head itself. A human-merged
parent, a parent without a valid label, or any failed parent check waits at most 10 minutes and then halts. The train does **not** `update-branch` such a head until the
verifier is ok for it: updating a pending child would create the X1 shape.

**Order and batch.** A head whose parent is open or closed unmerged halts. There is no separate `train:N`-versus-stack-order
check: the facts record covers the head only, and every reachable inversion (a child numbered below its parent) ends as a head
whose parent is open, which halts already. A stack member not in the batch above the head is ignored. (That GitHub itself
refuses a child-before-parent merge was not verified; the train does not rely on it.)

### 4. The merge call (stacked heads only)

A non-stacked head keeps `gh pr merge --squash --match-head-commit` unchanged. A stacked head, once `decide` says merge (not
draft, `CLEAN`, every check passing on the **new** head, one valid label, verifier ok), is merged so:

1. `GET /pulls/{n}`: merged means success; a `head.sha` different from the decided one means wait.
2. `PUT …/merge-async` with `merge_method=squash` and `sha=<the verified head>`; `merge_action` unset. The `sha` is always
   passed. **Hypothesis, not fact:** that a stale `sha` is rejected. The pilot showed only that a correct `sha` is accepted
   and an omitted one is filled in. Until the rehearsal proves rejection of a stale `sha` (response recorded), the pin is
   not counted as closing the race between verification and call, and the audit must not depend on `strict` (its behaviour
   for `merge-async` is unverified).
3. Any response but `202` with a uuid halts.
4. Poll every 3 seconds up to 180 seconds. `pending` and `enqueued` continue. `merged` goes to the audit. `failed`, an unknown
   status or a non-2xx poll halts at once. On **timeout** the PR is re-read after 60 more seconds: merged goes to the audit,
   anything else halts.
5. **Audit, in the same run.** Let S be `merge_commit_sha`, P_S its parent. It passes iff S has exactly one parent, `tree(S)`
   equals `tree(H)` (H the verified head), and P_S is an ancestor of H. Anything else is a failure. If `main` moves during the
   poll the audit may fail without a fault; the owner then inspects, which is the safe side.
6. **Stop on a failed audit.** The current workflow runs `decide` once before the merge, and `$pr` in the shell `halt()` is
   the PR just merged. `decide` scans only **open** PRs for `train:halted`, and GitHub accepts a label on a closed PR, so
   labelling the merged PR would stop nothing. Instead: after the merge the job re-lists the open PRs and calls a new
   subcommand `train_step.py audit-stop --audit FILE < open_prs.json`. It prints `halt` on the **lowest open `train:N`** (the
   successor; the merged PR, if still listed, is skipped), or `none` when there is no open `train:N` PR. The workflow then
   runs the existing `halt()` with `$pr` set to the successor. With no successor nothing is running: the job posts the
   failure as a comment on the merged PR and in the step summary and fails once (`::error::`); with no start-of-run audit it
   cannot repeat. A unit test asserts that the label target is an open PR.
7. **Idempotency.** `concurrency` serialises runs. A run cancelled after the `202` leaves the PR merged or open; the next run
   sees which. If open with the same head it repeats step 2 with the same `sha` (whether a second call is rejected while one
   is in flight is unverified; a rejection halts a PR that did merge and is cleaned up by hand).

**No start-of-run audit.** The train keeps no state, so a later run does not know which head it verified. At a later run
the merged PR's head is whatever GitHub reports, and GitHub guarantees `tree(S) == tree(head)` for a squash, so the check
cannot detect a swap between verification and merge. A merge whose audit never ran (cancelled run) is not re-audited; the
owner sees the run as cancelled.

### 5. Workflow changes (`update-behind-prs.yml`)

- Both lists: no `--base main`, `baseRefName` added (Decision 3).
- For a head with a `stack` key: assemble the facts record, fetch `R_child`, `R_parent`, `P_final` and `M_parent` by SHA
  (fetchable, as seen on the pilot; to be confirmed on the real repository), run the verifier with the three arguments,
  pass the facts to `decide`, merge with the Decision 4 steps, then the audit and the stop.
- Token: the App token has `contents: write` and `pull-requests: write` and `gh pr merge` merged unattended under
  `enforce_admins` (the App is not a bypass actor). That `merge-async` accepts it, and that checks re-run on a rebase
  triggered by an App merge, **must be verified in the real repository**; the REST docs were not reachable here.
- The `schedule` trigger is **not** part of the design. It is added only if the rehearsal shows that no `workflow_run` or
  `push` event fires after GitHub's rebase of an App-triggered merge (Decision 9, Open question 3). Without a trigger a wait
  would stall.
- Unchanged: `concurrency`, the checkout without `ref:`, and the rule that no PR code runs in the job.

Rejected: `merge-async` for every PR (the unstacked path has run in production); a separate workflow for stacks (two actors
on the head); polling the PR instead of the uuid (kept only as a fallback if the result shape proves unstable).

### 6. Signing

The rebased heads are unsigned. The repository does not require signed commits, and the squash commits are signed by GitHub.
Whether a squash of an unsigned head would be accepted under a signed-commits requirement is not verified; if it were
refused, the feature is turned off. The train never signs or pushes anything.

### 7. Failure modes, rollback, and what the train never does

| Condition | Action |
|---|---|
| `MERGE_TRAIN_STACKS` not `on`; position 3 or higher; parent open or closed unmerged | halt |
| `stack` or the facts record unparseable | the script exits 2: step fails, nothing merged, no label |
| Head not stacked and base not `main` | halt |
| Parent merged less than 10 minutes ago and the verifier is not ok, did not run (including a parent without a valid label), base not `main`, or `in_main` false | wait |
| Same, 10 minutes or more | halt (verifier reason) |
| Rule h fails (h1 to h6) after the window; `R_parent`, `R_child`, `P_final` or `M_parent` unfetchable | halt |
| Checks running on the new head | wait |
| `merge-async` not `202`, status `failed`, unknown status, non-2xx poll | halt |
| `merge-async` timeout | re-read after 60 s: merged, audit; else halt |
| Audit failure with an open successor | `train:halted` on the successor, job fails |
| Audit failure with no open successor | comment on the merged PR, summary, job fails once |
| Head `BEHIND` and the verifier ok | update-branch as today (*untested on a stacked child*) |
| Head `BEHIND` and the verifier not ok | wait or halt as above, never update |

**Rollback.** `gh variable set MERGE_TRAIN_STACKS --body off` (anything but `on` is off): stacked heads halt. `MERGE_TRAIN=off`
stops the whole train.

**The train never** merges a stacked PR with `gh pr merge`; calls `merge-async` without `sha`; merges a head the verifier did
not accept, or while pending; creates, rebases, retargets or force-pushes a PR branch; edits a `reviewed:` label; calls
`unstack`; sets `merge_action`; retries a failed merge; skips a PR; acts on a PR other than the head (the audit stop acts on
the successor, which is the head by definition once the merged PR is gone); updates a pending stacked child; or writes a
repository variable. The base filter exists only inside update-only mode.

### 8. Testing

Verifier tests are `tests/integration/test_train_verify_head*.py` (four files, real temporary git repositories, no git
mocks); decision tests are `tests/unit/test_train_step.py`. New files sit beside them. A helper simulates GitHub's rebase by
cherry-picking the child's commits onto `main`.

- **Integration** (`test_train_verify_head_native_stack.py`). Accepted: two-level stack, three-commit child, child deleting a
  file, changing a mode or adding an executable, adjacent hunks, `main` moved with an honest update merge on top. Halt:
  `R_parent` not an ancestor; extra bytes in H; a missing `main` change; a conflict; rename of a file `main` edits; a merge
  commit in the series; a root-commit series; H whose parent is not on `main`; a stale label on a human-merged parent
  (V3); an older `R_parent` with a line `main` reverted (V4, halts through h6); `tree(M_parent) != tree(P_final)`.
  **Named combined test** `test_stack__rule_h_passes_but_update_merge_is_evil__halts`, with an honest-update pair. **X1
  test:** an old head plus an update merge containing M_parent passes today's `verify` but must be judged by rule h and
  halt.
- **Unit** (`test_train_step_stack.py`). Visibility: A merged, B and C with base not `main`, independent D(4): wait on B,
  never merge D; the same for the closed list. Pending: parent merged 2 minutes ago and verifier not ok is wait; 11 minutes is
  halt; `in_main` false is wait even when a verifier result says ok; the transient head of #3 is wait; a parent without a valid
  label is wait, then halt. Path selection: position 3 halts; stacked head with `MERGE_TRAIN_STACKS` off halts; a pending head is not updated.
  `audit-stop`: with an open successor the target is that open PR (and a stale list still containing the merged PR does not
  pick it); with none the output is `none`.
- **Merge client** unit tests with an injected runner: `sha` always present, each poll state, timeout and re-read, the audit
  (swapped tree, P_S not an ancestor, two parents).
- **Mutations**, each must fail a test: `R_parent` replaced by the merge base; drop h2; drop h4; drop h6; use `main`'s tip
  for X; keep the `R == H` shortcut for stacked heads; skip `verify(T, H)` for update merges; choose the path by head shape
  instead of the stack record; restore `--base main` on the lists; evaluate the verifier before pending; drop the 10-minute
  limit; label the merged PR instead of the successor; drop the pinned attributes or `--no-renames`.
- **Run as CI runs** (`pytest -n auto`) and single-process, on the Windows mount and a Linux runner; git 2.40 or later.
- **Rehearsal on the pilot** (it has no train). It uses the scripts of PRs 1 and 2 (merged) and of PRs 3 and 4 (open as
  drafts, copied to the pilot), and needs: the App installed on the pilot with the two permissions, `MERGE_TRAIN` and
  `MERGE_TRAIN_STACKS` on, the labels, and a new stack made with `gh stack`. It must show: **a stale `sha` rejected**;
  the `merge-async` failure body; the App-merge token and checks; whether checks and `workflow_run` fire after the rebase
  (decides the schedule trigger); a 40-hex label on an open child across the parent's merge; **that an open child keeps `stack.position == 2` and the same
  stack record while its parent is already merged**; how GitHub rebases a
  multi-commit child and one with a merge commit; the retarget window with an independent higher `train:N`; a wrong parent
  label; a failing parent; `update-branch` on a rebased child; a repeated `merge-async` during a flight; fetch by SHA on the
  real repository; a deliberately failed audit whose label lands on an open PR. Each response goes into the how-to.

### 9. Rollout

Each PR is green on its own and changes nothing the train does until PR 4.

1. **PR 1, verifier.** Rule h with its three arguments, h1 to h6, the integration tests and mutations. No caller.
2. **PR 2, decision.** Facts record, visibility (absent `baseRefName` means `main` until PR 4), path selection, pending wait,
   the parent-state checks, `MERGE_TRAIN_STACKS`, the `audit-stop` subcommand, unit tests. The workflow is untouched.
3. **Drafts of PR 3 and PR 4 and the rehearsal.** PR 3 is the merge client (call, poll, audit), PR 4 the workflow. Both are
   opened as drafts and copied to the pilot for the rehearsal; PR 1 and PR 2 are already merged.
4. **PR 3 merges** only when the rehearsal has **proven that a stale `sha` is rejected**. If not, it is not merged and this
   record is amended.
5. **PR 4 merges** (workflow: lists, facts assembly, fetches, merge call, audit and stop) only with these gates met: the
   owner's check of the token, and a **hard gate on triggers**. The rehearsal must show either that a `workflow_run` or
   `push` event fires after GitHub's rebase of an App-triggered merge, or PR 4 adds the `schedule` trigger. Without one of the
   two a wait stalls silently, with no halt and no label.
6. **PR 5, documents:** the how-to, the halt table, a warning not to remove labels from merged parents, `CHANGELOG.md`.
7. **First live use:** a two-PR stack, owner present.
8. **Later PR:** three-level stacks, after a rehearsal of a three-level stack and a decision on recursion.

> **Note added 2026-10-27 (owner decision, the rehearsal evidence and what was built; this note does not change the record above).**
> Items 3 to 5 of the Rollout assumed a rehearsal on a scratch repository. The owner decided on 2026-10-27 to skip it and to prove
> the stacked path live on this repository, with the owner present, using a throwaway two-PR stack. PR 4 (#195) was therefore
> merged **before** the two gates of item 5 were met, with the stacked path off (the repository variable `MERGE_TRAIN_STACKS` is
> not set). Those two gates stay open and are checked by the first live use: that the App token is accepted by `merge-async` and
> can read `GET /repos/{o}/{r}/stacks/{n}` under `enforce_admins`, and whether an event fires after GitHub's rebase of an
> App-triggered merge (if none does, a `schedule` trigger is added before the feature is used). PR 4 merged without a
> `schedule` trigger, so once the variable is on a wait could stall silently until that question is answered. PRs 1 to 4 are #190, #191 with
> #192, #193 and #195.
>
> A first part of the rehearsal had been run on a scratch repository with the owner's own token. It proved item 4's gate: a
> `merge-async` call with a non-current `sha` (a nonexistent one, an older commit, another PR's head, uppercase hex, a short
> one) returns HTTP 400 "Pull request head branch was modified." and merges nothing. It also showed that an open child keeps
> `stack.position` and its stack record while its parent is merged (a two-level stack, and the middle child of a three-level
> stack); that an omitted `sha` is filled in by the server; that a required check that is still pending fails the job instead of
> waiting; that a repeated call while one is in flight returns HTTP 409 with the existing job id; that a 40-hex `reviewed:` label
> survives the rebase; that a merge commit inside a child is flattened; and that `update-branch` on a stacked child returns
> HTTP 403.
>
> Read the record above with these differences, which are what PRs 2b, 3 and 4 implement: a stacked head that is BEHIND halts
> and is never updated (`update-branch` is refused for stacked PRs, so the update merges that h5 covers cannot be produced by the
> train for a stacked head); a 409 from `merge-async` is a wait, not a halt and not an adoption of the existing job; the 202
> response must carry `expected_head_sha` equal to the verified head before it is polled; the audit fetches the merge commit by
> `sha` before it runs; `audit-stop` prints the PR number or `none` (not the word `halt`); the facts record has the optional keys
> `parent.head_sha` and `parent.state`; the job has a 20-minute limit and the merge client's fetch a 120-second limit.

> **Note added 2026-10-29 (owner decision, closed-unmerged cap; this note does not change the record above).** The
> closed-unmerged list is now requested with `--limit 1000` (`.github/workflows/update-behind-prs.yml`, PR #216), up from
> 100, and the train halts when that list reaches `CLOSED_LIMIT = 1000` entries (`scripts/train_step.py`). This answers
> open question 4 by raising the cap. The "Pre-existing cliff" paragraph of Decision 3 (about L140-141) and open question 4
> (about L366) state the old cap of 100 and are left as written. The open list stays at `--limit 100` (`LIST_LIMIT`).

### What was cut, and why

| Cut | Why |
|---|---|
| Rule h0, `--parent-merge-commit` as a verifier output and `pending` in the verifier | Pending is a decide-only wait; the verifier stays a yes/no. |
| Stack-completeness check | The 100-entry cap already halts. |
| Recursive re-derivation of `R_parent` for every level | V3: it halts a child that rule h accepts correctly. One level kept. |
| Three-level stacks in the first rollout | Rehearse first. |
| Start-of-run audit | No state; the check cannot detect a swap. |
| Recomputed-merge audit | Trivial or a false-alarm source; replaced by `tree(S) == tree(H)` and P_S an ancestor of H. |
| `schedule` trigger as a commitment | Added only if the rehearsal needs it. |
| Poll-both fallback | Only if the result shape proves unstable. |
| Writing `MERGE_TRAIN=off` on audit failure | Needs `Variables: write` for the App; the label on the successor is used. |

## Open questions for the owner (not decided by this record)

1. May native stacks and `gh-stack` be enabled on the real repository (preview status not confirmed)?
2. Is a false halt acceptable when GitHub's rebase and git's merge disagree (restack by hand)?
3. Is a `schedule` trigger acceptable if the rehearsal shows it is needed (it is then a hard gate of PR 4)?
4. The closed-unmerged list halts the train at 100 entries (73 today). Raise the cap, page the list, or accept it?
5. Should the App get `Variables: write` so that an audit failure can set `MERGE_TRAIN=off`? The design says no.
6. A second App, or the same App, on the pilot for the rehearsal?
7. Should a stacked PR's label be taken at its own commit (as designed) or after its parent merged?

Not verified, to be settled by the rehearsal: the `merge-async` failure body and stale-`sha` response; a repeated call during
a flight; a child whose parent's merge fails; token and `enforce_admins` for `merge-async`; whether checks fire after an
App-triggered rebase; a 40-hex label on an open child; that an open child keeps `stack.position == 2` and its stack record while its parent is
merged (every pilot read was taken after the whole stack closed; if GitHub re-indexes the child to position 1 or drops the
stack, the child goes to the old verifier path, rule a halts because `R_child` is not an ancestor of the rebased head, and the
train is safe but the feature is useless); multi-commit and merge-commit rebases; `update-branch` on a stacked
child; `gh pr merge` at position 1; signed-commit requirements; fetch by SHA on the real repository; stability of a preview
API.

## Consequences

1. A two-level stack can merge unattended with the bytes checked, without the manual restack.
2. The trust anchor stays the owner's labels. The verifier gains one rule and three optional arguments passed together; the
   workflow writes no label except the existing halt.
3. The train depends on a preview API; the switch and halt-by-default limit the cost of a change.
4. False halts are possible (renames); the cost is a manual restack. A human-merged parent halts its child; the owner
   restacks the child by hand.
5. The lists now include every base, so the train sees stacked children; update-only mode behaves as before.
6. The how-to changes in PR 5, not before. `mkdocs.yml` has a navigation entry after ADR 0036.

## Alternatives considered

Landing dependent work as one PR or restacking by hand remains valid and is what the how-to recommends today. Automatic
restacking by the train is rejected (write access to PR branches, an unreviewed head). A merge queue is not configured;
`merge_action=default` would use one. Skipping the check because GitHub did the rebase is rejected (Decision 1).

## Related decisions

- [ADR 0034](0034-errata-to-adr-0031-and-0033.md) and [ADR 0036](0036-errata-to-adr-0035.md): the form of this record.
- [`docs/how-to/merge-train.md`](../../how-to/merge-train.md): the rules today, and the "Stacked PRs" text PR 5 changes.
- `scripts/train_verify_head.py`, `scripts/train_step.py`, `.github/workflows/update-behind-prs.yml`: the code extended.
