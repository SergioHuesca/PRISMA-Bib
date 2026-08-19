"""Integration tests for ``src/prismabib/models.py`` (BUILD_PLAN Stage 1 Tests table, lines 731-744).

Real filesystem I/O -- :meth:`~prismabib.models.PayloadRef.resolve` reads an
actual file, unlike the pure-logic ``Record``/``Affiliation`` behaviour in
``tests/unit/test_models.py``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.models import PayloadRef


@pytest.mark.integration
@pytest.mark.acceptance("S01-AC3")
def test_payload_ref__resolves_to_originating_line(tmp_path: Path) -> None:
    raw_path = tmp_path / "raw.jsonl"
    raw_path.write_text('{"id": 0}\n{"id": 1}\n{"id": 2}\n', encoding="utf-8")
    payload_ref = PayloadRef(path=raw_path, line=1)

    resolved = payload_ref.resolve()

    assert resolved == '{"id": 1}'
