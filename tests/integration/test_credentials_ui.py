"""ADR 0042 Decision 8 and tests 70 and 84: the Connect Scopus panel keeps the key out of state."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from prismabib.credentials_ui import ConnectScopusPanel
from prismabib.sources import scopus
from tests.integration.test_credentials import SENTINEL, leaks, make_repo
from tests.openalex_search_helpers import openalex_project

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.delenv("SCOPUS_API_KEY", raising=False)
    saved = set(scopus._REDACTED_SECRETS)  # pyright: ignore[reportPrivateUsage]
    yield
    scopus._REDACTED_SECRETS.clear()  # pyright: ignore[reportPrivateUsage]
    scopus._REDACTED_SECRETS.update(saved)  # pyright: ignore[reportPrivateUsage]


def _models(panel: ConnectScopusPanel) -> str:
    """Everything Panel would serialise for the panel's widgets (what a notebook output embeds)."""
    root = panel.view().get_root()
    return json.dumps(root.document.to_json() if root.document else {}, default=str)  # type: ignore[no-untyped-call]


def test_gui_connect_scopus__value_cleared_after_write_and_not_persisted(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    panel = ConnectScopusPanel(env_file=repo / ".env")
    assert str(panel.status.object).startswith("Scopus key: not set.")

    panel.key_input.value = SENTINEL
    panel.key_input.value_input = SENTINEL
    message = panel.save()

    assert message == "Saved."
    assert panel.key_input.value == "" and panel.key_input.value_input == ""
    assert str(panel.status.object).startswith("Scopus key: set.")
    assert SENTINEL in scopus._REDACTED_SECRETS  # pyright: ignore[reportPrivateUsage]
    assert SENTINEL in (repo / ".env").read_text(encoding="utf-8")  # the one place it lives
    shown = f"{panel.status.object}{panel.alert.object}{message}{_models(panel)}"
    assert not leaks(shown)


def test_gui_connect_scopus__refused_write__value_still_cleared_and_not_echoed(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repo"
    root.mkdir()  # not a git repository: the writer refuses
    panel = ConnectScopusPanel(env_file=root / ".env")
    panel.key_input.value = SENTINEL

    message = panel.save()

    assert message.startswith("Refused:")
    assert panel.key_input.value == "" and panel.key_input.value_input == ""
    assert not (root / ".env").exists()
    assert not leaks(message + str(panel.alert.object) + _models(panel))


@pytest.mark.parametrize(("mode", "unused"), [("scopus", False), ("openalex", True)])
def test_gui_connect_scopus__enabled_in_every_mode_labelled_unused_in_openalex(
    tmp_path: Path, mode: str, unused: bool
) -> None:
    panel = ConnectScopusPanel(openalex_project(tmp_path, mode), env_file=tmp_path / ".env")

    assert not panel.save_button.disabled
    assert panel.unused is unused
    assert ("unused in this mode" in str(panel.title.object)) is unused


def test_no_secret__notebooks_clean(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Test 84: a notebook that displays the panel after use holds no form of the sentinel."""
    repo = make_repo(tmp_path / "repo")
    panel = ConnectScopusPanel(env_file=repo / ".env")
    panel.key_input.value = SENTINEL
    panel.save()
    notebook = repo / "notebooks" / "throwaway.ipynb"
    notebook.parent.mkdir()
    cell = {"cell_type": "code", "metadata": {}, "execution_count": 1, "source": ["panel"]}
    cell["outputs"] = [
        {"output_type": "display_data", "metadata": {}, "data": {"text/plain": [_models(panel)]}}
    ]
    notebook.write_text(
        json.dumps({"cells": [cell], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}),
        encoding="utf-8",
    )

    streams = capsys.readouterr()
    assert not leaks(streams.out + streams.err)
    for path in repo.glob("notebooks/*.ipynb"):
        assert not leaks(path.read_text(encoding="utf-8"))


def test_gui_connect_scopus__status_names_the_file_checked(tmp_path: Path) -> None:
    env_file = tmp_path / "notebooks" / ".env"

    panel = ConnectScopusPanel(env_file=env_file)

    assert str(env_file) in str(panel.status.object)


def test_gui_connect_scopus__unreadable_env__safe_status_and_alert(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "repo")
    (repo / ".env").write_bytes(bytes([0xFF, 0xFE]) + b" SCOPUS_API_KEY=junk")  # not UTF-8
    panel = ConnectScopusPanel(env_file=repo / ".env")
    assert "could not be read" in str(panel.status.object)

    panel.key_input.value = SENTINEL
    message = panel.save()

    assert message.startswith("Refused:")
    assert panel.key_input.value == ""
    assert not leaks(message + str(panel.alert.object) + str(panel.status.object))
