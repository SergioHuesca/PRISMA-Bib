# ADR 0006: Public Repository and Single-Owner Review


## Context

BUILD_PLAN.md §3.6 specifies two governance requirements:

1. **Visibility (§3.6.1)**: "Private is the safe default while the corpus methodology is unpublished; it can be flipped to public at `v1.0.0`."

2. **Branch protection (§3.6.3)**: "Require a pull request before merging, with at least one approving review."

On a GitHub free-tier account, these requirements conflict with a third constraint: the account is sole-owner.

### The constraint

GitHub free-tier branch protection and rulesets are **unavailable on private repositories**. This was verified by attempting to:

- POST to `/repos/{owner}/{repo}/branches/main/protection` → HTTP 403 "Upgrade to GitHub Pro or make this repository public"
- POST to `/repos/{owner}/{repo}/rulesets` → HTTP 403 "Upgrade to GitHub Pro or make this repository public"
- Enable secret scanning → HTTP 422 "Secret scanning is not available for this repository" (private only; free tier requires public)

These protections are mandatory per §3.6 because:
- Branch protection prevents accidental `--force` pushes and deletions
- Rulesets enable granular protection (e.g., dismissing stale reviews)
- Secret scanning + push protection is the second line of defense after `detect-secrets`

A private repository on free tier can enforce none of them without upgrading to GitHub Pro ($4/month).

### The sole-owner problem

GitHub's review model forbids an account owner from approving their own PR. This is intentional—it prevents a single actor from bypassing review by self-approving. Consequently, a sole-owner repository **cannot satisfy** the requirement "require at least one approving review" because:

- The owner opens a PR
- The owner cannot approve their own PR
- No second account exists to approve
- The PR is blocked forever

This is not a GitHub quirk—it is a security property. However, it means the requirement and the sole-owner constraint are mathematically unsatisfiable.

## Decision

The project goes **public in Stage 0** to enable branch protection, rulesets, and secret scanning. The **approving review requirement is disabled** until a second maintainer joins. All other branch protections are active and enforced.

This is a staged approach:

| Scenario | Visibility | Approval Required? | Why |
| --- | --- | --- | --- |
| Stage 0 (sole owner) | Public | No (disabled in settings) | Free tier branch protection available; owner cannot self-approve |
| v1.0+ with second maintainer | Public | Yes (re-enable) | Both protection and approval become possible |
| Future: upgrade to Pro | Private or public (choice) | Yes | Pro unlocks private-repo protection |

**Justification for public:**
- The methodology is frozen in BUILD_PLAN.md (§1.2 "not open for renegotiation"), and the system does not yet handle any real data
- History was audited before the flip: one root commit, zero payloads tracked
- Public repository enables full GitHub security features on free tier
- Public is transparent—it clarifies that this is a reference implementation, not a commercial system
- The LICENCE and CONTRIBUTING.md make data constraints explicit for any user

## Status

**Accepted.** This decision is irreversible (history is public), so reversing it would require starting a new repository. The decision is recorded here for clarity, not reconsideration.

## Consequences

### What is protected

Every item below was read back from
`GET /repos/SergioHuesca/PRISMA-Bib/branches/main/protection` rather than from intent,
and can be re-verified with that call:

- **Required status checks** (§3.6.3): `lint`, `fast`, `full`, `docs`. A red check blocks
  merge, and because `enforce_admins: true` the owner cannot override it either.
- **Up-to-date branches** (`strict: true`): a PR branch must be current with `main` before
  merging, so a stale branch cannot merge on the strength of a check that ran against an
  older base.
- **Conversation resolution**: all PR comments must be resolved before merge.
- **Force-push and deletion blocks**: `main` cannot be force-pushed or deleted, by anyone.
- **Enforce for admins** (`enforce_admins: true`): the owner cannot bypass the above.

### What is not protected

Stated plainly, because a governance document that overstates its own guarantees is worse
than none — a future reader would take an unenforced rule for an enforced one.

- **Approving review** — `required_approving_review_count: 0`. Nobody has to approve a PR
  before it merges. The owner can open and squash-merge unilaterally, subject only to the
  status checks above.
- **Stale-review dismissal** — `dismiss_stale_reviews: false`. Moot while approvals are not
  required; it would need enabling alongside any future approval requirement.
- **Code-owner review** — `require_code_owner_reviews: false`. `.github/CODEOWNERS` exists
  and GitHub will *auto-request* review on `src/prismabib/prisma/**` and
  `docs/methodology/**`, but nothing *blocks* a merge without it. §3.6.2 wanted those paths
  gated by a required reviewer; on a sole-owner repository they are not. There is no CI
  workflow enforcing code ownership either — do not read the auto-request as a gate.
- **Re-enabled when:** a second maintainer joins. Then
  `required_approving_review_count: 1`, `require_code_owner_reviews: true`, and
  `dismiss_stale_reviews: true` all become satisfiable, and §3.6.3 can be restored verbatim
  by one `gh api` call. Until then, the honest summary is that **CI, not human review, is
  the only thing gating `main`.**

- **GitHub secret scanning on a sole account**: Secret scanning is available (it became available on going public), but it is a *reactive* check—it flags secrets in push protection but does not prevent the push if the secret is already in a committed history. This is why `detect-secrets` in pre-commit is the primary defense; GitHub is the second line.

### What to know if you are a contributor

The project has:
- The branch protections listed under "What is protected" — and only those. Read the
  "What is not protected" list before assuming a rule from BUILD_PLAN §3.6 is in force.
- No required human review. A green CI run is the whole gate.
- Pre-commit hooks with `detect-secrets` and the §2.5 data guard, to catch API keys and
  licensed payloads before push
- GitHub secret scanning and push protection to flag them at the remote

If you commit an API key or Scopus payload, it will likely be flagged by GitHub push protection (free-tier feature enabled after going public). Treat this as seriously as a production outage—the remote's reflog is immutable, and forks or clones taken before deletion preserve the secret.

**Governance is cheap compared to recovering from a leaked API key.** Never `git push --force` to "fix" a commit that contained a secret.

### Future upgrades

If the account upgrades to GitHub Pro:
- Private repository becomes possible again without losing branch protection
- The visibility choice becomes truly free (it reverts to the original plan: private until v1.0.0)
- The approving review requirement can remain enabled regardless

If a second maintainer joins (even on free tier):
- Re-enable "require at least one approving review" in branch protection settings
- The approval rule becomes enforceable (each can approve the other's work)
- `CODEOWNERS` becomes a hard requirement in CI (route critical paths to required review)

### Why this asymmetry is acceptable

The purpose of review in this project is not "a human checks the code" (that is good hygiene, but not sufficient). The purpose is "no single actor can silently insert a wrong number into the methodology without audit."

BUILD_PLAN.md §1.4 explains: every number that appears in output is a query against a versioned store, never a literal. The test harness (conftest.py) enforces determinism and traceability (§3.7.3, §3.7.8). A code change that would produce wrong numbers is caught by:

1. **Property tests** (hypothesis on the PRISMA set algebra)
2. **Arithmetic closure assertions** (sums close, flow counts balance)
3. **Byte-identical reproducibility** (same input → same output, forever)
4. **Acceptance criteria claiming** (a stage cannot be declared done while a requirement has no test)
5. **Mutation testing** (weekly run against `prisma/` and `taxonomy/` to catch unasserted logic)
6. **Golden snapshots** (captions, tables, figures require explicit approval on change)

A second human's eyes on the PR are helpful for readability and catch carelessness. But in the presence of these automated checks, a sole actor who writes code, writes tests, and confirms green is not circumventing the integrity model—they are following it exactly as designed. This ADR documents why.
