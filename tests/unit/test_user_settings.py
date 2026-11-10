"""Unit tests for ``prismabib.user_settings`` (ticket #317). Fake values only; no real user folder."""

from __future__ import annotations

from pathlib import Path

import pytest

from prismabib.config import Settings, require_scopus_key_for_mode
from prismabib.credentials import write_env
from prismabib.errors import ConfigError
from prismabib.user_settings import (
    PROJECTS_FOLDER_KEY,
    REVIEWER_NAME_KEY,
    USER_FILE_OVERRIDE_VAR,
    load_settings,
    read_user_setting,
    user_settings_path,
    write_user_settings,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SCOPUS_API_KEY", raising=False)


def _key(settings: Settings) -> str | None:
    return settings.scopus_api_key.get_secret_value() if settings.scopus_api_key else None


@pytest.mark.unit
def test_user_file__no_env_file__key_is_read(tmp_path: Path) -> None:
    user = tmp_path / "cfg" / "settings.env"
    write_user_settings({"SCOPUS_API_KEY": "fake-user-key"}, user)  # pragma: allowlist secret

    assert _key(load_settings(None, user)) == "fake-user-key"


@pytest.mark.unit
def test_user_file__project_env_defines_key__project_wins_and_cwd_is_irrelevant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    user = tmp_path / "cfg" / "settings.env"
    project_env = tmp_path / "p" / ".env"
    project_env.parent.mkdir()
    write_user_settings(
        {
            "SCOPUS_API_KEY": "fake-user-key",  # pragma: allowlist secret
            "OPENALEX_API_KEY": "fake-oa",  # pragma: allowlist secret
        },
        user,
    )
    project_env.write_text(
        "SCOPUS_API_KEY=fake-project-key\n",  # pragma: allowlist secret
        encoding="utf-8",
    )
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / ".env").write_text("SCOPUS_API_KEY=fake-cwd-key\n", encoding="utf-8")
    monkeypatch.chdir(elsewhere)

    settings = load_settings(project_env, user)

    assert _key(settings) == "fake-project-key"  # the Project wins where both define it
    assert settings.openalex_api_key is not None  # and the user file fills the rest
    assert _key(load_settings(tmp_path / "missing.env", user)) == "fake-user-key"  # other way


@pytest.mark.unit
def test_user_settings_path__default_and_override__never_inside_a_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(USER_FILE_OVERRIDE_VAR, raising=False)
    project = tmp_path / "proj"
    project.mkdir()
    (project / "project.toml").write_text("", encoding="utf-8")
    monkeypatch.chdir(project)

    assert project.resolve() not in user_settings_path().parents  # default, cwd in a Project

    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(project / "s.env"))
    with pytest.raises(ConfigError, match="inside a Project"):
        user_settings_path()

    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "ok" / "s.env"))
    assert user_settings_path() == (tmp_path / "ok" / "s.env").resolve()


@pytest.mark.unit
def test_write__user_file_needs_no_git_check__in_repo_env_still_does(tmp_path: Path) -> None:
    write_user_settings(
        {"SCOPUS_API_KEY": "fake-user-key"},  # pragma: allowlist secret
        tmp_path / "u" / "settings.env",
    )

    with pytest.raises(ConfigError, match="Refusing to write"):  # tmp_path is no git repository
        write_env(
            tmp_path / ".env",
            {"SCOPUS_API_KEY": "fake-user-key"},  # pragma: allowlist secret
        )
    assert not (tmp_path / ".env").exists()


@pytest.mark.unit
def test_user_file__projects_folder_and_reviewer__round_trip(tmp_path: Path) -> None:
    user = tmp_path / "settings.env"
    write_user_settings(
        {PROJECTS_FOLDER_KEY: "D:/My Projects", REVIEWER_NAME_KEY: "Ana Pérez"}, user
    )

    assert read_user_setting(PROJECTS_FOLDER_KEY, user) == "D:/My Projects"
    assert read_user_setting(REVIEWER_NAME_KEY, user) == "Ana Pérez"
    assert read_user_setting(REVIEWER_NAME_KEY, tmp_path / "none.env") is None


@pytest.mark.unit
def test_write_user_settings__unknown_key__refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        write_user_settings({"PATH": "x"}, tmp_path / "s.env")


@pytest.mark.unit
def test_require_scopus_key_for_mode__missing_key__only_outside_openalex() -> None:
    settings = Settings(_env_file=None)

    assert require_scopus_key_for_mode(settings, "openalex") is None
    with pytest.raises(ConfigError, match="Scopus key is needed in mode 'scopus'"):
        require_scopus_key_for_mode(settings, "scopus")
    with pytest.raises(ConfigError, match="Scopus key is needed"):
        require_scopus_key_for_mode(settings, "scopus+openalex")
