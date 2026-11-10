"""The home screen's rules: first-run check, Projects folder, listing (ticket #322)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import prismabib
from prismabib import doctor_checks
from prismabib.doctor_checks import Check
from prismabib.gui import home_model as hm
from prismabib.user_settings import (
    PROJECTS_FOLDER_KEY,
    USER_FILE_OVERRIDE_VAR,
    read_user_setting,
    write_user_settings,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _user_file(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path_factory.mktemp("user") / "settings.env"
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(path))
    return path


OK = [Check("ok", "python 3.11.9"), Check("ok", "git: x"), Check("ok", "uv: x")]


def test_uv_check__missing__fails_and_found__passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_checks.shutil, "which", lambda name: None)
    assert doctor_checks.uv_check().level == "FAIL"
    monkeypatch.setattr(doctor_checks.shutil, "which", lambda name: "/bin/uv")
    assert doctor_checks.uv_check().level == "ok"


def test_prerequisite_problems__passing_machine__shows_none() -> None:
    checks = dict(zip(("python", "git", "uv"), OK, strict=True))
    assert hm.prerequisite_problems(checks, windows=False, first_run=False).problems == []


def test_prerequisite_problems__each_failure__has_a_sentence_and_its_fix() -> None:
    checks = {
        "python": Check("FAIL", "python 3.10"),
        "git": Check("FAIL", "git: not found"),
        "uv": Check("FAIL", "uv: not found"),
    }
    report = hm.prerequisite_problems(checks, windows=False, first_run=False)
    assert [p.name for p in report.problems] == ["python", "git", "uv"]
    assert all(p.sentence and p.fix for p in report.problems)
    assert "uv" in report.problems[2].fix


def test_prerequisite_problems__uv_missing__is_reported_with_the_fix() -> None:
    checks = {"python": OK[0], "git": OK[1], "uv": Check("FAIL", "uv: not found on PATH")}
    report = hm.prerequisite_problems(checks, windows=False, first_run=False)
    assert [p.name for p in report.problems] == ["uv"]
    assert "install uv" in report.problems[0].fix.lower()


@pytest.mark.parametrize(
    ("windows", "first_run", "failing", "shown"),
    [
        (False, True, True, False),
        (True, False, False, False),
        (True, True, False, True),
        (True, False, True, True),
    ],
)
def test_prerequisite_problems__app_control_hint__only_when_the_check_reports_it(
    windows: bool, first_run: bool, failing: bool, shown: bool
) -> None:
    checks = dict(zip(("python", "git", "uv"), OK, strict=True))
    if failing:
        checks["uv"] = Check("FAIL", "uv: not found")
    report = hm.prerequisite_problems(checks, windows=windows, first_run=first_run)
    assert (report.app_control_hint is not None) is shown


def test_default_checks__are_python_git_and_uv() -> None:
    assert list(hm.default_checks()) == ["python", "git", "uv"]


def test_take_first_run__true_once_then_false() -> None:
    assert hm.take_first_run() is True
    assert hm.take_first_run() is False


def test_check_folder__240_characters__is_accepted_and_241_refused(tmp_path: Path) -> None:
    base = str(tmp_path)
    for length, refused in ((240, False), (241, True)):
        path = Path(base + os.sep + "a" * (length - len(base) - 1))
        assert len(str(path)) == length
        result = hm.check_folder(path)
        if refused:
            assert result.refusal and "240" in result.refusal
        else:
            assert result.refusal is None, result.refusal


def test_check_folder__cannot_write__is_refused(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    result = hm.check_folder(blocker / "projects")
    assert result.refusal and "not writable" in result.refusal


def test_check_folder__inside_the_installed_package__is_refused() -> None:
    package = Path(prismabib.__file__).parent
    assert hm.check_folder(package / "projects").refusal


def test_check_folder__synced_location__warns_without_blocking(tmp_path: Path) -> None:
    result = hm.check_folder(tmp_path, is_synced=lambda p: True)
    assert result.refusal is None
    assert result.warning and "sync" in result.warning.lower()
    assert hm.check_folder(tmp_path, is_synced=lambda p: False).warning is None


@pytest.mark.parametrize(
    ("path", "synced"),
    [
        ("C:/Users/a/OneDrive/Docs", True),
        ("C:/Users/a/OneDrive - Uni/Docs", True),
        ("/home/a/Dropbox/x", True),
        ("/home/a/work/x", False),
    ],
)
def test_is_synced__default_rule(path: str, synced: bool) -> None:
    assert hm.is_synced(Path(path)) is synced


def test_resolve_folder__explicit_beats_remembered_beats_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    assert hm.resolve_folder(None).path == (tmp_path / "projects")
    write_user_settings({PROJECTS_FOLDER_KEY: str(tmp_path / "mine")})
    assert hm.resolve_folder(None).path == tmp_path / "mine"
    assert hm.resolve_folder(tmp_path / "given").path == tmp_path / "given"


def test_resolve_folder__remembered_folder_gone__says_so_and_offers_another(
    tmp_path: Path,
) -> None:
    gone = tmp_path / "gone"
    write_user_settings({PROJECTS_FOLDER_KEY: str(gone)})
    state = hm.resolve_folder(None)
    assert state.missing and "no longer exists" in state.missing
    assert state.path == gone


def test_use_folder__remembers_the_choice_and_creates_when_asked(tmp_path: Path) -> None:
    target = tmp_path / "new" / "Projects"
    assert hm.use_folder(target, create=False).refusal  # does not exist yet
    assert not target.exists()
    result = hm.use_folder(target, create=True)
    assert result.refusal is None and target.is_dir()
    assert hm.resolve_folder(None).path == target
    assert read_user_setting(PROJECTS_FOLDER_KEY) == str(target)


def test_use_folder__refused_path__is_not_remembered(tmp_path: Path) -> None:
    long = tmp_path / ("a" * 250)
    assert hm.use_folder(long, create=True).refusal
    assert read_user_setting(PROJECTS_FOLDER_KEY) is None
    assert not long.exists()


def test_list_projects__finds_only_project_folders(tmp_path: Path) -> None:
    from prismabib.gui import layer

    layer.create_project(
        "demo", title="Demo title", root=tmp_path, source_mode="openalex", reviewer="Ada"
    )
    (tmp_path / "not-a-project").mkdir()
    assert hm.list_projects(tmp_path) == [hm.ProjectEntry("demo", "Demo title")]
    assert hm.list_projects(tmp_path / "missing") == []


def test_home_operations__write_nothing_inside_the_package(tmp_path: Path) -> None:
    package = Path(prismabib.__file__).parent

    def snapshot() -> dict[str, tuple[int, int]]:
        return {
            str(p): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in package.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        }

    before = snapshot()
    hm.use_folder(tmp_path / "p", create=True)
    hm.use_folder(package / "p", create=True)
    hm.list_projects(tmp_path)
    hm.take_first_run()
    assert snapshot() == before
    assert not (package / "p").exists()
    assert stat.S_ISDIR(package.stat().st_mode)
