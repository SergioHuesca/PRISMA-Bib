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
