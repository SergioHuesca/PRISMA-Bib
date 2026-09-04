"""Integration tests for ``prismabib.fulltext.run`` -- what ``prismabib fulltext`` calls.

Real DuckDB (a store built the normal way, via ``build_store``), real
filesystem, no *credentialed* network: ``Settings`` here carries no
``ELSEVIER_SD_API_KEY``/``UNPAYWALL_EMAIL``, so
:func:`~prismabib.fulltext.resolve.default_chain` degrades to
:class:`~prismabib.fulltext.resolve.CrossrefTdmResolver` (unconditional --
Crossref needs no credential, ADR 0020) and
:class:`~prismabib.fulltext.resolve.ManualDropResolver` -- exactly the
scenario a researcher with no Elsevier entitlement and no wish to fetch
open-access copies actually has, and it lets this module's *orchestration*
(targeting, resumability, budget, persistence) be tested without mocking any
HTTP boundary **except** Crossref's own keyless lookup, which the
module-scoped :func:`_crossref_reports_nothing` fixture answers identically
for every DOI (``message.link`` absent -- Crossref's own measured majority
case, ADR 0020: 23 of 29 records on the corpus it measured) so this file's
actual subject stays unaffected by real network behaviour.

**ADR 0019 Decision 0.** ``run_fulltext_resolution`` writes Layer 0 only.
Every test below that wants to see ``fulltext_assets``/``fulltext_sections``
therefore calls ``build_store(project, rebuild=True)`` *after* resolving,
the same two-step shape already established for ``prismabib enrich``.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import pytest
import respx

from prismabib.capture.layout import is_sealed
from prismabib.config import Settings
from prismabib.errors import StoreError, ValidationError
from prismabib.fulltext.capture import RUNS_DIRNAME, already_resolved_record_ids, capture_fulltext
from prismabib.fulltext.resolve import manual_drop_path
from prismabib.fulltext.run import run_fulltext_resolution
from prismabib.prisma.log import DecisionLog
from prismabib.project import Project
from prismabib.sources.crossref import CrossrefTdmClient
from prismabib.stage import PrismaStage
from prismabib.store.db import connect
from prismabib.store.load import build_store
from tests.conftest import SeededIdFactory
from tests.fixtures.pdf_builder import make_minimal_pdf
from tests.store_helpers import make_entry, write_sealed_run

_STARTED_AT = datetime(2025, 1, 1, tzinfo=UTC)

_CROSSREF_LOOKUP_PREFIX = CrossrefTdmClient.LOOKUP_ENDPOINT_TEMPLATE.rsplit("/", 1)[0] + "/"


@pytest.fixture(autouse=True)
def _crossref_reports_nothing() -> Iterator[None]:
    """Answer every Crossref lookup with "no text-mining link", so this file stays no-network.

    ``default_chain`` now always constructs a :class:`~prismabib.fulltext.resolve.CrossrefTdmResolver`
    (Crossref needs no credential -- ADR 0020), so every call into
    ``run_fulltext_resolution``/``capture_fulltext`` in this module makes one
    real HTTP request per DOI unless intercepted. Mocking it here, once,
    keeps every individual test focused on what it actually tests
    (targeting, resumability, budget, persistence) rather than repeating an
    unrelated mock in each one.
    """
    with respx.mock:
        # HTTP 200 with no `message.link` -- Crossref *knows* the DOI and
        # simply names no text-mining link for it, the actual majority case
        # this fixture claims to model. HTTP 404 ("Crossref knows nothing
        # about this DOI at all") short-circuits `CrossrefTdmClient.lookup`
        # before `tdm_links` is ever reached and so answers a different
        # question than the one this fixture's own docstring states --
        # `tests/integration/fulltext/test_resolve.py`'s
        # `_NO_TDM_LINKS_RESPONSE` models the real case correctly; this
        # mirrors it.
        respx.get(url__startswith=_CROSSREF_LOOKUP_PREFIX).mock(
            return_value=httpx.Response(200, json={"message": {}})
        )
        yield


def _settings() -> Settings:
    # No ELSEVIER_SD_API_KEY, no UNPAYWALL_EMAIL: only ManualDropResolver runs.
    return Settings(_env_file=None, scopus_api_key="test-scopus-key")  # pragma: allowlist secret


def _build_project_with_two_included_records(tmp_path: Path) -> tuple[Project, str, str]:
    project = Project.init("fulltext-run-demo", title="Fulltext Run Demo", root=tmp_path)
    entries = [
        make_entry(eid="2-s2.0-85100000201", doi="10.1016/j.example.2026.100201"),
        make_entry(eid="2-s2.0-85100000202", doi="10.1109/tpami.2026.100202"),
    ]
    write_sealed_run(project.raw_dir, "20250101T000000Z-demorun01", entries, started_at=_STARTED_AT)
    build_store(project, rebuild=True)

    log = DecisionLog(project, id_factory=SeededIdFactory(seed=0, prefix="id"))
    record_a = "scopus:2-s2.0-85100000201"
    record_b = "scopus:2-s2.0-85100000202"
    for record_id in (record_a, record_b):
        log.append(
            stage=PrismaStage.TITLE_ABSTRACT,
            record_id=record_id,
            reviewer="kp",
            decision="include",
        )
    return project, record_a, record_b


def _drop_manual_pdf(project: Project, record_id: str) -> None:
    path = manual_drop_path(project.fulltext_dir, record_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_minimal_pdf(b"BT /F1 24 Tf 10 100 Td (Synthetic Manual Drop) Tj ET"))


@pytest.mark.integration
def test_run__manual_drop_for_both_records__seals_layer0_only(tmp_path: Path) -> None:
    """Resolution writes Layer 0, not Layer 1 (ADR 0019 Decision 0).

    Named for the opposite of what an earlier version of this test asserted:
    ``run_fulltext_resolution`` no longer writes ``fulltext_assets`` at all,
    so the store must be unaffected until a rebuild.
    """
    project, record_a, record_b = _build_project_with_two_included_records(tmp_path)
    _drop_manual_pdf(project, record_a)
    _drop_manual_pdf(project, record_b)

    summary = run_fulltext_resolution(project, settings=_settings())

    assert summary.records_considered == 2
    assert summary.records_attempted == 2
    assert summary.records_resolved == 2
    assert summary.resolved_by_resolver == {"manual": 2}
    assert summary.refused_by_resolver == {}
    assert summary.unresolved_record_ids == ()
    assert summary.failed_record_ids == ()
    assert summary.sealed is True

    connection = connect(project, read_only=True)
    try:
        asset_count = connection.execute("SELECT count(*) FROM fulltext_assets").fetchone()
        section_count = connection.execute("SELECT count(*) FROM fulltext_sections").fetchone()
    finally:
        connection.close()

    assert asset_count is not None and asset_count[0] == 0
    assert section_count is not None and section_count[0] == 0


@pytest.mark.integration
@pytest.mark.acceptance("S03-AC3")
def test_run__resolve_then_rebuild__loads_assets_and_sections_from_layer0(
    tmp_path: Path,
) -> None:
    """The BLOCKING fix: rebuilding the store must not lose a resolution run.

    Resolve, then ``build_store(rebuild=True)`` -- exactly the documented,
    recommended command ``prismabib build`` prints as losing nothing. Before
    ADR 0019 Decision 0, a resolver wrote ``fulltext_assets``/
    ``fulltext_sections`` directly into Layer 1, and this exact sequence
    discarded every row.
    """
    project, record_a, record_b = _build_project_with_two_included_records(tmp_path)
    _drop_manual_pdf(project, record_a)
    _drop_manual_pdf(project, record_b)

    run_fulltext_resolution(project, settings=_settings())
    build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        rows = connection.execute(
            "SELECT record_id, resolver_name, media_type, entitled FROM fulltext_assets "
            "ORDER BY record_id, resolver_name"
        ).fetchall()
        section_count = connection.execute("SELECT count(*) FROM fulltext_sections").fetchone()
    finally:
        connection.close()

    # One row per resolver *attempt*, not per asset (ADR 0019): `crossref_tdm`
    # ran too (it needs no credential, ADR 0020) and reported "no text-mining
    # link" (`entitled=NULL`) before `manual` produced the actual asset.
    assert rows == [
        (record_a, "crossref_tdm", None, None),
        (record_a, "manual", "pdf", True),
        (record_b, "crossref_tdm", None, None),
        (record_b, "manual", "pdf", True),
    ]
    # A page with a text layer produces one (non-low-confidence) section row.
    assert section_count is not None
    assert section_count[0] == 2

    # And rebuilding *again* -- deleting corpus.duckdb and rerunning, the
    # documented "loses nothing" command -- reproduces exactly the same rows,
    # because they are derived from the sealed Layer 0 run, not held only in
    # the store that was just deleted.
    build_store(project, rebuild=True)
    connection = connect(project, read_only=True)
    try:
        rows_again = connection.execute(
            "SELECT record_id, resolver_name, media_type, entitled FROM fulltext_assets "
            "ORDER BY record_id, resolver_name"
        ).fetchall()
    finally:
        connection.close()
    assert rows_again == rows


@pytest.mark.integration
def test_run__second_invocation__does_not_re_attempt_already_resolved_records(
    tmp_path: Path,
) -> None:
    project, record_a, record_b = _build_project_with_two_included_records(tmp_path)
    _drop_manual_pdf(project, record_a)
    _drop_manual_pdf(project, record_b)

    run_fulltext_resolution(project, settings=_settings())
    second_summary = run_fulltext_resolution(project, settings=_settings())

    assert second_summary.records_considered == 2
    assert second_summary.records_attempted == 0
    assert second_summary.records_resolved == 0
    assert second_summary.sealed is True


@pytest.mark.integration
def test_run__budget_of_one__attempts_only_one_record_this_call(tmp_path: Path) -> None:
    project, record_a, record_b = _build_project_with_two_included_records(tmp_path)
    _drop_manual_pdf(project, record_a)
    _drop_manual_pdf(project, record_b)

    first = run_fulltext_resolution(project, settings=_settings(), budget=1)

    assert first.records_considered == 2
    assert first.records_attempted == 1
    assert first.records_resolved == 1
    assert first.sealed is False

    second = run_fulltext_resolution(project, settings=_settings(), budget=1)

    assert second.records_attempted == 1
    assert second.records_resolved == 1
    assert second.sealed is True

    resolved = already_resolved_record_ids(project.fulltext_dir)
    assert resolved == {record_a, record_b}


@pytest.mark.integration
def test_run__no_manual_drop__record_stays_unresolved_and_is_reported(tmp_path: Path) -> None:
    project, record_a, record_b = _build_project_with_two_included_records(tmp_path)
    _drop_manual_pdf(project, record_a)
    # record_b gets no manual drop: the chain is exhausted for it.

    summary = run_fulltext_resolution(project, settings=_settings())

    assert summary.records_resolved == 1
    assert summary.unresolved_record_ids == (record_b,)


@pytest.mark.integration
def test_run__no_target_records__raises_validation_error(tmp_path: Path) -> None:
    project = Project.init("empty-fulltext-demo", title="Empty", root=tmp_path)
    entries = [make_entry(eid="2-s2.0-85100000301", doi="10.1016/j.example.2026.100301")]
    write_sealed_run(project.raw_dir, "20250101T000000Z-demorun02", entries, started_at=_STARTED_AT)
    build_store(project, rebuild=True)
    # No decision logged: manual_abstract_set is empty.

    with pytest.raises(ValidationError, match="No records to resolve"):
        run_fulltext_resolution(project, settings=_settings())


@pytest.mark.integration
@pytest.mark.acceptance("S06-AC2")
def test_run__explicit_record_ids_on_pre_v016_store__raises_actionable_store_error(
    tmp_path: Path,
) -> None:
    """A pre-``fulltext_assets`` store must be refused with guidance, not a raw ``CatalogException``.

    Passing ``record_ids`` explicitly is exactly the path that used to skip
    the stale-schema guard entirely: it never calls ``manual_abstract_set``
    (the only other place a read connection was opened), and the old
    implementation's own Layer 1 connection was opened read/write, which
    :mod:`prismabib.store.db` never guards. This call now always opens a
    read-only connection first (to look up DOIs), so the guard fires
    unconditionally.
    """
    project = Project.init("pre-v016-demo", title="Pre v0.16 Demo", root=tmp_path)
    entries = [make_entry(eid="2-s2.0-85100000401", doi="10.1016/j.example.2026.100401")]
    write_sealed_run(project.raw_dir, "20250101T000000Z-demorun03", entries, started_at=_STARTED_AT)
    build_store(project, rebuild=True)

    # Simulate a store built before ADR 0019: drop the two tables it added.
    connection = duckdb.connect(str(project.db_path))
    try:
        connection.execute("DROP TABLE fulltext_assets")
        connection.execute("DROP TABLE fulltext_sections")
    finally:
        connection.close()

    with pytest.raises(StoreError, match=r"prismabib build .* --rebuild"):
        run_fulltext_resolution(
            project, record_ids=["scopus:2-s2.0-85100000401"], settings=_settings()
        )


class _RaisesUnicodeError:
    """A resolver whose failure is neither ``PrismabibError`` nor a transport error.

    ``idna.IDNAError`` is a ``UnicodeError``, and ``httpx`` raises it on a URL
    whose host label is empty or over 63 characters -- a URL that arrives
    verbatim from Unpaywall's ``best_oa_location``, which is untrusted
    third-party data. ``OSError`` from a manual-drop file that passes
    ``is_file()`` and then fails to open reaches the same boundary.
    """

    name = "explodes"

    def resolve(self, *, record_id: str, doi: str | None) -> None:
        raise UnicodeError(f"malformed host label for {record_id} ({doi})")


@pytest.mark.integration
def test_run__resolver_raises_an_unexpected_exception__run_still_seals_and_keeps_prior_work(
    tmp_path: Path,
) -> None:
    """One record's unexpected failure costs one record, not the whole run.

    The per-record boundary catches ``Exception`` rather than a curated tuple,
    because what it defends is *scope*: anything a resolver can raise must stop
    at the record. Before that, an escaping ``UnicodeError`` aborted the loop
    **before the manifest was written** -- so nothing sealed, every refusal
    already recorded was lost, and the resumed run matched the same target
    digest and died on the same record forever, unable ever to seal.
    """
    project, record_a, _record_b = _build_project_with_two_included_records(tmp_path)
    _drop_manual_pdf(project, record_a)

    result = capture_fulltext(
        project,
        pending_ids=[record_a, _record_b],
        doi_by_record_id={record_a: None, _record_b: None},
        resolvers=[_RaisesUnicodeError()],
    )

    run_dir = project.fulltext_dir / RUNS_DIRNAME / result.manifest.run_id
    assert result.sealed, "the run must seal despite an unexpected resolver failure"
    assert is_sealed(run_dir)
    assert sorted(result.failed_record_ids) == sorted([record_a, _record_b])


@pytest.mark.integration
def test_build_store__fulltext_assets_table__stores_no_absolute_path(tmp_path: Path) -> None:
    """Nothing machine-dependent may enter a checksummed table.

    `fulltext_assets.path` once held an absolute filesystem path, so two clones
    of identical Layer 0 bytes produced different table checksums -- falsifying
    S03-AC1 and Stage 11's "a clean clone on a different machine reproduces
    `numbers.json`" for any project that resolves any full text.

    That was fixed, and then nothing tested it: reverting the fix left the
    entire suite green. The reproducibility test that should have caught it
    uses the reference project, which has **zero** full-text rows, so the
    golden it compares is `sha256("")` for this table -- a checksum test
    checksumming an empty table. This is the same degenerate-fixture pattern
    §5 warns about, and the fourth time on this stage that a test passed
    because the fixture could not contain the thing under test.

    Modelled on `test_build_store__malformed_entries_table__stores_no_absolute_path`,
    which already pins exactly this property for `malformed_entries.payload_file`.
    """
    project, record_a, _record_b = _build_project_with_two_included_records(tmp_path)
    _drop_manual_pdf(project, record_a)
    run_fulltext_resolution(project, settings=_settings())
    build_store(project, rebuild=True)

    connection = connect(project, read_only=True)
    try:
        rows = connection.execute("SELECT * FROM fulltext_assets").fetchall()
    finally:
        connection.close()

    cells = [str(cell) for row in rows for cell in row]

    assert rows, "guard the guard: an empty table would make this vacuously true"
    assert not [cell for cell in cells if str(tmp_path) in cell]
    assert not [cell for cell in cells if cell.startswith(("/", "\\")) or ":\\" in cell]
