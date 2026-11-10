"""The longest relative path the app budgets for is the longest the library really writes."""

from __future__ import annotations

from pathlib import Path, PurePosixPath

import pytest

from prismabib.capture.layout import (
    ABSTRACTS_DIRNAME,
    HTTP_CACHE_DIRNAME,
    OPENALEX_SEARCH_DIRNAME,
    new_run_id,
)
from prismabib.fulltext.capture import ASSETS_DIRNAME, RUNS_DIRNAME
from prismabib.gui.path_limit import LONGEST_RELATIVE_PATH
from prismabib.sources.cache import HttpCache

pytestmark = pytest.mark.unit


def _writers() -> dict[str, PurePosixPath]:
    """Every deep path a writer creates under a Project, built from the library's own names."""
    run = new_run_id()
    cache = HttpCache(Path(HTTP_CACHE_DIRNAME))
    entry = cache._path_for("0" * 64)  # pyright: ignore[reportPrivateUsage]
    cached = PurePosixPath(entry.as_posix() + ".tmp")  # cache.py writes <entry>.tmp, then renames
    sha = "0" * 64
    return {
        "scopus search http cache": PurePosixPath("raw", run, cached),
        "abstracts http cache": PurePosixPath("raw", ABSTRACTS_DIRNAME, run, cached),
        "full-text http cache": PurePosixPath("fulltext", RUNS_DIRNAME, run, cached),
        "full-text asset": PurePosixPath(
            "fulltext", RUNS_DIRNAME, run, ASSETS_DIRNAME, sha + ".pdf.tmp"
        ),
        "openalex search page": PurePosixPath(
            "raw", OPENALEX_SEARCH_DIRNAME, run, "response-0000.jsonl.tmp"
        ),
    }


def test_longest_relative_path__is_the_deepest_path_a_writer_creates() -> None:
    longest = max(_writers().items(), key=lambda item: len(str(item[1])))

    assert len(str(LONGEST_RELATIVE_PATH)) == len(str(longest[1])), longest[0]
