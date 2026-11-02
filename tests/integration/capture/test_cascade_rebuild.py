"""``prismabib cascade`` end to end with the REAL ``build_store`` (ADR 0041 Decisions 7 to 9).

Only HTTP is faked (``httpx.MockTransport``); the store is built from the sealed Layer 0 runs
and the per-field table is compared with the provenance columns read back from DuckDB.
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
from prismabib.errors import StoreError
from prismabib.project import Project
from prismabib.sources.ratelimit import RateLimiter
from prismabib.store.load import build_store
from tests.store_helpers import make_entry, write_sealed_run

runner = CliRunner()
R1, R2, R3 = (f"scopus:2-s2.0-80000000000{n}" for n in (1, 2, 3))
D2, DN = "10.1000/two", "10.1000/found"
_AFF = [{"affilname": "Uni", "affiliation-city": "Lima", "affiliation-country": "Peru"}]
_ARGS = ("--opt-in", "--scopus-budget", "10")
_FIELDS = ("abstract", "authors", "affiliations", "keywords")
_HEADER = "per field, Scopus records (from the store):"


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
        for sid, doi in (("800000000002", D2), ("800000000009", DN))
        if f'"{doi}"' in q
    ]
    results = {"opensearch:totalResults": str(len(entries)), "entry": entries}
    return _json({"search-results": results})


def _openalex(request: httpx.Request) -> httpx.Response:
    if "search" in request.url.params:  # only "Findable" is known, and it carries a DOI
        title = request.url.params["search"]
        year = int(request.url.params["filter"].removeprefix("publication_year:"))
        work = {"id": "https://openalex.org/W77", "title": title, "publication_year": year}
        work["doi"] = f"https://doi.org/{DN}"
        return _json({"results": [work] if title == "Findable" else []})
    wanted = request.url.params["filter"].removeprefix("doi:").split("|")
    results = [
        {
            "id": f"https://openalex.org/W{i}",
            "doi": f"https://doi.org/{d}",
            "title": "T",
            "publication_year": 2021,
            "abstract_inverted_index": {"Supplied": [0], "abstract": [1]},
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
            cascade_commands, attr, functools.partial(real, transport=out[name].transport, **common)
        )
    yield out


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """R1 complete; R2 has a DOI but no abstract; R3 has no DOI. Plus a DOI-less candidate."""
    p = Project.init("demo", title="Demo", root=tmp_path)
    entries = [
        make_entry(
            eid="2-s2.0-800000000001",
            title="Alpha Study",
            cover_date="2020-03-01",
            doi="10.1000/one",
            description="Scopus abstract one",
            authkeywords="Graphs | Vision",  # pragma: allowlist secret
            author=[{"authid": "A1", "surname": "Smith", "given-name": "Ann"}],
            affiliation=[{"afid": "1", "affilname": "MIT", "affiliation-country": "United States"}],
        ),
        make_entry(
            eid="2-s2.0-800000000002",
            title="Beta Study",
            cover_date="2021-03-01",
            doi=D2,
            description=None,
        ),
        make_entry(
            eid="2-s2.0-800000000003",
            title="Gamma Study",
            cover_date="2022-03-01",
            doi=None,
            description=None,
        ),
    ]
    write_sealed_run(
        p.raw_dir, "20250101T000000Z-11111111", entries, started_at=datetime(2025, 1, 1, tzinfo=UTC)
    )
    CandidateLog(p.root / "decisions" / "candidates.jsonl").add(
        CandidateEvent.from_row(
            CandidateRow("Findable", 2017), reviewer="sh", pasted_at=datetime.now(UTC)
        )
    )
    build_store(p)  # the cascade reads its targets from the store
    return p


def _cascade(root: Path, *args: str) -> tuple[int, str]:
    r = runner.invoke(app, ["cascade", "demo", "--root", str(root), *args])
    return r.exit_code, r.output


def _store_table(project: Project) -> list[str]:
    """The per-field table rebuilt from ``records`` by an independent query."""
    con = duckdb.connect(str(project.db_path), read_only=True)
    try:
        lines = [_HEADER]
        for f in _FIELDS:
            levels = [r[0] for r in con.execute(f"SELECT {f}_level FROM records").fetchall()]
            cells = ", ".join(f"level {i} {levels.count(i)}" for i in range(1, 6))
            lines.append(f"{f}: {cells}, unavailable {levels.count(0)}")
        return lines
    finally:
        con.close()


def _level(project: Project, record_id: str, field: str) -> int:
    con = duckdb.connect(str(project.db_path), read_only=True)
    try:
        row = con.execute(
            f"SELECT {field}_level FROM records WHERE record_id = ?", [record_id]
        ).fetchone()
    finally:
        con.close()
    assert row is not None
    return int(row[0])


def _table_in(out: str) -> list[str]:
    lines = [line.rstrip() for line in out.splitlines()]
    start = lines.index(_HEADER)
    return lines[start : start + 5]


@pytest.mark.integration
def test_cascade_rebuild__table_matches_the_provenance_columns_read_from_the_store(
    project: Project, fakes: dict[str, Fake]
) -> None:
    before = _store_table(project)

    code, out = _cascade(project.root.parent, *_ARGS)

    assert code == 0, out
    table = _table_in(out)
    assert table == _store_table(project)
    assert table != before  # the rebuild folded the new runs in
    assert "build --rebuild" not in out


@pytest.mark.integration
def test_cascade_rebuild__openalex_abstract__is_level_4_for_that_record_only(
    project: Project, fakes: dict[str, Fake]
) -> None:
    assert _level(project, R2, "abstract") == 0

    code, out = _cascade(project.root.parent, *_ARGS)

    assert code == 0, out
    assert _level(project, R1, "abstract") == 1  # Scopus supplied it
    assert _level(project, R2, "abstract") == 4
    assert _level(project, R3, "abstract") == 0
    assert "abstract: level 1 1, level 2 0, level 3 0, level 4 1, level 5 0, unavailable 1" in out


@pytest.mark.integration
def test_cascade_rebuild__doi_less_candidate__gets_doi_written_back_then_reaches_scopus(
    project: Project, fakes: dict[str, Fake]
) -> None:
    log = CandidateLog(project.root / "decisions" / "candidates.jsonl")

    code, out = _cascade(project.root.parent, *_ARGS)

    assert code == 0, out
    assert [e.doi for e in log.load_enrichments()] == [DN]
    assert {v.event.title: v.doi for v in log.view()}["Findable"] == DN
    searches = [r for r in fakes["openalex"].requests if "search" in r.url.params]
    assert {r.url.params["search"] for r in searches} == {"Findable", "Gamma Study"}
    level2 = [r.url.params["query"] for r in fakes["scopus"].requests if "query" in r.url.params]
    assert any(f'"{DN}"' in q for q in level2)
    assert "candidate DOIs from OpenAlex" in out


@pytest.mark.integration
def test_cascade_rebuild__second_run__makes_no_request_and_prints_the_same_table(
    project: Project, fakes: dict[str, Fake]
) -> None:
    _, first = _cascade(project.root.parent, *_ARGS)
    counts = (len(fakes["scopus"].requests), len(fakes["openalex"].requests))

    code, second = _cascade(project.root.parent, *_ARGS)

    assert code == 0, second
    assert (len(fakes["scopus"].requests), len(fakes["openalex"].requests)) == counts
    assert _table_in(second) == _table_in(first) == _store_table(project)


@pytest.mark.integration
def test_cascade_rebuild__failing_rebuild__exits_non_zero_with_the_message(
    project: Project, fakes: dict[str, Fake], monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise StoreError("rebuild exploded")

    monkeypatch.setattr(cascade_commands, "build_store", boom)

    code, out = _cascade(project.root.parent, *_ARGS)

    assert code != 0
    assert "rebuild exploded" in out
    assert "per field" not in out
    assert "Traceback" not in out
