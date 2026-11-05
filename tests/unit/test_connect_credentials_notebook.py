"""notebooks/00_connect_credentials.ipynb: hygiene, and its code cells run against a temp repo."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

NOTEBOOK = Path(__file__).resolve().parents[2] / "notebooks" / "00_connect_credentials.ipynb"


def _cells() -> list[dict[str, Any]]:
    return json.loads(NOTEBOOK.read_text(encoding="utf-8"))["cells"]


def _code() -> str:
    return "\n".join("".join(c["source"]) for c in _cells() if c["cell_type"] == "code")


def test_notebook__is_valid_stripped_and_holds_no_key_like_literal() -> None:
    notebook = json.loads(NOTEBOOK.read_text(encoding="utf-8"))

    assert notebook["nbformat"] == 4
    for cell in notebook["cells"]:
        if cell["cell_type"] == "code":
            assert cell["outputs"] == [] and cell["execution_count"] is None
    text = NOTEBOOK.read_text(encoding="utf-8")
    assert not re.search(r"[A-Za-z0-9_\-]{32,}", text)
    assert "pn.widgets" not in text  # the panel's own widgets stay in credentials_ui


def test_notebook__cells_construct_the_panel_at_the_repository_root_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "notebooks").mkdir()
    monkeypatch.chdir(repo / "notebooks")
    monkeypatch.delenv("PRISMABIB_NOTEBOOK_SLUG", raising=False)
    namespace: dict[str, Any] = {}

    exec(compile(_code(), str(NOTEBOOK), "exec"), namespace)  # noqa: S102

    panel = namespace["panel"]
    assert panel.env_file == repo.resolve() / ".env"
    assert panel.unused is False
    assert "Scopus key: not set" in panel.status.object
    assert not (repo / ".env").exists()  # constructing the panel writes nothing
