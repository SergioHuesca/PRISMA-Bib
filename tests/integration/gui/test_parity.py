"""Parity: the layer every screen calls gives the same result as the CLI (GUI spec #312, #320).

Each case runs one set of inputs through the layer and through the CLI, with the same fixed
clock and the same id factory injected into both paths (``_inject``), then compares what each
left on disk. Later tickets add their case to this module.

The normaliser
--------------
:func:`normalise` is the one function every case compares through. It turns a folder into a
mapping of relative path to normalised text and replaces three things, nothing else:

* run ids and event ids: any ``"event_id"`` / ``"run_id"`` value, and any 26-character ULID;
* timestamps: any ISO-8601 date-time (so a case also holds when a clock is not injected);
* the folder's own absolute path, which differs between the two paths by construction.

Exact equality, with no normalising at all, is required for the numbers export and the sealed
payload hash; git fields in the export manifest are excluded. Those cases add their own
comparison next to the one here.

Liveness: :func:`test_parity__one_altered_byte__fails` alters one byte of the layer's output and
asserts that :func:`assert_parity` then fails, so the check cannot pass by comparing nothing.

When ``PRISMABIB_PARITY_DIGEST`` names a file, the digest of the normalised trees is written to
it; CI runs this module under two time zones and locales and compares the two digests.
"""

from __future__ import annotations

import hashlib
import os
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prismabib import project as project_module
from prismabib import source_mode
from prismabib.cli import app
from prismabib.gui import layer
from prismabib.gui import screening_layer as sl
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR
from tests.conftest import SeededIdFactory

pytestmark = pytest.mark.integration

FIXED_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

_ULID = re.compile(r"\b[0-9A-HJKMNP-TV-Z]{26}\b")
_ID_FIELD = re.compile(r'("(?:event_id|run_id)"\s*:\s*)"[^"]*"')
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")


def normalise(root: Path) -> dict[str, str]:
    """Every file under ``root`` by relative path, with ids, timestamps and ``root`` replaced."""
    out: dict[str, str] = {}
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        text = path.read_bytes().decode("utf-8")
        text = text.replace(str(root), "<root>").replace(root.as_posix(), "<root>")
        text = _ID_FIELD.sub(r'\1"<id>"', text)
        text = _ULID.sub("<id>", text)
        out[path.relative_to(root).as_posix()] = _TIMESTAMP.sub("<ts>", text)
    return out


def assert_parity(layer_out: dict[str, str], cli_out: dict[str, str]) -> None:
    """Fail unless both normalised outputs are identical, file for file."""
    assert layer_out.keys() == cli_out.keys()
    for name, text in layer_out.items():
        assert text == cli_out[name], name


def digest(*outputs: dict[str, str]) -> str:
    h = hashlib.sha256()
    for out in outputs:
        for name, text in sorted(out.items()):
            h.update(name.encode() + b"\0" + text.encode() + b"\0")
    return h.hexdigest()


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz: object = None) -> datetime:  # type: ignore[override]
        return FIXED_NOW


def _inject(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same fixed clock and a fresh, identically seeded id factory for each path."""
    for module in (source_mode, project_module):
        monkeypatch.setattr(module, "datetime", _FixedDatetime)
    monkeypatch.setattr(source_mode, "_IDS", SeededIdFactory(seed=0, prefix="id"))


@pytest.fixture(autouse=True)
def _user_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(USER_FILE_OVERRIDE_VAR, str(tmp_path / "settings.env"))


def _create_via_layer(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    _inject(monkeypatch)
    layer.create_project(
        "demo", title="Demo review", root=root, source_mode="openalex", reviewer="Ada"
    )
    return normalise(root / "demo")


def _create_via_cli(root: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    _inject(monkeypatch)
    result = CliRunner().invoke(
        app,
        [
            "init",
            "demo",
            "--title",
            "Demo review",
            "--sources",
            "openalex",
            "--actor",
            "Ada",
            "--root",
            str(root),
        ],
    )
    assert result.exit_code == 0, result.output
    return normalise(root / "demo")


def test_parity__project_creation__layer_and_cli_leave_identical_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer_out = _create_via_layer(tmp_path / "layer", monkeypatch)
    cli_out = _create_via_cli(tmp_path / "cli", monkeypatch)
    assert any("event_id" in text or "<id>" in text for text in layer_out.values())
    assert_parity(layer_out, cli_out)
    target = os.environ.get("PRISMABIB_PARITY_DIGEST")
    if target:
        Path(target).write_text(digest(layer_out, cli_out) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("answer", "fill", "cli_args"),
    [
        ("Yes", True, ["--sources", "scopus+openalex"]),
        ("Yes", False, ["--sources", "scopus"]),
        ("No", True, ["--sources", "openalex"]),
    ],
)
def test_parity__scopus_access_answer__gui_and_cli_leave_identical_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, fill: bool, cli_args: list[str]
) -> None:
    """Ticket #324: the New Project answer, through its mapping, equals the CLI's mode flag."""
    from prismabib.gui.new_project_model import mode_for_choice

    _inject(monkeypatch)
    layer.create_project(
        "demo",
        title="Demo review",
        root=tmp_path / "layer",
        source_mode=mode_for_choice(answer, fill_gaps=fill),
        reviewer="Ada",
    )
    layer_out = normalise(tmp_path / "layer" / "demo")
    _inject(monkeypatch)
    result = CliRunner().invoke(
        app,
        [
            "init",
            "demo",
            "--title",
            "Demo review",
            *cli_args,
            "--actor",
            "Ada",
            "--root",
            str(tmp_path / "cli"),
        ],
    )
    assert result.exit_code == 0, result.output
    assert_parity(layer_out, normalise(tmp_path / "cli" / "demo"))


def _screening_log(
    root: Path, monkeypatch: pytest.MonkeyPatch, *, via_layer: bool
) -> dict[str, str]:
    """Ticket #332: screening has no CLI, so the other path is the Screener's decide interface."""
    from prismabib.prisma import log as log_module
    from prismabib.prisma.log import DecisionLog
    from prismabib.screening.queue import ScreeningQueue
    from prismabib.screening.ui import Screener
    from prismabib.stage import PrismaStage
    from tests.integration.gui.test_screening import make_project

    monkeypatch.setattr(log_module, "datetime", _FixedDatetime)
    project = make_project(root)
    stage = PrismaStage.TITLE_ABSTRACT
    queue = ScreeningQueue(
        project, stage, "Ada", log=DecisionLog(project, id_factory=SeededIdFactory(0, "id"))
    )
    if via_layer:
        with layer.Session(project) as session:
            sl.decide(session, queue, "include")
            sl.decide(session, queue, "exclude", reason_code="OFF_TOPIC")
            sl.decide(session, queue, "unsure")
    else:
        screener = Screener(queue, {})
        screener.decide("include")
        screener.decide("exclude", reason_code="OFF_TOPIC")
        screener.decide("unsure")
    only = root / "log-only"
    only.mkdir()
    (only / "decisions.jsonl").write_bytes(project.decisions_path.read_bytes())
    return normalise(only)


def test_parity__screening_decisions__layer_and_screener_leave_identical_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layer_out = _screening_log(tmp_path / "layer", monkeypatch, via_layer=True)
    other_out = _screening_log(tmp_path / "screener", monkeypatch, via_layer=False)
    assert layer_out["decisions.jsonl"].count("\n") == 3
    assert_parity(layer_out, other_out)
    altered = {name: text.replace("include", "includf", 1) for name, text in layer_out.items()}
    with pytest.raises(AssertionError):
        assert_parity(altered, other_out)


def _init_cli(root: Path, mode: str) -> Path:
    result = CliRunner().invoke(
        app,
        [
            "init",
            "demo",
            "--title",
            "Demo review",
            "--sources",
            mode,
            "--actor",
            "Ada",
            "--root",
            str(root),
        ],
    )
    assert result.exit_code == 0, result.output
    return root / "demo"


@pytest.mark.parametrize("mode", ["openalex", "scopus"])
def test_parity__query_form__equals_the_hand_edit_the_cli_workflow_uses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Ticket #327: the CLI has no query writer, so its path is `init` plus the documented edit."""
    from prismabib.gui.query_form_model import QueryInputs

    inputs = QueryInputs(terms=["carbon capture", "policy"], year_start=2015, year_end=2024)
    _inject(monkeypatch)
    project = layer.create_project(
        "demo",
        title="Demo review",
        root=tmp_path / "layer",
        source_mode=mode,  # type: ignore[arg-type]
        reviewer="Ada",
    )
    with layer.Session(project) as session:
        layer.save_query(session, inputs)
    layer_out = normalise(tmp_path / "layer" / "demo")

    _inject(monkeypatch)
    folder = _init_cli(tmp_path / "cli", mode)
    toml = folder / "project.toml"
    text = toml.read_text(encoding="utf-8")
    if mode == "openalex":
        text = text.replace('search = ""', """search = '"carbon capture" OR "policy"'""")
    else:
        text = text.replace("terms = []", 'terms = [\n  "carbon capture",\n  "policy",\n]', 1)
    toml.write_text(text, encoding="utf-8")
    crit = folder / "criteria.yaml"
    crit.write_text(
        crit.read_text(encoding="utf-8")
        .replace("year_start: 1900", "year_start: 2015")
        .replace(f"year_end: {FIXED_NOW.year}", "year_end: 2024"),
        encoding="utf-8",
    )
    cli_out = normalise(folder)
    assert (
        layer_out["project.toml"] != normalise(_init_cli(tmp_path / "plain", mode))["project.toml"]
    )
    assert_parity(layer_out, cli_out)
    layer_out["project.toml"] += "x"  # liveness: one altered byte must fail
    with pytest.raises(AssertionError):
        assert_parity(layer_out, cli_out)


def test_parity__one_altered_byte__fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layer_out = _create_via_layer(tmp_path / "layer", monkeypatch)
    cli_out = _create_via_cli(tmp_path / "cli", monkeypatch)
    assert_parity(layer_out, cli_out)
    name = "project.toml"
    layer_out[name] = layer_out[name][:-1] + chr(ord(layer_out[name][-1]) ^ 1)
    with pytest.raises(AssertionError):
        assert_parity(layer_out, cli_out)


def test_parity__normaliser__hides_ids_and_timestamps_only() -> None:
    assert _TIMESTAMP.sub("<ts>", "a 2026-01-02T03:04:05+00:00 b") == "a <ts> b"
    assert _ID_FIELD.sub(r'\1"<id>"', '{"event_id": "abc", "actor": "Ada"}') == (
        '{"event_id": "<id>", "actor": "Ada"}'
    )


def test_parity__saved_criteria__gui_and_cli_projects_leave_identical_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticket #326: the CLI has no Criteria writer, so its Project (``init``) takes the same save."""
    from prismabib.gui.criteria_model import criteria_from_form, form_from_criteria

    _create_via_layer(tmp_path / "layer", monkeypatch)
    _create_via_cli(tmp_path / "cli", monkeypatch)
    outs = []
    for name in ("layer", "cli"):
        session = layer.open_session("demo", root=tmp_path / name)
        form = form_from_criteria(session.project.criteria)
        form |= {"version": "1.2.0", "languages": "English", "subject_areas": "COMP, MATH"}
        layer.save_criteria(session, criteria_from_form(form))
        session.close()
        outs.append(normalise(tmp_path / name / "demo"))
    assert "COMP" in outs[0]["criteria.yaml"]
    assert_parity(*outs)


def _scopus_project(root: Path) -> project_module.Project:
    from prismabib.gui.query_form_model import QueryInputs

    project = layer.create_project(
        "demo", title="Demo", root=root, source_mode="scopus", reviewer="Ada"
    )
    with layer.Session(project) as session:
        layer.save_query(session, QueryInputs(terms=["carbon"], year_start=2015, year_end=2024))
    return project


def _by_run(project: project_module.Project) -> dict[str, str]:
    """``normalise`` of Layer 0 with the run folder's own name (it holds a random part) hidden."""
    (run,) = (p.name for p in project.raw_dir.iterdir() if p.is_dir() and p.name != "_cache")
    return {k.replace(run, "<run>"): v for k, v in normalise(project.raw_dir).items()}


def _sealed_hash(project: project_module.Project) -> str:
    import json

    (manifest,) = project.raw_dir.glob("*/manifest.json")
    return str(json.loads(manifest.read_text(encoding="utf-8"))["payload_sha256"])


def test_parity__scopus_capture__cancelled_run_and_sealed_hash_equal_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticket #329: a cancelled GUI capture equals an interrupted CLI one; the seals hash alike."""
    import threading

    import respx

    from prismabib.capture.writer import capture_search
    from prismabib.errors import CaptureCancelledError
    from prismabib.gui import capture_layer as cl
    from prismabib.gui import tasks
    from prismabib.query import build_query_for_project
    from tests.integration.gui.test_capture import _URL, Stub

    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    gui = _scopus_project(tmp_path / "gui")
    cli = _scopus_project(tmp_path / "cli")
    stub = Stub()
    stub.hold = True
    with respx.mock:
        route = respx.get(_URL).mock(side_effect=stub)
        cl.note_size_check(gui, build_query_for_project(gui))
        with layer.Session(gui) as session:
            cl.start_capture(session, confirmed=True)
        assert stub.arrived.wait(10)
        cl.cancel_capture(gui)
        stub.release.set()
        tasks.runner_for(gui).join(10)

        route.side_effect = Stub()
        flag = threading.Event()
        with pytest.raises(CaptureCancelledError):  # the CLI stops the same way on Ctrl-C
            capture_search(cli, cancel=flag, on_progress=lambda p: p.pages_done == 2 and flag.set())
        assert_parity(_by_run(gui), _by_run(cli))

        stub.hold = False
        route.side_effect = stub
        with layer.Session(gui) as session:
            cl.start_capture(session, confirmed=True)
        tasks.runner_for(gui).join(10)
        route.side_effect = Stub()
        result = CliRunner().invoke(app, ["search", "demo", "--root", str(tmp_path / "cli")])
        assert result.exit_code == 0, result.output
    assert _sealed_hash(gui) == _sealed_hash(cli)
    assert_parity(_by_run(gui), _by_run(cli))
    tasks.reset_runners()


def test_parity__built_store__gui_build_equals_the_cli_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticket #331: the same stubbed Layer 0 builds the same tables through the GUI and the CLI."""
    from prismabib.gui import build_layer as bl
    from prismabib.gui import tasks
    from prismabib.store.checksums import table_checksums
    from prismabib.store.db import connect
    from tests.prisma_helpers import RUN_ID, RUN_STARTED_AT, CorpusSpec, RecordSpec
    from tests.store_helpers import write_sealed_run

    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    sums = []
    for name in ("gui", "cli"):
        project = layer.create_project(
            "demo", title="Demo", root=tmp_path / name, source_mode="scopus", reviewer="Ada"
        )
        write_sealed_run(
            project.raw_dir,
            RUN_ID,
            [
                r.to_entry()
                for r in CorpusSpec(records=[RecordSpec(number=n) for n in (1, 2, 3)]).records
            ],
            started_at=RUN_STARTED_AT,
        )
        if name == "gui":
            with layer.Session(project) as session:
                bl.start_build(session)
            tasks.runner_for(project).join(30)
            assert bl.build_status(project).outcome == "done"
        else:
            result = CliRunner().invoke(
                app, ["build", "demo", "--rebuild", "--root", str(tmp_path / name)]
            )
            assert result.exit_code == 0, result.output
        connection = connect(project)
        sums.append(table_checksums(connection))
        connection.close()
    assert sums[0] == sums[1]
    assert sums[0]["records"] != hashlib.sha256(b"").hexdigest()  # not a compare of empty tables
    tasks.reset_runners()


def test_parity__openalex_capture_and_refresh__sealed_payload_hash_equals_the_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ticket #330: the GUI capture (key from the app's file) and the CLI seal the same bytes."""
    import respx

    from prismabib.gui import tasks
    from prismabib.user_settings import write_user_settings
    from tests.integration.gui import test_openalex_capture as oa

    monkeypatch.delenv("SCOPUS_API_KEY", raising=False)
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    write_user_settings({"OPENALEX_API_KEY": "oa-test-key-not-real"}, tmp_path / "settings.env")
    gui, cli = oa._project(tmp_path, "gui"), oa._project(tmp_path, "cli")

    def hashes(project: project_module.Project) -> list[str]:
        out = []
        for run in oa._runs(project):
            h = hashlib.sha256()
            for page in sorted(run.glob("response-*.jsonl")):
                h.update(page.read_bytes())
            out.append(h.hexdigest())
        return out

    def run_cli(*extra: str) -> None:
        result = CliRunner().invoke(
            app,
            ["openalex-search", "demo", "--budget", "10", "--root", str(tmp_path / "cli"), *extra],
            env={"OPENALEX_API_KEY": "oa-test-key-not-real"},
        )
        assert result.exit_code == 0, result.output

    with respx.mock:
        respx.get(oa._URL).mock(side_effect=oa.Stub())
        oa._start(gui)
        oa._finish(gui)
        run_cli()
        assert hashes(gui) == hashes(cli) and len(hashes(gui)) == 1
        oa._start(gui, refresh=True)
        oa._finish(gui)
        run_cli("--refresh")
    assert hashes(gui) == hashes(cli) and len(hashes(gui)) == 2
    tasks.reset_runners()
