"""The longest path a Project may need, and the limit on it (GUI spec #312, story 57).

Windows stops at 260 characters for a full path; 240 leaves headroom. The limit applies to the
deepest file the library writes under a Project, so a Project is refused at creation, before
anything is written, and not found out at the first capture.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Final

#: Headroom under the classic 260-character Windows path limit.
MAX_PATH_CHARS: Final = 240

#: The deepest path the library writes under a Project: the per-run HTTP cache of a search run,
#: ``raw/openalex-search/<run id>/_http/<2 hex>/<64 hex>.bin`` (a run id is
#: ``<UTC timestamp>-<8 hex>``, 25 characters).
LONGEST_RELATIVE_PATH: Final = PurePosixPath(
    "raw", "openalex-search", "20260115T090000Z-3f9a2c11", "_http", "00", "0" * 64 + ".bin"
)


def deepest_path(root: Path, slug: str) -> Path:
    """The longest path a Project ``slug`` in ``root`` can hold."""
    return root.expanduser().absolute() / slug / LONGEST_RELATIVE_PATH


def path_refusal(root: Path, slug: str) -> str | None:
    """A sentence naming the limit when the Project's deepest path is too long, else ``None``."""
    length = len(str(deepest_path(root, slug)))
    if length <= MAX_PATH_CHARS:
        return None
    return (
        f"A Project named {slug!r} in this folder would need paths of {length} characters; the "
        f"limit is {MAX_PATH_CHARS} (Windows stops at 260 and a Project needs room inside its "
        "folder). Choose a shorter Project name or a shorter Projects folder."
    )
