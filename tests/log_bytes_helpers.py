"""Generic, path-based byte surgery for any append-only JSONL log.

Extracted from :mod:`tests.prisma_helpers`'s project-scoped equivalents
(``overwrite_log_bytes``/``append_raw_bytes``/``rewrite_sidecar``/
``sidecar_path``/``sidecar_matches_log``, all hardcoded to
``project.decisions_path``) so that the *same* byte-level tampering helpers
work for Stage 8's ``taxonomy_overrides.jsonl`` too -- there is exactly one
place that knows how to corrupt an append-only log's bytes from the outside,
which is what these functions simulate (a text editor or a killed process,
never a patched ``prismabib.*`` symbol -- §3.7.3 rule 1).

:mod:`tests.prisma_helpers`'s own project-scoped helpers now delegate here
rather than duplicating the logic.
"""

from __future__ import annotations

import hashlib
from pathlib import Path


def sidecar_path_for(path: Path) -> Path:
    """The ``.sha256`` sidecar path for an append-only log at ``path``."""
    return path.with_name(path.name + ".sha256")


def read_bytes(path: Path) -> bytes:
    """The current raw bytes of the log at ``path``."""
    return path.read_bytes()


def overwrite_bytes(path: Path, content: bytes) -> None:
    """Replace the log's content wholesale, leaving its sidecar alone.

    Args:
        path: The log file to overwrite.
        content: The exact bytes to write.
    """
    path.write_bytes(content)


def append_raw_bytes(path: Path, content: bytes) -> None:
    """Append raw bytes to the log, leaving its sidecar alone.

    Args:
        path: The log file to append to.
        content: The exact bytes to append -- a partial line, a whole line,
            or several.
    """
    with path.open("ab") as handle:
        handle.write(content)


def rewrite_sidecar(path: Path, content: bytes | None = None) -> None:
    """Rewrite ``path``'s checksum sidecar to describe ``content``.

    Args:
        path: The log file whose sidecar to rewrite.
        content: The bytes the sidecar should describe. Defaults to the
            log's current on-disk content -- "re-bless whatever is there
            now", which is how a test gets a hand-written log past the
            checksum guard in order to assert a later, different rule.
    """
    payload = read_bytes(path) if content is None else content
    digest = hashlib.sha256(payload).hexdigest()
    sidecar_path_for(path).write_text(f"{digest}  {path.name}\n", encoding="utf-8")


def sidecar_matches(path: Path) -> bool:
    """Whether ``path``'s sidecar digest matches its current bytes."""
    recorded = sidecar_path_for(path).read_text(encoding="utf-8").split(maxsplit=1)[0]
    return recorded == hashlib.sha256(read_bytes(path)).hexdigest()


__all__ = [
    "append_raw_bytes",
    "overwrite_bytes",
    "read_bytes",
    "rewrite_sidecar",
    "sidecar_matches",
    "sidecar_path_for",
]
