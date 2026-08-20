"""Integration tests for ``src/prismabib/capture/writer.py`` (BUILD_PLAN Stage 2 Tests table, lines 821, 824-827).

Real filesystem (``tmp_path``), mocked network (``respx``) -- the standard
integration-kind mix (§3.7.2). Every test drives the frozen
``capture_search`` contract (BUILD_PLAN line 785) end to end rather than
reaching into ``prismabib.capture.writer``'s private helpers, except
``test_capture__raw_dir__is_never_reopened_for_write``, which calls
``_write_page`` directly -- that is white-box testing of an internal
function, not mocking one (§3.7.3 rule 1 forbids the latter, not the
former).

``test_search__malformed_page__raises_and_preserves_prior_pages`` lives here
rather than alongside its sibling ``test_search__*`` tests in
``tests/integration/sources/test_scopus.py``: bare ``ScopusClient.search``
never writes anything to disk, so "does not destroy Layer 0 work already
written" (BUILD_PLAN line 821) is inescapably a statement about
``capture_search`` + this module's writer, not about the client alone.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
import respx

from prismabib import __version__ as CLIENT_VERSION
from prismabib.capture.writer import SealedRunError, _write_page, capture_search
from prismabib.errors import ValidationError
from prismabib.project import Project

_SEARCH_URL = "https://api.elsevier.com/content/search/scopus"


def _page(*, entry_ids: list[str], current: str, next_cursor: str | None) -> dict:
    return {
        "search-results": {
            "opensearch:totalResults": "1575",
            "entry": [{"dc:identifier": f"SCOPUS_ID:{eid}"} for eid in entry_ids],
            "cursor": {"@current": current, "@next": next_cursor or ""},
        }
    }


def _init_project(tmp_path: Path) -> Project:
    return Project.init("demo", title="Demo Project", root=tmp_path)


@pytest.mark.integration
@pytest.mark.acceptance("S02-AC1")
def test_capture__run__writes_pages_and_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    page0 = _page(entry_ids=["1", "2"], current="*", next_cursor=None)

    with respx.mock:
        respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=page0))

        manifest = capture_search(project, query='TITLE-ABS-KEY("x")')

    run_dir = project.raw_dir / manifest.run_id
    page_path = run_dir / "page-0000.jsonl"
    manifest_path = run_dir / "manifest.json"

    assert page_path.is_file()
    assert manifest_path.is_file()
    assert manifest.pages_fetched == 1
    assert manifest.payload_files == ["page-0000.jsonl"]
    assert manifest.total_results == 1575
    assert manifest.client_version == CLIENT_VERSION
    assert manifest.criteria_version == project.criteria.version
    assert json.loads(page_path.read_text(encoding="utf-8")) == page0


@pytest.mark.integration
@pytest.mark.acceptance("S02-AC2")
def test_capture__warm_cache_rerun__payload_sha256_is_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    page0 = _page(entry_ids=["1"], current="*", next_cursor=None)

    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=page0))

        first = capture_search(project, query='TITLE-ABS-KEY("x")')
        second = capture_search(project, query='TITLE-ABS-KEY("x")')

    assert first.run_id != second.run_id
    assert first.payload_sha256 == second.payload_sha256
    assert route.call_count == 1


@pytest.mark.integration
@pytest.mark.acceptance("S02-AC3")
def test_capture__killed_at_page_2__resumes_without_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    page0 = _page(entry_ids=["1"], current="*", next_cursor="CUR1")
    page1 = _page(entry_ids=["2"], current="CUR1", next_cursor="CUR2")
    page2 = _page(entry_ids=["3"], current="CUR2", next_cursor=None)

    with respx.mock:
        respx.get(_SEARCH_URL).mock(
            side_effect=[
                httpx.Response(200, json=page0),
                httpx.Response(200, json=page1),
                httpx.ConnectError("simulated mid-run failure"),
            ]
        )

        with pytest.raises(httpx.ConnectError):
            capture_search(project, query='TITLE-ABS-KEY("x")')

    run_dirs = [
        entry for entry in project.raw_dir.iterdir() if entry.is_dir() and entry.name != "_cache"
    ]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "page-0000.jsonl").is_file()
    assert (run_dir / "page-0001.jsonl").is_file()
    assert not (run_dir / "page-0002.jsonl").exists()
    assert not (run_dir / "manifest.json").exists()
    page0_bytes_before = (run_dir / "page-0000.jsonl").read_bytes()
    page1_bytes_before = (run_dir / "page-0001.jsonl").read_bytes()

    with respx.mock:
        # Page 0 and page 1 are cache hits (their bytes were already stored
        # by the first, interrupted attempt) -- only page 2 reaches the
        # network this time.
        respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=page2))

        manifest = capture_search(project, query='TITLE-ABS-KEY("x")')

    assert manifest.run_id == run_dir.name
    assert manifest.pages_fetched == 3
    assert manifest.payload_files == ["page-0000.jsonl", "page-0001.jsonl", "page-0002.jsonl"]
    assert (run_dir / "page-0000.jsonl").read_bytes() == page0_bytes_before
    assert (run_dir / "page-0001.jsonl").read_bytes() == page1_bytes_before
    assert (run_dir / "manifest.json").is_file()


@pytest.mark.integration
def test_capture__raw_dir__is_never_reopened_for_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    page0 = _page(entry_ids=["1"], current="*", next_cursor=None)

    with respx.mock:
        respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=page0))
        manifest = capture_search(project, query='TITLE-ABS-KEY("x")')

    run_dir = project.raw_dir / manifest.run_id

    with pytest.raises(SealedRunError):
        _write_page(run_dir, "page-0001.jsonl", page0)

    assert not (run_dir / "page-0001.jsonl").exists()


@pytest.mark.integration
def test_search__malformed_page__raises_and_preserves_prior_pages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    page0 = _page(entry_ids=["1"], current="*", next_cursor="CUR1")

    with respx.mock:
        respx.get(_SEARCH_URL).mock(
            side_effect=[
                httpx.Response(200, json=page0),
                httpx.Response(200, content=b"{not valid json"),
            ]
        )

        with pytest.raises(ValidationError):
            capture_search(project, query='TITLE-ABS-KEY("x")')

    run_dirs = [
        entry for entry in project.raw_dir.iterdir() if entry.is_dir() and entry.name != "_cache"
    ]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert json.loads((run_dir / "page-0000.jsonl").read_text(encoding="utf-8")) == page0
    assert not (run_dir / "page-0001.jsonl").exists()
    assert not (run_dir / "manifest.json").exists()


@pytest.mark.integration
def test_capture__every_request__carries_view_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``capture_search`` itself must never request a lesser view.

    ``test_search__403_on_complete_view__raises_entitlement_error`` proves the
    *client* refuses to degrade. This closes the other half of the seam: that the
    capture layer asks for COMPLETE in the first place. STANDARD omits
    ``authkeywords`` and full affiliation data, so a downgrade here would quietly
    remove the keyword co-occurrence network and the geography analysis (§5 risk
    1, rated Critical) while every test still passed.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    page0 = _page(entry_ids=["1"], current="*", next_cursor="C2")
    page1 = _page(entry_ids=["2"], current="C2", next_cursor=None)

    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(
            side_effect=[httpx.Response(200, json=page0), httpx.Response(200, json=page1)]
        )
        capture_search(project, query='TITLE-ABS-KEY("x")')

    issued_views = [call.request.url.params.get("view") for call in route.calls]

    assert issued_views == ["COMPLETE", "COMPLETE"]


@pytest.mark.integration
def test_capture__cold_cache_resume__starts_from_persisted_cursor(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A resumed run continues from the stored cursor, not from ``*``.

    The HTTP cache is deleted before resuming, on purpose. Without that, a run
    that replays from ``cursor=*`` looks identical to one that resumes correctly,
    because every replayed page is a cache hit. But that cache is gitignored and
    disposable: on a cold cache the replay is real HTTP against a weekly quota,
    re-fetching pages Layer 0 already holds on disk. BUILD_PLAN line 768 requires
    resuming "without re-fetching" and §5 risk 2 says "never re-fetch what Layer 0
    already holds", so the cursor must be what carries the resumption, not luck.
    """
    monkeypatch.setenv("SCOPUS_API_KEY", "test-api-key")
    project = _init_project(tmp_path)
    page0 = _page(entry_ids=["1"], current="*", next_cursor="C2")
    page1 = _page(entry_ids=["2"], current="C2", next_cursor=None)

    with respx.mock:
        respx.get(_SEARCH_URL).mock(
            side_effect=[httpx.Response(200, json=page0), httpx.RequestError("boom")]
        )
        with pytest.raises(httpx.RequestError):
            capture_search(project, query='TITLE-ABS-KEY("x")')

    run_dir = next(p for p in project.raw_dir.iterdir() if p.is_dir() and p.name != "_cache")
    first_page_before = (run_dir / "page-0000.jsonl").read_bytes()
    cursor_state = json.loads((run_dir / "cursor.json").read_text(encoding="utf-8"))
    shutil.rmtree(project.raw_dir / "_cache", ignore_errors=True)

    with respx.mock:
        route = respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json=page1))
        manifest = capture_search(project, query='TITLE-ABS-KEY("x")')

    issued_cursors = [call.request.url.params.get("cursor") for call in route.calls]

    assert cursor_state["next_cursor"] == "C2"
    assert issued_cursors == ["C2"]
    assert (run_dir / "page-0000.jsonl").read_bytes() == first_page_before
    assert manifest.run_id == run_dir.name
    assert manifest.pages_fetched == 2
