"""The pushed-commit answer against the real remote (issue #365, spec #357).

``_git_provenance`` decides ``commit_is_pushed`` from remote-tracking refs, with
no network. This test asks the real remote through ``git ls-remote`` and
requires the two answers to agree for a published commit and for a commit that
cannot be on the remote. A disagreement is a product bug: file it, do not
change this test to match.

``ls-remote`` matches ref *names*, not commit ids, so "the remote has this
commit" is read as "the commit is the tip of a ref the remote lists". The
published commit is therefore a tag commit: tags are immutable by convention,
so the answer does not change as ``main`` moves. ``v0.1.0`` peels to the
repository's first published release commit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from prismabib.report.export import _git_provenance

pytestmark = pytest.mark.live

REPO_ROOT = Path(__file__).parent.parent.parent
PUBLISHED_TAG = "v0.1.0"
PUBLISHED_COMMIT = "17dca73879328d9666315af80862ffb2ad330eb1"  # pragma: allowlist secret


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    ).stdout.strip()


def _remote_lists(url: str, sha: str) -> bool:
    """Whether ``git ls-remote`` shows ``sha`` as the tip of any remote ref."""
    out = _git(["ls-remote", url], cwd=REPO_ROOT)
    return any(line.split("\t")[0] == sha for line in out.splitlines())


@pytest.fixture(scope="module")
def remote_url() -> str:
    return _git(["remote", "get-url", "origin"], cwd=REPO_ROOT)


@pytest.fixture
def clone(tmp_path: Path, remote_url: str) -> Path:
    """A blobless, checkout-less clone: full history, so ``origin/*`` refs exist."""
    work = tmp_path / "work"
    _git(
        ["clone", "--quiet", "--no-checkout", "--filter=blob:none", remote_url, str(work)], tmp_path
    )
    return work


def test_pushed_answer__published_tag_commit__agrees_with_ls_remote(
    clone: Path, remote_url: str
) -> None:
    peeled = _git(["ls-remote", remote_url, f"refs/tags/{PUBLISHED_TAG}^{{}}"], cwd=REPO_ROOT)
    assert peeled.split("\t")[0] == PUBLISHED_COMMIT  # the pinned sha is still the tag's
    _git(["update-ref", "--no-deref", "HEAD", PUBLISHED_COMMIT], cwd=clone)

    assert _remote_lists(remote_url, PUBLISHED_COMMIT) is True
    assert _git_provenance(clone)["commit_is_pushed"] is True


def test_pushed_answer__made_up_commit__agrees_with_ls_remote(clone: Path, remote_url: str) -> None:
    _git(
        [
            "-c",
            "user.email=t@example.invalid",
            "-c",
            "user.name=t",
            "commit",
            "--quiet",
            "--allow-empty",
            "-m",
            "never pushed",
        ],
        cwd=clone,
    )
    made_up = _git(["rev-parse", "HEAD"], cwd=clone)

    assert _remote_lists(remote_url, made_up) is False
    assert _git_provenance(clone)["commit_is_pushed"] is False
