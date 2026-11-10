"""The size-check screen, built headless over a stubbed transport (ticket #328)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

pytest.importorskip("panel")

import panel as pn

from prismabib.gui import layer
from prismabib.gui.query_form_model import QueryInputs
from prismabib.gui.size_check import build_size_check
from prismabib.gui.size_check_model import verdict
from prismabib.project import Project
from prismabib.user_settings import write_user_settings
from tests.integration.gui.test_home import unlabelled_controls

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 11, 10, 12, 0, 0, tzinfo=UTC)
_ZONE = timezone(timedelta(hours=5), "UTC+5")


@pytest.fixture(autouse=True)
def _no_ambient_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("OPENALEX_API_KEY", "SCOPUS_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def _make(tmp_path: Path, mode: str) -> Project:
    project = layer.create_project(
        "demo",
        title="Demo",
        root=tmp_path / "p",
        source_mode=mode,  # type: ignore[arg-type]
        reviewer="A",
    )
    with layer.Session(project) as session:
        layer.save_query(session, QueryInputs(terms=["carbon"], year_start=2015, year_end=2024))
    return project


def _keyfile(tmp_path: Path, name: str | None) -> Path:
    path = tmp_path / "user.env"
    write_user_settings(
        {name: "fake-key-not-real"} if name else {}, path
    )  # pragma: allowlist secret
    return path


def _oa(count: int, headers: dict[str, str] | None = None) -> httpx.MockTransport:
    body = json.dumps({"meta": {"count": count}, "results": []})
    return httpx.MockTransport(lambda r: httpx.Response(200, content=body, headers=headers))


def _scopus(total: int, headers: dict[str, str] | None = None) -> httpx.MockTransport:
    body = {"search-results": {"opensearch:totalResults": str(total), "entry": []}}
    return httpx.MockTransport(lambda r: httpx.Response(200, json=body, headers=headers))


def _text(view: pn.viewable.Viewable) -> str:
    return "\n".join(str(p.object) for p in view.select(pn.pane.Markdown))


def _button(view: pn.viewable.Viewable, start: str) -> pn.widgets.Button:
    return next(b for b in view.select(pn.widgets.Button) if b.name.startswith(start))


def _check(view: pn.viewable.Viewable) -> None:
    _button(view, "Check the size").clicks += 1


def test_scopus__shows_count_cost_and_weekly_allowance(tmp_path: Path) -> None:
    project = _make(tmp_path, "scopus")
    view = build_size_check(
        project,
        user_file=_keyfile(tmp_path, "SCOPUS_API_KEY"),
        transport=_scopus(101, {"X-RateLimit-Remaining": "19990"}),
    )
    _check(view)
    text = _text(view)
    assert "Records: 101" in text
    assert "Quota cost: 5 requests" in text
    assert "Remaining weekly allowance: 19990" in text
    assert "exceeded" not in text


def test_scopus__quota_that_would_run_out__is_stated_before_capture(tmp_path: Path) -> None:
    project = _make(tmp_path, "scopus")
    view = build_size_check(
        project,
        user_file=_keyfile(tmp_path, "SCOPUS_API_KEY"),
        transport=_scopus(101, {"X-RateLimit-Remaining": "3"}),
    )
    _check(view)
    assert "weekly quota would be exceeded" in _text(view)


def test_openalex__shows_numbers_and_reset_in_local_time(tmp_path: Path) -> None:
    project = _make(tmp_path, "openalex")
    view = build_size_check(
        project,
        user_file=_keyfile(tmp_path, "OPENALEX_API_KEY"),
        transport=_oa(250, {"x-ratelimit-remaining": "9000", "x-ratelimit-reset": "3600"}),
        now=lambda: _NOW,
        tz=_ZONE,
    )
    _check(view)
    text = _text(view)
    assert "Records: 250" in text
    assert "Cost bound: $" in text
    assert "Page budget: 3 pages" in text
    assert "Remaining daily allowance: 9000" in text
    assert "Allowance resets: 2026-11-10 18:00 UTC+5" in text  # 13:00 UTC shown at +5


def test_allowance_not_reported__the_screen_says_so(tmp_path: Path) -> None:
    project = _make(tmp_path, "openalex")
    view = build_size_check(
        project, user_file=_keyfile(tmp_path, "OPENALEX_API_KEY"), transport=_oa(5)
    )
    _check(view)
    text = _text(view)
    assert "Remaining daily allowance: not reported" in text
    assert "resets: not reported" in text


@pytest.mark.parametrize(
    ("count", "warns", "blocks"),
    [(2000, False, False), (2001, True, False), (10000, True, False), (10001, False, True)],
)
def test_thresholds__warning_confirm_and_block(
    tmp_path: Path, count: int, warns: bool, blocks: bool
) -> None:
    assert verdict(count) == ("block" if blocks else "warn" if warns else "ok")
    project = _make(tmp_path, "openalex")
    view = build_size_check(
        project, user_file=_keyfile(tmp_path, "OPENALEX_API_KEY"), transport=_oa(count)
    )
    _check(view)
    text = _text(view)
    go = _button(view, "Continue to capture")
    confirm = next(c for c in view.select(pn.widgets.Checkbox))
    assert ("narrow the query, or confirm" in text) is warns
    assert ("would slow the Screener" in text) is blocks
    assert confirm.visible is warns
    assert go.disabled is (warns or blocks)
    if warns:
        confirm.value = True
        assert go.disabled is False


def test_no_key__explains_and_links_to_the_keys_step(tmp_path: Path) -> None:
    project = _make(tmp_path, "openalex")
    opened: list[bool] = []
    view = build_size_check(
        project,
        user_file=_keyfile(tmp_path, None),
        transport=_oa(5),
        on_keys=lambda: opened.append(True),
    )
    link = _button(view, "Go to the keys step")
    assert link.visible is False
    _check(view)
    assert "key is needed" in _text(view)
    assert link.visible is True
    link.clicks += 1
    assert opened == [True]


def test_offline__shows_a_message_and_can_be_run_again_with_the_query_kept(tmp_path: Path) -> None:
    project = _make(tmp_path, "openalex")
    before = (project.root / "project.toml").read_bytes()
    calls = {"offline": 1}

    def handler(request: httpx.Request) -> httpx.Response:
        if calls["offline"]:
            raise httpx.ConnectError("offline")
        return httpx.Response(200, json={"meta": {"count": 7}, "results": []})

    view = build_size_check(
        project,
        user_file=_keyfile(tmp_path, "OPENALEX_API_KEY"),
        transport=httpx.MockTransport(handler),
    )
    _check(view)
    assert "offline" in _text(view)
    assert "saved query is unchanged" in _text(view)
    calls["offline"] = 0
    _check(view)
    assert "Records: 7" in _text(view)
    assert (project.root / "project.toml").read_bytes() == before


def test_screen__every_control_has_a_label(tmp_path: Path) -> None:
    project = _make(tmp_path, "scopus")
    view = build_size_check(project, user_file=_keyfile(tmp_path, None))
    assert unlabelled_controls(view) == []
