"""ADR 0041 Decision 5 and PR 7: cascade numbers, the records export and the conflicts export.

Fixture rows are written straight into the PR3 provenance columns, so these tests do not
depend on the cascade writers (PR 4-6).
"""

from __future__ import annotations

import csv
import io
import json
from typing import TYPE_CHECKING

import pytest

from prismabib.report.export import export_project
from prismabib.report.numbers import numbers_map
from prismabib.store.db import connect
from tests.prisma_helpers import CorpusSpec, CriteriaSpec, RecordSpec, build_project

if TYPE_CHECKING:
    from pathlib import Path

    from prismabib.project import Project

FIELDS = ("abstract", "authors", "affiliations", "keywords")
MANUAL_REF = "decisions/manual_entries.jsonl#01HX"


@pytest.fixture
def project(tmp_path: Path) -> Project:
    """Six Scopus records; provenance is then set per record by SQL."""
    spec = CorpusSpec(
        records=[RecordSpec(number=n) for n in range(1, 7)],
        criteria=CriteriaSpec(abstract_reason_codes=("OFF_TOPIC",)),
    )
    built = build_project(tmp_path, spec, slug="cascade")
    connection = connect(built, read_only=False)
    try:
        ids = [
            r[0] for r in connection.execute("SELECT record_id FROM records ORDER BY 1").fetchall()
        ]

        def set_field(rid: str, field: str, source: str, level: int, ref: str | None) -> None:
            outcome = "unavailable" if source == "unavailable" else "value"
            connection.execute(
                f"UPDATE records SET {field}_source=?, {field}_level=?, {field}_outcome=?, "
                f"{field}_payload_ref=? WHERE record_id=?",
                [source, level, outcome, ref, rid],
            )

        for field in FIELDS:  # start from nothing known; the loader's real values are not the point
            connection.execute(
                f"UPDATE records SET {field}_source='unavailable', {field}_level=0, "
                f"{field}_outcome='unavailable', {field}_payload_ref=NULL"
            )
        set_field(ids[0], "abstract", "scopus-complete", 1, "run/page-0001.jsonl#0")
        set_field(ids[1], "abstract", "scopus-complete", 1, "run/page-0001.jsonl#1")
        set_field(ids[2], "abstract", "openalex", 4, "openalex/r/response-0001.jsonl#W1")
        set_field(ids[3], "abstract", "manual", 5, MANUAL_REF)
        # ids[4], ids[5]: abstract unavailable.
        set_field(
            ids[0], "affiliations", "scopus-standard", 2, "scopus-cascade/r/response-0001.jsonl#1"
        )
        set_field(
            ids[1], "affiliations", "scopus-meta", 3, "scopus-cascade/r/response-0002.jsonl#2"
        )
        set_field(ids[2], "keywords", "openalex-topic", 4, "openalex/r/response-0001.jsonl#W1")
        set_field(ids[3], "authors", "manual", 5, MANUAL_REF)
        for rid, field, status, side, event in (
            (ids[0], "abstract", "open", None, None),
            (ids[1], "abstract", "resolved", "manual", "01HX"),
            (ids[0], "authors", "open", None, None),
        ):
            connection.execute(
                "INSERT INTO provenance_conflicts VALUES (?, ?, 1, 'scopus-complete', 'p', '\"a\"', "
                "'q', '\"b\"', ?, ?, ?)",
                [rid, field, status, side, event],
            )
    finally:
        connection.close()
    return built


@pytest.mark.integration
def test_numbers_map__cascade_keys__count_the_provenance_columns(project: Project) -> None:
    numbers = numbers_map(project)

    got = {k: v for k, v in numbers.items() if k.startswith("methods.cascade")}
    assert len(got) == 31
    assert numbers["methods.cascade.abstract.level1"] == 2
    assert numbers["methods.cascade.abstract.level4"] == 1
    assert numbers["methods.cascade.abstract.manual"] == 1
    assert numbers["methods.cascade.abstract.unavailable"] == 2
    assert numbers["methods.cascade.affiliations.level2"] == 1
    assert numbers["methods.cascade.affiliations.level3"] == 1
    assert numbers["methods.cascade.affiliations.unavailable"] == 4
    assert numbers["methods.cascade.keywords.level4"] == 1
    assert numbers["methods.cascade.authors.manual"] == 1
    assert numbers["methods.cascade.keywords.conflicts"] == 0
    assert numbers["methods.cascade.abstract.conflicts"] == 2
    assert numbers["methods.cascade.authors.conflicts"] == 1
    assert numbers["methods.cascade_conflicts_open"] == 2
    assert numbers["methods.cascade_conflicts_resolved"] == 1
    # Per field, every record is in exactly one bucket.
    for field in FIELDS:
        total = sum(
            numbers[f"methods.cascade.{field}.{s}"]
            for s in ("level1", "level2", "level3", "level4", "manual", "unavailable")
        )
        assert total == 6, field


@pytest.mark.integration
def test_numbers_map__cascade_sentence__quotes_the_counts(project: Project) -> None:
    sentence = numbers_map(project)["methods.cascade_sentence"]

    assert "from OpenAlex for 1 and entered manually for 1; 2 have no abstract" in sentence
    assert sentence.endswith("2 conflicts remain open and 1 were resolved by a reviewer.")


@pytest.mark.integration
def test_numbers_map__cascade_keys__are_deterministic(project: Project) -> None:
    assert json.dumps(numbers_map(project), sort_keys=False) == json.dumps(
        numbers_map(project), sort_keys=False
    )


@pytest.mark.integration
def test_export__records_csv__carries_the_16_provenance_columns_and_manual_label(
    project: Project,
) -> None:
    result = export_project(project)

    rows = list(csv.DictReader(io.StringIO((result.root / "records.csv").read_text("utf-8"))))
    assert len(rows) == 6
    for field in FIELDS:
        for suffix in ("source", "level", "outcome", "payload_ref"):
            assert f"{field}_{suffix}" in rows[0]
    by_abstract = [r["abstract_source"] for r in rows]
    assert by_abstract.count("manual") == 1
    manual = next(r for r in rows if r["abstract_source"] == "manual")
    assert (manual["abstract_level"], manual["abstract_outcome"]) == ("5", "value")
    assert manual["abstract_payload_ref"] == MANUAL_REF
    # Never a blank: an unavailable field says so, including its payload ref.
    unavailable = next(r for r in rows if r["keywords_source"] == "unavailable")
    assert unavailable["keywords_outcome"] == "unavailable"
    assert unavailable["keywords_payload_ref"] == "unavailable"


@pytest.mark.integration
def test_export__provenance_conflicts_csv__shows_open_and_resolved(project: Project) -> None:
    result = export_project(project)

    rows = list(
        csv.DictReader(io.StringIO((result.root / "provenance_conflicts.csv").read_text("utf-8")))
    )
    # Ordered by (record_id, field), not insertion order.
    assert [(r["field"], r["status"]) for r in rows] == [
        ("abstract", "open"),
        ("authors", "open"),
        ("abstract", "resolved"),
    ]
    resolved = next(r for r in rows if r["status"] == "resolved")
    assert (resolved["resolved_side"], resolved["resolution_event_id"]) == ("manual", "01HX")
    assert {"scopus_level", "scopus_source", "scopus_value", "openalex_value"} <= set(rows[0])


@pytest.mark.integration
def test_export__no_conflicts__still_writes_header_only_csv(tmp_path: Path) -> None:
    spec = CorpusSpec(
        records=[RecordSpec(number=1)], criteria=CriteriaSpec(abstract_reason_codes=("OFF_TOPIC",))
    )
    result = export_project(build_project(tmp_path, spec, slug="quiet"))

    lines = (result.root / "provenance_conflicts.csv").read_text("utf-8").splitlines()
    assert len(lines) == 1
    assert lines[0].startswith("record_id,field,")
