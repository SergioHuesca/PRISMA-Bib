"""ADR 0042 PR 2: credentials (Decision 8; Verification 71 to 84 that PR 2 owns).

Everything runs in temporary git repositories with dummy sentinel values; no real ``.env`` is read
and nothing touches the network (the Scopus request is a ``respx`` mock). The CLI tests assert exit
codes, our own messages and declared click parameters, never Rich-rendered help text. A sentinel
that must never appear is searched for in its raw and percent-encoded forms in every captured
stream, log event, file and exception text.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx
import pytest
import respx
import structlog
import typer
from dotenv import dotenv_values
from typer.testing import CliRunner

from prismabib import credentials as cred
from prismabib.cli import app
from prismabib.config import Settings, require_scopus_key
from prismabib.errors import ConfigError
from prismabib.project import Project
from prismabib.sources import scopus

pytestmark = pytest.mark.integration

runner = CliRunner()
#: Percent-encoding and a bare form differ, so each is searched for separately.
SENTINEL = "sent/inel+key=ZZ9-aaa"
TOKEN = "tok/en+sentinel=QQ7-bbb"
FORMS = (SENTINEL, quote_plus(SENTINEL, safe=""), TOKEN, quote_plus(TOKEN, safe=""))
_ALL_KEYS = ("SCOPUS_API_KEY", "SCOPUS_INSTTOKEN", "OPENALEX_API_KEY", "UNPAYWALL_EMAIL")


def leaks(text: str) -> bool:
    """Whether any form of a sentinel occurs in ``text``."""
    return any(form in text for form in FORMS)


def _git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", check=True
    )
    return out.stdout


def make_repo(
    path: Path, ignore: str | None = ".env\n*.env\n", *, track_ignore: bool = True
) -> Path:
    """A git repository at ``path`` with the given tracked ``.gitignore`` (or none)."""
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q")
    if ignore is not None:
        (path / ".gitignore").write_text(ignore, encoding="utf-8")
        if track_ignore:
            _git(path, "add", ".gitignore")
    return path


@pytest.fixture(autouse=True)
def _hermetic_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No user or system git config (an ``excludesFile`` there must not make a test pass)."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in _ALL_KEYS:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A repository ignoring ``.env`` / ``*.env`` through its tracked ``.gitignore``; the cwd."""
    root = make_repo(tmp_path / "repo")
    monkeypatch.chdir(root)
    return root


def _invoke(*args: str, input: str | None = None) -> Any:
    return runner.invoke(app, ["credentials", *args], input=input)


def _all_text(result: Any) -> str:
    return f"{result.output}\n{result.stdout}\n{result.stderr}\n{result.exception!r}"


# -- 71: Settings and require_scopus_key ----------------------------------------------------------


def test_settings__scopus_key_optional__require_scopus_key_names_the_variable() -> None:
    settings = Settings(_env_file=None)

    assert settings.scopus_api_key is None
    with pytest.raises(ConfigError, match=r"SCOPUS_API_KEY.*credentials set scopus"):
        require_scopus_key(settings)


def test_require_scopus_key__blank_value__counts_as_missing() -> None:
    with pytest.raises(ConfigError, match="SCOPUS_API_KEY"):
        require_scopus_key(Settings(_env_file=None, scopus_api_key="  "))  # type: ignore[arg-type]


def test_require_scopus_key__set__returns_it() -> None:
    settings = Settings(_env_file=None, scopus_api_key=SENTINEL)  # type: ignore[arg-type]

    assert require_scopus_key(settings).get_secret_value() == SENTINEL
    assert SENTINEL not in repr(settings)


# -- the OpenAlex-only fix: nothing but Scopus paths needs the Scopus key ------------------------


def test_clients__other_than_scopus__build_without_a_scopus_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismabib.sources.crossref import CrossrefTdmClient
    from prismabib.sources.openalex import OpenAlexClient
    from prismabib.sources.sciencedirect import ScienceDirectClient
    from prismabib.sources.unpaywall import UnpaywallClient

    monkeypatch.chdir(tmp_path)  # no .env
    monkeypatch.setenv("UNPAYWALL_EMAIL", "dummy@example.org")
    monkeypatch.setenv("ELSEVIER_SD_API_KEY", "sd-dummy")

    for build in (OpenAlexClient, CrossrefTdmClient, UnpaywallClient, ScienceDirectClient):
        build().close()  # type: ignore[attr-defined]


def test_capture_openalex__without_scopus_key__runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismabib.capture.openalex import capture_openalex

    monkeypatch.chdir(tmp_path)
    project = Project.init("demo", title="Demo", root=tmp_path / "projects")
    work = {
        "id": "https://openalex.org/W1",
        "doi": "https://doi.org/10.1/a",
        "title": "T",
        "publication_year": 2020,
    }
    transport = httpx.MockTransport(lambda _r: httpx.Response(200, json={"results": [work]}))

    manifest = capture_openalex(
        project, caller="openalex_command", dois=["10.1/a"], transport=transport
    )

    assert manifest is not None


def test_scopus_paths__without_key__fail_by_name_before_any_run_or_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismabib.capture.cascade import capture_scopus_cascade
    from prismabib.capture.enrich import capture_abstracts
    from prismabib.capture.writer import capture_search

    monkeypatch.chdir(tmp_path)  # no .env, no key
    project = Project.init("demo", title="Demo", root=tmp_path / "projects")
    calls = [
        lambda: capture_search(project, query="TITLE(x)"),
        lambda: capture_abstracts(project, record_ids=["1"]),
        lambda: capture_scopus_cascade(
            project, dois=["10.1/a"], fallback_opt_in=True, budget_requests=1
        ),
        lambda: scopus.ScopusClient(),
    ]

    with respx.mock(assert_all_called=False):  # an unmocked request raises
        for call in calls:
            with pytest.raises(ConfigError, match="SCOPUS_API_KEY"):
                call()

    assert not project.raw_dir.exists() or not any(project.raw_dir.iterdir())


# -- 75: the writer keeps what it does not manage -------------------------------------------------


def test_dotenv_writer__preserves_unrelated_lines_and_comments(repo: Path) -> None:
    original = (
        "# my notes\r\nFOO=bar\r\n\r\nexport OPENALEX_API_KEY=old1\r\n"
        "SCOPUS_API_KEY=old\r\nMULTI='a\r\nSCOPUS_API_KEY=inside'\r\nSCOPUS_API_KEY=dup\r\nTAIL=z"
    )
    (repo / ".env").write_bytes(original.encode())

    cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})

    text = (repo / ".env").read_bytes().decode()
    assert text == (
        "# my notes\r\nFOO=bar\r\n\r\nexport OPENALEX_API_KEY=old1\r\n"
        f"SCOPUS_API_KEY={SENTINEL}\r\nMULTI='a\r\nSCOPUS_API_KEY=inside'\r\nTAIL=z\r\n"
    )
    assert dotenv_values(repo / ".env")["SCOPUS_API_KEY"] == SENTINEL


def test_dotenv_writer__missing_file__is_created_with_only_the_key(repo: Path) -> None:
    path = cred.write_env(repo / ".env", {"OPENALEX_API_KEY": "abc"})

    assert path == (repo / ".env").resolve()
    assert (repo / ".env").read_text(encoding="utf-8") == "OPENALEX_API_KEY=abc\n"


@pytest.mark.parametrize(
    "value",
    ["a b", "x#y", "it's", "back\\slash", 'a"b', "  lead", "é", "x=y", "$FOO", "a'b\"c\\d$e#f g"],
)
def test_dotenv_writer__awkward_value__reads_back_unchanged(repo: Path, value: str) -> None:
    cred.write_env(repo / ".env", {"UNPAYWALL_EMAIL": value.strip() or "x"})

    assert dotenv_values(repo / ".env")["UNPAYWALL_EMAIL"] == (value.strip() or "x")


@pytest.mark.parametrize("value", ["a\nb", "a\rb", "a\x00b", "", "   ", "pre${FOO}post"])
def test_dotenv_writer__unusable_value__refused_and_file_untouched(repo: Path, value: str) -> None:
    (repo / ".env").write_text("KEEP=1\n", encoding="utf-8")

    with pytest.raises(ConfigError):
        cred.write_env(repo / ".env", {"SCOPUS_API_KEY": value})

    assert (repo / ".env").read_text(encoding="utf-8") == "KEEP=1\n"


def test_dotenv_writer__unmanaged_key__refused(repo: Path) -> None:
    with pytest.raises(ConfigError, match="not a credential"):
        cred.write_env(repo / ".env", {"PATH": "x"})


def test_dotenv_writer__none_removes_only_that_key(repo: Path) -> None:
    (repo / ".env").write_text("A=1\nSCOPUS_API_KEY=k\n# c\nSCOPUS_API_KEY=k2\nB=2\n")

    cred.write_env(repo / ".env", {"SCOPUS_API_KEY": None})

    assert (repo / ".env").read_text() == "A=1\n# c\nB=2\n"


# -- 76: refuses unless git confirms a tracked ignore rule ---------------------------------------


def test_dotenv_writer__refuses_when_not_ignored(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", ignore="*.log\n")

    with pytest.raises(ConfigError, match="Refusing to write"):
        cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})

    assert not (repo / ".env").exists()


def test_dotenv_writer__refuses_when_ignored_only_by_info_exclude(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", ignore="*.log\n")
    (repo / ".git" / "info" / "exclude").write_text(".env\n", encoding="utf-8")
    assert _git(repo, "check-ignore", "-v", "--", ".env")  # git does ignore it, via a local rule

    with pytest.raises(ConfigError, match=r"not a tracked .gitignore"):
        cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})

    assert not (repo / ".env").exists()


def test_dotenv_writer__refuses_when_ignored_only_by_core_excludes_file(
    tmp_path: Path,
) -> None:
    repo = make_repo(tmp_path / "r", ignore="*.log\n")
    excludes = tmp_path / "global-ignore"
    excludes.write_text("*.env\n", encoding="utf-8")
    _git(repo, "config", "core.excludesFile", str(excludes))
    assert _git(repo, "check-ignore", "-v", "--", ".env")

    with pytest.raises(ConfigError, match="outside the repository"):
        cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})


def test_dotenv_writer__refuses_when_the_gitignore_is_not_tracked(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", track_ignore=False)

    with pytest.raises(ConfigError, match="not tracked"):
        cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})


def test_dotenv_writer__refuses_when_matched_by_another_pattern(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", ignore=".e*\n")

    with pytest.raises(ConfigError, match=r"not by `\.env` or `\*\.env`"):
        cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})


def test_dotenv_writer__refuses_when_tracked(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r")
    (repo / ".env").write_text("A=1\n", encoding="utf-8")
    _git(repo, "add", "-f", ".env")

    with pytest.raises(ConfigError, match="is tracked by git"):
        cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})

    assert (repo / ".env").read_text(encoding="utf-8") == "A=1\n"


def test_dotenv_writer__refuses_outside_a_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = tmp_path / "plain"
    plain.mkdir()
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))

    with pytest.raises(ConfigError, match="not inside a git repository"):
        cred.write_env(plain / ".env", {"SCOPUS_API_KEY": SENTINEL})

    assert not (plain / ".env").exists()


def test_dotenv_writer__git_missing__refuses(
    repo: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))

    with pytest.raises(ConfigError, match="git is not installed"):
        cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})


def test_dotenv_writer__checks_the_file_written_not_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    safe = make_repo(tmp_path / "safe")
    unsafe = make_repo(tmp_path / "unsafe", ignore="*.log\n")
    monkeypatch.chdir(unsafe)  # the cwd is the unsafe repository

    cred.write_env(safe / ".env", {"OPENALEX_API_KEY": "abc"})  # fine: the target is safe
    monkeypatch.chdir(safe)
    with pytest.raises(ConfigError, match="Refusing to write"):
        cred.write_env(unsafe / ".env", {"OPENALEX_API_KEY": "abc"})  # unsafe target, safe cwd

    assert (safe / ".env").exists() and not (unsafe / ".env").exists()


def test_dotenv_writer__subdirectory_of_a_repository__uses_the_root_gitignore(repo: Path) -> None:
    (repo / "sub").mkdir()

    cred.write_env(repo / "sub" / ".env", {"OPENALEX_API_KEY": "abc"})

    assert (repo / "sub" / ".env").read_text() == "OPENALEX_API_KEY=abc\n"


# -- 77, 78: temporary file and atomicity ----------------------------------------------------------


def test_dotenv_writer__temp_file_ends_in_env_and_is_removed_on_failure(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[Path] = []

    def failing_replace(src: Any, dst: Any) -> None:
        seen.append(Path(src))
        assert Path(src).exists()
        raise PermissionError("another handle is open")

    monkeypatch.setattr(os, "replace", failing_replace)
    with pytest.raises(ConfigError) as caught:
        cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})

    [temp] = seen
    assert re.fullmatch(r"tmp-[0-9a-f]+\.env", temp.name) and fnmatch(temp.name, "*.env")
    assert not temp.exists()
    assert sorted(p.name for p in repo.iterdir() if p.name != ".git") == [".gitignore"]
    assert not leaks(str(caught.value))
    # the name is covered by the rule git was asked about, so an interrupted write could not
    # have left an unignored secret
    assert _git(repo, "check-ignore", "--", temp.name).strip() == temp.name


def test_dotenv_writer__failed_replace_leaves_old_file(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".env").write_bytes(b"KEEP=1\r\nSCOPUS_API_KEY=old\r\n")
    monkeypatch.setattr(os, "replace", lambda *_a: (_ for _ in ()).throw(OSError("boom")))

    with pytest.raises(ConfigError, match="unchanged"):
        cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})

    assert (repo / ".env").read_bytes() == b"KEEP=1\r\nSCOPUS_API_KEY=old\r\n"


def test_dotenv_writer__failure_while_writing__removes_temp_and_keeps_old_file(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".env").write_text("KEEP=1\n", encoding="utf-8")
    monkeypatch.setattr(os, "fsync", lambda *_a: (_ for _ in ()).throw(OSError("disk")))

    with pytest.raises(ConfigError):
        cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})

    assert (repo / ".env").read_text(encoding="utf-8") == "KEEP=1\n"
    assert not list(repo.glob("tmp-*.env"))


@pytest.mark.skipif(sys.platform == "win32", reason="NTFS has no POSIX mode bits (ADR 0042)")
def test_dotenv_writer__posix__file_is_mode_0600(repo: Path) -> None:
    cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})

    assert (repo / ".env").stat().st_mode & 0o777 == 0o600


# -- 73: no option accepts a value -----------------------------------------------------------------


def test_credentials_set__no_option_accepts_a_value() -> None:
    group = typer.main.get_command(app).commands["credentials"]  # type: ignore[attr-defined]
    declared = {
        name: {p.name: p for p in command.params} for name, command in group.commands.items()
    }

    assert set(declared) == {"set", "clear", "status", "test"}
    assert set(declared["set"]) == {"service", "from_stdin", "from_env"}
    for name in ("from_stdin", "from_env"):
        assert declared["set"][name].is_flag
    assert set(declared["clear"]) == {"service"}
    assert set(declared["status"]) == {"project", "root"}
    assert set(declared["test"]) == {"service", "project", "root"}


# -- 72: set writes, and prints and logs nothing secret --------------------------------------------


@pytest.fixture
def log_events() -> Iterator[list[dict[str, Any]]]:
    saved = dict(structlog.get_config())
    saved_secrets = set(scopus._REDACTED_SECRETS)
    events: list[dict[str, Any]] = []

    def sink(_l: Any, _n: str, event: Any) -> Any:
        events.append(dict(event))
        raise structlog.DropEvent

    structlog.configure(processors=[sink])
    try:
        yield events
    finally:
        structlog.configure(processors=saved["processors"])
        scopus._REDACTED_SECRETS.clear()
        scopus._REDACTED_SECRETS.update(saved_secrets)


def test_credentials_set__prompt__stores_both_and_output_never_contains_them(
    repo: Path, log_events: list[dict[str, Any]], caplog: pytest.LogCaptureFixture
) -> None:
    result = _invoke("set", "scopus", input=f"{SENTINEL}\n{TOKEN}\n")

    assert result.exit_code == 0, result.output
    values = dotenv_values(repo / ".env")
    assert values["SCOPUS_API_KEY"] == SENTINEL and values["SCOPUS_INSTTOKEN"] == TOKEN
    assert not leaks(_all_text(result))
    assert not leaks(repr(log_events)) and not leaks(caplog.text)
    assert "stored SCOPUS_API_KEY, SCOPUS_INSTTOKEN" in result.output


def test_credentials_set__blank_insttoken__leaves_the_existing_one(repo: Path) -> None:
    (repo / ".env").write_text("SCOPUS_INSTTOKEN=keepme\n", encoding="utf-8")

    result = _invoke("set", "scopus", input=f"{SENTINEL}\n\n")

    assert result.exit_code == 0, result.output
    assert dotenv_values(repo / ".env")["SCOPUS_INSTTOKEN"] == "keepme"


def test_credentials_set__stdin__reads_name_value_lines(repo: Path) -> None:
    payload = f"# comment\nSCOPUS_API_KEY={SENTINEL}\nSCOPUS_INSTTOKEN={TOKEN}\n"

    result = _invoke("set", "scopus", "--stdin", input=payload)

    assert result.exit_code == 0, result.output
    assert dotenv_values(repo / ".env")["SCOPUS_API_KEY"] == SENTINEL
    assert not leaks(_all_text(result))


@pytest.mark.parametrize(
    "payload", [f"{SENTINEL}\n", f"OTHER={SENTINEL}\n", "SCOPUS_INSTTOKEN=x\n"]
)
def test_credentials_set__stdin_malformed__refused_without_echoing_the_line(
    repo: Path, payload: str
) -> None:
    result = _invoke("set", "scopus", "--stdin", input=payload)

    assert result.exit_code == 1
    assert not leaks(_all_text(result))
    assert not (repo / ".env").exists()


def test_credentials_set__from_env__copies_the_process_environment(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", SENTINEL)

    result = _invoke("set", "scopus", "--from-env")

    assert result.exit_code == 0, result.output
    assert dotenv_values(repo / ".env") == {"SCOPUS_API_KEY": SENTINEL}
    assert not leaks(_all_text(result))


def test_credentials_set__from_env_without_the_variable__refused(repo: Path) -> None:
    result = _invoke("set", "scopus", "--from-env")

    assert result.exit_code == 1 and "SCOPUS_API_KEY is required" in result.output
    assert not (repo / ".env").exists()


def test_credentials_set__stdin_and_from_env_together__refused(repo: Path) -> None:
    result = _invoke("set", "openalex", "--stdin", "--from-env", input="OPENALEX_API_KEY=x\n")

    assert result.exit_code == 1 and "cannot be combined" in result.output


def test_credentials_set__unsafe_target__refused_and_nothing_printed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unsafe = make_repo(tmp_path / "u", ignore="*.log\n")
    monkeypatch.chdir(unsafe)

    result = _invoke("set", "scopus", input=f"{SENTINEL}\n\n")

    assert result.exit_code == 1 and "Refusing to write" in result.output
    assert not (unsafe / ".env").exists()
    assert not leaks(_all_text(result))


def test_credentials_set__environment_differs__notes_that_it_wins(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENALEX_API_KEY", "from-env")

    result = _invoke("set", "openalex", input="from-prompt\n")

    assert result.exit_code == 0
    assert "OPENALEX_API_KEY is also set in the environment, which wins" in result.stderr
    assert "from-env" not in result.output and "from-prompt" not in result.output


# -- 74: each setter writes only its own keys ---------------------------------------------------------


def test_credentials_set_openalex__writes_only_its_key(repo: Path) -> None:
    (repo / ".env").write_text("SCOPUS_API_KEY=old\nUNPAYWALL_EMAIL=me@example.org\n")

    result = _invoke("set", "openalex", input="oa-key\n")

    assert result.exit_code == 0, result.output
    assert dotenv_values(repo / ".env") == {
        "SCOPUS_API_KEY": "old",
        "UNPAYWALL_EMAIL": "me@example.org",
        "OPENALEX_API_KEY": "oa-key",
    }


def test_credentials_set_unpaywall__writes_only_its_key(repo: Path) -> None:
    (repo / ".env").write_text("SCOPUS_API_KEY=old\nOPENALEX_API_KEY=oa\n")

    result = _invoke("set", "unpaywall", input="me@example.org\n")

    assert result.exit_code == 0, result.output
    assert dotenv_values(repo / ".env") == {
        "SCOPUS_API_KEY": "old",
        "OPENALEX_API_KEY": "oa",
        "UNPAYWALL_EMAIL": "me@example.org",
    }


# -- clear -----------------------------------------------------------------------------------------


def test_credentials_clear_scopus__removes_both_scopus_keys_only(repo: Path) -> None:
    (repo / ".env").write_text(
        f"# c\nSCOPUS_API_KEY={SENTINEL}\nSCOPUS_INSTTOKEN={TOKEN}\nOPENALEX_API_KEY=oa\n"
    )

    result = _invoke("clear", "scopus")

    assert result.exit_code == 0, result.output
    assert (repo / ".env").read_text() == "# c\nOPENALEX_API_KEY=oa\n"
    assert not leaks(_all_text(result))


def test_credentials_clear__key_in_environment__says_it_is_untouched(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".env").write_text("SCOPUS_API_KEY=file\n")
    monkeypatch.setenv("SCOPUS_API_KEY", SENTINEL)

    result = _invoke("clear", "scopus")

    assert "SCOPUS_API_KEY is set in the process environment; unset it there." in result.output
    assert not leaks(_all_text(result))


def test_credentials_clear__nothing_stored__does_not_write(repo: Path) -> None:
    result = _invoke("clear", "scopus")

    assert result.exit_code == 0 and "nothing to clear" in result.output
    assert not (repo / ".env").exists()


def test_credentials_clear__unsafe_file__refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unsafe = make_repo(tmp_path / "u", ignore="*.log\n")
    (unsafe / ".env").write_text("SCOPUS_API_KEY=file\n")
    monkeypatch.chdir(unsafe)

    result = _invoke("clear", "scopus")

    assert result.exit_code == 1 and "Refusing to write" in result.output
    assert (unsafe / ".env").read_text() == "SCOPUS_API_KEY=file\n"


# -- 79: status --------------------------------------------------------------------------------------


def test_credentials_status__reports_source_environment_or_file(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (repo / ".env").write_text(f"SCOPUS_API_KEY=file\nOPENALEX_API_KEY={SENTINEL}\n")
    monkeypatch.setenv("SCOPUS_API_KEY", TOKEN)  # the environment wins over the file
    monkeypatch.setenv("UNPAYWALL_EMAIL", "me@example.org")

    result = _invoke("status")

    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert "SCOPUS_API_KEY: set (environment)" in lines
    assert "OPENALEX_API_KEY: set (.env)" in lines
    assert "UNPAYWALL_EMAIL: set (environment)" in lines
    assert "SCOPUS_INSTTOKEN: not set" in lines
    assert f"file: {(repo / '.env').resolve()} (exists)" in lines
    assert "git-ignored by a tracked rule: yes" in lines
    assert not leaks(_all_text(result)) and "me@example.org" not in result.output


def test_credentials_status__unsafe_directory__says_no_with_the_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(make_repo(tmp_path / "u", ignore="*.log\n"))

    result = _invoke("status")

    assert result.exit_code == 0
    assert "git-ignored by a tracked rule: no (" in result.output
    assert "(missing)" in result.output


def test_credentials_status__openalex_project_with_scopus_key__says_unused(
    repo: Path, tmp_path: Path
) -> None:
    Project.init("demo", title="D", root=tmp_path / "p", source_mode="openalex", actor="t")
    (repo / ".env").write_text("SCOPUS_API_KEY=file\n")

    result = _invoke("status", "--project", "demo", "--root", str(tmp_path / "p"))

    assert "sources mode: openalex" in result.output
    assert "stored but unused in this mode" in result.output


# -- 80: test scopus (one request, never the key) ------------------------------------------------------

_SEARCH = scopus.ScopusClient.SEARCH_ENDPOINT


@pytest.mark.parametrize(
    ("status", "label", "exit_code"),
    [(200, "ok", 0), (401, "401", 1), (403, "403", 1), (500, "http 500", 1)],
)
def test_credentials_test_scopus__one_request_and_no_key_in_output(
    repo: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    label: str,
    exit_code: int,
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", SENTINEL)
    monkeypatch.setenv("SCOPUS_INSTTOKEN", TOKEN)
    body = {"error": f"echoing {SENTINEL} and {TOKEN}"}
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(_SEARCH).mock(return_value=httpx.Response(status, json=body))
        result = _invoke("test", "scopus")

    assert route.call_count == 1
    sent = route.calls[0].request
    assert sent.headers["X-ELS-APIKey"] == SENTINEL  # the key was used, only in the header
    assert SENTINEL not in str(sent.url)
    assert result.exit_code == exit_code, result.output
    assert f"scopus: {label}" in result.output
    assert not leaks(_all_text(result))


def test_credentials_test_scopus__network_error__reports_the_class_only(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", SENTINEL)
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(_SEARCH).mock(side_effect=httpx.ConnectError(f"cannot reach {SENTINEL}"))
        result = _invoke("test", "scopus")

    assert route.call_count == 1
    assert result.exit_code == 1 and "scopus: network error (ConnectError)" in result.output
    assert not leaks(_all_text(result))


def test_credentials_test_scopus__no_key__refused_with_no_request(repo: Path) -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(_SEARCH).mock(return_value=httpx.Response(200))
        result = _invoke("test", "scopus")

    assert route.call_count == 0
    assert result.exit_code == 1 and "SCOPUS_API_KEY is not set" in result.output


def test_credentials_test_scopus__openalex_project__refused_with_no_request(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    Project.init("demo", title="D", root=tmp_path / "p", source_mode="openalex", actor="t")
    monkeypatch.setenv("SCOPUS_API_KEY", SENTINEL)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(_SEARCH).mock(return_value=httpx.Response(200))
        result = _invoke("test", "scopus", "--project", "demo", "--root", str(tmp_path / "p"))

    assert route.call_count == 0
    assert result.exit_code == 1 and "not available in sources mode 'openalex'" in result.output


# -- 81: redaction -------------------------------------------------------------------------------------


def test_redaction__key_handled_by_credentials_set__never_in_structlog_output(
    repo: Path, log_events: list[dict[str, Any]]
) -> None:
    result = _invoke("set", "scopus", input=f"{SENTINEL}\n{TOKEN}\n")
    assert result.exit_code == 0

    structlog.get_logger().info("later", url=f"https://x/?k={SENTINEL}", headers={"h": TOKEN})

    assert log_events[-1]["event"] == "later"
    assert not leaks(repr(log_events))


def test_redaction__registered_scopus_key_never_in_structlog_output(
    log_events: list[dict[str, Any]],
) -> None:
    scopus.ScopusClient(Settings(_env_file=None, scopus_api_key=SENTINEL)).__exit__()  # type: ignore[arg-type]

    structlog.get_logger().info("x", detail=SENTINEL)

    assert not leaks(repr(log_events))


# -- 83: no secret on any CLI stream, for every credential and sources command ----------------------------


def test_no_secret__cli_stdout_and_stderr_clean(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    projects = tmp_path / "p"
    Project.init("demo", title="D", root=projects)
    monkeypatch.setenv("SCOPUS_API_KEY", SENTINEL)
    monkeypatch.setenv("SCOPUS_INSTTOKEN", TOKEN)
    (repo / ".env").write_text(f"SCOPUS_API_KEY={SENTINEL}\nSCOPUS_INSTTOKEN={TOKEN}\n")
    root = str(projects)
    invocations = [
        ["credentials", "status"],
        ["credentials", "status", "--project", "demo", "--root", root],
        ["credentials", "set", "scopus", "--from-env"],
        ["credentials", "clear", "scopus"],
        ["doctor", "demo", "--root", root],
        ["sources", "demo", "--root", root],
        ["sources", "demo", "--set", "scopus", "--actor", "t", "--root", root],
    ]

    for args in invocations:
        result = runner.invoke(app, args)
        assert result.exit_code == 0, (args, result.output)
        assert not leaks(_all_text(result)), args
    for path in projects.rglob("*"):
        if path.is_file():
            assert not leaks(path.read_text(encoding="utf-8", errors="replace")), path


def test_leaks__detects_each_form() -> None:
    """The scanner itself: a printed value, raw or percent-encoded, is caught."""
    assert leaks(f"key={SENTINEL}") and leaks(quote_plus(SENTINEL, safe="")) and leaks(TOKEN)
    assert not leaks("SCOPUS_API_KEY: set (environment)")


def test_doctor__uses_credentials_status_and_points_to_set(repo: Path, tmp_path: Path) -> None:
    Project.init("demo", title="D", root=tmp_path / "p", source_mode="scopus", actor="t")

    result = runner.invoke(app, ["doctor", "demo", "--root", str(tmp_path / "p")])

    assert result.exit_code == 0, result.output
    assert "SCOPUS_API_KEY: not set" in result.output
    assert "prismabib credentials set scopus" in result.output
    assert "prismabib credentials status" in result.output


# -- review round: temp name, non-ASCII paths, input handling, git failures ------------------------


def test_dotenv_writer__bare_env_rule_only__refuses_before_writing_anything(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", ignore=".env\n")  # ignores .env but not tmp-<hex>.env

    with pytest.raises(ConfigError, match=r"temporary file name .* is not ignored") as caught:
        cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})

    assert not leaks(str(caught.value))
    assert sorted(p.name for p in repo.iterdir()) == [".git", ".gitignore"]


def test_dotenv_writer__non_ascii_directory__tracked_gitignore_works(tmp_path: Path) -> None:
    repo = make_repo(tmp_path / "r", ignore="*.log\n")
    sub = repo / "sub é dir"
    sub.mkdir()
    (sub / ".gitignore").write_text(".env\n*.env\n", encoding="utf-8")
    _git(repo, "add", "sub é dir/.gitignore")

    cred.write_env(sub / ".env", {"OPENALEX_API_KEY": "abc"})

    assert (sub / ".env").read_text(encoding="utf-8") == "OPENALEX_API_KEY=abc\n"


def test_credentials_set__prompt__surrounding_whitespace_is_stripped(repo: Path) -> None:
    result = _invoke("set", "scopus", input=f"  {SENTINEL}  \n {TOKEN} \n")

    assert result.exit_code == 0, result.output
    values = dotenv_values(repo / ".env")
    assert values["SCOPUS_API_KEY"] == SENTINEL and values["SCOPUS_INSTTOKEN"] == TOKEN


@pytest.mark.parametrize("quote", ['"', "'"])
def test_credentials_set__stdin_quoted_value__one_matching_pair_is_removed(
    repo: Path, quote: str
) -> None:
    result = _invoke("set", "scopus", "--stdin", input=f"SCOPUS_API_KEY={quote}abc{quote}\n")

    assert result.exit_code == 0, result.output
    assert dotenv_values(repo / ".env")["SCOPUS_API_KEY"] == "abc"


def test_credentials_set__stdin_export_form__refused_without_echo(repo: Path) -> None:
    result = _invoke("set", "scopus", "--stdin", input=f"export SCOPUS_API_KEY={SENTINEL}\n")

    assert result.exit_code == 1 and not leaks(_all_text(result))
    assert not (repo / ".env").exists()


def test_credentials_test_scopus__non_ascii_key__clear_result_and_no_request(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "k\u00e9y-NONASCII-77")
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(_SEARCH).mock(return_value=httpx.Response(200))
        result = _invoke("test", "scopus")

    assert route.call_count == 0
    assert result.exit_code == 1 and "non-ASCII" in result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert "NONASCII-77" not in _all_text(result)


def test_dotenv_writer__git_failure_other_than_no_repository__is_reported_as_such(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(*_a: Any, **_k: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 128, "", "fatal: detected dubious ownership")

    monkeypatch.setattr(cred.subprocess, "run", fake_run)

    with pytest.raises(ConfigError, match="git could not read the repository") as caught:
        cred.write_env(repo / ".env", {"SCOPUS_API_KEY": SENTINEL})

    assert "not inside a git repository" not in str(caught.value)
