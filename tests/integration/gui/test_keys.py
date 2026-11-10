"""The keys screen, built headless with a stubbed transport (ticket #325)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import httpx
import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.gui import layer
from prismabib.gui.keys import build_keys
from prismabib.gui.new_project import build_stepper
from prismabib.project import Project
from prismabib.report.export import export_project
from prismabib.store.load import build_store
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR, write_user_settings
from tests.integration.gui.helpers import text as _text
from tests.integration.gui.test_home import unlabelled_controls
from tests.openalex_search_helpers import work, write_search_run

pytestmark = pytest.mark.integration

SECRET = "oa-secret-0123456789"  # pragma: allowlist secret
SCOPUS_SECRET = "sc-secret-0123456789"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def _clean_env(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path_factory.mktemp("user") / "s.env"))
    for name in ("OPENALEX_API_KEY", "SCOPUS_API_KEY", "SCOPUS_INSTTOKEN"):
        monkeypatch.delenv(name, raising=False)


def _make(tmp_path: Path, mode: str = "openalex") -> Project:
    return layer.create_project("demo", title="Demo", root=tmp_path, source_mode=mode, reviewer="A")  # type: ignore[arg-type]


def _client(status: int) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(status)))


def _w(view: pn.viewable.Viewable, kind: type[pn.widgets.Widget], name: str) -> pn.widgets.Widget:
    return next(w for w in view.select(kind) if w.name == name)


def _save(view: pn.Column, value: str = SECRET) -> None:
    _w(view, pn.widgets.PasswordInput, "OpenAlex API key").value = value
    _w(view, pn.widgets.Button, "Save OpenAlex key").clicks += 1


def _test(view: pn.Column) -> None:
    _w(view, pn.widgets.Button, "Test OpenAlex key").clicks += 1


def test_keys__save_then_test__readable_result(tmp_path: Path) -> None:
    view = build_keys(_make(tmp_path / "p"), http_client=_client(200))
    _save(view)
    _test(view)
    assert "The key works." in _text(view)


def test_keys__rejected_key__shows_the_next_step_not_a_traceback(tmp_path: Path) -> None:
    view = build_keys(_make(tmp_path / "p"), http_client=_client(401))
    _save(view)
    _test(view)
    text = _text(view)
    assert "rejected" in text and "paste it here" in text and "Traceback" not in text


def test_keys__test_without_a_key__says_nothing_to_test(tmp_path: Path) -> None:
    view = build_keys(_make(tmp_path / "p"), http_client=_client(200))
    _test(view)
    assert "nothing to test" in _text(view)


def test_keys__after_entry__masked_and_cleared_and_never_shown(tmp_path: Path) -> None:
    view = build_keys(_make(tmp_path / "p"), http_client=_client(200))
    field = _w(view, pn.widgets.PasswordInput, "OpenAlex API key")
    _save(view)
    assert (field.value, field.value_input) == ("", "")
    assert SECRET not in _text(view) and "your per-user settings file" in _text(view)


def test_keys__saved__file_outside_project_and_no_key_in_project_or_status(
    tmp_path: Path, _clean_env: None
) -> None:
    project = _make(tmp_path / "p")
    subprocess.run(["git", "init", "-q"], cwd=project.root, check=True)
    view = build_keys(project)
    _save(view)
    from prismabib.user_settings import user_settings_path

    path = user_settings_path()
    assert SECRET in path.read_text(encoding="utf-8")
    assert project.root not in path.parents
    assert all(
        SECRET not in p.read_bytes().decode("utf-8", "ignore")
        for p in project.root.rglob("*")
        if p.is_file() and ".git" + "/objects" not in p.as_posix()
    )
    status = subprocess.run(
        ["git", "status", "--porcelain", "--ignored"],
        cwd=project.root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert SECRET not in status and ".env" not in status


def test_keys__save__no_key_in_the_log_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    view = build_keys(_make(tmp_path / "p"), http_client=_client(200))
    _save(view)
    _test(view)
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err + caplog.text


def test_keys__openalex_only__no_scopus_key_asked_and_project_reaches_export(
    tmp_path: Path,
) -> None:
    project = _make(tmp_path / "p")
    view = build_keys(project)
    assert "OpenAlex only" in _text(view)
    assert not any("Scopus" in str(w.name) for w in view.select(pn.widgets.Widget))
    assert "**Scopus key is needed**" not in _text(view)
    _save(view)
    write_search_run(project, "20261103T100000Z-aaaaaaaa", "carbon capture", [[work(1), work(2)]])
    build_store(project, rebuild=True)
    export_project(project)  # no Scopus key anywhere in the environment
    assert layer.step_progress(project)["export"]


@pytest.mark.parametrize("mode", ["scopus", "scopus+openalex"])
def test_keys__scopus_modes__state_the_key_is_needed_and_show_the_panel(
    tmp_path: Path, mode: str
) -> None:
    view = build_keys(_make(tmp_path / "p", mode))
    assert "Scopus key is needed" in _text(view)
    assert any(w.label == "Scopus API key" for w in view.select(pn.widgets.PasswordInput))


def test_keys__scopus_panel__saves_to_the_user_file_and_tests(tmp_path: Path) -> None:
    project = _make(tmp_path / "p", "scopus")
    view = build_keys(project, http_client=_client(401))
    scopus = next(w for w in view.select(pn.widgets.PasswordInput) if w.label == "Scopus API key")
    scopus.value = SCOPUS_SECRET
    _w(view, pn.widgets.Button, "Save key").clicks += 1
    from prismabib.user_settings import read_user_setting

    assert read_user_setting("SCOPUS_API_KEY") == SCOPUS_SECRET
    _w(view, pn.widgets.Button, "Test Scopus key").clicks += 1
    assert "401" in _text(view) and SCOPUS_SECRET not in _text(view)


def test_keys__project_file_defines_key__screen_says_it_wins(tmp_path: Path) -> None:
    project = _make(tmp_path / "p")
    write_user_settings({"OPENALEX_API_KEY": "user-key-0123456789"})  # pragma: allowlist secret
    assert "your per-user settings file" in _text(build_keys(project))
    (project.root / ".env").write_text(
        "OPENALEX_API_KEY=proj-key-0123456789\n",  # pragma: allowlist secret
        encoding="utf-8",
    )
    assert "this Project's environment file" in _text(build_keys(project))


def test_keys__stepper_source_mode_step__carries_the_screen(tmp_path: Path) -> None:
    view = build_stepper(_make(tmp_path / "p"))
    _w(view, pn.widgets.Button, "Source mode and keys (done)").clicks += 1
    assert any(w.name == "OpenAlex API key" for w in view.select(pn.widgets.PasswordInput))


@pytest.mark.parametrize("mode", ["openalex", "scopus"])
def test_keys__every_control__is_labelled(tmp_path: Path, mode: str) -> None:
    view = build_keys(_make(tmp_path / "p", mode))
    assert view.select(pn.widgets.Widget)
    assert [w for w in unlabelled_controls(view) if not getattr(w, "label", "")] == []
