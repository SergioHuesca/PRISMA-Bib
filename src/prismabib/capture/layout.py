"""The shared Layer 0 on-disk vocabulary (BUILD_PLAN §2.2, lines 99-102).

Everything that writes under ``projects/<slug>/raw/`` has to agree on four
things, and until this module existed each of them was a literal repeated in
whichever file happened to need it:

1. **What marks a run directory as sealed** -- the presence of
   ``manifest.json``, and nothing else (:data:`RUN_MANIFEST_FILENAME`,
   :func:`is_sealed`).
2. **What a sealed directory may never do again** -- be written to
   (:func:`guard_writable`, :class:`SealedRunError`).
3. **Which directories under ``raw/`` are not runs** at all
   (:data:`NON_RUN_DIRNAMES`).
4. **How a run id is minted** (:func:`new_run_id`).

The duplication was not hypothetical. ``store/load.py`` carried its own
``_CACHE_DIRNAME = "_cache"`` with a comment pointing at
``capture.writer._CACHE_DIRNAME`` as the real definition -- two constants that
had to stay equal by hand, on the two sides of the Layer 0 / Layer 1 boundary,
where disagreeing would have meant the loader either treating the HTTP cache as
a run directory or skipping a real one. This module is the single definition
both sides now import.

**Why ``NON_RUN_DIRNAMES`` is a set rather than one name.** ``raw/`` holds two
kinds of directory that are emphatically not search runs: ``raw/_cache/`` (the
content-addressed HTTP cache, see :mod:`prismabib.sources.cache`) and
``raw/abstracts/`` (the Abstract Retrieval enrichment runs of
:mod:`prismabib.capture.enrich`, which are nested one level deeper --
``raw/abstracts/<run_id>/`` -- and carry a different manifest schema
entirely). A scan that mistook either for a search run would try to parse
Abstract Retrieval payloads as search entries and fail on a missing
``prism:coverDate``. ``raw/abstracts/`` happens to be *already* invisible to
those scans, because it carries no ``manifest.json`` directly inside it -- but
that is an accident of layout, one refactor away from not being true. Naming it
here makes the exclusion the rule it was always meant to be rather than a
coincidence nothing asserts.

Nothing in this module knows what a *page* or an *abstract* is. It is
deliberately the layer below both writers, so that adding a third kind of Layer
0 run later means importing these four names, not copying them again.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from prismabib.errors import PrismabibError

#: The file whose presence -- and only whose presence -- means "this run
#: directory is sealed" (BUILD_PLAN §2.2). Both the search runs of
#: :mod:`prismabib.capture.writer` and the abstract runs of
#: :mod:`prismabib.capture.enrich` use this same name, so :func:`is_sealed`
#: answers the question for either without being told which kind it is looking
#: at. The two manifests' *schemas* differ; the seal does not.
RUN_MANIFEST_FILENAME = "manifest.json"

#: The content-addressed HTTP cache directory under ``raw/``
#: (:class:`prismabib.sources.cache.HttpCache`). Its name starts with ``_``
#: precisely so that a run scan can skip it; it never receives a
#: ``manifest.json``, so it is never sealed and stays writable across every run,
#: past and future.
CACHE_DIRNAME = "_cache"

#: The Abstract Retrieval enrichment tree under ``raw/``. Runs live one level
#: deeper, at ``raw/abstracts/<run_id>/``, each with its own
#: :class:`~prismabib.capture.manifest.AbstractRunManifest` and its own seal.
ABSTRACTS_DIRNAME = "abstracts"

#: Every immediate child of ``raw/`` that is not a search run directory. Any
#: code iterating ``raw/`` looking for runs must skip these by name rather than
#: relying on their contents to disqualify them.
NON_RUN_DIRNAMES = frozenset({CACHE_DIRNAME, ABSTRACTS_DIRNAME})


class SealedRunError(PrismabibError):
    """A write was attempted against a Layer 0 run directory that is already sealed.

    Not one of the named leaves in the BUILD_PLAN §3.3 error tree (that
    tree predates this module), but a direct subclass of
    :class:`~prismabib.errors.PrismabibError` for the Layer 0 immutability
    invariant §2.2 assigns to Layer 0 writers: a run directory carrying a
    ``manifest.json`` must never be written to again, enforced here in code
    (see :func:`guard_writable`), not left to convention or filesystem
    permissions (the latter do not survive this project's NTFS working copy
    and are not portable in any case).
    """


def is_sealed(run_dir: Path) -> bool:
    """Whether ``run_dir`` is a finished, immutable Layer 0 run directory.

    Args:
        run_dir: A candidate run directory -- ``raw/<run_id>/`` for a search
            run, ``raw/abstracts/<run_id>/`` for an abstract run.

    Returns:
        ``True`` if and only if ``run_dir/manifest.json`` exists -- the
        sole signal, by design, of "sealed" (BUILD_PLAN §2.2).
    """
    return (run_dir / RUN_MANIFEST_FILENAME).is_file()


def guard_writable(run_dir: Path) -> None:
    """Refuse any write into an already-sealed run directory.

    Args:
        run_dir: The run directory a write is about to target.

    Raises:
        SealedRunError: If ``run_dir`` already carries a ``manifest.json``.
    """
    if is_sealed(run_dir):
        raise SealedRunError(
            f"{run_dir} already carries a {RUN_MANIFEST_FILENAME} and is sealed. "
            "Layer 0 run directories are immutable once sealed (BUILD_PLAN "
            "§2.2, lines 99-102): nothing may write here again. Start a fresh "
            "run instead."
        )


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically (write-to-temp, then rename).

    A process killed mid-write can never leave a torn/partial file at
    ``path`` -- it either never appears, or appears complete -- which is
    what lets a resumed run trust every payload file its resumption
    sidecar names.

    Args:
        path: The destination path.
        data: The exact bytes to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_bytes(data)
    tmp_path.replace(path)


def new_run_id() -> str:
    """Generate a fresh, sortable, collision-safe run id.

    Returns:
        ``<UTC timestamp>-<8 hex chars>``, e.g.
        ``20260115T090000Z-3f9a2c11`` -- sortable so that a scan for a
        resumable run can deterministically pick the most recent match when
        more than one unsealed run directory qualifies.
    """
    return f"{datetime.now(UTC):%Y%m%dT%H%M%S}Z-{uuid.uuid4().hex[:8]}"


__all__ = [
    "ABSTRACTS_DIRNAME",
    "CACHE_DIRNAME",
    "NON_RUN_DIRNAMES",
    "RUN_MANIFEST_FILENAME",
    "SealedRunError",
    "atomic_write_bytes",
    "guard_writable",
    "is_sealed",
    "new_run_id",
]
