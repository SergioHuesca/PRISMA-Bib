"""``prismabib doctor`` polish: environment section, opt-in network checks, mode capabilities.

No test makes a network request or reads a real key: the environment is monkeypatched, HTTP goes
through respx, and the Scopus probe is replaced.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner

from prismabib import credentials, doctor_checks, source_commands
from prismabib import source_mode as sm
from prismabib.cli import app
from prismabib.project import Project

pytestmark = pytest.mark.integration

runner = CliRunner()
DUMMY_KEY = "dummy-openalex-key"  # pragma: allowlist secret


@pytest.fixture(autouse=True)
def clean_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no real .env, not a git repo
    for name in ("SCOPUS_API_KEY", "SCOPUS_INSTTOKEN", "OPENALEX_API_KEY", "UNPAYWALL_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PRISMABIB_PROJECTS_ROOT", str(tmp_path / "projects"))


def _doctor(*args: str) -> Any:
    return runner.invoke(app, ["doctor", *args])


def _project(tmp_path: Path, mode: str) -> None:
    Project.init("demo", title="Demo", root=tmp_path, source_mode=mode, actor="tester")  # type: ignore[arg-type]


def test_doctor__no_slug__prints_environment_only_and_leaves_no_debris(tmp_path: Path) -> None:
    result = _doctor()

    assert result.exit_code == 0, result.output  # .env warning does not fail
    assert result.output.startswith("environment:")
    for needle in ("ok   python 3.", "prismabib ", "platform:", "git:", "projects root:", ".env:"):
        assert needle in result.output
    assert "sources mode" not in result.output
    assert not (tmp_path / "projects").exists()  # creatable, but not created


def test_doctor__with_slug__environment_first_then_project_output(tmp_path: Path) -> None:
    _project(tmp_path, "scopus")

    out = _doctor("demo", "--root", str(tmp_path)).output

    assert out.index("environment:") < out.index("sources mode: scopus")
    assert out.rstrip().endswith("next: prismabib search demo")


@pytest.mark.parametrize("mode", sm.SOURCE_MODES)
def test_doctor__lists_every_command_as_enabled_or_refused(tmp_path: Path, mode: str) -> None:
    _project(tmp_path, mode)

    out = _doctor("demo", "--root", str(tmp_path)).output
    enabled = next(ln for ln in out.splitlines() if ln.startswith("  enabled: "))
    refused = next(ln for ln in out.splitlines() if "not available in this mode: " in ln)

    for command, modes in sm.COMMAND_MODES.items():
        shown = enabled if mode in modes else refused
        assert f" {command}" in shown.replace(":", "") + ",", (command, shown)


def test_doctor__command_added_to_table__is_shown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, "scopus")
    monkeypatch.setitem(sm.COMMAND_MODES, "brand-new", frozenset({"openalex"}))

    out = _doctor("demo", "--root", str(tmp_path)).output

    assert "brand-new" in next(ln for ln in out.splitlines() if "not available" in ln)


def test_doctor__python_too_old__fails() -> None:
    check = doctor_checks.python_check((3, 10, 9), "/usr/bin/python3")

    assert check.level == "FAIL" and "3.10.9" in check.line and "/usr/bin/python3" in check.line
    assert doctor_checks.python_check((3, 11, 0)).level == "ok"


def test_doctor__git_missing__exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_checks.shutil, "which", lambda _name: None)

    result = _doctor()

    assert result.exit_code == 1
    assert "FAIL git: not found on PATH" in result.output


def test_doctor__projects_root_not_writable__exit_1(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: Any, **_k: Any) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(tempfile, "TemporaryFile", refuse)

    result = _doctor()

    assert result.exit_code == 1
    assert "FAIL projects root:" in result.output


def test_doctor__windows_hint_only_on_windows() -> None:
    windows, _ = doctor_checks.environment_report(None, system="Windows")
    linux, _ = doctor_checks.environment_report(None, system="Linux")

    assert any("python.org" in ln for ln in windows)
    assert not any("python.org" in ln for ln in linux)


def test_doctor__env_file_not_git_ignored__warns_without_value(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"OPENALEX_API_KEY={DUMMY_KEY}\n", encoding="utf-8")

    out = _doctor().output

    assert "warn .env: present, not safely git-ignored" in out
    assert DUMMY_KEY not in out


def test_doctor__default__makes_no_request() -> None:
    with respx.mock(assert_all_called=False) as router:
        result = _doctor()

    assert result.exit_code == 0 and not router.calls


def test_doctor__online_without_key__reports_reachable_and_asks_for_key() -> None:
    with respx.mock() as router:
        route = router.get("https://api.openalex.org/").respond(200, json={})
        result = _doctor("--online")

    assert route.call_count == 1 and "Authorization" not in route.calls[0].request.headers
    assert "ok   openalex: api.openalex.org reachable (HTTP 200)" in result.output
    assert "credentials set openalex" in result.output


def test_doctor__online_with_key__sends_it_and_never_prints_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", DUMMY_KEY)
    with respx.mock() as router:
        route = router.get("https://api.openalex.org/").respond(200, json={})
        result = _doctor("--online")

    assert route.calls[0].request.headers["Authorization"] == f"Bearer {DUMMY_KEY}"
    assert DUMMY_KEY not in result.output and "credentials set openalex" not in result.output


@pytest.mark.parametrize(
    ("effect", "expected"),
    [(httpx.ConnectError("x"), "network error ConnectError"), (httpx.Response(503), "HTTP 503")],
)
def test_doctor__online_failure__warns_exit_0(effect: Any, expected: str) -> None:
    with respx.mock() as router:
        router.get("https://api.openalex.org/").mock(side_effect=[effect])
        result = _doctor("--online")

    assert result.exit_code == 0
    assert "warn openalex:" in result.output and expected in result.output


def test_doctor__check_scopus_without_slug__refused() -> None:
    result = _doctor("--check-scopus")

    assert result.exit_code == 1
    assert "needs a project" in result.output
    assert "environment:" not in result.output


def test_doctor__check_scopus_in_openalex_mode__refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _project(tmp_path, "openalex")
    monkeypatch.setattr(source_commands, "probe_scopus_key", _boom)

    result = _doctor("demo", "--check-scopus", "--root", str(tmp_path))

    assert result.exit_code == 1 and "not available in sources mode 'openalex'" in result.output


def _boom(*_a: Any, **_k: Any) -> None:
    raise AssertionError("a Scopus request was made")


@pytest.mark.parametrize(("ok", "code"), [(True, 0), (False, 1)])
def test_doctor__check_scopus__reuses_the_probe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ok: bool, code: int
) -> None:
    _project(tmp_path, "scopus")
    monkeypatch.setattr(
        source_commands, "probe_scopus_key", lambda _s: credentials.ProbeResult(ok, "label-x")
    )

    result = _doctor("demo", "--check-scopus", "--root", str(tmp_path))

    assert result.exit_code == code and "scopus: label-x" in result.output
