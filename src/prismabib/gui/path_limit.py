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

#: The deepest path the library writes under a Project: the temporary file of a cache entry in the
#: per-run HTTP cache of an abstracts run, ``raw/abstracts/<run id>/_http/<2 hex>/<64 hex>.bin.tmp``
#: (a run id is ``<UTC timestamp>-<8 hex>``, 25 characters). The full-text runs' cache is as long;
#: every other writer (search pages, assets, exports, store, taxonomy, logs) is shorter, which
#: ``tests/unit/gui/test_path_limit.py`` checks against the library's own names.
LONGEST_RELATIVE_PATH: Final = PurePosixPath(
    "raw", "abstracts", "20260115T090000Z-3f9a2c11", "_http", "00", "0" * 64 + ".bin.tmp"
)


def max_folder_chars() -> int:
    """The longest Projects folder path that still leaves room for a one-character Project name.

    The limit is read when called, never copied at import, so a test (or a future setting) that
    changes ``MAX_PATH_CHARS`` is honoured everywhere.
    """
    return MAX_PATH_CHARS - len(str(LONGEST_RELATIVE_PATH)) - 3  # two separators and the name


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
