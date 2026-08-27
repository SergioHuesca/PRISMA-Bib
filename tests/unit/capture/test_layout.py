"""Unit tests for ``src/prismabib/capture/layout.py`` -- the shared Layer 0 vocabulary.

Two things are pinned here that no other test can see.

**That the exclusion of ``raw/abstracts/`` is by name.** Both run scans --
:func:`prismabib.store.load._sealed_run_dirs` and
:func:`prismabib.capture.writer._find_resumable_run` -- currently skip
``raw/abstracts/`` for *two* reasons: it is named in
:data:`~prismabib.capture.layout.NON_RUN_DIRNAMES`, and it happens to carry no
``manifest.json`` or ``cursor.json`` directly inside it. The second reason is
an accident of layout. A test that only enriches a project and then asserts the
scans ignore the directory passes with the name check deleted, because the
accident carries it. So these tests plant exactly the file the accident depends
on being absent -- and then the name check is the only thing left holding.

**That ``writer``'s public surface did not change** when these helpers moved
out of it. ``is_sealed`` and ``SealedRunError`` were importable from
``prismabib.capture.writer`` before the refactor and still are; identity, not
just importability, is asserted, because two distinct ``SealedRunError``
classes would make ``except SealedRunError`` silently stop catching depending
on which module the caller imported from.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.capture import layout, writer
from prismabib.capture.layout import (
    ABSTRACTS_DIRNAME,
    CACHE_DIRNAME,
    NON_RUN_DIRNAMES,
    RUN_MANIFEST_FILENAME,
    SealedRunError,
    atomic_write_bytes,
    guard_writable,
    is_sealed,
    new_run_id,
)
from prismabib.store.load import _sealed_run_dirs


@pytest.mark.unit
def test_layout__non_run_dirnames__names_both_non_run_directories() -> None:
    assert set(NON_RUN_DIRNAMES) == {CACHE_DIRNAME, ABSTRACTS_DIRNAME}


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [pytest.param(CACHE_DIRNAME, id="cache"), pytest.param(ABSTRACTS_DIRNAME, id="abstracts")],
)
def test_layout__non_run_directory_carrying_a_manifest__is_still_not_a_sealed_run(
    tmp_path: Path, name: str
) -> None:
    """A ``manifest.json`` inside a non-run directory must not make it a run.

    This is the mutation the "obvious" version of this test cannot kill.
    ``raw/abstracts/`` normally has no ``manifest.json`` directly inside it --
    the seals live one level down, in ``raw/abstracts/<run_id>/`` -- so
    ``_sealed_run_dirs`` skips it whether or not it is excluded by name. Put
    the file there and the name check is the only remaining defence; without
    it, the loader reads an abstract run's directory as a search run, and the
    first thing it does is parse Abstract Retrieval payloads as search entries.
    """
    raw_dir = tmp_path / "raw"
    (raw_dir / name).mkdir(parents=True)
    (raw_dir / name / RUN_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

    assert _sealed_run_dirs(raw_dir) == []


@pytest.mark.unit
@pytest.mark.parametrize(
    "name",
    [pytest.param(CACHE_DIRNAME, id="cache"), pytest.param(ABSTRACTS_DIRNAME, id="abstracts")],
)
def test_layout__non_run_directory_carrying_a_cursor__is_never_resumed_as_a_search_run(
    tmp_path: Path, name: str
) -> None:
    """The same mutation, on the capture side.

    Resuming ``raw/abstracts/`` as a search run would write
    ``page-NNNN.jsonl`` files into the enrichment tree and seal it with a
    ``RunManifest``, which the loader would then read as a real capture.
    """
    raw_dir = tmp_path / "raw"
    (raw_dir / name).mkdir(parents=True)
    (raw_dir / name / "cursor.json").write_text(
        '{"query": "q", "view": "COMPLETE", "endpoint": "e", '
        '"started_at": "2026-01-01T00:00:00Z", "payload_files": []}',
        encoding="utf-8",
    )

    assert writer._find_resumable_run(raw_dir, query="q", view="COMPLETE", endpoint="e") is None


@pytest.mark.unit
def test_layout__writer__still_exposes_the_same_is_sealed_and_sealed_run_error() -> None:
    """The refactor moved these; it must not have forked them."""
    assert writer.is_sealed is is_sealed
    assert writer.SealedRunError is SealedRunError
    assert set(writer.__all__) == {"SealedRunError", "capture_search", "is_sealed"}


@pytest.mark.unit
def test_layout__is_sealed__is_true_only_when_the_manifest_exists(tmp_path: Path) -> None:
    run_dir = tmp_path / "20260115T090000Z-deadbeef"
    run_dir.mkdir()

    assert not is_sealed(run_dir)

    (run_dir / RUN_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

    assert is_sealed(run_dir)


@pytest.mark.unit
def test_layout__guard_writable__refuses_a_sealed_directory_and_names_it(tmp_path: Path) -> None:
    run_dir = tmp_path / "20260115T090000Z-deadbeef"
    run_dir.mkdir()
    guard_writable(run_dir)  # unsealed: returns

    (run_dir / RUN_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

    with pytest.raises(SealedRunError) as excinfo:
        guard_writable(run_dir)

    assert str(run_dir) in str(excinfo.value)


@pytest.mark.unit
def test_layout__atomic_write_bytes__leaves_no_temp_file_behind(tmp_path: Path) -> None:
    """The rename is what makes a killed process unable to leave a torn file.

    A stray ``.tmp`` sibling would also be picked up by a ``glob`` over the run
    directory, so its absence is asserted rather than assumed.
    """
    target = tmp_path / "nested" / "payload.jsonl"

    atomic_write_bytes(target, b'{"a":1}\n')

    assert target.read_bytes() == b'{"a":1}\n'
    assert sorted(path.name for path in target.parent.iterdir()) == ["payload.jsonl"]


@pytest.mark.unit
def test_layout__new_run_id__is_sortable_and_unique() -> None:
    """Sortability is load-bearing: both scans pick the most recent match with ``max``."""
    ids = [new_run_id() for _ in range(50)]

    assert len(set(ids)) == 50
    assert all(len(run_id) == len("20260115T090000Z-3f9a2c11") for run_id in ids)
    assert sorted(ids) == sorted(ids, key=lambda run_id: (run_id[:16], run_id[17:]))


@pytest.mark.unit
def test_layout__module__exports_exactly_the_shared_vocabulary() -> None:
    """A helper added here without an ``__all__`` entry is invisible to the docs build."""
    assert set(layout.__all__) == {
        "ABSTRACTS_DIRNAME",
        "CACHE_DIRNAME",
        "NON_RUN_DIRNAMES",
        "RUN_MANIFEST_FILENAME",
        "SealedRunError",
        "atomic_write_bytes",
        "guard_writable",
        "is_sealed",
        "new_run_id",
    }
