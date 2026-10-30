"""Integration tests for the OpenAlex loader side of ADR 0030 and ADR 0031.

Real filesystem, real DuckDB, no network: Layer 0 is built by hand with
synthetic OpenAlex-like payloads (:func:`tests.store_helpers.make_openalex_work`),
never licensed data.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from prismabib.bibliometrics.keywords import keyword_frequency
from prismabib.capture.layout import NON_RUN_DIRNAMES, OPENALEX_DIRNAME
from prismabib.errors import StoreError
from prismabib.prisma.flow import compute_flow_counts
from prismabib.project import Project
from prismabib.sources import openalex
from prismabib.stage import PrismaStage
from prismabib.store.checksums import table_checksums
from prismabib.store.db import connect
from prismabib.store.load import (
    RECORD_KEYWORD_KINDS,
    Corpus,
    _sealed_openalex_run_dirs,
    _sealed_run_dirs,
    build_store,
)
from tests.store_helpers import (
    SCHEMA_SQL_PATH,
    create_schema,
    make_entry,
    make_openalex_work,
    write_sealed_openalex_run,
    write_sealed_run,
)

_SEARCH_RUN = "20250101T000000Z-11111111"
_OA_RUN_1 = "20250102T000000Z-aaaaaaaa"
_OA_RUN_2 = "20250103T000000Z-bbbbbbbb"
_OA_RUN_3 = "20250104T000000Z-cccccccc"
_DOI_1 = "10.1000/alpha"
_DOI_2 = "10.1000/beta"


def _project(tmp_path: Path) -> Project:
    """A project whose Scopus run holds two records (one with a DOI each) and author keywords."""
    project = Project.init("oa", title="OpenAlex loader", root=tmp_path)
    entries = [
        make_entry(
            eid="2-s2.0-800000000001",
            title="Alpha Study",
            cover_date="2020-03-01",
            doi=_DOI_1,
            authkeywords="Machine Learning | Graphs",  # pragma: allowlist secret
        ),
        make_entry(
            eid="2-s2.0-800000000002",
            title="Beta: A Study!",
            cover_date="2021-03-01",
            authkeywords="Graphs",  # pragma: allowlist secret
        ),
    ]
    write_sealed_run(
        project.raw_dir, _SEARCH_RUN, entries, started_at=datetime(2025, 1, 1, tzinfo=UTC)
    )
    return project


def _alpha(topics: list[str], **kwargs: object) -> dict[str, object]:
    return make_openalex_work(
        openalex_id="https://openalex.org/W1",
        doi=_DOI_1,
        title="Alpha Study",
        year=2020,
        topics=topics,
        **kwargs,  # type: ignore[arg-type]
    )


def _doi_request(*works: dict[str, object], status: int = 200) -> dict[str, object]:
    return {
        "lookup": "doi_batch",
        "identifiers": [_DOI_1],
        "works": list(works),
        "http_status": status,
    }


def _topics(project: Project, record: str = "scopus:2-s2.0-800000000001") -> set[str]:
    # Raw SQL and an explicit close: an open handle would block deleting the db on Windows.
    connection = connect(project, read_only=True)
    try:
        rows = connection.execute(
            "SELECT k.term_raw FROM record_keywords rk JOIN keywords k USING (keyword_id) "
            "WHERE rk.kind = 'openalex-topic' AND rk.record_id = ?",
            [record],
        ).fetchall()
    finally:
        connection.close()
    return {term for (term,) in rows}


def _checksums(project: Project) -> dict[str, str]:
    connection = connect(project, read_only=True)
    try:
        return dict(table_checksums(connection))
    finally:
        connection.close()


@pytest.mark.unit
def test_record_keywords__kind__rejects_undocumented_value() -> None:
    connection = duckdb.connect(":memory:")
    create_schema(connection)
    connection.execute("INSERT INTO record_keywords VALUES ('r', 'k', 'openalex-topic')")
    with pytest.raises(duckdb.ConstraintException):
        connection.execute("INSERT INTO record_keywords VALUES ('r', 'k', 'bogus')")


@pytest.mark.unit
def test_record_keywords__kinds_written__are_in_the_documented_set(tmp_path: Path) -> None:
    ddl = SCHEMA_SQL_PATH.read_text(encoding="utf-8")
    match = re.search(r"kind TEXT CHECK \(kind IN \(([^)]*)\)\)", ddl)
    assert match is not None
    in_ddl = tuple(re.findall(r"'([^']+)'", match.group(1)))
    assert in_ddl == RECORD_KEYWORD_KINDS

    project = _project(tmp_path)
    write_sealed_openalex_run(project.raw_dir, _OA_RUN_1, [_doi_request(_alpha(["Graph Theory"]))])
    stats = build_store(project, rebuild=True)
    assert set(stats.record_keyword_links_by_kind) <= set(RECORD_KEYWORD_KINDS)


@pytest.mark.unit
def test_non_run_dirnames__openalex__is_excluded_from_the_search_scan(tmp_path: Path) -> None:
    project = _project(tmp_path)
    assert OPENALEX_DIRNAME in NON_RUN_DIRNAMES
    # A manifest directly inside raw/openalex/ would make it a "sealed search run" if the
    # name were not skipped.
    (project.raw_dir / OPENALEX_DIRNAME).mkdir()
    (project.raw_dir / OPENALEX_DIRNAME / "manifest.json").write_text("{}", encoding="utf-8")
    assert [p.name for p in _sealed_run_dirs(project.raw_dir)] == [_SEARCH_RUN]


@pytest.mark.integration
def test_sealed_openalex_run_dirs__unsealed_run__is_skipped(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_sealed_openalex_run(project.raw_dir, _OA_RUN_1, [_doi_request(_alpha(["T"]))])
    dead = project.raw_dir / OPENALEX_DIRNAME / _OA_RUN_2
    dead.mkdir()
    (dead / "response-0000.jsonl").write_text('{"results": []}\n', encoding="utf-8")
    assert [p.name for p in _sealed_openalex_run_dirs(project.raw_dir)] == [_OA_RUN_1]
    assert _sealed_openalex_run_dirs(tmp_path / "nowhere") == []


@pytest.mark.integration
def test_build_store__openalex_topics__attach_by_doi_and_by_title_year(tmp_path: Path) -> None:
    project = _project(tmp_path)
    beta = make_openalex_work(
        openalex_id="https://openalex.org/W2",
        doi=None,
        title="Beta A Study",
        year=2021,
        topics=["Topic B"],
    )
    write_sealed_openalex_run(
        project.raw_dir,
        _OA_RUN_1,
        [
            _doi_request(_alpha(["Graph Theory", "Topic A"])),
            {
                "lookup": "title_search",
                "identifiers": [{"title": "Beta: A Study!", "year": 2021}],
                "works": [beta],
            },
        ],
    )
    stats = build_store(project, rebuild=True)
    assert _topics(project) == {"Graph Theory", "Topic A"}
    assert _topics(project, "scopus:2-s2.0-800000000002") == {"Topic B"}
    assert stats.records_loaded == 2  # no record is created from an OpenAlex body
    assert stats.record_keyword_links_by_kind["openalex-topic"] == 3


@pytest.mark.integration
def test_build_store__openalex_work_matching_no_record__creates_nothing(tmp_path: Path) -> None:
    project = _project(tmp_path)
    stray = make_openalex_work(
        openalex_id="https://openalex.org/W9", doi="10.1/none", title="x", year=2000, topics=["Z"]
    )
    write_sealed_openalex_run(
        project.raw_dir,
        _OA_RUN_1,
        [{"lookup": "doi_batch", "identifiers": ["10.1/none"], "works": [stray]}],
    )
    stats = build_store(project, rebuild=True)
    assert stats.records_loaded == 2
    assert "openalex-topic" not in stats.record_keyword_links_by_kind


@pytest.mark.integration
def test_build_store__scopus_only__reports_no_topic_count(tmp_path: Path) -> None:
    stats = build_store(_project(tmp_path), rebuild=True)
    assert "openalex-topic" not in stats.record_keyword_links_by_kind
    assert stats.record_keyword_links_by_kind["author"] == stats.record_keyword_links_loaded


@pytest.mark.integration
def test_cli_print_store_stats__topic_bearing_store__shows_topic_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from prismabib.cli import _print_store_stats

    project = _project(tmp_path)
    _print_store_stats(build_store(project, rebuild=True), slug="oa", db_path=project.db_path)
    assert "openalex-topic" not in capsys.readouterr().out
    write_sealed_openalex_run(project.raw_dir, _OA_RUN_1, [_doi_request(_alpha(["Graph Theory"]))])
    _print_store_stats(build_store(project, rebuild=True), slug="oa", db_path=project.db_path)
    assert "openalex-topic links" in capsys.readouterr().out


@pytest.mark.integration
def test_keyword_defaults__topic_bearing_store__match_the_scopus_only_store(tmp_path: Path) -> None:
    plain = _project(tmp_path / "plain")
    topical = _project(tmp_path / "topical")
    write_sealed_openalex_run(
        topical.raw_dir, _OA_RUN_1, [_doi_request(_alpha(["Machine Learning", "Topic A"]))]
    )
    build_store(plain, rebuild=True)
    build_store(topical, rebuild=True)
    a, b = Corpus.open(plain), Corpus.open(topical)
    assert a.keywords(stage=PrismaStage.RAW).equals(b.keywords(stage=PrismaStage.RAW))
    left = keyword_frequency(a, min_occurrence=1)
    right = keyword_frequency(b, min_occurrence=1)
    assert left.data.equals(right.data)
    assert left.params == right.params


@pytest.mark.integration
def test_build_store__topic_bearing_project__rebuild_reproduces_checksums(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_sealed_openalex_run(
        project.raw_dir, _OA_RUN_1, [_doi_request(_alpha(["Graph Theory", "Topic A"]))]
    )
    build_store(project, rebuild=True)
    before = _checksums(project)
    topics_before = _topics(project)
    project.db_path.unlink()
    build_store(project)
    assert _checksums(project) == before
    assert _topics(project) == topics_before == {"Graph Theory", "Topic A"}


@pytest.mark.integration
def test_build_store__author_keyword_and_topic_share_term__author_term_raw_wins(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    write_sealed_openalex_run(
        project.raw_dir, _OA_RUN_1, [_doi_request(_alpha(["machine learning"]))]
    )
    build_store(project, rebuild=True)
    connection = connect(project, read_only=True)
    rows = connection.execute(
        "SELECT k.term_raw, rk.kind FROM keywords k JOIN record_keywords rk USING (keyword_id) "
        "WHERE k.term_norm = 'machine learning' ORDER BY rk.kind"
    ).fetchall()
    connection.close()
    assert rows == [("Machine Learning", "author"), ("Machine Learning", "openalex-topic")]


@pytest.mark.integration
def test_build_store__topic_array_order__does_not_change_term_raw(tmp_path: Path) -> None:
    raws = []
    for order in (["Deep Learning!", "Deep Learning"], ["Deep Learning", "Deep Learning!"]):
        project = _project(tmp_path / str(len(raws)))
        write_sealed_openalex_run(project.raw_dir, _OA_RUN_1, [_doi_request(_alpha(order))])
        build_store(project, rebuild=True)
        connection = connect(project, read_only=True)
        raws.append(
            connection.execute(
                "SELECT term_raw FROM keywords WHERE term_norm = 'deep learning'"
            ).fetchall()
        )
        connection.close()
    assert raws[0] == raws[1] == [("Deep Learning",)]


@pytest.mark.integration
def test_build_store__newer_run_without_a_value__keeps_the_older_topics(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_sealed_openalex_run(project.raw_dir, _OA_RUN_1, [_doi_request(_alpha(["Old Topic"]))])
    # Run 2: not_found (empty results). Run 3: found but with no topics (absent).
    write_sealed_openalex_run(project.raw_dir, _OA_RUN_2, [_doi_request()])
    write_sealed_openalex_run(project.raw_dir, _OA_RUN_3, [_doi_request(_alpha([]))])
    build_store(project, rebuild=True)
    assert _topics(project) == {"Old Topic"}


@pytest.mark.integration
def test_build_store__newer_run_with_a_value__replaces_the_older_topics(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_sealed_openalex_run(project.raw_dir, _OA_RUN_1, [_doi_request(_alpha(["Old Topic"]))])
    write_sealed_openalex_run(project.raw_dir, _OA_RUN_2, [_doi_request(_alpha(["New Topic"]))])
    build_store(project, rebuild=True)
    assert _topics(project) == {"New Topic"}


@pytest.mark.integration
def test_build_store__manifest_disagrees_with_bytes__fails_loudly(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_sealed_openalex_run(
        project.raw_dir, _OA_RUN_1, [_doi_request(_alpha(["Topic"]))], bad_outcomes=True
    )
    with pytest.raises(StoreError, match="disagrees"):
        build_store(project, rebuild=True)
    assert not project.db_path.exists()


def _legacy_title_key(title: str) -> str:
    """OpenAlex's title key before the shared normaliser: lowercase, delete ``P*``, keep accents."""
    kept = "".join(c for c in title.lower() if not unicodedata.category(c).startswith("P"))
    return " ".join(kept.split())


def _title_project(tmp_path: Path, record_title: str) -> Project:
    project = Project.init("oa", title="OpenAlex titles", root=tmp_path)
    entry = make_entry(eid="2-s2.0-800000000001", title=record_title, cover_date="2020-03-01")
    write_sealed_run(
        project.raw_dir, _SEARCH_RUN, [entry], started_at=datetime(2025, 1, 1, tzinfo=UTC)
    )
    return project


def _title_request(requested: str, work_title: str) -> dict[str, object]:
    work = make_openalex_work(
        openalex_id="https://openalex.org/W1", doi=None, title=work_title, year=2020, topics=["T"]
    )
    return {
        "lookup": "title_search",
        "identifiers": [{"title": requested, "year": 2020}],
        "works": [work],
    }


def _stored_outcome(project: Project) -> str:
    manifest = project.raw_dir / OPENALEX_DIRNAME / _OA_RUN_1 / "manifest.json"
    return str(
        json.loads(manifest.read_text(encoding="utf-8"))["requests"][0]["outcomes"][0]["outcome"]
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("requested", "work_title", "stored", "topics"),
    [
        # Case A: the legacy rule kept accents and deleted the hyphen, so it missed this pair.
        ("Well being: Etudes", "Well-being: Études", "not_found", set()),
        # Case B: the legacy rule joined "Well-being" to "Wellbeing"; the shared rule does not.
        ("Wellbeing", "Well-being", "value", {"T"}),
    ],
)
def test_build_store__run_sealed_under_the_legacy_title_rule__still_loads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    requested: str,
    work_title: str,
    stored: str,
    topics: set[str],
) -> None:
    project = _title_project(tmp_path, requested)
    with monkeypatch.context() as patch:
        patch.setattr(openalex, "normalise_title", _legacy_title_key)
        write_sealed_openalex_run(
            project.raw_dir, _OA_RUN_1, [_title_request(requested, work_title)]
        )
    assert _stored_outcome(project) == stored
    build_store(project, rebuild=True)
    assert _topics(project) == topics


@pytest.mark.integration
def test_build_store__run_sealed_under_the_shared_title_rule__is_verified_and_attached(
    tmp_path: Path,
) -> None:
    project = _title_project(tmp_path, "Well being: Etudes")
    write_sealed_openalex_run(
        project.raw_dir, _OA_RUN_1, [_title_request("Well being: Etudes", "Well-being: Études")]
    )
    assert _stored_outcome(project) == "value"
    build_store(project, rebuild=True)
    assert _topics(project) == {"T"}


@pytest.mark.integration
def test_build_store__title_outcome_matching_neither_rule__fails_loudly(tmp_path: Path) -> None:
    project = _title_project(tmp_path, "Exact Title")
    write_sealed_openalex_run(
        project.raw_dir, _OA_RUN_1, [_title_request("Exact Title", "Exact Title")]
    )
    manifest = project.raw_dir / OPENALEX_DIRNAME / _OA_RUN_1 / "manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    # Both rules match this pair, so a stored not_found is a tampered outcome.
    data["requests"][0]["outcomes"][0].update(outcome="not_found", openalex_id=None)
    manifest.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(StoreError, match="disagrees"):
        build_store(project, rebuild=True)


@pytest.mark.integration
def test_build_store__sealed_run_with_invalid_manifest__fails_loudly(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_sealed_openalex_run(project.raw_dir, _OA_RUN_1, [_doi_request(_alpha(["Topic"]))])
    manifest = project.raw_dir / OPENALEX_DIRNAME / _OA_RUN_1 / "manifest.json"
    manifest.write_text('{"not": "an OpenAlexRunManifest"}', encoding="utf-8")
    with pytest.raises(StoreError):
        build_store(project, rebuild=True)
    assert not project.db_path.exists()


@pytest.mark.unit
def test_flow_lost_paper_count__excludes_the_loaders_openalex_reason__same_literal() -> None:
    # prisma/flow.py cannot import the loader's constant (Stage 4 must not depend on the store
    # build), so it spells the reason out. If the two drift, OpenAlex audit rows would silently
    # count as lost papers again.
    import inspect

    from prismabib.prisma import flow
    from prismabib.store import load as loader

    # The comment above the SQL also names the reason, so assert the predicate itself.
    predicate = f"reason <> '{loader._REASON_MALFORMED_OPENALEX_BODY}'"
    assert predicate in inspect.getsource(flow._unloadable_count)


@pytest.mark.integration
def test_build_store__malformed_openalex_body__is_recorded_not_skipped_silently(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path)
    write_sealed_openalex_run(
        project.raw_dir,
        _OA_RUN_1,
        [{"lookup": "doi_batch", "identifiers": [_DOI_1], "raw_bytes": b"not json\n"}],
    )
    # raw_bytes with status 200 is recorded by the helper as a not_found outcome; the
    # loader must report the unreadable body itself.
    stats = build_store(project, rebuild=True)
    assert stats.malformed_entries_skipped == (f"openalex/{_OA_RUN_1}/response-0000.jsonl:0",)


@pytest.mark.integration
@pytest.mark.parametrize(
    "bad_body", [b"not json\n", b"[" * 100_000 + b"\n"], ids=["invalid_json", "deeply_nested"]
)
def test_build_store__malformed_openalex_body__is_audited_but_moves_no_flow_count(
    tmp_path: Path, bad_body: bytes
) -> None:
    plain = _project(tmp_path / "plain")
    broken = _project(tmp_path / "broken")
    write_sealed_openalex_run(
        broken.raw_dir,
        _OA_RUN_1,
        [{"lookup": "doi_batch", "identifiers": [_DOI_1], "raw_bytes": bad_body}],
    )
    build_store(plain, rebuild=True)
    stats = build_store(broken, rebuild=True)  # (a) the build succeeds
    connection = connect(broken, read_only=True)
    try:
        rows = connection.execute("SELECT run_id, record_id FROM malformed_entries").fetchall()
    finally:
        connection.close()
    assert rows == [(_OA_RUN_1, None)]  # (b) kept for audit
    assert stats.malformed_entries_skipped
    assert compute_flow_counts(broken) == compute_flow_counts(plain)  # (c)


@pytest.mark.integration
def test_build_store__blank_response_file__means_no_works_not_malformed(tmp_path: Path) -> None:
    project = _project(tmp_path)
    write_sealed_openalex_run(
        project.raw_dir,
        _OA_RUN_1,
        [{"lookup": "doi_batch", "identifiers": [_DOI_1], "raw_bytes": b"\n"}],
    )
    stats = build_store(project, rebuild=True)
    assert stats.malformed_entries_skipped == ()
    assert "openalex-topic" not in stats.record_keyword_links_by_kind
