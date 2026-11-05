"""ADR 0042 PR 1: the source mode of a project, its event log, the capability table, the
``sources`` and ``doctor`` commands, the ``init`` question, and the store stamp.

Test names keep the numbers of the ADR's Verification section in the docstring or comment of
each group. The CLI tests assert exit codes, our own messages and declared click parameters, never
rendered Rich help text.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
import shlex
import socket
from pathlib import Path
from typing import Any

import pytest
import respx
import typer
from click.testing import Result
from typer.testing import CliRunner

from prismabib import source_mode as sm
from prismabib.capture import cascade_commands
from prismabib.capture.candidates import CandidateEvent, CandidateLog, CandidateRow
from prismabib.capture.openalex import capture_openalex
from prismabib.capture.writer import capture_search
from prismabib.cli import app
from prismabib.errors import ConfigError, LogError, StoreError
from prismabib.project import Project
from prismabib.report.numbers import numbers_map
from prismabib.store import load as store_load
from prismabib.store import stamp as store_stamp
from prismabib.store.db import connect
from prismabib.store.load import build_store
from tests.prisma_helpers import (
    copy_reference_project_with_criteria,
    screen_reference_project,
)

pytestmark = pytest.mark.integration

runner = CliRunner()
_ROOT = Path(__file__).resolve().parents[2]
_SNAPSHOTS = _ROOT / "tests" / "golden" / "report" / "__snapshots__"


def _project(tmp_path: Path, mode: str | None, slug: str = "demo") -> Project:
    if mode is None:
        return Project.init(slug, title="Demo", root=tmp_path)
    return Project.init(slug, title="Demo", root=tmp_path, source_mode=mode, actor="tester")  # type: ignore[arg-type]


def _raw_dir(project: Project, *parts: str) -> Path:
    path = project.raw_dir.joinpath(*parts)
    path.mkdir(parents=True)
    return path


# -- 1, 5: reading the mode ----------------------------------------------------------------------


def test_source_mode__absent_table__is_scopus_plus_openalex(tmp_path: Path) -> None:
    project = _project(tmp_path, None)

    assert project.source_mode == "scopus+openalex"
    assert "[sources]" not in (project.root / "project.toml").read_text(encoding="utf-8")


def test_source_mode__invalid_value__config_error(tmp_path: Path) -> None:
    project = _project(tmp_path, None)
    toml = project.root / "project.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + '\n[sources]\nmode = "both"\n', "utf-8")

    with pytest.raises(ConfigError, match=r"project\.toml.*'both'"):
        _ = project.source_mode


def test_source_mode__hand_edit_without_event__config_error(tmp_path: Path) -> None:
    project = _project(tmp_path, None)
    toml = project.root / "project.toml"
    toml.write_text(toml.read_text(encoding="utf-8") + '\n[sources]\nmode = "openalex"\n', "utf-8")

    with pytest.raises(ConfigError, match=r"openalex.*no event"):
        _ = project.source_mode


def test_source_mode__event_disagrees_with_file__config_error_names_both(tmp_path: Path) -> None:
    project = _project(tmp_path, "scopus")
    toml = project.root / "project.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace('mode = "scopus"', 'mode = "openalex"'), "utf-8"
    )

    with pytest.raises(ConfigError, match=r"says openalex .* says scopus"):
        _ = project.source_mode


def test_source_mode__event_without_table__config_error(tmp_path: Path) -> None:
    project = _project(tmp_path, "scopus")
    toml = project.root / "project.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace('\n[sources]\nmode = "scopus"\n', ""), "utf-8"
    )

    with pytest.raises(ConfigError, match=r"no \[sources\] table"):
        _ = project.source_mode


def test_source_mode__reading_a_legacy_project__creates_no_log_file(tmp_path: Path) -> None:
    project = _project(tmp_path, None)

    _ = project.source_mode

    assert not sm.source_modes_path(project).exists()
    assert not sm.source_modes_path(project).with_name("source_modes.jsonl.sha256").exists()


# -- 18a, 18: Project.init and init ---------------------------------------------------------------


def test_project_init__default_source_mode_none__writes_no_table_and_no_event(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, None)

    text = (project.root / "project.toml").read_text(encoding="utf-8")
    assert "sources" not in text
    assert sorted(p.name for p in (project.root / "decisions").iterdir()) == ["decisions.jsonl"]


def test_project_init__source_mode_without_actor__refuses_and_writes_nothing(
    tmp_path: Path,
) -> None:
    with pytest.raises(ConfigError, match="actor"):
        Project.init("demo", title="Demo", root=tmp_path, source_mode="scopus")

    assert not (tmp_path / "demo").exists()


def test_init__sources_flag__writes_table_and_first_event(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["init", "demo", "--sources", "openalex", "--actor", "Ada", "--root", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    project = Project.open("demo", root=tmp_path)
    assert project.source_mode == "openalex"
    (event,) = sm.load_mode_events(project)
    assert (event.action, event.from_mode, event.to_mode, event.actor, event.project) == (
        "set_mode",
        None,
        "openalex",
        "Ada",
        "demo",
    )
    raw = json.loads(sm.source_modes_path(project).read_text(encoding="utf-8"))
    assert list(raw) == [
        "event_id",
        "schema_version",
        "action",
        "ts",
        "project",
        "actor",
        "from_mode",
        "to_mode",
        "note",
    ]
    assert raw["ts"].endswith("Z")


# -- 17, 17a, 17b: the init rules -----------------------------------------------------------------


def test_init__non_interactive_without_sources_flag__refuses_and_writes_nothing(
    tmp_path: Path,
) -> None:
    result = runner.invoke(app, ["init", "demo", "--actor", "Ada", "--root", str(tmp_path)])

    assert result.exit_code == 1
    assert "--sources" in result.output
    assert not (tmp_path / "demo").exists()


def test_init__without_actor_and_without_git_user_name__refuses_and_writes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("prismabib.cli.git_user_name", lambda: None)

    result = runner.invoke(app, ["init", "demo", "--sources", "scopus", "--root", str(tmp_path)])

    assert result.exit_code == 1
    assert "--actor" in result.output
    assert not (tmp_path / "demo").exists()


def test_init__actor_defaults_to_git_user_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("prismabib.cli.git_user_name", lambda: "Git Person")

    result = runner.invoke(app, ["init", "demo", "--sources", "scopus", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    (event,) = sm.load_mode_events(Project.open("demo", root=tmp_path))
    assert event.actor == "Git Person"


def test_init__existing_project__no_flags__succeeds_without_prompt(tmp_path: Path) -> None:
    runner.invoke(
        app, ["init", "demo", "--sources", "scopus", "--actor", "Ada", "--root", str(tmp_path)]
    )
    before = (tmp_path / "demo" / "project.toml").read_bytes()

    again = runner.invoke(app, ["init", "demo", "--root", str(tmp_path)])
    same = runner.invoke(app, ["init", "demo", "--sources", "scopus", "--root", str(tmp_path)])
    differs = runner.invoke(app, ["init", "demo", "--sources", "openalex", "--root", str(tmp_path)])

    assert again.exit_code == 0, again.output
    assert again.stdout.startswith("Reused project 'demo'")
    assert same.exit_code == 0, same.output
    assert differs.exit_code == 1
    assert "sources" in differs.output and "--set" in differs.output
    assert (tmp_path / "demo" / "project.toml").read_bytes() == before


def test_init__existing_legacy_project__only_the_legacy_default_is_accepted(
    tmp_path: Path,
) -> None:
    _project(tmp_path, None)

    ok = runner.invoke(
        app, ["init", "demo", "--sources", "scopus+openalex", "--root", str(tmp_path)]
    )
    bad = runner.invoke(app, ["init", "demo", "--sources", "scopus", "--root", str(tmp_path)])

    assert ok.exit_code == 0, ok.output
    assert bad.exit_code == 1


@pytest.mark.parametrize(
    ("answers", "expected"),
    [("n\n", "openalex"), ("y\ny\n", "scopus+openalex"), ("y\nn\n", "scopus")],
)
def test_init__interactive__the_question_selects_the_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: str, expected: str
) -> None:
    monkeypatch.setattr("prismabib.cli._stdin_is_tty", lambda: True)

    result = runner.invoke(
        app, ["init", "demo", "--actor", "Ada", "--root", str(tmp_path)], input=answers
    )

    assert result.exit_code == 0, result.output
    assert Project.open("demo", root=tmp_path).source_mode == expected


def test_init__sources_option__declares_the_three_choices() -> None:
    command = typer.main.get_command(app).commands["init"]  # type: ignore[attr-defined]
    option = next(p for p in command.params if "--sources" in p.opts)

    assert sorted(option.type.choices) == ["openalex", "scopus", "scopus+openalex"]
    assert "--actor" in {name for p in command.params for name in p.opts}


# -- 19: every documented init command parses -----------------------------------------------------

_DOC_FILES = [
    "README.md",
    "examples/README.md",
    "docs/getting-started.md",
    "docs/index.md",
    "docs/how-to/run-a-new-review.md",
    "docs/how-to/run-the-demo.md",
    "docs/how-to/build-a-query.md",
]
_INIT_LINE = re.compile(r"prismabib init (?P<args>\w[\w-]*[^`#\n]*)")


def _documented_init_commands() -> list[tuple[str, list[str]]]:
    found = []
    for name in _DOC_FILES:
        for line in (_ROOT / name).read_text(encoding="utf-8").splitlines():
            if match := _INIT_LINE.search(line):
                found.append((name, shlex.split(match["args"].replace('"..."', "x"))))
    return found


def test_init__every_call_site__passes_sources() -> None:
    commands = _documented_init_commands()
    init = typer.main.get_command(app).commands["init"]  # type: ignore[attr-defined]

    assert len(commands) >= 8
    for name, args in commands:
        assert "--sources" in args, f"{name}: prismabib init {args} has no --sources"
        init.make_context("init", list(args), resilient_parsing=False)  # raises on a bad spelling


# -- 20: doctor and sources ------------------------------------------------------------------------


def test_doctor__default__makes_no_request(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _project(tmp_path, "openalex")
    monkeypatch.chdir(tmp_path)  # no real .env
    for name in ("SCOPUS_INSTTOKEN", "OPENALEX_API_KEY", "UNPAYWALL_EMAIL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SCOPUS_API_KEY", "sentinel-not-printed")  # pragma: allowlist secret

    def refuse(*_a: Any, **_k: Any) -> None:
        raise AssertionError("doctor opened a socket")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    with respx.mock(assert_all_called=False):
        result = runner.invoke(app, ["doctor", "demo", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "sources mode: openalex" in result.output
    assert "SCOPUS_API_KEY: set (environment)" in result.output
    assert "OPENALEX_API_KEY: not set" in result.output
    assert "sentinel-not-printed" not in result.output
    assert "legacy default" not in result.output


def test_doctor__legacy_project__says_so_and_offers_to_record(tmp_path: Path) -> None:
    _project(tmp_path, None)

    result = runner.invoke(app, ["doctor", "demo", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "sources mode: scopus+openalex (legacy default" in result.output
    assert "prismabib sources demo --set" in result.output


def test_doctor__has_the_opt_in_network_options() -> None:
    command = typer.main.get_command(app).commands["doctor"]  # type: ignore[attr-defined]

    assert {"--check-scopus", "--online"} <= {name for p in command.params for name in p.opts}


def test_sources__without_set__prints_mode_and_last_event(tmp_path: Path) -> None:
    _project(tmp_path, "scopus")

    result = runner.invoke(app, ["sources", "demo", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "mode: scopus" in result.output
    assert "by tester" in result.output


# -- 6-11: transitions, the event log -------------------------------------------------------------


def _set(project: Project, to: str) -> sm.SourceModeEvent | None:
    return sm.set_mode(project, to, actor="tester")


def test_sources_set__appends_one_event_and_keeps_other_toml_bytes(tmp_path: Path) -> None:
    project = _project(tmp_path, "scopus")
    toml = project.root / "project.toml"
    # A comment, and a table after [sources], must survive byte for byte.
    text = toml.read_text(encoding="utf-8") + "\n# keep me\n[extra]\nk = 1\n"
    toml.write_text(text, encoding="utf-8")
    before = text.replace('[sources]\nmode = "scopus"\n', "@@")

    event = sm.set_mode(project, "scopus+openalex", actor="Ada", note="enrich")

    assert event is not None
    after = toml.read_text(encoding="utf-8")
    assert after.replace('[sources]\nmode = "scopus+openalex"\n', "@@") == before
    assert len(sm.load_mode_events(project)) == 2
    assert (event.from_mode, event.to_mode, event.note) == ("scopus", "scopus+openalex", "enrich")
    assert project.source_mode == "scopus+openalex"


def test_sources_set__same_mode_again__records_nothing(tmp_path: Path) -> None:
    project = _project(tmp_path, "scopus")

    assert _set(project, "scopus") is None
    assert len(sm.load_mode_events(project)) == 1


def test_sources_set__legacy_project__records_the_choice(tmp_path: Path) -> None:
    project = _project(tmp_path, None)

    event = _set(project, "scopus+openalex")

    assert event is not None
    assert (event.from_mode, event.to_mode) == ("scopus+openalex", "scopus+openalex")
    assert project.source_mode == "scopus+openalex"
    assert "[sources]" in (project.root / "project.toml").read_text(encoding="utf-8")


def test_sources_set__invalid_mode__config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not one of"):
        _set(_project(tmp_path, "scopus"), "both")


def test_sources_set__blank_actor__config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="actor"):
        sm.set_mode(_project(tmp_path, "scopus"), "scopus+openalex", actor="  ")


_SCOPUS_RUN = ("20260101T000000Z-aaaaaaaa",)
_OA_ENRICH = ("openalex", "20260101T000000Z-bbbbbbbb")
_OA_SEARCH = ("openalex-search", "20260101T000000Z-cccccccc")


@pytest.mark.parametrize(
    ("start", "target", "blocker", "refused"),
    [
        ("scopus", "scopus+openalex", _SCOPUS_RUN, False),
        ("scopus", "openalex", _SCOPUS_RUN, True),
        ("scopus", "openalex", _OA_ENRICH, False),
        ("scopus+openalex", "openalex", _SCOPUS_RUN, True),
        ("scopus+openalex", "scopus", _OA_ENRICH, True),
        ("scopus+openalex", "scopus", _SCOPUS_RUN, False),
        ("openalex", "scopus", _OA_SEARCH, True),
        ("openalex", "scopus+openalex", _OA_SEARCH, True),
        ("openalex", "scopus", _SCOPUS_RUN, False),
    ],
)
def test_sources_set__transition_table__each_cell(
    tmp_path: Path, start: str, target: str, blocker: tuple[str, ...], refused: bool
) -> None:
    project = _project(tmp_path, start)
    _raw_dir(project, *blocker)

    if refused:
        with pytest.raises(ConfigError, match="create a new project"):
            _set(project, target)
        assert project.source_mode == start
        assert len(sm.load_mode_events(project)) == 1
    else:
        assert _set(project, target) is not None
        assert project.source_mode == target


def test_sources_set__openalex_to_scopus__refused_with_sealed_openalex_run(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, "openalex")
    run = _raw_dir(project, *_OA_SEARCH)
    (run / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigError, match=r"openalex-search"):
        _set(project, "scopus")


def test_sources_set__scopus_plus_openalex_to_scopus__refused_with_active_registration(
    tmp_path: Path,
) -> None:
    from datetime import UTC, datetime

    from prismabib.capture.registrations import (
        PayloadAddress,
        RegisterEvent,
        RegistrationRecord,
        ReverseEvent,
        registration_log,
    )

    project = _project(tmp_path, "scopus+openalex")
    event_id = "01HZ7M3QK2W9T4R8N6Y1C5B0XD"
    register = RegisterEvent(
        event_id=event_id,
        ts=datetime.now(UTC),
        project="demo",
        reviewer="r",
        record_id=f"registered:{event_id}",
        candidate_id="c1",
        match_rule="doi",
        source_level=4,
        source_payload_ref=PayloadAddress(file="openalex/x/response-0000.jsonl", openalex_id="W1"),
        source_payload_sha256="0" * 64,
        doi="10.1/a",
        subject_map_version="1",
        subject_map_sha256="0" * 64,
    )
    log = registration_log(project.registrations_path)
    log.write_event(RegistrationRecord(register))

    with pytest.raises(ConfigError, match=r"active registration in .*registrations\.jsonl"):
        _set(project, "scopus")

    log.write_event(
        RegistrationRecord(
            ReverseEvent(
                event_id="01HZ7M3QK2W9T4R8N6Y1C5B0XE",
                ts=datetime.now(UTC),
                project="demo",
                reviewer="r",
                reverses=event_id,
                reason="not_eligible",
            )
        )
    )
    assert _set(project, "scopus") is not None


def test_sources_set__commented_header__is_edited_in_place(tmp_path: Path) -> None:
    project = _project(tmp_path, "scopus")
    toml = project.root / "project.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace("[sources]", "[sources]  # my choice"), "utf-8"
    )

    assert _set(project, "scopus+openalex") is not None

    text = toml.read_text(encoding="utf-8")
    assert text.count("[sources]") == 1
    assert '[sources]  # my choice\nmode = "scopus+openalex"\n' in text
    assert project.source_mode == "scopus+openalex"


def test_sources_set__toml_edit_that_cannot_be_validated__leaves_log_and_file_untouched(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, "scopus")
    toml = project.root / "project.toml"
    # An inline table has no header to edit; appending a second [sources] is invalid TOML.
    toml.write_text(
        'sources = { mode = "scopus" }\n'
        + toml.read_text(encoding="utf-8").replace('[sources]\nmode = "scopus"\n', ""),
        encoding="utf-8",
    )
    log_path = sm.source_modes_path(project)
    log_before = (log_path.read_bytes(), log_path.with_name(log_path.name + ".sha256").read_bytes())
    toml_before = toml.read_bytes()

    with pytest.raises(ConfigError, match=r"project\.toml"):
        _set(project, "scopus+openalex")

    after = (log_path.read_bytes(), log_path.with_name(log_path.name + ".sha256").read_bytes())
    assert after == log_before
    assert toml.read_bytes() == toml_before


def test_tables__command_rows_agree_with_capability_rows() -> None:
    assert sm.COMMAND_MODES["search"] == sm.CAPABILITY_MODES["scopus_search"]
    assert sm.COMMAND_MODES["enrich"] == sm.CAPABILITY_MODES["scopus_abstract"]
    assert sm.COMMAND_MODES["openalex"] == sm.CAPABILITY_MODES["openalex_command"]
    assert sm.COMMAND_MODES["cascade"] == (
        sm.CAPABILITY_MODES["scopus_cascade"] | sm.CAPABILITY_MODES["openalex_lookup"]
    )


def test_search__refused_in_openalex_mode__prints_no_capturing_banner(
    tmp_path: Path, no_scopus_key: None
) -> None:
    _project(tmp_path, "openalex")

    result = runner.invoke(app, ["search", "demo", "--root", str(tmp_path)])

    assert result.exit_code == 1
    assert "Capturing" not in result.output and "quota" not in result.output


def test_git_user_name__decodes_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    import subprocess

    seen: dict[str, Any] = {}

    def fake_run(*_a: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        seen.update(kwargs)
        return subprocess.CompletedProcess([], 0, stdout="Zoë\n", stderr="")

    monkeypatch.setattr(sm.subprocess, "run", fake_run)

    assert sm.git_user_name() == "Zoë"
    assert seen["encoding"] == "utf-8" and seen["errors"] == "replace"


def test_store_stamp__old_stamp_cannot_be_removed__store_error_not_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _openalex_mode_project_with_store(tmp_path)
    build_store(project, rebuild=True)
    store_before = project.db_path.read_bytes()
    real_unlink = Path.unlink

    def locked(self: Path, *args: Any, **kwargs: Any) -> None:
        if self.name == store_stamp.STAMP_FILENAME:
            raise PermissionError("held open")
        real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    with pytest.raises(StoreError, match=r"stamp .* could not be removed"):
        build_store(project, rebuild=True)
    monkeypatch.undo()

    assert project.db_path.read_bytes() == store_before
    connect(project).close()


def test_sources_set__scopus_plus_openalex_to_scopus__refused_with_openalex_enrichment(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path, "scopus+openalex")
    _raw_dir(project, *_OA_ENRICH)

    with pytest.raises(ConfigError, match=r"raw.openalex"):
        _set(project, "scopus")


def test_sources_set__decisions_present__refused(tmp_path: Path) -> None:
    project = _project(tmp_path, "scopus")
    project.decisions_path.write_bytes(b'{"event": "x"}\n')

    with pytest.raises(ConfigError, match="screening decisions exist"):
        _set(project, "scopus+openalex")
    assert project.source_mode == "scopus"


@pytest.mark.parametrize("state", ["unsealed", "discarded", "sealed"])
def test_sources_set__run_of_any_state_blocks_transition(tmp_path: Path, state: str) -> None:
    """Verification 10 (unsealed), 10a (discarded): a run directory blocks whatever it holds."""
    project = _project(tmp_path, "scopus")
    run = _raw_dir(project, *_SCOPUS_RUN)
    if state == "unsealed":
        (run / "progress.json").write_text("{}", encoding="utf-8")
    elif state == "discarded":
        (run / "progress.json").write_text('{"discarded": true}', encoding="utf-8")
    else:
        (run / "manifest.json").write_text("{}", encoding="utf-8")

    with pytest.raises(ConfigError, match="any state"):
        _set(project, "openalex")


def test_sources_set__repairs_a_hand_edited_file(tmp_path: Path) -> None:
    project = _project(tmp_path, "scopus")
    toml = project.root / "project.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace('mode = "scopus"', 'mode = "openalex"'), "utf-8"
    )

    event = _set(project, "scopus")  # the log says scopus: only the file is repaired

    assert event is None or event.to_mode == "scopus"
    assert project.source_mode == "scopus"


def test_source_modes_log__checksum_tamper__log_error(tmp_path: Path) -> None:
    project = _project(tmp_path, "scopus")
    path = sm.source_modes_path(project)
    path.write_bytes(path.read_bytes().replace(b'"scopus"', b'"openalex"'))

    with pytest.raises(LogError):
        sm.load_mode_events(project)
    with pytest.raises(LogError):
        _ = project.source_mode


def test_sources_command__set_and_refusal_exit_codes(tmp_path: Path) -> None:
    project = _project(tmp_path, "scopus")

    ok = runner.invoke(
        app,
        ["sources", "demo", "--set", "scopus+openalex", "--actor", "Ada", "--root", str(tmp_path)],
    )
    _raw_dir(project, *_SCOPUS_RUN)
    refused = runner.invoke(
        app, ["sources", "demo", "--set", "openalex", "--actor", "Ada", "--root", str(tmp_path)]
    )
    again = runner.invoke(
        app,
        ["sources", "demo", "--set", "scopus+openalex", "--actor", "Ada", "--root", str(tmp_path)],
    )

    assert ok.exit_code == 0, ok.output
    assert refused.exit_code == 1
    assert "create a new project" in refused.output
    assert again.exit_code == 0 and "already" in again.output


# -- 4: the capability table -----------------------------------------------------------------------


def test_capability_table__every_cli_command__has_a_declared_capability() -> None:
    commands = set(typer.main.get_command(app).commands)  # type: ignore[attr-defined]

    assert commands == set(sm.COMMAND_MODES)


def test_capability_table__every_capture_function__declares_a_capability() -> None:
    import pkgutil

    import prismabib.capture as capture_pkg

    found = []
    for info in pkgutil.iter_modules(capture_pkg.__path__):
        module = __import__(f"prismabib.capture.{info.name}", fromlist=["x"])
        for name, fn in inspect.getmembers(module, inspect.isfunction):
            if name.startswith("capture_") and fn.__module__ == module.__name__:
                found.append(name)
                source = inspect.getsource(fn)
                assert "require_capability(" in source or "require_caller(" in source, name

    assert sorted(found) == [
        "capture_abstracts",
        "capture_openalex",
        "capture_openalex_search",
        "capture_scopus_cascade",
        "capture_search",
    ]
    assert all(m in sm.SOURCE_MODES for modes in sm.CAPABILITY_MODES.values() for m in modes)


@pytest.mark.parametrize(
    ("capability", "mode", "allowed"),
    [
        ("scopus_search", "scopus", True),
        ("scopus_search", "scopus+openalex", True),
        ("scopus_search", "openalex", False),
        ("scopus_abstract", "openalex", False),
        ("scopus_cascade", "scopus", False),
        ("scopus_cascade", "scopus+openalex", True),
        ("openalex_command", "scopus", False),
        ("openalex_command", "openalex", False),
        ("openalex_lookup", "scopus", False),
        ("openalex_lookup", "openalex", True),
        ("openalex_search", "scopus+openalex", False),
        ("openalex_search", "openalex", True),
    ],
)
def test_capability_table__each_cell(
    tmp_path: Path, capability: str, mode: str, allowed: bool
) -> None:
    project = _project(tmp_path, mode)

    if allowed:
        assert sm.require_capability(project, capability) == mode
    else:
        with pytest.raises(ConfigError, match=re.escape(f"not available in sources mode '{mode}'")):
            sm.require_capability(project, capability)


def test_capability_table__unknown_capability__refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unknown"):
        sm.require_capability(_project(tmp_path, None), "scopus_everything")


def test_refusal_message__names_command_mode_file_and_fix(tmp_path: Path) -> None:
    project = _project(tmp_path, "scopus")

    with pytest.raises(ConfigError) as caught:
        sm.require_capability(project, "scopus_cascade")

    message = str(caught.value)
    assert "scopus_cascade" in message and "'scopus'" in message
    assert str(project.root / "project.toml") in message
    assert "prismabib sources demo --set scopus+openalex" in message


# -- 12-15a: enforcement, with no request before a refusal -----------------------------------------


@pytest.fixture
def no_scopus_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No key and no `.env`: a refusal must come from the mode, before `Settings()`."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("SCOPUS_API_KEY", raising=False)


def test_source_mode__openalex__search_refused_before_any_http(
    tmp_path: Path, no_scopus_key: None
) -> None:
    _project(tmp_path, "openalex")

    with respx.mock(assert_all_called=False):  # an unmocked request raises
        result = runner.invoke(app, ["search", "demo", "--root", str(tmp_path)])

    assert result.exit_code == 1
    assert "not available in sources mode 'openalex'" in result.output
    assert "SCOPUS_API_KEY" not in result.output


def test_source_mode__openalex__enrich_and_cascade_scopus_levels_refused(
    tmp_path: Path, no_scopus_key: None
) -> None:
    _project(tmp_path, "openalex")
    root = str(tmp_path)

    enrich = runner.invoke(app, ["enrich", "demo", "--budget", "100", "--root", root])
    opt_in = runner.invoke(
        app, ["cascade", "demo", "--opt-in", "--scopus-budget", "5", "--root", root]
    )

    assert enrich.exit_code == 1
    assert "not available in sources mode 'openalex'" in enrich.output
    assert opt_in.exit_code == 1
    assert "no Scopus records" in opt_in.output


def test_source_mode__scopus_mode__cascade_and_openalex_commands_refused(
    tmp_path: Path, no_scopus_key: None
) -> None:
    _project(tmp_path, "scopus")
    root = str(tmp_path)

    cascade = runner.invoke(app, ["cascade", "demo", "--root", root])
    openalex = runner.invoke(app, ["openalex", "demo", "--root", root])

    assert cascade.exit_code == 1 and "'scopus'" in cascade.output
    assert openalex.exit_code == 1 and "'scopus'" in openalex.output


def test_source_mode__openalex_mode__openalex_command_and_conflict_resolve_refused(
    tmp_path: Path, no_scopus_key: None
) -> None:
    _project(tmp_path, "openalex")
    root = str(tmp_path)

    openalex = runner.invoke(app, ["openalex", "demo", "--root", root])
    resolve = runner.invoke(
        app,
        [
            *["conflict-resolve", "demo", "scopus:x", "abstract", "scopus", "--reviewer", "r"],
            *["--root", root],
        ],
    )

    assert openalex.exit_code == 1 and "not available" in openalex.output
    assert resolve.exit_code == 1 and "not available" in resolve.output


def test_source_mode__scopus_mode__register_refused(tmp_path: Path, no_scopus_key: None) -> None:
    _project(tmp_path, "scopus")

    result = runner.invoke(
        app,
        [
            *["register", "demo", "c1", "--reviewer", "r"],
            *["--payload", "openalex/x/response-0000.jsonl", "--root", str(tmp_path)],
        ],
    )

    assert result.exit_code == 1
    assert "not available in sources mode 'scopus'" in result.output


def test_library__capture_search__refused_in_openalex_mode(
    tmp_path: Path, no_scopus_key: None
) -> None:
    project = _project(tmp_path, "openalex")

    with pytest.raises(ConfigError, match="scopus_search"):
        capture_search(project, query="x")


def test_capture_openalex__caller_none__refused(tmp_path: Path) -> None:
    project = _project(tmp_path, None)

    for bad in (None, "", "nobody"):
        with pytest.raises(ConfigError, match="caller"):
            capture_openalex(project, caller=bad, dois=["10.1/a"])  # type: ignore[arg-type]
    assert not (project.raw_dir / "openalex").exists()


def test_capture_openalex__caller_missing__is_a_type_error(tmp_path: Path) -> None:
    with pytest.raises(TypeError, match="caller"):
        capture_openalex(_project(tmp_path, None), dois=["10.1/a"])  # type: ignore[call-arg]


def test_capture_openalex__caller_capability_follows_the_mode(tmp_path: Path) -> None:
    scopus_only = _project(tmp_path, "scopus", slug="a")
    openalex_only = _project(tmp_path, "openalex", slug="b")

    with pytest.raises(ConfigError, match="openalex_lookup"):
        capture_openalex(scopus_only, caller="cascade", dois=["10.1/a"])
    with pytest.raises(ConfigError, match="openalex_command"):
        capture_openalex(openalex_only, caller="openalex_command", dois=["10.1/a"])


def _capture_openalex_calls(path: Path) -> list[ast.Call]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name | ast.Attribute)
        and getattr(node.func, "id", getattr(node.func, "attr", "")) == "capture_openalex"
    ]


def test_capture_openalex__every_existing_call_site__passes_caller() -> None:
    sites = [
        "src/prismabib/capture/cascade_commands.py",
        "tests/contract/test_openalex_run_layout.py",
        "tests/integration/capture/test_openalex.py",
        "tests/integration/capture/test_cascade_commands.py",
    ]
    expected = {"cascade_commands.py": {"cascade", "openalex_command"}}
    seen: dict[str, list[str]] = {}
    for site in sites:
        calls = _capture_openalex_calls(_ROOT / site)
        assert calls, site
        for call in calls:
            keywords = {k.arg: k.value for k in call.keywords}
            assert "caller" in keywords, f"{site}:{call.lineno} has no caller="
            value = keywords["caller"]
            assert isinstance(value, ast.Constant) and value.value in sm.CALLER_CAPABILITY
            seen.setdefault(site, []).append(value.value)
    assert set(seen["src/prismabib/capture/cascade_commands.py"]) == expected["cascade_commands.py"]
    assert seen["src/prismabib/capture/cascade_commands.py"].count("cascade") == 2


# -- 16: the cascade in mode openalex -------------------------------------------------------------


def test_cascade__openalex_mode__candidates_only_and_no_record_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SCOPUS_API_KEY", "x")  # pragma: allowlist secret  (Settings, until PR 2)
    project = _project(tmp_path, "openalex")
    build_store(project, rebuild=True)  # a real store: the cascade readers go through `connect`
    from datetime import UTC, datetime

    CandidateLog(project.root / "decisions" / "candidates.jsonl").add(
        CandidateEvent.from_row(
            CandidateRow("Cand", 2018, "10.1/c"), reviewer="sh", pasted_at=datetime.now(UTC)
        )
    )
    asked: list[dict[str, Any]] = []
    scopus: list[object] = []

    def fake_openalex(_project: Project, **kwargs: Any) -> None:
        asked.append(kwargs)

    def fake_scopus(*_a: Any, **_k: Any) -> None:
        scopus.append(1)

    monkeypatch.setattr(cascade_commands, "capture_openalex", fake_openalex)
    monkeypatch.setattr(cascade_commands, "capture_scopus_cascade", fake_scopus)
    monkeypatch.setattr(cascade_commands, "build_store", lambda *_a, **_k: None)
    monkeypatch.setattr(cascade_commands, "_field_report", lambda _p: [])

    result = runner.invoke(app, ["cascade", "demo", "--root", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert scopus == []
    assert {k["caller"] for k in asked} == {"cascade"}
    assert any(k.get("dois") == ["10.1/c"] for k in asked)


# -- 2, 3, 49: legacy byte identity ----------------------------------------------------------------


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False)


@pytest.fixture
def legacy_reference(tmp_path: Path) -> Project:
    project = copy_reference_project_with_criteria(tmp_path)
    build_store(project, rebuild=True)
    screen_reference_project(project)
    return project


def test_numbers_json__legacy_project__bytes_unchanged(legacy_reference: Project) -> None:
    """The goldens are the 'before' anchors: a legacy store rebuilt by this code matches them."""
    numbers = numbers_map(legacy_reference)

    golden_values = json.loads((_SNAPSHOTS / "reference_numbers.json").read_text(encoding="utf-8"))
    golden_keys = json.loads((_SNAPSHOTS / "numbers_keys.json").read_text(encoding="utf-8"))
    assert _canonical(numbers) == _canonical(golden_values)
    assert sorted(numbers) == golden_keys
    toml = (legacy_reference.root / "project.toml").read_text(encoding="utf-8")
    assert "[sources]" not in toml
    assert not sm.source_modes_path(legacy_reference).exists()


def test_methods__legacy_project__no_source_sentence(legacy_reference: Project) -> None:
    """Pins behaviour only: PR1 adds no sentence, so this holds until PR 6a (the methods text).

    It is not fail-first and compares no pre-change bytes; the goldens above anchor the numbers.
    """
    from prismabib.report.export import export_project

    result = export_project(legacy_reference)

    texts = [
        p.read_text(encoding="utf-8")
        for p in result.root.rglob("*")
        if p.suffix in {".md", ".txt", ".json"}
    ]
    assert texts
    for text in texts:
        assert "sources mode" not in text.lower()
        assert "no Scopus search was run" not in text
        assert "Records were identified by searching OpenAlex" not in text


def test_legacy_store__is_stamped_with_the_legacy_mode_and_stays_readable(
    legacy_reference: Project,
) -> None:
    stamp = json.loads(store_stamp.stamp_path(legacy_reference.db_path).read_text("utf-8"))

    assert stamp == {"schema": store_stamp.STORE_SCHEMA, "source_mode": "scopus+openalex"}
    connect(legacy_reference).close()
    build_store(legacy_reference)  # the reuse path


def test_legacy_store__without_a_stamp__is_refused_from_pr4a_on(legacy_reference: Project) -> None:
    """From PR 4a a store with no stamp is "needs rebuild" in every mode, the legacy one included.

    PR 1 read it as the legacy mode; that ended when the OpenAlex tables joined ``TABLE_NAMES``
    (ADR 0042 Decision 1), because such a store lacks them. The rebuild is what the message asks for.
    """
    store_stamp.stamp_path(legacy_reference.db_path).unlink()

    with pytest.raises(StoreError, match=r"no build stamp.*--rebuild"):
        connect(legacy_reference)
    with pytest.raises(StoreError, match=r"no build stamp.*--rebuild"):
        build_store(legacy_reference)
    assert build_store(legacy_reference, rebuild=True).records_loaded > 0
    connect(legacy_reference).close()


# -- 27, 29, 29a: the store stamp ------------------------------------------------------------------


def test_store__built_under_scopus__flow_refuses_after_sources_set_openalex(
    tmp_path: Path,
) -> None:
    project = copy_reference_project_with_criteria(tmp_path)
    sm.set_mode(project, "scopus", actor="t")
    build_store(project, rebuild=True)
    # `scopus` -> `openalex` is refused while a Scopus run exists, so move the file and the log
    # by hand to the state a later release could reach, and check that the reader refuses.
    sm.write_mode_event(project, actor="t", from_mode="scopus", to_mode="openalex")
    toml = project.root / "project.toml"
    toml.write_text(
        toml.read_text(encoding="utf-8").replace('mode = "scopus"', 'mode = "openalex"'), "utf-8"
    )

    with pytest.raises(StoreError, match=r"--rebuild"):
        connect(project)
    result = runner.invoke(app, ["flow", project.slug, "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "--rebuild" in result.output


def test_build__without_rebuild_refuses_stamp_mismatch(tmp_path: Path) -> None:
    project = copy_reference_project_with_criteria(tmp_path)
    build_store(project, rebuild=True)  # legacy mode, stamped scopus+openalex
    sm.set_mode(project, "scopus", actor="t")  # allowed: no OpenAlex run, no registration
    before = project.db_path.read_bytes()

    with pytest.raises(StoreError, match=r"scopus\+openalex.*scopus.*--rebuild"):
        build_store(project)
    assert project.db_path.read_bytes() == before

    build_store(project, rebuild=True)
    assert (
        json.loads(store_stamp.stamp_path(project.db_path).read_text("utf-8"))["source_mode"]
        == "scopus"
    )
    build_store(project)  # now matches


def test_store__unstamped_legacy_store__refused_by_a_project_in_another_mode(
    tmp_path: Path,
) -> None:
    project = copy_reference_project_with_criteria(tmp_path)
    build_store(project, rebuild=True)
    store_stamp.stamp_path(project.db_path).unlink()
    sm.set_mode(project, "scopus", actor="t")

    with pytest.raises(StoreError, match=r"no build stamp.*--rebuild"):
        connect(project)


def test_store__stamp_with_another_schema__refused(tmp_path: Path) -> None:
    project = copy_reference_project_with_criteria(tmp_path)
    build_store(project, rebuild=True)
    store_stamp.stamp_path(project.db_path).write_text(
        '{"schema": 0, "source_mode": "scopus+openalex"}', encoding="utf-8"
    )

    with pytest.raises(StoreError, match="schema 0"):
        connect(project)


def test_store__unreadable_stamp__refused(tmp_path: Path) -> None:
    project = copy_reference_project_with_criteria(tmp_path)
    build_store(project, rebuild=True)
    store_stamp.stamp_path(project.db_path).write_text("not json", encoding="utf-8")

    with pytest.raises(StoreError, match="not a readable store stamp"):
        connect(project)


def _openalex_mode_project_with_store(tmp_path: Path) -> Project:
    project = copy_reference_project_with_criteria(tmp_path)
    # The reference fixture holds a Scopus run, which blocks `openalex`; build a stamped
    # openalex-mode store from an empty raw/ instead.
    for child in project.raw_dir.iterdir():
        for sub in sorted(child.rglob("*"), reverse=True):
            sub.unlink() if sub.is_file() else sub.rmdir()
        child.rmdir()
    sm.set_mode(project, "openalex", actor="t")
    return project


def test_store_stamp__failure_between_replace_and_stamp__leaves_refused_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _openalex_mode_project_with_store(tmp_path)

    def boom(_db_path: Path, _mode: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(store_load, "write_stamp", boom)
    with pytest.raises(OSError, match="disk full"):
        build_store(project, rebuild=True)

    assert project.db_path.is_file()
    assert not store_stamp.stamp_path(project.db_path).exists()
    with pytest.raises(StoreError, match=r"no build stamp.*--rebuild"):
        connect(project)
    monkeypatch.undo()
    build_store(project, rebuild=True)  # the fix named in the message works
    connect(project).close()


def test_store_stamp__failed_replace__keeps_old_store_and_old_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _openalex_mode_project_with_store(tmp_path)
    build_store(project, rebuild=True)
    old_store = project.db_path.read_bytes()
    old_stamp = store_stamp.stamp_path(project.db_path).read_bytes()

    def failing_replace(_src: object, _dst: object) -> None:
        raise PermissionError("file in use")

    monkeypatch.setattr(store_load.os, "replace", failing_replace)
    with pytest.raises(StoreError, match="has not been changed"):
        build_store(project, rebuild=True)
    monkeypatch.undo()

    assert project.db_path.read_bytes() == old_store
    assert store_stamp.stamp_path(project.db_path).read_bytes() == old_stamp
    connect(project).close()


def test_store_stamp__first_build_failing_replace__leaves_no_stamp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _openalex_mode_project_with_store(tmp_path)

    def failing_replace(_src: object, _dst: object) -> None:
        raise PermissionError("file in use")

    monkeypatch.setattr(store_load.os, "replace", failing_replace)
    with pytest.raises(StoreError):
        build_store(project, rebuild=True)

    assert not store_stamp.stamp_path(project.db_path).exists()


def test_store_stamp__stamp_bytes_are_stable() -> None:
    assert store_stamp.stamp_bytes("scopus") == store_stamp.stamp_bytes("scopus")
    assert store_stamp.stamp_bytes("scopus").endswith(b"}\n")
    assert b"\r" not in store_stamp.stamp_bytes("scopus")


# -- the third answer to the access question -------------------------------------------------------


def _ask(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: str) -> Result:
    monkeypatch.setattr("prismabib.cli._stdin_is_tty", lambda: True)
    return runner.invoke(
        app, ["init", "demo", "--actor", "Ada", "--root", str(tmp_path)], input=answers
    )


@pytest.mark.parametrize(
    ("answers", "expected"),
    [
        ("s\n", "openalex"),
        ("not sure\n", "openalex"),
        ("Not  Sure\n", "openalex"),
        ("no\n", "openalex"),
        ("yes\nn\n", "scopus"),
        ("maybe\n\nx\ns\n", "openalex"),
    ],
)
def test_init__interactive__third_answer_and_re_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: str, expected: str
) -> None:
    result = _ask(tmp_path, monkeypatch, answers)

    assert result.exit_code == 0, result.output
    assert Project.open("demo", root=tmp_path).source_mode == expected
    assert "Do you have access to Scopus? [y/n/not sure]" in result.output


def test_init__not_sure__prints_how_to_check_and_how_to_switch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _ask(tmp_path, monkeypatch, "s\n")

    assert "Mode openalex was recorded" in result.output
    assert "prismabib credentials set scopus" in result.output
    assert "prismabib credentials test scopus" in result.output
    assert "prismabib sources demo --set scopus" in result.output
    assert "raw/openalex-search/" in result.output


@pytest.mark.parametrize("answers", ["n\n", "y\ny\n"])
def test_init__no_and_yes__do_not_print_the_not_sure_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answers: str
) -> None:
    result = _ask(tmp_path, monkeypatch, answers)

    assert "was recorded" not in result.output
    assert "credentials test" not in result.output
