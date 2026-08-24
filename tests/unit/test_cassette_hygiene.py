"""A positive guard on the committed cassettes (BUILD_PLAN §2.5, §3.7.5).

``detect-secrets`` is configured to skip ``tests/fixtures/cassettes/*.json``,
because Scopus's ``authkeywords`` field name trips its keyword heuristic on every
entry that has one and JSON cannot carry an inline ``pragma: allowlist secret``.
That exclusion is necessary but blunt: it silently covers every *future* cassette
too, so an added fixture would never be scanned.

This test is the positive half of that trade. It asserts directly what the
excluded scanner would otherwise have checked, and it applies to whatever is in
the directory rather than to a fixed list, so a cassette added later is covered
the moment it lands. The repository is public and a push is irreversible, so this
guard exists to fail *before* a credential is published, not to detect one after.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

_TESTS_ROOT = Path(__file__).parent.parent

# Every generated data file the `detect-secrets` hook is configured to skip. Keep this
# in step with `.pre-commit-config.yaml`'s exclude: the exclusion and this guard are a
# pair, and a path excluded there but absent here is unchecked by anything.
_GENERATED_DATA = sorted(
    [
        *(_TESTS_ROOT / "fixtures" / "cassettes").glob("*.json"),
        *(_TESTS_ROOT / "fixtures" / "projects").rglob("*.json"),
        *(_TESTS_ROOT / "fixtures" / "projects").rglob("*.jsonl"),
        *(_TESTS_ROOT / "golden").rglob("*.json"),
    ]
)

_CASSETTES = _GENERATED_DATA

# Elsevier keys are 32 hex characters. Any bare 32-hex token in a fixture is
# either a real credential or something indistinguishable from one; neither
# belongs in a public repository.
_ELSEVIER_KEY_SHAPE = re.compile(r"\b[0-9a-f]{32}\b")

_CREDENTIAL_MARKERS = ("X-ELS-APIKey", "X-ELS-Insttoken", "apiKey=", "insttoken=")


@pytest.mark.unit
def test_cassettes__fixtures_exist__so_this_guard_is_not_vacuously_true() -> None:
    """Guard the guard: an empty glob would make every assertion below pass."""
    assert _CASSETTES


@pytest.mark.unit
@pytest.mark.parametrize("cassette", _CASSETTES, ids=lambda p: p.name)
def test_cassettes__committed_fixtures__contain_no_credentials(cassette: Path) -> None:
    text = cassette.read_text(encoding="utf-8")

    assert not [marker for marker in _CREDENTIAL_MARKERS if marker in text]
    assert not _ELSEVIER_KEY_SHAPE.findall(text)


@pytest.mark.unit
@pytest.mark.parametrize(
    "document", [p for p in _GENERATED_DATA if p.suffix == ".json"], ids=lambda p: p.name
)
def test_generated_data__json_documents__parse(document: Path) -> None:
    """A fixture that no longer parses would silently disable the tests that read it."""
    assert json.loads(document.read_text(encoding="utf-8"))


@pytest.mark.unit
@pytest.mark.parametrize(
    "lines_file", [p for p in _GENERATED_DATA if p.suffix == ".jsonl"], ids=lambda p: p.name
)
def test_generated_data__json_lines_files__parse_line_by_line(lines_file: Path) -> None:
    """Layer 0 pages are JSON *Lines* -- one object per line, not one document.

    Split from the ``.json`` case rather than branching inside one test: §3.7.3
    rule 9 says an ``if`` in a test means it is two tests. It is also the property
    that makes ``payload_line`` able to address a record at all, so it deserves its
    own assertion rather than a branch.
    """
    parsed = [
        json.loads(line)
        for line in lines_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert parsed
