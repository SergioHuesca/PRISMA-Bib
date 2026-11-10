"""The whole create-to-export chain: the layer gives the CLI's results (GUI spec #312, ticket #336).

One chain runs the same inputs through the layer (no browser) and through the CLI or the library,
stage by stage: create, query, criteria, size check, capture, build, screening decisions, flow
counts, export. Every stage leaves one normalised output, and :func:`assert_chain` compares the two
runs stage by stage with :func:`tests.integration.gui.test_parity.assert_parity`.

Where the CLI has no path, the other side is the library, as in the per-step cases: criteria take
the same ``layer.save_criteria`` write (the CLI has no Criteria writer), the query is the
documented hand-edit of ``project.toml``, the size check is the library call, and screening is the
Screener's decide interface. The clock and the run id are fixed for both paths, so the sealed
payload hash and ``numbers.json`` are compared by exact equality; only the manifest's git fields
are left out of the export.

The stubbed transports serve invented Records (``RecordSpec``, ``works``); no licensed payload and
no key appears here (the keys are the obvious test strings).

When ``PRISMABIB_PARITY_DIGEST`` names a file, the digest of both runs of both paths is written to
``<that file>.chain``; the gui-parity CI job compares it across its two time zones and locales.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

pytest.importorskip("panel")

import panel as pn
import respx
from pydantic import SecretStr
from typer.testing import CliRunner

from prismabib.capture import openalex_search as oa_search_module
from prismabib.capture import writer as writer_module
from prismabib.cli import app
from prismabib.config import Settings
from prismabib.gui import build_layer as bl
from prismabib.gui import capture_layer as cl
from prismabib.gui import export_layer as xl
from prismabib.gui import layer, tasks
from prismabib.gui import openalex_capture_layer as oal
from prismabib.gui import screening_layer as sl
from prismabib.gui.query_form_model import QueryInputs
from prismabib.gui.size_check import build_size_check
from prismabib.prisma import log as log_module
from prismabib.prisma.log import DecisionLog
from prismabib.project import Criteria, Project
from prismabib.query import build_openalex_query_for_project, build_query_for_project
from prismabib.screening.queue import ScreeningQueue
from prismabib.screening.ui import Screener
from prismabib.sizecheck import check_openalex_size, check_scopus_size
from prismabib.stage import PrismaStage
from prismabib.store.checksums import table_checksums
from prismabib.store.db import connect
from prismabib.user_settings import USER_FILE_OVERRIDE_VAR, write_user_settings
from tests.conftest import SeededIdFactory
from tests.integration.gui import test_flow as tf
from tests.integration.gui.test_parity import (
    FIXED_NOW,
    _export_files,
    _FixedDatetime,
    _inject,
    _sealed_hash,
    assert_parity,
    digest,
    normalise,
)
from tests.prisma_helpers import CriteriaSpec, RecordSpec

pytestmark = pytest.mark.integration

STAGES = (
    "create",
    "query",
    "criteria",
    "size_check",
    "capture",
    "build",
    "screening",
    "flow",
    "export",
)
_FIRST_GUI_STAGE_AFTER_CAPTURE = STAGES.index("build")

_RUN_ID = "20260102T030405Z-00000000"
_SCOPUS_KEY = "test-api-key"
_OPENALEX_KEY = "oa-test-key-not-real"  # pragma: allowlist secret
_SCOPUS_URL = "https://api.elsevier.com/content/search/scopus"
_OPENALEX_URL = "https://api.openalex.org/works"
_TERMS = ["carbon capture", "policy"]

Stages = dict[str, dict[str, str]]


# -- the stubbed transports (invented Records only) ---------------------------------------------


def _scopus_pages() -> list[dict[str, Any]]:
    cursors = ["*", "C1", "C2", ""]
    return [
        {
            "search-results": {
                "opensearch:totalResults": "3",
                "entry": [RecordSpec(number=n).to_entry()],
                "cursor": {"@current": cursors[n - 1], "@next": cursors[n]},
            }
        }
        for n in (1, 2, 3)
    ]


def _scopus_handler(request: httpx.Request) -> httpx.Response:
    order = ["*", "C1", "C2"]
    return httpx.Response(200, json=_scopus_pages()[order.index(request.url.params["cursor"])])


def _openalex_pages() -> list[list[dict[str, Any]]]:
    from tests.integration.capture.test_openalex_search import works

    def titled(*numbers: int) -> list[dict[str, Any]]:  # the loader needs a title to keep a work
        return [
            w | {"title": f"Synthetic work {w['cited_by_count']}", "publication_year": 2020}
            for w in works(*numbers)
        ]

    return [titled(1, 2), titled(3, 4), titled(5)]


def _openalex_handler(request: httpx.Request) -> httpx.Response:
    from tests.integration.capture.test_openalex_search import Fake

    return Fake(_openalex_pages()).default(request)


def _size_transport(mode: str) -> httpx.MockTransport:
    if mode == "scopus":
        body: dict[str, Any] = {"search-results": {"opensearch:totalResults": "3", "entry": []}}
    else:
        body = {"meta": {"count": 5}, "results": []}
    return httpx.MockTransport(lambda r: httpx.Response(200, json=body))


# -- the two paths, stage by stage --------------------------------------------------------------


def _criteria() -> Criteria:
    text = CriteriaSpec(year_start=2015, year_end=2024).to_yaml()
    return Criteria.model_validate(yaml.safe_load(text))


def _create(mode: str, root: Path, *, via_layer: bool) -> Project:
    if via_layer:
        return layer.create_project(
            "demo",
            title="Demo review",
            root=root,
            source_mode=mode,
            reviewer="Ada",  # type: ignore[arg-type]
        )
    args = ["init", "demo", "--title", "Demo review", "--sources", mode, "--actor", "Ada"]
    result = CliRunner().invoke(app, [*args, "--root", str(root)])
    assert result.exit_code == 0, result.output
    return Project.open("demo", root=root)


def _query(project: Project, mode: str, *, via_layer: bool) -> None:
    if via_layer:
        inputs = QueryInputs(terms=_TERMS, year_start=2015, year_end=2024)
        with layer.Session(project) as session:
            layer.save_query(session, inputs)
        return
    toml = project.root / "project.toml"  # the CLI workflow's documented hand-edit
    text = toml.read_text(encoding="utf-8")
    if mode == "openalex":
        text = text.replace('search = ""', """search = '"carbon capture" OR "policy"'""")
    else:
        text = text.replace("terms = []", 'terms = [\n  "carbon capture",\n  "policy",\n]', 1)
    toml.write_text(text, encoding="utf-8")
    crit = project.root / "criteria.yaml"
    crit.write_text(
        crit.read_text(encoding="utf-8")
        .replace("year_start: 1900", "year_start: 2015")
        .replace(f"year_end: {FIXED_NOW.year}", "year_end: 2024"),
        encoding="utf-8",
    )


def _save_criteria(project: Project, *, via_layer: bool) -> None:
    del via_layer  # the CLI has no Criteria writer: both paths take the layer's save (as #326)
    with layer.Session(project) as session:
        layer.save_criteria(session, _criteria())


def _size_check(project: Project, mode: str, user_file: Path, *, via_layer: bool) -> str:
    transport = _size_transport(mode)
    if via_layer:
        view = build_size_check(
            project, user_file=user_file, transport=transport, now=lambda: FIXED_NOW
        )
        next(b for b in view.select(pn.widgets.Button) if "Check" in b.name).clicks += 1
        text = "\n".join(str(p.object) for p in view.select(pn.pane.Markdown))
        return next(line for line in text.splitlines() if line.startswith("Records: "))
    if mode == "scopus":
        found = check_scopus_size(
            build_query_for_project(project),
            settings=Settings(scopus_api_key=SecretStr(_SCOPUS_KEY)),
            transport=transport,
        )
        return f"Records: {found.record_count:,}"
    oa = check_openalex_size(
        build_openalex_query_for_project(project),
        settings=Settings(openalex_api_key=SecretStr(_OPENALEX_KEY)),
        transport=transport,
        now=lambda: FIXED_NOW,
    )
    return f"Records: {oa.record_count:,}"


def _capture(project: Project, mode: str, root: Path, *, via_layer: bool) -> None:
    url, handler = (
        (_SCOPUS_URL, _scopus_handler) if mode == "scopus" else (_OPENALEX_URL, _openalex_handler)
    )
    with respx.mock:
        respx.get(url).mock(side_effect=handler)
        if via_layer:
            with layer.Session(project) as session:
                if mode == "scopus":
                    cl.start_capture(session, confirmed=True)
                else:
                    oal.start_openalex_capture(session, budget=10, confirmed=True)
            tasks.runner_for(project).join(30)
            status = (
                cl.capture_status(project) if mode == "scopus" else oal.openalex_status(project)
            )
            assert getattr(status, "state", None) is cl.CaptureState.SEALED, status
            return
        command = ["search"] if mode == "scopus" else ["openalex-search", "--budget", "10"]
        env = {"SCOPUS_API_KEY": _SCOPUS_KEY, "OPENALEX_API_KEY": _OPENALEX_KEY}
        args = [command[0], "demo", *command[1:], "--root", str(root)]
        result = CliRunner().invoke(app, args, env=env)
        assert result.exit_code == 0, result.output


def _build(project: Project, root: Path, *, via_layer: bool) -> str:
    if via_layer:
        with layer.Session(project) as session:
            bl.start_build(session)
        tasks.runner_for(project).join(60)
        assert bl.build_status(project).outcome == "done"
    else:
        result = CliRunner().invoke(app, ["build", "demo", "--rebuild", "--root", str(root)])
        assert result.exit_code == 0, result.output
    connection = connect(project)
    try:
        return json.dumps(table_checksums(connection), sort_keys=True)
    finally:
        connection.close()


def _screen(project: Project, *, via_layer: bool) -> str:
    stage = PrismaStage.TITLE_ABSTRACT
    log = DecisionLog(project, id_factory=SeededIdFactory(0, "id"))
    queue = ScreeningQueue(project, stage, "Ada", log=log)
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
    return project.decisions_path.read_text(encoding="utf-8")


def _flow(project: Project, root: Path, *, via_layer: bool) -> str:
    numbers = tf.screen_numbers(project) if via_layer else tf.cli_numbers(project)
    assert len(numbers) > 10
    return json.dumps(numbers)


def _export(project: Project, root: Path, *, via_layer: bool) -> dict[str, str]:
    if via_layer:
        with layer.Session(project) as session:
            xl.start_export(session)
        tasks.runner_for(project).join(60)
        assert xl.export_status(project).outcome == "done"
    else:
        result = CliRunner().invoke(app, ["export", "demo", "--root", str(root)])
        assert result.exit_code == 0, result.output
    out = _export_files(project)
    out["numbers.json (exact)"] = (project.root / "exports" / "numbers.json").read_bytes().decode()
    out["sealed payload hash (exact)"] = _sealed_hash_any(project)
    return out


def _sealed_hash_any(project: Project) -> str:
    """The payload hash of the Scopus run, or of every OpenAlex search run, exactly."""
    if project.source_mode != "openalex":
        return _sealed_hash(project)
    hashes = []
    for run in sorted((project.raw_dir / "openalex-search").iterdir()):
        h = hashlib.sha256()
        for page in sorted(run.glob("response-*.jsonl")):
            h.update(page.read_bytes())
        hashes.append(h.hexdigest())
    return "\n".join(hashes)


def run_chain(
    mode: str, root: Path, monkeypatch: pytest.MonkeyPatch, *, gui_from: int = 0
) -> Stages:
    """Every stage's output; stages from index ``gui_from`` on go through the layer."""
    _inject(monkeypatch)
    monkeypatch.setattr(log_module, "datetime", _FixedDatetime)
    for module in (writer_module, oa_search_module):
        monkeypatch.setattr(module, "datetime", _FixedDatetime)
        monkeypatch.setattr(module, "new_run_id", lambda: _RUN_ID)
    user_file = Path(os.environ[USER_FILE_OVERRIDE_VAR])
    out: Stages = {}

    def gui(stage: str) -> bool:
        return STAGES.index(stage) >= gui_from

    project = _create(mode, root, via_layer=gui("create"))
    out["create"] = normalise(project.root)
    _query(project, mode, via_layer=gui("query"))
    out["query"] = normalise(project.root)
    _save_criteria(project, via_layer=gui("criteria"))
    out["criteria"] = normalise(project.root)
    out["size_check"] = {"size": _size_check(project, mode, user_file, via_layer=gui("size_check"))}
    _capture(project, mode, root, via_layer=gui("capture"))
    out["capture"] = normalise(project.raw_dir) | {"sealed hash (exact)": _sealed_hash_any(project)}
    out["build"] = {"table checksums": _build(project, root, via_layer=gui("build"))}
    decisions = _screen(project, via_layer=gui("screening"))
    out["screening"] = normalise_text({"decisions.jsonl": decisions})
    out["flow"] = {"counts": _flow(project, root, via_layer=gui("flow"))}
    out["export"] = _export(project, root, via_layer=gui("export"))
    tasks.reset_runners()
    return out


def normalise_text(files: dict[str, str]) -> dict[str, str]:
    """``normalise`` for in-memory text: through a scratch file, so one function does the work."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        for name, text in files.items():
            (Path(tmp) / name).write_bytes(text.encode("utf-8"))
        return normalise(Path(tmp))


def assert_chain(layer_out: Stages, other: Stages) -> None:
    """Fail unless every stage is identical, naming the first stage that is not."""
    assert layer_out.keys() == other.keys() == set(STAGES)
    for stage in STAGES:
        try:
            assert_parity(layer_out[stage], other[stage])
        except AssertionError as exc:
            raise AssertionError(f"stage {stage!r} differs: {exc}") from exc


# -- fixtures and tests -------------------------------------------------------------------------


@pytest.fixture(scope="module", params=["openalex", "scopus"])
def chains(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[Any]:
    """The all-layer, all-CLI and CLI-then-layer runs of one mode (each Project in its own folder)."""
    mode: str = request.param
    base = tmp_path_factory.mktemp(f"chain-{mode}")
    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(USER_FILE_OVERRIDE_VAR, str(base / "settings.env"))
        mp.setenv("SCOPUS_API_KEY", _SCOPUS_KEY)
        mp.delenv("OPENALEX_API_KEY", raising=False)
        mp.chdir(base)  # a stray ./.env must not be what supplies a key
        write_user_settings({"OPENALEX_API_KEY": _OPENALEX_KEY}, base / "settings.env")
        runs = {
            name: run_chain(mode, base / name, mp, gui_from=gui_from)
            for name, gui_from in (
                ("layer", 0),
                ("cli", len(STAGES)),
                ("cli_then_layer", _FIRST_GUI_STAGE_AFTER_CAPTURE),
            )
        }
    yield mode, runs
    tasks.reset_runners()


def test_chain__layer_equals_cli_at_every_stage(chains: Any) -> None:
    mode, runs = chains
    assert all(runs["layer"][stage] for stage in STAGES)  # no stage compares nothing
    done = runs["layer"]
    assert done["size_check"]["size"] == ("Records: 3" if mode == "scopus" else "Records: 5")
    assert any(name.endswith("manifest.json") for name in done["capture"])
    sums = json.loads(done["build"]["table checksums"])
    table = "records" if mode == "scopus" else "openalex_records"
    assert sums[table] != hashlib.sha256(b"").hexdigest()  # not a compare of empty tables
    assert done["screening"]["decisions.jsonl"].count("\n") == 3
    assert {"manifest.json", "records.csv", "numbers.json (exact)"} <= done["export"].keys()
    assert runs["layer"]["flow"]["counts"] == runs["cli"]["flow"]["counts"]
    assert_chain(runs["layer"], runs["cli"])
    target = os.environ.get("PRISMABIB_PARITY_DIGEST")
    if target:
        # pytest-randomly shuffles the order, so merge by mode and write sorted, never append
        path = Path(f"{target}.chain")
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        mine = digest(*(runs[k][s] for s in STAGES for k in ("layer", "cli")))
        merged = {line.split()[0]: line for line in lines} | {mode: f"{mode} {mine}"}
        path.write_text("".join(f"{merged[m]}\n" for m in sorted(merged)), encoding="utf-8")


def test_chain__cli_project_continues_in_the_layer__export_equals_the_all_cli_run(
    chains: Any,
) -> None:
    """A Project created and captured on the CLI, opened in the layer, built to export."""
    _, runs = chains
    mixed, cli = runs["cli_then_layer"], runs["cli"]
    assert_chain(mixed, cli)
    assert mixed["export"]["numbers.json (exact)"] == cli["export"]["numbers.json (exact)"]
    assert (
        mixed["export"]["sealed payload hash (exact)"]
        == (cli["export"]["sealed payload hash (exact)"])
    )


def _flip_last_byte(files: dict[str, str]) -> dict[str, str]:
    name = min(files)
    text = files[name]
    return {**files, name: text[:-1] + chr(ord(text[-1]) ^ 1)}


@pytest.mark.parametrize("stage", STAGES)
def test_chain__one_altered_byte_at_a_stage__fails_the_comparison(chains: Any, stage: str) -> None:
    """Liveness: the chain comparison cannot pass by comparing nothing, at any stage."""
    _, runs = chains
    assert_chain(runs["layer"], runs["cli"])
    altered = {**runs["layer"], stage: _flip_last_byte(runs["layer"][stage])}
    with pytest.raises(AssertionError, match=f"stage {stage!r}"):
        assert_chain(altered, runs["cli"])
