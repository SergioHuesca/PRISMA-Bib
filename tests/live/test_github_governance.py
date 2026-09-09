"""Stage 0's GitHub-governance acceptance criteria (BUILD_PLAN.md lines 629-634).

Four of Stage 0's six criteria are facts about GitHub's servers, not about
this clone, and the socket ban (§3.7.3 rule 2) means no default-run test can
verify them. They are claimed here, marked both ``live`` (deselected by
``-m "not live"``, so the socket ban is never at risk from a default run)
and ``acceptance`` (collected at collection time regardless of deselection,
so the criterion is honestly claimed -- see ``tests/markers.py``).

These shell out to ``git``/``gh`` rather than opening sockets directly --
which is exactly what ``tests/live/conftest.py``'s socket re-enabling exists
for, even though a subprocess is unaffected by the in-process socket ban
either way.

None of these are expected to pass on a fresh Stage 0 checkout: a merge-
blocking observation (AC5) needs a live PR with a red required check, and a
Pages deploy (AC3) needs a completed ``docs.yml`` run -- neither exists
before this stage's own PR has gone through the pipeline it is bootstrapping.
That is correct: BUILD_PLAN.md §3.7.7 describes this file as the nightly /
on-demand gate, not a pre-merge check, and it is not marked ``xfail`` because
these are not expected failures of the code under test -- they are honest
reports of GitHub-side state that has not happened yet.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import uuid
from collections.abc import Mapping
from pathlib import Path

import pytest

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).parent.parent.parent


def _origin_slug() -> str:
    """The ``owner/name`` this clone's ``origin`` actually points at.

    Derived rather than hardcoded so a fork exercises its *own* governance.
    These tests assert that the repository is version-managed on GitHub with
    branch protection and a published Pages site (BUILD_PLAN §3.6, S00-AC1);
    none of that is a claim about one particular owner. Pinning the upstream
    slug made a fork's `live` suite fail on identity rather than on
    behaviour, which teaches contributors that the suite is noise.
    """
    remote = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    ).stdout.strip()
    for prefix in ("https://github.com/", "ssh://git@github.com/", "git@github.com:"):
        if remote.startswith(prefix):
            return remote.removeprefix(prefix).removesuffix(".git")
    return remote.removesuffix(".git")


REPO = _origin_slug()


#: A git author/committer identity for the one test that writes an object.
#:
#: `git commit-tree` refuses with "Author identity unknown" when neither
#: `user.email` nor `user.name` is configured, which is every clean CI
#: container -- and passes locally only because a developer machine has a
#: global identity. That is the machine-dependence class CLAUDE.md names:
#: green here, red on the nightly, and the failure says nothing about the
#: governance rule the test exists to check.
#:
#: Supplied through the environment rather than `git config`, deliberately.
#: A `--global` write would mutate the runner's configuration for every
#: later step, and a local write would dirty the working copy this suite
#: also asserts is clean.
_GIT_IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "prismabib governance probe",
    "GIT_AUTHOR_EMAIL": "governance-probe@invalid",
    "GIT_COMMITTER_NAME": "prismabib governance probe",
    "GIT_COMMITTER_EMAIL": "governance-probe@invalid",
}


#: Credentials this job exports that a fresh contributor's clone would not
#: have. Removed from the environment before running the cloned suite, so
#: `S00-AC2` measures what it claims: that a clone taken from GitHub is
#: green for someone who has just arrived.
_CREDENTIAL_ENV_VARS = frozenset(
    {"SCOPUS_API_KEY", "SCOPUS_INSTTOKEN", "ELSEVIER_SD_API_KEY", "UNPAYWALL_EMAIL"}
)


def _run(*args: str, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    """Run a `git`/`gh` command from the repository root, never raising.

    Args:
        args: The command and its arguments.
        env: Extra environment variables, merged over the inherited
            environment. Merged rather than replacing it, because `git` and
            `gh` both need `PATH`, `HOME` and the runner's credentials to
            work at all.

    Returns:
        The completed process, with `check=False` so a caller asserts on
        the return code itself.
    """
    return subprocess.run(
        args,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        env={**os.environ, **env} if env else None,
    )


@pytest.mark.acceptance("S00-AC2")
def test_clean_clone__from_github__syncs_and_passes_the_default_suite() -> None:
    """S00-AC2 verbatim: green from a clone *taken from GitHub*, not the working copy.

    BUILD_PLAN.md line 630 is explicit that the working copy does not count. That
    makes this irreducibly a live test -- it needs the network to clone -- so it
    lives here rather than being proxied by the smoke test, which only compares
    ``__version__`` against ``pyproject.toml`` and cannot speak to whether a fresh
    clone installs and passes.
    """
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "PRISMA-Bib"

        clone = subprocess.run(
            ["gh", "repo", "clone", REPO, str(target)],
            capture_output=True,
            text=True,
            check=False,
            timeout=300,
        )
        assert clone.returncode == 0, clone.stderr

        sync = subprocess.run(
            ["uv", "sync", "--all-extras"],
            cwd=target,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
        )
        assert sync.returncode == 0, sync.stderr

        # No -m override: this asserts the DEFAULT invocation is green, which is
        # what the criterion says. addopts supplies `-m "not live"`, so this does
        # not recurse into the live suite.
        # The suite runs *without* this job's API keys, because S00-AC2 is
        # about a fresh contributor's clone and a fresh contributor has
        # none. The `live` job exports `SCOPUS_API_KEY` and
        # `ELSEVIER_SD_API_KEY`, and it is the only job in CI that does --
        # so a suite inheriting them here is exercising a configuration no
        # other job, and no new contributor, ever sees.
        #
        # That is not hypothetical: it is how six `fulltext/test_run.py`
        # tests came to fail here while `full`, `fast` and `full-matrix`
        # stayed green. Those tests are fixed, but scrubbing is the right
        # scope for this criterion regardless of whether anything currently
        # depends on it.
        contributor_env = {
            key: value for key, value in os.environ.items() if key not in _CREDENTIAL_ENV_VARS
        }
        # `-m "not benchmark"`, because a timing assertion measured *inside*
        # a nested pytest process on an already-busy runner measures
        # contention, not the code. The dedicated `benchmark` job exists to
        # run these in isolation and passed on the same run this failed:
        #
        #   benchmark job          : success
        #   nested inside this one : "the slowest of 20 decisions took
        #                             118.4 ms ... over the 100 ms of
        #                             BUILD_PLAN line 1090"
        #
        # No other CI job runs benchmarks bare either -- `full` uses
        # `-m "not live and not e2e and not benchmark"` -- so this was the
        # only place a performance budget was asserted under two concurrent
        # pytest sessions.
        #
        # Everything S00-AC2 actually claims is preserved: a clone taken
        # from GitHub installs and passes. What is dropped is a second,
        # worse-conditioned measurement of a budget already measured well.
        # `-n auto`, because this ran single-process and did not finish.
        # Run #24 failed with `subprocess.TimeoutExpired` after the full
        # 900 s: 1523 tests on a two-vCPU runner, where the same suite takes
        # ~155 s single-process on a developer machine. Under xdist it is
        # about two minutes.
        #
        # S00-AC2 claims a clone taken from GitHub *installs and passes*, not
        # that it passes single-process -- and `-n auto` is how this project
        # runs its suite everywhere else, including the `full` job whose
        # result this is meant to mirror. `pytest-xdist` is a dev dependency
        # and arrives with the `uv sync --all-extras` two steps above, so
        # this needs nothing a fresh contributor would not already have.
        suite = subprocess.run(
            ["uv", "run", "pytest", "-m", "not benchmark", "-n", "auto"],
            cwd=target,
            capture_output=True,
            text=True,
            check=False,
            timeout=900,
            env=contributor_env,
        )

        assert suite.returncode == 0, suite.stdout + suite.stderr


@pytest.mark.acceptance("S00-AC1")
def test_repository__origin_remote__points_at_github_with_main_pushed() -> None:
    remote = _run("git", "remote", "get-url", "origin")
    local_main = _run("git", "rev-parse", "refs/heads/main")
    remote_main = _run("git", "rev-parse", "refs/remotes/origin/main")

    # Compare the *remote*, not one spelling of it. The `.git` suffix is
    # optional in a GitHub clone URL and `actions/checkout` omits it, so
    # asserting on `origin\thttps://github.com/<repo>.git (fetch)` failed
    # every nightly while the remote was correct -- a test pinning a
    # cosmetic difference between two machines, which is what it exists to
    # rule out for the repository, not to reproduce for itself.
    assert remote.returncode == 0, remote.stderr
    assert remote.stdout.strip().removesuffix(".git") == f"https://github.com/{REPO}"
    assert local_main.stdout.strip() == remote_main.stdout.strip()


@pytest.mark.acceptance("S00-AC3")
def test_pages__docs_workflow__has_published_at_least_once() -> None:
    """Assert a successful `github-pages` deployment exists and the site serves.

    Deliberately NOT `repos/{owner}/{repo}/pages/builds`: that is the legacy
    branch-sourced Pages API, and it returns an empty list forever when Pages is
    sourced from a workflow (`build_type: workflow`, which is how §3.6.5 wires
    `docs.yml`). Querying it made this test fail permanently while the site was
    demonstrably live -- a false negative in the nightly gate, which is worse than
    no gate: a real failure becomes indistinguishable from the standing one.
    """
    deployments = _run(
        "gh",
        "api",
        f"repos/{REPO}/deployments?environment=github-pages",
        "--jq",
        "length",
    )
    assert deployments.returncode == 0, deployments.stderr
    assert int(deployments.stdout.strip() or "0") > 0

    # A deployment record is necessary but not sufficient -- one can exist for a run
    # that failed to publish. Confirm the site actually answers.
    served = _run(
        "curl",
        "--silent",
        "--show-error",
        "--location",
        "--max-time",
        "30",
        "--output",
        os.devnull,
        "--write-out",
        "%{http_code}",
        "https://sergiohuesca.github.io/PRISMA-Bib/",
    )

    assert served.stdout.strip() == "200", served.stdout + served.stderr


@pytest.mark.acceptance("S00-AC5")
def test_pull_request__red_required_check__cannot_be_merged() -> None:
    """Open a throwaway PR carrying a deliberately failing commit, and
    confirm GitHub refuses to merge it while its required check is red.
    """
    marker = uuid.uuid4().hex[:12]
    branch = f"stage-00-governance-probe-{marker}"
    probe_path = "tests/_governance_probe.py"
    pr_number = ""

    with tempfile.TemporaryDirectory() as scratch:
        # A scratch index, so building the probe's tree touches neither the
        # real working directory nor the real index of this clone.
        scratch_env = {**os.environ, "GIT_INDEX_FILE": str(Path(scratch) / "index")}

        try:
            _run("git", "fetch", "origin", "main")
            _run("git", "branch", branch, "origin/main")

            # A `ruff`-unparseable file: guaranteed to fail the `lint` check
            # without touching anything a real stage would ever author.
            blob = subprocess.run(
                ["git", "hash-object", "-w", "--stdin"],
                cwd=REPO_ROOT,
                input="def broken(:\n    pass\n",
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            blob_sha = blob.stdout.strip()

            subprocess.run(
                ["git", "read-tree", branch],
                cwd=REPO_ROOT,
                env=scratch_env,
                check=False,
                timeout=120,
            )
            subprocess.run(
                ["git", "update-index", "--add", "--cacheinfo", f"100644,{blob_sha},{probe_path}"],
                cwd=REPO_ROOT,
                env=scratch_env,
                check=False,
                timeout=120,
            )
            new_tree = subprocess.run(
                ["git", "write-tree"],
                cwd=REPO_ROOT,
                env=scratch_env,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            ).stdout.strip()

            commit = subprocess.run(
                [
                    "git",
                    "commit-tree",
                    new_tree,
                    "-p",
                    branch,
                    "-m",
                    f"test: intentionally failing governance probe {marker}",
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
                # The same identity `test_main_branch__direct_push` needs.
                # This call is a bare `subprocess.run` rather than `_run`,
                # so the fix that gave that test an identity never reached
                # here: `commit-tree` returned 128 "Author identity
                # unknown", `commit` was the empty string, and the failure
                # only surfaced three steps later as a GitHub API error
                # about a branch that was never created.
                env={**os.environ, **_GIT_IDENTITY_ENV},
            ).stdout.strip()

            # Guard the refspec before it is built -- the guard the sibling
            # test already documents, missing here. An empty left-hand side
            # turns `<commit>:refs/heads/<branch>` into `:refs/heads/<branch>`,
            # which is the DELETE-branch refspec. GitHub accepts it (there is
            # nothing to delete), the push returns 0, and the test sails past
            # its own assertion to fail later at `gh pr create` with "Head ref
            # must be a branch" -- a message about the wrong thing entirely.
            assert commit, (
                "git commit-tree produced no SHA; pushing an empty refspec would be a "
                "branch DELETE, not a branch create"
            )

            push = _run("git", "push", "origin", f"{commit}:refs/heads/{branch}")
            assert push.returncode == 0, push.stderr

            pr_create = _run(
                "gh",
                "pr",
                "create",
                "--head",
                branch,
                "--base",
                "main",
                "--title",
                f"test: governance probe {marker} (throwaway, do not merge)",
                "--body",
                "Stage 0 harness live check (S00-AC5). Safe to close; created and "
                "cleaned up by tests/live/test_github_governance.py.",
            )
            assert pr_create.returncode == 0, pr_create.stderr
            pr_number = pr_create.stdout.strip().rsplit("/", maxsplit=1)[-1]

            merge_attempt = _run("gh", "pr", "merge", pr_number, "--squash")

            assert merge_attempt.returncode != 0
        finally:
            if pr_number:
                _run("gh", "pr", "close", pr_number, "--delete-branch")
            _run("git", "push", "origin", "--delete", branch)
            _run("git", "branch", "-D", branch)


@pytest.mark.acceptance("S00-AC6")
def test_main_branch__direct_push__is_rejected_by_branch_protection() -> None:
    """Push a new commit straight to `main`'s ref and confirm it bounces.

    The pushed commit reuses `main`'s current tree (no content changes), so
    even in the failure mode where this assertion is wrong and the push is
    *not* rejected, the repository's tracked content is unaffected -- only
    an extra, identical-content commit would land on `main`.
    """
    fetch = _run("git", "fetch", "origin", "main")
    assert fetch.returncode == 0, fetch.stderr

    rev_parse = _run("git", "rev-parse", "refs/remotes/origin/main^{tree}")
    assert rev_parse.returncode == 0, rev_parse.stderr
    tree = rev_parse.stdout.strip()

    commit_tree = _run(
        "git",
        "commit-tree",
        tree,
        "-p",
        "refs/remotes/origin/main",
        "-m",
        "test: direct-push governance probe (expected to be rejected)",
        env=_GIT_IDENTITY_ENV,
    )
    assert commit_tree.returncode == 0, commit_tree.stderr
    commit = commit_tree.stdout.strip()

    # Guard the refspec before it is built. An empty left-hand side turns
    # `<commit>:refs/heads/main` into `:refs/heads/main`, which is the DELETE-branch
    # refspec -- and GitHub rejects a protected deletion with the same "protected
    # branch" wording this test asserts on, so both assertions below would still pass
    # while the test had just tried to delete the default branch. Only
    # `allow_deletions: false` would have prevented the loss.
    assert re.fullmatch(r"[0-9a-f]{40}", commit), f"refusing to push refspec: {commit!r}"

    push = _run("git", "push", "origin", f"{commit}:refs/heads/main")

    assert push.returncode != 0
    assert "protected branch" in (push.stderr or "").lower()
