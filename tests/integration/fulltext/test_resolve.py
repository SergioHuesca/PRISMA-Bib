"""Integration tests for the Stage 6 resolver chain (BUILD_PLAN Tests table, ADR 0019).

Real filesystem (``tmp_path``/:class:`~prismabib.project.Project`), mocked
network (``respx`` at the transport boundary only, exactly as
``tests/integration/sources/test_scopus.py`` does it) -- the standard
integration mix (§3.7.2).
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from prismabib.capture.layout import CACHE_DIRNAME
from prismabib.config import Settings
from prismabib.fulltext.resolve import (
    FullTextResolver,
    ManualDropResolver,
    OpenAccessResolver,
    ScienceDirectResolver,
    default_chain,
    manual_drop_path,
    resolve_fulltext,
)
from prismabib.project import Project
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.sciencedirect import ScienceDirectClient
from prismabib.sources.unpaywall import UnpaywallClient

_RECORD_ID = "scopus:2-s2.0-85100000010"
_DOI = "10.1109/tpami.2026.100001"  # an IEEE-registrant DOI -- ScienceDirect never serves it

_SD_ENDPOINT = ScienceDirectClient.ARTICLE_ENDPOINT_TEMPLATE.format(doi=_DOI)
_UNPAYWALL_ENDPOINT = UnpaywallClient.LOOKUP_ENDPOINT_TEMPLATE.format(doi=_DOI)
_OA_PDF_URL = "https://oa-host.example.org/paper.pdf"

_FAST_RATE_LIMITER_KWARGS = {"rate": 1000.0}

_MINIMAL_PDF = b"%PDF-1.4\n%%EOF"


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
        ScienceDirectResolver(client=sd_client),
        OpenAccessResolver(unpaywall_client=oa_client),
        ManualDropResolver(fulltext_dir=fulltext_dir),
    ]


def _unpaywall_response(pdf_url: str = _OA_PDF_URL) -> dict[str, object]:
    return {"best_oa_location": {"url_for_pdf": pdf_url, "url": pdf_url}}


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
    manual_drop_path(project.fulltext_dir, _RECORD_ID).parent.mkdir(parents=True)
    manual_drop_path(project.fulltext_dir, _RECORD_ID).write_bytes(_MINIMAL_PDF)

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
    assert by_resolver["sciencedirect"].content is None

    # Unpaywall's 404 is "not an entitlement question" -- NULL, not False.
    assert by_resolver["openaccess"].entitled is None

    assert by_resolver["manual"].entitled is True
    assert by_resolver["manual"].content == _MINIMAL_PDF


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
def test_chain__openaccess_landing_page__is_not_accepted_as_a_pdf(tmp_path: Path) -> None:
    """A 200 that is HTML, not a PDF, does not count as resolved (the defect BLOCKING item 4 pins).

    ``best_oa_pdf_url`` falls back to a generic ``url`` when Unpaywall's best
    OA location carries no direct PDF link, and that ``url`` is routinely a
    publisher landing page. Before this check existed, that HTML was written
    to disk as ``media_type="pdf"``/``entitled=True`` -- overstating
    coverage, which the report must never do.
    """
    project = Project.init("landing-page-demo", title="Landing Page Demo", root=tmp_path)

    with respx.mock:
        respx.get(_SD_ENDPOINT).mock(return_value=httpx.Response(404))
        respx.get(_UNPAYWALL_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_unpaywall_response())
        )
        respx.get(_OA_PDF_URL).mock(
            return_value=httpx.Response(
                200, headers={"content-type": "text/html"}, content=b"<html>not a pdf</html>"
            )
        )

        resolvers = _chain(project.fulltext_dir, _settings())
        asset, attempts = resolve_fulltext(record_id=_RECORD_ID, doi=_DOI, resolvers=resolvers)

    # The chain continues to `manual` (no drop present -> also None) rather than
    # stopping at a false "resolved".
    assert asset is None
    by_resolver = {attempt.resolver_name: attempt for attempt in attempts}
    assert by_resolver["openaccess"].entitled is None
    assert by_resolver["openaccess"].content is None


@pytest.mark.integration
def test_chain__openaccess_real_pdf__is_resolved(tmp_path: Path) -> None:
    """The positive case, so the not-a-pdf check above means something."""
    project = Project.init("real-pdf-demo", title="Real PDF Demo", root=tmp_path)

    with respx.mock:
        respx.get(_SD_ENDPOINT).mock(return_value=httpx.Response(404))
        respx.get(_UNPAYWALL_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_unpaywall_response())
        )
        respx.get(_OA_PDF_URL).mock(
            return_value=httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=_MINIMAL_PDF
            )
        )

        resolvers = _chain(project.fulltext_dir, _settings())
        asset, attempts = resolve_fulltext(record_id=_RECORD_ID, doi=_DOI, resolvers=resolvers)

    assert asset is not None
    assert asset.resolver_name == "openaccess"
    assert asset.content == _MINIMAL_PDF
    assert [attempt.entitled for attempt in attempts] == [None, True]


@pytest.mark.integration
def test_chain__manual_drop_not_a_real_pdf__is_not_accepted(tmp_path: Path) -> None:
    """A file dropped as ``.pdf`` that is not actually one is treated as absent."""
    project = Project.init("bad-manual-drop-demo", title="Bad Manual Drop Demo", root=tmp_path)
    path = manual_drop_path(project.fulltext_dir, _RECORD_ID)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"<html>this is not a pdf</html>")

    resolver = ManualDropResolver(fulltext_dir=project.fulltext_dir)

    asset = resolver.resolve(record_id=_RECORD_ID, doi=None)

    assert asset is None


@pytest.mark.integration
def test_default_chain__caches_http_responses_under_fulltext_dir_not_raw_dir(
    tmp_path: Path,
) -> None:
    """The BLOCKING regression this pins: fetched full text must never sit near ``raw/``.

    ``project.fulltext_dir``'s own docstring and ADR 0019 both require this:
    fetched publisher content is licensed and must stay out of the Layer 0
    archive entirely, including via an HTTP cache directory. Before this fix,
    ``default_chain`` rooted its ``HttpCache`` at ``project.raw_dir / "_cache"``.
    """
    project = Project.init("cache-location-demo", title="Cache Location Demo", root=tmp_path)

    with respx.mock:
        respx.get(_SD_ENDPOINT).mock(return_value=httpx.Response(404))
        respx.get(_UNPAYWALL_ENDPOINT).mock(return_value=httpx.Response(404))

        with default_chain(project, _settings()) as resolvers:
            resolve_fulltext(record_id=_RECORD_ID, doi=_DOI, resolvers=resolvers)

    fulltext_cache = project.fulltext_dir / CACHE_DIRNAME
    raw_cache = project.raw_dir / CACHE_DIRNAME

    assert fulltext_cache.is_dir()
    assert any(fulltext_cache.rglob("*.bin")), "the cache under fulltext/ is empty"
    assert not raw_cache.exists(), f"an HTTP cache was written under raw/: {raw_cache}"
