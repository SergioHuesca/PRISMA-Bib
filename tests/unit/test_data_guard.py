"""Tests for the §2.5 pre-commit data guard (``scripts/reject_licensed_content.sh``).

This script is the last gate between a licensed Scopus payload and an
irreversible push to a PUBLIC repository (BUILD_PLAN §2.5, §5 risk 10). Until
now it had no test at all, while its pattern was edited twice -- once to stop it
rejecting ``.env.example``, once to anchor ``projects/`` to the repository root
so it stopped blocking the reference fixture §3.7.5 line 536 requires be
committed.

Both edits were correct and both were made by eye. A guard that is only ever
verified by running it once, by hand, on the day it changes is a guard nobody
will notice has stopped working -- and its failure mode is silent: it does not
break the build, it just quietly stops rejecting things.

The two directions are tested separately and both matter. A guard that rejects
everything is as broken as one that rejects nothing, because the first thing
anyone does with a guard that blocks legitimate work is disable it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_GUARD = Path(__file__).parent.parent.parent / "scripts" / "reject_licensed_content.sh"

#: The guard is a POSIX shell script, invoked by pre-commit through the shell it
#: provides. Windows cannot execute it directly -- the first real Windows CI run
#: reported `OSError: [WinError 193] %1 is not a valid Win32 application` from
#: every case here. These tests assert the *script's* behaviour, so they are
#: skipped rather than rewritten: what a Windows contributor needs verified is
#: that pre-commit still runs the hook, which is a different claim and is not
#: covered here today.
_POSIX_ONLY = pytest.mark.skipif(
    sys.platform == "win32",
    reason="the §2.5 data guard is a POSIX shell script; Windows cannot exec it directly",
)


def _run_guard(*paths: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_GUARD), *paths],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


@pytest.mark.unit
@_POSIX_ONLY
def test_data_guard__script__is_executable() -> None:
    """``language: script`` needs the executable bit, or the hook dies on a fresh clone.

    The bit cannot be verified with ``os.access`` on the author's NTFS working copy
    (every file stats ``777`` there), so this asks git what mode it actually
    recorded -- which is what a fresh clone will get.
    """
    recorded = subprocess.run(
        ["git", "ls-files", "-s", str(_GUARD.relative_to(_GUARD.parents[1]))],
        cwd=_GUARD.parents[1],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    ).stdout

    assert recorded.startswith("100755")


@pytest.mark.unit
@_POSIX_ONLY
@pytest.mark.parametrize(
    "path",
    [
        pytest.param("projects/vad-2026/raw/page-0000.jsonl", id="layer0-payload"),
        pytest.param("projects/vad-2026/store/corpus.duckdb", id="store"),
        pytest.param("projects/vad-2026/fulltext/paper.pdf", id="fulltext"),
        pytest.param(".env", id="dotenv"),
        pytest.param("anywhere/corpus.duckdb", id="duckdb-anywhere"),
    ],
)
def test_data_guard__licensed_or_secret_path__is_rejected(path: str) -> None:
    """Every §2.5 category must be refused, and the message must name the path.

    Naming it matters: an operator who is told only "rejected" will reach for
    ``--no-verify``, which is the one response the guard cannot survive.
    """
    result = _run_guard(path)

    assert result.returncode != 0
    assert path in result.stderr


@pytest.mark.unit
@_POSIX_ONLY
@pytest.mark.parametrize(
    "path",
    [
        pytest.param(".env.example", id="dotenv-example-is-a-deliverable"),
        pytest.param(
            "tests/fixtures/projects/reference/raw/20260115T090000Z-fee5c0de/page-0000.jsonl",
            id="reference-fixture-layer0",
        ),
        pytest.param(
            "tests/fixtures/projects/reference/raw/20260115T090000Z-fee5c0de/manifest.json",
            id="reference-fixture-manifest",
        ),
        pytest.param("tests/fixtures/projects/reference/project.toml", id="reference-fixture-toml"),
        pytest.param("src/prismabib/store/load.py", id="ordinary-source"),
        pytest.param("docs/architecture/provenance.md", id="ordinary-docs"),
    ],
)
def test_data_guard__legitimate_path__is_accepted(path: str) -> None:
    """The guard must not block work the plan explicitly requires be committed.

    ``.env.example`` (§2.3 root layout) and the reference fixture (§3.7.5 line 536)
    are both mandated deliverables that an over-broad pattern has already blocked
    once each.
    """
    result = _run_guard(path)

    assert result.returncode == 0


@pytest.mark.unit
@_POSIX_ONLY
def test_data_guard__mixed_batch__rejects_and_names_only_the_offender() -> None:
    """A real commit mixes files; the guard must fail on one bad path among good ones."""
    result = _run_guard(
        "src/prismabib/store/load.py",
        "projects/vad-2026/raw/page-0000.jsonl",
        "docs/index.md",
    )

    assert result.returncode != 0
    assert "projects/vad-2026/raw/page-0000.jsonl" in result.stderr
    assert "src/prismabib/store/load.py" not in result.stderr
