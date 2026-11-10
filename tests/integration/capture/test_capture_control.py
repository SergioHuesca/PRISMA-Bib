"""Cancel, progress and discard of a capture run (ticket #315, run statuses of spec #312).

Only the HTTP transport is stubbed. A cancel and an interruption both leave an unsealed run;
``discard_run`` marks a run and never deletes Layer 0 data.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path

import httpx
import pytest
import respx

from prismabib import source_mode as sm
from prismabib.capture.discard import discard_run
from prismabib.capture.layout import OPENALEX_SEARCH_DIRNAME, SealedRunError, is_sealed
from prismabib.capture.progress import CaptureProgress
from prismabib.capture.writer import capture_search
from prismabib.errors import CaptureCancelledError, ConfigError, ValidationError
from prismabib.project import Project
from tests.integration.capture.test_openalex_search import (
    project as oa_project,
)
from tests.integration.capture.test_openalex_search import (
    run as oa_run,
)
from tests.integration.capture.test_openalex_search import (
    runs as oa_runs,
)
from tests.integration.capture.test_openalex_search import (
    three_pages,
)

pytestmark = pytest.mark.integration

_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"
_QUERY = 'TITLE-ABS-KEY("x")'


def _page(ids: list[str], current: str, next_cursor: str | None) -> dict:
    return {
        "search-results": {
            "opensearch:totalResults": "3",
            "entry": [{"dc:identifier": f"SCOPUS_ID:{i}"} for i in ids],
            "cursor": {"@current": current, "@next": next_cursor or ""},
        }
    }


_PAGES = [
    _page(["1"], "*", "CUR1"),
    _page(["2"], "CUR1", "CUR2"),
    _page(["3"], "CUR2", None),
]


def _scopus_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Project:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    return Project.init("demo", title="Demo", root=tmp_path)


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _cancel_after(n: int, flag: threading.Event, seen: list[CaptureProgress]):
    def on_progress(progress: CaptureProgress) -> None:
        seen.append(progress)
        if progress.event == "page" and progress.pages_done == n:
            flag.set()

    return on_progress


# -- Scopus ------------------------------------------------------------------------------------


def test_scopus_cancel__between_pages__stops_before_next_page_and_resumes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _scopus_project(tmp_path, monkeypatch)
    flag = threading.Event()
    seen: list[CaptureProgress] = []

    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(
            side_effect=[httpx.Response(200, json=p) for p in _PAGES]
        )
        with pytest.raises(CaptureCancelledError):
            capture_search(
                project, query=_QUERY, cancel=flag, on_progress=_cancel_after(1, flag, seen)
            )
        assert route.call_count == 1  # page 2 was never requested
        (run_dir,) = [d for d in project.raw_dir.iterdir() if d.is_dir() and d.name != "_cache"]
        assert not is_sealed(run_dir)

        route.side_effect = [httpx.Response(200, json=p) for p in _PAGES[1:]]
        manifest = capture_search(project, query=_QUERY)

    assert manifest.run_id == run_dir.name
    assert manifest.pages_fetched == 3


def test_scopus_progress__callback__reports_pages_done_and_records_seen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _scopus_project(tmp_path, monkeypatch)
    seen: list[CaptureProgress] = []

    with respx.mock:
        respx.get(_SEARCH_URL).mock(side_effect=[httpx.Response(200, json=p) for p in _PAGES])
        manifest = capture_search(project, query=_QUERY, on_progress=seen.append)

    assert [(p.event, p.pages_done, p.records_seen, p.total_results) for p in seen] == [
        ("started", 0, 0, None),
        ("page", 1, 1, 3),
        ("page", 2, 2, 3),
        ("page", 3, 3, 3),
        ("sealed", 3, 3, 3),
    ]
    assert {p.run_id for p in seen} == {manifest.run_id}


def test_scopus_discard__marks_run_deletes_nothing_and_it_is_not_resumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _scopus_project(tmp_path, monkeypatch)
    flag = threading.Event()
    with respx.mock:
        respx.get(_SEARCH_URL).mock(side_effect=[httpx.Response(200, json=p) for p in _PAGES])
        with pytest.raises(CaptureCancelledError):
            capture_search(
                project, query=_QUERY, cancel=flag, on_progress=_cancel_after(1, flag, [])
            )
    (run_dir,) = [d for d in project.raw_dir.iterdir() if d.is_dir() and d.name != "_cache"]
    pages_before = {p.name: p.read_bytes() for p in run_dir.glob("page-*")}

    discard_run(project, run_dir.name)
    discard_run(project, run_dir.name)  # idempotent

    assert {p.name: p.read_bytes() for p in run_dir.glob("page-*")} == pages_before
    assert not is_sealed(run_dir)
    with respx.mock:
        respx.get(_SEARCH_URL).mock(side_effect=[httpx.Response(200, json=p) for p in _PAGES])
        manifest = capture_search(project, query=_QUERY)
    assert manifest.run_id != run_dir.name  # a discarded run is never resumed or sealed
    assert not is_sealed(run_dir)


def test_scopus_cancel_and_discard__never_alter_a_sealed_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _scopus_project(tmp_path, monkeypatch)
    with respx.mock:
        respx.get(_SEARCH_URL).mock(side_effect=[httpx.Response(200, json=p) for p in _PAGES])
        sealed = capture_search(project, query=_QUERY)
    sealed_dir = project.raw_dir / sealed.run_id
    before = _tree_hash(sealed_dir)

    with pytest.raises(SealedRunError):
        discard_run(project, sealed.run_id)
    flag = threading.Event()
    flag.set()
    with respx.mock:
        respx.get(_SEARCH_URL).mock(side_effect=[httpx.Response(200, json=p) for p in _PAGES])
        with pytest.raises(CaptureCancelledError):
            capture_search(project, query=_QUERY, cancel=flag)  # a new run, cancelled

    assert _tree_hash(sealed_dir) == before


@pytest.mark.parametrize("bad", ["", "..", "nope", "../x", "_cache"])
def test_discard__not_a_run__is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad: str
) -> None:
    project = _scopus_project(tmp_path, monkeypatch)
    with pytest.raises(ValidationError):
        discard_run(project, bad)


# -- OpenAlex ----------------------------------------------------------------------------------


def test_openalex_cancel__between_pages__stops_before_next_page_and_resumes(
    tmp_path: Path,
) -> None:
    proj = oa_project(tmp_path)
    fake = three_pages()
    flag = threading.Event()

    with pytest.raises(CaptureCancelledError):
        oa_run(proj, fake, cancel=flag, on_progress=_cancel_after(1, flag, []))

    assert len(fake.requests) == 1
    (run_dir,) = oa_runs(proj)
    assert not is_sealed(run_dir)

    second = oa_run(proj, fake)
    assert second.status == "sealed"
    assert second.run_id == run_dir.name
    assert [r.url.params["cursor"] for r in fake.requests] == ["*", "c1", "c2"]


def test_openalex_progress__callback__reports_pages_done_and_records_seen(
    tmp_path: Path,
) -> None:
    proj = oa_project(tmp_path)
    seen: list[CaptureProgress] = []

    oa_run(proj, three_pages(), on_progress=seen.append)

    assert [(p.event, p.pages_done, p.records_seen, p.total_results) for p in seen] == [
        ("started", 0, 0, None),
        ("page", 1, 2, 5),
        ("page", 2, 4, 5),
        ("page", 3, 5, 5),
        ("sealed", 3, 5, 5),
    ]


def test_openalex_discard__keeps_pages_and_still_blocks_a_mode_change(
    tmp_path: Path,
) -> None:
    proj = oa_project(tmp_path)
    fake = three_pages()
    flag = threading.Event()
    with pytest.raises(CaptureCancelledError):
        oa_run(proj, fake, cancel=flag, on_progress=_cancel_after(1, flag, []))
    (run_dir,) = oa_runs(proj)
    pages_before = {p.name: p.read_bytes() for p in run_dir.glob("response-*")}

    discard_run(proj, run_dir.name)

    assert {p.name: p.read_bytes() for p in run_dir.glob("response-*")} == pages_before
    assert not is_sealed(run_dir)
    with pytest.raises(ConfigError, match="blocks it"):  # refused while the discarded run is alone
        sm.set_mode(proj, "scopus+openalex", actor="t")
    fake.requests.clear()
    assert oa_run(proj, fake).run_id != run_dir.name  # not resumed ...
    assert fake.requests[0].url.params["cursor"] == "*"
    assert not is_sealed(run_dir)  # ... and never sealed
    assert (proj.raw_dir / OPENALEX_SEARCH_DIRNAME / run_dir.name).is_dir()


def test_openalex_cancel_and_discard__never_alter_a_sealed_run(tmp_path: Path) -> None:
    proj = oa_project(tmp_path)
    fake = three_pages()
    sealed = oa_run(proj, fake)
    (sealed_dir,) = oa_runs(proj)
    before = _tree_hash(sealed_dir)

    with pytest.raises(SealedRunError):
        discard_run(proj, sealed.run_id)
    flag = threading.Event()
    flag.set()
    with pytest.raises(CaptureCancelledError):
        oa_run(proj, fake, refresh=True, cancel=flag)

    assert _tree_hash(sealed_dir) == before
