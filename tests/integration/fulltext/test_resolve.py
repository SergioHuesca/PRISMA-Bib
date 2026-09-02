"""Integration tests for the Stage 6 resolver chain (BUILD_PLAN Tests table, ADR 0019).

Real filesystem (``tmp_path``/:class:`~prismabib.project.Project`), mocked
network (``respx`` at the transport boundary only, exactly as
``tests/integration/sources/test_scopus.py`` does it) -- the standard
integration mix (§3.7.2).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import duckdb
import httpx
import pytest
import respx

from prismabib.config import Settings
from prismabib.fulltext.resolve import (
    FullTextAsset,
    FullTextResolver,
    ManualDropResolver,
    OpenAccessResolver,
    ScienceDirectResolver,
    resolve_fulltext,
)
from prismabib.fulltext.run import record_fulltext_attempt
from prismabib.project import Project
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.sciencedirect import ScienceDirectClient
from prismabib.sources.unpaywall import UnpaywallClient
from tests.store_helpers import create_schema
from tests.unit.fulltext.test_resolve import _StubResolver

_RECORD_ID = "scopus:2-s2.0-85100000010"
_DOI = "10.1109/tpami.2026.100001"  # an IEEE-registrant DOI -- ScienceDirect never serves it

_SD_ENDPOINT = ScienceDirectClient.ARTICLE_ENDPOINT_TEMPLATE.format(doi=_DOI)
_UNPAYWALL_ENDPOINT = UnpaywallClient.LOOKUP_ENDPOINT_TEMPLATE.format(doi=_DOI)

_FAST_RATE_LIMITER_KWARGS = {"rate": 1000.0}


def _settings() -> Settings:
    # Literal dummies, never real credentials -- same allowlist discipline as
    # tests/integration/sources/test_scopus.py's `_settings()`.
    return Settings(  # pragma: allowlist secret
        _env_file=None,
        scopus_api_key="test-scopus-key",  # pragma: allowlist secret
        elsevier_sd_api_key="test-sd-key",  # pragma: allowlist secret
        unpaywall_email="reviewer@example.org",
    )


def _chain(fulltext_dir: Path, settings: Settings) -> list[FullTextResolver]:
    sd_client = ScienceDirectClient(settings, rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS))
    oa_client = UnpaywallClient(settings, rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS))
    return [
        ScienceDirectResolver(fulltext_dir=fulltext_dir, client=sd_client),
        OpenAccessResolver(fulltext_dir=fulltext_dir, unpaywall_client=oa_client),
        ManualDropResolver(fulltext_dir=fulltext_dir),
    ]


@pytest.mark.integration
@pytest.mark.acceptance("S06-AC2")
def test_chain__sciencedirect_403__reaches_manual_drop_resolver(tmp_path: Path) -> None:
    """The central anti-bias test.

    A ScienceDirect 403 must not stop the chain, and it must not be
    conflated with "no full text exists": resolver 3 (manual drop) has to
    be reached, and the *ScienceDirect* attempt specifically has to be
    recorded with ``entitled=False``.
    """
    project = Project.init("sd-403-demo", title="SD 403 Demo", root=tmp_path)
    manual_dir = project.fulltext_dir / "manual"
    manual_dir.mkdir(parents=True)
    (manual_dir / f"{_RECORD_ID}.pdf").write_bytes(b"%PDF-1.4\n%%EOF")

    with respx.mock:
        sd_route = respx.get(_SD_ENDPOINT).mock(
            return_value=httpx.Response(403, json={"service-error": {}})
        )
        oa_route = respx.get(_UNPAYWALL_ENDPOINT).mock(return_value=httpx.Response(404))

        resolvers = _chain(project.fulltext_dir, _settings())
        asset, attempts = resolve_fulltext(record_id=_RECORD_ID, doi=_DOI, resolvers=resolvers)

    assert sd_route.call_count == 1
    assert oa_route.call_count == 1

    # Resolver 3 was reached and produced the asset.
    assert asset is not None
    assert asset.resolver_name == "manual"

    by_resolver = {attempt.resolver_name: attempt for attempt in attempts}
    assert set(by_resolver) == {"sciencedirect", "openaccess", "manual"}

    # The anti-bias assertion: a 403 records entitled=False, never a bare
    # "unavailable" collapsed together with a genuine 404.
    assert by_resolver["sciencedirect"].entitled is False
    assert by_resolver["sciencedirect"].media_type is None
    assert by_resolver["sciencedirect"].path is None

    # Unpaywall's 404 is "not an entitlement question" -- NULL, not False.
    assert by_resolver["openaccess"].entitled is None

    assert by_resolver["manual"].entitled is True
    assert by_resolver["manual"].path is not None


@pytest.mark.integration
def test_chain__all_fail__returns_none_and_logs_no_decision_event(tmp_path: Path) -> None:
    """Exhaustion is not a verdict: no decision event is written anywhere.

    ``resolve_fulltext`` never touches the decision log at all, so the
    strongest available assertion is a literal one: the project's
    ``decisions.jsonl`` is byte-identical before and after a call whose
    entire chain returns nothing.
    """
    project = Project.init("all-fail-demo", title="All Fail Demo", root=tmp_path)
    before = project.decisions_path.read_bytes()

    with respx.mock:
        respx.get(_SD_ENDPOINT).mock(return_value=httpx.Response(404))
        respx.get(_UNPAYWALL_ENDPOINT).mock(return_value=httpx.Response(404))

        resolvers = _chain(project.fulltext_dir, _settings())
        asset, attempts = resolve_fulltext(record_id=_RECORD_ID, doi=_DOI, resolvers=resolvers)

    assert asset is None
    assert len(attempts) == 3
    assert all(attempt.entitled is None for attempt in attempts)
    assert project.decisions_path.read_bytes() == before


@pytest.mark.integration
def test_assets__resolution__records_resolver_name_and_entitled_flag() -> None:
    """Every attempt persisted to ``fulltext_assets`` carries its resolver and entitlement.

    Real DuckDB (an in-memory store built from the checked-in ``schema.sql``),
    no network at all -- stub resolvers exercise the persistence path
    (:mod:`prismabib.fulltext.run`) end to end.
    """
    record_id = "scopus:2-s2.0-85100000099"
    refused = _StubResolver(name="sciencedirect", raises=True)
    resolved = _StubResolver(
        name="manual",
        outcome=FullTextAsset(
            record_id=record_id,
            resolver_name="manual",
            media_type="pdf",
            path=Path("/tmp/fulltext/manual") / f"{record_id}.pdf",
            retrieved_at=datetime(2024, 1, 1, tzinfo=UTC),
        ),
    )

    _, attempts = resolve_fulltext(
        record_id=record_id, doi="10.1016/z", resolvers=[refused, resolved]
    )

    connection = duckdb.connect(":memory:")
    try:
        create_schema(connection)
        for attempt in attempts:
            record_fulltext_attempt(connection, attempt)

        rows = connection.execute(
            "SELECT record_id, resolver_name, entitled, media_type FROM fulltext_assets "
            "ORDER BY resolver_name"
        ).fetchall()
    finally:
        connection.close()

    assert rows == [
        (record_id, "manual", True, "pdf"),
        (record_id, "sciencedirect", False, None),
    ]
