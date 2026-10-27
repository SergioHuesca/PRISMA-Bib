# ADR 0037: The Merge Train and GitHub Native Stacked Pull Requests

## Status

Accepted — 2026-10-27, on the merge of the pull request that adds this record, which the owner approves. Until the
merge, this record was `Proposed` on the branch `docs/adr-0037-train-native-stacks`. Accepting it changes no code, no
workflow and no repository setting: it fixes the design that the pull requests of Decision 9 implement, one at a time.

ADRs 0001 to 0036 are frozen. This record does not edit any of them.

## Context

### What happened on 2026-10-26

The unattended train (`.github/workflows/update-behind-prs.yml`, `scripts/train_step.py`,
`scripts/train_verify_head.py`) merged #182, #183 and #177, then halted at #178, which was stacked on #177. When #177 was
squash-merged, the child's update from `main` conflicted on adjacent hunks (the top `CHANGELOG.md` bullet and appended
tests). The conflict is textual. Rules f and g of the verifier check a merge that exists; they cannot make one. The
how-to (`docs/how-to/merge-train.md`, "Stacked PRs") lists the way out as manual: restack by hand, re-review, relabel, or
land dependent work as one PR.

### What the pilot showed

A throwaway public repository, `SergioHuesca/stacks-pilot`, has squash-only merges, the required check `gates` (strict),
conversation resolution and `enforce_admins`. Three native stacked PRs (`gh-stack` v0.3.0, `gh stack init/add/submit`;
stack number 4, PRs #1, #2, #3) were merged bottom first. The facts below are from the pilot's own history, unless marked
*not verified*.

- After a parent is squash-merged, GitHub retargets the child to `main` and rebases it, in steps. For #2 (parent #1 merged
  01:25:58): `automatic_base_change_succeeded` 01:25:59, `head_ref_force_pushed` 01:26:03. For #3: a force-push alone at
  01:26:04 (after #1 merged; base still the branch of #2), then, after #2 merged at 01:26:59, `automatic_base_change_succeeded`
  at 01:27:00 and `head_ref_force_pushed` at 01:27:04. So for 1 to 5 seconds after a parent merges the child has base
  `main` (or still its parent's branch) and its **old** head. There was no conflict on adjacent hunks. All PRs had one
  commit.
- `gates` ran again on each new head: `check-runs` of the new head of #2 (`fdbd412b…`) and of #3 (`cc2b8739…`) each show
  one `gates` run, `completed`, `success`, started 9 to 10 seconds after the head was created.
- A label survives the merge of its own PR: closed #2 still carries `reviewed:test`, and `GET /pulls/2` returns it. That
  label is not a 40-hex `reviewed:` label, and the pilot says nothing about the labels of an **open** child across its
  parent's merge and rebase. The rehearsal must test this with a 40-hex label (Decision 8).
- `gh pr merge` and `gh pr merge --auto` are refused for a stacked PR ("must be merged using the asynchronous merge REST
  API"). The route that worked is `PUT /repos/{o}/{r}/pulls/{n}/merge-async` with `merge_method=squash` and `sha`, which
  returns `202` and a uuid, followed by `GET /repos/{o}/{r}/pulls/{n}/merge-async/{uuid}` until `merged`.
- Every merge changes the head SHA of every PR above it, with no author push. The new head is the new `main` plus the
  PR's own diff (the tree of #2 matched `main` plus `git cherry-pick -n` of its original commit).
- The rebased commits are unsigned (`verification.verified=false`, `reason=unsigned`). The squash commits on `main` are
  signed by GitHub (`verified=true`, `valid`) and have one parent.
- #3 was rebased twice: first onto the rebased head of #2 (`0d35733f…`, parent `fdbd412b…`), then, after #2 merged, onto
  `main` (`cc2b8739…`, parent `bf51ec84…`, the squash of #2). A head whose parent is another PR's head is a transient
  state. GitHub still serves a force-pushed-away commit by SHA (`GET /commits/0d35733f…` returned it).

### The API surface found (read-only, against the pilot)

| Need | Endpoint and fields | Evidence |
|---|---|---|
| Is this PR in a stack, and where | REST `GET /repos/{o}/{r}/pulls/{n}`, key `stack`: `{"id": 2644774, "number": 4, "position": 1, "size": 3, "base": {"ref": "main", "sha": null}}`. Position 1 is the bottom. | Verified for #1, #2, #3. For a PR that is not stacked the key is **absent** (read-only `GET /repos/SergioHuesca/PRISMA-Bib/pulls/188`: `has("stack")` is false; the reviewer saw `null`, so the train treats absent and `null` alike as "not stacked"). |
| Same, GraphQL | `PullRequest.stack` (`PullRequestStack`: `id`, `number`, `size`, `baseRefName`, `entries(first:N)`) and `PullRequest.stackEntry` (`PullRequestStackEntry`: `id`, `position`, `pullRequest`, `stack`). | Verified by query on #3: entries 1 to 3 with `number`, `state`, `headRefOid`, `baseRefName`, `mergedAt`. |
| The stack's PRs in order | REST `GET /repos/{o}/{r}/stacks` (list) and `GET /repos/{o}/{r}/stacks/{number}`. Fields: `id`, `number`, `node_id`, `base.ref`, `open` (bool), `created_at`, `pull_requests[]` in position order (array order; no `position` field). Keys per entry in `GET /stacks/4`: `base`, `draft`, `head`, `html_url`, `id`, `merged_at`, `node_id`, `number`, `state`, `title`, `url`, `user`. There is **no** `merge_commit_sha` and **no** `labels`. The list form `GET /stacks` is slimmer: `draft`, `head{ref,sha}`, `merged_at`, `number`, `state`. | Verified on stack 4 (`open: false`, three entries in order). The merge commit and the labels need a second call, `GET /pulls/{parent}` (`merge_commit_sha`, `labels`): verified on #2 (`bf51ec84…`, one label). |
| Timeline events | `GET /repos/{o}/{r}/issues/{n}/timeline`: `added_to_stack`, `automatic_base_change_succeeded`, `head_ref_force_pushed` (with `commit_id`), `merged`, `closed`. | Verified on #2 and #3. |
| GitHub finished rebasing a child | No single field. Observable: `baseRefName == main`; `head.sha` differs from the head seen before the parent merged; a `head_ref_force_pushed` event after the parent's `merged_at`; `check-runs` of the new head complete; `mergeStateStatus` is `CLEAN`. | Each item seen in the pilot. A field that says "rebase finished" was not found. |
| Merge a stacked PR | `PUT /repos/{o}/{r}/pulls/{n}/merge-async`, body `merge_method`, `sha`; `merge_action` defaults to `default`. `202` and a uuid. Poll `GET …/merge-async/{uuid}`; status `merged`, `pending`, `enqueued` or `failed`. | The brief's pilot run. This session re-verified only that `GET …/merge-async/<unknown uuid>` is `404` and names `docs.github.com/rest/pulls/pulls#get-the-result-of-an-asynchronous-merge`. |
| `merge-async` on failure, and on a stale `sha` | Response body and status code. | **Not verified.** Writing is not allowed against the pilot. The design treats everything other than `202` on the call, and everything other than `merged` at the end of the poll, as a halt. |
| What happens to a child when the parent's merge fails | State of the child. | **Not verified.** The design acts on one PR per run, and does not touch a child until its parent is `MERGED`. |
| Real repository's protection | `required_status_checks`: strict, `lint`, `fast`, `full`, `docs`, `e2e`; `enforce_admins`: on; `required_signatures`: off; `required_linear_history`: off; no rulesets (so no merge queue). | Read-only `GET /branches/main/protection` and `/rulesets` on `SergioHuesca/PRISMA-Bib`. |

`gh pr list --json` has no stack field in the pilot's `gh`; the train would read `stack` per PR with `gh api`.

### Why the current verifier cannot accept a GitHub-rebased child

The reviewed commit R of the child is not an ancestor of the rebased head H. GitHub replaced the child's commits with new
ones. Rule a halts ("not an ancestor"). Adding a `reviewed:` label for H would mean a human reviews a commit that no
one wrote, every time a parent merges.

## Decision

### 1. The invariant

For a PR that merges, the tree that lands on `main` must equal **the tree of the three-way merge of the current `main`
and the reviewed head, on the base the reviewer reviewed against**, and the merge must be clean. In words: what merges is
the reviewed change plus only `main`'s own changes. If the train cannot prove it, it halts.

For an unstacked PR the base is the merge base with `main`, and rules a to g already enforce this. For a stacked child
whose parent has merged, the base is the reviewed head of the parent. Both cases are the same statement with a different
base. The squash commit's tree equals the tree of H when H is up to date (`strict` is on, so a merge only happens when it
is), so proving `tree(H)` proves what lands.

**Considered.**

- *Trust GitHub's rebase.* Rejected. It makes the train depend on a service's behaviour with no check, and the project's
  rule is that the gate checks bytes.
- *Require a new `reviewed:` label on every rebased head.* Rejected. It is the manual restack this record removes, and it
  asks the reviewer to approve a commit that is main plus an already approved diff.
- *Check that the rebased head's own diff equals the reviewed diff* (`git diff R_parent R_child` against
  `git diff main H`). Rejected as the sole test. It is correct when main did not change the touched files and wrong
  when it did, so it would halt where git merges cleanly. The three-way merge is the exact statement.

### 2. The new verifier rule (rule h)

`scripts/train_verify_head.py` gains an optional argument `--parent-reviewed R_parent`. The workflow passes it only for a
stacked head whose parent is merged (Decision 4). With it, rule h replaces the shortcut `R == H` and rule a.

**Rule h.** Walk the first-parent chain down from H. Two-parent merges at the top are update merges from `main` made after
the rebase. Let T be the first commit with one parent (T is H when there is no update merge). Let the series be the
commits of `main..T` on the first-parent chain, and X the parent of the bottom commit of the series. The head is
accepted iff:

- **h0 (pending, not a verdict).** With `--parent-merge-commit M_parent` (the parent's `merge_commit_sha`): M_parent must
  be an ancestor of `origin/main`, or the verifier halts. If M_parent is **not** an ancestor of H, GitHub has not yet
  rebased the child onto a `main` that holds its parent: the old head, or the transient head of #3 above (`0d35733f…`
  holds the squash of #1 but not of #2). The verifier then prints
  `{"ok": false, "pending": true, "reason": "..."}` and exits 0. It does not look at h1 to h5. `decide` turns this into
  a wait (Decision 3), never into a merge. When M_parent is an ancestor of H, `pending` is never emitted.
- **h1 (shape).** The series is not empty (H is not already on `main`), every commit of the series has exactly one parent
  (so a merge commit inside the rebased series halts), the bottom commit has a parent (a root commit has none to take X
  from: halt), X is an ancestor of `origin/main`, and the series has at most 500 commits. X is an ancestor, not the tip:
  `main` may have moved between GitHub's rebase and the train's look. The number of rebased commits is not fixed, because
  only the tree is compared.
- **h2 (relation to the review).** `R_parent` is a proper ancestor of `R_child`. The child was cut from the parent's
  reviewed head.
- **h3 (content).** `tree(T)` equals the tree written by
  `git merge-tree --write-tree --merge-base=R_parent X R_child`, run in the same pinned environment as rule g: global and
  system config off, `merge.renames=false`, `GIT_ATTR_SOURCE` set to the empty tree, `core.attributesFile` off,
  `XDG_CONFIG_HOME` empty, `diff.*` options pinned, git 2.40 or later. The result is `X + (R_child - R_parent)`, which is
  the `main` that GitHub rebased onto, plus the reviewed change.
- **h4 (no conflict).** That merge exits 0. Exit 1 (conflicts) halts.
- **h5 (update merges).** Each two-parent merge above T is checked by the full existing verifier, `verify(T, H)` (rules a to
  g with T as the reviewed commit). Rule h alone returns ok for an honest T under an evil update merge; only `verify(T, H)`
  rejects it. The hand-off is therefore part of the rule, has its own named test and its own mutation (Decision 8), and
  `train_verify_head.py` returns ok for a stacked head only when h1 to h5 all hold.

Rule h anchors the chain once at T; the existing rules carry it forward to the head. X, not the tip of `main`, is
"ours" in h3, so the proof does not depend on where `main` is when the train looks.

**No re-binding of the label.** The label keeps `R_child`, the commit the owner reviewed. The verifier proves H from three
inputs it can authenticate: `R_child` (the child's label), `R_parent` (the parent's label) and `main`. If GitHub rebases
again (the grandchild moves when a middle PR merges), H changes, and the proof is run again from the same inputs. Nothing
is written back.

**How `R_parent` and `M_parent` are obtained and authenticated.** Both come from GitHub's records, never from the
child's branch or history.

1. `GET /pulls/{child}` gives `stack.position`. `GET /stacks/{number}` lists the entries in array order. The train checks
   that the entry at `position - 1` (zero-based) is the child and the entry before it is the parent. Entries carry no
   position, labels or merge commit.
2. `GET /pulls/{parent}` gives `merged`, `merge_commit_sha` (M_parent) and `labels`. The parent must be merged, and M_parent
   must be an ancestor of `origin/main`. Otherwise halt.
3. `R_parent` is the single valid `reviewed:<40 hex>` label of the closed parent. A missing or malformed label halts.
4. **The label is checked against the parent's own history.** A label alone is not enough: a parent merged outside the train
   can carry a stale label that passes "one valid label". The train therefore re-derives it. It fetches the parent's final
   head (`head.sha` of the stack entry) and requires `verify(R_parent, parent_final_head)` to pass: rules a to g for the
   bottom of the stack, rule h with the grandparent's label and merge commit for a middle one (recursion; depth at most
   the stack size, capped at 10, deeper halts). It also requires M_parent's tree to equal git's merge of the parent's final
   head onto the parent of M_parent (the audit computation of Decision 3). A label that was edited after the merge, or a
   label on a parent the train did not merge, fails here. *That the objects of a closed PR remain fetchable
   (`pull/<parent>/head`) was not verified; an unfetchable object halts.* The cost is one extra verification per stack
   level, run on `main`'s code.
5. Label write access is owner-only (existing confirmation 2). The recursion protects against a stale or wrong label, not
   against an owner who writes false labels; for that the whole gate is already open.

Alternative considered for item 4: record the verified `R_parent` in the squash commit message. Rejected: it needs a commit
message parameter on `merge-async` that was not verified, and it would make the message a trust anchor.

**What a wrong `R_parent` can and cannot do.** The merge in h3 draws every byte of its result from two commits, X (`main`)
and `R_child` (the reviewed child), as git's three-way merge. So no unreviewed byte can enter the result, whatever
`R_parent` is. What `R_parent` decides is the *base*: which differences between the base and `main` count as "main's
changes win". With an `R_parent` that is older than the true one, an `H'` that re-adds a line that `main` had deliberately
reverted after the true `R_parent` is accepted (the reviewer's `s3.py` showed this: accepted with `R_parent` an old
commit, rejected with the true one). That is why `R_parent` must be the right commit, and why item 4 and h2 exist. h2
(`R_parent` is a proper ancestor of `R_child`) bounds the base to the child's own ancestry. A child that holds a different
version of its parent (the parent changed after the child was cut) fails h2 and halts.

**Cases.**

- *First PR of a stack (position 1).* There is no parent and no rebase. The head is verified by the existing rules a to g
  against its own label. `--parent-reviewed` is not passed. It still merges through `merge-async`, because `gh pr merge`
  is refused for any PR with a non-null `stack` (*refusal at position 1 not verified; the design does not rely on
  `gh pr merge` there*).
- *Multi-level stack (C after B after A).* Only the immediate parent is needed. When A merges, B is rebased onto `main`
  and C onto the new B. When B merges, C is rebased onto `main`; `R_parent` is B's label. `main` already holds A and B, so
  `R_child - R_parent` is C's own change. C is never merged while its parent is not `MERGED`.
- *Only part of the stack is in the batch.* If the head's parent is merged, rule h applies whether or not the parent was
  in this batch. If the head's parent is open, the train halts (Decision 5). A child outside the batch is not acted on;
  GitHub still rebases it, and it waits for a later batch.
- *Main moved again after the rebase (other batch PRs merged in between).* GitHub rebased onto the `main` of that moment,
  X. h1 accepts any ancestor of the current `main`, and h3 uses X, so the proof still holds. The head is `BEHIND`, the
  train updates it as today, and h5 verifies the update merge. A head whose X is not on `main` halts.
- *GitHub's rebase differs from git's merge.* The failure to expect is a **conflict** in h3 (exit 1, h4), not a tree
  difference: a child that renames a file that `main` also edits, or renames and edits it, conflicts under our rename-less
  merge where GitHub's rename-aware rebase succeeds (the reviewer's fuzz of 300 cases found no case where the trees
  differed). These are false halts, not holes; the owner restacks by hand as today. Not false halts: a child that edits the
  parent's lines, deletes a file, changes a mode, adds an executable file, or has three commits. How GitHub rebases a
  **multi-commit** child, and a child with a merge commit in its series, is *not verified* (the pilot had one commit per
  PR). A merge commit in the series halts at h1.
- *`H == R_child`* for a child with a merged parent (GitHub has not rebased yet). Rule h does not take the `R == H`
  shortcut. M_parent is not an ancestor of H, so the verifier prints `pending` (h0) and `decide` waits.

**Considered.**

- *Re-bind: the train writes `reviewed:<H>` after verifying H.* Rejected. A write path from the workflow to the trust
  anchor makes the anchor only as strong as the workflow, and it must run again after every rebase.
- *Use the merge base of `main` and H as the base.* Rejected. It is chosen by the history of H, which GitHub wrote but
  which an author could shape by pushing before the rebase.
- *Rebase the child ourselves, in the train.* Rejected. The train would hold a write path to a PR branch, and a force push
  by the App would re-trigger checks on a head nobody reviewed. The train never creates a head.
- *Compare patch ids (`git patch-id`) of the rebased commits to the reviewed ones.* Rejected. Patch ids ignore context, so
  a conflict resolution that changes surrounding lines would pass.

### 3. What the train sees, when it waits, and the merge call

**3.1 Visibility.** The workflow lists open PRs with `gh pr list --base main` and closed ones likewise. A native child's
base is its parent's **branch** until GitHub retargets it. With A(`train:1`), B(2), C(3) and an independent D(4), the
train would see only A and D while A is open. After A merges, B and C stay invisible until GitHub retargets them (1 to 5
seconds in the pilot), and `decide` would take D, the lowest listed label, as head and merge it ahead of B and C. The gap
check starts at the head's number, so it would not notice. The closed-unmerged check has the same blind spot. This breaks
"never merges out of order". The fix:

- Both lists drop `--base main` and add `baseRefName` to `--json`. The train head is the lowest `train:N` among **all**
  open PRs, whatever their base. The 100-PR cap now applies to the unfiltered list, and a full list still halts.
- Update-only mode keeps its contract ("the lowest-numbered open PR targeting main"): the filter on `baseRefName == main`
  moves from the query into the call to `select_next_pr`, so that mode behaves as before.
- A train head whose base is not `main` and that is **not** stacked halts. A stacked head whose base is not `main` while
  its parent is merged waits (retarget pending, 3.2). A stacked head whose parent is open halts (Decision 5).
- Stack-aware completeness, as a second layer: for a stacked head, every entry of its stack record that is open must
  appear in the listed PRs, else halt ("a stack member is not visible"). This catches a list cut by the cap or by an
  eventually consistent search.

**3.2 The transient state of a child (liveness).** In the seconds after a parent merges, the child has base `main` (or
still the parent's branch) and its old head, and its old head fails the verifier. `_head` halts as soon as the verifier's
result is not ok, before it looks at `mergeStateStatus` or the checks, so the "wait" row of the halt table would become a
sticky halt: the train is stateless and cannot know "the head is unchanged since before the parent merged". The predicate
that needs no state is derived from git and the parent's record:

- **pending** is true iff the parent is merged and the parent's `merge_commit_sha` (M_parent) is not an ancestor of H.
  Before GitHub's rebase H is the old head, or a head rebased onto the parent's old branch; neither holds M_parent. After
  the rebase H descends from a `main` that holds M_parent. The verifier computes it (rule h0) and prints
  `{"ok": false, "pending": true, "reason": "..."}`, exit 0. `train_step.py` carries `pending` through its head-check
  wrapper (today it keeps only `ok` and `reason`).
- `decide` order for a train head: draft halt; label halt; **pending or base not `main` with a merged parent: wait**;
  then verifier result; then checks and `mergeStateStatus`. Pending is evaluated before the verifier's `ok`.
- The wait is bounded without state: `decide` receives the parent's `merged_at` and the current time as inputs (`now` is
  injected, so tests fix it). If `now - merged_at` exceeds 10 minutes (the pilot took 5 seconds) the answer is halt
  ("GitHub did not rebase the child"). Pending can only produce a wait or a halt, never a merge.
- What re-triggers the evaluation: GitHub's force-push starts the child's checks, whose completion is a `workflow_run`
  trigger. Whether it does when the App performed the merge is *not verified*, so PR 4 adds a `schedule` trigger (every 15
  minutes) so that a wait cannot stall and the 10-minute limit can fire. The decision rules do not change; in update-only
  mode the same trigger applies the same rule.

**3.3 The merge call.** For a stacked head that passes all the checks of `train_step.py` (not draft, `CLEAN`, every check
passing on the **new** head, one valid label, rule h or rules a to g), the train makes these calls:

1. `GET /pulls/{n}`. If `merged` is true, stop with success (idempotency). If `head.sha` differs from the SHA the decision
   was made on, stop with `wait`: the head moved since the decision.
2. `PUT /repos/{o}/{r}/pulls/{n}/merge-async` with `merge_method=squash` and `sha=<the verified head>`. The `sha` is always
   passed, because omitting it fills in the current head and is not a match check. `merge_action` is not set.
   **Hypothesis to be proven, not a fact:** that a `sha` which is not the current head is rejected. The pilot run
   established that a correct `sha` is accepted and that an omitted one is filled in; the response to a stale `sha` was
   not observed. Until the rehearsal proves it (a deliberately stale `sha` on a throwaway PR, response recorded), the
   `sha` is not counted as closing the race between the verification and the call. That proof is an explicit gate of the
   first PR that contains a `merge-async` call (Decision 9). If a stale `sha` is accepted, the native path does not ship
   and this record is amended.
3. Anything but `202` with a uuid halts.
4. Poll `GET …/merge-async/{uuid}` every 3 seconds, up to 180 seconds (the pilot's rebase took 5 to 10 seconds). `pending`
   and `enqueued` keep polling (this repository has no merge queue, so `enqueued` is unexpected but not an error). `merged`
   succeeds. `failed`, an unknown status or a non-2xx poll halts. On timeout the train reads `GET /pulls/{n}` once more
   after 60 seconds: merged goes to the audit, otherwise halt.
5. **Audit** (detective, after the irreversible merge). Let S be the PR's `merge_commit_sha` and P_S its single parent. The
   audit recomputes git's merge of the verified head H onto P_S in the pinned environment (base = merge base of P_S and H)
   and requires `tree(S)` to equal it. The earlier wording "the parent was the `main` tip the train saw" is dropped: `main`
   can move legitimately while the train polls, which would raise a false alarm. (With `strict` on, GitHub refuses to merge
   a head that is behind `main`, so a late move shows as `failed`, not as a mismatch.)
6. **Stop on a failed audit.** The audit result is an input of `decide` (`--audit FILE`). A failed audit makes `decide`
   return halt on the head, the lowest open `train:N`, and the existing `halt()` labels it `train:halted` and comments.
   Every later run does nothing until the owner removes the label. The audit runs in the same run as the merge, before the
   `concurrency` group lets the next run start, so the successor cannot be acted on first. With no open successor nothing
   is running, and the job fails with `::error::`. The audit also runs at the **start of every run** for the most recent
   merged PR that has a `reviewed:` label (a second listing, `--state merged`, `mergedAt`, `mergeCommit`), so a merge that
   completed after a timeout, or a run cancelled between the merge and its audit, is audited at the next trigger. It is
   idempotent. A stronger stop, writing `MERGE_TRAIN=off` as a repository variable, would need the App to hold the
   `Variables: write` repository permission, which lets the unattended job change any Actions variable. It is not
   recommended; it is an owner question (Decision 10).

**The train never merges a child before GitHub finished rebasing it and `gates` is green on the new head.** The decision
reads the PR fresh each run, so it sees the new head. A child whose parent just merged is in one of these states: pending
or base not yet `main` (wait, 3.2); head changed and checks running (wait); head changed, green and `CLEAN` (rule h, then
merge). The residual race between the decision and the call is closed by the `sha` only once the hypothesis of step 2 is
proven; until then it is open and detected by the audit.

**Idempotency on re-dispatch.** Runs are serialised by the workflow's `concurrency` group (`cancel-in-progress: false`), so
a second dispatch waits. If a run is cancelled between the `202` and the end of the poll, the next run reads the PR: merged
means done (and the start-of-run audit covers it); open with the same head means the train repeats step 2 with the same
`sha`. *Whether GitHub rejects a second `merge-async` while one is in flight was not verified*; if it accepts both, the
second call on a merged PR returns an error and halts a PR that is already merged, which is cleaned up by hand. The train
does not store the uuid.

**Considered.** *Polling the PR instead of the uuid* (check `merged` every few seconds): simpler, no dependence on the
result shape, but loses the `failed` signal and waits out the timeout on failure. Recommended as the fallback if the result
shape of `merge-async` turns out unstable: poll both.

### 4. Workflow changes

- **Which path.** The decision step stays pure (`train_step.py`). It receives, for the head only, the `stack` object (or
  nothing). `stack` absent or null: the existing `gh pr merge --squash --match-head-commit` path, unchanged. `stack`
  non-null: the native path (`merge-async`). Any read of `stack` that fails to parse halts. A repository variable
  `MERGE_TRAIN_STACKS` must be `on` for the native path; otherwise a stacked head halts with a message that names the
  variable. The variable is the rollback (Decision 7).
- **Which verifier call.** For a stacked head with a merged parent: `train_verify_head.py <R_child> <H> --parent-reviewed
  <R_parent> --parent-merge-commit <M_parent>`. For position 1 and for unstacked heads: unchanged. Reading `R_parent` and
  `M_parent` takes `GET /pulls/{n}`, `GET /stacks/{number}` and `GET /pulls/{parent}` (Decision 2).
- **Fetching.** The workflow fetches `main`, `pull/<n>/head` and, by SHA, `R_child` and `R_parent`. `R_child` is no longer
  on any ref after the rebase. GitHub served a force-pushed-away commit through the REST API in the pilot; that
  `git fetch origin <sha>` works for it was **not verified** (no network except `gh api`). If it does not work, the
  fallback is to fetch `pull/<child>/head` before the parent merges and keep it, which an unattended run cannot do, so the
  rehearsal must settle this first. An unfetchable `R_child` halts ("reviewed commit is not in this repository").
- **Token.** The existing App token has `contents: write` and `pull-requests: write`, and `gh pr merge` has merged
  unattended under `enforce_admins` (the App is not a bypass actor). Whether `merge-async` accepts the same token and
  scopes must be verified in the real repository; the REST docs for the endpoint were not reachable here. The workflow's
  job permissions (`contents: read`) and the token step do not change if that holds. If the endpoint needs another
  permission, adding it is an owner action on the App, and the rehearsal finds this before the real workflow changes.
- **List queries** (3.1): both lists drop `--base main` and add `baseRefName`; a third query lists the last merged PR for the
  start-of-run audit.
- **Trigger**: a `schedule` (every 15 minutes), for the wait and its limit (3.2). Open question for the owner.
- **No other changes** to `concurrency`, checkout (no `ref:`), or the rule that no PR code runs in this job.

**Considered.** *Always use `merge-async`, also for unstacked PRs.* Rejected. The unstacked path has run in production
since 2026-10-26. A second route for every PR doubles the surface to rehearse. *A separate workflow for stacks.* Rejected.
Two workflows would each act on the head, and the single `concurrency` group and the single halt label are what keep one
PR moving at a time.

### 5. Labels and ordering

- `train:N` for stack members follows stack order, bottom first. For every open batch PR whose `stack` is non-null, the
  train compares the order of `train:N` with the stack order (the stack's array order). Any inversion halts ("train order
  disagrees with stack order"). The train does not renumber.
- A stacked head whose parent (position - 1) is **open** halts: the child must not merge before the parent. (That GitHub
  refuses a child-before-parent merge was not verified; the train does not rely on it.) A parent that is **closed unmerged** halts: the existing closed-unmerged rule covers a batch member below the
  head, and the new rule also covers a non-batch parent. The stack record for a stack with a closed-unmerged member is not
  specified by anything read here (*not verified*), so any entry of a stack that is neither `MERGED` below the head nor
  open above it halts.
- A stack member not in the batch, above the head, is ignored. One in the batch below the head, closed unmerged, halts as
  today.
- Every batch member keeps its own `reviewed:<R>` label, taken after the owner reviewed the commit at its position. The
  label of a child names the child's own commit, as the how-to says.
- The `reviewed:` label of a **merged** parent must stay on the closed PR. The cleanup step of the how-to must not remove
  labels from merged PRs. This is stated in the how-to by PR 5.

### 6. Signing

The rebased heads are unsigned, and the repository does not require signed commits (`required_signatures` is off). The
squash commits on `main` are signed by GitHub, whether the head was signed or not (pilot). If the repository ever requires
signed commits, the question is whether a squash merge of an unsigned head is accepted; that was **not verified** and is an
open question. If it is refused, a native stack cannot merge under that rule and the feature is turned off; the train
would not sign or re-push anything.

### 7. Failure modes, rollback, and what the train never does

| Condition | Action |
|---|---|
| `stack` unreadable, or `MERGE_TRAIN_STACKS` not `on` | halt |
| `train:N` order differs from stack order | halt |
| Head's parent open, closed unmerged, or not in the stack record | halt |
| Parent's `reviewed:` label missing or malformed | halt |
| `R_parent` or `R_child` unfetchable | halt |
| Rule h fails (h1 to h5) | halt, with the failing clause |
| Pending (M_parent not an ancestor of H), or base not `main` with a merged parent, for 10 minutes or less | wait |
| Pending or base not `main` for more than 10 minutes after the parent's `merged_at` | halt |
| Head's base is not `main` and the PR is not stacked | halt |
| A stack member is not in the listed PRs (completeness) | halt |
| Checks running on the new head | wait |
| Head `BEHIND` after the rebase | update-branch as today, then verify (*update-branch on a stacked child was not tested*) |
| `merge-async` not `202`, status `failed`, unknown status, timeout | halt |
| Post-merge audit mismatch | `train:halted` on the lowest open `train:N`, job fails |
| `merge-async` for a PR already merged | success (idempotent) |

**Rollback.** `gh variable set MERGE_TRAIN_STACKS --body off` (any value other than `on` is off) makes stacked heads halt
instead of merge. `MERGE_TRAIN=off` stops the whole train, as today. The code paths are inert while the variable is off.

**The train never** merges a stacked PR with `gh pr merge`, calls `merge-async` without `sha`, merges a head that rule h or
rules a to g did not accept, creates, rebases, retargets or force-pushes a PR branch, edits a `reviewed:` label, calls
`unstack`, uses `merge_action` other than the default, retries a failed merge, skips a PR, acts on a PR other than the
head, merges while `pending`, filters the PR lists by base, or writes a repository variable.

### 8. Testing

The existing verifier tests are `tests/integration/test_train_verify_head*.py` (four files) and the decision tests are
`tests/unit/test_train_step.py`; the new files sit next to them. All verifier tests use real temporary git repositories.
Nothing mocks `git`. A helper simulates GitHub's rebase: for a child with
commits `c1..ck` on parent branch `P`, after the squash of `P` onto `main`, it builds H by `cherry-pick` of `c1..ck` onto
`main`, with committer data fixed so the commit is deterministic.

- **Unit** (`tests/unit/test_train_step_stack.py`): decision logic with a `stack` object per PR: path selection,
  `MERGE_TRAIN_STACKS` off, order mismatch, open or closed-unmerged parent, unparseable `stack`, `merge_stack` action.
  Malformed `stack` raises `InputError` (exit 2). Added by this review:
  - **Visibility**: A(1) merged, B(2) and C(3) with a base that is not `main`, independent D(4): the decision is wait on B,
    never a merge of D. The same list with the `baseRefName == main` filter applied first makes D the head: that is the
    mutation, and the test fails on it. The same for the closed list. A list in which a stack member is missing halts.
  - **Transient child**: parent merged, child with base `main`, head equal to `R_child`, verifier output `pending`: wait,
    not halt. The same with `now - merged_at` over 10 minutes: halt. The transient head of #3 (`0d35733f…` shape: holds
    the grandparent's squash but not the parent's): wait. `pending` with `ok: true` is impossible and raises.
  - **Audit**: a failed `--audit` makes the head halt even when every other input says merge.
- **Unit** (merge client, `scripts/train_merge_async.py`, with an injected runner in place of `gh`): `sha` always present,
  `202` handling, polling states, timeout, already merged, post-merge audit.
- **Integration** (`tests/integration/test_train_verify_head_native_stack.py`): accepted cases: a two-level and a three-level
  stack, a multi-commit child (three commits), a child that deletes a file, changes a mode, or adds an executable, `main` moved after the rebase with an update merge on top, a child whose edits are adjacent to
  the parent's (the 2026-10-26 case, built from the real `CHANGELOG.md` shape). Attacks, each must halt:
  wrong base (`R_parent` older than the parent's reviewed head; `R_parent` not an ancestor of `R_child`), forged parent label
  (label of another PR, of a PR of a different stack, of an unmerged parent), extra bytes in H, a missing `main` change in H,
  a conflict in the merge, rename divergence (the child renames a file that `main` also edits: git's rename-less merge and
  GitHub's cherry-pick disagree), a merge of a different stack's parent (squash of another stack's commit on `main`), a
  two-parent H that is not an update merge, H whose parent is not on `main`, a rebased child that still holds a merge
  commit in its series, a series with a root commit, a rename of a file that `main` also edits (h4 conflict), `R_parent`
  older than the true one with a line `main` reverted (accepted by h3 alone; must be rejected by the parent re-derivation of
  Decision 2 item 4), a stale label on a parent merged outside the train, and `H == R_child` with a merged parent
  (`pending`, not ok). The **combined path** has a named test,
  `test_stack__rule_h_passes_but_update_merge_is_evil__halts`: an honest T under an update merge that adds a byte; h1 to h4
  pass and `verify(T, H)` rejects it. Its pair accepts an honest update merge on top of T.
- **Mutation checks**, each must fail at least one test: replace `R_parent` by the merge base of `main` and H; drop h2;
  drop h4 (accept `merge-tree` exit 1); compare against `R_parent`'s tree instead of the merge; use `main`'s tip where the
  rule needs X; keep the `R == H` shortcut for stacked heads; omit `--no-renames` or the attribute pinning; **skip
  `verify(T, H)` for update merges (h5)**; **accept an `R_parent` that is not derived from the parent's final head**; **drop
  the list-query fix** (filter by `--base main` again); evaluate the verifier result before `pending`; drop the 10-minute
  limit; compute the audit from the tip the train saw instead of from P_S.
- **Run as CI runs** (`pytest -n auto`) and single-process, on at least two machines or the Windows NTFS mount and a Linux
  runner. The rule uses git behaviour (`merge-tree`, cherry-pick) that is a version-inference risk; the test pins git
  2.40 or later.
- **Rehearsal on the pilot.** The pilot has no train. The rehearsal needs: the scripts and a copy of the workflow on the
  pilot's `main`; the GitHub App (or a second one) installed on the pilot with the same two permissions, because the
  rebase push may behave differently when the App merges (does `gates` still run on it); `MERGE_TRAIN=on` and
  `MERGE_TRAIN_STACKS=on`; the labels `train:1..3`, `train:halted`, `reviewed:<sha>`; a new three-PR stack made with
  `gh stack`, including one pair that edits adjacent hunks; a run of the workflow by dispatch. It must also test: a
  deliberately wrong parent label, **a stale `sha` (the gate of Decision 9)**, a failing parent, `update-branch` on a
  rebased child, a fetch by SHA of `R_child` and of a closed parent's head, whether a `merge-async` repeated during a flight
  is rejected, a **40-hex `reviewed:` label on an open child across its parent's merge and rebase**, a **multi-commit
  child** and a **child with a merge commit in its series** (how GitHub rebases each), the retarget window with an
  independent PR holding a higher `train:N` (the out-of-order case), the merge by the App token (checks re-run, `merge-async`
  accepted under `enforce_admins`), and a deliberately failed audit (the stop). Each observed response is written into the
  how-to.

### 9. Rollout

Each pull request is green alone, reviewed at its head, and changes nothing the train does until PR 4.

1. **PR 1, verifier.** Rule h and `--parent-reviewed` in `scripts/train_verify_head.py`, the integration tests and mutation
   checks of Decision 8, the docstring. No caller passes the argument. Gate: the five required checks, Copilot
   conversations resolved, and a re-run of the mutation list by the reviewer.
2. **PR 2, decision.** The `stack` input, path selection, order and parent checks, `baseRefName` handling (an absent field
   means `main` until PR 4 makes it required), the visibility and completeness rules, the pending wait with its injected
   `now`, the `--audit` input, and the new `merge_stack` action in `scripts/train_step.py`, with the unit tests of Decision
   8. The workflow is not changed, so the listing is still filtered and the new action is ignored. Gate: as PR 1.
3. **PR 3, merge client.** `scripts/train_merge_async.py` (the call, the poll, the audit computation) with its unit tests,
   not called by the workflow. It is the first PR with a `merge-async` call. **Gate: the rehearsal on the pilot has
   proven, with the response recorded, that a stale `sha` is rejected by `merge-async`.** If it is not rejected, this
   PR is not merged and the record is amended.
4. **Rehearsal on the pilot** (not a PR to this repository). Runs before PR 3 is merged, with the PR 1 to 3 scripts copied
   to the pilot. Settles the unverified items. The owner reads the written results.
5. **PR 4, workflow.** Wiring behind `MERGE_TRAIN_STACKS` (default off): **both list queries without `--base main` and with
   `baseRefName` (F1), the stateless wait and its `schedule` trigger (F2)**, the fetch of `R_child`, `R_parent` and the
   parent's final head, the native merge call, the start-of-run audit and its stop. Only this PR touches the workflow. Gate:
   as PR 1, plus the rehearsal results, plus the owner's check of the token (Decision 4) before the merge.
6. **PR 5, documents.** The how-to ("Stacked PRs", the halt table, the remove-labels warning), `CHANGELOG.md`. The
   "Not built" paragraph on native stacks is replaced by what the rehearsal found.
7. **First live use.** A two-PR stack with `MERGE_TRAIN_STACKS=on`, owner present. Afterwards the variable stays `on` or is
   set back.

### 10. Open questions for the owner, and risks not verified

Questions:

1. May the owner enable `gh-stack` / native stacks on the real repository (the pilot's preview status is not confirmed
   for it)?
2. Does the owner accept a false halt when GitHub's rebase and git's merge disagree (restack by hand as today)?
3. Is a second App or the same App on the pilot acceptable for the rehearsal?
4. Should a stacked PR's `reviewed:` label be taken at the child's own commit (as designed) or after the parent merged?
5. May the App be given `Variables: write` so that a failed audit can set `MERGE_TRAIN=off`? The design says no and uses the
   `train:halted` label on the successor.
6. Is a `schedule` trigger (every 15 minutes) acceptable?

Not verified (all would be settled by the rehearsal):

- `merge-async` failure body and status, **stale-`sha` response** (a gate, Decision 9), repeat-call behaviour.
- How GitHub rebases a **multi-commit** child and a child with a **merge commit** in its series (the pilot had one commit per
  PR).
- Whether a 40-hex `reviewed:` label of an open child survives its parent's merge and the rebase.
- Whether the objects of a closed parent (`pull/<parent>/head`) can be fetched, for the re-derivation of `R_parent`.
- Whether the `schedule` trigger is needed (whether an App-triggered rebase starts the checks that start the `workflow_run`).
- What a child does when its parent's merge fails.
- Whether the App token and its two permissions may call `merge-async` under `enforce_admins`.
- Whether `gates`/`lint`/`fast`/`full`/`docs`/`e2e` re-run on a rebase that was triggered by an App, as they did when a user
  merged in the pilot.
- `git fetch origin <sha>` of a force-pushed-away commit.
- Whether `gh pr merge` works at position 1. (The shape of `stack` for a PR that is not stacked **is** verified: the key is
  absent on PRISMA-Bib #188.)
- `update-branch` on a rebased child, and the stack record of a closed-unmerged member.
- Whether signed commits would block a native stack.
- Whether the `stack` field and the endpoints stay stable: they are a public preview.

## Consequences

1. A stack of dependent PRs can merge unattended, with the bytes checked, and without the manual restack of the how-to.
2. The trust anchor stays the owner's labels. The verifier gains one rule and one optional argument. No label is written by
   the workflow.
3. The train depends on a preview API. The feature switch and the halt-by-default behaviour limit the cost of a change in it.
4. A false halt is possible when git and GitHub disagree; the cost is a manual restack, as today.
5. The how-to gains the rows of Decision 7 in PR 5; the "Stacked PRs" text changes then, not before.
6. `mkdocs.yml` gains a navigation entry for this record, after the entry for ADR 0036.

## Alternatives considered

**Land dependent work as one PR, or restack by hand between batches.** Remains valid and is what the how-to recommends
today. Not enough alone: it asks the author to know in advance which PRs depend on which.

**Automatic restacking by the train** (rebase the child onto `main` after the parent merges, then ask for a new
review). Rejected: write access to PR branches from the unattended job, and a head that nobody reviewed.

**Merge queue.** Not configured (no rulesets). `merge_action=default` would use one if it were. A queue does not remove the
rebase of a child; it is a separate decision.

**Skip the check on stacked PRs because GitHub did the rebase.** Rejected (Decision 1).

## Related decisions

- [ADR 0034](0034-errata-to-adr-0031-and-0033.md) and [ADR 0036](0036-errata-to-adr-0035.md): the form of this record.
- [`docs/how-to/merge-train.md`](../../how-to/merge-train.md): the train's rules today, and the "Stacked PRs" text that
  PR 5 changes.
- `scripts/train_verify_head.py`, `scripts/train_step.py`, `.github/workflows/update-behind-prs.yml`: the code this record
  extends.
