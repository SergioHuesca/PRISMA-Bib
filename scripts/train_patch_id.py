"""Print the 12-hex net patch id of a PR head: the content a reviewer approves.

Usage: ``train_patch_id.py <40-hex head sha>``. It runs git only on data (diff and
patch-id), so the PR's code is never checked out or executed. The head object must exist
locally (``git fetch origin pull/<n>/head``) and ``origin/main`` must include its merge base.
Used by update-behind-prs.yml and by the orchestrator, so both compute the same id. Exit 1
if git fails or the head has no net change against main.
"""

from __future__ import annotations

import re
import subprocess
import sys

_SHA_RE = re.compile(r"[0-9a-f]{40}")
# Pinned so the id does not depend on the machine's git config. Three-dot: only the PR's own
# changes since its merge base, so main moving on does not change it. -U0: context lines are
# not compared, so neighbouring merges do not change it. Rename detection and the diff
# algorithm change which lines count as changed, so both are fixed.
_DIFF = [
    "git",
    "diff",
    "-U0",
    "--no-color",
    "--no-ext-diff",
    "--no-textconv",
    "--no-renames",
    "--diff-algorithm=myers",
    "--no-indent-heuristic",
]


def net_patch_id(head_sha: str) -> str:
    if not _SHA_RE.fullmatch(head_sha):
        raise ValueError("head sha must be 40 lowercase hex")
    diff = subprocess.run(
        [*_DIFF, f"origin/main...{head_sha}"], capture_output=True, check=True
    ).stdout
    fields = subprocess.run(
        ["git", "patch-id", "--stable"], input=diff, capture_output=True, check=True
    ).stdout.split()
    if not fields:
        raise ValueError("no net change against origin/main")
    return fields[0].decode("ascii")[:12]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: train_patch_id.py <40-hex head sha>", file=sys.stderr)
        return 2
    try:
        print(net_patch_id(args[0]))
    except (ValueError, subprocess.CalledProcessError) as exc:
        print(f"train_patch_id: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
