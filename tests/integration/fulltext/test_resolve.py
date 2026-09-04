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
    CrossrefTdmResolver,
    FullTextResolver,
    ManualDropResolver,
    OpenAccessResolver,
    ScienceDirectResolver,
    default_chain,
    manual_drop_path,
    resolve_fulltext,
)
from prismabib.project import Project
from prismabib.sources.crossref import CrossrefTdmClient
from prismabib.sources.ratelimit import RateLimiter
from prismabib.sources.sciencedirect import ScienceDirectClient
from prismabib.sources.unpaywall import UnpaywallClient

_RECORD_ID = "scopus:2-s2.0-85100000010"
_DOI = "10.1109/tpami.2026.100001"  # an IEEE-registrant DOI -- ScienceDirect never serves it

_SD_ENDPOINT = ScienceDirectClient.ARTICLE_ENDPOINT_TEMPLATE.format(doi=_DOI)
_CROSSREF_ENDPOINT = CrossrefTdmClient.LOOKUP_ENDPOINT_TEMPLATE.format(doi=_DOI)
_UNPAYWALL_ENDPOINT = UnpaywallClient.LOOKUP_ENDPOINT_TEMPLATE.format(doi=_DOI)
_OA_PDF_URL = "https://oa-host.example.org/paper.pdf"

_FAST_RATE_LIMITER_KWARGS = {"rate": 1000.0}

_MINIMAL_PDF = b"%PDF-1.4\n%%EOF"

#: Crossref reporting no text-mining link at all -- ADR 0020's own measured
#: majority case (23 of 29 records on the corpus it measured). Every chain
#: test below that is not specifically exercising Crossref TDM mocks this so
#: the chain proceeds past it exactly as if the resolver were absent.
_NO_TDM_LINKS_RESPONSE: dict[str, object] = {"message": {}}


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
    crossref_client = CrossrefTdmClient(
        settings, rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS)
    )
    oa_client = UnpaywallClient(settings, rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS))
    return [
        ScienceDirectResolver(client=sd_client),
        CrossrefTdmResolver(crossref_client=crossref_client),
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
        crossref_route = respx.get(_CROSSREF_ENDPOINT).mock(
            return_value=httpx.Response(200, json=_NO_TDM_LINKS_RESPONSE)
        )
        oa_route = respx.get(_UNPAYWALL_ENDPOINT).mock(return_value=httpx.Response(404))

        resolvers = _chain(project.fulltext_dir, _settings())
        asset, attempts = resolve_fulltext(record_id=_RECORD_ID, doi=_DOI, resolvers=resolvers)

    assert sd_route.call_count == 1
    assert crossref_route.call_count == 1
    assert oa_route.call_count == 1

    # Resolver 4 was reached and produced the asset.
    assert asset is not None
    assert asset.resolver_name == "manual"

    by_resolver = {attempt.resolver_name: attempt for attempt in attempts}
    assert set(by_resolver) == {"sciencedirect", "crossref_tdm", "openaccess", "manual"}

    # The anti-bias assertion: a 403 records entitled=False, never a bare
    # "unavailable" collapsed together with a genuine 404.
    assert by_resolver["sciencedirect"].entitled is False
    assert by_resolver["sciencedirect"].media_type is None
    assert by_resolver["sciencedirect"].content is None

    # No TDM link at all is "not an entitlement question" -- NULL, not False.
    assert by_resolver["crossref_tdm"].entitled is None

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
        respx.get(_CROSSREF_ENDPOINT).mock(return_value=httpx.Response(404))
        respx.get(_UNPAYWALL_ENDPOINT).mock(return_value=httpx.Response(404))

        resolvers = _chain(project.fulltext_dir, _settings())
        asset, attempts = resolve_fulltext(record_id=_RECORD_ID, doi=_DOI, resolvers=resolvers)

    assert asset is None
    assert len(attempts) == 4
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
        respx.get(_CROSSREF_ENDPOINT).mock(return_value=httpx.Response(404))
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
        respx.get(_CROSSREF_ENDPOINT).mock(return_value=httpx.Response(404))
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
    assert [attempt.entitled for attempt in attempts] == [None, None, True]


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
        respx.get(_CROSSREF_ENDPOINT).mock(return_value=httpx.Response(404))
        respx.get(_UNPAYWALL_ENDPOINT).mock(return_value=httpx.Response(404))

        with default_chain(project, _settings()) as resolvers:
            resolve_fulltext(record_id=_RECORD_ID, doi=_DOI, resolvers=resolvers)

    fulltext_cache = project.fulltext_dir / CACHE_DIRNAME
    raw_cache = project.raw_dir / CACHE_DIRNAME

    assert fulltext_cache.is_dir()
    assert any(fulltext_cache.rglob("*.bin")), "the cache under fulltext/ is empty"
    assert not raw_cache.exists(), f"an HTTP cache was written under raw/: {raw_cache}"


@pytest.mark.integration
@respx.mock
def test_openaccess__first_candidate_is_a_landing_page__the_next_one_is_tried(
    tmp_path: Path,
) -> None:
    """A bad candidate costs that candidate, not the record.

    Unpaywall reports every open-access location it knows. The publisher's own
    "best" one frequently links only to an HTML landing page while a repository
    mirror serves the PDF itself -- on a real 35-record corpus, nine records
    were reported as having no full text for exactly this reason.

    Stopping at the first non-PDF passed every other test in this suite, so
    this one exists to make the *continuation* observable rather than implied.
    """
    # The first candidate's `url_for_pdf` *lies* -- it answers 200 with HTML.
    # That is the realistic shape: publishers advertise a PDF link that lands on
    # a paywall or a cookie wall. A candidate that merely lacks `url_for_pdf`
    # would be ordered last and never reached, so it could not exercise this.
    landing_url = "https://publisher.example.org/claims-to-be.pdf"
    mirror_url = "https://repo.example.org/bitstream/paper.pdf"
    respx.get(UnpaywallClient.LOOKUP_ENDPOINT_TEMPLATE.format(doi=_DOI)).mock(
        return_value=httpx.Response(
            200,
            json={
                "best_oa_location": {"url_for_pdf": landing_url},
                "oa_locations": [{"url_for_pdf": mirror_url}],
            },
        )
    )
    respx.get(landing_url).mock(
        return_value=httpx.Response(
            200, content=b"<html>Sign in to view</html>", headers={"content-type": "text/html"}
        )
    )
    respx.get(mirror_url).mock(
        return_value=httpx.Response(
            200, content=_MINIMAL_PDF, headers={"content-type": "application/pdf"}
        )
    )

    client = UnpaywallClient(_settings(), rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS))
    with client:
        asset = OpenAccessResolver(unpaywall_client=client).resolve(
            record_id="scopus:2-s2.0-900000000001", doi=_DOI
        )

    assert asset is not None
    assert asset.content == _MINIMAL_PDF


@pytest.mark.integration
@respx.mock
def test_openaccess__a_candidate_host_refuses__the_next_one_is_tried(tmp_path: Path) -> None:
    """A 403 from one repository says nothing about the next.

    Open-access hosts sit behind bot filters; the same corpus drew 403s from
    two repositories that nonetheless publish the papers freely. Letting that
    abort the record discards the mirror Unpaywall named in the same response.
    """
    blocked_url = "https://blocked.example.org/paper.pdf"
    mirror_url = "https://repo.example.org/paper.pdf"
    respx.get(UnpaywallClient.LOOKUP_ENDPOINT_TEMPLATE.format(doi=_DOI)).mock(
        return_value=httpx.Response(
            200,
            json={
                "best_oa_location": {"url_for_pdf": blocked_url},
                "oa_locations": [{"url_for_pdf": mirror_url}],
            },
        )
    )
    respx.get(blocked_url).mock(return_value=httpx.Response(403))
    respx.get(mirror_url).mock(
        return_value=httpx.Response(
            200, content=_MINIMAL_PDF, headers={"content-type": "application/pdf"}
        )
    )

    client = UnpaywallClient(_settings(), rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS))
    with client:
        asset = OpenAccessResolver(unpaywall_client=client).resolve(
            record_id="scopus:2-s2.0-900000000002", doi=_DOI
        )

    assert asset is not None
    assert asset.content == _MINIMAL_PDF


@pytest.mark.integration
@respx.mock
def test_openaccess__a_candidate_host_is_unreachable__the_chain_continues(
    tmp_path: Path,
) -> None:
    """A dead mirror costs that mirror, not the record's manual drop.

    `httpx.ConnectError` is not an `UpstreamError`, so a narrower `except` let
    it escape into `resolve_fulltext`'s outer handler, which abandons the whole
    chain for the record -- meaning `ManualDropResolver` never ran and a PDF the
    reviewer had fetched by hand was silently ignored. `follow_redirects=True`
    adds two more of the same shape (`TooManyRedirects`, `UnsupportedProtocol`).
    """
    dead_url = "https://dead.example.org/paper.pdf"
    respx.get(UnpaywallClient.LOOKUP_ENDPOINT_TEMPLATE.format(doi=_DOI)).mock(
        return_value=httpx.Response(200, json={"best_oa_location": {"url_for_pdf": dead_url}})
    )
    respx.get(dead_url).mock(side_effect=httpx.ConnectError("host is down"))

    record_id = "scopus:2-s2.0-900000000003"
    drop = manual_drop_path(tmp_path, record_id)
    drop.parent.mkdir(parents=True, exist_ok=True)
    drop.write_bytes(_MINIMAL_PDF)

    oa_client = UnpaywallClient(_settings(), rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS))
    with oa_client:
        asset, attempts = resolve_fulltext(
            record_id=record_id,
            doi=_DOI,
            resolvers=[
                OpenAccessResolver(unpaywall_client=oa_client),
                ManualDropResolver(fulltext_dir=tmp_path),
            ],
        )

    # The manual drop is reached and wins -- the whole point.
    assert asset is not None
    assert asset.resolver_name == "manual"
    assert [attempt.resolver_name for attempt in attempts] == ["openaccess", "manual"]


@pytest.mark.integration
@respx.mock
@pytest.mark.parametrize(
    "malformed_url",
    [
        pytest.param("https://xn--/paper.pdf", id="idna-error-empty-punycode-label"),
        pytest.param("https://[::1/paper.pdf", id="invalid-url-unclosed-bracket"),
    ],
)
def test_openaccess__unpaywall_names_a_malformed_url__the_manual_drop_still_wins(
    tmp_path: Path, malformed_url: str
) -> None:
    """A URL that cannot even be built costs that candidate, not the record.

    These raise while *constructing* the request, before any transport is
    involved: `idna.IDNAError` and `httpx.InvalidURL`, neither of which is an
    `httpx.HTTPError`. They arrive verbatim from Unpaywall -- untrusted
    third-party data -- and the outer frame's own comment already names
    `idna.IDNAError` as the exception that defeated *its* curated tuple. This
    frame was briefly narrower than the one it was widened for.

    Escaping here abandons the whole chain for the record, so the PDF the
    reviewer fetched by hand is ignored -- on this run and every one after it.
    """
    respx.get(UnpaywallClient.LOOKUP_ENDPOINT_TEMPLATE.format(doi=_DOI)).mock(
        return_value=httpx.Response(200, json={"best_oa_location": {"url_for_pdf": malformed_url}})
    )

    record_id = "scopus:2-s2.0-900000000004"
    drop = manual_drop_path(tmp_path, record_id)
    drop.parent.mkdir(parents=True, exist_ok=True)
    drop.write_bytes(_MINIMAL_PDF)

    oa_client = UnpaywallClient(_settings(), rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS))
    with oa_client:
        asset, _attempts = resolve_fulltext(
            record_id=record_id,
            doi=_DOI,
            resolvers=[
                OpenAccessResolver(unpaywall_client=oa_client),
                ManualDropResolver(fulltext_dir=tmp_path),
            ],
        )

    assert asset is not None
    assert asset.resolver_name == "manual"


def _tdm_response(*links: dict[str, object]) -> dict[str, object]:
    return {"message": {"link": list(links)}}


@pytest.mark.integration
def test_crossref_tdm__springer_pdf__is_resolved() -> None:
    """The measured realistic case: Springer's own declared ``application/pdf`` is usable."""
    tdm_url = "https://link.springer.com/content/pdf/10.1007/x.pdf"
    with respx.mock:
        respx.get(_CROSSREF_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_tdm_response(
                    {
                        "URL": tdm_url,
                        "intended-application": "text-mining",
                        "content-type": "application/pdf",
                    }
                ),
            )
        )
        respx.get(tdm_url).mock(
            return_value=httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=_MINIMAL_PDF
            )
        )

        client = CrossrefTdmClient(
            _settings(), rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS)
        )
        with client:
            asset = CrossrefTdmResolver(crossref_client=client).resolve(
                record_id=_RECORD_ID, doi=_DOI
            )

    assert asset is not None
    assert asset.resolver_name == "crossref_tdm"
    assert asset.media_type == "pdf"
    assert asset.content == _MINIMAL_PDF


@pytest.mark.integration
def test_crossref_tdm__text_html_link__is_rejected() -> None:
    """A TDM link wearing an HTML landing page is not full text, whatever it is labelled."""
    tdm_url = "https://link.springer.com/content/pdf/10.1007/x.pdf"
    with respx.mock:
        respx.get(_CROSSREF_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_tdm_response(
                    {
                        "URL": tdm_url,
                        "intended-application": "text-mining",
                        "content-type": "text/html",
                    }
                ),
            )
        )
        respx.get(tdm_url).mock(
            return_value=httpx.Response(
                200, headers={"content-type": "text/html"}, content=b"<html>sign in</html>"
            )
        )

        client = CrossrefTdmClient(
            _settings(), rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS)
        )
        with client:
            asset = CrossrefTdmResolver(crossref_client=client).resolve(
                record_id=_RECORD_ID, doi=_DOI
            )

    assert asset is None


@pytest.mark.integration
def test_crossref_tdm__unspecified_content_type__is_sniffed_and_accepted() -> None:
    """ACM's declared ``unspecified`` type says nothing -- the actual bytes decide."""
    tdm_url = "https://dl.acm.org/doi/pdf/10.1145/x"
    with respx.mock:
        respx.get(_CROSSREF_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_tdm_response(
                    {
                        "URL": tdm_url,
                        "intended-application": "text-mining",
                        "content-type": "unspecified",
                    }
                ),
            )
        )
        respx.get(tdm_url).mock(
            return_value=httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=_MINIMAL_PDF
            )
        )

        client = CrossrefTdmClient(
            _settings(), rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS)
        )
        with client:
            asset = CrossrefTdmResolver(crossref_client=client).resolve(
                record_id=_RECORD_ID, doi=_DOI
            )

    assert asset is not None
    assert asset.content == _MINIMAL_PDF


@pytest.mark.integration
@pytest.mark.acceptance("S06-AC2")
def test_crossref_tdm__api_elsevier_host__is_skipped_with_no_request_issued() -> None:
    """ADR 0020 Decision 3: ``api.elsevier.com`` is ScienceDirect's own host.

    Without this rule the same record is refused twice for one underlying
    cause, inflating the coverage table's entitlement-gap count. The
    assertion that matters is ``call_count == 0``: not merely that this link
    is not accepted, but that no HTTP request is made to it at all.
    """
    elsevier_url = "https://api.elsevier.com/content/article/doi/10.1016/x"
    with respx.mock:
        respx.get(_CROSSREF_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_tdm_response(
                    {
                        "URL": elsevier_url,
                        "intended-application": "text-mining",
                        "content-type": "text/xml",
                    }
                ),
            )
        )
        elsevier_route = respx.get(elsevier_url).mock(
            return_value=httpx.Response(200, content=b"<xml/>")
        )

        client = CrossrefTdmClient(
            _settings(), rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS)
        )
        with client:
            asset = CrossrefTdmResolver(crossref_client=client).resolve(
                record_id=_RECORD_ID, doi=_DOI
            )

    assert asset is None
    assert elsevier_route.call_count == 0


@pytest.mark.integration
@pytest.mark.acceptance("S06-AC2")
def test_crossref_tdm__403__entitled_false_and_chain_continues_to_next_resolver(
    tmp_path: Path,
) -> None:
    """A TDM host's 403 records ``entitled=False`` and the chain moves on (ADR 0019, ADR 0020)."""
    project = Project.init("crossref-403-demo", title="Crossref 403 Demo", root=tmp_path)
    tdm_url = "https://link.springer.com/content/pdf/10.1007/x.pdf"

    with respx.mock:
        sd_route = respx.get(_SD_ENDPOINT).mock(return_value=httpx.Response(404))
        respx.get(_CROSSREF_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_tdm_response(
                    {
                        "URL": tdm_url,
                        "intended-application": "text-mining",
                        "content-type": "application/pdf",
                    }
                ),
            )
        )
        respx.get(tdm_url).mock(return_value=httpx.Response(403))
        respx.get(_UNPAYWALL_ENDPOINT).mock(return_value=httpx.Response(404))

        resolvers = _chain(project.fulltext_dir, _settings())
        asset, attempts = resolve_fulltext(record_id=_RECORD_ID, doi=_DOI, resolvers=resolvers)

    assert sd_route.call_count == 1

    # Exhausted the whole chain (no manual drop present) -- but the point is
    # what got recorded along the way, not the final outcome.
    assert asset is None
    by_resolver = {attempt.resolver_name: attempt for attempt in attempts}
    assert set(by_resolver) == {"sciencedirect", "crossref_tdm", "openaccess", "manual"}
    assert by_resolver["crossref_tdm"].entitled is False
    assert by_resolver["crossref_tdm"].media_type is None
    assert by_resolver["crossref_tdm"].content is None
    # The chain did continue: openaccess and manual were both still reached.
    assert by_resolver["openaccess"].entitled is None
    assert by_resolver["manual"].entitled is None


@pytest.mark.integration
def test_default_chain__order__is_sciencedirect_tdm_openaccess_manual(tmp_path: Path) -> None:
    """ADR 0020 Decision 1's chain order, asserted on `default_chain` itself.

    Moving `CrossrefTdmResolver` from second to third left the entire suite
    green: `_chain()` in this module restates the order by hand, so it agrees
    with itself no matter what `default_chain` does.

    The position is load-bearing rather than cosmetic. TDM yields the
    publisher's own full text -- the version of record -- while an
    open-access copy may legitimately be an author preprint, and a review that
    can cite the published version should prefer it. Demoting TDM below open
    access silently changes which version a corpus is assessed from.

    Names are written out literally, not read from the resolver constants,
    so renaming a constant cannot make this agree with itself either.
    """
    project = Project.init("chain-order", title="Chain order", root=tmp_path)

    with default_chain(project, _settings()) as chain:
        assert [resolver.name for resolver in chain] == [
            "sciencedirect",
            "crossref_tdm",
            "openaccess",
            "manual",
        ]


@pytest.mark.integration
def test_crossref_tdm__first_link_host_is_unreachable__the_next_link_is_tried() -> None:
    """A dead host costs that link, not the record.

    `OpenAccessResolver`'s identical per-candidate branch was itself the fix for
    a blocking defect: a dead mirror aborted the whole chain and the reviewer's
    hand-fetched PDF was silently ignored. The copy in this resolver had two
    uncovered lines and no test at all, so the same defect could return here
    without anything noticing.

    `httpx.ConnectError` is not an `UpstreamError`; escaping would reach
    `resolve_fulltext`'s outer handler and abandon the record.
    """
    dead_url = "https://dead.example.org/x.pdf"
    good_url = "https://link.springer.com/content/pdf/10.1007/x.pdf"
    with respx.mock:
        respx.get(_CROSSREF_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_tdm_response(
                    {"URL": dead_url, "intended-application": "text-mining"},
                    {"URL": good_url, "intended-application": "text-mining"},
                ),
            )
        )
        respx.get(dead_url).mock(side_effect=httpx.ConnectError("host is down"))
        respx.get(good_url).mock(
            return_value=httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=_MINIMAL_PDF
            )
        )
        client = CrossrefTdmClient(
            _settings(), rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS)
        )
        with client:
            asset = CrossrefTdmResolver(crossref_client=client).resolve(
                record_id=_RECORD_ID, doi=_DOI
            )

    assert asset is not None
    assert asset.content == _MINIMAL_PDF


@pytest.mark.integration
def test_crossref_tdm__record_without_a_doi__is_skipped_with_no_request() -> None:
    """No DOI means no Crossref lookup at all -- an uncovered branch before this."""
    with respx.mock:
        route = respx.get(url__startswith="https://api.crossref.org/")
        client = CrossrefTdmClient(
            _settings(), rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS)
        )
        with client:
            asset = CrossrefTdmResolver(crossref_client=client).resolve(
                record_id=_RECORD_ID, doi=None
            )

    assert asset is None
    assert route.call_count == 0


@pytest.mark.integration
def test_crossref_tdm__first_link_refused__the_next_link_still_resolves() -> None:
    """A 403 on one TDM link costs that link, not the record's other links.

    Reverting this to the old `raise` left 70 tests passing: the single-link
    403 test ends in `EntitlementError` under both behaviours, so nothing
    distinguished them. The change was entirely unpinned.

    Two numbers move the wrong way without it. A paper that was reachable goes
    unfetched, understating coverage; and the "Refused (entitlement gap)"
    column books a refusal against a resolver whose *other* candidate would
    have succeeded, overstating the gap. Both are the coverage table
    misstating what happened, which ADR 0019 exists to prevent.
    """
    refused_url = "https://api.wiley.com/onlinelibrary/tdm/v1/articles/x"
    good_url = "https://link.springer.com/content/pdf/10.1007/x.pdf"
    with respx.mock:
        respx.get(_CROSSREF_ENDPOINT).mock(
            return_value=httpx.Response(
                200,
                json=_tdm_response(
                    {"URL": refused_url, "intended-application": "text-mining"},
                    {"URL": good_url, "intended-application": "text-mining"},
                ),
            )
        )
        refused = respx.get(refused_url).mock(return_value=httpx.Response(403))
        good = respx.get(good_url).mock(
            return_value=httpx.Response(
                200, headers={"content-type": "application/pdf"}, content=_MINIMAL_PDF
            )
        )
        client = CrossrefTdmClient(
            _settings(), rate_limiter=RateLimiter(**_FAST_RATE_LIMITER_KWARGS)
        )
        with client:
            asset = CrossrefTdmResolver(crossref_client=client).resolve(
                record_id=_RECORD_ID, doi=_DOI
            )

    # The refusal was really attempted, and the next link really served the PDF.
    assert refused.call_count == 1
    assert good.call_count == 1
    assert asset is not None
    assert asset.content == _MINIMAL_PDF
