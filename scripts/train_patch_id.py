"""Print the 20-hex id of a PR's own change: the content a reviewer approves.

Usage: ``train_patch_id.py <40-hex head sha>``. It runs git only on data (diff), so the PR's
code is never checked out or executed. The head object must exist locally
(``git fetch origin pull/<n>/head``) and ``origin/main`` must include its merge base.
Used by update-behind-prs.yml and by the orchestrator, so both compute the same id. Exit 1
if git fails or the head has no net change against main.

The id is sha256 over the exact bytes of the PR's own changed lines (the +/- lines, file
paths, mode and binary markers), so any byte change in the reviewed content changes it,
including whitespace, indentation, CRLF and trailing spaces. ``git patch-id`` is not used
because it ignores whitespace and would accept an indentation change as the same patch.
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import sys

_SHA_RE = re.compile(r"[0-9a-f]{40}")
_ID_LEN = 20
# Pinned so the id does not depend on the machine's git config. Three-dot: only the PR's own
# changes since its merge base, so main moving on does not change it. -U0: context lines are
# not compared. Rename detection and the diff algorithm change which lines count as changed,
# so both are fixed.
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
# Hunk headers (@@), index lines and context lines are dropped: they depend on where the
# change sits in the file, which moves when main moves. Everything kept is content or a path.
_KEEP_PREFIXES = (
    b"+",
    b"-",
    b"diff --git ",
    b"new file mode ",
    b"deleted file mode ",
    b"old mode ",
    b"new mode ",
    b"Binary files ",
    b"\\ No newline",
)


def net_patch_id(head_sha: str) -> str:
    if not _SHA_RE.fullmatch(head_sha):
        raise ValueError("head sha must be 40 lowercase hex")
    diff = subprocess.run(
        [*_DIFF, f"origin/main...{head_sha}"], capture_output=True, check=True
    ).stdout
    kept = [line for line in diff.split(b"\n") if line.startswith(_KEEP_PREFIXES)]
    if not kept:
        raise ValueError("no net change against origin/main")
    return hashlib.sha256(b"\n".join(kept)).hexdigest()[:_ID_LEN]


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
