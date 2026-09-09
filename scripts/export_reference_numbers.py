"""Export the reference project's ``numbers.json`` to a given path.

Stage 11's reproducibility criterion needs the same pipeline run on two
independently provisioned machines and the results compared (ADR 0027
Decision 3). This is the "run it" half; ``compare_reference_numbers.py`` is
the "compare" half.

A script rather than a test, deliberately. A test asserts against a golden
committed to this repository, which proves a clone is self-consistent; the
property Stage 11 wants is that *two machines agree with each other*, and
neither can assert that alone. Splitting run from compare is what lets each
machine do its half.

Usage::

    uv run python scripts/export_reference_numbers.py numbers.json
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

# The repository root, so `tests.prisma_helpers` imports as a package -- it
# imports `tests.log_bytes_helpers` internally, which a bare `tests/` on the
# path cannot resolve.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests.prisma_helpers import (
    copy_reference_project_with_criteria,
    screen_reference_project,
)

from prismabib.report.export import export_project
from prismabib.store.load import build_store


def main() -> int:
    """Write the reference project's exported ``numbers.json`` to ``argv[1]``.

    Returns:
        ``0`` on success. Any failure raises rather than returning non-zero:
        a partial or missing file must not read as a successful export whose
        numbers merely differ.
    """
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} <output.json>", file=sys.stderr)
        return 2

    destination = Path(sys.argv[1])
    workspace = Path(tempfile.mkdtemp(prefix="prismabib-repro-"))
    try:
        project = copy_reference_project_with_criteria(workspace)
        build_store(project, rebuild=True)
        screen_reference_project(project)
        result = export_project(project)
        numbers = json.loads((result.root / "numbers.json").read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(workspace, ignore_errors=True)

    # `sort_keys` and an explicit newline: this file is compared byte-for-byte
    # against another machine's copy, so its own serialisation must not be a
    # source of difference. Dict ordering is insertion-ordered in CPython and
    # therefore stable here, but relying on that would make the comparison
    # depend on a language guarantee rather than on the numbers.
    destination.write_text(json.dumps(numbers, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {len(numbers)} numbers to {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
