"""CLI tests for ``prismabib cascade`` (ADR 0041 Decision 7): opt-in, budget, reuse, report.

No network: both capture functions are wrapped so they run against ``httpx.MockTransport``
fakes that count requests (a re-request is invisible in the output, so the counter is the
assertion).
"""

from __future__ import annotations

import functools
import json
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest
from typer.testing import CliRunner

from prismabib.capture import cascade_commands
from prismabib.capture.candidates import CandidateEvent, CandidateLog, CandidateRow
from prismabib.cli import app
from prismabib.config import Settings
from prismabib.project import Project
from prismabib.sources.ratelimit import RateLimiter

runner = CliRunner()
_AFF = [{"affilname": "Uni", "affiliation-city": "Lima", "affiliation-country": "Peru"}]


class Fake:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler
        self.transport = httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._handler(request)


def _json(payload: Any) -> httpx.Response:
    return httpx.Response(200, content=json.dumps(payload).encode())


def _scopus(request: httpx.Request) -> httpx.Response:
    if "/abstract/eid/" in request.url.path:
        return _json({"abstracts-retrieval-response": {"affiliation": _AFF}})
    q = request.url.params["query"]
    entries = [
        {"dc:identifier": f"SCOPUS_ID:{sid}", "prism:doi": doi, "affiliation": _AFF}
        for sid, doi in (("1", "10.1/a"), ("3", "10.1/c"))
        if f'"{doi}"' in q
    ]
    total = str(len(entries))
    return _json({"search-results": {"opensearch:totalResults": total, "entry": entries}})


def _openalex(request: httpx.Request) -> httpx.Response:
    wanted = request.url.params["filter"].removeprefix("doi:").split("|")
    results = [
        {
            "id": f"https://openalex.org/W{i}",
            "doi": f"https://doi.org/{d}",
            "title": "T",
            "publication_year": 2020,
            "abstract_inverted_index": {"a": [0]},
            "authorships": [{"author": {"display_name": "Ada"}, "institutions": []}],
            "topics": [{"display_name": "Topic", "score": 0.9}],
        }
        for i, d in enumerate(wanted)
    ]
    return _json({"results": results})


@pytest.fixture
def fakes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[dict[str, Fake]]:
    monkeypatch.chdir(tmp_path)  # no real .env
    monkeypatch.delenv("OPENALEX_API_KEY", raising=False)
    out = {"scopus": Fake(_scopus), "openalex": Fake(_openalex)}
    common = {"settings": Settings(scopus_api_key="x"), "rate_limiter": RateLimiter(rate=1e6)}
    for name, attr in (("scopus", "capture_scopus_cascade"), ("openalex", "capture_openalex")):
        real = getattr(cascade_commands, attr)
        monkeypatch.setattr(
            cascade_commands,
            attr,
            functools.partial(real, transport=out[name].transport, **common),
        )
    yield out


@pytest.fixture
def project(tmp_path: Path) -> Project:
    p = Project.init("demo", title="Demo", root=tmp_path)
    p.db_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(p.db_path))
    con.execute(
        "CREATE TABLE records (record_id TEXT, doi TEXT, title TEXT, year INTEGER, "
        "abstract_outcome TEXT, authors_outcome TEXT, affiliations_outcome TEXT, "
        "keywords_outcome TEXT)"
    )
    u, v = ("'unavailable'",) * 4, ("'value'",) * 4
    rows = [("1", "10.1/a", "A", 2020, u), ("2", "10.1/b", "B", 2021, u)]
    rows.append(("9", "10.1/z", "Z", 2019, v))  # complete: never requested
    for sid, doi, title, year, states in rows:
        con.execute(
            f"INSERT INTO records VALUES ('scopus:2-s2.0-{sid}', '{doi}', '{title}', {year}, "
            f"{', '.join(states)})"
        )
    con.close()
    CandidateLog(p.root / "decisions" / "candidates.jsonl").add(
        CandidateEvent.from_row(
            CandidateRow("Cand", 2018, "10.1/c"), reviewer="sh", pasted_at=datetime.now(UTC)
        )
    )
    return p


def _cascade(root: Path, *args: str) -> tuple[int, str]:
    r = runner.invoke(app, ["cascade", "demo", "--root", str(root), *args])
    return r.exit_code, r.output


def _dois(fake: Fake) -> set[str]:
    out: set[str] = set()
    for r in fake.requests:
        q = r.url.params.get("query") or r.url.params.get("filter") or ""
        out |= {d for d in ("10.1/a", "10.1/b", "10.1/c", "10.1/z") if d in q}
    return out


@pytest.mark.integration
def test_cascade__no_opt_in__scopus_never_called(project: Project, fakes: dict[str, Fake]) -> None:
    code, out = _cascade(project.root.parent)

    assert code == 0, out
    assert fakes["scopus"].requests == []
    assert not (project.raw_dir / "scopus-cascade").exists()
    assert _dois(fakes["openalex"]) == {"10.1/a", "10.1/b", "10.1/c"}  # z is complete


@pytest.mark.integration
def test_cascade__opt_in_without_budget__is_a_clear_error(
    project: Project, fakes: dict[str, Fake]
) -> None:
    code, out = _cascade(project.root.parent, "--opt-in")

    assert code != 0
    assert "--scopus-budget" in out
    assert fakes["scopus"].requests == [] and fakes["openalex"].requests == []


@pytest.mark.integration
def test_cascade__opt_in__runs_levels_and_reports_from_manifests(
    project: Project, fakes: dict[str, Fake]
) -> None:
    code, out = _cascade(project.root.parent, "--opt-in", "--scopus-budget", "10")

    assert code == 0, out
    assert _dois(fakes["scopus"]) == {"10.1/a", "10.1/b", "10.1/c"}
    meta = [r for r in fakes["scopus"].requests if "/abstract/eid/" in r.url.path]
    # 10.1/b is not_found at level 2; its record is asked at level 3, a and c are not.
    assert [r.url.path.rsplit("/", 1)[-1] for r in meta] == ["2-s2.0-2"]
    assert "level 2: value 2, absent 0, not_found 1, refused 0" in out
    assert "level 3: value 1, absent 0, not_found 0, refused 0" in out
    assert "openalex: value 3, not_found 0, refused 0" in out
    assert "remaining unavailable: 0" in out


@pytest.mark.integration
def test_cascade__second_run__makes_no_request(project: Project, fakes: dict[str, Fake]) -> None:
    args = ("--opt-in", "--scopus-budget", "10")
    _cascade(project.root.parent, *args)
    before = (len(fakes["scopus"].requests), len(fakes["openalex"].requests))
    runs = sorted((project.raw_dir / "scopus-cascade").iterdir())

    code, out = _cascade(project.root.parent, *args)

    assert code == 0, out
    assert (len(fakes["scopus"].requests), len(fakes["openalex"].requests)) == before
    assert sorted((project.raw_dir / "scopus-cascade").iterdir()) == runs
    assert "level 2: value 2" in out  # the report still reads the earlier sealed runs


@pytest.mark.integration
def test_cascade__unsealed_run__is_not_resumed_and_not_counted(
    project: Project, fakes: dict[str, Fake]
) -> None:
    stale = project.raw_dir / "scopus-cascade" / "stale-run"
    stale.mkdir(parents=True)
    (stale / "progress.json").write_text("{}", encoding="utf-8")

    code, out = _cascade(project.root.parent, "--opt-in", "--scopus-budget", "10")

    assert code == 0, out
    assert [p.name for p in stale.iterdir()] == ["progress.json"]
    assert len(list((project.raw_dir / "scopus-cascade").iterdir())) == 3  # stale + L2 + L3


@pytest.mark.integration
def test_cascade__dry_run__makes_no_call_and_writes_nothing(
    project: Project, fakes: dict[str, Fake]
) -> None:
    code, out = _cascade(project.root.parent, "--opt-in", "--scopus-budget", "10", "--dry-run")

    assert code == 0, out
    assert "level 2" in out
    assert fakes["scopus"].requests == [] and fakes["openalex"].requests == []
    assert not (project.raw_dir / "scopus-cascade").exists()
    assert not (project.raw_dir / "openalex").exists()


@pytest.mark.integration
def test_cascade__unknown_project__is_a_clean_error(tmp_path: Path) -> None:
    code, out = _cascade(tmp_path, "--dry-run")

    assert code != 0
    assert "Traceback" not in out


@pytest.mark.integration
def test_cascade__openalex_refused__is_requested_again_value_is_not(
    project: Project, fakes: dict[str, Fake]
) -> None:
    args = ("--opt-in", "--scopus-budget", "10")
    fakes["openalex"]._handler = lambda r: httpx.Response(403)
    _cascade(project.root.parent, *args)
    fakes["openalex"]._handler = _openalex
    fakes["openalex"].requests.clear()

    _, dry = _cascade(project.root.parent, *args, "--dry-run")
    code, out = _cascade(project.root.parent, *args)

    assert code == 0, out
    assert "openalex: 3 DOIs" in dry
    assert _dois(fakes["openalex"]) == {"10.1/a", "10.1/b", "10.1/c"}
    fakes["openalex"].requests.clear()
    _cascade(project.root.parent, *args)
    assert fakes["openalex"].requests == []  # now value: held


@pytest.mark.integration
def test_cascade__doi_with_quote__is_skipped_with_a_note(
    project: Project, fakes: dict[str, Fake]
) -> None:
    con = duckdb.connect(str(project.db_path))
    con.execute(
        "INSERT INTO records VALUES ('scopus:2-s2.0-5', '10.1/q\"x', 'Q', 2020, "
        "'unavailable','unavailable','unavailable','unavailable')"
    )
    con.close()

    code, out = _cascade(project.root.parent, "--opt-in", "--scopus-budget", "10")

    assert code == 0, out
    assert '10.1/q"x' in out and "skipped" in out
    assert _dois(fakes["scopus"]) == {"10.1/a", "10.1/b", "10.1/c"}
    assert any("/abstract/eid/" in r.url.path for r in fakes["scopus"].requests)  # level 3 ran


@pytest.mark.integration
def test_cascade__real_run__says_rebuild_is_needed(
    project: Project, fakes: dict[str, Fake]
) -> None:
    _, out = _cascade(project.root.parent)
    _, dry = _cascade(project.root.parent, "--dry-run")

    assert "prismabib build --rebuild" in out
    assert "prismabib build --rebuild" not in dry
