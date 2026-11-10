"""`prismabib init` text and on-disk result, frozen before the GUI-spec #313 extraction.

The golden file was produced by the pre-extraction CLI; the test fails if any case's stdout, exit
code or written files change by a byte (event ids and timestamps are normalised away).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prismabib.cli import app

pytestmark = pytest.mark.integration

GOLDEN = Path(__file__).resolve().parents[1] / "golden" / "cli" / "init_cases.json"
_VOLATILE = re.compile(r'"(event_id|ts)": ?"[^"]*"')

# name -> (args, stdin answers, stdin is a tty, openalex key missing, pre-create the project)
CASES: dict[str, tuple[list[str], str, bool, bool, bool]] = {
    "sources_openalex_key_missing": (["--sources", "openalex"], "", False, True, False),
    "sources_openalex_key_present": (["--sources", "openalex"], "", False, False, False),
    "sources_scopus": (["--sources", "scopus"], "", False, True, False),
    "sources_both": (["--sources", "scopus+openalex"], "", False, True, False),
    "ask_yes_fill": ([], "y\ny\n", True, True, False),
    "ask_yes_no_fill": ([], "YES\nn\n", True, True, False),
    "ask_no": ([], "n\n", True, True, False),
    "ask_not_sure": ([], "not  sure\n", True, False, False),
    "ask_bad_then_s": ([], "maybe\ns\n", True, False, False),
    "no_tty_no_sources": ([], "", False, True, False),
    "rerun_existing": (["--sources", "scopus"], "", False, True, True),
    "rerun_mismatch": (["--sources", "openalex"], "", False, True, True),
}


def _disk(root: Path) -> dict[str, str]:
    files: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        text = _VOLATILE.sub(r'"\1": "X"', path.read_text(encoding="utf-8"))
        if path.suffix == ".sha256":  # a digest of the log, which holds ids and timestamps
            text = "X"
        files[path.relative_to(root).as_posix()] = hashlib.sha256(text.encode()).hexdigest()
    return files


def run_case(name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    args, answers, tty, key_missing, pre = CASES[name]
    monkeypatch.setattr("prismabib.cli._stdin_is_tty", lambda: tty)
    monkeypatch.setattr("prismabib.cli.openalex_key_missing", lambda: key_missing)
    base = ["init", "demo", "--actor", "Ada", "--root", str(tmp_path)]
    if pre:
        CliRunner().invoke(app, [*base, "--sources", "scopus"])
    result = CliRunner().invoke(app, [*base, *args], input=answers)
    output = result.output.replace(str(tmp_path), "<root>").replace("\\", "/")
    return {"exit": result.exit_code, "output": output, "files": _disk(tmp_path)}


@pytest.mark.parametrize("name", sorted(CASES))
def test_init__case__matches_golden(
    name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert run_case(name, tmp_path, monkeypatch) == golden[name]
