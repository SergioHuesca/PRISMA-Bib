"""End-to-end test for ADR 0021: an entitlement refusal is attributed to a publisher.

This is the number Issue #36 is actually about, so it is asserted on the
*rendered* by-publisher coverage table -- real resolver chain (mocked at the
HTTP transport boundary, ``respx``), real ``capture_fulltext`` Layer 0 seal,
real ``build_store``, real :func:`~prismabib.fulltext.coverage.coverage_by_publisher_table`
-- not on the attempt rows in isolation, which
``tests/integration/fulltext/test_coverage.py`` already covers with
hand-inserted fixture data. An unentitled Elsevier key 403s ScienceDirect for
*every* record regardless of its own publisher; before ADR 0021 that put a
false entitlement gap against every non-Elsevier publisher in the corpus
(measured on ``Baseball-CVPR``: "IEEE: 17 refused", never asked).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx

from prismabib.config import Settings
from prismabib.errors import EntitlementError
from prismabib.fulltext.capture import capture_fulltext
from prismabib.fulltext.coverage import coverage_by_publisher_table, coverage_by_resolver_table
from prismabib.fulltext.resolve import default_chain
from prismabib.project import Project
from prismabib.store.db import connect
from prismabib.store.load import build_store
from tests.store_helpers import make_entry, write_sealed_run

_STARTED_AT = datetime(2025, 1, 1, tzinfo=UTC)

#: Two Elsevier records, and three records a real, unentitled Elsevier key
#: has no bearing on at all: IEEE, Springer, and one with no DOI. Mixing
#: publishers -- and Elsevier being a *minority* of the corpus -- is the
#: point: the defect this pins over-reported against every one of the
#: latter three.
_RECORDS = {
    "elsevier-1": ("scopus:2-s2.0-85200000001", "10.1016/j.example.2026.100001", "Elsevier"),
    "elsevier-2": ("scopus:2-s2.0-85200000002", "10.1016/j.example.2026.100002", "Elsevier"),
    "ieee": ("scopus:2-s2.0-85200000003", "10.1109/tpami.2026.100003", "IEEE"),
    "springer": ("scopus:2-s2.0-85200000004", "10.1007/s10994-026-100004", "Springer"),
    "no-doi": ("scopus:2-s2.0-85200000005", None, "unknown"),
}


def _settings() -> Settings:
    # Every credential set, so `default_chain` builds all four resolvers --
    # exactly an operator holding an unentitled Elsevier key alongside a
    # working Unpaywall registration, the scenario the coverage table must
    # not misreport.
    return Settings(  # pragma: allowlist secret
        _env_file=None,
        scopus_api_key="test-scopus-key",  # pragma: allowlist secret
        elsevier_sd_api_key="test-sd-key",  # pragma: allowlist secret
        unpaywall_email="reviewer@example.org",
    )


def _build_project(tmp_path: Path) -> Project:
    project = Project.init(
        "entitlement-attribution-demo", title="Entitlement Attribution", root=tmp_path
    )
    entries = [
        make_entry(eid=record_id.split(":", 1)[1], doi=doi)
        for record_id, doi, _publisher in _RECORDS.values()
    ]
    write_sealed_run(project.raw_dir, "20250101T000000Z-demorun01", entries, started_at=_STARTED_AT)
    build_store(project, rebuild=True)
    return project


_SEARCH_RUN_ID = "20250101T000000Z-11111111"
_SEARCH_STARTED_AT = datetime(2025, 1, 1, tzinfo=UTC)


@dataclass
class _AlwaysRefuses:
    """A resolver that is refused for every record, as an unentitled key would be."""

    name: str

    def resolve(self, *, record_id: str, doi: str | None) -> None:
        raise EntitlementError(f"no entitlement for {record_id} ({doi})")


@pytest.mark.integration
@pytest.mark.acceptance("S06-AC3")
def test_coverage_by_publisher__sciencedirect_refuses_everything__reports_only_elsevier(
    tmp_path: Path,
) -> None:
    project = _build_project(tmp_path)

    with respx.mock:
        # Every ScienceDirect request 403s, regardless of DOI -- exactly what
        # an unentitled Elsevier key does: it never even looks at the DOI.
        respx.get(url__startswith="https://api.elsevier.com/content/article/doi/").mock(
            return_value=httpx.Response(403, json={"service-error": {}})
        )
        # Crossref and Unpaywall both come back empty for every DOI, and
        # there is no manual drop -- every record ends the chain
        # unresolved. What matters for this test is what got *recorded*
        # along the way, not the final resolved count.
        respx.get(url__startswith="https://api.crossref.org/works/").mock(
            return_value=httpx.Response(200, json={"message": {}})
        )
        respx.get(url__startswith="https://api.unpaywall.org/v2/").mock(
            return_value=httpx.Response(404)
        )

        with default_chain(project, _settings()) as resolvers:
            capture_fulltext(
                project,
                pending_ids=[record_id for record_id, _doi, _publisher in _RECORDS.values()],
                doi_by_record_id={
                    record_id: doi for record_id, doi, _publisher in _RECORDS.values()
                },
                resolvers=resolvers,
            )

    build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        by_resolver = coverage_by_resolver_table(connection)
        by_publisher = coverage_by_publisher_table(connection)
    finally:
        connection.close()

    # The by-resolver assertion: ScienceDirect really was asked about all
    # five records (quota really was spent), but only 2 -- the Elsevier
    # ones -- are a genuine entitlement gap.
    resolver_rows = {row[0]: row[1:] for row in by_resolver.rows}
    assert resolver_rows["sciencedirect"][1] == 2, (
        "ScienceDirect's 'Refused' count must be the 2 Elsevier records, not all 5 attempted"
    )

    # The assertion this issue is actually about, on the *rendered* table:
    # every publisher's refused count, read directly off the table a
    # methods section would transcribe.
    publisher_refused = {row[0]: row[3] for row in by_publisher.rows}
    assert publisher_refused["Elsevier"] == 2
    assert publisher_refused["IEEE"] == 0
    assert publisher_refused["Springer"] == 0
    assert publisher_refused["unknown"] == 0

    # Every publisher is still present -- ADR 0019's still-standing rule
    # that the population is every record *attempted*, not every record
    # resolved, so "0 refused" here reads as "asked, not refused", never as
    # "never asked".
    assert set(publisher_refused) == {"Elsevier", "IEEE", "Springer", "unknown"}


@pytest.mark.integration
def test_rebuild__run_sealed_under_the_old_rule__is_reinterpreted_not_copied(
    tmp_path: Path,
) -> None:
    """A rebuild repairs a corpus sealed before ADR 0021, without re-fetching.

    Applying the attribution at capture time corrects new attempts and leaves
    every sealed run alone -- which is right, Layer 0 being immutable, and
    useless, because the corpus that exposed the defect already held its
    refusals. Its table still read "IEEE: 5 refused" after the capture-time
    fix. Re-resolving from scratch to repair a *label* would re-spend a weekly
    quota.

    So the derivation lives in Layer 1: Layer 0's ``entitled: false`` is the
    raw fact "this resolver was refused", and which publisher that counts
    against is decided here, from a DOI Layer 1 already holds.

    This fixture writes Layer 0 exactly as a pre-ADR-0021 run did -- a blanket
    ``entitled: false`` from ScienceDirect for an IEEE record -- and asserts
    the rebuild reinterprets it rather than copying it through.
    """
    project = Project.init("reinterpret", title="Reinterpret", root=tmp_path)
    eid = "2-s2.0-900000000001"
    record_id = f"scopus:{eid}"
    write_sealed_run(
        project.raw_dir,
        _SEARCH_RUN_ID,
        # An IEEE DOI: ScienceDirect could never have served it, entitled or not.
        [make_entry(eid=eid, doi="10.1109/tpami.2026.100001")],
        started_at=_SEARCH_STARTED_AT,
        total_results=1,
    )
    # `capture_fulltext` writes exactly the pre-ADR-0021 shape, because Layer 0
    # still records the raw refusal: a blanket `entitled: false` from
    # ScienceDirect, for a record ScienceDirect could never have served.
    build_store(project, rebuild=True)
    capture_fulltext(
        project,
        pending_ids=[record_id],
        doi_by_record_id={record_id: "10.1109/tpami.2026.100001"},
        resolvers=[_AlwaysRefuses(name="sciencedirect")],
    )

    build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        (entitled,) = connection.execute(
            "SELECT entitled FROM fulltext_assets WHERE resolver_name = 'sciencedirect'"
        ).fetchone()
    finally:
        connection.close()

    # Layer 0 still says `false`; Layer 1 must not.
    assert entitled is None
