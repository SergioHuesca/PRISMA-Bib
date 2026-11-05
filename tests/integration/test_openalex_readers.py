"""Readers, manual entry and registration checks for OpenAlex-origin records (ADR 0042, PR 4c).

A synthetic mode-``openalex`` project (``tests/openalex_search_helpers.py``), real DuckDB, no
network. Verification numbers are those of the ADR.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from structlog.testing import capture_logs

from prismabib.bibliometrics import citations, geography, keywords, network, trends, venues
from prismabib.capture import cascade_commands
from prismabib.capture.candidates import CandidateEvent, CandidateLog, CandidateRow
from prismabib.capture.manual import ManualEntryRefusal, add_manual_entry
from prismabib.capture.registrations import RegistrationRefusal, _scopus_duplicate
from prismabib.errors import StoreError
from prismabib.models import (
    ConflictRecord,
    ConflictResolution,
    ManualEntry,
    ManualRecord,
    conflict_resolutions_log,
    manual_entries_log,
)
from prismabib.prisma import engine
from prismabib.project import Project
from prismabib.report import numbers
from prismabib.screening.ui import load_records
from prismabib.stage import PrismaStage
from prismabib.store.db import connect
from prismabib.store.load import Corpus, build_store
from tests.openalex_search_helpers import openalex_project, work, write_search_run

pytestmark = pytest.mark.integration

RAW = PrismaStage.RAW
RUN_A = "20261103T100000Z-aaaaaaaa"
RUN_B = "20261103T110000Z-bbbbbbbb"
W1 = "openalex:https://openalex.org/W1"
W2 = "openalex:https://openalex.org/W2"
W3 = "openalex:https://openalex.org/W3"


def _two_authors(n: int) -> list[dict[str, Any]]:
    """Two authors on one work, so the co-authorship graph has an edge."""
    first = work(n)["authorships"][0]
    second = work(n + 100)["authorships"][0]
    return [first, second]


def _built(tmp_path: Path, *works: dict[str, Any]) -> Project:
    project = openalex_project(tmp_path)
    write_search_run(project, RUN_A, "carbon capture", [list(works)])
    build_store(project, rebuild=True)
    return project


def _full(tmp_path: Path) -> Project:
    """Three works sharing the topic ``Shared`` and one author pair; W3 has no source."""
    shared = {"topics": [{"display_name": "Shared", "score": 0.9}]}
    return _built(
        tmp_path,
        work(1, authorships=_two_authors(1), **shared),
        work(2, authorships=_two_authors(1), **shared),
        work(3, primary_location=None, **shared),
    )


def _sql(project: Project, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    con = connect(project, read_only=True)
    try:
        return con.execute(sql, params or []).fetchall()
    finally:
        con.close()


# -- Verification 41: each analysis is populated, or refuses by name -------------------------------


def test_analyses__openalex_mode__keyword_frequency_reads_topics(tmp_path: Path) -> None:
    corpus = Corpus.open(_full(tmp_path))

    result = keywords.keyword_frequency(corpus, stage=RAW)

    assert corpus.keyword_kind == "openalex-topic"
    assert result.params["kind"] == "openalex-topic"
    assert ("shared", 3) in result.data.select("term", "count").iter_rows()


def test_analyses__openalex_mode__keyword_evolution_reads_topics(tmp_path: Path) -> None:
    result = keywords.keyword_evolution(Corpus.open(_full(tmp_path)), stage=RAW)

    assert result.data.height > 0
    assert "shared" in result.data["term"].to_list()


def test_analyses__openalex_mode__keyword_network_is_not_empty(tmp_path: Path) -> None:
    project = _built(
        tmp_path,
        *[
            work(n, topics=[{"display_name": "Alpha", "score": 1}, {"display_name": "Beta"}])
            for n in (1, 2, 3)
        ],
    )

    result = network.keyword_cooccurrence_network(Corpus.open(project), stage=RAW, min_occurrence=1)

    assert result.data.filter(result.data["row_kind"] == "edge").height == 1
    assert result.params["kind"] == "openalex-topic"


def test_analyses__scopus_mode_corpus_without_project__defaults_stay_author(
    tmp_path: Path,
) -> None:
    project = _full(tmp_path)
    con = connect(project, read_only=True)
    try:
        assert Corpus(con).keyword_kind == "author"  # no project to ask: today's default
    finally:
        con.close()
    legacy = openalex_project(tmp_path / "legacy", "scopus+openalex")
    build_store(legacy, rebuild=True)
    assert Corpus.open(legacy).keyword_kind == "author"


def test_analyses__openalex_mode__explicit_kind_is_respected(tmp_path: Path) -> None:
    result = keywords.keyword_frequency(Corpus.open(_full(tmp_path)), stage=RAW, kind="author")

    assert result.data.height == 0  # an explicit kind is not overridden by the mode
    assert result.params["kind"] == "author"


def test_analyses__openalex_mode__coauthorship_network_has_edges(tmp_path: Path) -> None:
    result = network.coauthorship_network(Corpus.open(_full(tmp_path)), stage=RAW)

    assert result.data.filter(result.data["row_kind"] == "edge").height == 1


def test_analyses__openalex_mode__country_counts_and_impact_are_real(tmp_path: Path) -> None:
    corpus = Corpus.open(_full(tmp_path))

    counts = geography.country_counts(corpus, stage=RAW)
    impact = geography.citation_impact_by_country(corpus, stage=RAW)

    assert counts.data.filter(counts.data["country"] == "ESP")["count"].to_list() == [3.0]
    (row,) = impact.data.filter(impact.data["country"] == "ESP").iter_rows(named=True)
    assert row["total_citations"] == 10 + 20 + 30  # the snapshots, not the silent zero


def test_analyses__openalex_mode__venue_statistics_keep_openalex_rows(tmp_path: Path) -> None:
    corpus = Corpus.open(_full(tmp_path))

    top = venues.top_venues(corpus, stage=RAW)

    assert sorted(top.data["venue"].to_list()) == ["Journal 1", "Journal 2"]
    assert venues.count_unnamed_venues(corpus, stage=RAW) == 1  # W3, no source: counted, not lost
    assert corpus.venues(RAW).height == 3


def test_venues__openalex_rows_kept(tmp_path: Path) -> None:
    frame = Corpus.open(_full(tmp_path)).venues(RAW)

    assert frame["record_id"].to_list() == [W1, W2, W3]
    assert frame["venue_id"].to_list() == [None, None, None]
    assert frame["name"].to_list() == ["Journal 1", "Journal 2", ""]  # NULL name: unnamed


def test_analyses__openalex_mode__citation_statistics_and_trends(tmp_path: Path) -> None:
    corpus = Corpus.open(_full(tmp_path))

    stats = citations.citation_statistics(corpus, stage=RAW)
    annual = trends.annual_counts(corpus, stage=RAW)

    assert stats.data["records_with_a_snapshot"].to_list() == [3]
    assert stats.data["total"].to_list() == [60]
    assert annual.data["count"].sum() == 3


def test_analyses__openalex_mode__taxonomy_coder_sees_topics(tmp_path: Path) -> None:
    from prismabib.taxonomy.coder import code
    from prismabib.taxonomy.schema import load_dimensions
    from tests.taxonomy_helpers import load_rule_files, write_dimensions, write_rule_file

    project = _full(tmp_path)
    write_dimensions(project)
    rules = (
        "version: 1.0.0\ndimension: learning_paradigm\ncounting_unit: papers\ncategories:\n"
        "  - id: supervised\n    any:\n      - {field: author_keywords, pattern: 'Shared'}\n"
        "  - id: unsupervised\n    any:\n      - {field: index_keywords, pattern: 'Shared'}\n"
    )
    write_rule_file(project, "learning_paradigm", rules)
    schema = load_dimensions(project)
    rule_files = load_rule_files(project, schema, "learning_paradigm")

    result = code(Corpus.open(project), rule_files, [], stage=RAW)

    assert {(a.record_id, a.category) for a in result.assignments} == {
        (rid, c) for rid in (W1, W2, W3) for c in ("supervised", "unsupervised")
    }
    assert not result.field_diagnostics  # a field with no content would be named here


def test_screening__openalex_mode__records_carry_topics_authors_and_citations(
    tmp_path: Path,
) -> None:
    project = _full(tmp_path)

    (record,) = load_records(project, [W1]).values()

    assert record.keywords == ("Shared",)
    assert record.cited_by == 10
    assert len(record.authors) == 2


def test_dashboard__openalex_mode__corpus_browser_shows_origin(tmp_path: Path) -> None:
    from prismabib.viz.dashboard import Dashboard

    dash = Dashboard(_full(tmp_path))
    dash._filter_state = type(dash._filter_state)(stage=RAW)  # every record, whatever the screen

    table = dash._corpus_browser_tab()[1]

    assert set(table.value["origin"]) == {"openalex"}
    assert len(table.value) == 3


# -- Verification 43 and 45: the engine needs no edit -----------------------------------------------


def test_engine__openalex_null_venue__is_not_raised_and_fails_whitelist_like_scopus(
    tmp_path: Path,
) -> None:
    """Pins behaviour (not fail-first): NULL venue reads as '' and behaves like a Scopus no-source."""
    conference = {
        "primary_location": {"source": {"display_name": "Other Conf", "type": "conference"}}
    }
    project = _built(tmp_path, work(1), work(2, primary_location=None), work(3, **conference))
    con = connect(project, read_only=True)
    try:
        attrs = engine._fetch_record_attributes(con)  # no registered-only refusal, no raise
    finally:
        con.close()
    scopus_without_source = engine._RecordAttributes(
        year=2020,
        doc_type="",
        language=None,
        venue_type="",
        venue_name="",
        subject_areas=frozenset(),
        subject_area_observed=True,
    )

    verdicts = {rid: engine._passes_conference_whitelist(a, ["Neural"]) for rid, a in attrs.items()}

    assert attrs[W2].venue_name == "" and attrs[W2].venue_type == ""
    assert verdicts[W2] == engine._passes_conference_whitelist(scopus_without_source, ["Neural"])
    assert verdicts[W1] is True  # a journal is never narrowed by the whitelist
    assert verdicts[W3] is False  # a conference whose name matches no token fails it


# -- Verification 16 and 16a: the cascade ----------------------------------------------------------


def test_cascade__openalex_mode__candidates_only_and_no_record_targets(tmp_path: Path) -> None:
    project = _full(tmp_path)
    log = CandidateLog(project.root / "decisions" / "candidates.jsonl")
    for title, year in (("Synthetic title 1", 2021), ("Not in the corpus", 2019)):
        log.add(
            CandidateEvent.from_row(
                CandidateRow(title, year), reviewer="t", pasted_at=datetime.now(UTC)
            )
        )

    targets = cascade_commands._targets(project)

    # No work of `openalex_records` is a target, and a candidate that is one is not either.
    assert [(t.title, t.candidate_id is not None) for t in targets] == [("Not in the corpus", True)]


def test_cascade__openalex_mode__field_report_does_not_raise(tmp_path: Path) -> None:
    project = _built(tmp_path, work(1, abstract_inverted_index=None), work(2))

    lines = cascade_commands._field_report(project)

    assert "per field, OpenAlex-primary records (from the store):" in lines
    start = lines.index("per field, OpenAlex-primary records (from the store):")
    assert lines[start + 1 : start + 5] == [
        "abstract: level 4 1, manual 0, unavailable 1",
        "authors: level 4 2, manual 0, unavailable 0",
        "affiliations: level 4 2, manual 0, unavailable 0",
        "keywords: level 4 2, manual 0, unavailable 0",
    ]
    assert "abstract: level 1 0, level 2 0, level 3 0, level 4 0, manual 0, unavailable 0, " in (
        lines[1] + ", "
    )  # the Scopus group is all zeros, present and named


def test_cascade__field_report_and_targets__refuse_a_store_with_another_stamp(
    tmp_path: Path,
) -> None:
    project = _full(tmp_path)
    stamp = project.db_path.with_name("build_stamp.json")
    stamp.write_text(stamp.read_text(encoding="utf-8").replace("openalex", "scopus"), "utf-8")

    for reader in (cascade_commands._field_report, cascade_commands._targets):
        with pytest.raises(StoreError, match="rebuild"):
            reader(project)


# -- numbers.py -----------------------------------------------------------------------------------


def test_numbers__openalex_mode__cascade_counts_include_openalex_records(tmp_path: Path) -> None:
    project = _built(tmp_path, work(1, abstract_inverted_index=None), work(2))
    con = connect(project, read_only=True)
    try:
        got = numbers._cascade_numbers(con)
    finally:
        con.close()

    assert got["methods.cascade.abstract.level4"] == 1
    assert got["methods.cascade.abstract.unavailable"] == 1
    assert got["methods.cascade.authors.level4"] == 2
    assert got["methods.cascade.keywords.level4"] == 2
    assert got["methods.cascade.abstract.level1"] == 0
    assert got["methods.cascade_conflicts_open"] == 0


# -- Verification 62 and 63: manual entry for openalex: ids ----------------------------------------


def test_manual_entry__openalex_id_unavailable_field__written_and_folded(tmp_path: Path) -> None:
    project = _built(tmp_path, work(1, abstract_inverted_index=None, authorships=[]))

    event = add_manual_entry(project, record_id=W1, field="abstract", value="Typed.", reviewer="kp")
    add_manual_entry(
        project,
        record_id=W1,
        field="authors",
        value="Park, Min\nLee, Bo",
        reviewer="kp",
    )
    add_manual_entry(
        project,
        record_id=W1,
        field="affiliations",
        value="Institute X; Madrid; Spain",
        reviewer="kp",
    )
    assert event.doi == "10.1/item-1"
    stats = build_store(project, rebuild=True)

    (row,) = _sql(
        project,
        "SELECT abstract, abstract_source, abstract_level, abstract_outcome, abstract_payload_ref,"
        " authors_source, authors_level, authors_outcome, affiliations_source, affiliations_level"
        " FROM openalex_records",
    )
    assert row[:4] == ("Typed.", "manual", 5, "value")
    assert row[4] == f"decisions/manual_entries.jsonl#{event.event_id}"
    assert row[5:] == ("manual", 5, "value", "manual", 5)
    ids = _sql(project, "SELECT author_id FROM record_authors WHERE record_id = ? ORDER BY 1", [W1])
    assert [i[0].rsplit(":", 1)[1] for i in ids] == ["1", "2"]
    assert all(i[0].startswith("manual:") for i in ids)
    assert _sql(
        project,
        "SELECT a.name, a.country_iso3 FROM record_affiliations ra JOIN affiliations a "
        "USING (afid) WHERE ra.record_id = ?",
        [W1],
    ) == [("Institute X", "ESP")]
    assert stats.provenance_report["manual_superseded"] == 0
    assert stats.provenance_report["manual_orphaned"] == 0
    # the analyses see the typed values
    assert Corpus.open(project).authors(RAW).height == 2


def test_manual_entry__openalex_rebuild__is_deterministic(tmp_path: Path) -> None:
    from prismabib.store.checksums import table_checksums

    project = _built(tmp_path, work(1, authorships=[]))
    add_manual_entry(project, record_id=W1, field="authors", value="Park, Min", reviewer="kp")

    def sums() -> dict[str, str]:
        build_store(project, rebuild=True)
        con = connect(project, read_only=True)
        try:
            return table_checksums(con, ("openalex_records", "authors", "record_authors"))
        finally:
            con.close()

    assert sums() == sums()


def test_manual_entry__openalex_id_supplied_field__refused(tmp_path: Path) -> None:
    project = _built(tmp_path, work(1))

    with pytest.raises(ManualEntryRefusal, match="not unavailable"):
        add_manual_entry(project, record_id=W1, field="abstract", value="x", reviewer="kp")
    assert not project.manual_entries_path.exists() or not project.manual_entries_path.read_text()


def test_manual_entry__unknown_openalex_id__refused_naming_both_kinds(tmp_path: Path) -> None:
    project = _built(tmp_path, work(1))

    with pytest.raises(ManualEntryRefusal, match="no scopus or openalex record"):
        add_manual_entry(
            project, record_id="openalex:nope", field="abstract", value="x", reviewer="k"
        )


def _manual_event(record_id: str, field: str = "abstract") -> ManualRecord:
    return ManualRecord(
        ManualEntry(
            event_id="01HZ7M3QK2W9T4R8N6Y1C5B0XD",
            ts=datetime(2026, 11, 3, tzinfo=UTC),
            project="demo",
            reviewer="kp",
            record_id=record_id,
            field=field,  # type: ignore[arg-type]
            value="Typed.",
        )
    )


def test_manual_fold__openalex_field_supplied_later__is_superseded_not_applied(
    tmp_path: Path,
) -> None:
    project = _built(tmp_path, work(1))
    manual_entries_log(project.manual_entries_path).write_event(_manual_event(W1))

    stats = build_store(project, rebuild=True)

    assert stats.provenance_report["manual_superseded"] == 1
    assert _sql(project, "SELECT abstract, abstract_source FROM openalex_records") == [
        ("Synthetic abstract1", "openalex")
    ]


def test_manual_fold__openalex_id_not_in_store__is_orphaned(tmp_path: Path) -> None:
    project = _built(tmp_path, work(1, abstract_inverted_index=None))
    manual_entries_log(project.manual_entries_path).write_event(
        _manual_event("openalex:https://openalex.org/W999")
    )

    stats = build_store(project, rebuild=True)

    assert stats.provenance_report["manual_orphaned"] == 1
    assert _sql(project, "SELECT abstract_source FROM openalex_records") == [("unavailable",)]


def test_manual_fold__openalex_id_in_a_scopus_mode_project__is_orphaned(tmp_path: Path) -> None:
    project = openalex_project(tmp_path, "scopus+openalex")
    manual_entries_log(project.manual_entries_path).write_event(_manual_event(W1))

    assert build_store(project, rebuild=True).provenance_report["manual_orphaned"] == 1


def test_conflict_resolution__openalex_record__is_orphaned_and_changes_nothing(
    tmp_path: Path,
) -> None:
    project = _built(tmp_path, work(1))
    digest = "0" * 64
    conflict_resolutions_log(project.conflict_resolutions_path).write_event(
        ConflictRecord(
            ConflictResolution(
                event_id="01HZ7M3QK2W9T4R8N6Y1C5B0XE",
                ts=datetime(2026, 11, 3, tzinfo=UTC),
                project="demo",
                reviewer="kp",
                record_id=W1,
                field="abstract",
                chosen_side="openalex",
                scopus_digest=digest,
                openalex_digest=digest,
            )
        )
    )

    stats = build_store(project, rebuild=True)

    assert stats.provenance_report["conflict_resolution_orphaned"] == 1
    assert _sql(project, "SELECT COUNT(*) FROM provenance_conflicts") == [(0,)]


# -- Verification 59 and 61: registrations against OpenAlex-primary records ------------------------


def _derived(doi: str | None, title: str, year: int) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(doi=doi, title=title, year=year)


def test_registration__openalex_record_with_same_doi__refused(tmp_path: Path) -> None:
    project = _built(tmp_path, work(1))

    assert _scopus_duplicate(project, _derived("10.1/item-1", "Another", 1999)) == W1


def test_registration__openalex_record_with_same_title_and_year__refused(tmp_path: Path) -> None:
    project = _built(tmp_path, work(1, doi=None))

    assert _scopus_duplicate(project, _derived(None, "synthetic  TITLE 1", 2021)) == W1
    assert _scopus_duplicate(project, _derived(None, "synthetic title 1", 2000)) is None


def test_registration__scopus_mode_store__duplicate_check_still_reads_records(
    tmp_path: Path,
) -> None:
    project = openalex_project(tmp_path, "scopus+openalex")
    build_store(project, rebuild=True)

    assert _scopus_duplicate(project, _derived("10.1/none", "None", 2000)) is None


def _registration_env(tmp_path: Path) -> Any:
    from tests.integration.capture.test_registrations import Env

    project = openalex_project(tmp_path)
    return Env(project, CandidateLog(project.root / "decisions" / "candidates.jsonl"))


def _search_work_of_the_fixture() -> dict[str, Any]:
    from tests.integration.capture.test_registrations import DOI_ONE, TITLE_ONE

    return work(1, doi=f"https://doi.org/{DOI_ONE}", title=TITLE_ONE, publication_year=2021)


def test_registration__openalex_search_work_with_same_doi__refused_through_the_writer(
    tmp_path: Path,
) -> None:
    from tests.integration.capture.test_registrations import DOI_ONE, TITLE_ONE

    env = _registration_env(tmp_path)
    env.write_run()
    write_search_run(env.project, RUN_A, "q", [[_search_work_of_the_fixture()]])
    env.build()
    cid = env.candidate(TITLE_ONE, 2021, DOI_ONE)

    with pytest.raises(RegistrationRefusal, match="already an OpenAlex record"):
        env.register(cid)
    assert env.snapshot() == (None, None)  # nothing written


def test_build__registration_and_openalex_record_same_doi__reported(tmp_path: Path) -> None:
    from tests.integration.capture.test_registrations import DOI_ONE, TITLE_ONE

    env = _registration_env(tmp_path)
    env.write_run()
    env.build()  # a store with no search run yet: the registration is allowed
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    write_search_run(env.project, RUN_A, "q", [[_search_work_of_the_fixture()]])

    with capture_logs() as logs:
        stats = build_store(env.project, rebuild=True)

    assert stats.provenance_report["registered_openalex_overlap"] == 1
    (entry,) = [
        e for e in logs if e["event"] == "store.load.registered_and_openalex_record_overlap"
    ]
    assert entry["pairs"] == [
        {"register_event_id": event.event_id, "openalex_record_id": W1, "match_rule": "doi"}
    ]
    # reported, and superseded in the same build: the paper is held once
    assert env.registered_ids() == []
    assert _sql(env.project, "SELECT COUNT(*) FROM openalex_records") == [(1,)]


def test_supersede__openalex_search_finds_active_registration_doi__writes_one_event(
    tmp_path: Path,
) -> None:
    from prismabib.capture.registrations import SupersedeEvent, load_registration_events
    from tests.integration.capture.test_registrations import DOI_ONE, TITLE_ONE

    env = _registration_env(tmp_path)
    env.write_run()
    env.build()
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    write_search_run(env.project, RUN_A, "q", [[_search_work_of_the_fixture()]])

    with capture_logs() as logs:
        build_store(env.project, rebuild=True)
    build_store(env.project, rebuild=True)  # idempotent: the registration is no longer active

    (superseded,) = [
        e
        for e in load_registration_events(env.project.registrations_path)
        if isinstance(e, SupersedeEvent)
    ]
    assert (superseded.reverses, superseded.scopus_record_id, superseded.match_rule) == (
        event.event_id,
        W1,
        "doi",
    )
    (report,) = [e for e in logs if e["event"] == "store.load.registrations_superseded"]
    assert report["event_ids"] == [superseded.event_id]


def test_build__no_overlap__reports_zero(tmp_path: Path) -> None:
    project = _built(tmp_path, work(1))

    assert build_store(project, rebuild=True).provenance_report["registered_openalex_overlap"] == 0


# -- full-text runs for openalex: ids (the loader's full-text fold) ------------------------------

FT_RUN = "20261104T100000Z-ffffffff"


def _fulltext_run(project: Project, attempts: list[dict[str, Any]]) -> None:
    """A hand-written sealed full-text run whose attempts carry the given rows."""
    import hashlib
    import json

    from prismabib.capture.manifest import FullTextRunManifest

    run_dir = project.fulltext_dir / "runs" / FT_RUN
    run_dir.mkdir(parents=True)
    payload = "".join(json.dumps(a) + "\n" for a in attempts).encode("utf-8")
    (run_dir / "attempts.jsonl").write_bytes(payload)
    now = datetime(2026, 11, 4, tzinfo=UTC)
    manifest = FullTextRunManifest(
        run_id=FT_RUN,
        started_at=now,
        finished_at=now,
        records_requested=len(attempts),
        records_attempted=len(attempts),
        records_resolved=0,
        resolved_by_resolver={},
        refused_by_resolver={},
        unresolved_record_ids=[],
        failed_record_ids=[],
        attempts_file="attempts.jsonl",
        attempts_sha256=hashlib.sha256(payload).hexdigest(),
        client_version="test",
        criteria_version="test",
    )
    (run_dir / "manifest.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")


def _attempt(
    record_id: str, resolver: str = "sciencedirect", entitled: bool | None = False
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "resolver_name": resolver,
        "media_type": None,
        "asset_file": None,
        "entitled": entitled,
        "retrieved_at": "2026-11-04T10:00:00+00:00",
    }


def test_fulltext__openalex_ids__are_stored_attributed_by_doi_and_counted(tmp_path: Path) -> None:
    from prismabib.fulltext.coverage import coverage_by_publisher_table, coverage_by_resolver_table

    project = _built(tmp_path, work(1, doi="https://doi.org/10.1016/j.x.1"), work(2))
    _fulltext_run(
        project, [_attempt(W1), _attempt(W2), _attempt("openalex:https://openalex.org/W404")]
    )

    stats = build_store(project, rebuild=True)

    rows = _sql(
        project, "SELECT record_id, resolver_name, entitled FROM fulltext_assets ORDER BY 1"
    )
    # W1 is an Elsevier DOI refused by ScienceDirect: a real gap. W2 has a 10.1 prefix the
    # publisher map does not know: not an entitlement question (None). Both were attributed by DOI.
    assert rows == [(W1, "sciencedirect", False), (W2, "sciencedirect", None)]
    assert stats.fulltext_runs_loaded == 1
    assert stats.unmatched_fulltext_record_ids == ("openalex:https://openalex.org/W404",)
    con = connect(project, read_only=True)
    try:
        assert coverage_by_resolver_table(con).rows
        assert coverage_by_publisher_table(con).rows
    finally:
        con.close()


def test_fulltext__unknown_openalex_id__is_logged_loudly_not_dropped(tmp_path: Path) -> None:
    project = _built(tmp_path, work(1))
    _fulltext_run(project, [_attempt("openalex:https://openalex.org/W404")])

    with capture_logs() as logs:
        build_store(project, rebuild=True)

    assert any(e["event"] == "store.load.unmatched_fulltext_records" for e in logs)
    assert _sql(project, "SELECT COUNT(*) FROM fulltext_assets") == [(0,)]


def test_fulltext__openalex_id_in_a_scopus_mode_project__is_unmatched(tmp_path: Path) -> None:
    project = openalex_project(tmp_path, "scopus+openalex")
    _fulltext_run(project, [_attempt(W1)])

    stats = build_store(project, rebuild=True)

    assert stats.unmatched_fulltext_record_ids == (W1,)
    assert _sql(project, "SELECT COUNT(*) FROM fulltext_assets") == [(0,)]


def test_fulltext__openalex_rebuild__is_deterministic(tmp_path: Path) -> None:
    from prismabib.store.checksums import table_checksums

    project = _built(tmp_path, work(1), work(2))
    _fulltext_run(project, [_attempt(W1), _attempt(W2, "unpaywall", True)])

    def sums() -> dict[str, str]:
        build_store(project, rebuild=True)
        con = connect(project, read_only=True)
        try:
            return table_checksums(con, ("fulltext_assets", "fulltext_sections"))
        finally:
            con.close()

    assert sums() == sums()


# -- review round: overlap rules, registrations never in cascade counts, truncation --------------


def _overlap_case(tmp_path: Path, search_work: dict[str, Any]) -> tuple[Any, Any, list[Any]]:
    from tests.integration.capture.test_registrations import DOI_ONE, TITLE_ONE

    env = _registration_env(tmp_path)
    env.write_run()
    env.build()
    event = env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    write_search_run(env.project, RUN_A, "q", [[search_work]])
    with capture_logs() as logs:
        stats = build_store(env.project, rebuild=True)
    return event, stats, logs


def _overlap_rules(logs: list[Any]) -> list[str]:
    (entry,) = [
        e for e in logs if e["event"] == "store.load.registered_and_openalex_record_overlap"
    ]
    return [p["match_rule"] for p in entry["pairs"]]


def test_build__registration_and_openalex_record_same_title_year_only__reported(
    tmp_path: Path,
) -> None:
    from tests.integration.capture.test_registrations import TITLE_ONE

    other_doi = work(1, title=TITLE_ONE, publication_year=2021)  # a different DOI
    _, stats, logs = _overlap_case(tmp_path, other_doi)

    assert stats.provenance_report["registered_openalex_overlap"] == 1
    assert _overlap_rules(logs) == ["title_year"]


def test_build__registration_and_openalex_record_same_doi_only__reported(tmp_path: Path) -> None:
    from tests.integration.capture.test_registrations import DOI_ONE

    same_doi = work(1, doi=f"https://doi.org/{DOI_ONE}", title="Another", publication_year=1999)
    _, stats, logs = _overlap_case(tmp_path, same_doi)

    assert stats.provenance_report["registered_openalex_overlap"] == 1
    assert _overlap_rules(logs) == ["doi"]


def test_numbers__openalex_mode__cascade_counts_never_include_registrations(
    tmp_path: Path,
) -> None:
    from tests.integration.capture.test_registrations import DOI_ONE, TITLE_ONE

    env = _registration_env(tmp_path)
    env.write_run()
    env.build()
    env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    write_search_run(env.project, RUN_A, "q", [[work(2)]])  # unrelated to the registration
    build_store(env.project, rebuild=True)

    con = connect(env.project, read_only=True)
    try:
        got = numbers._cascade_numbers(con)
    finally:
        con.close()

    assert _sql(env.project, "SELECT COUNT(*) FROM registered_records") == [(1,)]
    assert got["methods.cascade.abstract.level4"] == 1  # the search work only


def test_build__overlap_warning__is_truncated_like_the_malformed_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from prismabib.store import load as load_module
    from tests.integration.capture.test_registrations import DOI_ONE, TITLE_ONE

    env = _registration_env(tmp_path)
    env.write_run()
    env.build()
    env.register(env.candidate(TITLE_ONE, 2021, DOI_ONE))
    write_search_run(env.project, RUN_A, "q", [[_search_work_of_the_fixture()]])
    other = "A Synthetic Conference Paper"  # second fixture work, registered too
    env.register(env.candidate(other, 2020, "10.9999/synthetic.reg.two"))
    write_search_run(
        env.project,
        RUN_B,
        "q2",
        [
            [
                work(
                    2,
                    doi="https://doi.org/10.9999/synthetic.reg.two",
                    title=other,
                    publication_year=2020,
                )
            ]
        ],
    )
    monkeypatch.setattr(load_module, "_MAX_LOGGED_MALFORMED_ENTRIES", 1)

    with capture_logs() as logs:
        build_store(env.project, rebuild=True)

    (entry,) = [
        e for e in logs if e["event"] == "store.load.registered_and_openalex_record_overlap"
    ]
    assert entry["count"] == 2
    assert len(entry["pairs"]) == 1
    assert entry["truncated"] is True


# -- null publication year (owner decision 2026-11-04) -------------------------------------------


def _null_year(tmp_path: Path) -> Project:
    return _built(tmp_path, work(1), work(3, publication_year=None))


def test_flow__openalex_work_without_a_year__fails_the_year_test_and_the_equation_closes(
    tmp_path: Path,
) -> None:
    from prismabib.prisma.flow import compute_flow_counts

    counts = compute_flow_counts(_null_year(tmp_path))

    counts.assert_consistent()
    assert counts.excluded_automated_by_reason["year"] == 1
    assert (
        counts.after_automated == 1
    )  # W1 stays; W3 is in the corpus but excluded at the year step


def test_numbers__openalex_work_without_a_year__builds(tmp_path: Path) -> None:
    got = numbers.numbers_map(_null_year(tmp_path))

    assert got["corpus.size"] == 2
    assert got["flow.excluded_automated"] == 1


def test_analyses__openalex_work_without_a_year__none_crashes(tmp_path: Path) -> None:
    corpus = Corpus.open(_null_year(tmp_path))
    span = (2000, 2030)

    assert trends.annual_counts(corpus, stage=RAW).data["count"].sum() == 1  # dated records only
    keywords.keyword_evolution(corpus, stage=RAW)
    network.keyword_cooccurrence_network(corpus, stage=RAW, min_occurrence=1, years=span)
    network.coauthorship_network(corpus, stage=RAW, years=span)
    geography.country_counts(corpus, stage=RAW, years=span)
    geography.citation_impact_by_country(corpus, stage=RAW, years=span)
    venues.top_venues(corpus, stage=RAW, years=span)
    venues.venue_type_split(corpus, stage=RAW, years=span)
    citations.citations_by_year(corpus, stage=RAW)
    assert citations.citation_statistics(corpus, stage=RAW).data["total"].to_list() == [10 + 30]
    # and the default INCLUDED stage, which runs the automated filter
    assert Corpus.open(_null_year(tmp_path / "again")).records().height == 0
